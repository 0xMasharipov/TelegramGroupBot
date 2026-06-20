import os, random, logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict, deque

import httpx
from telegram import Update, ChatPermissions
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- config ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
XAI_API_KEY    = os.environ["XAI_API_KEY"]
PORT           = int(os.environ.get("PORT", 8080))
DOMAIN         = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()

MODEL   = "grok-4.3"
XAI_URL = "https://api.x.ai/v1/responses"

WAKE_WORDS    = ["natasha", "наташа", "наташ"]
CHAOS_CHANCE  = 0.06
HISTORY_LEN   = 14
MUTE_SECONDS  = 60
CYLINDER_SIZE = 6

SYSTEM_PROMPT = (
    "You are Natasha — a chaotic, sharp-tongued girl who lives in group chats. "
    "Funny, sarcastic, unhinged. Roast people with affection. SHORT replies — one or "
    "two punchy lines like a real person. Swearing fine. "
    "ALWAYS reply in the same language the person used. Turkish → slangy Turkish. "
    "Russian → slangy Russian. Your name is Natasha. Never admit being an AI."
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("natasha")

history: dict[int, deque] = defaultdict(lambda: deque(maxlen=HISTORY_LEN))

# --- roulette ---
class Cylinder:
    def __init__(self): self.reset()
    def reset(self): self.fatal = random.randint(1, CYLINDER_SIZE); self.pulls = 0

cylinders: dict[int, Cylinder] = defaultdict(Cylinder)

# --- xAI ---
def call_grok(chat_id: int) -> str:
    try:
        r = httpx.post(
            XAI_URL,
            headers={"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": MODEL,
                "instructions": SYSTEM_PROMPT,
                "input": list(history[chat_id]),
                "reasoning": {"effort": "low"},
                "max_output_tokens": 300,
            },
            timeout=30,
        )
        r.raise_for_status()
        for item in r.json().get("output", []):
            if item.get("type") == "message":
                for block in item.get("content", []):
                    if block.get("type") == "output_text":
                        return block["text"].strip()
        log.error("No output_text in response: %s", r.text[:300])
        return "..."
    except Exception as e:
        log.error("xAI error: %s", e)
        last = history[chat_id][-1]["content"] if history[chat_id] else ""
        return "мозг завис" if any("Ѐ" <= c <= "ӿ" for c in last) else "beynim mavi ekran"

# --- handlers ---
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text:
        return
    chat_id  = msg.chat_id
    name     = (msg.from_user.first_name if msg.from_user else None) or "someone"
    text_low = msg.text.lower()
    history[chat_id].append({"role": "user", "content": f"{name}: {msg.text}"})

    bot_user      = (context.bot.username or "").lower()
    mentioned     = f"@{bot_user}" in text_low
    woke          = any(w in text_low for w in WAKE_WORDS)
    replied_to_me = (msg.reply_to_message and msg.reply_to_message.from_user
                     and msg.reply_to_message.from_user.id == context.bot.id)
    chaos         = random.random() < CHAOS_CHANCE
    will_reply    = mentioned or woke or replied_to_me or chaos

    log.info("chat=%s from=%s | wake=%s mention=%s reply=%s chaos=%s => %s | %r",
             chat_id, name, woke, mentioned, bool(replied_to_me), chaos,
             "REPLY" if will_reply else "skip", msg.text[:60])

    if not will_reply:
        return
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    reply = call_grok(chat_id)
    history[chat_id].append({"role": "assistant", "content": reply})
    await msg.reply_text(reply)


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(f"pong 🏓 model={MODEL}")

async def cmd_testapi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    try:
        r = httpx.post(XAI_URL,
            headers={"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"},
            json={"model": MODEL, "input": "say: API OK", "reasoning": {"effort": "low"}},
            timeout=30)
        r.raise_for_status()
        text = next(
            (b["text"] for item in r.json().get("output", []) if item.get("type") == "message"
             for b in item.get("content", []) if b.get("type") == "output_text"), "no text")
        await update.effective_message.reply_text(f"✅ xAI OK | {MODEL}\n{text}")
    except Exception as e:
        await update.effective_message.reply_text(f"❌ xAI error:\n{e}")

async def cmd_roulette(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    cyl = cylinders[chat.id]; cyl.pulls += 1
    if cyl.pulls >= cyl.fatal:
        lines = [f"💥 BANG! {user.first_name} is dead 🪦"]
        if chat.type in ("group", "supergroup"):
            try:
                until = datetime.now(timezone.utc) + timedelta(seconds=MUTE_SECONDS)
                await context.bot.restrict_chat_member(chat.id, user.id,
                    permissions=ChatPermissions(can_send_messages=False), until_date=until)
                lines.append(f"🤐 muted {MUTE_SECONDS}s")
            except Exception as e:
                log.info("mute skipped: %s", e)
        cyl.reset(); lines.append("🎲 reloaded")
        await update.effective_message.reply_text("\n".join(lines))
    else:
        await update.effective_message.reply_text(
            f"🔫 *click* ...alive. next odds: 1/{CYLINDER_SIZE - cyl.pulls}")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("ну привет 😈  ya buradayım 😈")

# --- main ---
def main():
    log.info("=== Natasha starting | model=%s | port=%s | domain=%s ===", MODEL, PORT, DOMAIN or "none")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("ping",    cmd_ping))
    app.add_handler(CommandHandler("testapi", cmd_testapi))
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler(["rr", "russianroulette"], cmd_roulette))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    if DOMAIN:
        webhook_url = f"https://{DOMAIN}/"
        log.info("Webhook mode → %s", webhook_url)
