"""Discord bot that generates songs with the Treblo Melodia v3 API."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from typing import Literal

import discord
from discord import app_commands
from dotenv import load_dotenv

from .treblo import GenerationRequest, GenerationResult, TrebloClient, TrebloError

log = logging.getLogger("musicbot")

STATUS_TEXT = {
    "RECEIVED": "Request received",
    "PENDING": "Queued",
    "QUEUED": "Queued",
    "GENERATING": "Generating music",
    "GENERATING_LYRICS": "Writing lyrics",
    "PROMPT": "Reading your prompt",
    "TAGS": "Picking a style",
    "DECOMPRESSING": "Mixing",
    "SAVING": "Saving audio",
    "SUCCESS": "Done",
}

LengthChoice = Literal["short (~1 min)", "medium (~2 min)", "long (~3+ min)"]
LENGTHS: dict[str, tuple[int, int]] = {
    "short (~1 min)": (30, 90),
    "medium (~2 min)": (90, 150),
    "long (~3+ min)": (150, 240),
}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


class MusicBot(discord.Client):
    def __init__(self, treblo: TrebloClient, dev_guild_id: int | None) -> None:
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.treblo = treblo
        self.dev_guild = discord.Object(id=dev_guild_id) if dev_guild_id else None
        self.jobs = asyncio.Semaphore(_env_int("MAX_CONCURRENT_JOBS", 3))
        self.cooldown = _env_int("USER_COOLDOWN_SECONDS", 30)
        self.timeout = _env_int("GENERATION_TIMEOUT_SECONDS", 600)
        self.active_users: set[int] = set()
        self.last_request: dict[int, float] = {}

    async def setup_hook(self) -> None:
        register_commands(self)
        if self.dev_guild:
            # Guild commands appear instantly; global ones can take up to an hour.
            self.tree.copy_global_to(guild=self.dev_guild)
            await self.tree.sync(guild=self.dev_guild)
        else:
            await self.tree.sync()

    async def close(self) -> None:
        await self.treblo.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, getattr(self.user, "id", "?"))

    def check_rate_limit(self, user_id: int) -> str | None:
        if user_id in self.active_users:
            return "You already have a song generating, hang tight until it finishes."
        wait = self.last_request.get(user_id, 0) + self.cooldown - time.monotonic()
        if wait > 0:
            return f"Slow down! You can make another song in {int(wait) + 1}s."
        return None

    async def run_generation(
        self,
        interaction: discord.Interaction,
        request: GenerationRequest,
        title: str,
    ) -> None:
        user = interaction.user
        if error := self.check_rate_limit(user.id):
            await interaction.response.send_message(error, ephemeral=True)
            return

        self.active_users.add(user.id)
        self.last_request[user.id] = time.monotonic()
        await interaction.response.send_message(f"🎼 **{title}**\n⏳ Waiting for a free slot…")
        try:
            async with self.jobs:
                task_id = await self.treblo.create_generation(request)
                log.info("User %s started task %s", user.id, task_id)

                async def on_status(status: str) -> None:
                    text = STATUS_TEXT.get(status, status.replace("_", " ").title())
                    await interaction.edit_original_response(
                        content=f"🎼 **{title}**\n⏳ {text}…"
                    )

                result = await self.treblo.wait_for_result(
                    task_id, timeout=self.timeout, on_status=on_status
                )
                await self.deliver(interaction, result, title, request.output_format)
        except TrebloError as exc:
            log.warning("Generation for %s failed: %s", user.id, exc)
            await interaction.edit_original_response(content=f"🎼 **{title}**\n❌ {exc}")
        except Exception:
            log.exception("Unexpected error generating for %s", user.id)
            await interaction.edit_original_response(
                content=f"🎼 **{title}**\n❌ Something went wrong. Please try again."
            )
        finally:
            self.active_users.discard(user.id)

    async def deliver(
        self,
        interaction: discord.Interaction,
        result: GenerationResult,
        title: str,
        fmt: str,
    ) -> None:
        limit = interaction.guild.filesize_limit if interaction.guild else 10 * 1024**2
        files: list[discord.File] = []
        links: list[str] = []
        for i, url in enumerate(result.song_urls, start=1):
            data = await self.treblo.download(url, max_bytes=limit - 64 * 1024)
            if data is None:
                links.append(f"[Version {i}]({url})")
            else:
                files.append(discord.File(io.BytesIO(data), filename=f"song_{i}.{fmt}"))

        lines = [f"🎶 **{title}** by {interaction.user.mention}"]
        if result.tags:
            lines.append(f"Style: {', '.join(result.tags[:10])}")
        if links:
            lines.append("Too big to upload here, download: " + " · ".join(links))
        await interaction.edit_original_response(
            content="\n".join(lines),
            attachments=files,
            allowed_mentions=discord.AllowedMentions.none(),
        )

        if result.lyrics and result.lyrics.strip():
            lyrics = result.lyrics.strip()
            if len(lyrics) > 1900:
                await interaction.followup.send(
                    "📝 Lyrics:",
                    file=discord.File(io.BytesIO(lyrics.encode()), filename="lyrics.txt"),
                )
            else:
                await interaction.followup.send(f"📝 **Lyrics**\n```\n{lyrics}\n```")


class ComposeModal(discord.ui.Modal, title="Compose a song"):
    style = discord.ui.TextInput(
        label="Style tags (comma separated)",
        placeholder="e.g. synthwave, female vocals, upbeat, 80s",
        max_length=500,
    )
    lyrics = discord.ui.TextInput(
        label="Lyrics (leave empty for instrumental)",
        style=discord.TextStyle.paragraph,
        placeholder="[Verse]\nWrite your lyrics here...\n\n[Chorus]\n...",
        required=False,
        max_length=4000,
    )

    def __init__(self, bot: MusicBot, fmt: str) -> None:
        super().__init__()
        self.bot = bot
        self.fmt = fmt

    async def on_submit(self, interaction: discord.Interaction) -> None:
        tags = [t.strip() for t in self.style.value.split(",") if t.strip()]
        lyrics = self.lyrics.value.strip()
        request = GenerationRequest(
            tags=tags,
            lyrics=lyrics or None,
            instrumental=not lyrics,
            output_format=self.fmt,
        )
        await self.bot.run_generation(interaction, request, title=", ".join(tags[:5]))


def register_commands(bot: MusicBot) -> None:
    tree = bot.tree

    @tree.command(name="song", description="Generate a song from a text prompt")
    @app_commands.describe(
        prompt="Describe the song, e.g. 'Country song about my WiFi going out mid-meeting'",
        instrumental="No vocals",
        length="Roughly how long the song should be",
        format="Audio file format (mp3 plays inline in Discord)",
    )
    async def song(
        interaction: discord.Interaction,
        prompt: app_commands.Range[str, 3, 1500],
        instrumental: bool = False,
        length: LengthChoice | None = None,
        format: Literal["mp3", "ogg", "wav", "flac", "m4a"] = "mp3",
    ) -> None:
        request = GenerationRequest(
            prompt=prompt,
            instrumental=instrumental,
            output_format=format,
            length_range=LENGTHS[length] if length else None,
        )
        title = prompt if len(prompt) <= 200 else prompt[:197] + "…"
        await bot.run_generation(interaction, request, title=title)

    @tree.command(name="compose", description="Write your own lyrics and pick the style")
    @app_commands.describe(format="Audio file format (mp3 plays inline in Discord)")
    async def compose(
        interaction: discord.Interaction,
        format: Literal["mp3", "ogg", "wav", "flac", "m4a"] = "mp3",
    ) -> None:
        if error := bot.check_rate_limit(interaction.user.id):
            await interaction.response.send_message(error, ephemeral=True)
            return
        await interaction.response.send_modal(ComposeModal(bot, format))

    @tree.command(name="credits", description="Show the bot's remaining Treblo credits")
    @app_commands.default_permissions(manage_guild=True)
    async def credits(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            balance = await bot.treblo.get_balance()
        except TrebloError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        if isinstance(balance, dict):
            text = "\n".join(f"**{k.replace('_', ' ')}**: {v}" for k, v in balance.items())
        else:
            text = str(balance)
        await interaction.followup.send(f"💳 Treblo balance\n{text}", ephemeral=True)

    @tree.command(name="help", description="How to use the music bot")
    async def help_(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "**🎵 Music bot commands**\n"
            "`/song prompt:` describe a song and get it back as audio\n"
            "`/compose` write your own lyrics and choose the style\n"
            "`/credits` check remaining API credits (server managers)\n"
            f"One song at a time per person, {bot.cooldown}s cooldown between requests.",
            ephemeral=True,
        )


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    token = os.getenv("DISCORD_TOKEN")
    api_key = os.getenv("TREBLO_API_KEY")
    if not token or not api_key:
        raise SystemExit("Set DISCORD_TOKEN and TREBLO_API_KEY (see .env.example)")

    treblo = TrebloClient(api_key, base_url=os.getenv("TREBLO_BASE_URL", "https://api.treblo.com"))
    guild_id = os.getenv("DEV_GUILD_ID")
    bot = MusicBot(treblo, int(guild_id) if guild_id else None)
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
