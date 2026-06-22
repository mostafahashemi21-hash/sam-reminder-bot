import html
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, time
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from database import (
    init_db,
    add_user,
    get_user,
    get_users,
    count_admins,
    get_admin_ids,
    create_task,
    get_task,
    get_tasks,
    get_open_tasks,
    complete_task,
    update_task_field,
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
GROUP_CHAT_ID_RAW = os.getenv("GROUP_CHAT_ID")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

GROUP_CHAT_ID: Optional[int] = None
if GROUP_CHAT_ID_RAW:
    try:
        GROUP_CHAT_ID = int(GROUP_CHAT_ID_RAW)
    except Exception:
        GROUP_CHAT_ID = None

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

PROJECTS = ["تخته", "میوه", "پتروشیمی", "مالی", "غلات", "غیره"]
PRIORITIES = ["فوری", "زیاد", "متوسط", "کم"]
STATUSES = {
    "pending": "باز",
    "in_progress": "در حال پیگیری",
    "waiting": "منتظر پاسخ",
    "done": "انجام شد",
    "cancelled": "لغو شد",
}
STATUS_EMOJI = {
    "pending": "📋",
    "in_progress": "🔄",
    "waiting": "⏳",
    "done": "✅",
    "cancelled": "⛔",
}
USER_STATE: Dict[int, str] = {}

# ------------------------- normalizers -------------------------

def normalize_text(text: str) -> str:
    text = text or ""
    text = text.replace("ي", "ی").replace("ك", "ک")
    return text.strip()


def fa_to_en_digits(text: str) -> str:
    text = text or ""
    fa = "۰۱۲۳۴۵۶۷۸۹"
    ar = "٠١٢٣٤٥٦٧٨٩"
    en = "0123456789"
    for i in range(10):
        text = text.replace(fa[i], en[i]).replace(ar[i], en[i])
    return text


def clean_command_arg_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    if context.args:
        return " ".join(context.args).strip()
    if update.message and update.message.text:
        parts = update.message.text.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""
    return ""


def extract_task_id(text: str) -> Optional[int]:
    text = fa_to_en_digits(normalize_text(text))
    # Prefer explicit task indicators, so random numbers do not trigger task actions.
    m = re.search(r"(?:کار|task|#)\s*(\d+)", text, flags=re.I)
    if m:
        return int(m.group(1))
    if re.fullmatch(r"\d+", text.strip()):
        return int(text.strip())
    return None


def detect_project(text: str) -> str:
    text = normalize_text(text)
    table = {
        "تخته": ["تخته", "چوب", "روسیه", "الوار", "MDF", "mdf", "نجاری"],
        "میوه": ["میوه", "سیب", "پرتقال", "نارنگی", "نکتارین", "کاهو", "سبزی", "صادرات میوه"],
        "پتروشیمی": ["پتروشیمی", "شیمی", "مواد", "sibur", "SIBUR", "پلیمر", "لاستیک", "گاز"],
        "مالی": ["مالی", "پول", "پرداخت", "حساب", "بانک", "فاکتور", "invoice", "واریز", "دلار", "روبل"],
        "غلات": ["غلات", "گندم", "جو", "ذرت", "نهاده", "کنجاله", "حبوبات", "نخود"],
    }
    for project, keywords in table.items():
        if any(k.lower() in text.lower() for k in keywords):
            return project
    return "غیره"


def detect_priority(text: str) -> str:
    text = normalize_text(text)
    if any(w in text for w in ["فوری", "ضروری", "اورژانسی", "خیلی مهم", "🔥", "قرمز"]):
        return "فوری"
    if any(w in text for w in ["مهم", "بالا", "زیاد", "اولویت بالا", "🔴"]):
        return "زیاد"
    if any(w in text for w in ["کم", "پایین", "بعدا", "بعداً", "سبز"]):
        return "کم"
    return "متوسط"


def detect_status_from_text(text: str) -> Optional[str]:
    text = normalize_text(text)
    if any(w in text for w in ["انجام شد", "انجام دادم", "فرستادم", "ارسال شد", "تمام شد", "حل شد", "اوکی شد", "تکمیل شد"]):
        return "done"
    if any(w in text for w in ["جواب نداد", "زنگ زدم جواب نداد", "منتظر", "منتظر پاسخ", "خبر بده", "پاسخ بده", "بعدا جواب", "بعداً جواب"]):
        return "waiting"
    if any(w in text for w in ["پیگیری", "در حال", "شروع کردم", "دارم انجام", "در دست اقدام"]):
        return "in_progress"
    if any(w in text for w in ["لغو", "کنسل", "حذف", "بیخیال"]):
        return "cancelled"
    return None


def parse_datetime_text(text: str) -> Tuple[Optional[str], str]:
    """Return (YYYY-MM-DD HH:MM, repeat). None means no valid datetime."""
    raw = normalize_text(fa_to_en_digits(text))
    if not raw or any(w in raw.lower() for w in ["none", "بدون", "ندارد"]):
        return "none", "none"

    repeat = "none"
    if any(w in raw.lower() for w in ["daily", "هر روز", "روزانه"]):
        repeat = "daily"
    elif any(w in raw.lower() for w in ["weekly", "هر هفته", "هفتگی"]):
        repeat = "weekly"

    cleaned = raw
    for w in ["daily", "weekly", "هر روز", "روزانه", "هر هفته", "هفتگی"]:
        cleaned = cleaned.replace(w, "")
    cleaned = cleaned.strip()
    now = datetime.now()

    hm = re.search(r"(\d{1,2})[:.](\d{2})", cleaned)
    hour = int(hm.group(1)) if hm else 10
    minute = int(hm.group(2)) if hm else 0

    if "پس فردا" in cleaned:
        dt = now + timedelta(days=2)
        return dt.replace(hour=hour, minute=minute, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M"), repeat
    if "فردا" in cleaned:
        dt = now + timedelta(days=1)
        return dt.replace(hour=hour, minute=minute, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M"), repeat
    if "امروز" in cleaned:
        dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return dt.strftime("%Y-%m-%d %H:%M"), repeat

    # ISO-like formats.
    for fmt in ["%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M"]:
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.strftime("%Y-%m-%d %H:%M"), repeat
        except Exception:
            pass

    return None, repeat


def parse_db_dt(value: Optional[str]) -> Optional[datetime]:
    if not value or value == "none":
        return None
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"]:
        try:
            return datetime.strptime(value, fmt)
        except Exception:
            pass
    return None


def is_back(text: str) -> bool:
    text = normalize_text(text).lower()
    return text in ["بازگشت", "🔙 بازگشت", "⬅️بازگشت", "لغو", "cancel", "/exit", "exit", "خروج"]


def trim(text: str, n: int = 80) -> str:
    text = normalize_text(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def user_name(update: Update) -> str:
    return update.effective_user.full_name if update.effective_user else "کاربر"


def user_id(update: Update) -> Optional[int]:
    return update.effective_user.id if update.effective_user else None

# ------------------------- keyboards -------------------------

def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["➕ کار جدید", "📋 کارها"],
            ["🧠 مدیر هوشمند", "🧠 تحلیل چت"],
            ["🎙 فرمان صوتی", "📊 گزارش‌ها"],
            ["👥 اعضا", "👤 پروفایل"],
            ["❓ راهنما"],
        ],
        resize_keyboard=True,
    )


def back_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["🔙 بازگشت"]], resize_keyboard=True)


def project_keyboard(prefix: str = "new_project") -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(PROJECTS), 2):
        row = []
        for p in PROJECTS[i:i + 2]:
            row.append(InlineKeyboardButton(p, callback_data=f"{prefix}:{p}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="main:back")])
    return InlineKeyboardMarkup(rows)


def task_list_keyboard(tasks: List[Dict[str, Any]], page: int = 0, per_page: int = 12) -> InlineKeyboardMarkup:
    start = page * per_page
    selected = tasks[start:start + per_page]
    rows = []
    for t in selected:
        label = f"#{t['id']} | {STATUS_EMOJI.get(t['status'], '')} {trim(t['title'], 42)}"
        rows.append([InlineKeyboardButton(label, callback_data=f"task:open:{t['id']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"tasks:page:{page-1}"))
    if start + per_page < len(tasks):
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"tasks:page:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("➕ کار جدید", callback_data="new:start")])
    return InlineKeyboardMarkup(rows)


def task_menu_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ انجام شد", callback_data=f"task:status:{task_id}:done"),
            InlineKeyboardButton("🔄 پیگیری", callback_data=f"task:status:{task_id}:in_progress"),
        ],
        [
            InlineKeyboardButton("⏳ منتظر پاسخ", callback_data=f"task:status:{task_id}:waiting"),
            InlineKeyboardButton("⛔ لغو", callback_data=f"task:status:{task_id}:cancelled"),
        ],
        [
            InlineKeyboardButton("📝 شرح‌ها", callback_data=f"task:notes:{task_id}"),
            InlineKeyboardButton("➕ شرح", callback_data=f"task:add_note:{task_id}"),
        ],
        [
            InlineKeyboardButton("☑️ چک‌لیست", callback_data=f"task:checklist:{task_id}"),
            InlineKeyboardButton("➕ چک‌لیست", callback_data=f"task:add_check:{task_id}"),
        ],
        [
            InlineKeyboardButton("📎 فایل‌ها", callback_data=f"task:files:{task_id}"),
            InlineKeyboardButton("⏰ یادآوری", callback_data=f"task:remind:{task_id}"),
        ],
        [
            InlineKeyboardButton("🧾 تاریخچه", callback_data=f"task:history:{task_id}"),
            InlineKeyboardButton("📌 پین", callback_data=f"task:pin:{task_id}"),
        ],
        [
            InlineKeyboardButton("🗑 حذف", callback_data=f"task:delete:{task_id}"),
            InlineKeyboardButton("🔙 لیست کارها", callback_data="tasks:page:0"),
        ],
    ])


def summary_range_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("۱ ساعت اخیر", callback_data="summary:1h"), InlineKeyboardButton("۲ ساعت اخیر", callback_data="summary:2h")],
        [InlineKeyboardButton("دیروز", callback_data="summary:yesterday"), InlineKeyboardButton("۷ روز اخیر", callback_data="summary:7d")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="main:back")],
    ])


def reports_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["📊 آمار", "📅 گزارش روزانه"], ["📈 گزارش هفتگی", "📥 خروجی اکسل"], ["📄 خروجی PDF", "🔙 بازگشت"]],
        resize_keyboard=True,
    )

# ------------------------- formatters -------------------------

def task_summary_label(task: Dict[str, Any]) -> str:
    pin = "📌 " if task.get("pinned") else ""
    status = STATUSES.get(task.get("status"), task.get("status"))
    return f"{pin}#{task['id']} | {status} | {task.get('project') or 'غیره'} | {trim(task['title'], 60)}"


def task_menu_text(task: Dict[str, Any]) -> str:
    assigned = "نامشخص"
    if task.get("assigned_to"):
        u = get_user(int(task["assigned_to"]))
        assigned = u.get("full_name") if u else str(task.get("assigned_to"))
    reminder = task.get("reminder_time") or "none"
    if reminder == "none":
        reminder = "ندارد"
    title = html.escape(task.get("title") or "")
    desc = html.escape(task.get("description") or "-")
    return (
        f"📂 <b>کار #{task['id']}</b>\n\n"
        f"📌 <b>عنوان:</b>\n{title}\n\n"
        f"📁 <b>پروژه:</b> {html.escape(task.get('project') or 'غیره')}\n"
        f"📍 <b>وضعیت:</b> {STATUSES.get(task.get('status'), task.get('status'))}\n"
        f"🔥 <b>اولویت:</b> {html.escape(task.get('priority') or 'متوسط')}\n"
        f"👤 <b>مسئول:</b> {html.escape(assigned or 'نامشخص')}\n"
        f"⏰ <b>یادآوری:</b> {html.escape(reminder)}\n\n"
        f"📝 <b>توضیح:</b>\n{desc}"
    )


def task_list_text(tasks: List[Dict[str, Any]]) -> str:
    if not tasks:
        return "📋 کارهای باز\n\n✅ کار بازی وجود ندارد."
    return "📋 کارهای باز\n\nروی هر کار بزن تا منوی همان کار باز شود."

# ------------------------- common actions -------------------------

async def register_user(update: Update) -> None:
    if not update.effective_user:
        return
    role = "admin" if count_admins() == 0 else "member"
    add_user(update.effective_user.id, update.effective_user.username or "", update.effective_user.full_name or "", role, now_str())


def clear_user_states(context: ContextTypes.DEFAULT_TYPE, uid: Optional[int] = None) -> None:
    context.user_data.clear()
    if uid is not None:
        USER_STATE.pop(uid, None)


async def show_task_list(update_or_query: Any, context: ContextTypes.DEFAULT_TYPE, page: int = 0, edit: bool = False) -> None:
    tasks = get_open_tasks(200)
    text = task_list_text(tasks)
    keyboard = task_list_keyboard(tasks, page=page)
    if hasattr(update_or_query, "edit_message_text"):
        await update_or_query.edit_message_text(text, reply_markup=keyboard)
        return
    msg = await update_or_query.message.reply_text(text, reply_markup=keyboard)
    # List message is not linked to a single task. Individual task menus will be linked.


async def open_task_menu(query, task_id: int) -> None:
    task = get_task(task_id)
    if not task or task.get("deleted"):
        await query.edit_message_text("❌ کار پیدا نشد یا حذف شده است.")
        return
    await query.edit_message_text(task_menu_text(task), reply_markup=task_menu_keyboard(task_id), parse_mode="HTML")
    try:
        link_task_message(query.message.chat_id, query.message.message_id, task_id)
    except Exception:
        pass


async def create_task_silent(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str, project: Optional[str] = None) -> Optional[int]:
    title = normalize_text(title)
    if not title:
        await update.message.reply_text("عنوان کار خالی است.")
        return None
    project = project or detect_project(title)
    priority = detect_priority(title)
    assigned_to = user_id(update)
    task_id = create_task(
        title=title,
        assigned_to=assigned_to,
        assigned_by=user_id(update),
        priority=priority,
        reminder_time="none",
        created_at=now_str(),
        description="",
        project=project,
        tag="",
    )
    add_history(task_id, user_id(update), user_name(update), "create", "", title)
    await update.message.reply_text("✅ ثبت شد")
    return task_id


async def set_status_and_reply(update: Update, task_id: int, status: str, quiet: bool = False) -> None:
    task = get_task(task_id)
    if not task:
        if not quiet:
            await update.message.reply_text("❌ کار پیدا نشد.")
        return
    old = task.get("status")
    update_task_field(task_id, "status", status)
    if status == "done":
        update_task_field(task_id, "completed_at", now_str())
    add_history(task_id, user_id(update), user_name(update), "status", old, status)
    add_task_note(task_id, user_id(update), user_name(update), f"وضعیت تغییر کرد: {STATUSES.get(old, old)} → {STATUSES.get(status, status)}", "status")
    if not quiet:
        await update.message.reply_text("✅ انجام شد")


async def add_note_and_reply(update: Update, task_id: int, note: str, source: str = "manual", quiet: bool = False) -> None:
    task = get_task(task_id)
    if not task:
        if not quiet:
            await update.message.reply_text("❌ کار پیدا نشد.")
        return
    note = normalize_text(note)
    if not note:
        return
    add_task_note(task_id, user_id(update), user_name(update), note, source, update.message.message_id if update.message else None)
    add_history(task_id, user_id(update), user_name(update), "add_note", "", note)
    status = detect_status_from_text(note)
    if status:
        update_task_field(task_id, "status", status)
        if status == "done":
            update_task_field(task_id, "completed_at", now_str())
        add_history(task_id, user_id(update), user_name(update), "auto_status_from_note", task.get("status"), status)
    if not quiet:
        await update.message.reply_text("✅ ثبت شد")

# ------------------------- commands -------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    clear_user_states(context, user_id(update))
    await update.message.reply_text("✅ ربات آماده است", reply_markup=main_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    await update.message.reply_text(
        "❓ راهنما\n\n"
        "📋 /tasks — لیست دکمه‌ای کارها\n"
        "➕ /newtask عنوان کار — ثبت کار\n"
        "✅ /done 1 — انجام‌شده کردن کار\n"
        "⏰ /remind 1 2026-06-25 18:00 — یادآوری\n"
        "🧠 /smart — تحلیل چت و کارها با ChatGPT\n"
        "📥 /export_excel — خروجی اکسل\n"
        "📄 /export_pdf — خروجی PDF\n\n"
        "متنی هم می‌توانی بنویسی:\n"
        "کار جدید: پیگیری مالی با حسابدار\n"
        "کار 1: زنگ زدم جواب ندادند\n"
        "کار 1 انجام شد",
        reply_markup=main_keyboard(),
    )


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    await show_task_list(update, context)


async def newtask_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    raw = clean_command_arg_text(update, context)
    if raw:
        await create_task_silent(update, context, raw)
        return
    context.user_data["new_project"] = None
    await update.message.reply_text("📁 پروژه کار را انتخاب کن:", reply_markup=project_keyboard())


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    raw = clean_command_arg_text(update, context)
    task_id = extract_task_id(raw)
    if not task_id:
        await update.message.reply_text("مثال درست:\n/done 1")
        return
    await set_status_and_reply(update, task_id, "done")


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    raw = clean_command_arg_text(update, context)
    task_id = extract_task_id(raw)
    if not task_id:
        await update.message.reply_text("مثال درست:\n/delete 1")
        return
    task = get_task(task_id)
    if not task:
        await update.message.reply_text("❌ کار پیدا نشد.")
        return
    update_task_field(task_id, "deleted", 1)
    update_task_field(task_id, "status", "cancelled")
    add_history(task_id, user_id(update), user_name(update), "delete", "", "deleted")
    await update.message.reply_text("✅ حذف شد")


async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    raw = clean_command_arg_text(update, context)
    task_id = extract_task_id(raw)
    if not task_id:
        await update.message.reply_text("مثال درست:\n/restore 1")
        return
    task = get_task(task_id)
    if not task:
        await update.message.reply_text("❌ کار پیدا نشد.")
        return
    update_task_field(task_id, "deleted", 0)
    update_task_field(task_id, "status", "pending")
    add_history(task_id, user_id(update), user_name(update), "restore", "deleted", "pending")
    await update.message.reply_text("✅ بازیابی شد")


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    raw = clean_command_arg_text(update, context)
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("مثال درست:\n/remind 1 2026-06-25 18:00\n/remind 1 فردا 10:00")
        return
    task_id = extract_task_id(parts[0])
    dt, repeat = parse_datetime_text(parts[1])
    if not task_id or dt is None:
        await update.message.reply_text("❌ فرمت زمان اشتباه است.\nمثال درست:\n/remind 1 2026-06-25 18:00")
        return
    task = get_task(task_id)
    if not task:
        await update.message.reply_text("❌ کار پیدا نشد.")
        return
    update_task_field(task_id, "reminder_time", dt)
    update_task_field(task_id, "reminder_repeat", repeat)
    add_history(task_id, user_id(update), user_name(update), "reminder", task.get("reminder_time"), f"{dt} / {repeat}")
    await update.message.reply_text("✅ تنظیم شد")


async def note_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    raw = clean_command_arg_text(update, context)
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("مثال درست:\n/note 1 زنگ زدم جواب ندادند")
        return
    task_id = extract_task_id(parts[0])
    if not task_id:
        await update.message.reply_text("شماره کار را ننوشتی.")
        return
    await add_note_and_reply(update, task_id, parts[1], "command")


async def checklist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    raw = clean_command_arg_text(update, context)
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("مثال درست:\n/checklist 1 ارسال قرارداد")
        return
    task_id = extract_task_id(parts[0])
    if not task_id or not get_task(task_id):
        await update.message.reply_text("❌ کار پیدا نشد.")
        return
    add_checklist_item(task_id, parts[1], user_id(update))
    add_history(task_id, user_id(update), user_name(update), "add_checklist", "", parts[1])
    await update.message.reply_text("✅ ثبت شد")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    tasks = get_tasks(include_done=True, include_deleted=False, limit=10000)
    total = len(tasks)
    done = len([t for t in tasks if t.get("status") == "done"])
    open_count = len([t for t in tasks if t.get("status") not in ["done", "cancelled"]])
    await update.message.reply_text(f"📊 آمار\n\nکل کارها: {total}\nانجام‌شده: {done}\nباز: {open_count}")


async def daily_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    await update.message.reply_text(build_daily_report())


async def weekly_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    await update.message.reply_text(build_weekly_report())


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    raw = clean_command_arg_text(update, context)
    if raw:
        await send_chat_summary(update, context, raw)
        return
    await update.message.reply_text("🧠 بازه تحلیل را انتخاب کن:", reply_markup=summary_range_keyboard())


async def smart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    msg = await update.message.reply_text("🧠 در حال تحلیل چت و کارها…")
    await run_smart_analysis(update, context, progress_message=msg)


async def ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    USER_STATE[user_id(update)] = "ai"
    await update.message.reply_text("🤖 دستیار فعال شد. سوالت را بنویس. برای خروج /exit", reply_markup=back_keyboard())


async def exit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_user_states(context, user_id(update))
    await update.message.reply_text("✅ خارج شد", reply_markup=main_keyboard())


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    u = get_user(user_id(update))
    role = "مدیر" if u and u.get("role") == "admin" else "عضو"
    await update.message.reply_text(f"👤 پروفایل\n\nنام: {user_name(update)}\nنقش: {role}")


async def members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    users = get_users()
    if not users:
        await update.message.reply_text("هنوز عضوی ثبت نشده.")
        return
    lines = ["👥 اعضا"]
    for u in users:
        icon = "👑" if u.get("role") == "admin" else "👤"
        lines.append(f"{icon} {u.get('full_name') or '-'} | @{u.get('username') or '-'}")
    await update.message.reply_text("\n".join(lines))


async def chatid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Chat ID:\n{update.effective_chat.id}\n\nType:\n{update.effective_chat.type}")


async def export_excel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    try:
        path = build_excel_report()
        await update.message.reply_document(document=InputFile(path), filename="sam_tasks_report.xlsx")
    except Exception as e:
        await update.message.reply_text(f"❌ خطای خروجی اکسل: {e}")


async def export_pdf_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    try:
        path = build_pdf_report()
        await update.message.reply_document(document=InputFile(path), filename=os.path.basename(path))
    except Exception as e:
        await update.message.reply_text(f"❌ خطای خروجی PDF: {e}")

# ------------------------- callbacks -------------------------

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await register_user(update)
    data = query.data or ""

    if data == "main:back":
        clear_user_states(context, query.from_user.id)
        await query.message.reply_text("✅ بازگشت", reply_markup=main_keyboard())
        return

    if data == "new:start":
        await query.message.reply_text("📁 پروژه کار را انتخاب کن:", reply_markup=project_keyboard())
        return

    if data.startswith("new_project:"):
        project = data.split(":", 1)[1]
        context.user_data["new_project"] = project
        context.user_data["state"] = "waiting_new_title"
        await query.message.reply_text(f"پروژه: {project}\nعنوان کار را بنویس:", reply_markup=back_keyboard())
        return

    if data.startswith("tasks:page:"):
        page = int(data.split(":")[-1])
        await show_task_list(query, context, page=page, edit=True)
        return

    if data.startswith("summary:"):
        mode = data.split(":", 1)[1]
        await query.message.reply_text("🧠 در حال تحلیل…")
        await send_chat_summary_from_chat(query.message, context, mode)
        return

    if data.startswith("task:"):
        parts = data.split(":")
        action = parts[1]
        task_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        if action == "open" and task_id:
            await open_task_menu(query, task_id)
            return
        if not task_id:
            await query.message.reply_text("❌ شماره کار نامعتبر است.")
            return
        task = get_task(task_id)
        if not task:
            await query.message.reply_text("❌ کار پیدا نشد.")
            return

        if action == "status":
            status = parts[3]
            old = task.get("status")
            update_task_field(task_id, "status", status)
            if status == "done":
                update_task_field(task_id, "completed_at", now_str())
            add_history(task_id, query.from_user.id, query.from_user.full_name, "status", old, status)
            add_task_note(task_id, query.from_user.id, query.from_user.full_name, f"وضعیت تغییر کرد: {STATUSES.get(old, old)} → {STATUSES.get(status, status)}", "button")
            await open_task_menu(query, task_id)
            return

        if action == "notes":
            notes = get_task_notes(task_id, 20)
            text = f"📝 شرح‌های کار #{task_id}\n\n"
            text += "شرحی ثبت نشده." if not notes else "\n\n".join([f"{n['created_at']} | {n['full_name']}:\n{n['note']}" for n in notes])
            await query.message.reply_text(text)
            return

        if action == "add_note":
            context.user_data["state"] = "waiting_note"
            context.user_data["task_id"] = task_id
            await query.message.reply_text("شرح را بنویس:", reply_markup=back_keyboard())
            return

        if action == "checklist":
            rows = get_checklist(task_id)
            if not rows:
                await query.message.reply_text("☑️ چک‌لیستی ثبت نشده.")
                return
            kb = []
            lines = [f"☑️ چک‌لیست کار #{task_id}"]
            for r in rows:
                mark = "✅" if r.get("is_done") else "☐"
                lines.append(f"{mark} #{r['id']} {r['item_text']}")
                kb.append([InlineKeyboardButton(f"تغییر #{r['id']}", callback_data=f"check:toggle:{r['id']}:{task_id}")])
            await query.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))
            return

        if action == "add_check":
            context.user_data["state"] = "waiting_check"
            context.user_data["task_id"] = task_id
            await query.message.reply_text("آیتم چک‌لیست را بنویس:", reply_markup=back_keyboard())
            return

        if action == "files":
            rows = get_task_files(task_id)
            text = f"📎 فایل‌های کار #{task_id}\n\n"
            if not rows:
                text += "فایلی ثبت نشده. روی پیام منوی همین کار Reply کن و عکس/فایل بفرست."
            else:
                text += "\n".join([f"#{r['id']} | {r['created_at']} | {r['file_type']} | {r.get('caption') or '-'}" for r in rows])
            await query.message.reply_text(text)
            return

        if action == "history":
            rows = get_task_history(task_id, 30)
            text = f"🧾 تاریخچه کار #{task_id}\n\n"
            text += "تاریخچه‌ای ثبت نشده." if not rows else "\n".join([f"{r['created_at']} | {r['full_name']} | {r['action']}: {r['old_value']} → {r['new_value']}" for r in rows])
            await query.message.reply_text(text)
            return

        if action == "pin":
            new_val = 0 if task.get("pinned") else 1
            update_task_field(task_id, "pinned", new_val)
            add_history(task_id, query.from_user.id, query.from_user.full_name, "pin", task.get("pinned"), new_val)
            await open_task_menu(query, task_id)
            return

        if action == "remind":
            context.user_data["state"] = "waiting_remind"
            context.user_data["task_id"] = task_id
            await query.message.reply_text("زمان یادآوری را بنویس. مثال:\n2026-06-25 18:00\nیا: فردا 10:00", reply_markup=back_keyboard())
            return

        if action == "delete":
            update_task_field(task_id, "deleted", 1)
            update_task_field(task_id, "status", "cancelled")
            add_history(task_id, query.from_user.id, query.from_user.full_name, "delete", "", "deleted")
            await show_task_list(query, context, edit=True)
            return

    if data.startswith("check:toggle:"):
        _, _, item_id, task_id = data.split(":")
        toggle_checklist_item(int(item_id))
        add_history(int(task_id), query.from_user.id, query.from_user.full_name, "toggle_checklist", item_id, "")
        await query.message.reply_text("✅ تغییر کرد")
        return

    if data.startswith("advice:show:"):
        sid = int(data.split(":")[-1])
        row = get_ai_suggestion(sid)
        if not row:
            await query.message.reply_text("کامنت پیدا نشد.")
            return
        update_ai_suggestion_status(sid, "shown", query.from_user.id)
        await query.message.reply_text(f"🧠 کامنت مدیریتی:\n\n{row.get('note') or row.get('reason') or '-'}")
        return

    if data.startswith("advice:reject:"):
        sid = int(data.split(":")[-1])
        update_ai_suggestion_status(sid, "rejected", query.from_user.id)
        await query.message.reply_text("✅ رد شد")
        return

# ------------------------- text and file routers -------------------------

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    await register_user(update)
    text = normalize_text(update.message.text)
    reply_id = update.message.reply_to_message.message_id if update.message.reply_to_message else None
    save_chat_message(update.effective_chat.id, user_id(update), user_name(update), update.effective_user.username or "", text, update.message.message_id, reply_id)

    if is_back(text):
        clear_user_states(context, user_id(update))
        await update.message.reply_text("✅ بازگشت", reply_markup=main_keyboard())
        return

    state = context.user_data.get("state")
    if state == "waiting_new_title":
        project = context.user_data.get("new_project") or detect_project(text)
        clear_user_states(context, user_id(update))
        await create_task_silent(update, context, text, project=project)
        return

    if state == "waiting_note":
        task_id = int(context.user_data.get("task_id"))
        clear_user_states(context, user_id(update))
        await add_note_and_reply(update, task_id, text, "button")
        return

    if state == "waiting_check":
        task_id = int(context.user_data.get("task_id"))
        clear_user_states(context, user_id(update))
        add_checklist_item(task_id, text, user_id(update))
        add_history(task_id, user_id(update), user_name(update), "add_checklist", "", text)
        await update.message.reply_text("✅ ثبت شد", reply_markup=main_keyboard())
        return

    if state == "waiting_remind":
        task_id = int(context.user_data.get("task_id"))
        dt, repeat = parse_datetime_text(text)
        if dt is None:
            await update.message.reply_text("❌ فرمت زمان اشتباه است. مثال:\n2026-06-25 18:00\nیا: فردا 10:00")
            return
        clear_user_states(context, user_id(update))
        old = get_task(task_id).get("reminder_time") if get_task(task_id) else ""
        update_task_field(task_id, "reminder_time", dt)
        update_task_field(task_id, "reminder_repeat", repeat)
        add_history(task_id, user_id(update), user_name(update), "reminder", old, f"{dt} / {repeat}")
        await update.message.reply_text("✅ تنظیم شد", reply_markup=main_keyboard())
        return

    if USER_STATE.get(user_id(update)) == "ai":
        await ai_chat_reply(update, context, text)
        return

    # Reply to task menu/message adds note.
    if reply_id:
        task_id = get_task_id_by_message(update.effective_chat.id, reply_id)
        if task_id:
            await add_note_and_reply(update, task_id, text, "reply")
            return

    # Keyboard buttons and Persian aliases.
    if text in ["➕ کار جدید", "کار جدید", "ایجاد کار", "ساخت کار"]:
        await newtask_command(update, context)
        return
    if text in ["📋 کارها", "کارها", "لیست کارها", "پیگیری", "⏱ پیگیری"]:
        await tasks_command(update, context)
        return
    if text in ["🧠 مدیر هوشمند", "مدیر هوشمند"]:
        await smart_command(update, context)
        return
    if text in ["🧠 تحلیل چت", "تحلیل چت", "خلاصه چت"]:
        await summary_command(update, context)
        return
    if text in ["🎙 فرمان صوتی", "فرمان صوتی", "ویس"]:
        await update.message.reply_text("🎙 ویس بفرست. من اول تبدیل به متن می‌کنم، بعد فقط اگر فرمان واضح باشد اجرا می‌کنم.")
        return
    if text in ["📊 گزارش‌ها", "گزارش‌ها"]:
        await update.message.reply_text("📊 گزارش‌ها", reply_markup=reports_keyboard())
        return
    if text in ["📊 آمار", "آمار"]:
        await stats_command(update, context)
        return
    if text in ["📅 گزارش روزانه", "گزارش روزانه"]:
        await daily_report_command(update, context)
        return
    if text in ["📈 گزارش هفتگی", "گزارش هفتگی"]:
        await weekly_report_command(update, context)
        return
    if text in ["📥 خروجی اکسل", "خروجی اکسل", "اکسل"]:
        await export_excel_command(update, context)
        return
    if text in ["📄 خروجی PDF", "خروجی PDF", "PDF", "pdf"]:
        await export_pdf_command(update, context)
        return
    if text in ["👥 اعضا", "اعضا"]:
        await members(update, context)
        return
    if text in ["👤 پروفایل", "پروفایل"]:
        await whoami(update, context)
        return
    if text in ["❓ راهنما", "راهنما", "کمک"]:
        await help_command(update, context)
        return

    if text in ["۱ ساعت اخیر", "یک ساعت اخیر"]:
        await send_chat_summary(update, context, "1h")
        return
    if text in ["۲ ساعت اخیر", "دو ساعت اخیر"]:
        await send_chat_summary(update, context, "2h")
        return
    if text == "دیروز":
        await send_chat_summary(update, context, "yesterday")
        return
    if text in ["۷ روز اخیر", "7 روز اخیر"]:
        await send_chat_summary(update, context, "7d")
        return

    # Structured text patterns.
    m = re.search(r"کار\s*([0-9۰-۹٠-٩]+)\s*[:：-]\s*(.+)", text)
    if m:
        task_id = int(fa_to_en_digits(m.group(1)))
        await add_note_and_reply(update, task_id, m.group(2), "pattern")
        return

    m = re.search(r"کار\s*([0-9۰-۹٠-٩]+).*(انجام|تمام|لغو|کنسل|منتظر|پیگیری|در حال)", text)
    if m:
        task_id = int(fa_to_en_digits(m.group(1)))
        status = detect_status_from_text(text) or "in_progress"
        await set_status_and_reply(update, task_id, status)
        return

    if re.match(r"^(کار جدید|ایجاد کار|ساخت کار|تسک جدید|وظیفه جدید)\s*[:：-]\s*", text):
        raw = re.sub(r"^(کار جدید|ایجاد کار|ساخت کار|تسک جدید|وظیفه جدید)\s*[:：-]\s*", "", text).strip()
        await create_task_silent(update, context, raw)
        return

    # No answer to random group text. It is only stored for smart analysis.


async def file_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await register_user(update)
    reply_id = update.message.reply_to_message.message_id if update.message.reply_to_message else None
    if not reply_id:
        await update.message.reply_text("برای ذخیره فایل، روی منوی همان کار Reply کن و فایل/عکس بفرست.")
        return
    task_id = get_task_id_by_message(update.effective_chat.id, reply_id)
    if not task_id:
        await update.message.reply_text("این پیام به کار خاصی وصل نیست.")
        return
    file_id = None
    file_type = None
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
    caption = update.message.caption or ""
    add_task_file(task_id, user_id(update), user_name(update), file_id, file_type, caption)
    add_history(task_id, user_id(update), user_name(update), "add_file", "", file_type)
    if caption:
        add_task_note(task_id, user_id(update), user_name(update), caption, "file_caption", update.message.message_id)
    await update.message.reply_text("✅ فایل ذخیره شد")

# ------------------------- voice -------------------------

async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    if not client:
        await update.message.reply_text("OPENAI_API_KEY تنظیم نشده.")
        return
    try:
        file = await context.bot.get_file(update.message.voice.file_id)
        path = tempfile.mktemp(suffix=".ogg")
        await file.download_to_drive(path)
        with open(path, "rb") as f:
            tr = client.audio.transcriptions.create(model=TRANSCRIBE_MODEL, file=f)
        text = normalize_text(tr.text)
    except Exception as e:
        await update.message.reply_text(f"❌ خطای تبدیل ویس: {e}")
        return
    await update.message.reply_text(f"📝 متن ویس:\n{text}")
    await handle_voice_text(update, context, text)


async def handle_voice_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    t = normalize_text(text)
    if any(w in t for w in ["لیست کار", "کارها رو", "کارها را", "کارهای باز"]):
        await tasks_command(update, context)
        return
    if any(w in t for w in ["مدیر هوشمند", "تحلیل هوشمند"]):
        await smart_command(update, context)
        return
    if any(w in t for w in ["گزارش", "آمار"]):
        await stats_command(update, context)
        return
    m = re.search(r"کار\s*([0-9۰-۹٠-٩]+).*(انجام|تمام|لغو|منتظر|پیگیری|در حال)", t)
    if m:
        task_id = int(fa_to_en_digits(m.group(1)))
        status = detect_status_from_text(t) or "in_progress"
        await set_status_and_reply(update, task_id, status)
        return
    m = re.search(r"کار\s*([0-9۰-۹٠-٩]+)\s*[:：-]?\s*(.+)", t)
    if m and any(w in t for w in ["زنگ", "جواب", "گفت", "فرستادم", "پیگیری"]):
        task_id = int(fa_to_en_digits(m.group(1)))
        await add_note_and_reply(update, task_id, m.group(2), "voice")
        return
    if any(w in t for w in ["کار جدید", "تسک جدید", "وظیفه جدید"]):
        raw = re.sub(r".*?(کار جدید|تسک جدید|وظیفه جدید)", "", t).strip(" :،-")
        if raw:
            await create_task_silent(update, context, raw)
            return
    await update.message.reply_text("فرمان واضح نبود. چیزی اجرا نشد.")

# ------------------------- AI -------------------------

def extract_json(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except Exception:
            pass
    return {}


async def ai_chat_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if not client:
        await update.message.reply_text("OPENAI_API_KEY تنظیم نشده.")
        return
    try:
        res = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "تو دستیار مدیریتی فارسی برای تیم هستی. کوتاه، عملی و دقیق جواب بده."},
                {"role": "user", "content": text},
            ],
        )
        await update.message.reply_text(res.choices[0].message.content[:3500])
    except Exception as e:
        await update.message.reply_text(f"❌ خطای دستیار: {e}")


async def send_chat_summary(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str) -> None:
    await send_chat_summary_from_chat(update.message, context, mode)


async def send_chat_summary_from_chat(message, context: ContextTypes.DEFAULT_TYPE, mode: str) -> None:
    if not client:
        await message.reply_text("OPENAI_API_KEY تنظیم نشده.")
        return
    end = datetime.now()
    start = end - timedelta(hours=1)
    if mode in ["2h", "دو", "2"]:
        start = end - timedelta(hours=2)
    elif mode in ["yesterday", "دیروز"]:
        y = end - timedelta(days=1)
        start = y.replace(hour=0, minute=0, second=0)
        end = y.replace(hour=23, minute=59, second=59)
    elif mode in ["7d", "هفته", "7"]:
        start = end - timedelta(days=7)
    rows = get_chat_messages_between(message.chat_id, start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"))
    if not rows:
        await message.reply_text("در این بازه پیامی برای تحلیل پیدا نشد.")
        return
    history = "\n".join([f"{r['created_at']} | {r['full_name']}: {r['text']}" for r in rows[-100:]])
    try:
        res = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "خلاصه‌ساز و تحلیل‌گر مدیریتی فارسی هستی."},
                {"role": "user", "content": f"این چت را خلاصه کن و کارهای قابل پیگیری، تصمیم‌ها، ریسک‌ها و اشتباهات برنامه‌ریزی را بگو:\n\n{history}"},
            ],
        )
        await message.reply_text(res.choices[0].message.content[:3900])
    except Exception as e:
        await message.reply_text(f"❌ خطای تحلیل: {e}")


async def run_smart_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE, progress_message=None) -> None:
    if not client:
        text = "OPENAI_API_KEY تنظیم نشده."
        if progress_message:
            await progress_message.edit_text(text)
        else:
            await update.message.reply_text(text)
        return

    chat_id = update.effective_chat.id
    messages = get_recent_chat_messages(chat_id, 80)
    tasks = get_open_tasks(80)
    if not messages:
        await progress_message.edit_text("پیامی برای تحلیل پیدا نشد.")
        return

    chat_text = "\n".join([f"{m['created_at']} | {m['full_name']}: {m['text']}" for m in messages[-80:]])
    task_text = "\n".join([f"#{t['id']} | {t['title']} | {STATUSES.get(t['status'], t['status'])} | پروژه:{t.get('project')} | اولویت:{t.get('priority')}" for t in tasks]) or "کار بازی وجود ندارد."
    prompt = f"""
تو مدیر هوشمند تیم هستی. چت و کارهای باز را تحلیل کن.
خروجی فقط JSON معتبر باشد.
هر جا مطمئن هستی، action بده تا ربات اعمال کند.
برای مشورت مدیریتی، action را advice بگذار. advice را مستقیم افشا نمی‌کنیم و اول اجازه می‌گیریم.

JSON format:
{{
  "applied": [
    {{
      "action": "new_task | add_note | change_status",
      "task_id": 1,
      "title": "عنوان کار جدید",
      "note": "شرح برای کار یا دلیل",
      "new_status": "pending | in_progress | waiting | done | cancelled",
      "project": "تخته | میوه | پتروشیمی | مالی | غلات | غیره",
      "priority": "فوری | زیاد | متوسط | کم",
      "confidence": 0.0
    }}
  ],
  "advice": [
    {{"note": "اشتباهات برنامه‌ریزی، ریسک‌ها، پیشنهاد مدیریتی", "confidence": 0.0}}
  ]
}}

کارهای باز:
{task_text}

چت اخیر:
{chat_text}
"""
    try:
        res = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "فقط JSON معتبر بده. توضیح خارج JSON ننویس."},
                {"role": "user", "content": prompt},
            ],
        )
        data = extract_json(res.choices[0].message.content)
    except Exception as e:
        await progress_message.edit_text(f"❌ خطای مدیر هوشمند: {e}")
        return

    applied_count = 0
    new_count = 0
    note_count = 0
    status_count = 0
    skipped_count = 0

    for item in data.get("applied", [])[:12]:
        try:
            confidence = float(item.get("confidence", 0) or 0)
        except Exception:
            confidence = 0
        if confidence < 0.60:
            skipped_count += 1
            continue
        action = item.get("action")
        project = item.get("project") if item.get("project") in PROJECTS else detect_project(str(item.get("title") or item.get("note") or ""))
        priority = item.get("priority") if item.get("priority") in PRIORITIES else detect_priority(str(item.get("title") or item.get("note") or ""))

        if action == "new_task":
            title = normalize_text(str(item.get("title") or ""))
            if not title:
                continue
            task_id = create_task(title=title, assigned_to=None, assigned_by=0, priority=priority, reminder_time="none", created_at=now_str(), description="ساخته‌شده توسط مدیر هوشمند", project=project)
            add_history(task_id, 0, "AI", "ai_create", "", title)
            if item.get("note"):
                add_task_note(task_id, 0, "AI", str(item.get("note")), "ai")
            applied_count += 1
            new_count += 1
        elif action == "add_note":
            task_id = item.get("task_id")
            if not task_id or not get_task(int(task_id)):
                continue
            note = normalize_text(str(item.get("note") or ""))
            if not note:
                continue
            add_task_note(int(task_id), 0, "AI", note, "ai")
            add_history(int(task_id), 0, "AI", "ai_note", "", note)
            applied_count += 1
            note_count += 1
        elif action == "change_status":
            task_id = item.get("task_id")
            status = item.get("new_status")
            if not task_id or status not in STATUSES or not get_task(int(task_id)):
                continue
            old = get_task(int(task_id)).get("status")
            update_task_field(int(task_id), "status", status)
            if status == "done":
                update_task_field(int(task_id), "completed_at", now_str())
            add_history(int(task_id), 0, "AI", "ai_status", old, status)
            if item.get("note"):
                add_task_note(int(task_id), 0, "AI", str(item.get("note")), "ai_status")
            applied_count += 1
            status_count += 1

    advice_items = []
    for adv in data.get("advice", [])[:3]:
        try:
            c = float(adv.get("confidence", 0) or 0)
        except Exception:
            c = 0
        note = normalize_text(str(adv.get("note") or ""))
        if note and c >= 0.55:
            sid = save_ai_suggestion(chat_id, "advice", note=note, reason="مدیر هوشمند", confidence=c, raw_json=json.dumps(adv, ensure_ascii=False))
            advice_items.append(sid)

    result = (
        "✅ تحلیل تمام شد\n\n"
        f"اعمال‌شده‌ها: {applied_count}\n"
        f"کار جدید: {new_count}\n"
        f"شرح اضافه‌شده: {note_count}\n"
        f"تغییر وضعیت: {status_count}"
    )
    if skipped_count:
        result += f"\nرد شده به خاطر اطمینان پایین: {skipped_count}"

    if advice_items:
        sid = advice_items[0]
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("نمایش کامنت", callback_data=f"advice:show:{sid}"), InlineKeyboardButton("رد", callback_data=f"advice:reject:{sid}")]])
        result += "\n\n🧠 کامنت مدیریتی دارم. اجازه می‌دهی نشان بدهم؟"
        await progress_message.edit_text(result, reply_markup=kb)
    else:
        await progress_message.edit_text(result)

# ------------------------- reports -------------------------

def build_daily_report() -> str:
    tasks = get_tasks(include_done=True, include_deleted=False, limit=10000)
    today = datetime.now().strftime("%Y-%m-%d")
    open_tasks = [t for t in tasks if t.get("status") not in ["done", "cancelled"]]
    done_today = [t for t in tasks if (t.get("completed_at") or "").startswith(today)]
    urgent = [t for t in open_tasks if t.get("priority") in ["فوری", "زیاد"]]
    projects: Dict[str, int] = {}
    for t in open_tasks:
        projects[t.get("project") or "غیره"] = projects.get(t.get("project") or "غیره", 0) + 1
    ptext = "\n".join([f"• {p}: {c}" for p, c in projects.items()]) or "موردی ثبت نشده"
    return f"📊 گزارش روزانه SAM\n\nتاریخ: {today}\n\nکارهای باز: {len(open_tasks)}\nکارهای فوری/زیاد: {len(urgent)}\nانجام‌شده امروز: {len(done_today)}\n\nوضعیت پروژه‌ها:\n{ptext}"


def build_weekly_report() -> str:
    tasks = get_tasks(include_done=True, include_deleted=False, limit=10000)
    since = datetime.now() - timedelta(days=7)
    week = [t for t in tasks if parse_db_dt(t.get("created_at")) and parse_db_dt(t.get("created_at")) >= since]
    done = [t for t in week if t.get("status") == "done"]
    return f"📈 گزارش هفتگی SAM\n\nکارهای جدید هفته: {len(week)}\nانجام‌شده: {len(done)}\nباز از کارهای هفته: {len([t for t in week if t.get('status') not in ['done','cancelled']])}"


def build_excel_report() -> str:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tasks"
    headers = ["ID", "Title", "Project", "Status", "Priority", "Reminder", "Created", "Completed", "Description"]
    ws.append(headers)
    for t in get_tasks(include_done=True, include_deleted=False, limit=10000):
        ws.append([
            t.get("id"), t.get("title"), t.get("project"), STATUSES.get(t.get("status"), t.get("status")),
            t.get("priority"), t.get("reminder_time"), t.get("created_at"), t.get("completed_at"), t.get("description"),
        ])
    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 3, 45)
    path = tempfile.mktemp(suffix=".xlsx")
    wb.save(path)
    return path


def build_pdf_report() -> str:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        path = tempfile.mktemp(suffix=".pdf")
        c = canvas.Canvas(path, pagesize=A4)
        try:
            pdfmetrics.registerFont(TTFont("DejaVu", font_path))
            c.setFont("DejaVu", 10)
        except Exception:
            c.setFont("Helvetica", 10)
        y = 800
        c.drawString(40, y, "SAM PRO Team Manager Report")
        y -= 25
        for t in get_tasks(include_done=True, include_deleted=False, limit=80):
            line = f"#{t.get('id')} | {t.get('project')} | {t.get('status')} | {trim(t.get('title') or '', 70)}"
            c.drawString(40, y, line)
            y -= 17
            if y < 50:
                c.showPage()
                try:
                    c.setFont("DejaVu", 10)
                except Exception:
                    c.setFont("Helvetica", 10)
                y = 800
        c.save()
        return path
    except Exception:
        path = tempfile.mktemp(suffix=".txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(build_daily_report())
        return path

# ------------------------- jobs -------------------------

async def followup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not GROUP_CHAT_ID:
        return
    tasks = get_open_tasks(200)
    text = "⏱ پیگیری سه‌ساعته\n\n" + task_list_text(tasks)
    try:
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=text, reply_markup=task_list_keyboard(tasks))
    except Exception as e:
        print(f"followup_job error: {e}")


async def check_due_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now()
    for task in get_open_tasks(500):
        dt = parse_db_dt(task.get("reminder_time"))
        if not dt or dt > now:
            continue
        text = f"⏰ یادآوری\n\n{task_summary_label(task)}"
        if task.get("assigned_to"):
            try:
                await context.bot.send_message(chat_id=task.get("assigned_to"), text=text, reply_markup=task_menu_keyboard(task["id"]))
            except Exception as e:
                print(f"private reminder error: {e}")
        if GROUP_CHAT_ID:
            try:
                await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=text, reply_markup=task_menu_keyboard(task["id"]))
            except Exception as e:
                print(f"group reminder error: {e}")
        repeat = task.get("reminder_repeat") or "none"
        if repeat == "daily":
            update_task_field(task["id"], "reminder_time", (dt + timedelta(days=1)).strftime("%Y-%m-%d %H:%M"))
        elif repeat == "weekly":
            update_task_field(task["id"], "reminder_time", (dt + timedelta(days=7)).strftime("%Y-%m-%d %H:%M"))
        else:
            update_task_field(task["id"], "reminder_time", "none")
        add_history(task["id"], 0, "BOT", "reminder_sent", "", task.get("reminder_time"))


async def daily_report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    targets = set(get_admin_ids())
    if GROUP_CHAT_ID:
        targets.add(GROUP_CHAT_ID)
    for chat_id in targets:
        try:
            await context.bot.send_message(chat_id=chat_id, text=build_daily_report())
        except Exception as e:
            print(f"daily report error: {e}")

# ------------------------- app -------------------------

def build_app() -> Application:
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("tasks", tasks_command))
    app.add_handler(CommandHandler("newtask", newtask_command))
    app.add_handler(CommandHandler("new", newtask_command))
    app.add_handler(CommandHandler("done", done_command))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CommandHandler("restore", restore_command))
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CommandHandler("note", note_command))
    app.add_handler(CommandHandler("checklist", checklist_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("dailyreport", daily_report_command))
    app.add_handler(CommandHandler("report", daily_report_command))
    app.add_handler(CommandHandler("weeklyreport", weekly_report_command))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(CommandHandler("smart", smart_command))
    app.add_handler(CommandHandler("ai", ai_command))
    app.add_handler(CommandHandler("exit", exit_command))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("profile", whoami))
    app.add_handler(CommandHandler("members", members))
    app.add_handler(CommandHandler("chatid", chatid_command))
    app.add_handler(CommandHandler("export_excel", export_excel_command))
    app.add_handler(CommandHandler("export_pdf", export_pdf_command))

    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler), group=0)
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO, file_router), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router), group=1)

    # Every minute checks only due reminders; it does NOT send the task list.
    app.job_queue.run_repeating(check_due_reminders, interval=60, first=20)
    # The group follow-up list is sent only every 3 hours.
    app.job_queue.run_repeating(followup_job, interval=3 * 60 * 60, first=3 * 60 * 60)
    app.job_queue.run_daily(daily_report_job, time=time(hour=21, minute=0), name="daily_report")
    return app


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")
    print("BOT_TOKEN loaded:", bool(TOKEN), TOKEN[-6:] if TOKEN else "NO TOKEN")
    print("OPENAI_MODEL:", OPENAI_MODEL)
    print("GROUP_CHAT_ID:", GROUP_CHAT_ID)
    app = build_app()
    print("SAM PRO Team Manager V6 Stable Started...")
    app.run_polling(drop_pending_updates=True)
