"""
Natasha — Telegram group bot powered by xAI grok-4.3 (Responses API).

Required env vars (Railway dashboard → Variables):
  TELEGRAM_TOKEN   from @BotFather
  XAI_API_KEY      from console.x.ai

BotFather — MUST do or bot is blind to group messages:
  /mybots → your bot → Bot Settings → Group Privacy → Turn off
Then make the bot an Admin in your group.

Debug commands:
  /ping     — confirm bot is alive
  /testapi  — test xAI connection
"""

import os
import random
import logging
import threading
from datetime import datetime, timedelta, timezone
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
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
PORT           = int(os.environ.get("PORT", "8080"))

if not TELEGRAM_TOKEN:
    raise SystemExit("FATAL: TELEGRAM_TOKEN is not set.")
if not XAI_API_KEY:
    raise SystemExit("FATAL: XAI_API_KEY is not set.")

MODEL         = "grok-4.3"
XAI_URL       = "https://api.x.ai/v1/responses"
XAI_HEADERS   = {
    "Content-Type":  "application/json",
    "Authorization": f"Bearer {XAI_API_KEY}",
}

CHAOS_CHANCE  = 0.06
HISTORY_LEN   = 14
MAX_TOKENS    = 400
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
    "Turkish → slangy Turkish. Russian → slangy Russian, not stiff textbook Russian. "
    "Never mix languages unless the user did. Match their energy and register. "
    "Your name is Natasha. Never admit you are an AI or a bot. Never break character. "
    "Hard limits: no slurs, no harassment of protected groups, nothing that enables "
    "real-world harm. Everything else is fair game."
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("natasha")

# Per-chat message history  {chat_id: deque of {"role": ..., "content": ...}}
history: dict[int, deque] = defaultdict(lambda: deque(maxlen=HISTORY_LEN))

FALLBACKS = {
    "ru": ["мозг завис, попробуй ещё раз", "лагаю, спроси позже"],
    "tr": ["beynim mavi ekran verdi, bi daha dene", "kasıyorum, sonra sor"],
}

# ---------------------------------------------------------------------------
# Russian roulette
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


def user_lang(update: Update) -> str:
    code = (update.effective_user.language_code or "").lower()
    return "ru" if code.startswith("ru") else "tr"


def detect_lang(text: str) -> str:
    return "ru" if any("Ѐ" <= ch <= "ӿ" for ch in text) else "tr"


# ---------------------------------------------------------------------------
# xAI Responses API
# ---------------------------------------------------------------------------
def _extract_text(data: dict) -> str:
    """Pull the assistant text out of a Responses API reply."""
    for item in data.get("output", []):
        if item.get("type") == "message":
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    return block.get("text", "").strip()
    return ""


def call_grok(chat_id: int) -> str:
    payload = {
        "model":        MODEL,
        "instructions": SYSTEM_PROMPT,
        "input":        list(history[chat_id]),
        "reasoning":    {"effort": "low"},
        "max_output_tokens": MAX_TOKENS,
    }
    try:
        r = httpx.post(XAI_URL, headers=XAI_HEADERS, json=payload, timeout=30.0)
        r.raise_for_status()
        text = _extract_text(r.json())
        if not text:
            log.error("Empty text in response: %s", r.text[:300])
            raise ValueError("empty response")
        return text
    except Exception as exc:
        log.error("Grok error: %s", exc)
        last = history[chat_id][-1]["content"] if history[chat_id] else ""
        return random.choice(FALLBACKS[detect_lang(last)])


# ---------------------------------------------------------------------------
# Telegram handlers
# ---------------------------------------------------------------------------
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text:
        return

    chat_id  = msg.chat_id
    name     = (msg.from_user.first_name if msg.from_user else None) or "someone"
    text_low = msg.text.lower()

    history[chat_id].append({"role": "user", "content": f"{name}: {msg.text}"})

    bot_username  = (context.bot.username or "").lower()
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
        "chat=%s [%s] from=%s | mention=%s wake=%s reply=%s chaos=%s => %s | %r",
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


async def cmd_testapi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    try:
        r = httpx.post(
            XAI_URL,
            headers=XAI_HEADERS,
            json={
                "model":     MODEL,
                "input":     "Reply with exactly: API OK",
                "reasoning": {"effort": "low"},
            },
            timeout=30.0,
        )
        r.raise_for_status()
        text = _extract_text(r.json())
        await update.effective_message.reply_text(
            f"✅ xAI API OK\nModel: {MODEL}\nResponse: {text}"
        )
    except Exception as exc:
        await update.effective_message.reply_text(
            f"❌ xAI API error:\n{exc}"
        )


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
                log.info("Mute skipped (need admin): %s", exc)
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
        f"я тут 🏓  @{me.username} | model={MODEL}"
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "ну привет. тегайте или отвечайте — отвечу 😈\n"
        "ya buradayım. etiketleyin ya da yanıtlayın 😈"
    )


# ---------------------------------------------------------------------------
# Railway health-check server
# ---------------------------------------------------------------------------
class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def _start_health_server():
    HTTPServer(("0.0.0.0", PORT), _HealthHandler).serve_forever()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    log.info("Natasha starting | model=%s | port=%s", MODEL, PORT)
    threading.Thread(target=_start_health_server, daemon=True).start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("ping",    cmd_ping))
    app.add_handler(CommandHandler("testapi", cmd_testapi))
    app.add_handler(CommandHandler(["rr", "russianroulette"], cmd_roulette))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    log.info("Polling started.")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
