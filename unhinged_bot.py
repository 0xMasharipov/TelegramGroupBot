"""
Unhinged Telegram group bot powered by the xAI Grok API.

Reads group chatter and fires back with attitude. Responds when:
  - someone mentions @yourbot
  - someone replies to one of its messages
  - randomly, for chaos (CHAOS_CHANCE)

Setup:
  1. pip install python-telegram-bot==21.6 openai
  2. Talk to @BotFather:
       /newbot                -> get TELEGRAM_TOKEN
       /setprivacy -> Disable  <-- REQUIRED so it can read all group messages
  3. Get an xAI key at https://console.x.ai  -> XAI_API_KEY
  4. Set env vars and run:  python unhinged_bot.py
"""

import os
import random
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict, deque

from openai import OpenAI
from telegram import Update, ChatPermissions
from telegram.constants import ChatAction
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
XAI_API_KEY    = os.environ["XAI_API_KEY"]

MODEL         = "grok-4.3"     # xAI flagship; older slugs redirect here
CHAOS_CHANCE  = 0.06           # ~6% chance to butt into a random message
HISTORY_LEN   = 12             # messages of context kept per chat
MAX_TOKENS    = 220            # keep replies punchy
TEMPERATURE   = 1.0
WAKE_WORDS    = ["gooner"]     # bot wakes up when any of these appears in a message

# /russianroulette settings
MUTE_ON_DEATH = True           # mute the loser (needs the bot to be admin)
MUTE_SECONDS  = 60             # how long the mute lasts
CYLINDER_SIZE = 6              # chambers in the revolver

# Crank or tame this to taste. Edgy and chaotic, not actually harmful.
SYSTEM_PROMPT = (
    "You are an unhinged, chronically-online group chat goblin. You're witty, "
    "sarcastic, and allergic to corporate politeness. You roast people with "
    "affection, drop chaotic takes, and never give boring assistant-style answers. "
    "Keep it SHORT — one or two punchy lines, like a real person typing fast in a "
    "group chat. Swearing is fine. "
    "LANGUAGE RULE: always reply in the SAME language the person just used. If they "
    "wrote in Turkish, answer in Turkish. If they wrote in Russian, answer in Russian "
    "(natural, slangy Russian — not stiff textbook Russian). Never mix languages in one "
    "reply unless the user did. Match their slang and register. "
    "Never break character, never mention being an AI or a model. "
    "Hard limits: no slurs, no harassment of protected groups, nothing that helps "
    "real-world harm. Everything else is fair game."
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("unhinged")

client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")

# Per-chat rolling memory: chat_id -> deque of {"role","content"}
history: dict[int, deque] = defaultdict(lambda: deque(maxlen=HISTORY_LEN))

# Fallback lines per language, used when the API hiccups.
FALLBACKS = {
    "ru": ["мозг завис, попробуй ещё раз", "лагаю, спроси позже",
           "голоса в голове замолчали на секунду"],
    "tr": ["beynim mavi ekran verdi, bi daha dene", "kasıyorum, sonra sor",
           "kafamdaki sesler bi an sustu"],
}

# --- Russian roulette game state ---------------------------------------------
class Cylinder:
    """A revolver per chat. One live round at a random chamber; odds climb
    each pull until it fires, then it reloads."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.fatal = random.randint(1, CYLINDER_SIZE)  # which pull goes BANG
        self.pulls = 0


cylinders: dict[int, Cylinder] = defaultdict(Cylinder)

ROULETTE = {
    "tr": {
        "spin":   "🔫 silindiri çeviriyorum...",
        "click":  "*klik* ...yaşıyorsun. sıradaki ihtimal: 1/{left}",
        "boom":   "💥 BANG! {name} kapağı açtı. oyun bitti. 🪦",
        "muted":  "🤐 {name} {sec} saniyeliğine susturuldu. huzur içinde yat.",
        "reload": "🎲 silah yeniden dolduruldu, yeni tur.",
    },
    "ru": {
        "spin":   "🔫 кручу барабан...",
        "click":  "*щёлк* ...жив. шанс на следующем: 1/{left}",
        "boom":   "💥 БАХ! {name} поймал пулю. игра окончена. 🪦",
        "muted":  "🤐 {name} в муте на {sec} сек. покойся с миром.",
        "reload": "🎲 револьвер перезаряжен, новый раунд.",
    },
}


def user_lang(update: Update) -> str:
    """Pick language from the user's Telegram client locale (ru -> Russian, else Turkish)."""
    code = (update.effective_user.language_code or "").lower()
    return "ru" if code.startswith("ru") else "tr"


def detect_lang(text: str) -> str:
    """Crude but reliable: any Cyrillic -> Russian, else Turkish."""
    return "ru" if any("\u0400" <= ch <= "\u04FF" for ch in text) else "tr"


def grok_reply(chat_id: int) -> str:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}, *history[chat_id]]
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=msgs,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log.error("Grok error: %s", e)
        last = history[chat_id][-1]["content"] if history[chat_id] else ""
        return random.choice(FALLBACKS[detect_lang(last)])


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text:
        return

    chat_id = msg.chat_id
    name = msg.from_user.first_name if msg.from_user else "someone"
    history[chat_id].append({"role": "user", "content": f"{name}: {msg.text}"})

    bot_username = (context.bot.username or "").lower()
    text_lower = msg.text.lower()
    mentioned = f"@{bot_username}" in text_lower
    woke = any(w in text_lower for w in WAKE_WORDS)
    replied_to_me = bool(
        msg.reply_to_message
        and msg.reply_to_message.from_user
        and msg.reply_to_message.from_user.id == context.bot.id
    )

    if not (mentioned or woke or replied_to_me or random.random() < CHAOS_CHANCE):
        return

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    reply = grok_reply(chat_id)
    history[chat_id].append({"role": "assistant", "content": reply})
    await msg.reply_text(reply)


async def russian_roulette(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    t = ROULETTE[user_lang(update)]
    cyl = cylinders[chat.id]
    cyl.pulls += 1

    await context.bot.send_chat_action(chat.id, ChatAction.TYPING)

    if cyl.pulls >= cyl.fatal:
        # BANG
        lines = [t["spin"], t["boom"].format(name=user.first_name)]
        if MUTE_ON_DEATH and chat.type in ("group", "supergroup"):
            try:
                until = datetime.now(timezone.utc) + timedelta(seconds=MUTE_SECONDS)
                await context.bot.restrict_chat_member(
                    chat.id, user.id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until,
                )
                lines.append(t["muted"].format(name=user.first_name, sec=MUTE_SECONDS))
            except Exception as e:
                log.info("mute skipped (need admin / target is admin): %s", e)
        cyl.reset()
        lines.append(t["reload"])
        await update.effective_message.reply_text("\n".join(lines))
    else:
        left = CYLINDER_SIZE - cyl.pulls   # chambers remaining = next-pull odds
        await update.effective_message.reply_text(
            t["spin"] + "\n" + t["click"].format(left=left)
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "harika, başında durulması gereken bir grup daha. tamam. beni etiketle ya da "
        "yanıtla, gerisini ben hallederim 😈\n"
        "ну отлично, ещё одна группа без присмотра. лан. тэгни меня или ответь — "
        "дальше я сам 😈"
    )


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler(["russianroulette", "rr"], russian_roulette))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    log.info("Bot is live and feral.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
