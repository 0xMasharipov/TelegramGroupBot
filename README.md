# Natasha Telegram Group Bot

Natasha is a Telegram group chat bot powered by xAI Grok. It reads group chatter, keeps lightweight memory in SQLite, replies in the user's language, and can automatically drop GIFs, Imgflip meme templates, and MyInstants sound reactions when Grok decides the moment needs it.

## Features

- Natural short replies in Turkish, Russian, or English
- Replies when mentioned, replied to, woken by configured wake words, or randomly via chaos mode
- SQLite memory for recent messages, nicknames, and notes
- Photo understanding through Grok vision
- Tenor GIF reactions with `[gif: query]`
- Imgflip meme template reactions with `[meme: lang | query]`
- MyInstants sound reactions with `[sound: lang | query]`
- Owner-only memory commands
- Russian roulette group mini-game

## Requirements

- Python 3.11+
- Telegram bot token from BotFather
- xAI API key
- Telegram privacy mode disabled for the bot if it should read all group messages

Install dependencies:

```bash
pip install -r requirements.txt
```

## Environment Variables

Required:

```bash
export TELEGRAM_TOKEN="your-telegram-bot-token"
export XAI_API_KEY="your-xai-api-key"
```

Optional:

```bash
export OWNER_ID="1346274959"
export DB_PATH="natasha.db"
export TENOR_API_KEY="your-tenor-api-key"
```

`TENOR_API_KEY` is only needed for GIF reactions. Imgflip template images and MyInstants sounds are scraped from public pages and do not require API keys.

## Run Locally

```bash
python natasha_bot.py
```

The bot uses polling, so it does not need a public webhook URL.

## Deployment

The repo includes both:

- `Procfile`: `web: python natasha_bot.py`
- `nixpacks.toml`: installs `requirements.txt` and starts `python natasha_bot.py`

For Railway or similar hosts, set the required environment variables in the project settings. For persistent memory, mount a volume and set:

```bash
DB_PATH=/data/natasha.db
```

## Media Tool Tags

Grok is instructed to use these tags sparingly and only when a reaction fits:

```text
[gif: facepalm]
[meme: en | drake]
[meme: tr | distracted boyfriend]
[sound: ru | bruh]
[sound: tr | bass boosted]
[sound: en | airhorn]
```

The bot strips these tags from text replies, downloads the matching media, and sends it to the chat. If Grok omits the language part, the bot infers `tr`, `ru`, or `en` from the reply text.

## Commands

Owner-only:

```text
/start
/ping
/nick <name>
/remember <note>
/forget
/whoami
/memory
```

Open to everyone:

```text
/russianroulette
/rr
```

## Notes

- Keep Telegram privacy mode disabled in BotFather for group chatter reading.
- The bot must be an admin if roulette mute behavior should work.
- MyInstants and Imgflip have no stable public API for these pages, so media scraping is best-effort and may need selector updates if their markup changes.
