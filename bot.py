"""
SVU Med Triage Bot
- Telegram group monitoring with local keyword scanning.
- One Gemini request per triggered message (mention, keyword, or reply to bot).
- Durable Turso/libSQL storage for configuration, FAQs, tickets, blacklist, and API usage.
- Leaders make final decisions; AI failures are escalated for human review.

Required environment variables:
  TELEGRAM_BOT_TOKEN
  GEMINI_API_KEY
  TURSO_DATABASE_URL
  TURSO_AUTH_TOKEN
  OWNER_TELEGRAM_ID

Optional:
  GEMINI_MODEL=gemini-2.5-flash
  GEMINI_RPM_LIMIT=10
  GEMINI_RPD_LIMIT=1000
  MAX_MEDIA_MB=15
  CONTEXT_MESSAGES=20
  ALLOWED_GROUP_ID=       # optional discussion group ID; recommended once known
  AUTO_FAQ_REPLY=true
  FAQ_CONFIDENCE_THRESHOLD=0.85
  ALLOW_LOCAL_DB=0        # set to 1 only for local testing; Render disk is ephemeral
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Literal, Optional

from google import genai
from google.genai import types
from libsql_client import create_client_sync
from pydantic import BaseModel, Field
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.constants import ChatType
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ----------------------------- Configuration -----------------------------

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("svu-triage")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
TURSO_URL = os.getenv("TURSO_DATABASE_URL", "").strip()
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "").strip()
OWNER_ID_RAW = os.getenv("OWNER_TELEGRAM_ID", "").strip()
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
RPM_LIMIT = max(1, int(os.getenv("GEMINI_RPM_LIMIT", "10")))
RPD_LIMIT = max(1, int(os.getenv("GEMINI_RPD_LIMIT", "1000")))
MAX_MEDIA_BYTES = max(1, int(os.getenv("MAX_MEDIA_MB", "15"))) * 1024 * 1024
CONTEXT_LIMIT = max(5, min(40, int(os.getenv("CONTEXT_MESSAGES", "20"))))
ALLOWED_GROUP_ID_RAW = os.getenv("ALLOWED_GROUP_ID", "").strip()
AUTO_FAQ_REPLY = os.getenv("AUTO_FAQ_REPLY", "true").lower() in {"1", "true", "yes"}
FAQ_CONFIDENCE_THRESHOLD = float(os.getenv("FAQ_CONFIDENCE_THRESHOLD", "0.85"))
ALLOW_LOCAL_DB = os.getenv("ALLOW_LOCAL_DB", "0").lower() in {"1", "true", "yes"}

if not BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN environment variable.")
if not GEMINI_API_KEY:
    raise RuntimeError("Missing GEMINI_API_KEY environment variable.")
if not OWNER_ID_RAW.isdigit():
    raise RuntimeError("OWNER_TELEGRAM_ID must be your numeric Telegram user ID.")
OWNER_ID = int(OWNER_ID_RAW)
ALLOWED_GROUP_ID = int(ALLOWED_GROUP_ID_RAW) if ALLOWED_GROUP_ID_RAW.lstrip("-").isdigit() else None

if not TURSO_URL and not ALLOW_LOCAL_DB:
    raise RuntimeError(
        "Missing TURSO_DATABASE_URL. Configure Turso before deploying to Render. "
        "For local-only testing, set ALLOW_LOCAL_DB=1."
    )

# libsql-client's legacy libsql:// transport uses WebSockets. Try HTTPS for
# the same Turso endpoint; keep the Render environment variable unchanged.
if TURSO_URL:
    if not TURSO_TOKEN:
        raise RuntimeError("TURSO_AUTH_TOKEN is required for a remote Turso database.")
    if TURSO_URL.startswith("libsql://"):
        DB_URL = "https://" + TURSO_URL[len("libsql://"):]
    elif TURSO_URL.startswith("https://"):
        DB_URL = TURSO_URL
    else:
        raise RuntimeError(
            "Unsupported TURSO_DATABASE_URL scheme. Expected libsql:// or https://."
        )
else:
    DB_URL = "file:triage_local.db"

db = create_client_sync(
    url=DB_URL,
    auth_token=TURSO_TOKEN if TURSO_URL else None,
)
gemini = genai.Client(api_key=GEMINI_API_KEY)
gemini_async = gemini.aio

# Render liveness and readiness state. Liveness stays healthy while startup
# dependencies initialize; readiness becomes healthy only after DB + Telegram.
DB_READY = False
BOT_READY = False

# Context only includes messages observed since this process started.
CHAT_CONTEXT: dict[int, deque[dict]] = defaultdict(lambda: deque(maxlen=CONTEXT_LIMIT))
AI_REQUEST_TIMES: deque[float] = deque()
AI_RATE_LOCK = asyncio.Lock()
BOT_USERNAME: Optional[str] = None

DEFAULT_KEYWORDS = [
    "امتحان", "اتلغى", "ميدترم", "تأجيل", "دكتور", "ضروري",
    "سقوط", "داتا", "data", "deadline", "جدول الامتحانات",
    "تغيير الجدول", "تسجيل المواد",
]

# ----------------------------- Data model --------------------------------

class TriageResult(BaseModel):
    urgency: Literal["URGENT", "MEDIUM", "LOW", "UNIMPORTANT"]
    action: Literal["LEADER_REVIEW", "FAQ_REPLY", "NO_ACTION"]
    summary_ar: str = Field(description="Concise Egyptian/Modern Standard Arabic summary for leaders.")
    reason_ar: str = Field(description="Short Arabic reason for the decision.")
    reply_ar: str = Field(description="A concise Arabic response to the student; empty if no reply.")
    faq_id: Optional[int] = Field(default=None, description="ID of the exact FAQ used, or null.")
    confidence: float = Field(ge=0, le=1, description="Confidence that the selected action/FAQ is correct.")

# ----------------------------- Database ----------------------------------

def _exec(sql: str, args: tuple = ()):
    return db.execute(sql, args)

def init_db() -> None:
    statements = [
        """CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS smart_keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1,
            created_by INTEGER,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS blacklist (
            user_id INTEGER PRIMARY KEY,
            reason TEXT NOT NULL DEFAULT '',
            added_by INTEGER,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS faqs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            keywords TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_by INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_used_at TEXT,
            last_reviewed_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_chat_id INTEGER NOT NULL,
            source_message_id INTEGER NOT NULL,
            user_id INTEGER,
            username TEXT,
            urgency TEXT NOT NULL,
            summary TEXT NOT NULL,
            reason TEXT NOT NULL,
            original_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_delivery',
            created_at TEXT NOT NULL,
            leader_message_id INTEGER,
            claimed_by INTEGER,
            resolved_by INTEGER,
            resolved_at TEXT,
            UNIQUE(source_chat_id, source_message_id)
        )""",
        """CREATE TABLE IF NOT EXISTS api_usage (
            usage_date TEXT PRIMARY KEY,
            requests INTEGER NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS processed_messages (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            processed_at TEXT NOT NULL,
            PRIMARY KEY(chat_id, message_id)
        )""",
    ]
    for statement in statements:
        _exec(statement)
    now = utc_now()
    for keyword in DEFAULT_KEYWORDS:
        _exec(
            "INSERT OR IGNORE INTO smart_keywords(keyword, active, created_at) VALUES (?, 1, ?)",
            (keyword, now),
        )
    _exec("INSERT OR IGNORE INTO config(key, value) VALUES ('leader_chat_id', '')")
    # Bound deduplication-table growth without deleting tickets or FAQ history.
    _exec("DELETE FROM processed_messages WHERE processed_at < datetime('now', '-30 days')")
    log.info("Database schema initialized.")

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def get_config(key: str) -> str:
    result = _exec("SELECT value FROM config WHERE key = ?", (key,))
    return str(result.rows[0][0]) if result.rows else ""

def set_config(key: str, value: str) -> None:
    _exec(
        "INSERT INTO config(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )

def get_leader_chat_id() -> Optional[int]:
    raw = get_config("leader_chat_id")
    try:
        return int(raw) if raw else None
    except ValueError:
        return None

def get_active_keywords() -> list[str]:
    result = _exec("SELECT keyword FROM smart_keywords WHERE active = 1 ORDER BY keyword")
    return [str(row[0]) for row in result.rows]

def is_blacklisted(user_id: int) -> bool:
    return bool(_exec("SELECT 1 FROM blacklist WHERE user_id = ?", (user_id,)).rows)

def add_ticket(
    message: Message,
    urgency: str,
    summary: str,
    reason: str,
    original_text: str,
) -> int:
    user = message.from_user
    username = f"@{user.username}" if user and user.username else (
        f"{user.full_name}" if user else "Unknown"
    )
    _exec(
        """INSERT OR IGNORE INTO tickets
        (source_chat_id, source_message_id, user_id, username, urgency, summary, reason,
         original_text, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending_delivery', ?)""",
        (
            message.chat_id, message.message_id, user.id if user else None, username,
            urgency, summary, reason, original_text[:12000], utc_now(),
        ),
    )
    row = _exec(
        "SELECT id FROM tickets WHERE source_chat_id = ? AND source_message_id = ?",
        (message.chat_id, message.message_id),
    )
    return int(row.rows[0][0])

def ticket_row(ticket_id: int):
    result = _exec(
        """SELECT id, source_chat_id, source_message_id, user_id, username, urgency,
                  summary, reason, original_text, status, leader_message_id, claimed_by
           FROM tickets WHERE id = ?""",
        (ticket_id,),
    )
    return result.rows[0] if result.rows else None

def mark_ticket_sent(ticket_id: int, leader_message_id: int) -> None:
    _exec(
        "UPDATE tickets SET status='open', leader_message_id=? WHERE id=?",
        (leader_message_id, ticket_id),
    )

def set_ticket_status(ticket_id: int, status: str, actor_id: int) -> None:
    if status == "claimed":
        _exec(
            "UPDATE tickets SET status='claimed', claimed_by=? WHERE id=? AND status IN ('open','claimed')",
            (actor_id, ticket_id),
        )
    elif status == "resolved":
        _exec(
            "UPDATE tickets SET status='resolved', resolved_by=?, resolved_at=? WHERE id=? AND status IN ('open','claimed')",
            (actor_id, utc_now(), ticket_id),
        )

def get_faqs() -> list[dict]:
    rows = _exec(
        "SELECT id, question, answer, keywords FROM faqs WHERE active=1 ORDER BY id"
    ).rows
    return [
        {"id": int(r[0]), "question": str(r[1]), "answer": str(r[2]), "keywords": str(r[3])}
        for r in rows
    ]

def get_faq(faq_id: int):
    result = _exec(
        "SELECT id, question, answer FROM faqs WHERE id=? AND active=1", (faq_id,)
    )
    return result.rows[0] if result.rows else None

def reserve_daily_request() -> bool:
    """Atomically reserve one daily Gemini request across concurrent handlers."""
    day = datetime.now(timezone.utc).date().isoformat()
    _exec("INSERT OR IGNORE INTO api_usage(usage_date, requests) VALUES (?, 0)", (day,))
    result = _exec(
        "UPDATE api_usage SET requests=requests+1 "
        "WHERE usage_date=? AND requests < ?",
        (day, RPD_LIMIT),
    )
    return int(getattr(result, "rows_affected", 0)) == 1


def claim_message_for_processing(chat_id: int, message_id: int) -> bool:
    """Atomically claim a message; only the insert winner may process it."""
    result = _exec(
        "INSERT OR IGNORE INTO processed_messages(chat_id, message_id, processed_at) "
        "VALUES (?, ?, ?)",
        (chat_id, message_id, utc_now()),
    )
    return int(getattr(result, "rows_affected", 0)) == 1

# ----------------------------- Text helpers ------------------------------

def normalize_text(value: str) -> str:
    value = value.lower().replace("ـ", "")
    value = re.sub(r"[\u064B-\u065F\u0670]", "", value)
    value = value.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي"}))
    return re.sub(r"\s+", " ", value).strip()

def message_text(message: Message) -> str:
    return (message.text or message.caption or "").strip()

def bot_is_mentioned(message: Message) -> bool:
    raw = message_text(message)
    if BOT_USERNAME and re.search(rf"(?i)(?<!\w)@{re.escape(BOT_USERNAME)}\b", raw):
        return True
    for entity in (message.entities or []) + (message.caption_entities or []):
        if entity.type == "mention" and BOT_USERNAME:
            fragment = raw[entity.offset: entity.offset + entity.length].lower()
            if fragment == f"@{BOT_USERNAME.lower()}":
                return True
    return False

def urgency_hint_and_clean_text(text: str) -> tuple[Optional[str], str]:
    """Accept urgency hints anywhere, in English or Arabic, independent of mention order."""
    patterns = [
        (r"(?i)(?<!\w)(urgent|عاجل|أولوية\s+عاجلة)(?!\w)", "URGENT"),
        (r"(?i)(?<!\w)(medium|متوسط|متوسطة|أولوية\s+متوسطة)(?!\w)", "MEDIUM"),
        (r"(?i)(?<!\w)(low|منخفض|منخفضة|أولوية\s+منخفضة)(?!\w)", "LOW"),
    ]
    hint = None
    cleaned = text
    for pattern, urgency in patterns:
        if re.search(pattern, cleaned):
            hint = urgency
            cleaned = re.sub(pattern, " ", cleaned)
            break
    if BOT_USERNAME:
        cleaned = re.sub(rf"(?i)(?<!\w)@{re.escape(BOT_USERNAME)}\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \t\n,:;—-")
    return hint, cleaned

def matched_keyword(text: str, keywords: list[str]) -> Optional[str]:
    normalized = normalize_text(text)
    for keyword in keywords:
        candidate = normalize_text(keyword)
        if candidate and candidate in normalized:
            return keyword
    return None

def user_display(message: Message) -> str:
    user = message.from_user
    if not user:
        return "Unknown sender"
    safe_name = html.escape(user.full_name)
    return f"{safe_name} (@{html.escape(user.username)})" if user.username else safe_name

def safe_html(value: str) -> str:
    return html.escape(value or "", quote=False)

def push_context(message: Message) -> None:
    if message.from_user and message.from_user.is_bot:
        return
    text = message_text(message)
    media = media_description(message)
    if not text:
        text = media or "[رسالة بدون نص]"
    CHAT_CONTEXT[message.chat_id].append({
        "message_id": message.message_id,
        "user_id": message.from_user.id if message.from_user else None,
        "name": message.from_user.full_name if message.from_user else "Unknown",
        "text": text[:4000],
        "reply_to": message.reply_to_message.message_id if message.reply_to_message else None,
    })

def media_description(message: Message) -> str:
    if message.photo:
        return "[صورة]"
    if message.video:
        return "[فيديو]"
    if message.voice:
        return "[رسالة صوتية]"
    if message.audio:
        return "[ملف صوتي]"
    if message.document:
        return f"[ملف: {message.document.file_name or message.document.mime_type or 'غير معروف'}]"
    if message.animation:
        return "[GIF/Animation]"
    if message.sticker:
        return "[ملصق]"
    return ""

def make_context(message: Message) -> str:
    entries = list(CHAT_CONTEXT[message.chat_id])[-CONTEXT_LIMIT:]
    blocks = [
        f"- {item['name']} (message_id={item['message_id']}): {item['text']}"
        for item in entries
        if item["message_id"] != message.message_id
    ]
    replied = message.reply_to_message
    if replied:
        reply_text = message_text(replied) or media_description(replied) or "[رسالة بدون نص]"
        if not any(item["message_id"] == replied.message_id for item in entries):
            blocks.insert(0, f"- الرسالة التي تم الرد عليها ({replied.message_id}, {replied.from_user.full_name if replied.from_user else 'Unknown'}): {reply_text[:4000]}")
    return "\n".join(blocks)[-24000:] or "(لا يوجد سياق سابق متاح للبوت)"

# ----------------------------- Authorization -----------------------------

async def is_leader(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return False
    if user.id == OWNER_ID:
        return True
    leader_chat_id = get_leader_chat_id()
    if not leader_chat_id:
        return False
    try:
        member = await context.bot.get_chat_member(leader_chat_id, user.id)
        return member.status in {"creator", "administrator", "member"}
    except TelegramError as exc:
        log.warning("Leader membership check failed: %s", exc)
        return False

async def require_leader(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if await is_leader(update, context):
        return True
    if update.effective_message:
        await update.effective_message.reply_text(
            "الأمر ده متاح لقيادة الجروب فقط."
        )
    return False

# ----------------------------- Media and Gemini --------------------------

async def get_media_part(message: Message):
    media_obj = None
    mime_type = None
    if message.photo:
        media_obj, mime_type = message.photo[-1], "image/jpeg"
    elif message.video:
        media_obj, mime_type = message.video, (message.video.mime_type or "video/mp4")
    elif message.voice:
        media_obj, mime_type = message.voice, (message.voice.mime_type or "audio/ogg")
    elif message.audio:
        media_obj, mime_type = message.audio, (message.audio.mime_type or "audio/mpeg")
    elif message.document:
        doc = message.document
        mime_type = doc.mime_type or ""
        if mime_type.startswith(("image/", "audio/", "video/")):
            media_obj = doc
    if not media_obj:
        return None, None, None
    if getattr(media_obj, "file_size", None) and media_obj.file_size > MAX_MEDIA_BYTES:
        return None, mime_type, "حجم الملف أكبر من الحد الذي يفحصه البوت."
    try:
        tg_file = await message.get_bot().get_file(media_obj.file_id)
        payload = bytes(await tg_file.download_as_bytearray())
        if len(payload) > MAX_MEDIA_BYTES:
            return None, mime_type, "حجم الملف أكبر من الحد الذي يفحصه البوت."
        return types.Part.from_bytes(data=payload, mime_type=mime_type), mime_type, None
    except Exception as exc:
        log.warning("Could not download media: %s", exc)
        return None, mime_type, "تعذر تنزيل الملف لفحصه."

async def wait_for_rpm_slot() -> None:
    # Never sleep while holding the lock: otherwise one queued request blocks
    # every other handler from re-checking the rolling window.
    while True:
        async with AI_RATE_LOCK:
            now = time.monotonic()
            while AI_REQUEST_TIMES and now - AI_REQUEST_TIMES[0] >= 60:
                AI_REQUEST_TIMES.popleft()
            if len(AI_REQUEST_TIMES) < RPM_LIMIT:
                AI_REQUEST_TIMES.append(now)
                return
            wait_seconds = max(0.1, 60 - (now - AI_REQUEST_TIMES[0]))
        await asyncio.sleep(wait_seconds)

async def call_gemini(
    message: Message,
    text_for_analysis: str,
    context_text: str,
    media_part,
    media_problem: Optional[str],
    urgency_hint: Optional[str],
) -> Optional[TriageResult]:
    await wait_for_rpm_slot()
    try:
        allowed = await asyncio.to_thread(reserve_daily_request)
    except Exception:
        log.exception("Could not reserve daily API quota.")
        return None
    if not allowed:
        log.warning("Gemini daily request limit reached.")
        return None

    faqs = await asyncio.to_thread(get_faqs)
    faq_payload = json.dumps(faqs, ensure_ascii=False)
    prompt = f"""
أنت مساعد فرز رسائل لطلاب سنة أولى طب في جروب جامعي مصري. أنت لا تملك سلطة إدارية أو طبية؛ دورك تنظيم الرسائل فقط، والقيادة البشرية صاحبة القرار النهائي.
حلل الرسالة الحالية مع السياق السابق وقاعدة الأسئلة الشائعة أدناه. لا تفترض أن معلومة جامعية صحيحة لمجرد أنها مكتوبة في الرسالة.

تصنيفات الأولوية:
- URGENT: يحتاج انتباه القيادة بسرعة.
- MEDIUM: يحتاج مراجعة/متابعة، لكن ليس عاجلاً.
- LOW: مسألة عادية تستحق رد FAQ أو مراجعة غير عاجلة.
- UNIMPORTANT: دردشة عادية أو شيء لا يحتاج أي إجراء.

الأفعال:
- LEADER_REVIEW: تحويل للقيادة عندما يلزم تأكيد، قرار، أو متابعة، أو عندما تكون الإجابة غير مؤكدة.
- FAQ_REPLY: استخدم فقط إذا كانت هناك إجابة FAQ موجودة ومطابقة للمعنى بشكل واضح. ضع faq_id الصحيح بالضبط. لا تخترع FAQ ولا تخلط سؤالاً قريباً بسؤال مختلف.
- NO_ACTION: فقط إذا كانت الرسالة بوضوح لا تحتاج أي إجراء.

قواعد مهمة:
1) لو الإجابة قريبة في الكلمات لكن مختلفة في المعنى، لا تستخدمها؛ اختر LEADER_REVIEW.
2) لو الرسالة تحتاج قراراً من الدكاترة/الإدارة، أو بها تضارب/معلومة غير مؤكدة، اختر LEADER_REVIEW.
3) لا تعتبر كل كلمة مفتاحية حالة مهمة تلقائياً؛ افهم المقصود والسياق.
4) لا تصنف الرسائل غير المهمة كعاجلة.
5) إذا وُجدت مشكلة في فحص المرفق، اختر LEADER_REVIEW.
6) استخدم عربية واضحة وبسيطة. summary_ar وreason_ar قصيران.
7) urgency_hint هو تلميح كتبه الطالب، وليس حقيقة مؤكدة. استخدمه ما لم يكن واضحاً أنه لا ينطبق.
8) لا تقدم نصائح طبية تشخيصية أو علاجية؛ وجّه المسائل الطبية الشخصية إلى مختص/جهة مناسبة.
9) أخرج JSON مطابقاً للمخطط فقط.

التلميح الذي كتبه الطالب للأولوية: {urgency_hint or "لا يوجد"}
مشكلة فحص المرفق إن وجدت: {media_problem or "لا توجد"}
المرفقات المرسلة للفحص: {"نعم" if media_part is not None else ("موجود لكن تعذر فحصه" if media_problem else "لا يوجد")}
السياق السابق المتاح:
{context_text}

الرسالة الأصلية كاملة (للحفاظ على المعنى، حتى لو احتوت منشن أو تلميح أولوية):
{text_for_analysis}

قاعدة FAQ الحالية:
{faq_payload}
"""
    contents = [prompt]
    if media_part is not None:
        contents.append(media_part)
    try:
        response = await gemini_async.models.generate_content(
            model=MODEL_NAME,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=TriageResult,
                temperature=0.1,
            ),
        )
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, TriageResult):
            return parsed
        raw = getattr(response, "text", None)
        if raw:
            return TriageResult.model_validate_json(raw)
        log.error("Gemini returned no parsable content.")
        return None
    except Exception:
        log.exception("Gemini request failed.")
        return None

# ----------------------------- Ticket delivery ---------------------------

def urgency_label(urgency: str) -> str:
    return {
        "URGENT": "عاجل",
        "MEDIUM": "متوسط",
        "LOW": "عادي",
        "UNIMPORTANT": "غير مهم",
    }.get(urgency, "يحتاج مراجعة")

async def send_ticket(
    message: Message,
    urgency: str,
    summary: str,
    reason: str,
    original_text: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> Optional[int]:
    try:
        ticket_id = await asyncio.to_thread(
            add_ticket, message, urgency, summary, reason, original_text
        )
    except Exception:
        log.exception("Could not persist ticket.")
        return None

    leader_chat_id = get_leader_chat_id()
    if not leader_chat_id:
        log.error("No leader group configured; ticket %s is saved but not delivered.", ticket_id)
        try:
            await message.reply_text(
                "وصلتني الرسالة، لكن جروب القيادة لم يتم ربطه بعد؛ لم أتمكن من إرسال التنبيه."
            )
        except TelegramError:
            pass
        return ticket_id

    text = (
        f"📌 <b>بلاغ #{ticket_id}</b>\n"
        f"الأولوية: <b>{urgency_label(urgency)}</b>\n"
        f"المرسل: {user_display(message)}\n"
        f"المجموعة: <code>{message.chat_id}</code> | الرسالة: <code>{message.message_id}</code>\n\n"
        f"<b>الملخص:</b> {safe_html(summary)}\n"
        f"<b>السبب:</b> {safe_html(reason)}\n"
        f"<b>النص الأصلي:</b>\n<pre>{safe_html(original_text[:3000])}</pre>"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("استلام البلاغ", callback_data=f"claim:{ticket_id}"),
            InlineKeyboardButton("تم الحل", callback_data=f"resolve:{ticket_id}"),
        ],
        [InlineKeyboardButton("إضافة المرسل لقائمة البوت السوداء", callback_data=f"blacklist:{ticket_id}")],
    ])
    try:
        sent = await context.bot.send_message(
            chat_id=leader_chat_id, text=text, parse_mode="HTML",
            reply_markup=keyboard, disable_web_page_preview=True,
        )
        await asyncio.to_thread(mark_ticket_sent, ticket_id, sent.message_id)
    except TelegramError:
        log.exception("Ticket %s saved, but delivery to leaders failed.", ticket_id)
    return ticket_id

# ----------------------------- Main message flow -------------------------

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global BOT_USERNAME
    message = update.effective_message
    if not message or not update.effective_chat or update.effective_chat.type not in {
        ChatType.GROUP, ChatType.SUPERGROUP
    }:
        return
    if message.from_user and message.from_user.is_bot:
        return
    leader_id = get_leader_chat_id()
    if ALLOWED_GROUP_ID:
        # When configured, monitor only the designated student discussion group.
        if message.chat_id != ALLOWED_GROUP_ID:
            return
    elif leader_id and message.chat_id == leader_id:
        # Until ALLOWED_GROUP_ID is configured, never triage leaders' own discussion.
        return
    if message.from_user and await asyncio.to_thread(is_blacklisted, message.from_user.id):
        return

    # Keep every observed group message as local context; no AI call happens just for this.
    push_context(message)
    raw_text = message_text(message)
    media_desc = media_description(message)
    if not raw_text and not media_desc:
        return

    mentioned = bot_is_mentioned(message)
    replied_to_bot = False
    if message.reply_to_message and message.reply_to_message.from_user:
        replied_to_bot = (
            message.reply_to_message.from_user.is_bot
            and BOT_USERNAME is not None
            and message.reply_to_message.from_user.username == BOT_USERNAME
        )

    keywords = await asyncio.to_thread(get_active_keywords)
    keyword = matched_keyword(raw_text, keywords) if raw_text else None
    triggered = mentioned or replied_to_bot or bool(keyword)
    if not triggered:
        return

    # Atomic deduplication: concurrent retries cannot both start Gemini calls.
    try:
        claimed = await asyncio.to_thread(
            claim_message_for_processing, message.chat_id, message.message_id
        )
        if not claimed:
            return
    except Exception:
        # Fail closed here: database trouble must not trigger unbounded duplicate
        # AI calls. The next Telegram retry can be processed after DB recovery.
        log.exception("Deduplication storage failed; skipping this message safely.")
        return

    hint, cleaned = urgency_hint_and_clean_text(raw_text)
    # Keep original text intact in the prompt and ticket. Cleaned text is only a readable supplement.
    analysis_text = raw_text or media_desc
    if cleaned and cleaned != raw_text:
        analysis_text += f"\n\n[نص بعد إزالة المنشن/تلميح الأولوية للتحليل: {cleaned}]"
    media_part, mime_type, media_problem = await get_media_part(message)
    if media_desc and not media_part and not media_problem:
        media_problem = "نوع المرفق غير مدعوم للفحص التلقائي؛ يلزم فحص بشري."
    context_text = make_context(message)

    # One Gemini call maximum per triggered message.
    result = await call_gemini(
        message, analysis_text, context_text, media_part, media_problem, hint
    )

    if result is None:
        await send_ticket(
            message,
            hint or "MEDIUM",
            "تعذر تحليل الرسالة تلقائياً؛ يلزم مراجعة بشرية.",
            "لم يتم استخدام الذكاء الاصطناعي بسبب حد الاستخدام أو خطأ في الخدمة/قاعدة البيانات.",
            analysis_text + (f"\n{media_desc}" if media_desc else ""),
            context,
        )
        if mentioned:
            try:
                await message.reply_text(
                    "مش قادر أحلل الرسالة حالياً، فحوّلتها للقيادة للمراجعة البشرية."
                )
            except TelegramError:
                pass
        return

    if result.urgency == "UNIMPORTANT" or result.action == "NO_ACTION":
        if mentioned:
            try:
                await message.reply_text(
                    "وصلتني رسالتك، لكن مش باين إنها محتاجة تحويل للقيادة حالياً."
                )
            except TelegramError:
                pass
        return

    should_escalate = result.action == "LEADER_REVIEW" or result.urgency == "URGENT"
    if should_escalate:
        await send_ticket(
            message, result.urgency, result.summary_ar, result.reason_ar,
            analysis_text + (f"\n{media_desc}" if media_desc else ""), context,
        )
        return

    if (
        result.action == "FAQ_REPLY"
        and AUTO_FAQ_REPLY
        and result.faq_id is not None
        and result.confidence >= FAQ_CONFIDENCE_THRESHOLD
    ):
        faq = await asyncio.to_thread(get_faq, result.faq_id)
        if faq:
            faq_id, question, answer = faq
            try:
                await message.reply_text(
                    f"إجابة من الأسئلة الشائعة (FAQ #{faq_id}):\n{answer}",
                    disable_web_page_preview=True,
                )
                await asyncio.to_thread(
                    lambda: _exec(
                        "UPDATE faqs SET last_used_at=? WHERE id=?",
                        (utc_now(), faq_id),
                    )
                )
                return
            except TelegramError:
                log.exception("Could not send FAQ reply.")

    # Any uncertainty or missing/low-confidence FAQ is handed to leaders.
    await send_ticket(
        message, result.urgency, result.summary_ar,
        result.reason_ar or "لا توجد إجابة FAQ مؤكدة بدرجة كافية.",
        analysis_text + (f"\n{media_desc}" if media_desc else ""), context,
    )

# ----------------------------- Commands ----------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "أنا بوت فرز رسائل الجروب. راقب الكلمات محلياً، وأستخدم Gemini فقط عند المنشن أو كلمة مفتاحية أو الرد عليّ.\n"
        "للمساعدة في الأوامر: /help"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "أوامر القيادة (داخل جروب القيادة أو بواسطة المالك):\n"
        "/setleaders — ربط الجروب الحالي كجروب قيادة (للمالك فقط)\n"
        "/status — حالة الإعدادات والاستخدام\n"
        "/addword كلمة | /delword كلمة | /listwords\n"
        "/addfaq السؤال | الإجابة | الكلمات الاختيارية\n"
        "/listfaqs | /delfaq رقم\n"
        "/reviewold — عرض الأسئلة الشائعة التي لم تُراجع منذ 90 يوماً\n"
        "/archivefaq رقم — أرشفة FAQ يدوياً\n\n"
        "تلميح الأولوية يمكن كتابته في أي مكان: urgent / medium / low أو عاجل / متوسط / منخفض.\n"
        "ملاحظة: القائمة السوداء تمنع البوت من معالجة رسائل المستخدم فقط؛ لا تطرده من تيليجرام."
    )

async def setleaders_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        await message.reply_text("الأمر ده متاح لمالك البوت فقط.")
        return
    if not update.effective_chat or update.effective_chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await message.reply_text("شغّل الأمر داخل جروب القيادة نفسه.")
        return
    set_config("leader_chat_id", str(update.effective_chat.id))
    await message.reply_text(
        f"تم ربط جروب القيادة الحالي (ID: {update.effective_chat.id}).\n"
        "للتأكد من صلاحيات الأعضاء، أضف البوت للجروب واجعله مشرفاً بصلاحيات محدودة."
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_leader(update, context):
        return
    leader_id = get_leader_chat_id()
    day = datetime.now(timezone.utc).date().isoformat()
    try:
        row = _exec("SELECT requests FROM api_usage WHERE usage_date=?", (day,)).rows
        used = int(row[0][0]) if row else 0
        keyword_count = len(get_active_keywords())
        faq_count = len(get_faqs())
        await update.effective_message.reply_text(
            f"حالة البوت:\n"
            f"جروب القيادة: {leader_id or 'غير مضبوط'}\n"
            f"النموذج: {MODEL_NAME}\n"
            f"استخدام Gemini اليوم: {used}/{RPD_LIMIT}\n"
            f"حد الدقيقة: {RPM_LIMIT} طلب/دقيقة\n"
            f"الكلمات النشطة: {keyword_count}\n"
            f"الأسئلة الشائعة النشطة: {faq_count}\n"
            f"الرد التلقائي من FAQ: {'مفعّل' if AUTO_FAQ_REPLY else 'متوقف'}"
        )
    except Exception:
        log.exception("Status command failed.")
        await update.effective_message.reply_text("تعذر قراءة الحالة من قاعدة البيانات.")

async def addword_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_leader(update, context):
        return
    keyword = " ".join(context.args).strip()
    if not keyword:
        await update.effective_message.reply_text("استخدم: /addword الكلمة أو العبارة")
        return
    try:
        _exec(
            "INSERT OR IGNORE INTO smart_keywords(keyword, active, created_by, created_at) VALUES (?,1,?,?)",
            (keyword, update.effective_user.id, utc_now()),
        )
        await update.effective_message.reply_text(f"تمت إضافة/تفعيل الكلمة: {keyword}")
    except Exception:
        log.exception("Could not add keyword.")
        await update.effective_message.reply_text("حصل خطأ أثناء حفظ الكلمة.")

async def delword_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_leader(update, context):
        return
    keyword = " ".join(context.args).strip()
    if not keyword:
        await update.effective_message.reply_text("استخدم: /delword الكلمة أو العبارة")
        return
    _exec("UPDATE smart_keywords SET active=0 WHERE lower(keyword)=lower(?)", (keyword,))
    await update.effective_message.reply_text(f"تم تعطيل الكلمة إن كانت موجودة: {keyword}")

async def listwords_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_leader(update, context):
        return
    words = await asyncio.to_thread(get_active_keywords)
    await update.effective_message.reply_text(
        "الكلمات المفتاحية النشطة:\n" + ("\n".join(f"• {word}" for word in words) or "لا توجد كلمات.")
    )

async def addfaq_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_leader(update, context):
        return
    raw = " ".join(context.args).strip()
    parts = [part.strip() for part in raw.split("|", 2)]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        await update.effective_message.reply_text(
            "استخدم:\n/addfaq السؤال | الإجابة | كلمات مفتاحية اختيارية"
        )
        return
    question, answer = parts[0], parts[1]
    keywords = parts[2] if len(parts) > 2 else ""
    now = utc_now()
    _exec(
        """INSERT INTO faqs(question, answer, keywords, active, created_by, created_at, updated_at, last_reviewed_at)
           VALUES (?, ?, ?, 1, ?, ?, ?, ?)""",
        (question, answer, keywords, update.effective_user.id, now, now, now),
    )
    await update.effective_message.reply_text("تمت إضافة السؤال الشائع.")

async def listfaqs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_leader(update, context):
        return
    rows = _exec(
        "SELECT id, question FROM faqs WHERE active=1 ORDER BY id"
    ).rows
    output = "\n".join(f"#{row[0]} — {row[1]}" for row in rows)
    await update.effective_message.reply_text(output or "لا توجد أسئلة شائعة نشطة.")

async def delfaq_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_leader(update, context):
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("استخدم: /delfaq رقم")
        return
    faq_id = int(context.args[0])
    _exec("UPDATE faqs SET active=0, updated_at=? WHERE id=?", (utc_now(), faq_id))
    await update.effective_message.reply_text(f"تم تعطيل FAQ #{faq_id} إن كان موجوداً.")

async def archivefaq_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_leader(update, context):
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("استخدم: /archivefaq رقم")
        return
    faq_id = int(context.args[0])
    _exec(
        "UPDATE faqs SET active=0, updated_at=?, last_reviewed_at=? WHERE id=?",
        (utc_now(), utc_now(), faq_id),
    )
    await update.effective_message.reply_text(f"تمت أرشفة FAQ #{faq_id}.")

async def reviewold_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_leader(update, context):
        return
    rows = _exec(
        """SELECT id, question, COALESCE(last_reviewed_at, created_at)
           FROM faqs WHERE active=1
           AND julianday('now') - julianday(COALESCE(last_reviewed_at, created_at)) >= 90
           ORDER BY id"""
    ).rows
    if not rows:
        await update.effective_message.reply_text("لا توجد أسئلة شائعة تجاوزت 90 يوماً دون مراجعة.")
        return
    lines = ["أسئلة تحتاج مراجعة يدوية (لم تُحذف تلقائياً):"]
    for row in rows[:40]:
        lines.append(f"#{row[0]} — {row[1]}")
    await update.effective_message.reply_text("\n".join(lines))

async def blacklist_ticket_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE, ticket_id: int, actor_id: int
) -> None:
    row = await asyncio.to_thread(ticket_row, ticket_id)
    if not row or row[3] is None:
        await update.callback_query.answer("لم أجد صاحب البلاغ.", show_alert=True)
        return
    user_id = int(row[3])
    _exec(
        "INSERT INTO blacklist(user_id, reason, added_by, created_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET reason=excluded.reason, added_by=excluded.added_by, created_at=excluded.created_at",
        (user_id, f"Added from ticket #{ticket_id}", actor_id, utc_now()),
    )
    await update.callback_query.answer("تمت إضافة المستخدم لقائمة البوت السوداء.", show_alert=True)

async def ticket_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    if not await is_leader(update, context):
        await query.answer("الإجراء ده متاح لأعضاء جروب القيادة فقط.", show_alert=True)
        return
    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        await query.answer("زر غير صالح.", show_alert=True)
        return
    action, ticket_id = parts[0], int(parts[1])
    if action == "blacklist":
        await blacklist_ticket_callback(update, context, ticket_id, update.effective_user.id)
        return
    if action == "claim":
        await asyncio.to_thread(set_ticket_status, ticket_id, "claimed", update.effective_user.id)
        await query.answer("تم تسجيل استلام البلاغ.")
    elif action == "resolve":
        await asyncio.to_thread(set_ticket_status, ticket_id, "resolved", update.effective_user.id)
        await query.answer("تم تسجيل البلاغ كمحلول.")
    else:
        await query.answer("إجراء غير معروف.")
        return
    # Update visible ticket card, but preserve text if editing fails.
    try:
        old_text = query.message.text_html if query.message else ""
        suffix = f"\n\nالحالة: <b>{'تم استلامه' if action == 'claim' else 'تم الحل'}</b> بواسطة {html.escape(update.effective_user.full_name)}"
        if query.message and old_text and "الحالة:" not in old_text:
            await query.edit_message_text(
                old_text + suffix, parse_mode="HTML",
                reply_markup=query.message.reply_markup,
            )
    except (TelegramError, BadRequest):
        log.info("Could not update ticket message after callback.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled Telegram handler error: %s", context.error, exc_info=context.error)

# ----------------------------- Render health server ----------------------

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/health/live"):
            status, body = 200, b"OK"
        elif path == "/health/ready":
            ready = DB_READY and BOT_READY
            status, body = (200, b"READY") if ready else (503, b"NOT READY")
        else:
            status, body = 404, b"NOT FOUND"
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        # Avoid noisy health-check access logs on Render.
        return


def start_health_server() -> None:
    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    Thread(target=server.serve_forever, name="render-health", daemon=True).start()
    log.info("Health server listening on port %s", port)


# ----------------------------- Entrypoint --------------------------------

def main() -> None:
    global BOT_USERNAME, DB_READY, BOT_READY
    start_health_server()
    init_db()
    DB_READY = True

    async def post_init(application: Application) -> None:
        global BOT_USERNAME, BOT_READY
        me = await application.bot.get_me()
        BOT_USERNAME = me.username
        BOT_READY = True
        log.info("Bot started as @%s; Gemini model=%s", BOT_USERNAME, MODEL_NAME)

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("setleaders", setleaders_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("addword", addword_command))
    app.add_handler(CommandHandler("delword", delword_command))
    app.add_handler(CommandHandler("listwords", listwords_command))
    app.add_handler(CommandHandler("addfaq", addfaq_command))
    app.add_handler(CommandHandler("listfaqs", listfaqs_command))
    app.add_handler(CommandHandler("delfaq", delfaq_command))
    app.add_handler(CommandHandler("archivefaq", archivefaq_command))
    app.add_handler(CommandHandler("reviewold", reviewold_command))
    app.add_handler(CallbackQueryHandler(ticket_callback, pattern=r"^(claim|resolve|blacklist):\d+$"))
    # Group messages include plain text, captions, photos, video, voice/audio and documents.
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & ~filters.COMMAND,
        handle_group_message,
    ))
    app.add_error_handler(error_handler)

    log.info("Starting polling.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)

if __name__ == "__main__":
    main()
