# Natasha Telegram Group Bot

Natasha is an xAI Grok-powered Telegram group bot built for lively group chats. It replies like a regular chat member, remembers lightweight context, understands photos, and can drop GIFs, Imgflip meme templates, and MyInstants voice-note reactions when the conversation calls for it.

```text
Telegram group -> Natasha -> Grok reply -> optional GIF / meme / voice reaction
```

## Highlights

| Area | What Natasha Does |
| --- | --- |
| Conversation | Short, natural replies in Turkish, Russian, or English |
| Triggers | Replies when mentioned, replied to, woken by keywords, or by chaos chance |
| Memory | Stores recent chat context, nicknames, and owner-written notes in SQLite |
| Photos | Sends images to Grok vision and reacts in character |
| Media | Uses Tenor GIFs, Imgflip meme templates, and MyInstants voice sounds |
| Commands | Owner memory tools plus a public Russian roulette mini-game |

## Quick Start

1. Create a Telegram bot with BotFather.
2. Disable privacy mode in BotFather if the bot should read all group messages.
3. Get an xAI API key.
4. Install dependencies and run the bot.

```bash
pip install -r requirements.txt

export TELEGRAM_TOKEN="your-telegram-bot-token"
export XAI_API_KEY="your-xai-api-key"

python natasha_bot.py
```

Natasha uses Telegram polling, so you do not need a webhook or public URL for local use.

## Configuration

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `TELEGRAM_TOKEN` | Yes | none | Telegram bot token from BotFather |
| `XAI_API_KEY` | Yes | none | xAI API key for Grok |
| `OWNER_ID` | No | `1346274959` | Telegram user ID allowed to run owner-only commands |
| `DB_PATH` | No | `natasha.db` | SQLite database path |
| `TENOR_API_KEY` | No | empty | Enables Tenor GIF reactions |

Imgflip and MyInstants do not need API keys. They are scraped from public pages, so those integrations are best-effort.

## Media Reactions

Grok is instructed to use hidden media tags when a reaction fits. The bot removes these tags from the visible text, downloads the media, and sends it to the chat.

```text
[gif: facepalm]
[meme: en | drake]
[meme: tr | distracted boyfriend]
[sound: ru | bruh]
[sound: tr | bass boosted]
[sound: en | airhorn]
```

Language hints can be `tr`, `ru`, or `en`. If Grok omits the language, Natasha infers it from the reply.

Sound reactions are downloaded from MyInstants as MP3 files. When `ffmpeg` is available, Natasha converts them to OGG/Opus and sends them as Telegram voice notes. If conversion fails, it falls back to normal audio.

## Commands

Owner-only commands:

```text
/start
/ping
/nick <name>
/remember <note>
/forget
/whoami
/memory
```

Public commands:

```text
/russianroulette
/rr
```

Russian roulette mute behavior requires the bot to be an admin in the group.

## Deployment

This repo is ready for Railway-style deployment:

| File | Purpose |
| --- | --- |
| `Procfile` | Starts the bot with `python natasha_bot.py` |
| `nixpacks.toml` | Installs Python, `ffmpeg`, Python dependencies, then starts the bot |
| `requirements.txt` | Python packages needed at runtime |

For persistent memory on Railway, mount a volume and set:

```bash
DB_PATH=/data/natasha.db
```

After changing dependencies, make sure the latest commit is pushed and the service is rebuilt. If deployment logs show `ModuleNotFoundError: No module named 'requests'`, the deployed revision does not include the current `requirements.txt` or did not rebuild dependencies.

## Project Layout

```text
.
├── natasha_bot.py      # bot logic, Grok calls, media scraping, commands
├── requirements.txt    # Python dependencies
├── nixpacks.toml       # Railway/Nixpacks build and start config
├── Procfile            # process start command
└── LICENSE
```

## Notes

- Keep Telegram privacy mode disabled if Natasha should react to regular group chatter.
- Media scraping can break if Imgflip or MyInstants changes page markup.
- Tenor GIFs require `TENOR_API_KEY`; memes and sounds do not require keys.
- The bot stores local SQLite memory. Use a persistent `DB_PATH` in production.
