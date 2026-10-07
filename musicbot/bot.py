"""Discord bot that generates songs with the Treblo Melodia v3 API."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time

import discord
from discord import app_commands
from dotenv import load_dotenv

from .treblo import GenerationRequest, GenerationResult, TrebloClient, TrebloError

log = logging.getLogger("musicbot")

# Suggestions shown while typing the genre; any other text is accepted too.
GENRES = [
    "Pop", "Rock", "Hip-Hop", "Rap", "R&B", "Country", "Jazz", "Blues",
    "Electronic", "EDM", "House", "Techno", "Lo-fi", "Synthwave", "Metal",
    "Punk", "Indie", "Folk", "Classical", "Reggae", "K-pop", "Latin",
    "Funk", "Disco", "Soul", "Gospel", "Ambient", "Trap", "Drill", "Musical",
]


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def build_prompt(prompt: str, genre: str | None) -> str:
    prompt = prompt.strip()
    if genre and genre.strip():
        return f"{prompt}\nGenre: {genre.strip()}"
    return prompt


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

    async def generate(
        self, interaction: discord.Interaction, prompt: str, genre: str | None
    ) -> None:
        user = interaction.user
        if error := self.check_rate_limit(user.id):
            await interaction.response.send_message(error, ephemeral=True)
            return

        self.active_users.add(user.id)
        self.last_request[user.id] = time.monotonic()
        # Shows "<bot> is thinking..." until the song is posted.
        await interaction.response.defer(thinking=True)
        try:
            async with self.jobs:
                request = GenerationRequest(prompt=build_prompt(prompt, genre))
                result = await self.treblo.generate(request, timeout=self.timeout)
                await self.deliver(interaction, result, prompt, genre)
        except TrebloError as exc:
            log.warning("Generation for %s failed: %s", user.id, exc)
            await interaction.followup.send(f"❌ Couldn't make that song: {exc}")
        except Exception:
            log.exception("Unexpected error generating for %s", user.id)
            await interaction.followup.send("❌ Something went wrong. Please try again.")
        finally:
            self.active_users.discard(user.id)

    async def deliver(
        self,
        interaction: discord.Interaction,
        result: GenerationResult,
        prompt: str,
        genre: str | None,
    ) -> None:
        limit = interaction.guild.filesize_limit if interaction.guild else 10 * 1024**2
        files: list[discord.File] = []
        links: list[str] = []
        for i, url in enumerate(result.song_urls, start=1):
            data = await self.treblo.download(url, max_bytes=limit - 64 * 1024)
            if data is None:
                links.append(f"[Version {i}]({url})")
            else:
                files.append(discord.File(io.BytesIO(data), filename=f"song_{i}.mp3"))

        title = prompt if len(prompt) <= 200 else prompt[:197] + "…"
        lines = [f"🎶 **{title}**"]
        if genre:
            lines.append(f"Genre: {genre}")
        if links:
            lines.append("Too big to upload here, download: " + " · ".join(links))
        await interaction.followup.send(
            "\n".join(lines),
            files=files,
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


def register_commands(bot: MusicBot) -> None:
    @bot.tree.command(name="generate", description="Generate a song from a text prompt")
    @app_commands.describe(
        prompt="What the song is about, e.g. 'my WiFi going out mid-meeting'",
        genre="Optional genre, e.g. country, lo-fi, metal",
    )
    async def generate(
        interaction: discord.Interaction,
        prompt: app_commands.Range[str, 3, 1500],
        genre: app_commands.Range[str, 1, 100] | None = None,
    ) -> None:
        await bot.generate(interaction, prompt, genre)

    @generate.autocomplete("genre")
    async def genre_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        matches = [g for g in GENRES if current.lower() in g.lower()]
        return [app_commands.Choice(name=g, value=g) for g in matches[:25]]


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
