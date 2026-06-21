import os
import sqlite3
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, List, Any

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from openai import AsyncOpenAI
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# =========================================================
# تنظیمات محیط
# =========================================================
# Required:
#   BOT_TOKEN
#   OPENAI_API_KEY   (optional for runtime, but required if you want /ai to work)
#
# Optional:
#   OPENAI_MODEL     default: gpt-5
#   DB_PATH          default: sam_pro.db
#   APP_TIMEZONE     default: Europe/London
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
DB_PATH = os.getenv("DB_PATH", "sam_pro.db")
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Europe/London")

TZ = ZoneInfo(APP_TIMEZONE)
logger = logging.getLogger("sam_pro")

# APScheduler روی event loop تلگرام/asyncio اجرا می‌شود
scheduler = AsyncIOScheduler(timezone=TZ)

# AsyncOpenAI باعث می‌شود درخواست AI هندلر را block نکند
ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"

STATUS_PENDING = "pending"
STATUS_DONE = "done"

PRIORITY_LABELS = {
    "high": "زیاد 🔴",
    "medium": "متوسط 🟡",
    "low": "کم 🟢",
}


# =========================================================
# ابزارهای زمانی
# =========================================================
def now_local() -> datetime:
    return datetime.now(TZ)


def fmt_dt(dt: Optional[datetime]) -> str:
    """Datetime timezone-aware را به رشته پایدار DB تبدیل می‌کند."""
    if not dt:
        return "—"
    return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M")


def parse_dt(text: str) -> datetime:
    """فرمت سفارشی یادآوری: YYYY-MM-DD HH:MM"""
    dt = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M")
    return dt.replace(tzinfo=TZ)


def quick_today_reminder() -> datetime:
    """Preset سریع امروز. اگر 18:00 گذشته باشد، یک ساعت بعد را می‌گذارد."""
    now = now_local()
    candidate = now.replace(hour=18, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate = now + timedelta(hours=1)
        candidate = candidate.replace(second=0, microsecond=0)
    return candidate


def quick_tomorrow_reminder() -> datetime:
    """Preset سریع فردا ساعت 09:00"""
    candidate = now_local() + timedelta(days=1)
    return candidate.replace(hour=9, minute=0, second=0, microsecond=0)


# =========================================================
# دیتابیس SQLite
# =========================================================
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """ساخت جدول‌ها و ایندکس‌ها"""
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            priority TEXT NOT NULL DEFAULT 'medium',
            assigned_to INTEGER,
            assigned_by INTEGER,
            reminder_at TEXT,
            reminder_sent INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (assigned_to) REFERENCES users(user_id) ON DELETE SET NULL,
            FOREIGN KEY (assigned_by) REFERENCES users(user_id) ON DELETE SET NULL
        )
        """
    )

    cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_reminder ON tasks(reminder_sent, reminder_at)"
    )

    conn.commit()
    conn.close()


# -------------------------
# users
# -------------------------
def add_user(user_id: int, username: Optional[str], full_name: str, role: str) -> None:
    """کاربر را create/update می‌کند ولی role را بی‌دلیل overwrite نمی‌کند."""
    conn = db()
    conn.execute(
        """
        INSERT INTO users(user_id, username, full_name, role, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            full_name = excluded.full_name
        """,
        (user_id, username, full_name, role, fmt_dt(now_local())),
    )
    conn.commit()
    conn.close()


def get_user(user_id: int) -> Optional[sqlite3.Row]:
    conn = db()
    row = conn.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return row


def get_users() -> List[sqlite3.Row]:
    conn = db()
    rows = conn.execute(
        "SELECT * FROM users ORDER BY CASE WHEN role = 'admin' THEN 0 ELSE 1 END, full_name COLLATE NOCASE ASC"
    ).fetchall()
    conn.close()
    return rows


def count_admins() -> int:
    conn = db()
    value = conn.execute(
        "SELECT COUNT(*) FROM users WHERE role = ?",
        (ROLE_ADMIN,),
    ).fetchone()[0]
    conn.close()
    return int(value)


def ensure_user_record(tg_user) -> sqlite3.Row:
    """
    اگر کاربر اولین admin باشد، همان لحظه admin می‌شود.
    بقیه users به‌صورت member ثبت می‌شوند.
    """
    existing = get_user(tg_user.id)
    role = ROLE_ADMIN if (count_admins() == 0 and not existing) else (
        existing["role"] if existing else ROLE_MEMBER
    )

    add_user(
        user_id=tg_user.id,
        username=tg_user.username,
        full_name=tg_user.full_name,
        role=role,
    )
    return get_user(tg_user.id)


# -------------------------
# tasks
# -------------------------
def create_task(
    title: str,
    assigned_to: Optional[int],
    assigned_by: int,
    priority: str = "medium",
    reminder_at: Optional[datetime] = None,
) -> int:
    now_s = fmt_dt(now_local())
    reminder_s = fmt_dt(reminder_at) if reminder_at else None

    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO tasks(
            title, status, priority, assigned_to, assigned_by,
            reminder_at, reminder_sent, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            title.strip(),
            STATUS_PENDING,
            priority,
            assigned_to,
            assigned_by,
            reminder_s,
            now_s,
            now_s,
        ),
    )
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(task_id)


def get_task(task_id: int) -> Optional[sqlite3.Row]:
    conn = db()
    row = conn.execute(
        """
        SELECT
            t.*,
            u1.full_name AS assigned_to_name,
            u2.full_name AS assigned_by_name
        FROM tasks t
        LEFT JOIN users u1 ON u1.user_id = t.assigned_to
        LEFT JOIN users u2 ON u2.user_id = t.assigned_by
        WHERE t.id = ?
        """,
        (task_id,),
    ).fetchone()
    conn.close()
    return row


def get_tasks(assigned_to: Optional[int] = None, include_done: bool = True) -> List[sqlite3.Row]:
    conn = db()

    where = []
    params = []

    if assigned_to is not None:
        where.append("t.assigned_to = ?")
        params.append(assigned_to)

    if not include_done:
        where.append("t.status != ?")
        params.append(STATUS_DONE)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    rows = conn.execute(
        f"""
        SELECT
            t.*,
            u1.full_name AS assigned_to_name,
            u2.full_name AS assigned_by_name
        FROM tasks t
        LEFT JOIN users u1 ON u1.user_id = t.assigned_to
        LEFT JOIN users u2 ON u2.user_id = t.assigned_by
        {where_sql}
        ORDER BY
            CASE WHEN t.status = 'pending' THEN 0 ELSE 1 END,
            t.id DESC
        """,
        tuple(params),
    ).fetchall()

    conn.close()
    return rows


def set_task_status(task_id: int, status: str) -> None:
    conn = db()
    conn.execute(
        """
        UPDATE tasks
        SET status = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, fmt_dt(now_local()), task_id),
    )
    conn.commit()
    conn.close()


def delete_task(task_id: int) -> None:
    conn = db()
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def get_due_reminders() -> List[sqlite3.Row]:
    conn = db()
    rows = conn.execute(
        """
        SELECT
            t.*,
            u1.full_name AS assigned_to_name,
            u2.full_name AS assigned_by_name
        FROM tasks t
        LEFT JOIN users u1 ON u1.user_id = t.assigned_to
        LEFT JOIN users u2 ON u2.user_id = t.assigned_by
        WHERE t.reminder_at IS NOT NULL
          AND t.reminder_sent = 0
          AND t.status != ?
          AND t.reminder_at <= ?
        ORDER BY t.reminder_at ASC
        """,
        (STATUS_DONE, fmt_dt(now_local())),
    ).fetchall()
    conn.close()
    return rows


def mark_reminder_sent(task_id: int) -> None:
    conn = db()
    conn.execute(
        """
        UPDATE tasks
        SET reminder_sent = 1, updated_at = ?
        WHERE id = ?
        """,
        (fmt_dt(now_local()), task_id),
    )
    conn.commit()
    conn.close()


def get_stats_snapshot(user_id: Optional[int] = None) -> dict:
    tasks = get_tasks(assigned_to=user_id, include_done=True)
    total = len(tasks)
    done = len([t for t in tasks if t["status"] == STATUS_DONE])
    pending = total - done
    return {"total": total, "done": done, "pending": pending}


# =========================================================
# ابزارهای UI و مجوز
# =========================================================
def is_admin(user_row: sqlite3.Row) -> bool:
    return bool(user_row and user_row["role"] == ROLE_ADMIN)


def can_manage_task(user_row: sqlite3.Row, task_row: sqlite3.Row) -> bool:
    """مدیر یا کاربری که task به او assign شده می‌تواند done بزند."""
    if is_admin(user_row):
        return True
    return bool(task_row and task_row["assigned_to"] == user_row["user_id"])


def escape_html(text: Optional[str]) -> str:
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def main_menu_markup() -> InlineKeyboardMarkup:
    """منوی اصلی دکمه‌ای"""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ کار جدید", callback_data="nav:create"),
                InlineKeyboardButton("📋 کارها", callback_data="nav:list"),
            ],
            [
                InlineKeyboardButton("👥 اعضا", callback_data="nav:members"),
                InlineKeyboardButton("📊 آمار", callback_data="nav:stats"),
            ],
            [
                InlineKeyboardButton("👤 پروفایل", callback_data="nav:profile"),
                InlineKeyboardButton("🤖 AI", callback_data="nav:ai_help"),
            ],
        ]
    )


def task_card(task: sqlite3.Row) -> str:
    """نمایش استاندارد یک task"""
    status_icon = "✅" if task["status"] == STATUS_DONE else "⏳"
    return (
        f"<b>#{task['id']} {status_icon} {escape_html(task['title'])}</b>\n"
        f"اولویت: {escape_html(PRIORITY_LABELS.get(task['priority'], task['priority']))}\n"
        f"مسئول: {escape_html(task['assigned_to_name'] or '—')}\n"
        f"ایجادکننده: {escape_html(task['assigned_by_name'] or '—')}\n"
        f"یادآوری: {escape_html(task['reminder_at'] or '—')}\n"
        f"وضعیت: {escape_html(task['status'])}"
    )


def task_actions_markup(task_id: int, can_delete: bool = False) -> InlineKeyboardMarkup:
    row1 = [InlineKeyboardButton("✅ انجام شد", callback_data=f"task:done:{task_id}")]
    if can_delete:
        row1.append(
            InlineKeyboardButton("🗑 حذف", callback_data=f"task:delete_confirm:{task_id}")
        )
    row2 = [InlineKeyboardButton("⬅️ بازگشت به لیست", callback_data="nav:list")]
    return InlineKeyboardMarkup([row1, row2])


# =========================================================
# state machine سبک و دکمه‌ای
# =========================================================
def flow_reset(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("task_flow", None)


def flow_get(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("task_flow", {})


async def respond(target: Any, text: str, reply_markup=None, parse_mode=ParseMode.HTML) -> None:
    """
    اگر target از callback query آمده باشد، ترجیحاً همان پیام edit می‌شود.
    اگر target از message آمده باشد، پیام جدید reply می‌شود.
    """
    if hasattr(target, "edit_message_text"):
        try:
            await target.edit_message_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            return
        except Exception:
            pass

    if hasattr(target, "reply_text"):
        await target.reply_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        return

    if hasattr(target, "message") and target.message:
        await target.message.reply_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        return

    raise RuntimeError("Unsupported response target")


async def send_member_picker(target: Any) -> None:
    users = get_users()
    rows = [
        [InlineKeyboardButton(row["full_name"], callback_data=f"create:member:{row['user_id']}")]
        for row in users
    ]
    rows.append([InlineKeyboardButton("❌ لغو", callback_data="flow:cancel")])

    await respond(
        target,
        "👤 مسئول انجام کار را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def send_priority_picker(target: Any) -> None:
    rows = [
        [InlineKeyboardButton("🔴 زیاد", callback_data="create:priority:high")],
        [InlineKeyboardButton("🟡 متوسط", callback_data="create:priority:medium")],
        [InlineKeyboardButton("🟢 کم", callback_data="create:priority:low")],
        [InlineKeyboardButton("❌ لغو", callback_data="flow:cancel")],
    ]
    await respond(
        target,
        "اولویت را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def send_reminder_picker(target: Any) -> None:
    today_dt = quick_today_reminder()
    tomorrow_dt = quick_tomorrow_reminder()

    rows = [
        [InlineKeyboardButton(f"⏰ امروز ({today_dt.strftime('%H:%M')})", callback_data="create:reminder:today")],
        [InlineKeyboardButton(f"🌤 فردا ({tomorrow_dt.strftime('%H:%M')})", callback_data="create:reminder:tomorrow")],
        [InlineKeyboardButton("📝 زمان دلخواه", callback_data="create:reminder:custom")],
        [InlineKeyboardButton("🚫 بدون یادآوری", callback_data="create:reminder:none")],
        [InlineKeyboardButton("❌ لغو", callback_data="flow:cancel")],
    ]

    await respond(
        target,
        "یادآوری را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def finalize_task_creation(
    target: Any,
    context: ContextTypes.DEFAULT_TYPE,
    reminder_at: Optional[datetime],
) -> None:
    """
    آخرین مرحله‌ی ساخت task
    target می‌تواند callback query یا message باشد.
    """
    flow = flow_get(context)
    creator = ensure_user_record(target.from_user)

    title = flow.get("title", "").strip()
    assigned_to = flow.get("assigned_to")
    priority = flow.get("priority", "medium")

    if not title:
        flow_reset(context)
        await respond(
            target,
            "❌ عنوان کار پیدا نشد. دوباره از نو شروع کن.",
            reply_markup=main_menu_markup(),
        )
        return

    task_id = create_task(
        title=title,
        assigned_to=assigned_to,
        assigned_by=creator["user_id"],
        priority=priority,
        reminder_at=reminder_at,
    )
    task = get_task(task_id)
    flow_reset(context)

    await respond(
        target,
        "✅ <b>کار با موفقیت ثبت شد</b>\n\n" + task_card(task),
        reply_markup=task_actions_markup(task_id, can_delete=True),
        parse_mode=ParseMode.HTML,
    )

    # اطلاع‌رسانی به مسئول assigned task
    if assigned_to:
        try:
            await context.bot.send_message(
                chat_id=assigned_to,
                text="📌 <b>کار جدید به شما تخصیص داده شد</b>\n\n" + task_card(task),
                parse_mode=ParseMode.HTML,
                reply_markup=task_actions_markup(task_id),
            )
        except Exception:
            logger.exception("Failed to notify assigned member for task_id=%s", task_id)


# =========================================================
# Reminder loop
# =========================================================
async def check_due_reminders(application: Application) -> None:
    """
    هر 60 ثانیه اجرا می‌شود و یادآوری‌های due شده را از DB می‌خواند.
    این طراحی persistent است چون state reminder در DB نگه‌داری می‌شود.
    """
    due_tasks = get_due_reminders()
    if not due_tasks:
        return

    logger.info("Found %s due reminder(s)", len(due_tasks))

    for task in due_tasks:
        if not task["assigned_to"]:
            mark_reminder_sent(task["id"])
            continue

        text = "🔔 <b>یادآوری کار</b>\n\n" + task_card(task)

        try:
            await application.bot.send_message(
                chat_id=task["assigned_to"],
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=task_actions_markup(task["id"]),
            )
            mark_reminder_sent(task["id"])
        except Exception:
            logger.exception("Failed to send reminder for task_id=%s", task["id"])


# =========================================================
# Startup / Shutdown hooks
# =========================================================
async def post_init(application: Application) -> None:
    init_db()

    # اگر قبلاً webhook ست شده باشد، برای polling حذفش می‌کنیم
    await application.bot.delete_webhook(drop_pending_updates=True)

    # ثبت commandها برای منوی تلگرام
    await application.bot.set_my_commands(
        [
            BotCommand("start", "نمایش منوی اصلی"),
            BotCommand("menu", "نمایش منوی اصلی"),
            BotCommand("tasks", "نمایش کارها"),
            BotCommand("newtask", "ساخت کار جدید"),
            BotCommand("whoami", "نمایش پروفایل من"),
            BotCommand("ai", "پرسش از AI"),
            BotCommand("cancel", "لغو عملیات جاری"),
        ]
    )

    # راه‌اندازی scheduler فقط یک‌بار
    if not scheduler.running:
        scheduler.add_job(
            check_due_reminders,
            "interval",
            seconds=60,
            id="task_reminder_loop",
            replace_existing=True,
            kwargs={"application": application},
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()

    logger.info("Application initialized successfully.")


async def post_shutdown(application: Application) -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)

    if ai_client is not None:
        try:
            await ai_client.close()
        except Exception:
            logger.exception("Failed to close OpenAI client cleanly.")

    logger.info("Application shutdown complete.")


# =========================================================
# command handlers
# =========================================================
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = ensure_user_record(update.effective_user)
    text = (
        "🤖 <b>SAM PRO Team Manager</b>\n\n"
        f"سلام {escape_html(user_row['full_name'])}.\n"
        "همهٔ عملیات اصلی از طریق دکمه‌ها انجام می‌شود."
    )
    await update.message.reply_text(
        text,
        reply_markup=main_menu_markup(),
        parse_mode=ParseMode.HTML,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await menu_command(update, context)


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = ensure_user_record(update.effective_user)
    role_text = "👑 مدیر" if is_admin(user_row) else "👤 عضو"

    text = (
        f"{role_text}\n\n"
        f"نام: {escape_html(user_row['full_name'])}\n"
        f"یوزرنیم: @{escape_html(user_row['username'] or '-')}\n"
        f"شناسه: <code>{user_row['user_id']}</code>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def members_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ensure_user_record(update.effective_user)
    users = get_users()

    if not users:
        await update.message.reply_text("هنوز عضوی ثبت نشده است.")
        return

    lines = ["👥 <b>اعضا</b>", ""]
    for row in users:
        badge = "👑" if row["role"] == ROLE_ADMIN else "👤"
        lines.append(f"{badge} {escape_html(row['full_name'])} — @{escape_html(row['username'] or '-')}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = ensure_user_record(update.effective_user)
    snapshot = get_stats_snapshot(None if is_admin(user_row) else user_row["user_id"])

    text = (
        "📊 <b>آمار</b>\n\n"
        f"کل کارها: <b>{snapshot['total']}</b>\n"
        f"انجام‌شده: <b>{snapshot['done']}</b>\n"
        f"باز: <b>{snapshot['pending']}</b>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def list_tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = ensure_user_record(update.effective_user)
    tasks = get_tasks(None if is_admin(user_row) else user_row["user_id"])

    if not tasks:
        await update.message.reply_text(
            "هیچ کاری ثبت نشده است.",
            reply_markup=main_menu_markup(),
        )
        return

    lines = ["📋 <b>لیست کارها</b>", ""]
    rows = []

    for task in tasks[:20]:
        status_icon = "✅" if task["status"] == STATUS_DONE else "⏳"
        lines.append(
            f"#{task['id']} {status_icon} {escape_html(task['title'])} — {escape_html(task['assigned_to_name'] or '—')}"
        )
        rows.append(
            [InlineKeyboardButton(f"باز کردن #{task['id']}", callback_data=f"task:view:{task['id']}")]
        )

    rows.append([InlineKeyboardButton("➕ کار جدید", callback_data="nav:create")])

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def newtask_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_row = ensure_user_record(update.effective_user)

    if not is_admin(user_row):
        await update.message.reply_text("فقط مدیر می‌تواند کار جدید بسازد.")
        return

    flow_reset(context)
    flow = flow_get(context)
    flow["step"] = "await_title"

    await update.message.reply_text(
        "📝 عنوان کار را بفرست.\n\nبرای لغو: /cancel",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ لغو", callback_data="flow:cancel")]]
        ),
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    flow_reset(context)
    await update.message.reply_text(
        "عملیات لغو شد.",
        reply_markup=main_menu_markup(),
    )


async def ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    مثال:
      /ai سلام
      /ai برای جلسه امروز سه اقدام عملی پیشنهاد بده
    """
    prompt_parts = context.args if context.args else []

    # اگر کاربر روی یک پیام reply کرده و arg نداده، متن همان پیام را بگیر
    if update.message and update.message.reply_to_message and update.message.reply_to_message.text:
        if not prompt_parts:
            prompt_parts = [update.message.reply_to_message.text]

    prompt = " ".join(prompt_parts).strip()

    if not prompt:
        await update.message.reply_text(
            "مثال:\n<code>/ai برای جلسه امروز سه اقدام عملی پیشنهاد بده</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if ai_client is None:
        await update.message.reply_text(
            "OPENAI_API_KEY تنظیم نشده است؛ قابلیت AI فعلاً غیرفعال است."
        )
        return

    waiting = await update.message.reply_text("🤖 در حال پردازش...")

    try:
        response = await ai_client.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
        )
        answer = getattr(response, "output_text", "") or "پاسخی دریافت نشد."
    except Exception as exc:
        logger.exception("OpenAI request failed")
        answer = f"❌ خطا در ارتباط با OpenAI\n\n{exc}"

    try:
        await waiting.edit_text(answer)
    except Exception:
        await update.message.reply_text(answer)


# =========================================================
# callback router
# =========================================================
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_row = ensure_user_record(query.from_user)
    data = query.data or ""

    # -------------------------
    # navigation
    # -------------------------
    if data == "nav:create":
        if not is_admin(user_row):
            await respond(
                query,
                "فقط مدیر می‌تواند کار جدید بسازد.",
                reply_markup=main_menu_markup(),
            )
            return

        flow_reset(context)
        flow = flow_get(context)
        flow["step"] = "await_title"

        await respond(
            query,
            "📝 عنوان کار را در یک پیام متنی بفرست.\n\nبرای لغو: /cancel",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ لغو", callback_data="flow:cancel")]]
            ),
        )
        return

    if data == "nav:list":
        tasks = get_tasks(None if is_admin(user_row) else user_row["user_id"])

        if not tasks:
            await respond(query, "هیچ کاری ثبت نشده است.", reply_markup=main_menu_markup())
            return

        lines = ["📋 <b>لیست کارها</b>", ""]
        rows = []

        for task in tasks[:20]:
            icon = "✅" if task["status"] == STATUS_DONE else "⏳"
            lines.append(
                f"#{task['id']} {icon} {escape_html(task['title'])} — {escape_html(task['assigned_to_name'] or '—')}"
            )
            rows.append(
                [InlineKeyboardButton(f"باز کردن #{task['id']}", callback_data=f"task:view:{task['id']}")]
            )

        rows.append([InlineKeyboardButton("⬅️ منو", callback_data="nav:menu")])

        await respond(
            query,
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "nav:menu":
        await respond(query, "منوی اصلی:", reply_markup=main_menu_markup())
        return

    if data == "nav:members":
        users = get_users()
        lines = ["👥 <b>اعضا</b>", ""]
        for row in users:
            badge = "👑" if row["role"] == ROLE_ADMIN else "👤"
            lines.append(f"{badge} {escape_html(row['full_name'])} — @{escape_html(row['username'] or '-')}")
        await respond(
            query,
            "\n".join(lines),
            reply_markup=main_menu_markup(),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "nav:stats":
        snapshot = get_stats_snapshot(None if is_admin(user_row) else user_row["user_id"])
        text = (
            "📊 <b>آمار</b>\n\n"
            f"کل کارها: <b>{snapshot['total']}</b>\n"
            f"انجام‌شده: <b>{snapshot['done']}</b>\n"
            f"باز: <b>{snapshot['pending']}</b>"
        )
        await respond(query, text, reply_markup=main_menu_markup(), parse_mode=ParseMode.HTML)
        return

    if data == "nav:profile":
        role_text = "👑 مدیر" if is_admin(user_row) else "👤 عضو"
        text = (
            f"{role_text}\n\n"
            f"نام: {escape_html(user_row['full_name'])}\n"
            f"یوزرنیم: @{escape_html(user_row['username'] or '-')}\n"
            f"شناسه: <code>{user_row['user_id']}</code>"
        )
        await respond(query, text, reply_markup=main_menu_markup(), parse_mode=ParseMode.HTML)
        return

    if data == "nav:ai_help":
        text = (
            "🤖 <b>AI</b>\n\n"
            "برای استفاده:\n"
            "<code>/ai خلاصه جلسه امروز را به ۳ کار عملی تبدیل کن</code>"
        )
        await respond(query, text, reply_markup=main_menu_markup(), parse_mode=ParseMode.HTML)
        return

    if data == "flow:cancel":
        flow_reset(context)
        await respond(query, "عملیات لغو شد.", reply_markup=main_menu_markup())
        return

    # -------------------------
    # create flow
    # -------------------------
    if data.startswith("create:member:"):
        if not is_admin(user_row):
            await respond(query, "اجازهٔ این عملیات را ندارید.", reply_markup=main_menu_markup())
            return

        member_id = int(data.split(":")[2])
        flow = flow_get(context)
        flow["assigned_to"] = member_id
        flow["step"] = "await_priority"

        await send_priority_picker(query)
        return

    if data.startswith("create:priority:"):
        if not is_admin(user_row):
            await respond(query, "اجازهٔ این عملیات را ندارید.", reply_markup=main_menu_markup())
            return

        priority = data.split(":")[2]
        flow = flow_get(context)
        flow["priority"] = priority
        flow["step"] = "await_reminder"

        await send_reminder_picker(query)
        return

    if data.startswith("create:reminder:"):
        if not is_admin(user_row):
            await respond(query, "اجازهٔ این عملیات را ندارید.", reply_markup=main_menu_markup())
            return

        reminder_key = data.split(":")[2]
        flow = flow_get(context)

        if reminder_key == "today":
            await finalize_task_creation(query, context, quick_today_reminder())
            return

        if reminder_key == "tomorrow":
            await finalize_task_creation(query, context, quick_tomorrow_reminder())
            return

        if reminder_key == "none":
            await finalize_task_creation(query, context, None)
            return

        if reminder_key == "custom":
            flow["step"] = "await_custom_reminder"
            await respond(
                query,
                "⏰ زمان دلخواه را بفرست.\nمثال:\n<code>2026-06-25 18:00</code>",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ لغو", callback_data="flow:cancel")]]
                ),
                parse_mode=ParseMode.HTML,
            )
            return

    # -------------------------
    # task actions
    # -------------------------
    if data.startswith("task:view:"):
        task_id = int(data.split(":")[2])
        task = get_task(task_id)

        if not task:
            await respond(query, "این کار پیدا نشد.", reply_markup=main_menu_markup())
            return

        if not is_admin(user_row) and task["assigned_to"] != user_row["user_id"]:
            await respond(query, "اجازهٔ مشاهدهٔ این کار را ندارید.", reply_markup=main_menu_markup())
            return

        await respond(
            query,
            task_card(task),
            reply_markup=task_actions_markup(task_id, can_delete=is_admin(user_row)),
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith("task:done:"):
        task_id = int(data.split(":")[2])
        task = get_task(task_id)

        if not task:
            await respond(query, "این کار پیدا نشد.", reply_markup=main_menu_markup())
            return

        if not can_manage_task(user_row, task):
            await respond(
                query,
                "فقط مدیر یا مسئول این کار می‌تواند آن را انجام‌شده کند.",
                reply_markup=main_menu_markup(),
            )
            return

        set_task_status(task_id, STATUS_DONE)
        task = get_task(task_id)

        await respond(
            query,
            "✅ <b>کار انجام شد</b>\n\n" + task_card(task),
            reply_markup=task_actions_markup(task_id, can_delete=is_admin(user_row)),
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith("task:delete_confirm:"):
        task_id = int(data.split(":")[2])

        if not is_admin(user_row):
            await respond(query, "فقط مدیر می‌تواند کار را حذف کند.", reply_markup=main_menu_markup())
            return

        await respond(
            query,
            f"آیا از حذف کار #{task_id} مطمئنی؟",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🗑 بله، حذف کن", callback_data=f"task:delete:{task_id}")],
                    [InlineKeyboardButton("↩️ نه", callback_data=f"task:view:{task_id}")],
                ]
            ),
        )
        return

    if data.startswith("task:delete:"):
        task_id = int
