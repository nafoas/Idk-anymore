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

1. **Create the Discord bot**
   - Go to <https://discord.com/developers/applications> → *New Application* → *Bot* → *Reset Token*, and copy the token.
   - Under *OAuth2 → URL Generator*, tick the `bot` and `applications.commands` scopes and the *Send Messages* and *Attach Files* bot permissions. Open the generated URL to invite the bot to your server.
   - No privileged intents are needed.
2. **Get a Treblo API key** at <https://treblo.com/developers>.
3. **Install and configure**
   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   cp .env.example .env              # then fill in DISCORD_TOKEN and TREBLO_API_KEY
   ```
   Set `DEV_GUILD_ID` to your server's ID (right-click the server → *Copy Server ID*, with Developer Mode on) to make the slash commands appear immediately. Without it, the commands are registered globally, which can take up to an hour.
4. **Run it**
   ```bash
   python -m musicbot
   ```

## Configuration (`.env`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `DISCORD_TOKEN` | — | Bot token (required) |
| `TREBLO_API_KEY` | — | Treblo API key (required) |
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
