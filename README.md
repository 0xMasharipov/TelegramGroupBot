![Natasha — Grok-powered Telegram group bot](assets/natasha_readme_banner.png)

# Natasha Telegram Group Bot

**Natasha** is a Grok-powered Telegram group bot for chaotic, multilingual group chats. She replies like a real chat member, remembers lightweight context, reacts to photos, and can drop GIFs, Imgflip meme templates, and MyInstants voice-note sounds without needing slash commands.

![Natasha profile](assets/natasha_profile.png)

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
| Natural participation | Replies to every group message by default; no mention or wake word is required |
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
| `REPLY_TO_ALL_MESSAGES` | No | `1` | Set to `0` to restore mention/reply/random-chaos-only responses |
| `PERSONA_UTC_OFFSET_HOURS` | No | `3` | Local UTC offset used for time-aware Natasha photo settings |
| `IMAGE_MODEL` | No | `grok-imagine-image-quality` | xAI Imagine model for generated Natasha photos |
| `IMAGE_ASPECT_RATIO` | No | `1:1` | Aspect ratio for generated Natasha photos |
| `IMAGE_RESOLUTION` | No | `1k` | Image resolution, usually `1k` or `2k` |

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

## Generated Images

When a user asks Natasha to create, draw, generate, or send an image, she generates a new Grok Imagine image from the user's message and sends it directly to the Telegram chat. If the request refers to recent chat context, Natasha includes nearby messages in the image prompt so the generated image follows what the user meant.

When the request is specifically for Natasha's photo, selfie, avatar, or picture, the prompt locks her face, body proportions, hair, makeup, black knit sweater, choker, cross earrings, nail polish, and fixed room components. A request may change only her pose, camera angle, or mood; it cannot change her identity, outfit, or the scheduled room setup.

Her self-portrait background follows this local-time schedule:

| Local time | Background |
| --- | --- |
| 05:00–08:59 | Fully clothed in bed, in a quiet realistic bedroom with soft morning light |
| 09:00–11:59 | Working from her room as a freelance web designer, at a realistic desk setup |
| Other times | Fixed charcoal-gray wall, matte-black lamp, and warm practical light |

Every generated image gets a short, situation-aware caption from Grok. The caption is based on the request and recent chat, rather than a fixed day/night label. If a Natasha persona image generation fails, the bot falls back to the deployable profile asset:

`assets/natasha_profile.png`

### Natasha Imagine Prompt

Use this as the base prompt for Grok Imagine when generating Natasha selfies or new persona images:

```text
Photorealistic editorial portrait of Natasha, an original fictional adult Russian-goth woman in her mid-20s. She has pale natural skin with visible real texture, gray-green eyes with softly smudged black eyeliner, deep black lipstick, long straight jet-black hair with blunt bangs, and a small beauty mark. Her expression is calm, deadpan, and effortlessly cool. Use believable skin pores, individual hair strands, natural facial asymmetry, realistic camera lighting, and a restrained black wardrobe. Keep the same face, hairstyle, and goth identity recognizable across images. Fully clothed. No illustration, anime, CGI, beauty-filter look, plastic skin, nudity, explicit content, text, watermark, or extra characters.
```

Add only the requested pose, camera angle, or mood. Keep the canonical face, body proportions, outfit, accessories, and fixed scene components unchanged across every generated image.

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
├── assets/
│   ├── natasha_readme_banner.png
│   ├── natasha_persona_day.png
│   ├── natasha_persona_night.png
│   └── natasha_profile.png
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
