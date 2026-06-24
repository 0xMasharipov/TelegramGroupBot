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
import re
import base64
import random
import asyncio
import sqlite3
import logging
from io import BytesIO
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import httpx
from openai import OpenAI
from telegram import Update, ChatPermissions
from telegram.constants import ChatAction
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
XAI_API_KEY    = os.environ["XAI_API_KEY"]
OWNER_ID       = int(os.environ.get("OWNER_ID", "1346274959"))  # only this user may run commands

# Optional media modules — each works only if its key is set (otherwise skipped).
TENOR_API_KEY     = os.environ.get("TENOR_API_KEY", "")       # GIF/meme reactions (tenor.googleapis.com)
FREESOUND_API_KEY = os.environ.get("FREESOUND_API_KEY", "")   # CC-licensed sound effects (freesound.org)

MODEL         = "grok-4.20"    # xAI model; reasoning + non-reasoning modes
CHAOS_CHANCE  = 0.06           # ~6% chance to butt into a random message
HISTORY_LEN   = 12             # messages of context kept per chat
MAX_TOKENS    = 160            # hard length cap — keeps replies texty, not essays
TEMPERATURE   = 1.0
WAKE_WORDS    = ["gooner", "natasha", "наташа"]  # wakes when any appears in a message
MAX_BUBBLES   = 3              # hard ceiling on bubbles; default behaviour is 1

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
    "PERSONALITY MIX: ~40% loyal friend, 25% street-smart, 15% comedian, 10% philosopher, "
    "10% chaos goblin. You're witty, emotionally reactive, sarcastic when it fits, "
    "supportive when it's needed, sometimes chaotic — never corporate.\n\n"
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
    "- To react with a sound effect, add a line: [sound: short search terms] (e.g. "
    "[sound: airhorn], [sound: sad violin], [sound: rimshot].)\n"
    "Keep the search terms in English even if you're talking in another language. Put the tag "
    "on its own line. Most messages have NO tag. Never use more than one of each per reply.\n"
    "You CAN see images people send and should react to them naturally.\n"
    "Do NOT output any [VOICE_MESSAGE] or [MEME_AUDIO_REQUEST] tags — only [gif:] / [sound:]."
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


# Lines like [VOICE_MESSAGE], mood:, voice_script: etc. — stripped if the model leaks them.
_TAG_LINE = re.compile(
    r"^\s*(\[?(MEME_AUDIO_REQUEST|VOICE_MESSAGE)\]?|mood|energy|query|audio_type|"
    r"duration|copyright_safe|voice_script)\s*:.*$",
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
_GIF_TAG   = re.compile(r"\[gif:\s*([^\]]+)\]", re.IGNORECASE)
_SOUND_TAG = re.compile(r"\[sound:\s*([^\]]+)\]", re.IGNORECASE)


def extract_media(text: str):
    """Pull the first [gif:] and [sound:] queries out of the reply and return
    (clean_text, gif_query_or_None, sound_query_or_None)."""
    gif = _GIF_TAG.search(text)
    snd = _SOUND_TAG.search(text)
    gif_q = gif.group(1).strip() if gif else None
    snd_q = snd.group(1).strip() if snd else None
    clean = _SOUND_TAG.sub("", _GIF_TAG.sub("", text)).strip()
    return clean, gif_q, snd_q


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


async def fetch_sound(query: str):
    """Search Freesound for a CC-licensed clip; returns (mp3_bytes, name) or None."""
    if not FREESOUND_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get("https://freesound.org/apiv2/search/text/", params={
                "query": query, "fields": "name,previews",
                "page_size": 10, "filter": "duration:[0.3 TO 12]",
                "token": FREESOUND_API_KEY,
            })
            results = r.json().get("results", [])
            results = [x for x in results if x.get("previews")]
            if not results:
                return None
            item = random.choice(results[:10])
            url = item["previews"].get("preview-hq-mp3") or item["previews"].get("preview-lq-mp3")
            audio = await c.get(url)
            return audio.content, item.get("name", "sound")
    except Exception as e:
        log.warning("freesound fetch failed: %s", e)
        return None


async def send_reactions(context, chat_id, gif_q, snd_q):
    """Fire off any requested GIF / sound reactions, best-effort."""
    if gif_q:
        url = await fetch_gif(gif_q)
        if url:
            try:
                await context.bot.send_animation(chat_id, url)
            except Exception as e:
                log.warning("send_animation failed: %s", e)
    if snd_q:
        got = await fetch_sound(snd_q)
        if got:
            data, name = got
            try:
                await context.bot.send_audio(chat_id, audio=BytesIO(data),
                                             filename=f"{name[:30]}.mp3", title=name[:30])
            except Exception as e:
                log.warning("send_audio failed: %s", e)


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
    woke = any(w in text_lower for w in WAKE_WORDS)
    replied_to_me = bool(
        msg.reply_to_message
        and msg.reply_to_message.from_user
        and msg.reply_to_message.from_user.id == context.bot.id
    )

    will_reply = mentioned or woke or replied_to_me or random.random() < CHAOS_CHANCE
    log.info("MSG chat=%s type=%s from=%s | mention=%s woke=%s reply=%s -> %s | text=%r",
             chat_id, msg.chat.type, name, mentioned, woke, replied_to_me,
             "REPLY" if will_reply else "skip", msg.text[:80])

    if not will_reply:
        return

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    reply = grok_reply(chat_id)
    save_message(chat_id, "assistant", reply)

    text, gif_q, snd_q = extract_media(reply)
    bubbles = split_bubbles(text) if text else []
    for i, bubble in enumerate(bubbles):
        if i > 0:
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(min(0.6 + len(bubble) * 0.025, 2.5))
        if i == 0:
            await msg.reply_text(bubble)
        else:
            await context.bot.send_message(chat_id, bubble)

    await send_reactions(context, chat_id, gif_q, snd_q)


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

    # React under the same rules as text: addressed, replied-to, or random chaos.
    text_lower = caption.lower()
    bot_username = (context.bot.username or "").lower()
    addressed = (f"@{bot_username}" in text_lower
                 or any(w in text_lower for w in WAKE_WORDS))
    replied_to_me = bool(msg.reply_to_message and msg.reply_to_message.from_user
                         and msg.reply_to_message.from_user.id == context.bot.id)
    if not (addressed or replied_to_me or random.random() < CHAOS_CHANCE):
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

    text, gif_q, snd_q = extract_media(reply)
    for i, bubble in enumerate(split_bubbles(text) if text else []):
        if i > 0:
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(min(0.6 + len(bubble) * 0.025, 2.5))
        await (msg.reply_text(bubble) if i == 0 else context.bot.send_message(chat_id, bubble))
    await send_reactions(context, chat_id, gif_q, snd_q)


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
    """Restrict a command to OWNER_ID; everyone else gets a sassy brush-off."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
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
