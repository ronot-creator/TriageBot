import os
import sqlite3
import logging
import json
import re
import asyncio
from collections import defaultdict, deque
from typing import Dict, Optional, Tuple, List

from openai import AsyncOpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment Variables & Configuration
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

ai_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)
LLM_MODEL = "google/gemini-2.0-flash-lite-001"

# ---------------------------------------------------------------------------
# Global State & Memory
# ---------------------------------------------------------------------------
# Expanded context window from 5 to 10 based on engineering review
GROUP_CONTEXTS: Dict[int, deque] = defaultdict(lambda: deque(maxlen=10))

# In-memory cache for keywords to prevent SQLite I/O bottlenecks on every message
ACTIVE_KEYWORDS_CACHE: List[str] = []

# ---------------------------------------------------------------------------
# Database Management (Robust State Persistence)
# ---------------------------------------------------------------------------
def init_db():
    """Initializes tables and populates default keywords if empty."""
    conn = sqlite3.connect("triage_system.db")
    cursor = conn.cursor()
    
    # Blacklist Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS blacklist (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            reason TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Config Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    # Smart Keywords Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS smart_keywords (
            word TEXT PRIMARY KEY,
            added_by TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Seed default keywords if table is empty
    cursor.execute("SELECT COUNT(*) FROM smart_keywords")
    if cursor.fetchone()[0] == 0:
        default_words = [
            "امتحان", "اتلغى", "ميدترم", "تاجيل", "تأجيل", 
            "دكتور", "ضروري", "سقوط", "data", "داتا"
        ]
        for w in default_words:
            cursor.execute("INSERT INTO smart_keywords (word, added_by) VALUES (?, ?)", (w, "system"))
            
    conn.commit()
    conn.close()
    
    refresh_keywords_cache()

def refresh_keywords_cache():
    """Loads keywords from SQLite into high-speed memory."""
    global ACTIVE_KEYWORDS_CACHE
    conn = sqlite3.connect("triage_system.db")
    cursor = conn.cursor()
    cursor.execute("SELECT word FROM smart_keywords")
    ACTIVE_KEYWORDS_CACHE = [row[0].lower() for row in cursor.fetchall()]
    conn.close()
    logger.info(f"Loaded {len(ACTIVE_KEYWORDS_CACHE)} smart keywords into cache.")

def add_smart_keyword(word: str, admin_name: str) -> bool:
    word = word.lower().strip()
    try:
        conn = sqlite3.connect("triage_system.db")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO smart_keywords (word, added_by) VALUES (?, ?)", (word, admin_name))
        conn.commit()
        conn.close()
        refresh_keywords_cache()
        return True
    except sqlite3.IntegrityError:
        return False # Word already exists

def remove_smart_keyword(word: str) -> bool:
    word = word.lower().strip()
    conn = sqlite3.connect("triage_system.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM smart_keywords WHERE word = ?", (word,))
    changes = conn.total_changes
    conn.commit()
    conn.close()
    refresh_keywords_cache()
    return changes > 0

def get_leader_group_id() -> Optional[int]:
    conn = sqlite3.connect("triage_system.db")
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM config WHERE key = 'leader_group_id'")
    result = cursor.fetchone()
    conn.close()
    return int(result[0]) if result else None

def set_leader_group_id(chat_id: int):
    conn = sqlite3.connect("triage_system.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('leader_group_id', ?)", (str(chat_id),))
    conn.commit()
    conn.close()

def is_banned(user_id: int) -> bool:
    conn = sqlite3.connect("triage_system.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM blacklist WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def ban_user(user_id: int, username: str, reason: str = "Overuse / Spam"):
    conn = sqlite3.connect("triage_system.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO blacklist (user_id, username, reason) VALUES (?, ?, ?)",
        (user_id, username, reason)
    )
    conn.commit()
    conn.close()

# ---------------------------------------------------------------------------
# Dynamic Group Tracking Logic
# ---------------------------------------------------------------------------
def update_dynamic_leader_status(chat_id: int, chat_title: str):
    if not chat_title:
        return
    LEADER_KEYWORDS = ["leader", "leaders", "رؤساء", "قيادة"]
    title_lower = chat_title.lower()
    if any(keyword in title_lower for keyword in LEADER_KEYWORDS):
        current_id = get_leader_group_id()
        if current_id != chat_id:
            set_leader_group_id(chat_id)
            logger.info(f"👑 Saved Leader Group ID to Database: {chat_id}")

async def track_group_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat and chat.type in ["group", "supergroup"]:
        update_dynamic_leader_status(chat.id, chat.title)

# ---------------------------------------------------------------------------
# Admin Commands (For Leaders to manage Database dynamically)
# ---------------------------------------------------------------------------
async def cmd_addword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command to add a new smart listener keyword (Admin/Leader only)."""
    # Security check: Ensure command is run in the leader group or by an admin
    if update.effective_chat.id != get_leader_group_id():
        return
    
    if not context.args:
        await update.message.reply_text("استخدم: /addword الكلمة")
        return
        
    word = context.args[0]
    success = add_smart_keyword(word, update.message.from_user.first_name)
    if success:
        await update.message.reply_text(f"✅ تم إضافة '{word}' لقاعدة البيانات.")
    else:
        await update.message.reply_text(f"⚠️ الكلمة '{word}' موجودة بالفعل.")

async def cmd_delword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command to remove a smart listener keyword."""
    if update.effective_chat.id != get_leader_group_id():
        return
        
    if not context.args:
        await update.message.reply_text("استخدم: /delword الكلمة")
        return
        
    word = context.args[0]
    success = remove_smart_keyword(word)
    if success:
        await update.message.reply_text(f"🗑️ تم حذف '{word}' من قاعدة البيانات.")
    else:
        await update.message.reply_text(f"❌ الكلمة '{word}' غير موجودة.")

async def cmd_listwords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists all active smart keywords."""
    if update.effective_chat.id != get_leader_group_id():
        return
    words = ", ".join(ACTIVE_KEYWORDS_CACHE)
    await update.message.reply_text(f"📝 **الكلمات المفتاحية النشطة:**\n{words}", parse_mode="Markdown")

# ---------------------------------------------------------------------------
# AI Engine: OpenRouter Gemini Evaluator
# ---------------------------------------------------------------------------
async def evaluate_triage(user_name: str, target_text: str, context_logs: str) -> dict:
    prompt = f"""
    You are an AI Triage Assistant for medical student leaders.
    Analyze the request in Egyptian Ameya/Arabic/English.

    Context Messages (Last 10 messages - Warning: May contain unrelated noisy chatter):
    {context_logs if context_logs else "No context"}
    
    Target Request:
    "{target_text}"

    Tasks:
    1. Determine urgency: URGENT, MID, or LOW. 
       - CRITICAL: Filter out context noise. Only grade based on the Target Request's true intent.
       - Use LOW for absolute spam/chatter or questions already clearly answered in the context.
    2. Provide a concise Arabic summary.
    3. Reason for classification.

    Respond STRICTLY in JSON:
    {{
        "urgency": "URGENT", 
        "summary": "Arabic summary...",
        "reason": "Because..."
    }}
    """
    try:
        response = await ai_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            timeout=20.0
        )
        raw_text = response.choices[0].message.content.strip()
        
        json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))
        else:
            raise ValueError("No JSON found in LLM response.")
            
    except Exception as e:
        logger.error(f"LLM Eval Failed: {e}")
        return {
            "urgency": "URGENT",
            "summary": f"⚠️ (AI Fallback) {target_text[:200]}...",
            "reason": "API/Parsing Failure - Defaulted to Urgent."
        }

# ---------------------------------------------------------------------------
# Group Interaction & Smart Listener
# ---------------------------------------------------------------------------
async def handle_group_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    message = update.message
    bot_username = context.bot.username

    update_dynamic_leader_status(chat.id, chat.title)
    if is_banned(message.from_user.id):
        return

    text = message.text or message.caption or ""
    text_lower = text.lower()
    mention_tag = f"@{bot_username}"

    # Prepare rolling context
    recent_msgs = list(GROUP_CONTEXTS[chat.id])
    
    # CASE 1: Bot is explicitly mentioned
    if mention_tag in text:
        clean_request = text.replace(mention_tag, "").strip()
        reply_context = ""
        
        if message.reply_to_message and message.reply_to_message.text:
            reply_context = f"Replying to: {message.reply_to_message.text}\n"
            if not clean_request:
                clean_request = message.reply_to_message.text 
                
        if not clean_request:
            await message.reply_text("عذراً، يرجى كتابة مشكلتك بجوار التاج.")
            return

        context_str = reply_context + "\n".join(recent_msgs)
        processing_msg = await message.reply_text("⏳ جاري التحليل...")
        
        triage_data = await evaluate_triage(message.from_user.full_name, clean_request, context_str)
        await route_ticket(triage_data, message, context, processing_msg, explicit_mention=True)

    # CASE 2: Smart Listener (Checks against DB-backed Cache)
    elif any(kw in text_lower for kw in ACTIVE_KEYWORDS_CACHE):
        GROUP_CONTEXTS[chat.id].append(f"[{message.from_user.first_name}]: {text}")
        
        context_str = "\n".join(recent_msgs)
        triage_data = await evaluate_triage(message.from_user.full_name, text, context_str)
        
        if triage_data.get("urgency") == "URGENT":
            await route_ticket(triage_data, message, context, processing_msg=None, explicit_mention=False)
            
    else:
        GROUP_CONTEXTS[chat.id].append(f"[{message.from_user.first_name}]: {text}")

async def route_ticket(triage_data: dict, message, context, processing_msg=None, explicit_mention=True):
    urgency = triage_data.get("urgency", "URGENT")
    leader_group_id = get_leader_group_id()

    if urgency == "LOW" and explicit_mention:
        warning = "⚠️ تم تصنيف رسالتك كغير هامة. يرجى استخدام البوت للطوارئ فقط."
        if processing_msg:
            await processing_msg.edit_text(warning)
        return
    elif urgency == "LOW" and not explicit_mention:
        return

    if not leader_group_id:
        if processing_msg:
            await processing_msg.edit_text("⚠️ النظام قيد التهيئة. لا يوجد مجموعة قيادة.")
        return

    badge = "🔴 عاجل جداً" if urgency == "URGENT" else "🟡 متوسطة"
    listen_tag = "" if explicit_mention else "\n🤖 *(Auto-Detected)*"
    
    ticket_text = (
        f"📌 **تذكرة جديدة** {listen_tag}\n"
        f"• **الحالة:** {badge}\n"
        f"• **الاسم:** {message.from_user.full_name}\n"
        f"• **الملخص:** {triage_data.get('summary', 'بدون ملخص')}\n\n"
        f"🔍 *السبب:* {triage_data.get('reason', '')}"
    )

    kb = [
        [InlineKeyboardButton("✅ تم الحل", callback_data=f"claim_{message.from_user.id}")],
        [InlineKeyboardButton("🚫 حظر الطالب", callback_data=f"ban_{message.from_user.id}")]
    ]
    
    try:
        await context.bot.send_message(
            chat_id=leader_group_id, text=ticket_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
        )
        if processing_msg:
            await processing_msg.edit_text("✅ تم تحويل الرسالة للقيادة.")
    except Exception as e:
        logger.error(f"Routing failed: {e}")
        if processing_msg:
            await processing_msg.edit_text("❌ حدث خطأ أثناء التحويل.")

# ---------------------------------------------------------------------------
# Callback Handlers
# ---------------------------------------------------------------------------
async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    leader_name = query.from_user.first_name

    if data.startswith("claim_"):
        await query.edit_message_text(
            text=f"{query.message.text}\n\n✅ **تم الاستلام بواسطة:** {leader_name}",
            parse_mode="Markdown"
        )
    elif data.startswith("ban_"):
        target_id = int(data.split("_")[1])
        ban_user(target_id, "Unknown (Via Button)")
        await query.edit_message_text(
            text=f"{query.message.text}\n\n🚫 **تم الحظر بواسطة:** {leader_name}",
            parse_mode="Markdown"
        )

# ---------------------------------------------------------------------------
# Main App Initialization
# ---------------------------------------------------------------------------
def main():
    if not TELEGRAM_BOT_TOKEN or not OPENROUTER_API_KEY:
        raise ValueError("CRITICAL: Missing API Keys in Environment Variables.")

    init_db()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Admin Commands
    app.add_handler(CommandHandler("addword", cmd_addword))
    app.add_handler(CommandHandler("delword", cmd_delword))
    app.add_handler(CommandHandler("listwords", cmd_listwords))

    # Core Handlers
    app.add_handler(ChatMemberHandler(track_group_events, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT & (~filters.COMMAND), handle_group_messages))
    app.add_handler(CallbackQueryHandler(handle_callbacks))

    logger.info("🟢 SVU Med Triage Bot Started (Dynamic DB & Expanded Context)!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
