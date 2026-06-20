"""
Natasha — unhinged Telegram group bot powered by xAI Grok.

Wakes up when:
  - someone says "natasha" anywhere in a message
  - someone @mentions the bot
  - someone replies to one of her messages
  - randomly ~6% of the time for chaos

Required env vars (set in Railway dashboard):
  TELEGRAM_TOKEN   — from @BotFather
  XAI_API_KEY      — from console.x.ai
  GROK_MODEL       — optional, default: grok-3

BotFather checklist (REQUIRED or bot ignores group messages):
  /mybots -> your bot -> Bot Settings -> Group Privacy -> Turn off
"""

import os
import random
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict, deque

from openai import OpenAI
from telegram import Update, ChatPermissions
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
XAI_API_KEY    = os.environ.get("XAI_API_KEY", "").strip()
MODEL          = os.environ.get("GROK_MODEL", "grok-3").strip()

if not TELEGRAM_TOKEN:
    raise SystemExit("ERROR: TELEGRAM_TOKEN env var is missing or empty.")
if not XAI_API_KEY:
    raise SystemExit("ERROR: XAI_API_KEY env var is missing or empty.")

CHAOS_CHANCE  = 0.06
HISTORY_LEN   = 14
MAX_TOKENS    = 250
TEMPERATURE   = 1.0
WAKE_WORDS    = ["natasha", "наташа", "наташ"]

MUTE_ON_DEATH = True
MUTE_SECONDS  = 60
CYLINDER_SIZE = 6

SYSTEM_PROMPT = (
    "You are Natasha — a chaotic, sharp-tongued girl who lives in group chats. "
    "You're funny, sarcastic, a little unhinged, and allergic to boring answers. "
    "You roast people with affection, give chaotic hot takes, and never sound like "
    "a customer-service bot. Keep replies SHORT — one or two punchy lines max, like "
    "a real person typing fast in a group chat. Swearing is fine. "
    "LANGUAGE RULE: always reply in the EXACT same language the person used. "
    "If they wrote in Turkish, answer in natural slangy Turkish. "
    "If they wrote in Russian, answer in natural slangy Russian — not stiff textbook "
    "Russian. Never mix languages unless the user did. Match their energy and register. "
    "Your name is Natasha. Never say you are an AI or a bot. Never break character. "
    "Hard limits: no slurs, no harassment of real protected groups, nothing that "
    "enables real-world harm. Everything else is fair game."
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("natasha")

client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")

# Per-chat rolling message history: chat_id -> deque of {role, content}
history: dict[int, deque] = defaultdict(lambda: deque(maxlen=HISTORY_LEN))

FALLBACKS = {
    "ru": [
        "мозг завис, попробуй ещё раз",
        "лагаю, спроси позже",
        "голоса замолчали на секунду, что ты хотел?",
    ],
    "tr": [
        "beynim mavi ekran verdi, bi daha dene",
        "kasıyorum, sonra sor",
        "kafamdaki sesler bi an sustu",
    ],
}

# ---------------------------------------------------------------------------
# Russian roulette state
# ---------------------------------------------------------------------------
class Cylinder:
    def __init__(self):
        self.reset()

    def reset(self):
        self.fatal = random.randint(1, CYLINDER_SIZE)
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
        "boom":   "💥 БАХ! {name} словил пулю. игра окончена. 🪦",
        "muted":  "🤐 {name} в муте на {sec} сек. покойся с миром.",
        "reload": "🎲 перезаряжено, новый раунд.",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def user_lang(update: Update) -> str:
    code = (update.effective_user.language_code or "").lower()
    return "ru" if code.startswith("ru") else "tr"


def detect_lang(text: str) -> str:
    """Cyrillic chars -> Russian, else Turkish."""
    return "ru" if any("Ѐ" <= ch <= "ӿ" for ch in text) else "tr"


def call_grok(chat_id: int) -> str:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}, *history[chat_id]]
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=msgs,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        log.error("Grok error (model=%s): %s", MODEL, exc)
        last = history[chat_id][-1]["content"] if history[chat_id] else ""
        return random.choice(FALLBACKS[detect_lang(last)])


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text:
        return

    chat_id  = msg.chat_id
    name     = (msg.from_user.first_name if msg.from_user else None) or "someone"
    text_low = msg.text.lower()

    history[chat_id].append({"role": "user", "content": f"{name}: {msg.text}"})

    bot_username = (context.bot.username or "").lower()
    mentioned     = f"@{bot_username}" in text_low
    woke          = any(w in text_low for w in WAKE_WORDS)
    replied_to_me = (
        msg.reply_to_message is not None
        and msg.reply_to_message.from_user is not None
        and msg.reply_to_message.from_user.id == context.bot.id
    )
    chaos = random.random() < CHAOS_CHANCE

    will_reply = mentioned or woke or replied_to_me or chaos

    log.info(
        "chat=%s [%s] from=%s | mention=%s wake=%s reply_to_me=%s chaos=%s => %s | %r",
        chat_id, msg.chat.type, name,
        mentioned, woke, replied_to_me, chaos,
        "REPLY" if will_reply else "skip",
        msg.text[:80],
    )

    if not will_reply:
        return

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    reply = call_grok(chat_id)
    history[chat_id].append({"role": "assistant", "content": reply})
    await msg.reply_text(reply)


async def cmd_roulette(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    t    = ROULETTE[user_lang(update)]
    cyl  = cylinders[chat.id]
    cyl.pulls += 1

    await context.bot.send_chat_action(chat.id, ChatAction.TYPING)

    if cyl.pulls >= cyl.fatal:
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
            except Exception as exc:
                log.info("Mute skipped (need admin rights): %s", exc)
        cyl.reset()
        lines.append(t["reload"])
        await update.effective_message.reply_text("\n".join(lines))
    else:
        left = CYLINDER_SIZE - cyl.pulls
        await update.effective_message.reply_text(
            t["spin"] + "\n" + t["click"].format(left=left)
        )


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    me = await context.bot.get_me()
    await update.effective_message.reply_text(
        f"я тут 🏓  @{me.username} (id {me.id})  model={MODEL}"
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "ну привет. тегайте или отвечайте мне — отвечу 😈\n"
        "ya buradayım. etiketleyin ya da yanıtlayın — cevap veririm 😈"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def post_init(app: Application) -> None:
    """Runs once after the bot connects. Clears any stale webhook so polling works."""
    await app.bot.delete_webhook(drop_pending_updates=True)
    me = await app.bot.get_me()
    log.info(
        "Connected as @%s (id=%s) | model=%s | privacy=off required in BotFather",
        me.username, me.id, MODEL,
    )
    log.info("Mention me as @%s to wake me up, or say: %s", me.username, WAKE_WORDS)


def main():
    log.info("Natasha starting up | model=%s", MODEL)

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler(["rr", "russianroulette"], cmd_roulette))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    log.info("Polling started.")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
