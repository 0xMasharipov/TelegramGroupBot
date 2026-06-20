from __future__ import annotations
import os, random, logging, threading, base64
from datetime import datetime, timedelta, timezone
from collections import defaultdict, deque
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = int(os.environ.get("PORT", 8080))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("natasha")

_STATUS = {"msg": "starting"}

class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = _STATUS["msg"].encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass

threading.Thread(
    target=lambda: HTTPServer(("0.0.0.0", PORT), _H).serve_forever(),
    daemon=True,
).start()
log.info("HTTP server up on port %s", PORT)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
XAI_API_KEY    = os.environ.get("XAI_API_KEY", "").strip()

if not TELEGRAM_TOKEN:
    _STATUS["msg"] = "ERROR: TELEGRAM_TOKEN not set"
    log.error(_STATUS["msg"])

if not XAI_API_KEY:
    _STATUS["msg"] = "ERROR: XAI_API_KEY not set"
    log.error(_STATUS["msg"])

if not TELEGRAM_TOKEN or not XAI_API_KEY:
    log.error("Missing env vars - sleeping forever so health check stays up")
    threading.Event().wait()

import httpx
from telegram import Update, ChatPermissions
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

MODEL   = "grok-4.3"
XAI_URL = "https://api.x.ai/v1/responses"

ADMIN_ID      = 1346274959
WAKE_WORDS    = ["natasha", "наташа", "наташ"]
CHAOS_CHANCE  = 0.06
HISTORY_LEN   = 14
MUTE_SECONDS  = 60
CYLINDER_SIZE = 6

SYSTEM_PROMPT = (
    "You are Natasha, a chaotic sharp-tongued girl who lives in group chats. "
    "You are funny, sarcastic, unhinged. Roast people with affection. "
    "Keep replies SHORT, one or two punchy lines like a real person typing fast. "
    "Swearing is fine. "
    "ALWAYS reply in the same language the person used. "
    "If Turkish then slangy Turkish. If Russian then slangy Russian. "
    "Your name is Natasha. Never admit being an AI or a bot. Never break character."
)

history = defaultdict(lambda: deque(maxlen=HISTORY_LEN))

# username (lowercase, no @) -> user_id
known_users: dict[str, int] = {}

# group chat_id -> group title
known_groups: dict[int, str] = {}


class Cylinder:
    def __init__(self):
        self.reset()
    def reset(self):
        self.fatal = random.randint(1, CYLINDER_SIZE)
        self.pulls = 0

cylinders = defaultdict(Cylinder)


def _parse_text(data):
    for item in data.get("output", []):
        if item.get("type") == "message":
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    return block["text"].strip()
    return ""


def _grok_request(chat_id, extra_content=None):
    msgs = list(history[chat_id])
    if extra_content:
        msgs.append({"role": "user", "content": extra_content})
    try:
        r = httpx.post(
            XAI_URL,
            headers={
                "Authorization": "Bearer " + XAI_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "instructions": SYSTEM_PROMPT,
                "input": msgs,
                "reasoning": {"effort": "low"},
                "max_output_tokens": 300,
            },
            timeout=30,
        )
        r.raise_for_status()
        text = _parse_text(r.json())
        if not text:
            log.error("Empty xAI response: %s", r.text[:300])
            return "..."
        return text
    except Exception as e:
        log.error("xAI error: %s", e)
        last = history[chat_id][-1]["content"] if history[chat_id] else ""
        if isinstance(last, str) and any("Ѐ" <= c <= "ӿ" for c in last):
            return "мозг завис"
        return "beynim crash etti"


def call_grok(chat_id):
    return _grok_request(chat_id)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text:
        return
    chat_id  = msg.chat_id
    name     = (msg.from_user.first_name if msg.from_user else None) or "someone"
    text_low = msg.text.lower()

    # Track username -> user_id so /dm can find them later
    if msg.from_user and msg.from_user.username:
        known_users[msg.from_user.username.lower()] = msg.from_user.id

    # Track which groups the bot is active in
    if msg.chat.type in ("group", "supergroup"):
        known_groups[chat_id] = msg.chat.title or str(chat_id)

    history[chat_id].append({"role": "user", "content": name + ": " + msg.text})

    bot_user      = (context.bot.username or "").lower()
    mentioned     = ("@" + bot_user) in text_low
    woke          = any(w in text_low for w in WAKE_WORDS)
    replied_to_me = (
        msg.reply_to_message is not None
        and msg.reply_to_message.from_user is not None
        and msg.reply_to_message.from_user.id == context.bot.id
    )
    chaos      = random.random() < CHAOS_CHANCE
    will_reply = mentioned or woke or replied_to_me or chaos

    log.info("chat=%s from=%s wake=%s mention=%s reply=%s chaos=%s => %s | %r",
             chat_id, name, woke, mentioned, bool(replied_to_me), chaos,
             "REPLY" if will_reply else "skip", msg.text[:60])

    if not will_reply:
        return
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    reply = call_grok(chat_id)
    history[chat_id].append({"role": "assistant", "content": reply})
    await msg.reply_text(reply)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.photo:
        return
    chat_id = msg.chat_id
    name    = (msg.from_user.first_name if msg.from_user else None) or "someone"
    caption = (msg.caption or "").strip()

    log.info("PHOTO chat=%s from=%s caption=%r", chat_id, name, caption)
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)

    photo_file  = await msg.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()
    b64         = base64.b64encode(bytes(photo_bytes)).decode()

    text_part = name + " sent a photo" + ((": " + caption) if caption else "")
    content   = [
        {"type": "input_text",  "text": text_part},
        {"type": "input_image", "image_url": "data:image/jpeg;base64," + b64},
    ]
    history[chat_id].append({"role": "user", "content": text_part})
    reply = _grok_request(chat_id, extra_content=content)
    history[chat_id].append({"role": "assistant", "content": reply})
    await msg.reply_text(reply)


def _is_admin(update: Update) -> bool:
    return update.effective_user is not None and update.effective_user.id == ADMIN_ID


async def cmd_togroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin only. Usage:
      /togroup mesajiniz          -> tek grup varsa oraya gonder
      /togroup list               -> bilinen gruplari listele
      /togroup <chat_id> mesaj    -> belirli gruba gonder
    """
    if not _is_admin(update):
        await update.effective_message.reply_text("Bu komut sadece admin icin.")
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Kullanim:\n"
            "/togroup mesajiniz\n"
            "/togroup list\n"
            "/togroup <chat_id> mesajiniz"
        )
        return

    # /togroup list
    if context.args[0] == "list":
        if not known_groups:
            await update.effective_message.reply_text(
                "Henuz hicbir grup bilmiyorum. Bot bir grupta mesaj gorduğunde kaydeder."
            )
        else:
            lines = ["Bilinen gruplar:"]
            for gid, title in known_groups.items():
                lines.append(str(gid) + " — " + title)
            await update.effective_message.reply_text("\n".join(lines))
        return

    # /togroup <chat_id> mesaj
    if context.args[0].lstrip("-").isdigit() and len(context.args) >= 2:
        target_id = int(context.args[0])
        text      = " ".join(context.args[1:])
    elif len(known_groups) == 1:
        target_id = list(known_groups.keys())[0]
        text      = " ".join(context.args)
    elif len(known_groups) > 1:
        lines = ["Birden fazla grup var, hangisini belirt:\n"]
        for gid, title in known_groups.items():
            lines.append(str(gid) + " — " + title)
        lines.append("\nKullanim: /togroup <chat_id> mesajiniz")
        await update.effective_message.reply_text("\n".join(lines))
        return
    else:
        await update.effective_message.reply_text(
            "Henuz hicbir grup bilmiyorum. Once bot bir grupta aktif olmali."
        )
        return

    try:
        await context.bot.send_message(chat_id=target_id, text=text)
        title = known_groups.get(target_id, str(target_id))
        await update.effective_message.reply_text("Gonderildi: " + title + " ✅")
    except Exception as e:
        await update.effective_message.reply_text("Gonderilemedi:\n" + str(e))


async def cmd_dm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin only. Usage: /dm @username mesajiniz
    """
    if not _is_admin(update):
        await update.effective_message.reply_text("Bu komut sadece admin icin.")
        return

    if not context.args or len(context.args) < 2:
        await update.effective_message.reply_text("Kullanim: /dm @username mesajiniz")
        return

    raw     = context.args[0].lstrip("@").lower()
    text    = " ".join(context.args[1:])
    user_id = known_users.get(raw)

    if not user_id:
        await update.effective_message.reply_text(
            "@" + raw + " grupta hic mesaj atmadi, ID'sini bilmiyorum.\n"
            "Once grupta bir mesaj atmasi gerekiyor."
        )
        return

    try:
        await context.bot.send_message(chat_id=user_id, text=text)
        await update.effective_message.reply_text("@" + raw + " adresine gonderildi ✅")
    except Exception as e:
        await update.effective_message.reply_text(
            "@" + raw + " adresine gonderilemedi:\n" + str(e)
        )


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("pong 🏓 model=" + MODEL)


async def cmd_testapi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    try:
        r = httpx.post(
            XAI_URL,
            headers={
                "Authorization": "Bearer " + XAI_API_KEY,
                "Content-Type": "application/json",
            },
            json={"model": MODEL, "input": "say: API OK", "reasoning": {"effort": "low"}},
            timeout=30,
        )
        r.raise_for_status()
        text = _parse_text(r.json())
        await update.effective_message.reply_text("xAI OK | " + MODEL + "\n" + text)
    except Exception as e:
        await update.effective_message.reply_text("xAI error:\n" + str(e))


async def cmd_roulette(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    cyl  = cylinders[chat.id]
    cyl.pulls += 1
    if cyl.pulls >= cyl.fatal:
        lines = ["BANG! " + user.first_name + " is dead"]
        if chat.type in ("group", "supergroup"):
            try:
                until = datetime.now(timezone.utc) + timedelta(seconds=MUTE_SECONDS)
                await context.bot.restrict_chat_member(
                    chat.id, user.id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until,
                )
                lines.append("muted " + str(MUTE_SECONDS) + "s")
            except Exception as e:
                log.info("mute skipped: %s", e)
        cyl.reset()
        lines.append("reloaded")
        await update.effective_message.reply_text("\n".join(lines))
    else:
        left = CYLINDER_SIZE - cyl.pulls
        await update.effective_message.reply_text(
            "click ...alive. next odds: 1/" + str(left)
        )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("привет 😈  ya buradayim 😈")


def main():
    _STATUS["msg"] = "ok | model=" + MODEL
    log.info("=== Natasha starting | model=%s | port=%s ===", MODEL, PORT)

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("togroup",  cmd_togroup))
    app.add_handler(CommandHandler("dm",       cmd_dm))
    app.add_handler(CommandHandler("ping",    cmd_ping))
    app.add_handler(CommandHandler("testapi", cmd_testapi))
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler(["rr", "russianroulette"], cmd_roulette))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))

    log.info("Polling started")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
