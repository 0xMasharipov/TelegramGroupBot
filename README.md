# Natasha Telegram Group Bot

**Natasha** is a Grok-powered Telegram group bot for chaotic, multilingual group chats. She replies like a real chat member, remembers lightweight context, reacts to photos, and can drop GIFs, Imgflip meme templates, and MyInstants voice-note sounds without needing slash commands.

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-Bot-26A5E4?style=flat-square&logo=telegram&logoColor=white)
![Grok](https://img.shields.io/badge/xAI-Grok-111111?style=flat-square)
![SQLite](https://img.shields.io/badge/Memory-SQLite-003B57?style=flat-square&logo=sqlite&logoColor=white)

```text
Group chat -> Natasha -> Grok reply -> text + optional GIF / meme / voice reaction
```

## What It Does

| Capability | Details |
| --- | --- |
| Natural replies | Short, casual replies in Turkish, Russian, or English |
| Smart triggers | Responds to mentions, replies, wake words, and occasional chaos |
| Memory | Keeps recent context, nicknames, and notes in SQLite |
| Vision | Reacts to photos with Grok vision |
| GIFs | Uses Tenor when `TENOR_API_KEY` is set |
| Memes | Finds matching Imgflip meme templates from the chat context |
| Voice sounds | Downloads MyInstants sounds and sends them as Telegram voice notes |
| Mini-game | Includes a public Russian roulette command |

## Quick Start

Create a bot with BotFather, disable privacy mode if she should read group chatter, then run:

```bash
pip install -r requirements.txt

export TELEGRAM_TOKEN="your-telegram-bot-token"
export XAI_API_KEY="your-xai-api-key"

python natasha_bot.py
```

Natasha uses Telegram polling, so local development does not need a webhook or public URL.

## Configuration

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `TELEGRAM_TOKEN` | Yes | none | Telegram bot token from BotFather |
| `XAI_API_KEY` | Yes | none | xAI API key for Grok |
| `DB_PATH` | No | `natasha.db` | SQLite database location |
| `TENOR_API_KEY` | No | empty | Enables Tenor GIF reactions |
| `OWNER_ID` | No | not set | Set this yourself to enable owner tools |

Imgflip meme templates and MyInstants sounds use public pages and do not require API keys.

To enable owner tools, set your own Telegram user ID:

```bash
export OWNER_ID="x"
```

Replace `x` with your actual numeric Telegram user ID. Natasha does not include a hardcoded owner ID.

## Media Reactions

Grok can emit hidden media tags. Natasha removes the tag from the visible reply, downloads the matching media, and sends it to the chat.

```text
[gif: facepalm]
[meme: en | drake]
[meme: tr | distracted boyfriend]
[sound: ru | bruh]
[sound: tr | bass boosted]
[sound: en | airhorn]
```

Rules:

- `tr`, `ru`, and `en` help match the chat language.
- If Grok omits the language, Natasha infers it from the reply.
- Meme searches are based on the current chat, image, joke, or direct user request.
- If no matching meme template exists, Natasha skips it instead of sending a random unrelated meme.
- MyInstants MP3 files are converted to OGG/Opus voice notes when `ffmpeg` is available.
- If voice conversion fails, Natasha falls back to regular Telegram audio.

## Commands

Owner tools:

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

Roulette mute behavior requires Natasha to be an admin in the group.

## Deploy

The repo includes deployment files for Railway-style hosts:

| File | Role |
| --- | --- |
| `Procfile` | Runs `python natasha_bot.py` |
| `nixpacks.toml` | Installs Python, `ffmpeg`, dependencies, and starts the bot |
| `requirements.txt` | Python runtime dependencies |

For persistent memory, mount a volume and set:

```bash
DB_PATH=/data/natasha.db
```

After dependency changes, push the latest commits and force a clean rebuild. If logs show `ModuleNotFoundError: No module named 'requests'`, the deployed revision is stale or dependencies were not rebuilt.

## Project Structure

```text
.
├── natasha_bot.py      # bot logic, Grok calls, media scraping, commands
├── requirements.txt    # Python dependencies
├── nixpacks.toml       # Nixpacks build/start config
├── Procfile            # process command
└── LICENSE
```

## Notes

- Disable Telegram privacy mode if Natasha should read regular group messages.
- Give Natasha admin rights if roulette mute behavior should work.
- Imgflip and MyInstants scraping is best-effort and may need updates if their HTML changes.
- Use a persistent `DB_PATH` in production so memory survives redeploys.
