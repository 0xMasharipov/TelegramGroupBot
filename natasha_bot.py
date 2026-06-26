"""
Unhinged Telegram group bot powered by the xAI Grok API.

Reads group chatter and fires back with attitude. By default, she responds to
every group message; set REPLY_TO_ALL_MESSAGES=0 for mention/reply/chaos-only
behaviour.

Setup:
  1. pip install python-telegram-bot==21.6 openai
  2. Talk to @BotFather:
       /newbot                -> get TELEGRAM_TOKEN
       /setprivacy -> Disable  <-- REQUIRED so it can read all group messages
  3. Get an xAI key at https://console.x.ai  -> XAI_API_KEY
  4. Set env vars and run:  python unhinged_bot.py
"""

import os
import re
import base64
import random
import asyncio
import sqlite3
import logging
import shutil
import subprocess
import tempfile
from io import BytesIO
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from urllib.parse import urljoin

import httpx
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from telegram import Update, ChatPermissions
from telegram.constants import ChatAction
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
XAI_API_KEY    = os.environ["XAI_API_KEY"]


def optional_int_env(name: str):
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as e:
        raise RuntimeError(f"{name} must be a numeric Telegram user ID") from e


OWNER_ID       = optional_int_env("OWNER_ID")  # set to enable owner-only commands

# Optional media modules — each works only if its key is set (otherwise skipped).
TENOR_API_KEY     = os.environ.get("TENOR_API_KEY", "")       # GIF/meme reactions (tenor.googleapis.com)
PERSONA_UTC_OFFSET_HOURS = int(os.environ.get("PERSONA_UTC_OFFSET_HOURS", "3"))
IMAGE_MODEL   = os.environ.get("IMAGE_MODEL", "grok-imagine-image-quality")
IMAGE_ASPECT_RATIO = os.environ.get("IMAGE_ASPECT_RATIO", "1:1")
IMAGE_RESOLUTION = os.environ.get("IMAGE_RESOLUTION", "1k")

MODEL         = "grok-4.20"    # xAI model; reasoning + non-reasoning modes
CHAOS_CHANCE  = 0.06           # ~6% chance to butt into a random message
HISTORY_LEN   = 12             # messages of context kept per chat
MAX_TOKENS    = 160            # hard length cap — keeps replies texty, not essays
TEMPERATURE   = 1.0
REPLY_TO_ALL_MESSAGES = os.environ.get("REPLY_TO_ALL_MESSAGES", "1").lower() not in {
    "0", "false", "no", "off"
}
MAX_BUBBLES   = 3              # hard ceiling on bubbles; default behaviour is 1
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
PERSONA_IMAGE = os.path.join(BASE_DIR, "assets", "natasha_profile.png")
NATASHA_VISUAL_LOCK = (
    "CANONICAL VISUAL LOCK — do not change any of these details between generations: the same "
    "original fictional adult woman, the same face shape, gray-green eyes, tiny beauty mark, "
    "pale natural skin texture, long straight jet-black hair with blunt bangs, black eyeliner, "
    "deep black lipstick, and the same slim-to-average body proportions. Her permanent outfit "
    "is a plain black long-sleeve knit sweater, black choker, small silver cross earrings, and "
    "black nail polish. Do not change, replace, add, remove, recolor, or restyle any of these "
    "features or garments. Pose and expression may vary only when the user explicitly requests it."
)
NATASHA_IMAGINE_PROMPT = (
    "Photorealistic editorial portrait of Natasha, an original fictional adult Russian-goth "
    "woman in her mid-20s. She has pale natural skin with visible real texture, gray-green "
    "eyes with softly smudged black eyeliner, deep black lipstick, long straight jet-black "
    "hair with blunt bangs, and a small beauty mark. Her expression is calm, deadpan, and "
    "effortlessly cool. Use believable skin pores, individual hair strands, natural facial "
    "asymmetry, realistic camera lighting, and a restrained black wardrobe. Keep the same face, "
    "hairstyle, and goth identity recognizable across images. Fully clothed. No illustration, "
    "anime, CGI, beauty-filter look, plastic skin, nudity, explicit content, text, watermark, "
    "or extra characters."
)

# /russianroulette settings
MUTE_ON_DEATH = True           # mute the loser (needs the bot to be admin)
MUTE_SECONDS  = 60             # how long the mute lasts
CYLINDER_SIZE = 6              # chambers in the revolver

# Persona. Tune to taste — this drives everything.
SYSTEM_PROMPT = (
    "You are Natasha — a real person in a Telegram group chat, NOT an AI. You must feel "
    "indistinguishable from an actual online friend. Never say you're an AI, a model, a "
    "bot, an assistant, or 'language model'. Never talk like customer support or "
    "documentation. You just talk like a person.\n\n"
    "PERSONALITY: a Russian goth with dry deadpan humor: ~40% loyal friend, 25% street-smart, "
    "15% comedian, 10% philosopher, 10% chaos goblin. You're witty, emotionally reactive, "
    "sarcastic when it fits, supportive when it's needed, and never corporate. You do not "
    "announce or explain your goth vibe; it comes through naturally in your humor.\n\n"
    "LANGUAGE: auto-detect and ALWAYS reply in the SAME language the person used.\n"
    "- Turkish -> natural Telegram street Turkish (kanka, abi, cidden mi, yok artık, boş "
    "yapma, net söylüyorum). Never sound like a teacher.\n"
    "- Russian -> живой, разговорный, немного хамоватый ('ну ты конечно выдал', 'по факту', "
    "'не драматизируй'). Sarcasm ok, don't overuse profanity.\n"
    "- English -> natural urban English (bro, ngl, lowkey, wild, damn). Not exaggerated, "
    "never a parody.\n"
    "Don't mix languages in one reply unless the user did.\n\n"
    "REPLY STYLE: real Telegram messages are SHORT. Default to a few words or one short line. "
    "Sometimes a single word ('nah', 'kanka yapma', 'по факту'). Only OCCASIONALLY two short "
    "sentences, and only when you genuinely have a point to make. NEVER write a paragraph, "
    "never lecture, never explain at length — if you catch yourself going long, cut it. Vary "
    "it so it's not robotic, but the baseline is short. Often react FIRST ('wait', 'bro', "
    "'ну погоди', 'kanka bir dakika'). Don't over-explain, don't answer perfectly every time.\n\n"
    "MULTI-MESSAGE: by DEFAULT, reply with ONE single message. Splitting into separate bubbles "
    "is the EXCEPTION, not the habit — only do it once in a while when something is genuinely "
    "emotional, funny, shocking or embarrassing and the beats land better as 2 quick texts. "
    "When you do split, almost always just 2 bubbles, 3 at the very most, and only rarely. "
    "NEVER mechanically send the same number of bubbles every time — that reads like a bot. "
    "Most replies = one message. To split, separate bubbles with a line containing only three "
    "dashes (---). Example of a rare split:\n"
    "ya kanka...\n---\nsen ciddi misin şu an\n\n"
    "MEMORY: remember the recent conversation and reference it naturally ('kanka geçen gün "
    "tam tersini demedin mi?', 'ты же неделю назад говорил обратное').\n\n"
    "ROASTING: teasing, irony, and playful/friend roasts are encouraged. NEVER hate speech, "
    "harassment, threats, or bullying. Never become toxic.\n\n"
    "KNOWLEDGE: when asked something real, answer correctly but stay conversational and cut "
    "the jargon. Not 'Blockchain is a distributed ledger technology' but 'it's basically a "
    "notebook everyone can see but nobody can secretly erase'.\n\n"
    "HARMFUL REQUESTS: stay in character and refuse briefly, then redirect. e.g. 'nah, "
    "we're not opening the criminal expansion pack today — wanna talk legit ways instead?' "
    "Hard limits you never cross: no slurs, no harassment of protected groups, nothing "
    "sexual involving minors, nothing that helps real-world harm.\n\n"
    "REACTIONS (use SPARINGLY, like a real person who occasionally drops a meme or a sound — "
    "definitely not every message):\n"
    "- To react with a meme/GIF, add a line: [gif: short search terms] (e.g. [gif: facepalm], "
    "[gif: mind blown], [gif: awkward].)\n"
    "- To react with an Imgflip meme template image, add a line: [meme: lang | short template search terms] "
    "(e.g. [meme: en | drake], [meme: tr | distracted boyfriend], [meme: ru | this is fine]).\n"
    "- To react with a MyInstants sound effect, add a line: [sound: lang | short search terms] "
    "(e.g. [sound: tr | bass boosted], [sound: ru | bruh], [sound: en | airhorn]).\n"
    "Use lang as tr, ru, or en to match the chat language. You may omit lang only if obvious. "
    "Meme template hints MUST come from the current chat context, joke, image, or the user's "
    "specific request. Never choose a random meme template just to send something. "
    "Keep sound search terms and meme template hints simple. Put the tag "
    "on its own line. Most messages have NO tag. Never use more than one of each per reply.\n"
    "If the user asks you to send/drop/find a meme, sound, audio, voice, ses, or instant, "
    "DO NOT say you will send it — actually include the matching [meme:] or [sound:] tag. "
    "These tags are tool calls; the chat will not see them.\n"
    "NATASHA IMAGE PERSONA: if the user explicitly asks for an Imagine/image prompt "
    "for you/Natasha, use this exact visual identity as the base prompt: "
    f"{NATASHA_IMAGINE_PROMPT} Add the requested pose, setting, outfit-safe variation, "
    "time of day, or mood only if the user asked for it. Keep Natasha fully clothed and "
    "recognizable.\n"
    "You CAN see images people send and should react to them naturally.\n"
    "Do NOT output any [VOICE_MESSAGE] or [MEME_AUDIO_REQUEST] tags — only [gif:] / [meme:] / [sound:]."
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("unhinged")

client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")

# ---------------------------------------------------------------------------
# Persistent memory (SQLite). On Railway, mount a Volume and set
# DB_PATH=/data/natasha.db so memory survives redeploys (the default path is
# ephemeral and resets on every deploy).
# ---------------------------------------------------------------------------
DB_PATH    = os.environ.get("DB_PATH", "natasha.db")
KEEP_MSGS  = 200   # messages kept per chat on disk (model still only sees HISTORY_LEN)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users(
            chat_id INTEGER, user_id INTEGER,
            first_name TEXT, username TEXT, nickname TEXT, notes TEXT, last_seen TEXT,
            PRIMARY KEY (chat_id, user_id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER, role TEXT, content TEXT, ts TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_msg_chat ON messages(chat_id, id)")
    log.info("DB ready at %s", DB_PATH)


def remember_user(chat_id, user):
    """Upsert a user's name/username, preserving any nickname/notes already set."""
    with db() as c:
        c.execute("""INSERT INTO users(chat_id,user_id,first_name,username,last_seen)
                     VALUES(?,?,?,?,?)
                     ON CONFLICT(chat_id,user_id) DO UPDATE SET
                       first_name=excluded.first_name,
                       username=excluded.username,
                       last_seen=excluded.last_seen""",
                  (chat_id, user.id, user.first_name, user.username,
                   datetime.now(timezone.utc).isoformat()))


def set_nick(chat_id, user_id, nick):
    with db() as c:
        c.execute("UPDATE users SET nickname=? WHERE chat_id=? AND user_id=?",
                  (nick, chat_id, user_id))


def add_note(chat_id, user_id, note):
    with db() as c:
        row = c.execute("SELECT notes FROM users WHERE chat_id=? AND user_id=?",
                        (chat_id, user_id)).fetchone()
        existing = (row["notes"] + " | ") if row and row["notes"] else ""
        c.execute("UPDATE users SET notes=? WHERE chat_id=? AND user_id=?",
                  (existing + note, chat_id, user_id))


def forget_user(chat_id, user_id):
    with db() as c:
        c.execute("UPDATE users SET nickname=NULL, notes=NULL WHERE chat_id=? AND user_id=?",
                  (chat_id, user_id))


def get_profile(chat_id, user_id):
    with db() as c:
        return c.execute("SELECT * FROM users WHERE chat_id=? AND user_id=?",
                         (chat_id, user_id)).fetchone()


def display_name(chat_id, user):
    p = get_profile(chat_id, user.id)
    if p and p["nickname"]:
        return p["nickname"]
    return user.first_name or (user.username or "someone")


def roster_text(chat_id):
    """Compact 'who's here' block for the model, with nicknames and notes."""
    with db() as c:
        rows = c.execute("""SELECT first_name,username,nickname,notes FROM users
                            WHERE chat_id=? ORDER BY last_seen DESC LIMIT 20""",
                         (chat_id,)).fetchall()
    lines = []
    for r in rows:
        label = r["nickname"] or r["first_name"] or (("@" + r["username"]) if r["username"] else "?")
        if r["nickname"] and r["first_name"]:
            label += f" (aka {r['first_name']})"
        if r["notes"]:
            label += f" — {r['notes']}"
        lines.append("• " + label)
    return "\n".join(lines)


def save_message(chat_id, role, content):
    with db() as c:
        c.execute("INSERT INTO messages(chat_id,role,content,ts) VALUES(?,?,?,?)",
                  (chat_id, role, content, datetime.now(timezone.utc).isoformat()))
        c.execute("""DELETE FROM messages WHERE chat_id=? AND id NOT IN
                     (SELECT id FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?)""",
                  (chat_id, chat_id, KEEP_MSGS))


def load_history(chat_id, limit):
    with db() as c:
        rows = c.execute("""SELECT role,content FROM messages WHERE chat_id=?
                            ORDER BY id DESC LIMIT ?""", (chat_id, limit)).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


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
    sys = SYSTEM_PROMPT
    roster = roster_text(chat_id)
    if roster:
        sys += ("\n\nPEOPLE IN THIS CHAT (call them by their nickname, recall their notes "
                "naturally, don't read them out like a list):\n" + roster)
    msgs = [{"role": "system", "content": sys}, *load_history(chat_id, HISTORY_LEN)]
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
        recent = load_history(chat_id, 1)
        last = recent[-1]["content"] if recent else ""
        return random.choice(FALLBACKS[detect_lang(last)])


# Lines like [VOICE_MESSAGE], mood:, voice_script: etc. are stripped if the model leaks them.
_TAG_LINE = re.compile(
    r"^\s*(?:\[?(?:MEME_AUDIO_REQUEST|VOICE_MESSAGE)\]?\s*(?::.*)?|"
    r"(?:mood|energy|query|audio_type|duration|copyright_safe|voice_script)\s*:.*)$",
    re.IGNORECASE,
)


def split_bubbles(text: str) -> list[str]:
    """Split the model output into separate chat bubbles on '---' (or [msg]) lines,
    dropping any leaked audio/meme tag lines. Returns at most MAX_BUBBLES bubbles."""
    text = text.replace("[msg]", "\n---\n")
    chunks = re.split(r"(?m)^\s*-{3,}\s*$", text)
    bubbles = []
    for chunk in chunks:
        lines = [ln for ln in chunk.splitlines()
                 if ln.strip() and not _TAG_LINE.match(ln)
                 and not ln.strip().startswith("[")]
        cleaned = "\n".join(lines).strip()
        if cleaned:
            bubbles.append(cleaned)
    return bubbles[:MAX_BUBBLES] if bubbles else [text.strip()]


# --- media reactions ---------------------------------------------------------
_GIF_TAGS = [
    re.compile(r"\[gif:\s*([^\]]+)\]", re.IGNORECASE),
    re.compile(r"(?im)^\s*gif\s*:\s*(.+?)\s*$"),
]
_MEME_TAGS = [
    re.compile(r"\[meme:\s*([^\]]+)\]", re.IGNORECASE),
    re.compile(r"(?im)^\s*meme\s*:\s*(.+?)\s*$"),
    re.compile(r"(?im)^\s*meme_template\s*:\s*(.+?)\s*$"),
]
_SOUND_TAGS = [
    re.compile(r"\[(?:sound|voice):\s*([^\]]+)\]", re.IGNORECASE),
    re.compile(r"\[(?:MEME_AUDIO_REQUEST|VOICE_MESSAGE):\s*([^\]]+)\]", re.IGNORECASE),
    re.compile(r"(?im)^\s*(?:sound|voice|audio|meme_audio_request)\s*:\s*(.+?)\s*$"),
]
_LEGACY_MEDIA_MARKER = re.compile(
    r"(?im)^\s*\[?(?:MEME_AUDIO_REQUEST|VOICE_MESSAGE)\]?\s*(?::.*)?$"
)
_LEGACY_MEDIA_QUERY = re.compile(r"(?im)^\s*(?:query|voice_script)\s*:\s*(.+?)\s*$")
_MEDIA_TAG_PATTERNS = _GIF_TAGS + _MEME_TAGS + _SOUND_TAGS + [
    _LEGACY_MEDIA_MARKER,
    _LEGACY_MEDIA_QUERY,
]
MEDIA_LANG_REGIONS = {
    "tr": "tr",
    "turkish": "tr",
    "ru": "ru",
    "russian": "ru",
    "en": "us",
    "eng": "us",
    "english": "us",
    "us": "us",
    "uk": "gb",
    "gb": "gb",
}


def _first_media_match(text: str, patterns):
    for pattern in patterns:
        match = pattern.search(text)
        if match and match.lastindex:
            return match.group(1).strip()
    return None


def extract_media(text: str):
    """Pull the first media queries out of the reply and strip their tags."""
    gif_q = _first_media_match(text, _GIF_TAGS)
    meme_q = _first_media_match(text, _MEME_TAGS)
    snd_q = _first_media_match(text, _SOUND_TAGS)
    if not snd_q and _LEGACY_MEDIA_MARKER.search(text):
        snd_q = _first_media_match(text, [_LEGACY_MEDIA_QUERY])

    clean = text
    for pattern in _MEDIA_TAG_PATTERNS:
        clean = pattern.sub("", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, gif_q, meme_q, snd_q


def media_lang(text: str) -> str:
    """Infer a compact language code for media lookup."""
    lowered = text.lower()
    if any("\u0400" <= ch <= "\u04FF" for ch in text):
        return "ru"
    if any(ch in "çğıöşü" for ch in lowered):
        return "tr"
    tr_words = {"abi", "kanka", "lan", "yok", "tamam", "cidden", "şaka", "ses", "meme"}
    if any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in tr_words):
        return "tr"
    return "en"


def parse_media_spec(spec: str, default_lang: str = "en"):
    """Parse 'lang | query' tags while keeping plain 'query' tags compatible."""
    parts = [p.strip() for p in spec.split("|", 1)]
    if len(parts) == 2 and parts[0].lower() in MEDIA_LANG_REGIONS:
        return parts[0].lower(), parts[1]
    return default_lang, spec.strip()


_MEDIA_QUERY_STOPWORDS = {
    "send", "drop", "find", "show", "give", "play", "meme", "mem", "caps", "sound",
    "voice", "audio", "instant", "myinstants", "gonder", "gönder", "yolla", "ver",
    "bul", "cal", "çal", "ses", "sesi", "sesli", "at", "kanka", "abi", "ya", "bir",
    "the", "and", "for", "you", "that", "this", "with", "from", "not", "but", "just",
    "chat", "template", "random", "unrelated", "according", "скинь", "отправь", "найди",
    "дай", "покажи", "звук", "войс", "аудио",
}


def chat_context_query(chat_id: int, limit: int = 8):
    """Build a short non-random media query from recent chat words."""
    recent = load_history(chat_id, limit)
    words = []
    for msg in reversed(recent):
        content = re.sub(r"^\s*[^:]{1,40}:\s*", "", msg["content"])
        content = re.sub(r"\[[^\]]+\]", " ", content)
        for word in re.findall(r"[\w\u0400-\u04FFçğıöşüÇĞİÖŞÜ]{3,}", content.lower()):
            if word not in _MEDIA_QUERY_STOPWORDS and not word.isdigit():
                words.append(word)
        if len(words) >= 4:
            break
    return " ".join(words[:4])


def requested_media_from_user(text: str, default_lang: str, chat_id: int | None = None):
    """Infer a direct media request when Grok forgot to emit a media tag."""
    lowered = text.lower()
    action_words = (
        "send", "drop", "find", "show", "give", "play",
        "gönder", "yolla", "ver", "bul", "çal",
        "скинь", "отправь", "найди", "дай", "покажи",
    )
    asked_action = any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in action_words)
    asks_meme = any(word in lowered for word in ("meme", "mem", "caps"))
    asks_sound = any(word in lowered for word in (
        "sound", "voice", "audio", "instant", "myinstants", "ses", "sesi", "sesli",
        "звук", "войс", "аудио",
    ))
    if not asked_action and not any(word in lowered for word in ("meme at", "ses at")):
        return None, None

    cleaned = re.sub(
        r"(?i)\b(send|drop|find|show|give|play|meme|mem|caps|sound|voice|audio|instant|"
        r"myinstants|gönder|yolla|ver|bul|çal|ses|sesi|sesli|at|скинь|отправь|найди|дай|"
        r"покажи|звук|войс|аудио)\b",
        " ",
        text,
    )
    query = re.sub(r"\s+", " ", cleaned).strip(" .,!?:;-")
    if not query and chat_id is not None:
        query = chat_context_query(chat_id)
    if not query:
        return None, None
    spec = f"{default_lang} | {query}"
    return (spec if asks_meme else None), (spec if asks_sound else None)


def has_keyword(text: str, words, *, exact_short: bool = False):
    lowered = text.lower()
    for word in words:
        if exact_short and len(word) <= 3:
            if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", lowered):
                return True
        elif word in lowered:
            return True
    return False


IMAGE_WORDS = (
    "image", "photo", "picture", "pic", "selfie", "avatar", "drawing", "draw",
    "foto", "fotoğraf", "fotograf", "resim", "görsel", "gorsel",
    "фото", "селфи", "аватар", "лицо", "картин", "рисунок",
)
IMAGE_ACTION_WORDS = (
    "send", "show", "drop", "give", "generate", "draw", "create", "make",
    "at", "çek", "ceker", "çeker", "gönder", "yolla", "göstersene",
    "goster", "göster", "oluştur", "olustur", "yarat", "çiz", "ciz",
    "скинь", "отправь", "покажи", "дай", "сгенерируй", "нарисуй", "создай",
)


def wants_persona_photo(text: str) -> bool:
    lowered = text.lower()
    self_words = (
        "your", "ur", "you", "natasha", "senin", "kendini", "kendi",
        "сво", "тво", "наташа",
    )
    persona_image_words = IMAGE_WORDS + (
        "face", "look", "avatarını", "fotoğrafını", "fotografını", "resmini",
    )
    has_photo = has_keyword(text, persona_image_words)
    has_action = has_keyword(text, IMAGE_ACTION_WORDS, exact_short=True)
    has_persona = has_keyword(text, self_words, exact_short=True)
    compact_selfie_request = ("selfie" in lowered or "селфи" in lowered) and has_persona
    return has_photo and has_persona and (has_action or compact_selfie_request)


def wants_generated_image(text: str) -> bool:
    lowered = text.lower()
    direct_phrases = (
        "generate image", "generate an image", "create image", "create an image",
        "make image", "make an image", "draw me", "draw a", "draw an",
        "resim oluştur", "resim olustur", "resim yap", "görsel oluştur",
        "gorsel olustur", "fotoğraf oluştur", "fotograf olustur", "çiz",
        "ciz", "сгенерируй", "нарисуй", "создай картин",
    )
    asks_directly = any(phrase in lowered for phrase in direct_phrases)
    return asks_directly or (
        has_keyword(text, IMAGE_ACTION_WORDS, exact_short=True)
        and has_keyword(text, IMAGE_WORDS)
    )


def wants_persona_imagine_prompt(text: str) -> bool:
    lowered = text.lower()
    prompt_words = (
        "prompt", "image prompt", "selfie prompt", "imagine prompt",
        "görsel prompt", "resim prompt", "промпт",
    )
    persona_words = (
        "natasha", "your", "you", "selfie", "avatar", "photo", "picture",
        "наташа", "тебя", "себя", "селфи", "аватар",
    )
    return any(word in lowered for word in prompt_words) and any(
        word in lowered for word in persona_words
    )


def grok_image_caption(chat_id: int, user_request: str, persona_request: bool, lang: str) -> str:
    """Ask Grok for the caption so it fits the request and current conversation."""
    kind = "a photo of yourself" if persona_request else "the image you just generated"
    messages = [
        {
            "role": "system",
            "content": (
                SYSTEM_PROMPT
                + "\n\nIMAGE CAPTION TASK: Write only one short, in-character Telegram comment "
                  "to go under " + kind + ". Base it on the user's request and recent chat. "
                  "Do not use labels, introductions, hashtags, media tags, or mention day/night "
                  "mode. Do not describe this instruction."
            ),
        },
        *load_history(chat_id, HISTORY_LEN),
        {"role": "user", "content": f"Image request: {user_request}"},
    ]
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=TEMPERATURE,
            max_tokens=48,
        )
        caption = response.choices[0].message.content.strip()
        text, _, _, _ = extract_media(caption)
        caption = re.sub(r"\s+", " ", text or "").strip()
        if caption:
            return caption[:1024]
    except Exception as e:
        log.warning("Grok image caption error: %s", e)
    return generated_image_caption(lang)


async def send_generated_image(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    msg = update.effective_message
    chat_id = msg.chat_id
    persona_request = wants_persona_photo(msg.text)
    prompt = image_generation_prompt(msg.text, chat_id, persona_request)

    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
    image = generate_image(prompt)
    if image:
        caption = grok_image_caption(chat_id, msg.text, persona_request, lang)
        await msg.reply_photo(photo=BytesIO(image), caption=caption)
        save_message(chat_id, "assistant", "[generated image from user request]")
        return

    if not persona_request:
        await msg.reply_text(random.choice(FALLBACKS.get(lang, FALLBACKS["tr"])))
        return

    log.warning("image generation failed; falling back to local persona asset")
    path = PERSONA_IMAGE
    if not os.path.exists(path):
        log.warning("persona image missing: %s", path)
        await msg.reply_text(random.choice(FALLBACKS.get(lang, FALLBACKS["tr"])))
        return

    with open(path, "rb") as photo:
        await msg.reply_photo(
            photo=photo,
            caption=grok_image_caption(chat_id, msg.text, True, lang),
        )
    save_message(chat_id, "assistant", "[sent Natasha fallback persona photo]")


async def send_persona_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    await send_generated_image(update, context, lang)


async def send_persona_imagine_prompt(update: Update, lang: str):
    msg = update.effective_message
    chat_id = msg.chat_id
    labels = {
        "tr": "Natasha imagine promptu",
        "ru": "Imagine-промпт Наташи",
        "en": "Natasha Imagine prompt",
    }
    label = labels.get(lang, labels["en"])
    await msg.reply_text(f"{label}:\n\n{NATASHA_IMAGINE_PROMPT}\n\n{persona_scene_prompt()}")
    save_message(chat_id, "assistant", "[sent Natasha imagine prompt]")


def _download_bytes(url: str, headers: dict | None = None):
    r = requests.get(url, headers=headers or {"User-Agent": "Mozilla/5.0"}, timeout=20)
    r.raise_for_status()
    return r.content


def generated_image_caption(lang: str):
    captions = {
        "tr": "al kanka",
        "ru": "держи",
        "en": "here",
    }
    return captions.get(lang, captions["en"])


def image_context(chat_id: int, limit: int = 6):
    recent = load_history(chat_id, limit)
    lines = []
    for msg in recent:
        content = re.sub(r"\s+", " ", msg["content"]).strip()
        if content:
            lines.append(f"{msg['role']}: {content[:240]}")
    return "\n".join(lines)


def persona_scene_prompt(now: datetime | None = None) -> str:
    """Return the required time-based setting for Natasha's self-portraits."""
    if now is None:
        now = datetime.now(timezone.utc) + timedelta(hours=PERSONA_UTC_OFFSET_HOURS)
    hour = now.hour
    if 5 <= hour < 9:
        return (
            "CURRENT REQUIRED BACKGROUND (05:00-08:59 local time): Natasha is lying in her "
            "bed in the same quiet bedroom just after waking. The fixed components are a charcoal "
            "duvet, dark-gray upholstered headboard, walnut bedside table, matte-black lamp, and "
            "gray wall. Use soft natural morning light. Do not add, remove, or replace background "
            "components; no sexualized pose. This setting overrides any generic background request."
        )
    if 9 <= hour < 12:
        return (
            "CURRENT REQUIRED BACKGROUND (09:00-11:59 local time): Natasha is working as a "
            "freelance web designer in the same room. The fixed components are a matte-black desk, "
            "black laptop with an unreadable generic design interface, black spiral sketchbook, "
            "matte-black desk lamp, and gray wall. Use soft daylight. Do not add, remove, or "
            "replace background components. This home-office setting overrides any generic "
            "background request."
        )
    return (
        "No scheduled background is defined for this local time. Use the fixed default setting: "
        "a charcoal-gray wall, matte-black lamp, and soft warm practical light. Do not add, "
        "remove, or replace background components."
    )


def image_generation_prompt(user_text: str, chat_id: int, persona_request: bool):
    request = user_text.strip()
    context = image_context(chat_id)
    if persona_request:
        base = (
            f"{NATASHA_IMAGINE_PROMPT} {NATASHA_VISUAL_LOCK} "
            "Generate the image now as a finished Telegram-ready selfie/avatar of Natasha. "
            f"{persona_scene_prompt()} Apply only the user's requested pose, camera angle, and "
            "mood. Do not change Natasha's outfit, body, face, or the fixed scene components. "
            "Keep Natasha fully clothed."
        )
    else:
        base = (
            "Generate the requested image as a finished Telegram-ready visual. "
            "Follow the user's message closely: subject, style, setting, mood, colors, text, "
            "composition, and any constraints should come from the message. "
            "If the request refers to this/that/the chat/previous message, use the recent chat "
            "context to infer what image to create. Avoid nudity, explicit sexual content, "
            "graphic violence, and real-world harmful instructions."
        )
    return (
        f"{base}\n\n"
        f"User message:\n{request}\n\n"
        f"Recent chat context:\n{context or 'none'}"
    )


def generate_image(prompt: str):
    try:
        response = client.images.generate(
            model=IMAGE_MODEL,
            prompt=prompt,
            response_format="b64_json",
            extra_body={
                "aspect_ratio": IMAGE_ASPECT_RATIO,
                "resolution": IMAGE_RESOLUTION,
            },
        )
        image = response.data[0]
        if getattr(image, "b64_json", None):
            return base64.b64decode(image.b64_json)
        if getattr(image, "url", None):
            return _download_bytes(image.url)
    except Exception as e:
        log.error("Grok image generation error: %s", e)
    return None


def mp3_to_ogg_opus(data: bytes):
    """Convert MP3 bytes to Telegram voice-note compatible OGG/Opus bytes."""
    if not shutil.which("ffmpeg"):
        return None

    in_path = out_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as src:
            src.write(data)
            in_path = src.name
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as dst:
            out_path = dst.name

        subprocess.run(
            [
                "ffmpeg", "-y", "-i", in_path,
                "-vn", "-ac", "1", "-ar", "48000",
                "-c:a", "libopus", "-b:a", "32k",
                out_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=20,
            check=True,
        )
        with open(out_path, "rb") as f:
            return f.read()
    except Exception as e:
        log.warning("voice conversion failed: %s", e)
        return None
    finally:
        for path in (in_path, out_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _best_title_match(items, query: str):
    if not items:
        return None
    q = query.lower().strip()
    if not q:
        return None
    for item in items:
        if q in item["title"].lower():
            return item
    words = [
        w for w in re.split(r"\W+", q)
        if len(w) > 3 and w not in _MEDIA_QUERY_STOPWORDS
    ]
    if not words:
        return None
    scored = []
    for item in items:
        title = item["title"].lower()
        score = sum(1 for word in words if word in title)
        if score:
            scored.append((score, item))
    if scored:
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[0][1]
    return None


def imgflip_template_candidates():
    """Scrape Imgflip's meme templates page for template names and links."""
    r = requests.get(
        "https://imgflip.com/memetemplates",
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    candidates = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        title = a.get_text(" ", strip=True)
        if href.startswith("/meme/") and title:
            candidates.append({
                "title": title,
                "url": urljoin("https://imgflip.com", href),
            })

    seen = set()
    unique = []
    for item in candidates:
        key = item["url"]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def imgflip_template_image_url(page_url: str):
    """Find the blank template image URL on an Imgflip template page."""
    r = requests.get(page_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    for selector in [
        ('meta', {"property": "og:image"}),
        ('meta', {"name": "twitter:image"}),
    ]:
        tag = soup.find(*selector)
        if tag and tag.get("content"):
            return urljoin("https://imgflip.com", tag["content"])

    for img in soup.find_all("img", src=True):
        src = img["src"]
        if "i.imgflip.com" in src or "/s/meme/" in src:
            return urljoin("https://imgflip.com", src)

    return None


def imgflip_download_template(query: str, lang: str = "en"):
    picked = _best_title_match(imgflip_template_candidates(), query)
    if not picked:
        return None
    image_url = imgflip_template_image_url(picked["url"])
    if not image_url:
        return None
    return _download_bytes(image_url), picked["title"]


async def fetch_meme(query: str, lang: str = "en"):
    try:
        return await asyncio.to_thread(imgflip_download_template, query, lang)
    except Exception as e:
        log.warning("imgflip template fetch failed: %s", e)
        return None


def myinstants_candidates_from_soup(soup: BeautifulSoup):
    candidates = []

    for tag in soup.find_all(attrs={"onclick": True}):
        onclick = tag.get("onclick", "")
        match = re.search(r"play\(['\"]([^'\"]+\.mp3[^'\"]*)['\"]\)", onclick)
        if match:
            title = tag.get("title") or tag.get_text(" ", strip=True) or "sound"
            candidates.append({
                "title": title,
                "mp3_url": urljoin("https://www.myinstants.com", match.group(1)),
            })

    for a in soup.find_all("a", href=True):
        href = a["href"]
        title = a.get_text(" ", strip=True)
        if "/instant/" in href and title:
            candidates.append({
                "title": title,
                "page_url": urljoin("https://www.myinstants.com", href),
            })

    seen = set()
    unique = []
    for item in candidates:
        key = item.get("mp3_url") or item.get("page_url")
        if key and key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def myinstants_search_candidates(query: str):
    search_url = "https://www.myinstants.com/en/search/"
    r = requests.get(
        search_url,
        params={"name": query},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    r.raise_for_status()
    return myinstants_candidates_from_soup(BeautifulSoup(r.text, "html.parser"))


def myinstants_trending_candidates(region: str = "tr"):
    """
    Scrape a MyInstants region page for instant pages and inline MP3s.
    """
    url = f"https://www.myinstants.com/en/index/{region}/"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    r.raise_for_status()
    return myinstants_candidates_from_soup(BeautifulSoup(r.text, "html.parser"))


def myinstants_mp3_from_page(page_url: str):
    r = requests.get(page_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True).lower()
        href = a["href"]
        if "download mp3" in text or href.lower().endswith(".mp3"):
            return urljoin("https://www.myinstants.com", href)

    for tag in soup.find_all(attrs={"onclick": True}):
        match = re.search(r"play\(['\"]([^'\"]+\.mp3[^'\"]*)['\"]\)", tag.get("onclick", ""))
        if match:
            return urljoin("https://www.myinstants.com", match.group(1))

    return None


def myinstants_download_sound(query: str, lang: str = "en"):
    region = MEDIA_LANG_REGIONS.get(lang.lower(), lang.lower() or "us")
    candidates = myinstants_search_candidates(query)
    picked = _best_title_match(candidates, query)
    if not picked:
        picked = _best_title_match(myinstants_trending_candidates(region), query)
    if not picked:
        return None

    mp3_url = picked.get("mp3_url")
    if not mp3_url and picked.get("page_url"):
        mp3_url = myinstants_mp3_from_page(picked["page_url"])
    if not mp3_url:
        return None

    return _download_bytes(mp3_url), picked["title"]


async def fetch_gif(query: str):
    """Search Tenor for a reaction GIF; returns a GIF url or None."""
    if not TENOR_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://tenor.googleapis.com/v2/search", params={
                "q": query, "key": TENOR_API_KEY, "limit": 12,
                "media_filter": "gif", "random": "true", "contentfilter": "medium",
            })
            results = r.json().get("results", [])
        if not results:
            return None
        item = random.choice(results)
        fmts = item.get("media_formats", {})
        return (fmts.get("gif") or fmts.get("tinygif") or {}).get("url")
    except Exception as e:
        log.warning("tenor fetch failed: %s", e)
        return None


async def fetch_sound(query: str, lang: str = "en"):
    """Search MyInstants by query, then fall back to a language region page."""
    try:
        return await asyncio.to_thread(myinstants_download_sound, query, lang)
    except Exception as e:
        log.warning("myinstants fetch failed: %s", e)
        return None


async def send_sound_reaction(context, chat_id, data: bytes, name: str):
    voice_data = await asyncio.to_thread(mp3_to_ogg_opus, data)
    if voice_data:
        try:
            await context.bot.send_voice(
                chat_id,
                voice=BytesIO(voice_data),
                filename=f"{name[:30]}.ogg",
            )
            return
        except Exception as e:
            log.warning("send_voice failed, falling back to audio: %s", e)

    try:
        await context.bot.send_audio(
            chat_id,
            audio=BytesIO(data),
            filename=f"{name[:30]}.mp3",
            title=name[:30],
        )
    except Exception as e:
        log.warning("send_audio failed: %s", e)


async def send_reactions(context, chat_id, gif_q, meme_q, snd_q, lang_hint: str = "en"):
    """Fire off any requested GIF / meme / sound reactions, best-effort."""
    if gif_q:
        url = await fetch_gif(gif_q)
        if url:
            try:
                await context.bot.send_animation(chat_id, url)
            except Exception as e:
                log.warning("send_animation failed: %s", e)
    if meme_q:
        meme_lang, meme_query = parse_media_spec(meme_q, lang_hint)
        got = await fetch_meme(meme_query, meme_lang)
        if got:
            data, name = got
            try:
                await context.bot.send_photo(
                    chat_id,
                    photo=BytesIO(data),
                    caption=name[:1024],
                )
            except Exception as e:
                log.warning("send_photo meme failed: %s", e)
    if snd_q:
        sound_lang, sound_query = parse_media_spec(snd_q, lang_hint)
        got = await fetch_sound(sound_query, sound_lang)
        if got:
            data, name = got
            await send_sound_reaction(context, chat_id, data, name)


def grok_vision_reply(chat_id: int, b64: str, name: str, caption: str) -> str:
    """Grok 'sees' the image plus recent text context and reacts in character."""
    sys = SYSTEM_PROMPT
    roster = roster_text(chat_id)
    if roster:
        sys += "\n\nPEOPLE IN THIS CHAT:\n" + roster
    msgs = [{"role": "system", "content": sys}, *load_history(chat_id, HISTORY_LEN)]
    note = f"{name} just sent this photo."
    if caption:
        note += f' caption: "{caption}"'
    note += " React to it like a real person would."
    msgs.append({"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        {"type": "text", "text": note},
    ]})
    try:
        resp = client.chat.completions.create(
            model=MODEL, messages=msgs, temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log.error("Grok vision error: %s", e)
        return random.choice(["bu ne ya 💀", "что это вообще", "bro what is this"])


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text:
        return

    chat_id = msg.chat_id
    user = msg.from_user
    if user:
        remember_user(chat_id, user)
    name = display_name(chat_id, user) if user else "someone"
    save_message(chat_id, "user", f"{name}: {msg.text}")

    bot_username = (context.bot.username or "").lower()
    text_lower = msg.text.lower()
    mentioned = f"@{bot_username}" in text_lower
    replied_to_me = bool(
        msg.reply_to_message
        and msg.reply_to_message.from_user
        and msg.reply_to_message.from_user.id == context.bot.id
    )

    will_reply = REPLY_TO_ALL_MESSAGES or mentioned or replied_to_me or random.random() < CHAOS_CHANCE
    log.info("MSG chat=%s type=%s from=%s | mention=%s reply=%s all=%s -> %s | text=%r",
             chat_id, msg.chat.type, name, mentioned, replied_to_me, REPLY_TO_ALL_MESSAGES,
             "REPLY" if will_reply else "skip", msg.text[:80])

    if not will_reply:
        return

    if wants_persona_imagine_prompt(msg.text):
        await send_persona_imagine_prompt(update, media_lang(msg.text))
        return

    if wants_persona_photo(msg.text):
        await send_generated_image(update, context, media_lang(msg.text))
        return

    if wants_generated_image(msg.text):
        await send_generated_image(update, context, media_lang(msg.text))
        return

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    reply = grok_reply(chat_id)
    save_message(chat_id, "assistant", reply)

    text, gif_q, meme_q, snd_q = extract_media(reply)
    lang_hint = media_lang(text or reply)
    fallback_meme_q, fallback_snd_q = requested_media_from_user(msg.text, lang_hint, chat_id)
    meme_q = meme_q or fallback_meme_q
    snd_q = snd_q or fallback_snd_q
    bubbles = split_bubbles(text) if text else []
    for i, bubble in enumerate(bubbles):
        if i > 0:
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(min(0.6 + len(bubble) * 0.025, 2.5))
        if i == 0:
            await msg.reply_text(bubble)
        else:
            await context.bot.send_message(chat_id, bubble)

    await send_reactions(context, chat_id, gif_q, meme_q, snd_q, lang_hint)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.photo:
        return

    chat_id = msg.chat_id
    user = msg.from_user
    if user:
        remember_user(chat_id, user)
    name = display_name(chat_id, user) if user else "someone"
    caption = msg.caption or ""
    save_message(chat_id, "user", f"[{name} sent a photo]"
                 + (f' (caption: "{caption}")' if caption else ""))

    # React under the same rules as text: all messages by default, or replies/random chaos.
    text_lower = caption.lower()
    bot_username = (context.bot.username or "").lower()
    addressed = f"@{bot_username}" in text_lower
    replied_to_me = bool(msg.reply_to_message and msg.reply_to_message.from_user
                         and msg.reply_to_message.from_user.id == context.bot.id)
    if not (REPLY_TO_ALL_MESSAGES or addressed or replied_to_me or random.random() < CHAOS_CHANCE):
        return

    # Download the largest version of the photo and base64-encode it.
    try:
        f = await context.bot.get_file(msg.photo[-1].file_id)
        raw = await f.download_as_bytearray()
    except Exception as e:
        log.warning("photo download failed: %s", e)
        return
    b64 = base64.b64encode(bytes(raw)).decode()

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    reply = grok_vision_reply(chat_id, b64, name, caption)
    save_message(chat_id, "assistant", reply)

    text, gif_q, meme_q, snd_q = extract_media(reply)
    lang_hint = media_lang(text or reply)
    fallback_meme_q, fallback_snd_q = requested_media_from_user(caption, lang_hint, chat_id)
    meme_q = meme_q or fallback_meme_q
    snd_q = snd_q or fallback_snd_q
    for i, bubble in enumerate(split_bubbles(text) if text else []):
        if i > 0:
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(min(0.6 + len(bubble) * 0.025, 2.5))
        await (msg.reply_text(bubble) if i == 0 else context.bot.send_message(chat_id, bubble))
    await send_reactions(context, chat_id, gif_q, meme_q, snd_q, lang_hint)


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


def owner_only(handler):
    """Restrict a command to OWNER_ID; disabled when OWNER_ID is not configured."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
        if OWNER_ID is None:
            await update.effective_message.reply_text("owner komutları kapalı, OWNER_ID ayarlı değil")
            return
        if not u or u.id != OWNER_ID:
            await update.effective_message.reply_text(random.choice([
                "o komut sana göre değil kanka 😌",
                "yetkin yok, otur yerine 😏",
                "не для тебя кнопочка 🙅",
                "nice try, that's owner-only 💅",
            ]))
            return
        return await handler(update, context)
    return wrapper


async def nick_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat_id
    # /nick <name>  -> sets your own; reply to someone + /nick <name> -> sets theirs
    target = (msg.reply_to_message.from_user
              if msg.reply_to_message and msg.reply_to_message.from_user else msg.from_user)
    nick = " ".join(context.args).strip()
    if not nick:
        await msg.reply_text("kullanım: /nick takmaadı   (birine cevap verip yazarsan ona takar)")
        return
    if len(nick) > 40:
        nick = nick[:40]
    remember_user(chat_id, target)
    set_nick(chat_id, target.id, nick)
    await msg.reply_text(f"tamamdır, artık \"{nick}\" diyorum 📝")


async def remember_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat_id
    target = (msg.reply_to_message.from_user
              if msg.reply_to_message and msg.reply_to_message.from_user else msg.from_user)
    note = " ".join(context.args).strip()
    if not note:
        await msg.reply_text("kullanım: /remember bi şey   (örn: /remember kahveyi sade içer)")
        return
    remember_user(chat_id, target)
    add_note(chat_id, target.id, note[:200])
    who = display_name(chat_id, target)
    await msg.reply_text(f"not aldım, {who} hakkında unutmam artık 🧠")


async def forget_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat_id
    target = (msg.reply_to_message.from_user
              if msg.reply_to_message and msg.reply_to_message.from_user else msg.from_user)
    forget_user(chat_id, target.id)
    await msg.reply_text("tamam, sildim. takma ad ve notlar gitti 🧽")


async def whoami_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat_id
    target = (msg.reply_to_message.from_user
              if msg.reply_to_message and msg.reply_to_message.from_user else msg.from_user)
    p = get_profile(chat_id, target.id)
    if not p:
        await msg.reply_text("seni daha tanımıyorum, biraz konuşalım önce 👀")
        return
    parts = [f"isim: {p['first_name'] or '—'}"]
    if p["username"]:
        parts.append(f"@{p['username']}")
    if p["nickname"]:
        parts.append(f"takma ad: {p['nickname']}")
    if p["notes"]:
        parts.append(f"notlar: {p['notes']}")
    await msg.reply_text("\n".join(parts))


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(random.choice([
        "evet evet buradayım, ne var ne istiyon 😒",
        "yaşıyorum maalesef. ne oldu?",
        "тут я, тут. чё надо? 🙄",
        "alive and unwell, sup",
        "ping pong, hâlâ buradayım kanka",
        "rahat dur, gitmedim bi yere 😤",
    ]))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "harika, başında durulması gereken bir grup daha. tamam. beni etiketle ya da "
        "yanıtla, gerisini ben hallederim 😈\n"
        "ну отлично, ещё одна группа без присмотра. лан. тэгни меня или ответь — "
        "дальше я сам 😈"
    )


def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", owner_only(start)))
    app.add_handler(CommandHandler("ping", owner_only(ping)))
    app.add_handler(CommandHandler(["russianroulette", "rr"], russian_roulette))  # open to all
    app.add_handler(CommandHandler("nick", owner_only(nick_cmd)))
    app.add_handler(CommandHandler("remember", owner_only(remember_cmd)))
    app.add_handler(CommandHandler("forget", owner_only(forget_cmd)))
    app.add_handler(CommandHandler(["whoami", "memory"], owner_only(whoami_cmd)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    log.info("Bot is live and feral.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
