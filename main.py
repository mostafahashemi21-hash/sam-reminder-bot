# -*- coding: utf-8 -*-
"""
SAM PRO Team Manager V7 Clean
A stable Telegram task-management bot with menus, voice commands, OpenAI smart manager,
chat analysis, reminders, files, notes, checklist, Excel export, and clean state handling.
"""

import asyncio
import html
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, time
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database import (
    init_db,
    add_user,
    get_user,
    get_users,
    count_admins,
    get_admin_ids,
    set_user_role,
    create_task,
    get_task,
    get_tasks,
    get_open_tasks,
    complete_task,
    update_task_field,
    search_tasks,
    add_task_note,
    get_task_notes,
    add_history,
    get_task_history,
    add_checklist_item,
    get_checklist,
    toggle_checklist_item,
    add_task_file,
    get_task_files,
    link_task_message,
    get_task_id_by_message,
    save_chat_message,
    get_recent_chat_messages,
    get_chat_messages_between,
    save_ai_suggestion,
    get_ai_suggestion,
    update_ai_suggestion_status,
    now_str,
)

TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID_RAW = os.getenv("GROUP_CHAT_ID", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe").strip()
DELETE_USER_MENU_MESSAGES = os.getenv("DELETE_USER_MENU_MESSAGES", "false").lower() in {"1", "true", "yes"}

try:
    GROUP_CHAT_ID = int(GROUP_CHAT_ID_RAW) if GROUP_CHAT_ID_RAW else None
except Exception:
    GROUP_CHAT_ID = None

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

PROJECTS = ["تخته", "میوه", "پتروشیمی", "مالی", "غلات", "غیره"]
PRIORITIES = ["فوری", "زیاد", "متوسط", "کم"]
STATUS_TEXT = {
    "open": "باز",
    "in_progress": "در حال پیگیری",
    "waiting": "منتظر پاسخ",
    "done": "انجام شد",
    "cancelled": "لغو شد",
}
STATUS_EMOJI = {
    "open": "📍",
    "in_progress": "🔄",
    "waiting": "⏳",
    "done": "✅",
    "cancelled": "⛔",
}
PRIORITY_EMOJI = {"فوری": "🔥", "زیاد": "🔴", "متوسط": "🟡", "کم": "🟢"}

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
EN_DIGITS = "0123456789"
PERSIAN_WORD_NUMBERS = {
    "صفر": 0,
    "یک": 1,
    "يه": 1,
    "دو": 2,
    "سه": 3,
    "چهار": 4,
    "پنج": 5,
    "شش": 6,
    "هفت": 7,
    "هشت": 8,
    "نه": 9,
    "ده": 10,
    "یازده": 11,
    "دوازده": 12,
    "سیزده": 13,
    "چهارده": 14,
    "پانزده": 15,
    "شانزده": 16,
    "هفده": 17,
    "هجده": 18,
    "نوزده": 19,
    "بیست": 20,
}

MENU_LABELS = {
    "new_task": "کار جدید ➕",
    "tasks": "کارها 📋",
    "smart": "مدیر هوشمند 🧠",
    "summary": "تحلیل چت 🧠",
    "voice": "فرمان صوتی 🎙",
    "chatgpt": "چت جی پی تی 🤖",
    "reports": "گزارش‌ها 📊",
    "members": "اعضا 👥",
    "profile": "پروفایل 👤",
    "help": "راهنما ❓",
    "back": "بازگشت 🔙",
    "cancel": "لغو ❌",
}

MAIN_MENU_ACTIONS = {"new_task", "tasks", "smart", "summary", "voice", "chatgpt", "reports", "members", "profile", "help", "back", "cancel"}


def fa_to_en(text: str) -> str:
    text = text or ""
    for i in range(10):
        text = text.replace(PERSIAN_DIGITS[i], EN_DIGITS[i]).replace(ARABIC_DIGITS[i], EN_DIGITS[i])
    return text


def clean_text(text: str) -> str:
    text = fa_to_en(text or "")
    text = text.replace("ي", "ی").replace("ك", "ک")
    text = text.replace("‌", " ")
    text = re.sub(r"[\u200e\u200f]", "", text)
    return text.strip()


def norm(text: str) -> str:
    text = clean_text(text).lower()
    text = re.sub(r"[/@#:_\-،,.!?؟()\[\]{}\n\r\t]+", " ", text)
    text = re.sub(r"[📋➕🧠🎙🤖📊👥👤❓🔙❌✅📝📎⏰📌🗑🔄⏳⛔☑️🧾🔥🔴🟡🟢]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def detect_main_action(text: str) -> Optional[str]:
    n = norm(text)
    if not n:
        return None
    # Exact or strong button intents. Keep this strict to avoid creating wrong tasks.
    if n in {"کار جدید", "ایجاد کار", "ساخت کار", "تسک جدید", "وظیفه جدید", "new task", "newtask", "new"}:
        return "new_task"
    if n in {"کارها", "لیست کارها", "لیست کار", "tasks", "task", "پیگیری"}:
        return "tasks"
    if n in {"مدیر هوشمند", "smart", "تحلیل هوشمند"}:
        return "smart"
    if n in {"تحلیل چت", "خلاصه چت", "summary", "summarize", "تحلیل"}:
        return "summary"
    if n in {"فرمان صوتی", "ویس", "voice", "صوتی"}:
        return "voice"
    if n in {"چت جی پی تی", "چت gpt", "chatgpt", "chat gpt", "دستیار هوشمند", "دستیار"}:
        return "chatgpt"
    if n in {"گزارش ها", "گزارشها", "گزارش", "reports", "report"}:
        return "reports"
    if n in {"اعضا", "members", "کاربران"}:
        return "members"
    if n in {"پروفایل", "profile", "من"}:
        return "profile"
    if n in {"راهنما", "help"}:
        return "help"
    if n in {"بازگشت", "برگشت", "back", "منوی اصلی"}:
        return "back"
    if n in {"لغو", "کنسل", "cancel", "خروج", "exit"}:
        return "cancel"
    return None


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [MENU_LABELS["new_task"], MENU_LABELS["tasks"]],
            [MENU_LABELS["smart"], MENU_LABELS["summary"]],
            [MENU_LABELS["voice"], MENU_LABELS["chatgpt"]],
            [MENU_LABELS["reports"], MENU_LABELS["members"]],
            [MENU_LABELS["profile"], MENU_LABELS["help"]],
        ],
        resize_keyboard=True,
    )


def project_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("تخته", callback_data="new:project:تخته"), InlineKeyboardButton("میوه", callback_data="new:project:میوه")],
            [InlineKeyboardButton("پتروشیمی", callback_data="new:project:پتروشیمی"), InlineKeyboardButton("مالی", callback_data="new:project:مالی")],
            [InlineKeyboardButton("غلات", callback_data="new:project:غلات"), InlineKeyboardButton("غیره", callback_data="new:project:غیره")],
            [InlineKeyboardButton("لغو", callback_data="flow:cancel")],
        ]
    )


def responsible_keyboard() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton("بدون مسئول", callback_data="new:assignee:0"), InlineKeyboardButton("خودم", callback_data="new:assignee:self")]]
    row = []
    for user in get_users():
        label = (user.get("full_name") or user.get("username") or str(user.get("user_id")))[:28]
        row.append(InlineKeyboardButton(label, callback_data=f"new:assignee:{user['user_id']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("لغو", callback_data="flow:cancel")])
    return InlineKeyboardMarkup(buttons)


def reminder_keyboard(prefix: str = "new") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("بدون یادآوری", callback_data=f"{prefix}:reminder:none")],
            [InlineKeyboardButton("فردا 10:00", callback_data=f"{prefix}:reminder:tomorrow_10"), InlineKeyboardButton("امروز 18:00", callback_data=f"{prefix}:reminder:today_18")],
            [InlineKeyboardButton("زمان دلخواه", callback_data=f"{prefix}:reminder:custom")],
            [InlineKeyboardButton("لغو", callback_data="flow:cancel")],
        ]
    )


def reports_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("آمار", callback_data="report:stats"), InlineKeyboardButton("گزارش روزانه", callback_data="report:daily")],
            [InlineKeyboardButton("گزارش هفتگی", callback_data="report:weekly"), InlineKeyboardButton("خروجی اکسل", callback_data="report:excel")],
            [InlineKeyboardButton("خروجی PDF/TXT", callback_data="report:pdf"), InlineKeyboardButton("بازگشت", callback_data="menu:back")],
        ]
    )


def summary_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("۱ ساعت اخیر", callback_data="summary:1h"), InlineKeyboardButton("۲ ساعت اخیر", callback_data="summary:2h")],
            [InlineKeyboardButton("دیروز", callback_data="summary:yesterday"), InlineKeyboardButton("۷ روز اخیر", callback_data="summary:7d")],
            [InlineKeyboardButton("بازگشت", callback_data="menu:back")],
        ]
    )


def html_escape(value: Any) -> str:
    return html.escape(str(value or ""))


def status_label(status: str) -> str:
    return f"{STATUS_EMOJI.get(status, '📍')} {STATUS_TEXT.get(status, status)}"


def priority_label(priority: str) -> str:
    return f"{PRIORITY_EMOJI.get(priority, '🟡')} {priority or 'متوسط'}"


def extract_task_id(text: str) -> Optional[int]:
    text = norm(text)
    m = re.search(r"(?:کار|task)?\s*(\d+)", text)
    if m:
        return int(m.group(1))
    for word, num in PERSIAN_WORD_NUMBERS.items():
        if re.search(rf"(?:کار|شماره)\s+{word}\b", text):
            return num
    return None


def detect_status(text: str) -> Optional[str]:
    n = norm(text)
    if any(x in n for x in ["انجام شد", "انجام دادم", "فرستادم", "تمام شد", "اوکی شد", "حل شد", "done"]):
        return "done"
    if any(x in n for x in ["منتظر", "جواب", "پاسخ", "خبر بده", "waiting"]):
        return "waiting"
    if any(x in n for x in ["پیگیری", "در حال", "شروع", "in progress"]):
        return "in_progress"
    if any(x in n for x in ["لغو", "حذف", "کنسل", "cancel"]):
        return "cancelled"
    return None


def detect_priority(text: str) -> str:
    n = norm(text)
    if any(x in n for x in ["فوری", "خیلی مهم", "اضطراری", "urgent"]):
        return "فوری"
    if any(x in n for x in ["مهم", "زیاد", "بالا", "high"]):
        return "زیاد"
    if any(x in n for x in ["کم", "پایین", "بعدا", "low"]):
        return "کم"
    return "متوسط"


def detect_project(text: str) -> str:
    n = norm(text)
    if any(x in n for x in ["تخته", "چوب", "روسیه", "الوار", "wood", "timber"]):
        return "تخته"
    if any(x in n for x in ["میوه", "نکتارین", "کاهو", "سیب", "کرفس", "پسته", "fruit"]):
        return "میوه"
    if any(x in n for x in ["پتروشیمی", "سایبور", "sibur", "پلیمر", "مواد", "chemical"]):
        return "پتروشیمی"
    if any(x in n for x in ["مالی", "پول", "حساب", "بانک", "پرداخت", "invoice", "payment"]):
        return "مالی"
    if any(x in n for x in ["غلات", "گندم", "جو", "ذرت", "نخود", "grain"]):
        return "غلات"
    return "غیره"


def parse_datetime_text(text: str) -> str:
    raw = clean_text(text)
    n = norm(raw)
    now = datetime.now()
    if not raw or any(x in n for x in ["بدون", "ندارد", "none", "no"]):
        return ""
    if "tomorrow_10" in raw:
        return (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
    if "today_18" in raw:
        return now.replace(hour=18, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
    hm = re.search(r"(\d{1,2})[:.](\d{2})", raw)
    if "فردا" in n:
        base = now + timedelta(days=1)
        if hm:
            return base.replace(hour=int(hm.group(1)), minute=int(hm.group(2)), second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
        return base.replace(hour=10, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
    if "امروز" in n:
        if hm:
            return now.replace(hour=int(hm.group(1)), minute=int(hm.group(2)), second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
        return now.strftime("%Y-%m-%d %H:%M")
    for fmt in ["%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%d.%m.%Y %H:%M", "%d-%m-%Y %H:%M"]:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    return "INVALID"


def user_display(user_id: Optional[int]) -> str:
    if not user_id:
        return "بدون مسئول"
    user = get_user(int(user_id))
    if not user:
        return str(user_id)
    return user.get("full_name") or ("@" + user.get("username", "")) or str(user_id)


def find_user_by_text(text: str) -> Optional[int]:
    n = norm(text).replace("@", "")
    if not n:
        return None
    if n in {"خودم", "من"}:
        return -1
    if n in {"بدون مسئول", "بدون", "نامشخص"}:
        return 0
    for user in get_users():
        username = norm(user.get("username") or "")
        fullname = norm(user.get("full_name") or "")
        if n == str(user.get("user_id")):
            return int(user["user_id"])
        if username and username in n:
            return int(user["user_id"])
        if fullname and (fullname in n or n in fullname):
            return int(user["user_id"])
    return None


def parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M")
    except Exception:
        return None


async def safe_delete_message(update: Update) -> None:
    if not DELETE_USER_MENU_MESSAGES or not update.message:
        return
    try:
        if update.effective_chat.type != "private":
            await update.message.delete()
    except Exception:
        pass


async def register_user(update: Update) -> None:
    if not update.effective_user:
        return
    user = update.effective_user
    role = "admin" if count_admins() == 0 else None
    add_user(user.id, user.username, user.full_name, role=role)


def clear_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("flow", None)
    context.user_data.pop("ai_chat", None)
    context.user_data.pop("waiting_custom_reminder_task", None)
    context.user_data.pop("waiting_note_task", None)
    context.user_data.pop("waiting_check_task", None)


def set_flow(context: ContextTypes.DEFAULT_TYPE, name: str, step: str, data: Optional[Dict[str, Any]] = None) -> None:
    context.user_data["flow"] = {"name": name, "step": step, "data": data or {}}


def get_flow(context: ContextTypes.DEFAULT_TYPE) -> Optional[Dict[str, Any]]:
    return context.user_data.get("flow")


# ------------------------- task UI -------------------------

def task_title_line(task: Dict[str, Any]) -> str:
    pin = "📌 " if int(task.get("pinned") or 0) else ""
    return f"{pin}#{task['id']} | 📋 {task['title'][:45]}"


def task_details_text(task: Dict[str, Any]) -> str:
    reminder = task.get("reminder_time") or "ندارد"
    if reminder == "none":
        reminder = "ندارد"
    desc = task.get("description") or "-"
    return (
        f"📂 <b>کار #{task['id']}</b>\n\n"
        f"📌 <b>عنوان:</b> {html_escape(task.get('title'))}\n"
        f"🗂 <b>پروژه:</b> {html_escape(task.get('project') or 'غیره')}\n"
        f"{STATUS_EMOJI.get(task.get('status'), '📍')} <b>وضعیت:</b> {html_escape(STATUS_TEXT.get(task.get('status'), task.get('status')))}\n"
        f"{PRIORITY_EMOJI.get(task.get('priority'), '🟡')} <b>اولویت:</b> {html_escape(task.get('priority') or 'متوسط')}\n"
        f"👤 <b>مسئول:</b> {html_escape(user_display(task.get('assigned_to')))}\n"
        f"⏰ <b>یادآوری:</b> {html_escape(reminder)}\n\n"
        f"📝 <b>توضیح:</b>\n{html_escape(desc)}"
    )


def tasks_list_keyboard(tasks: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for task in tasks[:30]:
        label = task_title_line(task)
        rows.append([InlineKeyboardButton(label, callback_data=f"task:open:{task['id']}")])
    rows.append([InlineKeyboardButton("کار جدید ➕", callback_data="flow:new_task")])
    return InlineKeyboardMarkup(rows)


def task_menu_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ انجام شد", callback_data=f"task:status:{task_id}:done"), InlineKeyboardButton("🔄 پیگیری", callback_data=f"task:status:{task_id}:in_progress")],
            [InlineKeyboardButton("⏳ منتظر پاسخ", callback_data=f"task:status:{task_id}:waiting"), InlineKeyboardButton("⛔ لغو", callback_data=f"task:status:{task_id}:cancelled")],
            [InlineKeyboardButton("📝 شرح‌ها", callback_data=f"task:notes:{task_id}"), InlineKeyboardButton("➕ شرح", callback_data=f"task:addnote:{task_id}")],
            [InlineKeyboardButton("☑️ چک‌لیست", callback_data=f"task:checklist:{task_id}"), InlineKeyboardButton("➕ چک‌لیست", callback_data=f"task:addcheck:{task_id}")],
            [InlineKeyboardButton("📎 فایل‌ها", callback_data=f"task:files:{task_id}"), InlineKeyboardButton("⏰ یادآوری", callback_data=f"task:reminder:{task_id}")],
            [InlineKeyboardButton("🧾 تاریخچه", callback_data=f"task:history:{task_id}"), InlineKeyboardButton("📌 پین", callback_data=f"task:pin:{task_id}")],
            [InlineKeyboardButton("🗑 حذف", callback_data=f"task:delete:{task_id}"), InlineKeyboardButton("🔙 لیست کارها", callback_data="task:list")],
        ]
    )


async def send_or_edit(query, text: str, reply_markup=None, parse_mode: Optional[str] = None) -> None:
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest:
        await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)


async def show_tasks(update_or_query: Any, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    tasks = get_open_tasks(100)
    text = "📋 کارهای باز\n\nروی هر کار بزن تا منوی همان کار باز شود."
    if not tasks:
        text += "\n\n✅ کار بازی وجود ندارد."
    kb = tasks_list_keyboard(tasks)
    if hasattr(update_or_query, "edit_message_text"):
        await send_or_edit(update_or_query, text, kb)
    else:
        msg = await update_or_query.message.reply_text(text, reply_markup=kb)
        for task in tasks:
            link_task_message(update_or_query.effective_chat.id, msg.message_id, task["id"])


async def show_task_detail(query, task_id: int) -> None:
    task = get_task(task_id)
    if not task or int(task.get("deleted") or 0):
        await send_or_edit(query, "این کار پیدا نشد یا حذف شده است.", tasks_list_keyboard(get_open_tasks(100)))
        return
    await send_or_edit(query, task_details_text(task), task_menu_keyboard(task_id), parse_mode="HTML")
    try:
        link_task_message(query.message.chat_id, query.message.message_id, task_id)
    except Exception:
        pass


# ------------------------- commands -------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    clear_flow(context)
    await update.message.reply_text("✅ ربات آماده است", reply_markup=main_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    clear_flow(context)
    await update.message.reply_text(
        "❓ راهنما\n\n"
        "📋 /tasks — لیست دکمه‌ای کارها\n"
        "➕ /newtask — ساخت کار مرحله‌ای\n"
        "🧠 /smart — مدیر هوشمند؛ چت‌ها و کارها را تحلیل می‌کند\n"
        "🧠 /summary — تحلیل چت با انتخاب بازه زمانی\n"
        "✅ /done 1 — انجام‌شده کردن کار\n"
        "⏰ /remind 1 2026-06-25 18:00 — تنظیم یادآوری\n"
        "📤 /export_excel — خروجی اکسل\n\n"
        "برای اضافه کردن شرح: روی پیام منوی کار Reply کن یا بنویس: کار 1: توضیح...",
        reply_markup=main_keyboard(),
    )


async def chatid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Chat ID:\n{update.effective_chat.id}\n\nType:\n{update.effective_chat.type}")


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    clear_flow(context)
    await show_tasks(update, context)


async def newtask_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    args = " ".join(context.args).strip()
    if args:
        await create_task_from_free_text(update, context, args, quiet=False)
        return
    await start_new_task_flow(update, context)


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    task_id = extract_task_id(" ".join(context.args)) if context.args else None
    if not task_id:
        await update.message.reply_text("مثال: /done 1")
        return
    await change_status_and_reply(update, task_id, "done")


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    task_id = extract_task_id(" ".join(context.args)) if context.args else None
    if not task_id or not get_task(task_id):
        await update.message.reply_text("مثال: /delete 1")
        return
    update_task_field(task_id, "deleted", 1)
    update_task_field(task_id, "status", "cancelled")
    add_history(task_id, update.effective_user.id, update.effective_user.full_name, "delete", "", "deleted")
    await update.message.reply_text("✅ حذف شد")


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    raw = " ".join(context.args).strip()
    task_id = extract_task_id(raw)
    if not task_id or not get_task(task_id):
        await update.message.reply_text("مثال: /remind 1 2026-06-25 18:00")
        return
    rest = re.sub(r"^\s*\d+\s*", "", fa_to_en(raw)).strip()
    dt = parse_datetime_text(rest)
    if dt == "INVALID":
        await update.message.reply_text("فرمت زمان اشتباه است. مثال: 2026-06-25 18:00 یا فردا 10:00")
        return
    update_task_field(task_id, "reminder_time", dt)
    add_history(task_id, update.effective_user.id, update.effective_user.full_name, "reminder", "", dt)
    await update.message.reply_text("✅ تنظیم شد")


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    clear_flow(context)
    if context.args:
        await run_summary(update, context, context.args[0])
        return
    await update.message.reply_text("بازه تحلیل چت را انتخاب کن:", reply_markup=summary_keyboard())


async def smart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    clear_flow(context)
    await run_smart_manager(update, context)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    await update.message.reply_text(build_stats_text())


async def daily_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    await update.message.reply_text(build_daily_report())


async def weekly_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    await update.message.reply_text(build_weekly_report())


async def members_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    rows = get_users()
    if not rows:
        await update.message.reply_text("عضوی ثبت نشده است.")
        return
    text = "👥 اعضا\n\n" + "\n".join(f"{'👑' if u.get('role')=='admin' else '👤'} {u.get('full_name') or '-'} | @{u.get('username') or '-'}" for u in rows)
    await update.message.reply_text(text)


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    u = get_user(update.effective_user.id)
    await update.message.reply_text(f"👤 پروفایل\n\nنام: {update.effective_user.full_name}\nیوزرنیم: @{update.effective_user.username or '-'}\nنقش: {u.get('role') if u else '-'}")


async def export_excel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    try:
        path = build_excel_report()
        await update.message.reply_document(InputFile(path), filename="sam_tasks_report.xlsx")
    except Exception as e:
        await update.message.reply_text(f"خطای خروجی اکسل: {e}")


async def export_pdf_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    path = build_text_report()
    await update.message.reply_document(InputFile(path), filename="sam_tasks_report.txt")


async def ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    clear_flow(context)
    context.user_data["ai_chat"] = True
    await update.message.reply_text("🤖 چت جی‌پی‌تی فعال شد. سوالت را بنویس. برای خروج: /exit")


async def exit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_flow(context)
    await update.message.reply_text("✅ خارج شد", reply_markup=main_keyboard())


# ------------------------- task creation flow -------------------------

async def start_new_task_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_flow(context)
    set_flow(context, "new_task", "title", {})
    await update.message.reply_text("عنوان کار را بنویس:", reply_markup=ReplyKeyboardMarkup([[MENU_LABELS["cancel"], MENU_LABELS["back"]]], resize_keyboard=True))


async def create_task_from_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, quiet: bool = True) -> int:
    title = re.sub(r"^(کار جدید|ایجاد کار|ساخت کار|تسک جدید)\s*[:：-]?\s*", "", clean_text(text), flags=re.I).strip()
    if not title:
        title = clean_text(text).strip()
    if len(title) > 120:
        title = title[:120]
    project = detect_project(text)
    priority = detect_priority(text)
    assigned_to = update.effective_user.id if update.effective_user else None
    reminder = ""
    dt = parse_datetime_text(text)
    if dt != "INVALID":
        reminder = dt
    task_id = create_task(title=title, assigned_to=assigned_to, assigned_by=update.effective_user.id, priority=priority, reminder_time=reminder, project=project, created_at=now_str())
    add_history(task_id, update.effective_user.id, update.effective_user.full_name, "create", "", title)
    add_task_note(task_id, update.effective_user.id, update.effective_user.full_name, f"متن اولیه: {text}", "create", update.message.message_id if update.message else None)
    if quiet:
        await update.message.reply_text("✅ ثبت شد", reply_markup=main_keyboard())
    else:
        await update.message.reply_text("✅ ثبت شد", reply_markup=main_keyboard())
        await show_tasks(update, context)
    return task_id


async def handle_new_task_flow_text(update: Update, context: ContextTypes.DEFAULT_TYPE, flow: Dict[str, Any], text: str) -> bool:
    action = detect_main_action(text)
    if action in {"back", "cancel"}:
        clear_flow(context)
        await update.message.reply_text("✅ برگشت", reply_markup=main_keyboard())
        return True
    # Important: menu buttons must not be consumed as task title.
    if action in MAIN_MENU_ACTIONS and flow.get("step") == "title":
        clear_flow(context)
        await handle_action(update, context, action)
        return True

    step = flow.get("step")
    data = flow.get("data", {})
    if step == "title":
        title = clean_text(text)
        if len(title) < 2:
            await update.message.reply_text("عنوان خیلی کوتاه است. دوباره بنویس:")
            return True
        data["title"] = title[:150]
        flow["step"] = "project"
        flow["data"] = data
        await update.message.reply_text("پروژه کار را انتخاب کن:", reply_markup=project_keyboard())
        return True
    if step == "custom_reminder":
        dt = parse_datetime_text(text)
        if dt == "INVALID":
            await update.message.reply_text("فرمت زمان اشتباه است. مثال: 2026-06-25 18:00 یا فردا 10:00")
            return True
        data["reminder_time"] = dt
        await finish_new_task(update, context, data)
        return True
    return False


async def finish_new_task(update_or_query: Any, context: ContextTypes.DEFAULT_TYPE, data: Dict[str, Any]) -> None:
    user = update_or_query.effective_user if hasattr(update_or_query, "effective_user") else update_or_query.from_user
    title = data.get("title") or "کار جدید"
    project = data.get("project") or "غیره"
    assigned_to = data.get("assigned_to")
    priority = data.get("priority") or "متوسط"
    reminder = data.get("reminder_time") or ""
    task_id = create_task(title=title, assigned_to=assigned_to, assigned_by=user.id, priority=priority, reminder_time=reminder, project=project, created_at=now_str())
    add_history(task_id, user.id, user.full_name, "create", "", title)
    clear_flow(context)
    if hasattr(update_or_query, "message") and update_or_query.message:
        await update_or_query.message.reply_text("✅ ثبت شد", reply_markup=main_keyboard())
    else:
        await update_or_query.message.reply_text("✅ ثبت شد", reply_markup=main_keyboard())


# ------------------------- text router -------------------------

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    await register_user(update)
    text = update.message.text.strip()
    reply_id = update.message.reply_to_message.message_id if update.message.reply_to_message else None
    save_chat_message(update.effective_chat.id, update.effective_user.id, update.effective_user.full_name, update.effective_user.username, text, update.message.message_id, reply_id)

    if context.user_data.get("ai_chat") and norm(text) not in {"خروج", "exit", "لغو", "بازگشت"}:
        await ai_chat_reply(update, context, text)
        return

    action = detect_main_action(text)
    flow = get_flow(context)
    if flow:
        handled = await handle_new_task_flow_text(update, context, flow, text)
        if handled:
            return

    if action:
        await safe_delete_message(update)
        await handle_action(update, context, action)
        return

    if context.user_data.get("waiting_note_task"):
        task_id = context.user_data.pop("waiting_note_task")
        await add_note_to_task(update, int(task_id), text, "manual")
        return

    if context.user_data.get("waiting_check_task"):
        task_id = context.user_data.pop("waiting_check_task")
        add_checklist_item(int(task_id), text, update.effective_user.id)
        add_history(int(task_id), update.effective_user.id, update.effective_user.full_name, "add_checklist", "", text)
        await update.message.reply_text("✅ اضافه شد")
        return

    if context.user_data.get("waiting_custom_reminder_task"):
        task_id = int(context.user_data.pop("waiting_custom_reminder_task"))
        dt = parse_datetime_text(text)
        if dt == "INVALID":
            await update.message.reply_text("فرمت زمان اشتباه است. مثال: 2026-06-25 18:00 یا فردا 10:00")
            return
        update_task_field(task_id, "reminder_time", dt)
        add_history(task_id, update.effective_user.id, update.effective_user.full_name, "reminder", "", dt)
        await update.message.reply_text("✅ تنظیم شد")
        return

    if reply_id:
        task_id = get_task_id_by_message(update.effective_chat.id, reply_id)
        if task_id:
            await add_note_to_task(update, task_id, text, "reply")
            return

    # کار 1: شرح...
    m = re.search(r"کار\s*([0-9۰-۹٠-٩]+)\s*[:：-]\s*(.+)", clean_text(text))
    if m:
        task_id = int(fa_to_en(m.group(1)))
        note = m.group(2).strip()
        await add_note_to_task(update, task_id, note, "pattern")
        return

    # کار 1 انجام شد / منتظر پاسخ / ...
    task_id = extract_task_id(text)
    status = detect_status(text)
    if task_id and status:
        await change_status_and_reply(update, task_id, status)
        return

    if norm(text).startswith(("کار جدید", "ایجاد کار", "ساخت کار", "تسک جدید")):
        await create_task_from_free_text(update, context, text, quiet=True)
        return

    # In groups, normal chatter is only saved for smart analysis. No noisy answer.
    return


async def handle_action(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str) -> None:
    if action == "new_task":
        await start_new_task_flow(update, context)
    elif action == "tasks":
        clear_flow(context)
        await show_tasks(update, context)
    elif action == "smart":
        clear_flow(context)
        await run_smart_manager(update, context)
    elif action == "summary":
        clear_flow(context)
        await update.message.reply_text("بازه تحلیل چت را انتخاب کن:", reply_markup=summary_keyboard())
    elif action == "voice":
        clear_flow(context)
        await update.message.reply_text("ویس بفرست. مثال: لیست کارها، کار شماره یک انجام شد، کار جدید پیگیری مالی")
    elif action == "chatgpt":
        await ai_command(update, context)
    elif action == "reports":
        clear_flow(context)
        await update.message.reply_text("گزارش را انتخاب کن:", reply_markup=reports_keyboard())
    elif action == "members":
        clear_flow(context)
        await members_command(update, context)
    elif action == "profile":
        clear_flow(context)
        await profile_command(update, context)
    elif action == "help":
        clear_flow(context)
        await help_command(update, context)
    elif action in {"back", "cancel"}:
        clear_flow(context)
        await update.message.reply_text("✅ برگشت", reply_markup=main_keyboard())


# ------------------------- callbacks -------------------------

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await register_user(update)
    data = query.data or ""

    if data == "menu:back":
        clear_flow(context)
        await send_or_edit(query, "✅ ربات آماده است", None)
        return

    if data == "flow:cancel":
        clear_flow(context)
        await send_or_edit(query, "✅ لغو شد")
        return

    if data == "flow:new_task":
        clear_flow(context)
        set_flow(context, "new_task", "title", {})
        await query.message.reply_text("عنوان کار را بنویس:", reply_markup=ReplyKeyboardMarkup([[MENU_LABELS["cancel"], MENU_LABELS["back"]]], resize_keyboard=True))
        return

    if data.startswith("new:"):
        await handle_new_task_callback(query, context, data)
        return

    if data == "task:list":
        await show_tasks(query, context)
        return

    if data.startswith("task:open:"):
        task_id = int(data.split(":")[-1])
        await show_task_detail(query, task_id)
        return

    if data.startswith("task:status:"):
        _, _, task_id, status = data.split(":")
        await callback_change_status(query, int(task_id), status)
        return

    if data.startswith("task:notes:"):
        task_id = int(data.split(":")[-1])
        notes = get_task_notes(task_id, 20)
        text = f"📝 شرح‌های کار #{task_id}\n\n"
        text += "شرحی ثبت نشده." if not notes else "\n\n".join(f"{n['created_at']} | {n['full_name']}:\n{n['note']}" for n in notes)
        await query.message.reply_text(text)
        return

    if data.startswith("task:addnote:"):
        task_id = int(data.split(":")[-1])
        context.user_data["waiting_note_task"] = task_id
        await query.message.reply_text("شرح را بنویس:")
        return

    if data.startswith("task:checklist:"):
        await show_checklist(query, int(data.split(":")[-1]))
        return

    if data.startswith("task:addcheck:"):
        task_id = int(data.split(":")[-1])
        context.user_data["waiting_check_task"] = task_id
        await query.message.reply_text("آیتم چک‌لیست را بنویس:")
        return

    if data.startswith("check:toggle:"):
        parts = data.split(":")
        toggle_checklist_item(int(parts[2]))
        await show_checklist(query, int(parts[3]))
        return

    if data.startswith("task:files:"):
        task_id = int(data.split(":")[-1])
        files = get_task_files(task_id)
        text = f"📎 فایل‌های کار #{task_id}\n\n"
        text += "فایلی ثبت نشده. روی پیام منوی همین کار Reply کن و عکس/فایل بفرست." if not files else "\n".join(f"#{f['id']} | {f['created_at']} | {f['file_type']} | {f['caption'] or '-'}" for f in files)
        await query.message.reply_text(text)
        return

    if data.startswith("task:reminder:"):
        task_id = int(data.split(":")[-1])
        await query.message.reply_text("زمان یادآوری را انتخاب کن:", reply_markup=reminder_keyboard(prefix=f"reminder:{task_id}"))
        return

    if data.startswith("reminder:"):
        await handle_reminder_callback(query, context, data)
        return

    if data.startswith("task:history:"):
        task_id = int(data.split(":")[-1])
        rows = get_task_history(task_id, 30)
        text = f"🧾 تاریخچه کار #{task_id}\n\n"
        text += "تاریخچه‌ای ثبت نشده." if not rows else "\n".join(f"{r['created_at']} | {r['full_name']} | {r['action']}: {r['old_value']} → {r['new_value']}" for r in rows)
        await query.message.reply_text(text)
        return

    if data.startswith("task:pin:"):
        task_id = int(data.split(":")[-1])
        task = get_task(task_id)
        if task:
            update_task_field(task_id, "pinned", 0 if int(task.get("pinned") or 0) else 1)
            add_history(task_id, query.from_user.id, query.from_user.full_name, "pin", task.get("pinned"), 0 if int(task.get("pinned") or 0) else 1)
        await show_task_detail(query, task_id)
        return

    if data.startswith("task:delete:"):
        task_id = int(data.split(":")[-1])
        update_task_field(task_id, "deleted", 1)
        update_task_field(task_id, "status", "cancelled")
        add_history(task_id, query.from_user.id, query.from_user.full_name, "delete", "", "deleted")
        await show_tasks(query, context)
        return

    if data.startswith("summary:"):
        await run_summary_query(query, context, data.split(":")[-1])
        return

    if data.startswith("report:"):
        await handle_report_callback(query, context, data.split(":")[-1])
        return

    if data.startswith("ai_comment:"):
        await handle_ai_comment_callback(query, data)
        return


async def handle_new_task_callback(query, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    flow = get_flow(context)
    if not flow or flow.get("name") != "new_task":
        await query.message.reply_text("اول کار جدید را شروع کن.")
        return
    parts = data.split(":")
    kind = parts[1]
    value = ":".join(parts[2:])
    fdata = flow.get("data", {})

    if kind == "project":
        fdata["project"] = value if value in PROJECTS else "غیره"
        flow["step"] = "assignee"
        flow["data"] = fdata
        await query.message.reply_text("مسئول کار را انتخاب کن:", reply_markup=responsible_keyboard())
        return

    if kind == "assignee":
        if value == "self":
            fdata["assigned_to"] = query.from_user.id
        elif value == "0":
            fdata["assigned_to"] = None
        else:
            fdata["assigned_to"] = int(value)
        flow["step"] = "reminder"
        flow["data"] = fdata
        await query.message.reply_text("یادآوری را انتخاب کن:", reply_markup=reminder_keyboard(prefix="new"))
        return

    if kind == "reminder":
        if value == "custom":
            flow["step"] = "custom_reminder"
            flow["data"] = fdata
            await query.message.reply_text("زمان یادآوری را بنویس. مثال: 2026-06-25 18:00 یا فردا 10:00")
            return
        dt = "" if value == "none" else parse_datetime_text(value)
        fdata["reminder_time"] = dt if dt != "INVALID" else ""
        await finish_new_task(query, context, fdata)
        return


async def handle_reminder_callback(query, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    # reminder:<task_id>:reminder:<value>
    parts = data.split(":")
    task_id = int(parts[1])
    value = parts[-1]
    if value == "custom":
        context.user_data["waiting_custom_reminder_task"] = task_id
        await query.message.reply_text("زمان یادآوری را بنویس. مثال: 2026-06-25 18:00 یا فردا 10:00")
        return
    dt = "" if value == "none" else parse_datetime_text(value)
    if dt == "INVALID":
        await query.message.reply_text("فرمت زمان اشتباه است.")
        return
    old = (get_task(task_id) or {}).get("reminder_time", "")
    update_task_field(task_id, "reminder_time", dt)
    add_history(task_id, query.from_user.id, query.from_user.full_name, "reminder", old, dt)
    await query.message.reply_text("✅ تنظیم شد")
    await show_task_detail(query, task_id)


async def callback_change_status(query, task_id: int, status: str) -> None:
    task = get_task(task_id)
    if not task:
        await query.message.reply_text("کار پیدا نشد.")
        return
    old = task.get("status")
    update_task_field(task_id, "status", status)
    if status == "done":
        update_task_field(task_id, "completed_at", now_str())
    add_history(task_id, query.from_user.id, query.from_user.full_name, "status", old, status)
    add_task_note(task_id, query.from_user.id, query.from_user.full_name, f"وضعیت تغییر کرد: {STATUS_TEXT.get(old, old)} → {STATUS_TEXT.get(status, status)}", "button")
    await show_task_detail(query, task_id)


async def show_checklist(query, task_id: int) -> None:
    rows = get_checklist(task_id)
    text = f"☑️ چک‌لیست کار #{task_id}\n\n"
    buttons = []
    if not rows:
        text += "آیتمی ثبت نشده."
    for item in rows:
        mark = "✅" if int(item.get("is_done") or 0) else "☐"
        text += f"{mark} #{item['id']} {item['item_text']}\n"
        buttons.append([InlineKeyboardButton(f"تغییر #{item['id']}", callback_data=f"check:toggle:{item['id']}:{task_id}")])
    buttons.append([InlineKeyboardButton("➕ چک‌لیست", callback_data=f"task:addcheck:{task_id}"), InlineKeyboardButton("🔙 کار", callback_data=f"task:open:{task_id}")])
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))


# ------------------------- task operations -------------------------

async def add_note_to_task(update: Update, task_id: int, note: str, source: str) -> None:
    task = get_task(task_id)
    if not task:
        await update.message.reply_text("کار پیدا نشد.")
        return
    add_task_note(task_id, update.effective_user.id, update.effective_user.full_name, note, source, update.message.message_id)
    add_history(task_id, update.effective_user.id, update.effective_user.full_name, "add_note", "", note)
    status = detect_status(note)
    if status:
        old = task.get("status")
        update_task_field(task_id, "status", status)
        if status == "done":
            update_task_field(task_id, "completed_at", now_str())
        add_history(task_id, update.effective_user.id, update.effective_user.full_name, "auto_status", old, status)
    await update.message.reply_text("✅ ثبت شد")


async def change_status_and_reply(update: Update, task_id: int, status: str) -> None:
    task = get_task(task_id)
    if not task:
        await update.message.reply_text("کار پیدا نشد.")
        return
    old = task.get("status")
    update_task_field(task_id, "status", status)
    if status == "done":
        update_task_field(task_id, "completed_at", now_str())
    add_history(task_id, update.effective_user.id, update.effective_user.full_name, "status", old, status)
    await update.message.reply_text("✅ انجام شد" if status == "done" else "✅ تغییر کرد")


# ------------------------- file router -------------------------

async def file_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await register_user(update)
    caption = update.message.caption or ""
    reply_id = update.message.reply_to_message.message_id if update.message.reply_to_message else None
    task_id = None
    if reply_id:
        task_id = get_task_id_by_message(update.effective_chat.id, reply_id)
    if not task_id:
        task_id = extract_task_id(caption)
    if not task_id or not get_task(task_id):
        await update.message.reply_text("برای ذخیره فایل، روی پیام منوی کار Reply کن یا در کپشن بنویس: کار 1")
        return
    file_id, file_type = None, "file"
    if update.message.document:
        file_id = update.message.document.file_id
        file_type = "document"
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_type = "photo"
    elif update.message.video:
        file_id = update.message.video.file_id
        file_type = "video"
    elif update.message.audio:
        file_id = update.message.audio.file_id
        file_type = "audio"
    if not file_id:
        return
    add_task_file(task_id, update.effective_user.id, update.effective_user.full_name, file_id, file_type, caption)
    add_history(task_id, update.effective_user.id, update.effective_user.full_name, "add_file", "", file_type)
    if caption:
        add_task_note(task_id, update.effective_user.id, update.effective_user.full_name, caption, "file_caption", update.message.message_id)
    await update.message.reply_text("✅ فایل ثبت شد")


# ------------------------- voice -------------------------

async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    if not client:
        await update.message.reply_text("OPENAI_API_KEY تنظیم نشده است.")
        return
    try:
        file = await context.bot.get_file(update.message.voice.file_id)
        path = tempfile.mktemp(suffix=".ogg")
        await file.download_to_drive(path)

        def transcribe():
            with open(path, "rb") as f:
                return client.audio.transcriptions.create(model=TRANSCRIBE_MODEL, file=f, language="fa")

        result = await asyncio.to_thread(transcribe)
        text = clean_text(result.text)
    except Exception as e:
        await update.message.reply_text(f"خطای تبدیل ویس: {e}")
        return

    await update.message.reply_text(f"📝 متن ویس:\n{text}")
    await handle_voice_text(update, context, text)


async def handle_voice_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    action = detect_main_action(text)
    if action and action != "voice":
        await handle_action(update, context, action)
        return
    task_id = extract_task_id(text)
    status = detect_status(text)
    if task_id and status:
        await change_status_and_reply(update, task_id, status)
        return
    if norm(text).startswith(("کار جدید", "ایجاد کار", "ساخت کار")) or "کار جدید" in norm(text):
        await create_task_from_free_text(update, context, text, quiet=True)
        return
    if task_id:
        note = re.sub(r"کار\s*(?:\d+|یک|دو|سه|چهار|پنج|شش|هفت|هشت|نه|ده)\s*[:：-]?", "", text).strip()
        if note:
            await add_note_to_task(update, task_id, note, "voice")
            return
    await update.message.reply_text("فرمان واضح نبود. مثال: لیست کارها، کار شماره یک انجام شد، کار جدید پیگیری مالی")


# ------------------------- OpenAI / smart manager -------------------------

async def ai_chat_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if not client:
        await update.message.reply_text("OPENAI_API_KEY تنظیم نشده است.")
        return

    def call_ai():
        return client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "تو دستیار مدیریتی فارسی برای تیم هستی. پاسخ کوتاه، کاربردی و دقیق بده."},
                {"role": "user", "content": text},
            ],
        )

    try:
        result = await asyncio.to_thread(call_ai)
        await update.message.reply_text(result.choices[0].message.content[:3900])
    except Exception as e:
        await update.message.reply_text(f"خطای چت جی‌پی‌تی: {e}")


def extract_json(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text or "", re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {}


async def run_summary(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str) -> None:
    if not client:
        await update.message.reply_text("OPENAI_API_KEY تنظیم نشده است.")
        return
    now = datetime.now()
    if mode == "2h":
        start = now - timedelta(hours=2)
    elif mode == "yesterday":
        y = now - timedelta(days=1)
        start = y.replace(hour=0, minute=0, second=0)
        now = y.replace(hour=23, minute=59, second=59)
    elif mode == "7d":
        start = now - timedelta(days=7)
    else:
        start = now - timedelta(hours=1)
    rows = get_chat_messages_between(update.effective_chat.id, start.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S"))
    if not rows:
        await update.message.reply_text("در این بازه پیامی برای تحلیل پیدا نشد.")
        return
    await update.message.reply_text("🧠 در حال تحلیل چت...")
    answer = await summarize_rows(rows)
    await update.message.reply_text(answer[:3900])


async def run_summary_query(query, context: ContextTypes.DEFAULT_TYPE, mode: str) -> None:
    fake_update = type("FakeUpdate", (), {})()
    fake_update.effective_chat = query.message.chat
    fake_update.message = query.message
    await query.message.reply_text("🧠 در حال تحلیل چت...")
    now = datetime.now()
    if mode == "2h":
        start = now - timedelta(hours=2)
    elif mode == "yesterday":
        y = now - timedelta(days=1)
        start = y.replace(hour=0, minute=0, second=0)
        now = y.replace(hour=23, minute=59, second=59)
    elif mode == "7d":
        start = now - timedelta(days=7)
    else:
        start = now - timedelta(hours=1)
    rows = get_chat_messages_between(query.message.chat_id, start.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S"))
    if not rows:
        await query.message.reply_text("در این بازه پیامی برای تحلیل پیدا نشد.")
        return
    answer = await summarize_rows(rows)
    await query.message.reply_text(answer[:3900])


async def summarize_rows(rows: List[Dict[str, Any]]) -> str:
    history = "\n".join(f"{r['created_at']} | {r['full_name']}: {r['text']}" for r in rows[-100:])
    prompt = f"""
چت زیر را برای مدیریت تیم تحلیل کن.
خروجی فارسی و ساختاریافته بده:
1) خلاصه
2) کارهای قابل پیگیری
3) تصمیم‌ها
4) ریسک‌ها
5) اشتباهات برنامه‌ریزی
6) پیشنهادهای عملی

چت:
{history}
"""

    def call_ai():
        return client.chat.completions.create(model=OPENAI_MODEL, messages=[{"role": "user", "content": prompt}])

    result = await asyncio.to_thread(call_ai)
    return result.choices[0].message.content


async def run_smart_manager(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not client:
        await update.message.reply_text("OPENAI_API_KEY تنظیم نشده است.")
        return
    await update.message.reply_text("🧠 در حال بررسی چت و کارها...")
    chat_id = update.effective_chat.id
    messages = get_recent_chat_messages(chat_id, 80)
    tasks = get_open_tasks(80)
    if not messages:
        await update.message.reply_text("پیامی برای تحلیل پیدا نشد.")
        return
    messages_text = "\n".join(f"{m['created_at']} | {m['full_name']}: {m['text']}" for m in messages[-80:])
    tasks_text = "\n".join(f"#{t['id']} | {t['title']} | {t['project']} | {t['status']} | {t['priority']} | مسئول:{user_display(t.get('assigned_to'))}" for t in tasks)
    prompt = f"""
تو مدیر هوشمند ربات SAM PRO هستی. پیام‌های گروه و کارهای باز را بررسی کن.
قوانین:
- اگر از چت معلوم شد کاری انجام شده، change_status بده.
- اگر توضیحی مثل «زنگ زدم جواب ندادند» مربوط به کاری است، add_note بده.
- اگر کار جدید لازم است، create_task بده.
- اگر فقط مشورت مدیریتی یا ایراد برنامه‌ریزی داری، در management_comment بنویس.
- فقط JSON معتبر بده.
- confidence از 0 تا 1 باشد؛ فقط موارد واقعاً قابل دفاع را پیشنهاد بده.

فرمت JSON:
{{
  "actions": [
    {{"type":"create_task", "title":"", "project":"تخته|میوه|پتروشیمی|مالی|غلات|غیره", "assigned_to_name":"", "priority":"فوری|زیاد|متوسط|کم", "reminder_time":"", "reason":"", "confidence":0.0}},
    {{"type":"add_note", "task_id":1, "note":"", "reason":"", "confidence":0.0}},
    {{"type":"change_status", "task_id":1, "status":"done|waiting|in_progress|cancelled", "reason":"", "confidence":0.0}}
  ],
  "management_comment":""
}}

کارهای باز:
{tasks_text or 'کار بازی ثبت نشده'}

پیام‌های اخیر:
{messages_text}
"""

    def call_ai():
        return client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "فقط JSON معتبر خروجی بده."},
                {"role": "user", "content": prompt},
            ],
        )

    try:
        result = await asyncio.to_thread(call_ai)
        data = extract_json(result.choices[0].message.content)
    except Exception as e:
        await update.message.reply_text(f"خطای مدیر هوشمند: {e}")
        return

    applied = {"new": 0, "note": 0, "status": 0}
    for action in data.get("actions", [])[:12]:
        try:
            confidence = float(action.get("confidence") or 0)
        except Exception:
            confidence = 0
        if confidence < 0.55:
            continue
        atype = action.get("type")
        if atype == "create_task":
            title = clean_text(action.get("title") or "")
            if len(title) < 2:
                continue
            project = action.get("project") if action.get("project") in PROJECTS else detect_project(title)
            priority = action.get("priority") if action.get("priority") in PRIORITIES else detect_priority(title)
            assignee = find_user_by_text(action.get("assigned_to_name") or "")
            if assignee == -1:
                assignee = update.effective_user.id
            task_id = create_task(title=title, project=project, priority=priority, assigned_to=assignee, assigned_by=update.effective_user.id, created_at=now_str(), description=f"ساخته شده توسط مدیر هوشمند. دلیل: {action.get('reason','')}")
            add_history(task_id, update.effective_user.id, update.effective_user.full_name, "ai_create", "", title)
            applied["new"] += 1
        elif atype == "add_note":
            task_id = int(action.get("task_id") or 0)
            note = clean_text(action.get("note") or "")
            if task_id and note and get_task(task_id):
                add_task_note(task_id, update.effective_user.id, update.effective_user.full_name, note, "ai")
                add_history(task_id, update.effective_user.id, update.effective_user.full_name, "ai_note", "", note)
                applied["note"] += 1
        elif atype == "change_status":
            task_id = int(action.get("task_id") or 0)
            status = action.get("status")
            if status in STATUS_TEXT and get_task(task_id):
                old = get_task(task_id).get("status")
                update_task_field(task_id, "status", status)
                if status == "done":
                    update_task_field(task_id, "completed_at", now_str())
                add_history(task_id, update.effective_user.id, update.effective_user.full_name, "ai_status", old, status)
                applied["status"] += 1

    comment = clean_text(data.get("management_comment") or "")
    text = f"✅ تحلیل تمام شد\n\nاعمال‌شده‌ها:\nکار جدید: {applied['new']}\nشرح اضافه‌شده: {applied['note']}\nتغییر وضعیت: {applied['status']}"
    if comment:
        sid = save_ai_suggestion(chat_id, "management_comment", note=comment, confidence=1, raw_json=json.dumps(data, ensure_ascii=False))
        await update.message.reply_text(text + "\n\n🧠 کامنت مدیریتی دارم. اجازه می‌دهی نشان بدهم؟", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("نمایش کامنت", callback_data=f"ai_comment:show:{sid}"), InlineKeyboardButton("رد", callback_data=f"ai_comment:reject:{sid}")]]))
    else:
        await update.message.reply_text(text)


async def handle_ai_comment_callback(query, data: str) -> None:
    _, action, sid = data.split(":")
    row = get_ai_suggestion(int(sid))
    if not row:
        await query.message.reply_text("کامنت پیدا نشد.")
        return
    if action == "reject":
        update_ai_suggestion_status(int(sid), "rejected", query.from_user.id)
        await query.edit_message_text("✅ رد شد")
        return
    update_ai_suggestion_status(int(sid), "shown", query.from_user.id)
    await query.edit_message_text("🧠 کامنت مدیریتی:\n\n" + (row.get("note") or "-"))


# ------------------------- reports -------------------------

def build_stats_text() -> str:
    tasks = get_tasks(include_done=True, include_deleted=False, limit=10000)
    total = len(tasks)
    done = len([t for t in tasks if t.get("status") == "done"])
    open_count = len([t for t in tasks if t.get("status") not in {"done", "cancelled"}])
    waiting = len([t for t in tasks if t.get("status") == "waiting"])
    cancelled = len([t for t in tasks if t.get("status") == "cancelled"])
    return f"📊 آمار\n\nکل کارها: {total}\nباز: {open_count}\nمنتظر پاسخ: {waiting}\nانجام‌شده: {done}\nلغو: {cancelled}"


def build_daily_report() -> str:
    tasks = get_tasks(include_done=True, include_deleted=False, limit=10000)
    today = datetime.now().strftime("%Y-%m-%d")
    created_today = len([t for t in tasks if (t.get("created_at") or "").startswith(today)])
    done_today = len([t for t in tasks if (t.get("completed_at") or "").startswith(today)])
    open_count = len([t for t in tasks if t.get("status") not in {"done", "cancelled"}])
    urgent = len([t for t in tasks if t.get("priority") in {"فوری", "زیاد"} and t.get("status") not in {"done", "cancelled"}])
    projects: Dict[str, int] = {}
    for t in tasks:
        if t.get("status") not in {"done", "cancelled"}:
            projects[t.get("project") or "غیره"] = projects.get(t.get("project") or "غیره", 0) + 1
    ptxt = "\n".join(f"• {p}: {c}" for p, c in projects.items()) or "موردی ثبت نشده"
    return f"📊 گزارش روزانه SAM\n\n📅 تاریخ: {today}\n\n📋 کارهای باز: {open_count}\n🔥 کارهای فوری/زیاد: {urgent}\n✅ انجام‌شده امروز: {done_today}\n🆕 ثبت‌شده امروز: {created_today}\n\n🏗 وضعیت پروژه‌ها:\n{ptxt}"


def build_weekly_report() -> str:
    tasks = get_tasks(include_done=True, include_deleted=False, limit=10000)
    since = datetime.now() - timedelta(days=7)
    week = []
    for t in tasks:
        try:
            if datetime.strptime((t.get("created_at") or "")[:19], "%Y-%m-%d %H:%M:%S") >= since:
                week.append(t)
        except Exception:
            pass
    return f"📈 گزارش هفتگی\n\nکارهای جدید هفته: {len(week)}\nانجام‌شده‌ها: {len([t for t in week if t.get('status') == 'done'])}\nباز: {len([t for t in week if t.get('status') not in {'done','cancelled'}])}"


def build_excel_report() -> str:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tasks"
    ws.append(["ID", "Title", "Project", "Status", "Priority", "Assignee", "Reminder", "Created", "Completed", "Description"])
    for t in get_tasks(include_done=True, include_deleted=False, limit=10000):
        ws.append([t.get("id"), t.get("title"), t.get("project"), STATUS_TEXT.get(t.get("status"), t.get("status")), t.get("priority"), user_display(t.get("assigned_to")), t.get("reminder_time"), t.get("created_at"), t.get("completed_at"), t.get("description")])
    for col in ws.columns:
        try:
            ws.column_dimensions[col[0].column_letter].width = min(max(len(str(c.value or "")) for c in col) + 2, 50)
        except Exception:
            pass
    path = tempfile.mktemp(suffix=".xlsx")
    wb.save(path)
    return path


def build_text_report() -> str:
    path = tempfile.mktemp(suffix=".txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_daily_report())
        f.write("\n\n")
        for t in get_tasks(include_done=True, include_deleted=False, limit=200):
            f.write(f"#{t['id']} | {t['title']} | {t['project']} | {t['status']} | {t['priority']}\n")
    return path


async def handle_report_callback(query, context: ContextTypes.DEFAULT_TYPE, kind: str) -> None:
    if kind == "stats":
        await query.message.reply_text(build_stats_text())
    elif kind == "daily":
        await query.message.reply_text(build_daily_report())
    elif kind == "weekly":
        await query.message.reply_text(build_weekly_report())
    elif kind == "excel":
        try:
            path = build_excel_report()
            await query.message.reply_document(InputFile(path), filename="sam_tasks_report.xlsx")
        except Exception as e:
            await query.message.reply_text(f"خطای خروجی اکسل: {e}")
    elif kind == "pdf":
        path = build_text_report()
        await query.message.reply_document(InputFile(path), filename="sam_tasks_report.txt")


# ------------------------- jobs -------------------------

async def check_due_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now()
    for task in get_open_tasks(500):
        dt = parse_dt(task.get("reminder_time") or "")
        if not dt or dt > now:
            continue
        text = f"⏰ یادآوری کار #{task['id']}\n\n📌 {task['title']}\n🗂 {task.get('project') or 'غیره'}\n{status_label(task.get('status'))}"
        targets = []
        if task.get("assigned_to"):
            targets.append(int(task["assigned_to"]))
        if GROUP_CHAT_ID:
            targets.append(GROUP_CHAT_ID)
        for chat_id in set(targets):
            try:
                msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=task_menu_keyboard(task["id"]))
                link_task_message(chat_id, msg.message_id, task["id"])
            except Exception as e:
                print(f"reminder send error: {e}")
        add_history(task["id"], 0, "BOT", "reminder_sent", "", task.get("reminder_time"))
        repeat = task.get("reminder_repeat") or "none"
        if repeat == "daily":
            update_task_field(task["id"], "reminder_time", (dt + timedelta(days=1)).strftime("%Y-%m-%d %H:%M"))
        elif repeat == "weekly":
            update_task_field(task["id"], "reminder_time", (dt + timedelta(days=7)).strftime("%Y-%m-%d %H:%M"))
        else:
            update_task_field(task["id"], "reminder_time", "")


async def three_hour_followup(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not GROUP_CHAT_ID:
        return
    tasks = get_open_tasks(100)
    text = "⏱ پیگیری سه‌ساعته\n\nروی هر کار بزن تا منوی همان کار باز شود."
    if not tasks:
        text += "\n\n✅ کار بازی وجود ندارد."
    try:
        await context.bot.send_message(GROUP_CHAT_ID, text=text, reply_markup=tasks_list_keyboard(tasks))
    except Exception as e:
        print(f"followup error: {e}")


async def daily_report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    targets = set(get_admin_ids())
    if GROUP_CHAT_ID:
        targets.add(GROUP_CHAT_ID)
    for chat_id in targets:
        try:
            await context.bot.send_message(chat_id, build_daily_report())
        except Exception as e:
            print(f"daily report error: {e}")


# ------------------------- app -------------------------

def build_app() -> Application:
    init_db()
    app = Application.builder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("chatid", chatid_command))
    app.add_handler(CommandHandler("tasks", tasks_command))
    app.add_handler(CommandHandler("newtask", newtask_command))
    app.add_handler(CommandHandler("new", newtask_command))
    app.add_handler(CommandHandler("done", done_command))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(CommandHandler("smart", smart_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("dailyreport", daily_report_command))
    app.add_handler(CommandHandler("weeklyreport", weekly_report_command))
    app.add_handler(CommandHandler("members", members_command))
    app.add_handler(CommandHandler("whoami", profile_command))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("export_excel", export_excel_command))
    app.add_handler(CommandHandler("export_pdf", export_pdf_command))
    app.add_handler(CommandHandler("ai", ai_command))
    app.add_handler(CommandHandler("exit", exit_command))

    # Callbacks and messages
    app.add_handler(CallbackQueryHandler(callback_router), group=0)
    app.add_handler(MessageHandler(filters.VOICE, voice_handler), group=0)
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO, file_router), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router), group=1)

    # Jobs
    app.job_queue.run_repeating(check_due_reminders, interval=300, first=30)
    app.job_queue.run_repeating(three_hour_followup, interval=3 * 60 * 60, first=3 * 60 * 60)
    app.job_queue.run_daily(daily_report_job, time=time(hour=21, minute=0), name="daily_report")
    return app


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")
    print("BOT_TOKEN loaded:", bool(TOKEN), TOKEN[-6:] if TOKEN else "NO TOKEN")
    print("OPENAI_MODEL:", OPENAI_MODEL)
    app = build_app()
    print("SAM PRO Team Manager V7 Clean Started...")
    app.run_polling(drop_pending_updates=True)
