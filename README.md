# Music Bot 🎵

A Discord bot that generates full songs from slash commands using the
[Treblo](https://treblo.com/developers) **Melodia v3** API.

## Usage

```
/generate prompt: Song about my WiFi going out mid-meeting  genre: Country
```

- `prompt` (required): what the song should be about
- `genre` (optional): any genre. Suggestions appear as you type, but you can enter anything.

Discord shows *"Bot is thinking…"* while the song generates, which usually
takes about two minutes. Then the bot posts the finished MP3 (around 2.5 MB)
and the lyrics. A file too big for
the server's upload limit is posted as a download link instead.

Built-in limits: each user can have one song generating at a time, with a
cooldown between requests, and only a few songs generate at once across the
whole bot. These protect your API credits.

## Setup

The Treblo API key is already built in, so you only need a Discord bot.

1. **Create the Discord bot** at <https://discord.com/developers/applications>:
   *New Application* → *Bot* → *Reset Token* (copy it). Then go to
   *OAuth2 → URL Generator*, tick `bot` + `applications.commands` and the *Send Messages* and *Attach Files*
   permissions, and open the generated link to add the bot to your server.
2. **Run it**
   ```bash
   pip install -r requirements.txt
   python -m musicbot
   ```
   The first time it runs, it asks you to paste the Discord token and saves it, so you only do this once.

New slash commands can take a while to show up everywhere. To make `/generate` appear instantly in your
server, add `DEV_GUILD_ID=<your server ID>` to `.env`.

## Configuration (`.env`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `DISCORD_TOKEN` | asked on first run | Bot token |
| `TREBLO_API_KEY` | built in | Overrides the built-in Treblo key |
| `DEV_GUILD_ID` | unset | Sync commands to this server only, instantly |
| `MAX_CONCURRENT_JOBS` | `3` | Songs generating at the same time across the bot |
| `USER_COOLDOWN_SECONDS` | `30` | Wait time between one user's requests |
| `GENERATION_TIMEOUT_SECONDS` | `600` | Time to wait for a song before giving up. Keep it under Discord's 15-minute interaction limit. |
| `TREBLO_BASE_URL` | `https://api.treblo.com` | API base URL |

## Project layout

```
musicbot/
  treblo.py   # async API client: create task → poll status → fetch song URLs
  bot.py      # Discord slash commands, rate limiting, uploads
tests/        # client tests against a local fake API server
```

Each song costs 100 Treblo credits. Run the tests with `pip install -r requirements-dev.txt && pytest`.
