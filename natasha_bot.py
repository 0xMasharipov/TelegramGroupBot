import os, random, logging, threading
from datetime import datetime, timedelta, timezone
from collections import defaultdict, deque
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── PORT is the only thing read before the HTTP server starts ──────────────
PORT = int(os.environ.get("PORT", 8080))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("natasha")

# ── Start HTTP server on PORT immediately ──────────────────────────────────
# Must happen before ANYTHING else so Railway never sees a 502.
# Daemon=True means it lives as long as the main thread lives.
_STATUS = {"msg": "starting"}

class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = _STATUS["msg"].encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass

threading.Thread(
    target=lambda: HTTPServer(("0.0.0.0", PORT), _H).serve_forever(),
    daemon=True,
).start()
log.info("HTTP server up on port %s", PORT)

# ── Read env vars — server is already up so any crash here won't 502 ───────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
XAI_API_KEY    = os.environ.get("XAI_API_KEY", "").strip()

if not TELEGRAM_TOKEN:
    _STATUS["msg"] = "ERROR: TELEGRAM_TOKEN env var not set"
    log.error(_STATUS["msg"])

if not XAI_API_KEY:
    _STATUS["msg"] = "ERROR: XAI_API_KEY env var not set"
    log.error(_STATUS["msg"])

if not TELEGRAM_TOKEN or not XAI_API_KEY:
    # Block main thread forever — keeps daemon HTTP server alive
    # so you can curl the URL and read the error
    threading.Event().wait()

# ── Heavy imports after env check ──────────────────────────────────────────
import httpx
from telegram import Update, ChatPermissions
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
)

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
