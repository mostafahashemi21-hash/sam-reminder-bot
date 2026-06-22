from openai import OpenAI
import os
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    CallbackQueryHandler,
    ApplicationHandlerStop,
    filters,
    JobQueue
)

from database import (
    init_db,
    add_user,
    get_user,
    get_users,
    count_admins,
    create_task,
    get_tasks,
    complete_task
)

from datetime import datetime, timedelta

import sqlite3
import json
import re


STATUS_TEXT = {
    "pending": "⏳ باز",
    "in_progress": "🔄 در حال پیگیری",
    "waiting": "⏳ منتظر پاسخ",
    "done": "✅ انجام شد",
    "cancelled": "⛔ لغو شد"
}


async def check_tasks(context: ContextTypes.DEFAULT_TYPE):

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        SELECT id, title, assigned_to, assigned_by, priority, status, reminder_time
        FROM tasks
        WHERE status NOT IN ('done', 'cancelled')
        AND reminder_time IS NOT NULL
        AND reminder_time != 'none'
    """)

    tasks = cur.fetchall()
    conn.close()

    for task in tasks:

        task_id, title, assigned_to, assigned_by, priority, status, reminder_time = task

        try:
            reminder_dt = datetime.strptime(
                reminder_time,
                "%Y-%m-%d %H:%M"
            )
        except:
            continue

        if reminder_dt > datetime.now():
            continue

        status_fa = STATUS_TEXT.get(status, "⏳ باز")

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔄 در حال پیگیری",
                    callback_data=f"task_status:{task_id}:in_progress"
                )
            ],
            [
                InlineKeyboardButton(
                    "⏳ منتظر پاسخ",
                    callback_data=f"task_status:{task_id}:waiting"
                )
            ],
            [
                InlineKeyboardButton(
                    "✅ انجام شد",
                    callback_data=f"task_status:{task_id}:done"
                )
            ],
            [
                InlineKeyboardButton(
                    "⛔ لغو شد",
                    callback_data=f"task_status:{task_id}:cancelled"
                )
            ],
            [
                InlineKeyboardButton(
                    "📂 پرونده کار",
                    callback_data=f"taskmenu:open:{task_id}"
                )
            ]
        ])

        private_text = f"""
⏰ یادآوری کار انجام‌نشده

🆔 شناسه کار:
{task_id}

📌 عنوان:
{title}

🔥 اولویت:
{priority}

📍 وضعیت فعلی:
{status_fa}

لطفاً وضعیت کار را مشخص کن:
"""

        try:
            if assigned_to:
                await context.bot.send_message(
                    chat_id=assigned_to,
                    text=private_text,
                    reply_markup=keyboard
                )

        except Exception as e:
            print(f"Reminder send error for task {task_id}: {e}")

        if GROUP_CHAT_ID:

            group_text = f"""
⏰ یادآوری گروهی کار

👤 مسئول:
<a href="tg://user?id={assigned_to}">مسئول کار</a>

🆔 شناسه کار:
{task_id}

📌 عنوان:
{title}

🔥 اولویت:
{priority}

📍 وضعیت فعلی:
{status_fa}

لطفاً وضعیت این کار مشخص شود.
"""

            try:
                await context.bot.send_message(
                    chat_id=GROUP_CHAT_ID,
                    text=group_text,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
            except Exception as e:
                print(f"Group reminder error for task {task_id}: {e}")

async def task_status_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    data = query.data.split(":")

    if len(data) != 3:
        await query.edit_message_text("❌ دستور نامعتبر است.")
        return

    task_id = int(data[1])
    new_status = data[2]

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        SELECT title, assigned_to, assigned_by, status
        FROM tasks
        WHERE id=?
    """, (task_id,))

    task = cur.fetchone()

    if not task:
        conn.close()
        await query.edit_message_text("❌ این کار پیدا نشد.")
        return

    title, assigned_to, assigned_by, old_status = task

    cur.execute("""
        UPDATE tasks
        SET status=?
        WHERE id=?
    """, (new_status, task_id))

    conn.commit()
    conn.close()

    log_task_history(
        task_id,
        query.from_user.id,
        query.from_user.full_name,
        "status_changed",
        STATUS_TEXT.get(old_status, old_status),
        STATUS_TEXT.get(new_status, new_status)
    )

    status_fa = STATUS_TEXT.get(new_status, new_status)

    await query.edit_message_text(
        f"""
✅ وضعیت کار بروزرسانی شد

🆔 شناسه کار: {task_id}

📌 عنوان:
{title}

📍 وضعیت جدید:
{status_fa}
"""
    )

    if assigned_by and assigned_by != query.from_user.id:
        try:
            await context.bot.send_message(
                chat_id=assigned_by,
                text=f"""
📢 بروزرسانی وضعیت کار

🆔 شناسه کار: {task_id}

📌 عنوان:
{title}

👤 توسط:
{query.from_user.full_name}

📍 وضعیت جدید:
{status_fa}
"""
            )
        except Exception as e:
            print(f"Notify admin error for task {task_id}: {e}")
USER_STATE = {}

TOKEN = os.getenv("BOT_TOKEN")

GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")

if GROUP_CHAT_ID:
    GROUP_CHAT_ID = int(GROUP_CHAT_ID)

print("BOT_TOKEN loaded:", bool(TOKEN), TOKEN[-6:] if TOKEN else "NO TOKEN")

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

CREATE_TITLE = 1
CREATE_MEMBER = 2
CREATE_PRIORITY = 3
CREATE_REMINDER = 4


def db():
    return sqlite3.connect("sam_pro.db")



def init_collaboration_tables():

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS task_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            user_id INTEGER,
            full_name TEXT,
            note_text TEXT,
            source TEXT DEFAULT 'manual',
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS task_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            user_id INTEGER,
            full_name TEXT,
            action TEXT,
            old_value TEXT,
            new_value TEXT,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS task_message_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            chat_id INTEGER,
            message_id INTEGER,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def safe_full_name(user):
    if not user:
        return "کاربر نامشخص"
    return user.full_name or user.username or str(user.id)


def log_task_history(task_id, user_id, full_name, action, old_value="", new_value=""):

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO task_history (
            task_id,
            user_id,
            full_name,
            action,
            old_value,
            new_value,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        task_id,
        user_id,
        full_name,
        action,
        old_value,
        new_value,
        now_text()
    ))

    conn.commit()
    conn.close()


def add_task_note(task_id, user_id, full_name, note_text, source="manual"):

    note_text = str(note_text).strip()

    if not note_text:
        return False

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO task_notes (
            task_id,
            user_id,
            full_name,
            note_text,
            source,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        task_id,
        user_id,
        full_name,
        note_text,
        source,
        now_text()
    ))

    conn.commit()
    conn.close()

    log_task_history(
        task_id,
        user_id,
        full_name,
        "note_added",
        "",
        note_text
    )

    return True


def get_task_notes(task_id, limit=20):

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        SELECT full_name, note_text, source, created_at
        FROM task_notes
        WHERE task_id=?
        ORDER BY id DESC
        LIMIT ?
    """, (task_id, limit))

    rows = cur.fetchall()
    conn.close()

    rows.reverse()

    return rows


def get_task_history_rows(task_id, limit=20):

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        SELECT full_name, action, old_value, new_value, created_at
        FROM task_history
        WHERE task_id=?
        ORDER BY id DESC
        LIMIT ?
    """, (task_id, limit))

    rows = cur.fetchall()
    conn.close()

    rows.reverse()

    return rows


def format_task_notes(task_id):

    notes = get_task_notes(task_id, limit=20)

    if not notes:
        return f"""
📝 شرح کار #{task_id}

هنوز شرحی ثبت نشده.

برای اضافه کردن شرح داخل گروه بنویس:
کار {task_id}: متن شرح
"""

    text = f"📝 شرح کار #{task_id}\n\n"

    for full_name, note_text, source, created_at in notes:
        text += f"""
🕒 {created_at}
👤 {full_name}
▫️ {note_text}
"""

    return text


def format_task_history(task_id):

    rows = get_task_history_rows(task_id, limit=20)

    if not rows:
        return f"""
🧾 تاریخچه کار #{task_id}

هنوز تغییری ثبت نشده.
"""

    action_text = {
        "note_added": "📝 شرح اضافه کرد",
        "status_changed": "📍 وضعیت را تغییر داد",
        "task_created": "➕ کار را ساخت",
        "task_updated": "✏️ کار را ویرایش کرد"
    }

    text = f"🧾 تاریخچه کار #{task_id}\n\n"

    for full_name, action, old_value, new_value, created_at in rows:
        action_fa = action_text.get(action, action)
        text += f"""
🕒 {created_at}
👤 {full_name}
{action_fa}
از: {old_value if old_value else "-"}
به: {new_value if new_value else "-"}
"""

    return text


def extract_task_id_from_any_text(text):

    if not text:
        return None

    text = fa_to_en_digits(str(text)) if "fa_to_en_digits" in globals() else str(text)

    patterns = [
        r"(?:کار|task)\s*#?\s*(\d+)",
        r"#\s*(\d+)"
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))

    return None


def extract_note_for_task(text):

    if not text:
        return None, None

    original_text = text.strip()
    clean_text = fa_to_en_digits(original_text) if "fa_to_en_digits" in globals() else original_text

    task_id = extract_task_id_from_any_text(clean_text)

    if not task_id:
        return None, None

    note_text = original_text

    for separator in [":", "：", "-", "—"]:
        if separator in original_text:
            before, after = original_text.split(separator, 1)
            if extract_task_id_from_any_text(before):
                note_text = after.strip()
                break

    helper_phrases = [
        f"برای کار {task_id} بنویس",
        f"برای کار {task_id} اضافه کن",
        f"به کار {task_id} اضافه کن",
        f"شرح کار {task_id}",
        f"توضیح کار {task_id}",
        f"کار {task_id}"
    ]

    for phrase in helper_phrases:
        if note_text.startswith(phrase):
            note_text = note_text.replace(phrase, "", 1).strip(" :：-—")

    return task_id, note_text.strip()


def detect_status_from_text(text):

    text = str(text).strip()

    done_words = [
        "انجام شد",
        "انجام دادم",
        "تموم شد",
        "تمام شد",
        "کامل شد",
        "فرستادم",
        "ارسال شد"
    ]

    waiting_words = [
        "منتظر پاسخ",
        "منتظر جواب",
        "قرار شد خبر بده",
        "قرار شد جواب بده",
        "فردا خبر میده",
        "فردا جواب میده"
    ]

    progress_words = [
        "در حال پیگیری",
        "دارم پیگیری می‌کنم",
        "دارم پیگیری میکنم",
        "پیگیری می‌کنم",
        "پیگیری میکنم"
    ]

    cancelled_words = [
        "لغو شد",
        "کنسل شد",
        "نیاز نیست",
        "حذف شود"
    ]

    if any(word in text for word in done_words):
        return "done"

    if any(word in text for word in cancelled_words):
        return "cancelled"

    if any(word in text for word in waiting_words):
        return "waiting"

    if any(word in text for word in progress_words):
        return "in_progress"

    return None


def update_task_status_with_history(task_id, new_status, user_id, full_name):

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        SELECT status
        FROM tasks
        WHERE id=?
    """, (task_id,))

    row = cur.fetchone()

    if not row:
        conn.close()
        return False, None

    old_status = row[0]

    cur.execute("""
        UPDATE tasks
        SET status=?, completed_at=?
        WHERE id=?
    """, (
        new_status,
        now_text() if new_status == "done" else None,
        task_id
    ))

    conn.commit()
    conn.close()

    log_task_history(
        task_id,
        user_id,
        full_name,
        "status_changed",
        STATUS_TEXT.get(old_status, old_status),
        STATUS_TEXT.get(new_status, new_status)
    )

    return True, old_status


def create_task_direct(title, assigned_to, assigned_by, priority="🟡 متوسط", reminder_time="none", project="🧩 عمومی", tag="🧩 عمومی", creator_name="کاربر"):

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO tasks (
            title,
            assigned_to,
            assigned_by,
            priority,
            reminder_time,
            created_at,
            project,
            tag
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        title,
        assigned_to,
        assigned_by,
        priority,
        reminder_time,
        now_text(),
        project,
        tag
    ))

    task_id = cur.lastrowid

    conn.commit()
    conn.close()

    log_task_history(
        task_id,
        assigned_by,
        creator_name,
        "task_created",
        "",
        title
    )

    return task_id


def guess_priority_from_text(text):

    if any(word in text for word in ["فوری", "خیلی مهم", "ضروری", "امروز"]):
        return "🔴 زیاد"

    if any(word in text for word in ["کم", "بعدا", "هر وقت"]):
        return "🟢 کم"

    return "🟡 متوسط"


def guess_assignee_from_text(text, fallback_user_id, fallback_name):

    users = get_users()

    for user_id, username, full_name, role in users:
        if full_name and full_name in text:
            return user_id, full_name
        if username and ("@" + username) in text:
            return user_id, full_name
        if full_name:
            first_name = full_name.split()[0]
            if first_name and first_name in text:
                return user_id, full_name

    return fallback_user_id, fallback_name


def extract_new_task_title(text):

    text = str(text).strip()

    prefixes = [
        "کار جدید:",
        "کار جدید：",
        "تسک جدید:",
        "وظیفه جدید:",
        "ربات این کار رو بساز:",
        "ربات، این کار رو بساز:",
        "ربات این کار را بساز:",
        "ربات، این کار را بساز:"
    ]

    for prefix in prefixes:
        if text.startswith(prefix):
            return text.replace(prefix, "", 1).strip()

    return None


def should_ignore_collaboration_text(text):

    ignored = set(SILENT_IGNORE_TEXTS) if "SILENT_IGNORE_TEXTS" in globals() else set()

    ignored.update([
        "📋 کارها",
        "⏱ پیگیری",
        "🧠 تحلیل چت",
        "🤖 دستیار هوشمند",
        "🧠 مدیر هوشمند",
        "👥 اعضا",
        "📊 آمار",
        "👤 پروفایل",
        "➕ کار جدید",
        "🎙 فرمان صوتی",
        "⬅️بازگشت"
    ])

    return text in ignored


async def send_task_group_card(context, chat_id, task_id):

    task = get_task_by_id(task_id)

    if not task:
        return

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=single_task_text(task),
            reply_markup=single_task_keyboard(task_id)
        )
    except Exception as e:
        print(f"Group task card send error for task {task_id}: {e}")


async def collaboration_text_watcher(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message or not update.message.text:
        return

    if update.effective_user and update.effective_user.is_bot:
        return

    text = update.message.text.strip()

    if not text or text.startswith("/"):
        return

    if should_ignore_collaboration_text(text):
        return

    if USER_STATE.get(update.effective_user.id) == "ai_mode":
        return

    await register_user(update)

    user_id = update.effective_user.id
    full_name = safe_full_name(update.effective_user)

    new_task_title = extract_new_task_title(text)

    if new_task_title:

        assigned_to, member_name = guess_assignee_from_text(
            new_task_title,
            user_id,
            full_name
        )

        priority = guess_priority_from_text(new_task_title)

        task_id = create_task_direct(
            title=new_task_title,
            assigned_to=assigned_to,
            assigned_by=user_id,
            priority=priority,
            reminder_time="none"
        )

        log_task_history(
            task_id,
            user_id,
            full_name,
            "task_created",
            "",
            new_task_title
        )

        await update.message.reply_text(
            f"""
✅ کار جدید از پیام گروه ثبت شد

🆔 کار:
{task_id}

📝 عنوان:
{new_task_title}

👤 مسئول:
{member_name}

🔥 اولویت:
{priority}
"""
        )

        return

    reply_task_id = None

    if update.message.reply_to_message:
        reply_text = update.message.reply_to_message.text or update.message.reply_to_message.caption
        reply_task_id = extract_task_id_from_any_text(reply_text)

    task_id, note_text = extract_note_for_task(text)

    if not task_id and reply_task_id:
        task_id = reply_task_id
        note_text = text

    if not task_id:
        return

    task = get_task_by_id(task_id)

    if not task:
        await update.message.reply_text(
            f"❌ کار شماره {task_id} پیدا نشد."
        )
        return

    if note_text:
        add_task_note(
            task_id,
            user_id,
            full_name,
            note_text,
            source="group_message"
        )

    new_status = detect_status_from_text(text)

    status_line = ""

    if new_status:
        ok, old_status = update_task_status_with_history(
            task_id,
            new_status,
            user_id,
            full_name
        )

        if ok:
            status_line = f"\n📍 وضعیت جدید: {STATUS_TEXT.get(new_status, new_status)}"

    await update.message.reply_text(
        f"""
📝 شرح برای کار #{task_id} ثبت شد.{status_line}

📌 متن:
{note_text if note_text else text}
"""
    )

def get_member_id_by_name(name):

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT user_id
        FROM users
        WHERE full_name=?
        """,
        (name,)
    )

    row = cur.fetchone()

    conn.close()

    if row:
        return row[0]

    return None


async def register_user(update: Update):

    tg_user = update.effective_user

    existing = get_user(tg_user.id)

    if existing:
        return

    role = "member"

    if count_admins() == 0:
        role = "admin"

    add_user(
        tg_user.id,
        tg_user.username,
        tg_user.full_name,
        role,
        datetime.now().strftime("%Y-%m-%d %H:%M")
    )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await register_user(update)

    keyboard = [
    ["➕ کار جدید"],
    ["🎙 فرمان صوتی"],
    ["📋 کارها"],
    ["🧠 تحلیل چت"],
    ["🧠 مدیر هوشمند"],
    ["🤖 دستیار هوشمند"],
    ["👥 اعضا"],
    ["📊 آمار"],
    ["👤 پروفایل"],
    ["⏱ پیگیری"]
]
    await update.message.reply_text(
        "🤖 SAM PRO Team Manager",
        reply_markup=ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True
        )
    )


async def whoami(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = get_user(
        update.effective_user.id
    )

    if not user:
        return

    role = (
        "👑 مدیر"
        if user[3] == "admin"
        else "👤 عضو"
    )

    await update.message.reply_text(
        f"""
{role}

نام:
{user[2]}

یوزرنیم:
@{user[1] if user[1] else "-"}
"""
    )


async def members(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    users = get_users()

    text = "👥 اعضا\n\n"

    for user in users:

        role = (
            "👑"
            if user[3] == "admin"
            else "👤"
        )

        text += (
            f"{role} {user[2]}\n"
        )

    await update.message.reply_text(
        text
    )


async def stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    tasks = get_tasks()

    total = len(tasks)

    done_count = len(
        [
            t
            for t in tasks
            if t[4] == "done"
        ]
    )

    pending = total - done_count

    await update.message.reply_text(
        f"""
📊 آمار

کل کارها: {total}

انجام شده: {done_count}

باز: {pending}
"""
    )


async def list_tasks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    tasks = get_tasks()

    if not tasks:

        await update.message.reply_text(
            "هیچ کاری ثبت نشده"
        )

        return

    msg = "📋 لیست کارها\n\n"

    for task in tasks:

        status = (
            "✅"
            if task[4] == "done"
            else "⏳"
        )

        msg += (
            f"{task[0]}. "
            f"{status} "
            f"{task[1]}\n"
        )

    await update.message.reply_text(
        msg
    )
async def create_task_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await register_user(update)

    await update.message.reply_text(
        "📝 عنوان کار را وارد کن:"
    )

    return CREATE_TITLE


async def create_task_title(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data["title"] = (
        update.message.text
    )

    users = get_users()

    keyboard = []

    for user in users:

        keyboard.append(
            [KeyboardButton(user[2])]
        )

    await update.message.reply_text(
        "👤 مسئول انجام کار را انتخاب کن:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )

    return CREATE_MEMBER


async def create_task_member(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data["member"] = (
        update.message.text
    )

    keyboard = [
        ["🔴 زیاد"],
        ["🟡 متوسط"],
        ["🟢 کم"]
    ]

    await update.message.reply_text(
        "اولویت را انتخاب کن:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )

    return CREATE_PRIORITY

async def create_task_priority(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data["priority"] = (
        update.message.text
    )

    keyboard = [
        ["⏰ یک ساعت بعد"],
        ["⏰ دو ساعت بعد"],
        ["🕒 مشخص کردن زمان"],
        ["🚫 بدون یادآوری"]
    ]

    await update.message.reply_text(
        "⏰ زمان یادآوری را انتخاب کن:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )

    return CREATE_REMINDER

async def create_task_reminder(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    reminder_choice = update.message.text

    if context.user_data.get("waiting_custom_reminder"):

        try:
            datetime.strptime(reminder_choice, "%Y-%m-%d %H:%M")
            reminder_time = reminder_choice
            context.user_data.pop("waiting_custom_reminder", None)

        except:
            await update.message.reply_text(
                """
❌ فرمت زمان اشتباه است.

مثال درست:
2026-06-25 18:00
"""
            )
            return CREATE_REMINDER

    elif reminder_choice == "⏰ یک ساعت بعد":

        reminder_time = (
            datetime.now() + timedelta(hours=1)
        ).strftime("%Y-%m-%d %H:%M")

    elif reminder_choice == "⏰ دو ساعت بعد":

        reminder_time = (
            datetime.now() + timedelta(hours=2)
        ).strftime("%Y-%m-%d %H:%M")

    elif reminder_choice == "🕒 مشخص کردن زمان":

        context.user_data["waiting_custom_reminder"] = True

        await update.message.reply_text(
            """
🕒 زمان یادآوری را وارد کن.

مثال:
2026-06-25 18:00
"""
        )

        return CREATE_REMINDER

    elif reminder_choice == "🚫 بدون یادآوری":

        reminder_time = "none"

    else:

        await update.message.reply_text(
            "لطفاً یکی از دکمه‌ها را انتخاب کن."
        )

        return CREATE_REMINDER

    reminder_text = (
        "بدون یادآوری"
        if reminder_time == "none"
        else reminder_time
    )

    title = context.user_data["title"]

    member_name = (
        context.user_data["member"]
    )

    priority = (
        context.user_data["priority"]
    )

    assigned_to = (
        get_member_id_by_name(
            member_name
        )
    )

    task_id = create_task(
        title=title,
        assigned_to=assigned_to,
        assigned_by=update.effective_user.id,
        priority=priority,
        reminder_time=reminder_time,
        created_at=datetime.now().strftime(
            "%Y-%m-%d %H:%M"
        )
    )

    log_task_history(
        task_id,
        update.effective_user.id,
        update.effective_user.full_name,
        "task_created",
        "",
        title
    )

    await update.message.reply_text(
        f"""
✅ کار ثبت شد

🆔 شماره:
{task_id}

عنوان:
{title}

مسئول:
{member_name}

اولویت:
{priority}

یادآوری:
{reminder_text}
"""
    )

    try:

        await context.bot.send_message(
            chat_id=assigned_to,
            text=f"""
📌 کار جدید

عنوان:
{title}

اولویت:
{priority}

زمان یادآوری:
{reminder_text}
"""
        )

    except:

        pass

    if GROUP_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=f"""
📌 کار جدید ثبت شد

🆔 شماره:
{task_id}

📝 عنوان:
{title}

👤 مسئول:
<a href="tg://user?id={assigned_to}">{member_name}</a>

🔥 اولویت:
{priority}

⏰ یادآوری:
{reminder_text}
""",
                parse_mode="HTML",
                reply_markup=single_task_keyboard(task_id)
            )
        except Exception as e:
            print(f"Group new task send error for task {task_id}: {e}")

    return ConversationHandler.END


async def cancel_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "عملیات لغو شد."
    )

    return ConversationHandler.END


async def done_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.args:

        await update.message.reply_text(
            "مثال:\n/done 1"
        )

        return

    try:

        task_id = int(
            context.args[0]
        )

    except:

        await update.message.reply_text(
            "شناسه نامعتبر است."
        )

        return

    complete_task(task_id)

    await update.message.reply_text(
        "✅ کار انجام شد."
    )

async def buttons(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await register_user(update)

    text = update.message.text

    if text == "📋 کارها":
        await open_tasks_panel(update, context)
        return

    if text == "⏱ پیگیری":
        await open_tasks_panel(update, context)
        return

    if text == "🧠 تحلیل چت":

        keyboard = [
            ["⏱ یک ساعت اخیر"],
            ["⏱ دو ساعت اخیر"],
            ["📅 دیروز"],
            ["📊 ۷ روز اخیر"],
            ["⬅️بازگشت"]
        ]

        await update.message.reply_text(
            "🧠 بازه تحلیل چت را انتخاب کن:",
            reply_markup=ReplyKeyboardMarkup(
                keyboard,
                resize_keyboard=True
            )
        )

        return

    if text == "⏱ یک ساعت اخیر":
        await summary_command(update, context, "1h")
        return

    if text == "⏱ دو ساعت اخیر":
        await summary_command(update, context, "2h")
        return

    if text == "📅 دیروز":
        await summary_command(update, context, "yesterday")
        return

    if text == "📊 ۷ روز اخیر":
        await summary_command(update, context, "7d")
        return

    if text == "⬅️بازگشت":
        await start(update, context)
        return

    if text == "🧠 مدیر هوشمند":
        await smart_assistant_command(update, context)
        return

    if text == "🤖 دستیار هوشمند":

        USER_STATE[
            update.effective_user.id
        ] = "ai_mode"

        await update.message.reply_text(
            """
🤖 دستیار هوشمند فعال شد

هر سوالی داری بنویس.

/exit
"""
        )

        return

    if text == "👥 اعضا":
        await members(update, context)
        return

    if text == "📊 آمار":
        await stats(update, context)
        return

    if text == "👤 پروفایل":
        await whoami(update, context)
        return

    if text == "➕ کار جدید":
        await open_task_panel(update, context)
        return

    if "فرمان صوتی" in text or "کار با ویس" in text:

        context.user_data["waiting_voice_task"] = True

        await update.message.reply_text(
            """
🎙 فرمان صوتی فعال شد

حالا یک ویس بفرست.

فرمان‌هایی که می‌فهمم:

📋 لیست کارها را بفرست
📅 کارهای امروز را بگو
📆 کارهای فردا را بگو
⏳ کارهای مانده را بگو
🗑 کار شماره ۱۲ را پاک کن

همچنین:
➕ یک کار جدید بساز
✅ وضعیت کار شماره ۱۲ را انجام‌شده کن
📝 برای کار شماره ۸ توضیح اضافه کن
⏰ یادآوری کار شماره ۵ را تغییر بده
"""
        )

        return
task_conversation = ConversationHandler(

    entry_points=[
        CommandHandler(
            "newtask",
            create_task_start
        )
    ],

    states={

        CREATE_TITLE: [
            MessageHandler(
                filters.TEXT &
                ~filters.COMMAND,
                create_task_title
            )
        ],

        CREATE_MEMBER: [
            MessageHandler(
                filters.TEXT &
                ~filters.COMMAND,
                create_task_member
            )
        ],

        CREATE_PRIORITY: [
            MessageHandler(
                filters.TEXT &
                ~filters.COMMAND,
                create_task_priority
            )
        ],

        CREATE_REMINDER: [
            MessageHandler(
                filters.TEXT &
                ~filters.COMMAND,
                create_task_reminder
            )
        ]
    },

    fallbacks=[
        CommandHandler(
            "cancel",
            cancel_task
        )
    ]
)

async def done_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.args:

        await update.message.reply_text(
            "مثال:\n/done 1"
        )

        return

    try:

        task_id = int(
            context.args[0]
        )

        complete_task(task_id)

        await update.message.reply_text(
            "✅ کار انجام شد"
        )

    except Exception as e:

        await update.message.reply_text(
            f"خطا:\n{e}"
        )


async def ai_command(update, context):

    question = " ".join(context.args)

    if not question:
        await update.message.reply_text(
            "مثال:\n/ai سلام"
        )
        return

    await update.message.reply_text(
        "🤖 در حال پردازش..."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-5",
            messages=[
                {
                    "role": "user",
                    "content": question
                }
            ]
        )

        answer = response.choices[0].message.content

    except Exception as e:
        answer = (
            "❌ خطا در ارتباط با OpenAI\n\n"
            f"{e}"
        )

    await update.message.reply_text(answer)


async def exit_ai(update, context):

    USER_STATE.pop(
        update.effective_user.id,
        None
    )

    await update.message.reply_text(
        "✅ دستیار هوشمند غیرفعال شد"
    )


async def ai_chat(update, context):

    user_id = update.effective_user.id

    if USER_STATE.get(user_id) != "ai_mode":
        return

    question = update.message.text

    try:

        response = client.chat.completions.create(
            model="gpt-5",
            messages=[
                {
                    "role": "user",
                    "content": question
                }
            ]
        )

        answer = response.choices[0].message.content

    except Exception as e:

        answer = f"❌ {e}"

    await update.message.reply_text(answer)
SILENT_IGNORE_TEXTS = {
    "📋 کارها",
    "🤖 دستیار هوشمند",
    "🧠 مدیر هوشمند",
    "👥 اعضا",
    "📊 آمار",
    "👤 پروفایل",
    "➕ کار جدید",
    "⏱ پیگیری",
    "🔴 زیاد",
    "🟡 متوسط",
    "🟢 کم",
    "⏰ یک ساعت بعد",
    "⏰ دو ساعت بعد",
    "🕒 مشخص کردن زمان",
    "🚫 بدون یادآوری"
}


def init_silent_ai_tables():

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            user_id INTEGER,
            full_name TEXT,
            username TEXT,
            text TEXT,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ai_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            title TEXT,
            member_name TEXT,
            assigned_to INTEGER,
            priority TEXT,
            reminder_time TEXT,
            source_text TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ai_task_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            task_id INTEGER,
            proposed_status TEXT,
            note_text TEXT,
            confidence REAL,
            reason TEXT,
            advice TEXT,
            source_text TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


def save_chat_message(update: Update):

    if not update.message or not update.message.text:
        return

    user = update.effective_user
    chat = update.effective_chat

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cutoff = (
        datetime.now() - timedelta(days=7)
    ).strftime("%Y-%m-%d %H:%M")

    cur.execute("""
        DELETE FROM chat_messages
        WHERE created_at < ?
    """, (cutoff,))

    cur.execute("""
        INSERT INTO chat_messages (
            chat_id,
            user_id,
            full_name,
            username,
            text,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        chat.id,
        user.id,
        user.full_name,
        user.username,
        update.message.text,
        datetime.now().strftime("%Y-%m-%d %H:%M")
    ))

    conn.commit()
    conn.close()


def get_recent_chat_messages(chat_id, limit=30):

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        SELECT full_name, text, created_at
        FROM chat_messages
        WHERE chat_id=?
        ORDER BY id DESC
        LIMIT ?
    """, (chat_id, limit))

    rows = cur.fetchall()
    conn.close()

    rows.reverse()

    return rows


def get_admin_ids():

    users = get_users()

    admins = []

    for user in users:
        if user[3] == "admin":
            admins.append(user[0])

    return admins


def extract_json_array(text):

    try:
        return json.loads(text)
    except:
        pass

    try:
        start = text.find("[")
        end = text.rfind("]") + 1

        if start >= 0 and end > start:
            return json.loads(text[start:end])

    except:
        pass

    return []


def save_ai_suggestion(
    chat_id,
    title,
    member_name,
    assigned_to,
    priority,
    reminder_time,
    source_text
):

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        SELECT id
        FROM ai_suggestions
        WHERE title=?
        AND status='pending'
        LIMIT 1
    """, (title,))

    existing = cur.fetchone()

    if existing:
        conn.close()
        return None

    cur.execute("""
        INSERT INTO ai_suggestions (
            chat_id,
            title,
            member_name,
            assigned_to,
            priority,
            reminder_time,
            source_text,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
    """, (
        chat_id,
        title,
        member_name,
        assigned_to,
        priority,
        reminder_time,
        source_text,
        datetime.now().strftime("%Y-%m-%d %H:%M")
    ))

    suggestion_id = cur.lastrowid

    conn.commit()
    conn.close()

    return suggestion_id


def get_ai_suggestion(suggestion_id):

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        SELECT id, chat_id, title, member_name, assigned_to, priority, reminder_time, source_text, status
        FROM ai_suggestions
        WHERE id=?
    """, (suggestion_id,))

    row = cur.fetchone()
    conn.close()

    return row


def update_ai_suggestion_status(suggestion_id, status):

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        UPDATE ai_suggestions
        SET status=?
        WHERE id=?
    """, (status, suggestion_id))

    conn.commit()
    conn.close()


def get_user_display_name(user_id):

    if not user_id:
        return "نامشخص"

    user = get_user(user_id)

    if not user:
        return str(user_id)

    return user[2] or user[1] or str(user_id)


def get_open_tasks_for_ai(limit=25):

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        SELECT id, title, assigned_to, priority, status, reminder_time, created_at, project, tag
        FROM tasks
        WHERE status NOT IN ('done', 'cancelled')
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))

    rows = cur.fetchall()
    conn.close()

    result = []

    for row in rows:
        task_id, title, assigned_to, priority, status, reminder_time, created_at, project, tag = row
        recent_notes = get_task_notes(task_id, limit=3)
        notes_text = " | ".join([note[1] for note in recent_notes]) if recent_notes else ""
        result.append({
            "id": task_id,
            "title": title,
            "assigned_to": assigned_to,
            "assigned_name": get_user_display_name(assigned_to),
            "priority": priority,
            "status": status,
            "status_text": STATUS_TEXT.get(status, status),
            "reminder_time": reminder_time,
            "created_at": created_at,
            "project": project or "🧩 عمومی",
            "tag": tag or "🧩 عمومی",
            "recent_notes": notes_text
        })

    return result


def save_ai_task_update(chat_id, task_id, proposed_status, note_text, confidence, reason, advice, source_text):

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        SELECT id
        FROM ai_task_updates
        WHERE task_id=?
        AND status='pending'
        AND COALESCE(note_text, '')=?
        LIMIT 1
    """, (task_id, note_text or ""))

    existing = cur.fetchone()

    if existing:
        conn.close()
        return None

    cur.execute("""
        INSERT INTO ai_task_updates (
            chat_id,
            task_id,
            proposed_status,
            note_text,
            confidence,
            reason,
            advice,
            source_text,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
    """, (
        chat_id,
        task_id,
        proposed_status,
        note_text,
        confidence,
        reason,
        advice,
        source_text,
        now_text()
    ))

    update_id = cur.lastrowid

    conn.commit()
    conn.close()

    return update_id


def get_ai_task_update(update_id):

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        SELECT id, chat_id, task_id, proposed_status, note_text, confidence, reason, advice, source_text, status, created_at
        FROM ai_task_updates
        WHERE id=?
    """, (update_id,))

    row = cur.fetchone()
    conn.close()

    return row


def update_ai_task_update_status(update_id, status):

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        UPDATE ai_task_updates
        SET status=?
        WHERE id=?
    """, (status, update_id))

    conn.commit()
    conn.close()


def format_ai_update_text(update_id, task_id, proposed_status, note_text, confidence, reason, advice):

    task = get_task_by_id(task_id)
    title = task[1] if task else "کار نامشخص"
    status_fa = STATUS_TEXT.get(proposed_status, proposed_status) if proposed_status and proposed_status != "none" else "بدون تغییر وضعیت"
    confidence_percent = int(float(confidence or 0) * 100)

    return f"""
🧠 پیشنهاد هوشمند برای تکمیل کار

🆔 پیشنهاد:
{update_id}

🆔 کار:
{task_id}

📌 عنوان:
{title}

📍 وضعیت پیشنهادی:
{status_fa}

📝 شرح پیشنهادی:
{note_text if note_text else "-"}

💡 مشورت / پیشنهاد روش درست:
{advice if advice else "-"}

🎯 اطمینان:
{confidence_percent}%

🔎 دلیل:
{reason if reason else "-"}

اعمال شود؟
"""


def ai_update_keyboard(update_id):

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ اعمال کن",
                callback_data=f"aiupdate:{update_id}:apply"
            ),
            InlineKeyboardButton(
                "❌ رد",
                callback_data=f"aiupdate:{update_id}:reject"
            )
        ]
    ])


async def silent_ai_completion_analyze(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    force=False
):

    chat_id = update.effective_chat.id

    messages = get_recent_chat_messages(chat_id, limit=40)
    open_tasks = get_open_tasks_for_ai(limit=25)

    if not messages or not open_tasks:
        if force and update.message:
            await update.message.reply_text(
                "برای تحلیل هوشمند، هم پیام گروه لازم است هم کار باز."
            )
        return

    history = ""
    for full_name, text, created_at in messages:
        history += f"{created_at} | {full_name}: {text}\n"

    tasks_json = json.dumps(
        open_tasks,
        ensure_ascii=False,
        indent=2
    )

    prompt = f"""
تو مدیر هوشمند کارهای تیم هستی.

وظیفه تو:
1. پیام‌های چت را با لیست کارهای باز مقایسه کن.
2. تشخیص بده آیا پیام‌ها نشان می‌دهند کاری انجام شده، در حال پیگیری است یا منتظر پاسخ است.
3. اگر پیام مربوط به یک کار موجود است، برای همان کار یک شرح کوتاه پیشنهاد بده.
4. اگر لازم است، روش درست پیگیری یا مشورت مدیریتی بده.
5. فقط وقتی مطمئن هستی خروجی بده. اگر مطمئن نیستی چیزی نساز.

وضعیت‌های مجاز:
- done
- in_progress
- waiting
- none

خروجی فقط JSON array معتبر باشد. هیچ توضیح اضافه ننویس.

فرمت خروجی:
[
  {{
    "task_id": 12,
    "proposed_status": "done یا in_progress یا waiting یا none",
    "note_text": "شرح کوتاهی که باید به پرونده کار اضافه شود",
    "confidence": 0.85,
    "reason": "چرا این پیام را مربوط به این کار دانستی",
    "advice": "پیشنهاد روش درست پیگیری یا مشورت مدیریتی",
    "source_text": "جمله یا پیام اصلی مرتبط"
  }}
]

کارهای باز:
{tasks_json}

چت اخیر:
{history}
"""

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "تو فقط JSON معتبر خروجی می‌دهی و وضعیت کارهای موجود را از چت تشخیص می‌دهی."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        content = response.choices[0].message.content

    except Exception as e:
        print(f"Smart completion AI error: {e}")
        if force and update.message:
            await update.message.reply_text(
                f"❌ خطا در تحلیل هوشمند: {e}"
            )
        return

    updates = extract_json_array(content)

    if not updates:
        if force and update.message:
            await update.message.reply_text(
                "🧠 تحلیل انجام شد، ولی پیشنهاد قابل اطمینان پیدا نشد."
            )
        return

    created_count = 0

    for item in updates:

        try:
            task_id = int(item.get("task_id"))
        except:
            continue

        if not get_task_by_id(task_id):
            continue

        proposed_status = str(item.get("proposed_status", "none")).strip()

        if proposed_status not in ["done", "in_progress", "waiting", "none"]:
            proposed_status = "none"

        note_text = str(item.get("note_text", "")).strip()
        reason = str(item.get("reason", "")).strip()
        advice = str(item.get("advice", "")).strip()
        source_text = str(item.get("source_text", "")).strip()

        try:
            confidence = float(item.get("confidence", 0))
        except:
            confidence = 0

        if confidence < 0.65:
            continue

        if not note_text and proposed_status == "none" and not advice:
            continue

        update_id = save_ai_task_update(
            chat_id=chat_id,
            task_id=task_id,
            proposed_status=proposed_status,
            note_text=note_text,
            confidence=confidence,
            reason=reason,
            advice=advice,
            source_text=source_text
        )

        if not update_id:
            continue

        target_chat_id = GROUP_CHAT_ID if GROUP_CHAT_ID else chat_id

        try:
            await context.bot.send_message(
                chat_id=target_chat_id,
                text=format_ai_update_text(
                    update_id,
                    task_id,
                    proposed_status,
                    note_text,
                    confidence,
                    reason,
                    advice
                ),
                reply_markup=ai_update_keyboard(update_id)
            )
            created_count += 1
        except Exception as e:
            print(f"Send smart update error: {e}")

    if force and update.message:
        await update.message.reply_text(
            f"🧠 تحلیل هوشمند انجام شد. تعداد پیشنهادها: {created_count}"
        )


async def ai_task_update_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()
    await register_user(update)

    data = query.data.split(":")

    if len(data) != 3:
        await query.edit_message_text("دستور نامعتبر است.")
        return

    update_id = int(data[1])
    action = data[2]

    row = get_ai_task_update(update_id)

    if not row:
        await query.edit_message_text("این پیشنهاد پیدا نشد.")
        return

    (
        uid,
        chat_id,
        task_id,
        proposed_status,
        note_text,
        confidence,
        reason,
        advice,
        source_text,
        status,
        created_at
    ) = row

    if status != "pending":
        await query.edit_message_text("این پیشنهاد قبلاً بررسی شده است.")
        return

    if action == "reject":
        update_ai_task_update_status(update_id, "rejected")
        await query.edit_message_text(
            f"""
❌ پیشنهاد هوشمند رد شد.

🆔 پیشنهاد: {update_id}
🆔 کار: {task_id}
"""
        )
        return

    if action == "apply":

        full_name = query.from_user.full_name
        user_id = query.from_user.id

        if note_text:
            add_task_note(
                task_id,
                user_id,
                full_name,
                note_text,
                source="ai_completion"
            )

        if proposed_status and proposed_status != "none":
            update_task_status_with_history(
                task_id,
                proposed_status,
                user_id,
                full_name
            )

        if advice:
            add_task_note(
                task_id,
                user_id,
                full_name,
                f"مشورت هوشمند: {advice}",
                source="ai_advice"
            )

        update_ai_task_update_status(update_id, "applied")

        task = get_task_by_id(task_id)

        await query.edit_message_text(
            f"""
✅ پیشنهاد هوشمند اعمال شد

🆔 پیشنهاد:
{update_id}

🆔 کار:
{task_id}

📍 وضعیت فعلی:
{STATUS_TEXT.get(task[5], task[5]) if task else "-"}

📝 شرح/مشورت در پرونده کار ثبت شد.
"""
        )


async def smart_assistant_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await register_user(update)

    await update.message.reply_text(
        "🧠 مدیر هوشمند در حال تحلیل چت و کارهای باز است..."
    )

    save_chat_message(update)

    await silent_ai_analyze(update, context)
    await silent_ai_completion_analyze(update, context, force=True)


async def silent_ai_analyze(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_id = update.effective_chat.id

    messages = get_recent_chat_messages(chat_id, limit=30)

    if len(messages) < 3:
        return

    history = ""

    for full_name, text, created_at in messages:
        history += f"{created_at} | {full_name}: {text}\n"

    prompt = f"""
تو دستیار مدیریت کارها هستی.

از متن چت زیر، فقط کارهای واقعی و قابل پیگیری را استخراج کن.
اگر چیزی قطعی نیست، کاری نساز.

خروجی فقط JSON باشد.
هیچ توضیح اضافه ننویس.

فرمت خروجی:
[
  {{
    "title": "عنوان کار",
    "assigned_to": "نام مسئول اگر مشخص بود وگرنه خالی",
    "priority": "🔴 زیاد یا 🟡 متوسط یا 🟢 کم",
    "reminder_time": "none",
    "reason": "جمله‌ای که باعث شد این کار را تشخیص بدهی"
  }}
]

چت:
{history}
"""

    try:

        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "تو فقط JSON معتبر خروجی می‌دهی و کارهای قابل پیگیری را از چت استخراج می‌کنی."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        content = response.choices[0].message.content

    except Exception as e:
        print(f"Silent AI error: {e}")
        return

    tasks = extract_json_array(content)

    if not tasks:
        return

    admins = get_admin_ids()

    if not admins:
        return

    for item in tasks:

        title = str(item.get("title", "")).strip()

        if not title:
            continue

        member_name = str(item.get("assigned_to", "")).strip()

        priority = str(
            item.get("priority", "🟡 متوسط")
        ).strip()

        if priority not in ["🔴 زیاد", "🟡 متوسط", "🟢 کم"]:
            priority = "🟡 متوسط"

        reminder_time = str(
            item.get("reminder_time", "none")
        ).strip()

        reason = str(
            item.get("reason", "")
        ).strip()

        assigned_to = None

        if member_name:
            assigned_to = get_member_id_by_name(member_name)

        suggestion_id = save_ai_suggestion(
            chat_id=chat_id,
            title=title,
            member_name=member_name,
            assigned_to=assigned_to,
            priority=priority,
            reminder_time=reminder_time,
            source_text=reason
        )

        if not suggestion_id:
            continue

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ ثبت کار",
                    callback_data=f"suggestion:{suggestion_id}:approve"
                ),
                InlineKeyboardButton(
                    "❌ رد",
                    callback_data=f"suggestion:{suggestion_id}:reject"
                )
            ]
        ])

        text = f"""
🤖 پیشنهاد کار از چت

📌 عنوان:
{title}

👤 مسئول تشخیص‌داده‌شده:
{member_name if member_name else "نامشخص"}

🔥 اولویت:
{priority}

📝 دلیل:
{reason if reason else "-"}
"""

        target_chats = []

        if GROUP_CHAT_ID:
            target_chats.append(GROUP_CHAT_ID)
        else:
            target_chats.extend(admins)

        for target_chat_id in target_chats:

            try:
                await context.bot.send_message(
                    chat_id=target_chat_id,
                    text=text,
                    reply_markup=keyboard
                )

            except Exception as e:
                print(f"Send suggestion error: {e}")


async def suggestion_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    await register_user(update)

    data = query.data.split(":")

    if len(data) != 3:
        await query.edit_message_text("دستور نامعتبر است.")
        return

    suggestion_id = int(data[1])
    action = data[2]

    suggestion = get_ai_suggestion(suggestion_id)

    if not suggestion:
        await query.edit_message_text("این پیشنهاد پیدا نشد.")
        return

    (
        sid,
        chat_id,
        title,
        member_name,
        assigned_to,
        priority,
        reminder_time,
        source_text,
        status
    ) = suggestion

    if status != "pending":
        await query.edit_message_text("این پیشنهاد قبلاً بررسی شده است.")
        return

    if action == "reject":

        update_ai_suggestion_status(suggestion_id, "rejected")

        await query.edit_message_text(
            f"""
❌ پیشنهاد رد شد

📌 عنوان:
{title}
"""
        )

        return

    if action == "approve":

        if not assigned_to:
            assigned_to = query.from_user.id

        task_id = create_task(
            title=title,
            assigned_to=assigned_to,
            assigned_by=query.from_user.id,
            priority=priority,
            reminder_time=reminder_time,
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M")
        )

        log_task_history(
            task_id,
            query.from_user.id,
            query.from_user.full_name,
            "task_created",
            "پیشنهاد هوشمند",
            title
        )

        if source_text:
            add_task_note(
                task_id,
                query.from_user.id,
                query.from_user.full_name,
                f"از تحلیل چت: {source_text}",
                source="ai_suggestion"
            )

        update_ai_suggestion_status(suggestion_id, "approved")

        await query.edit_message_text(
            f"""
✅ کار ثبت شد

🆔 شماره:
{task_id}

📌 عنوان:
{title}

🔥 اولویت:
{priority}
"""
        )

        try:
            await context.bot.send_message(
                chat_id=assigned_to,
                text=f"""
📌 کار جدید از تحلیل چت

عنوان:
{title}

اولویت:
{priority}
"""
            )
        except:
            pass

        if GROUP_CHAT_ID:
            try:
                await context.bot.send_message(
                    chat_id=GROUP_CHAT_ID,
                    text=f"""
🤖 کار جدید از تحلیل چت ثبت شد

🆔 شماره:
{task_id}

📌 عنوان:
{title}

🔥 اولویت:
{priority}
""",
                    reply_markup=single_task_keyboard(task_id)
                )
            except Exception as e:
                print(f"Group AI task send error for task {task_id}: {e}")


async def silent_message_watcher(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message or not update.message.text:
        return

    if update.effective_user and update.effective_user.is_bot:
        return

    text = update.message.text

    if text in SILENT_IGNORE_TEXTS:
        return

    if USER_STATE.get(update.effective_user.id) == "ai_mode":
        return

    await register_user(update)

    save_chat_message(update)

    counter = context.chat_data.get("silent_counter", 0)
    counter += 1

    context.chat_data["silent_counter"] = counter

    if counter >= 5:
        context.chat_data["silent_counter"] = 0
        await silent_ai_analyze(update, context)
        await silent_ai_completion_analyze(update, context)
def get_chat_messages_between(chat_id, start_time, end_time):

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        SELECT full_name, text, created_at
        FROM chat_messages
        WHERE chat_id=?
        AND created_at >= ?
        AND created_at <= ?
        ORDER BY id ASC
    """, (
        chat_id,
        start_time.strftime("%Y-%m-%d %H:%M"),
        end_time.strftime("%Y-%m-%d %H:%M")
    ))

    rows = cur.fetchall()
    conn.close()

    return rows
def resolve_summary_range(text):

    now = datetime.now()

    text = text.lower()

    if "1h" in text or "یک ساعت" in text or "۱ ساعت" in text:
        return now - timedelta(hours=1), now, "یک ساعت اخیر"

    if "2h" in text or "دو ساعت" in text or "۲ ساعت" in text:
        return now - timedelta(hours=2), now, "دو ساعت اخیر"

    if "yesterday" in text or "دیروز" in text:
        yesterday = now - timedelta(days=1)

        start = yesterday.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

        end = yesterday.replace(
            hour=23,
            minute=59,
            second=0,
            microsecond=0
        )

        return start, end, "دیروز"

    if "7d" in text or "هفته" in text or "۷ روز" in text or "7 روز" in text:
        return now - timedelta(days=7), now, "۷ روز اخیر"

    return now - timedelta(hours=1), now, "یک ساعت اخیر"


async def summary_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    forced=None
):

    chat_id = update.effective_chat.id

    query_text = forced if forced else " ".join(context.args)

    if not query_text:
        query_text = "1h"

    start_time, end_time, label = resolve_summary_range(query_text)

    messages = get_chat_messages_between(
        chat_id,
        start_time,
        end_time
    )

    if not messages:
        await update.message.reply_text(
            f"برای بازه «{label}» پیامی ذخیره نشده."
        )
        return

    history = ""

    for full_name, text, created_at in messages:
        history += f"{created_at} | {full_name}: {text}\n"

    await update.message.reply_text(
        "⏳ در حال خلاصه‌سازی چت..."
    )

    prompt = f"""
چت زیر مربوط به بازه {label} است.

لطفاً خلاصه دقیق و کاربردی بده:

1. خلاصه کلی بحث
2. تصمیم‌های گرفته‌شده
3. کارهای قابل پیگیری
4. مسئول هر کار اگر مشخص است
5. نکات مهم
6. ریسک‌ها یا موارد مبهم

متن چت:
{history}
"""

    try:

        response = client.chat.completions.create(
            model="gpt-5",
            messages=[
                {
                    "role": "system",
                    "content": "تو دستیار خلاصه‌سازی و مدیریت کارها هستی. پاسخ را فارسی، مرتب و خلاصه بده."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        answer = response.choices[0].message.content

    except Exception as e:

        answer = f"❌ خطا در خلاصه‌سازی:\n{e}"

    await update.message.reply_text(answer)    

TASK_DRAFTS = {}


def get_task_draft(user_id):

    if user_id not in TASK_DRAFTS:
        TASK_DRAFTS[user_id] = {
    "title": "",
    "assigned_to": None,
    "member_name": "",
    "priority": "🟡 متوسط",
    "project": "🧩 عمومی",
    "tag": "🧩 عمومی",
    "reminder_time": "none",
    "reminder_text": "بدون یادآوری",
    "panel_chat_id": None,
    "panel_message_id": None
}

    return TASK_DRAFTS[user_id]


def task_panel_text(draft):

    title = draft["title"] if draft["title"] else "ثبت نشده"

    member = (
        f'{draft["member_name"]} | ID: {draft["assigned_to"]}'
        if draft["assigned_to"]
        else "ثبت نشده"
    )

    return f"""
🧾 پنل ایجاد کار

📝 عنوان کار:
{title}

👤 مسئول / پیگیری‌کننده:
{member}

🔥 اولویت:
{draft["priority"]}

🏗 پروژه:
{draft["project"]}

🏷 دسته‌بندی:
{draft["tag"]}

⏰ یادآوری:
{draft["reminder_text"]}
"""



def task_panel_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📝 عنوان کار",
                callback_data="draft:title"
            )
        ],
        [
            InlineKeyboardButton(
                "👤 مسئول",
                callback_data="draft:members"
            )
        ],
        [
            InlineKeyboardButton(
                "🔥 اولویت",
                callback_data="draft:priority_menu"
            )
        ],
        [
            InlineKeyboardButton(
                "🏗 پروژه",
                callback_data="draft:project_menu"
            )
        ],
        [
            InlineKeyboardButton(
                "🏷 دسته‌بندی",
                callback_data="draft:tag_menu"
            )
        ],
        [
            InlineKeyboardButton(
                "⏰ یادآوری",
                callback_data="draft:reminder_menu"
            )
        ],
        [
            InlineKeyboardButton(
                "✅ ثبت کار",
                callback_data="draft:save"
            ),
            InlineKeyboardButton(
                "❌ لغو",
                callback_data="draft:cancel"
            )
        ]
    ])
async def open_task_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await register_user(update)

    user = get_user(update.effective_user.id)

    if not user or user[3] != "admin":
        await update.message.reply_text(
            "فقط مدیر می‌تواند کار ایجاد کند."
        )
        return

    draft = get_task_draft(update.effective_user.id)

    msg = await update.message.reply_text(
        task_panel_text(draft),
        reply_markup=task_panel_keyboard()
    )

    draft["panel_chat_id"] = msg.chat_id
    draft["panel_message_id"] = msg.message_id


async def edit_task_panel(
    context: ContextTypes.DEFAULT_TYPE,
    user_id,
    extra_text=""
):

    draft = get_task_draft(user_id)

    text = task_panel_text(draft)

    if extra_text:
        text += f"\n\n{extra_text}"

    await context.bot.edit_message_text(
        chat_id=draft["panel_chat_id"],
        message_id=draft["panel_message_id"],
        text=text,
        reply_markup=task_panel_keyboard()
    )


async def task_draft_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    draft = get_task_draft(user_id)

    data = query.data

    if data == "draft:title":

        context.user_data["task_draft_waiting"] = "title"

        await query.edit_message_text(
            task_panel_text(draft)
            + "\n\n📝 عنوان کار را در پیام بعدی بنویس:",
            reply_markup=task_panel_keyboard()
        )

        return

    if data == "draft:members":

        users = get_users()

        keyboard = []

        for user in users:
            keyboard.append([
                InlineKeyboardButton(
                    f"{user[2]} | ID: {user[0]}",
                    callback_data=f"draft:member:{user[0]}"
                )
            ])

        keyboard.append([
            InlineKeyboardButton(
                "⬅️ برگشت",
                callback_data="draft:back"
            )
        ])

        await query.edit_message_text(
            "👤 مسئول / پیگیری‌کننده را انتخاب کن:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return

    if data.startswith("draft:member:"):

        selected_id = int(data.split(":")[2])

        users = get_users()

        selected_name = ""

        for user in users:
            if user[0] == selected_id:
                selected_name = user[2]
                break

        draft["assigned_to"] = selected_id
        draft["member_name"] = selected_name

        await query.edit_message_text(
            task_panel_text(draft),
            reply_markup=task_panel_keyboard()
        )

        return

    if data == "draft:priority_menu":

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔴 زیاد",
                    callback_data="draft:priority:🔴 زیاد"
                )
            ],
            [
                InlineKeyboardButton(
                    "🟡 متوسط",
                    callback_data="draft:priority:🟡 متوسط"
                )
            ],
            [
                InlineKeyboardButton(
                    "🟢 کم",
                    callback_data="draft:priority:🟢 کم"
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ برگشت",
                    callback_data="draft:back"
                )
            ]
        ])

        await query.edit_message_text(
            "🔥 اولویت را انتخاب کن:",
            reply_markup=keyboard
        )

        return

    if data.startswith("draft:priority:"):

        priority = data.replace("draft:priority:", "")

        draft["priority"] = priority

        await query.edit_message_text(
            task_panel_text(draft),
            reply_markup=task_panel_keyboard()
        )

        return
    if data == "draft:project_menu":

        keyboard = []

        for i, project in enumerate(PROJECT_OPTIONS):
            keyboard.append([
                InlineKeyboardButton(
                    project,
                    callback_data=f"draft:project:{i}"
                )
            ])

        keyboard.append([
            InlineKeyboardButton(
                "⬅️ برگشت",
                callback_data="draft:back"
            )
        ])

        await query.edit_message_text(
            "🏗 پروژه را انتخاب کن:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return

    if data.startswith("draft:project:"):

        index = int(data.split(":")[2])

        if 0 <= index < len(PROJECT_OPTIONS):
            draft["project"] = PROJECT_OPTIONS[index]

        await query.edit_message_text(
            task_panel_text(draft),
            reply_markup=task_panel_keyboard()
        )

        return

    if data == "draft:tag_menu":

        keyboard = []

        for i, tag in enumerate(TAG_OPTIONS):
            keyboard.append([
                InlineKeyboardButton(
                    tag,
                    callback_data=f"draft:tag:{i}"
                )
            ])

        keyboard.append([
            InlineKeyboardButton(
                "⬅️ برگشت",
                callback_data="draft:back"
            )
        ])

        await query.edit_message_text(
            "🏷 دسته‌بندی را انتخاب کن:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return

    if data.startswith("draft:tag:"):

        index = int(data.split(":")[2])

        if 0 <= index < len(TAG_OPTIONS):
            draft["tag"] = TAG_OPTIONS[index]

        await query.edit_message_text(
            task_panel_text(draft),
            reply_markup=task_panel_keyboard()
        )

        return 

    if data == "draft:reminder_menu":

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⏰ یک ساعت بعد",
                    callback_data="draft:reminder:1h"
                )
            ],
            [
                InlineKeyboardButton(
                    "⏰ دو ساعت بعد",
                    callback_data="draft:reminder:2h"
                )
            ],
            [
                InlineKeyboardButton(
                    "🕒 مشخص کردن زمان",
                    callback_data="draft:reminder:custom"
                )
            ],
            [
                InlineKeyboardButton(
                    "🚫 بدون یادآوری",
                    callback_data="draft:reminder:none"
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ برگشت",
                    callback_data="draft:back"
                )
            ]
        ])

        await query.edit_message_text(
            "⏰ زمان یادآوری را انتخاب کن:",
            reply_markup=keyboard
        )

        return

    if data.startswith("draft:reminder:"):

        reminder = data.split(":")[2]

        if reminder == "1h":

            draft["reminder_time"] = (
                datetime.now() + timedelta(hours=1)
            ).strftime("%Y-%m-%d %H:%M")

            draft["reminder_text"] = "یک ساعت بعد"

        elif reminder == "2h":

            draft["reminder_time"] = (
                datetime.now() + timedelta(hours=2)
            ).strftime("%Y-%m-%d %H:%M")

            draft["reminder_text"] = "دو ساعت بعد"

        elif reminder == "none":

            draft["reminder_time"] = "none"
            draft["reminder_text"] = "بدون یادآوری"

        elif reminder == "custom":

            context.user_data["task_draft_waiting"] = "reminder"

            await query.edit_message_text(
                task_panel_text(draft)
                + """

🕒 زمان یادآوری را در پیام بعدی وارد کن.

مثال:
2026-06-25 18:00
""",
                reply_markup=task_panel_keyboard()
            )

            return

        await query.edit_message_text(
            task_panel_text(draft),
            reply_markup=task_panel_keyboard()
        )

        return

    if data == "draft:back":

        await query.edit_message_text(
            task_panel_text(draft),
            reply_markup=task_panel_keyboard()
        )

        return

    if data == "draft:cancel":

        TASK_DRAFTS.pop(user_id, None)

        await query.edit_message_text(
            "❌ ایجاد کار لغو شد."
        )

        return

    if data == "draft:save":

        if not draft["title"]:
            await query.edit_message_text(
                task_panel_text(draft)
                + "\n\n⚠️ اول عنوان کار را وارد کن.",
                reply_markup=task_panel_keyboard()
            )
            return

        if not draft["assigned_to"]:
            await query.edit_message_text(
                task_panel_text(draft)
                + "\n\n⚠️ اول مسئول / پیگیری‌کننده را انتخاب کن.",
                reply_markup=task_panel_keyboard()
            )
            return

        task_id = create_task(
            title=draft["title"],
            assigned_to=draft["assigned_to"],
            assigned_by=query.from_user.id,
            priority=draft["priority"],
            reminder_time=draft["reminder_time"],
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M")
        )

        conn = sqlite3.connect("sam_pro.db")
        cur = conn.cursor()

        cur.execute("""
            UPDATE tasks
            SET project=?, tag=?
            WHERE id=?
        """, (
            draft["project"],
            draft["tag"],
            task_id
        ))

        conn.commit()
        conn.close()

        log_task_history(
            task_id,
            query.from_user.id,
            query.from_user.full_name,
            "task_created",
            "",
            draft["title"]
        )

        await query.edit_message_text(
            f"""
✅ کار ثبت شد

📝 عنوان:
{draft["title"]}

👤 مسئول:
{draft["member_name"]}

🔥 اولویت:
{draft["priority"]}

🏗 پروژه:
{draft["project"]}

🏷 دسته‌بندی:
{draft["tag"]}

⏰ یادآوری
{draft["reminder_text"]}
"""
        )

        try:
            await context.bot.send_message(
                chat_id=draft["assigned_to"],
                text=f"""
📌 کار جدید

📝 عنوان:
{draft["title"]}

🔥 اولویت
{draft["priority"]}

🏗 پروژه:
{draft["project"]}

🏷 دسته‌بندی:
{draft["tag"]}

⏰ یادآوری
{draft["reminder_text"]}
"""
            )
        except:
            pass

        if GROUP_CHAT_ID:
            try:
                await context.bot.send_message(
                    chat_id=GROUP_CHAT_ID,
                    text=f"""
📌 کار جدید ثبت شد

🆔 شماره:
{task_id}

📝 عنوان:
{draft["title"]}

👤 مسئول:
<a href="tg://user?id={draft["assigned_to"]}">{draft["member_name"]}</a>

🔥 اولویت:
{draft["priority"]}

🏗 پروژه:
{draft["project"]}

🏷 دسته‌بندی:
{draft["tag"]}
""",
                    parse_mode="HTML",
                    reply_markup=single_task_keyboard(task_id)
                )
            except Exception as e:
                print(f"Group new task send error for task {task_id}: {e}")

        TASK_DRAFTS.pop(user_id, None)

        return


async def task_draft_text_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    waiting = context.user_data.get("task_draft_waiting")

    if not waiting:
        return

    user_id = update.effective_user.id
    draft = get_task_draft(user_id)

    text = update.message.text.strip()

    if waiting == "title":

        draft["title"] = text

    elif waiting == "reminder":

        try:
            datetime.strptime(text, "%Y-%m-%d %H:%M")
            draft["reminder_time"] = text
            draft["reminder_text"] = text

        except:
            await update.message.reply_text(
                """
❌ فرمت زمان اشتباه است.

مثال درست:
2026-06-25 18:00
"""
            )
            raise ApplicationHandlerStop

    context.user_data.pop("task_draft_waiting", None)

    try:
        await update.message.delete()
    except:
        pass

    await edit_task_panel(
        context,
        user_id
    )

    raise ApplicationHandlerStop
async def join_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await register_user(update)

    await update.message.reply_text(
        f"""
✅ ثبت شد

👤 نام:
{update.effective_user.full_name}

🆔 Telegram ID:
{update.effective_user.id}

از این به بعد می‌شود این شخص را به‌عنوان مسئول کار انتخاب کرد.
"""
    )
def get_open_tasks_for_panel():

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        SELECT id, title, assigned_to, assigned_by, priority, status, reminder_time, created_at, project, tag
        FROM tasks
        WHERE status NOT IN ('done', 'cancelled')
        ORDER BY id DESC
    """)

    rows = cur.fetchall()
    conn.close()

    return rows


def get_task_by_id(task_id):

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        SELECT id, title, assigned_to, assigned_by, priority, status, reminder_time, created_at, project, tag
        FROM tasks
        WHERE id=?
    """, (task_id,))

    row = cur.fetchone()
    conn.close()

    return row


def task_list_keyboard():

    tasks = get_open_tasks_for_panel()

    keyboard = []

    if not tasks:
        keyboard.append([
            InlineKeyboardButton(
                "✅ کار بازی وجود ندارد",
                callback_data="taskmenu:none"
            )
        ])
    else:
        for task in tasks:
            task_id = task[0]
            title = task[1]
            priority = task[4]
            status = task[5]

            status_fa = STATUS_TEXT.get(status, status)

            short_title = title[:35]

            keyboard.append([
                InlineKeyboardButton(
                    f"#{task_id} | {priority} | {status_fa} | {short_title}",
                    callback_data=f"taskmenu:open:{task_id}"
                )
            ])

    return InlineKeyboardMarkup(keyboard)


async def open_tasks_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "📋 لیست کارهای باز:",
        reply_markup=task_list_keyboard()
    )


def single_task_keyboard(task_id):

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ انجام شد",
                callback_data=f"taskmenu:status:{task_id}:done"
            )
        ],
        [
            InlineKeyboardButton(
                "🔄 در حال پیگیری",
                callback_data=f"taskmenu:status:{task_id}:in_progress"
            )
        ],
        [
            InlineKeyboardButton(
                "⏳ منتظر پاسخ",
                callback_data=f"taskmenu:status:{task_id}:waiting"
            )
        ],
        [
            InlineKeyboardButton(
                "📝 شرح‌ها",
                callback_data=f"taskmenu:notes:{task_id}"
            ),
            InlineKeyboardButton(
                "🧾 تاریخچه",
                callback_data=f"taskmenu:history:{task_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "🗑 حذف از لیست",
                callback_data=f"taskmenu:status:{task_id}:cancelled"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ برگشت به لیست",
                callback_data="taskmenu:list"
            )
        ]
    ])


def single_task_text(task):

    task_id = task[0]
    title = task[1]
    assigned_to = task[2]
    assigned_by = task[3]
    priority = task[4]
    status = task[5]
    reminder_time = task[6]
    created_at = task[7]
    project = task[8] if len(task) > 8 and task[8] else "🧩 عمومی"
    tag = task[9] if len(task) > 9 and task[9] else "🧩 عمومی"

    status_fa = STATUS_TEXT.get(status, status)

    reminder_text = (
        "بدون یادآوری"
        if reminder_time == "none"
        else reminder_time
    )

    notes_count = len(get_task_notes(task_id, limit=100))
    history_count = len(get_task_history_rows(task_id, limit=100))

    return f"""
📌 جزئیات کار

🆔 شناسه:
{task_id}

📝 عنوان:
{title}

👤 مسئول:
{assigned_to}

👨‍💼 ثبت‌کننده:
{assigned_by}

🔥 اولویت:
{priority}

🏗 پروژه:
{project}

🏷 دسته‌بندی:
{tag}

📍 وضعیت:
{status_fa}

⏰ یادآوری:
{reminder_text}

🕒 تاریخ ثبت:
{created_at}

📝 تعداد شرح‌ها:
{notes_count}

🧾 تعداد تغییرات:
{history_count}

برای اضافه کردن شرح داخل گروه بنویس:
کار {task_id}: متن شرح
"""


async def task_menu_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    data = query.data.split(":")

    if query.data == "taskmenu:none":
        return

    if query.data == "taskmenu:list":

        await query.edit_message_text(
            "📋 لیست کارهای باز:",
            reply_markup=task_list_keyboard()
        )

        return

    if len(data) >= 3 and data[1] == "open":

        task_id = int(data[2])

        task = get_task_by_id(task_id)

        if not task:
            await query.edit_message_text(
                "❌ این کار پیدا نشد."
            )
            return

        await query.edit_message_text(
            single_task_text(task),
            reply_markup=single_task_keyboard(task_id)
        )

        return

    if len(data) >= 3 and data[1] == "notes":

        task_id = int(data[2])

        await query.edit_message_text(
            format_task_notes(task_id),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ برگشت به کار",
                        callback_data=f"taskmenu:open:{task_id}"
                    )
                ]
            ])
        )

        return

    if len(data) >= 3 and data[1] == "history":

        task_id = int(data[2])

        await query.edit_message_text(
            format_task_history(task_id),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ برگشت به کار",
                        callback_data=f"taskmenu:open:{task_id}"
                    )
                ]
            ])
        )

        return

    if len(data) >= 4 and data[1] == "status":

        task_id = int(data[2])
        new_status = data[3]

        conn = sqlite3.connect("sam_pro.db")
        cur = conn.cursor()

        cur.execute("""
            SELECT status
            FROM tasks
            WHERE id=?
        """, (task_id,))

        row = cur.fetchone()
        old_status = row[0] if row else ""

        cur.execute("""
            UPDATE tasks
            SET status=?
            WHERE id=?
        """, (
            new_status,
            task_id
        ))

        conn.commit()
        conn.close()

        log_task_history(
            task_id,
            query.from_user.id,
            query.from_user.full_name,
            "status_changed",
            STATUS_TEXT.get(old_status, old_status),
            STATUS_TEXT.get(new_status, new_status)
        )

        if new_status == "cancelled":

            await query.edit_message_text(
                "🗑 کار از لیست حذف شد.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "⬅️ برگشت به لیست",
                            callback_data="taskmenu:list"
                        )
                    ]
                ])
            )

            return

        if new_status == "done":

            await query.edit_message_text(
                "✅ کار انجام‌شده ثبت شد.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "⬅️ برگشت به لیست",
                            callback_data="taskmenu:list"
                        )
                    ]
                ])
            )

            return

        task = get_task_by_id(task_id)

        await query.edit_message_text(
            single_task_text(task),
            reply_markup=single_task_keyboard(task_id)
        )

        return
PROJECT_OPTIONS = [
    "🏗 چوب",
    "💰 مالی",
    "🚚 حمل‌ونقل",
    "📄 قرارداد",
    "📞 مشتری",
    "📦 سفارش",
    "🧩 عمومی"
]


TAG_OPTIONS = [
    "🔥 فوری",
    "📞 تماس",
    "💵 پرداخت",
    "📄 سند",
    "🚚 ارسال",
    "🔍 پیگیری",
    "🧠 تحلیل",
    "🧩 عمومی"
]


def init_task_metadata_columns():

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    try:
        cur.execute("""
            ALTER TABLE tasks
            ADD COLUMN project TEXT DEFAULT '🧩 عمومی'
        """)
    except:
        pass

    try:
        cur.execute("""
            ALTER TABLE tasks
            ADD COLUMN tag TEXT DEFAULT '🧩 عمومی'
        """)
    except:
        pass

    conn.commit()
    conn.close()
def get_daily_report_text():

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    today = datetime.now().strftime("%Y-%m-%d")
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M")

    cur.execute("""
        SELECT COUNT(*)
        FROM tasks
        WHERE status NOT IN ('done', 'cancelled')
    """)
    open_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM tasks
        WHERE status='done'
    """)
    done_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM tasks
        WHERE status='cancelled'
    """)
    cancelled_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM tasks
        WHERE status NOT IN ('done', 'cancelled')
        AND priority LIKE '%زیاد%'
    """)
    high_priority_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM tasks
        WHERE status NOT IN ('done', 'cancelled')
        AND reminder_time != 'none'
        AND reminder_time < ?
    """, (now_text,))
    overdue_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM tasks
        WHERE created_at LIKE ?
    """, (today + "%",))
    today_created_count = cur.fetchone()[0]

    cur.execute("""
        SELECT project, COUNT(*)
        FROM tasks
        WHERE status NOT IN ('done', 'cancelled')
        GROUP BY project
        ORDER BY COUNT(*) DESC
    """)
    project_rows = cur.fetchall()

    cur.execute("""
        SELECT id, title, priority, project, tag, reminder_time
        FROM tasks
        WHERE status NOT IN ('done', 'cancelled')
        ORDER BY id DESC
        LIMIT 5
    """)
    latest_tasks = cur.fetchall()

    conn.close()

    projects_text = ""

    if project_rows:
        for project, count in project_rows:
            projects_text += f"• {project}: {count}\n"
    else:
        projects_text = "موردی ثبت نشده"

    latest_text = ""

    if latest_tasks:
        for task in latest_tasks:
            task_id, title, priority, project, tag, reminder_time = task

            reminder = (
                "بدون یادآوری"
                if reminder_time == "none"
                else reminder_time
            )

            latest_text += f"""
#{task_id} | {priority}
{title}
🏗 {project} | 🏷 {tag}
⏰ {reminder}
"""
    else:
        latest_text = "کاری وجود ندارد."

    return f"""
📊 گزارش روزانه SAM

📅 تاریخ:
{today}

📋 کارهای باز:
{open_count}

🔥 کارهای فوری:
{high_priority_count}

⚠️ کارهای عقب‌افتاده:
{overdue_count}

✅ کل انجام‌شده‌ها:
{done_count}

🗑 حذف‌شده‌ها:
{cancelled_count}

🆕 کارهای ثبت‌شده امروز:
{today_created_count}

🏗 وضعیت پروژه‌ها:
{projects_text}

📌 آخرین کارهای باز:
{latest_text}
"""
async def daily_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.message.reply_text(get_daily_report_text())
async def send_daily_report_job(
    context: ContextTypes.DEFAULT_TYPE
):

    report = get_daily_report_text()

    admin_ids = get_admin_ids()

    sent_targets = []

    if GROUP_CHAT_ID:
        sent_targets.append(GROUP_CHAT_ID)

    sent_targets.extend(admin_ids)

    for target_id in set(sent_targets):

        try:

            await context.bot.send_message(
                chat_id=target_id,
                text=report
            )

        except Exception as e:

            print(f"Daily report send error for {target_id}: {e}")

    if GROUP_CHAT_ID:

        try:

            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=report
            )

        except Exception as e:

            print(f"Daily report group send error: {e}")


def clean_json_text(text):

    text = text.strip()

    if text.startswith("```"):
        text = text.replace("```json", "")
        text = text.replace("```", "")
        text = text.strip()

    return text


def match_option(value, options, default_value):

    if not value:
        return default_value

    for option in options:
        if value in option or option in value:
            return option

    return default_value


async def voice_task_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.user_data.get("waiting_voice_task"):
        return

    await register_user(update)

    user = get_user(update.effective_user.id)

    if not user or user[3] != "admin":
        await update.message.reply_text(
            "فقط مدیر می‌تواند با ویس کار ایجاد کند."
        )
        context.user_data.pop("waiting_voice_task", None)
        return

    if not update.message.voice:
        return

    await update.message.reply_text(
        "🎙 ویس دریافت شد. در حال تبدیل به متن..."
    )

    voice = update.message.voice

    file = await context.bot.get_file(voice.file_id)

    file_path = f"/tmp/voice_task_{update.effective_user.id}.ogg"

    await file.download_to_drive(file_path)
    with open(file_path, "rb") as audio_file:
        transcript_response = client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=audio_file
        )

    transcript = transcript_response.text

    handled = await handle_voice_command(
        update,
        context,
        transcript
    )

    if handled:
        context.user_data.pop("waiting_voice_task", None)
        return
   
    await update.message.reply_text(
        f"""
📝 متن ویس:

{transcript}

در حال ساخت پیش‌نویس کار...
"""
    )

    prompt = f"""
از متن زیر اطلاعات یک کار مدیریتی را استخراج کن.

فقط JSON بده. هیچ توضیح اضافه نده.

فرمت خروجی:
{{
  "title": "عنوان کار",
  "member_name": "نام مسئول اگر گفته شده",
  "priority": "🔴 زیاد یا 🟡 متوسط یا 🟢 کم",
  "project": "یکی از این‌ها: {PROJECT_OPTIONS}",
  "tag": "یکی از این‌ها: {TAG_OPTIONS}",
  "reminder_time": "none",
  "reminder_text": "بدون یادآوری"
}}

قوانین:
- اگر اولویت بالا/فوری/مهم بود، priority را "🔴 زیاد" بگذار.
- اگر اولویت مشخص نبود، "🟡 متوسط" بگذار.
- اگر پروژه مشخص نبود، "🧩 عمومی" بگذار.
- اگر دسته‌بندی مشخص نبود، "🔍 پیگیری" بگذار.
- اگر زمان یادآوری دقیق گفته نشده، reminder_time را "none" بگذار.
- اگر یادآوری گفته نشده، reminder_text را "بدون یادآوری" بگذار.

متن ویس:
{transcript}
"""

    try:

        response = client.chat.completions.create(
            model="gpt-5",
            messages=[
                {
                    "role": "system",
                    "content": "تو دستیار مدیریت کار هستی و فقط JSON معتبر برمی‌گردانی."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        raw_answer = response.choices[0].message.content

        data = json.loads(
            clean_json_text(raw_answer)
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ خطا در استخراج اطلاعات کار:\n{e}"
        )
        context.user_data.pop("waiting_voice_task", None)
        return

    draft = get_task_draft(update.effective_user.id)

    draft["title"] = data.get("title", transcript)

    member_name = data.get("member_name", "")

    assigned_to = None

    if member_name:
        assigned_to = get_member_id_by_name(member_name)

    if assigned_to:
        draft["assigned_to"] = assigned_to
        draft["member_name"] = member_name
    else:
        draft["assigned_to"] = None
        draft["member_name"] = ""

    draft["priority"] = data.get(
        "priority",
        "🟡 متوسط"
    )

    draft["project"] = match_option(
        data.get("project", ""),
        PROJECT_OPTIONS,
        "🧩 عمومی"
    )

    draft["tag"] = match_option(
        data.get("tag", ""),
        TAG_OPTIONS,
        "🔍 پیگیری"
    )

    draft["reminder_time"] = data.get(
        "reminder_time",
        "none"
    )

    draft["reminder_text"] = data.get(
        "reminder_text",
        "بدون یادآوری"
    )

    msg = await update.message.reply_text(
        task_panel_text(draft),
        reply_markup=task_panel_keyboard()
    )

    draft["panel_chat_id"] = msg.chat_id
    draft["panel_message_id"] = msg.message_id

    context.user_data.pop("waiting_voice_task", None)

def fa_to_en_digits(text):

    fa_digits = "۰۱۲۳۴۵۶۷۸۹"
    ar_digits = "٠١٢٣٤٥٦٧٨٩"
    en_digits = "0123456789"

    for i in range(10):
        text = text.replace(fa_digits[i], en_digits[i])
        text = text.replace(ar_digits[i], en_digits[i])

    return text


def extract_task_id_from_text(text):

    text = fa_to_en_digits(text)

    import re

    match = re.search(r"\d+", text)

    if match:
        return int(match.group())

    word_numbers = {
        "یک": 1,
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
        "بیست": 20
    }

    for word, number in word_numbers.items():
        if word in text:
            return number

    return None


def get_voice_tasks_text(mode):

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (
        datetime.now() + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    if mode == "today":

        cur.execute("""
            SELECT id, title, priority, status, project, tag, reminder_time
            FROM tasks
            WHERE status NOT IN ('done', 'cancelled')
            AND (
                created_at LIKE ?
                OR reminder_time LIKE ?
            )
            ORDER BY id DESC
            LIMIT 20
        """, (
            today + "%",
            today + "%"
        ))

        title = "📅 کارهای امروز"

    elif mode == "tomorrow":

        cur.execute("""
            SELECT id, title, priority, status, project, tag, reminder_time
            FROM tasks
            WHERE status NOT IN ('done', 'cancelled')
            AND reminder_time LIKE ?
            ORDER BY id DESC
            LIMIT 20
        """, (
            tomorrow + "%",
        ))

        title = "📅 کارهای فردا"

    else:

        cur.execute("""
            SELECT id, title, priority, status, project, tag, reminder_time
            FROM tasks
            WHERE status NOT IN ('done', 'cancelled')
            ORDER BY id DESC
            LIMIT 20
        """)

        title = "📋 کارهای مانده / باز"

    rows = cur.fetchall()
    conn.close()

    if not rows:
        return f"{title}\n\nکاری پیدا نشد."

    text = f"{title}\n\n"

    for row in rows:

        task_id, task_title, priority, status, project, tag, reminder_time = row

        status_fa = STATUS_TEXT.get(status, status)

        reminder = (
            "بدون یادآوری"
            if reminder_time == "none"
            else reminder_time
        )

        text += f"""
🆔 #{task_id}
📝 {task_title}
🔥 {priority}
📍 {status_fa}
🏗 {project}
🏷 {tag}
⏰ {reminder}

"""

    return text


def cancel_task_by_voice(task_id):

    conn = sqlite3.connect("sam_pro.db")
    cur = conn.cursor()

    cur.execute("""
        SELECT title
        FROM tasks
        WHERE id=?
    """, (task_id,))

    row = cur.fetchone()

    if not row:
        conn.close()
        return None

    title = row[0]

    cur.execute("""
        UPDATE tasks
        SET status='cancelled'
        WHERE id=?
    """, (task_id,))

    conn.commit()
    conn.close()

    return title


async def handle_voice_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    transcript
):

    text = transcript.strip()

    if "پاک کن" in text or "حذف کن" in text:

        task_id = extract_task_id_from_text(text)

        if not task_id:
            await update.message.reply_text(
                "❌ شماره کار را متوجه نشدم. مثلا بگو: کار شماره ۱۲ را پاک کن."
            )
            return True

        title = cancel_task_by_voice(task_id)

        if not title:
            await update.message.reply_text(
                f"❌ کار شماره {task_id} پیدا نشد."
            )
            return True

        await update.message.reply_text(
            f"""
🗑 کار از لیست حذف شد

🆔 شماره:
{task_id}

📝 عنوان:
{title}
"""
        )

        return True

    if "امروز" in text:

        await update.message.reply_text(
            get_voice_tasks_text("today")
        )

        return True

    if "فردا" in text:

        await update.message.reply_text(
            get_voice_tasks_text("tomorrow")
        )

        return True

    remaining_words = [
        "مانده",
        "باقی",
        "باز",
        "انجام نشده"
    ]

    if any(word in text for word in remaining_words):

        await update.message.reply_text(
            get_voice_tasks_text("remaining")
        )

        return True

    list_words = [
        "لیست کار",
        "کارها",
        "کارا",
        "کارارو",
        "کارها رو",
        "کارها را",
        "امور",
        "عمر",
        "بفرست",
        "نمایش بده",
        "نشان بده"
    ]

    if any(word in text for word in list_words):

        await update.message.reply_text(
            get_voice_tasks_text("remaining")
        )

        return True

    return False
async def chatid_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        f"""
🆔 Chat ID:

{update.effective_chat.id}

نوع چت:
{update.effective_chat.type}
"""
    )
init_db()
init_silent_ai_tables()
init_task_metadata_columns()
init_collaboration_tables()

app = (
    Application
    .builder()
    .token(TOKEN)
    .build()
)

job_queue = app.job_queue



app.add_handler(
    CommandHandler(
        "start",
        start
    )
)

app.add_handler(
    CommandHandler(
        "chatid",
        chatid_command
    )
)

app.add_handler(
    CommandHandler(
        "join",
        join_command
    )
)

app.add_handler(
    CommandHandler(
        "whoami",
        whoami
    )
)

app.add_handler(
    CommandHandler(
        "members",
        members
    )
)


app.add_handler(
    CommandHandler(
        "tasks",
        list_tasks
    )
)

app.add_handler(
    CommandHandler(
        "stats",
        stats
    )
)

app.add_handler(
    CommandHandler(
        "dailyreport",
     daily_report_command
    )
)

app.add_handler(
    CommandHandler(
        "summary",
        summary_command
    )
)

app.add_handler(
    CommandHandler(
        "smart",
        smart_assistant_command
    )
)
app.add_handler(
    task_conversation
)

app.add_handler(
    CommandHandler(
        "done",
        done_task
    )
)

app.add_handler(
    CommandHandler(
        "ai",
        ai_command
    )
)

app.add_handler(
    CommandHandler(
        "exit",
        exit_ai
    )
)

app.add_handler(
    CallbackQueryHandler(
        task_status_callback,
        pattern="^task_status:"
    )
)

app.add_handler(
    CallbackQueryHandler(
        suggestion_callback,
        pattern="^suggestion:"
    )
)

app.add_handler(
    CallbackQueryHandler(
        ai_task_update_callback,
        pattern="^aiupdate:"
    )
)

app.add_handler(
    CallbackQueryHandler(
        task_draft_callback,
        pattern="^draft:"
    )
)

app.add_handler(
    CallbackQueryHandler(
        task_menu_callback,
        pattern="^taskmenu:"
    )
)
app.add_handler(
    MessageHandler(
        filters.Regex("^📋 کارها$"),
        buttons
    )
)

app.add_handler(
    MessageHandler(
        filters.Regex("^🤖 دستیار هوشمند$"),
        buttons
    )
)

app.add_handler(
    MessageHandler(
        filters.Regex("^👥 اعضا$"),
        buttons
    )
)

app.add_handler(
    MessageHandler(
        filters.Regex("^📊 آمار$"),
        buttons
    )
)

app.add_handler(
    MessageHandler(
        filters.Regex("^👤 پروفایل$"),
        buttons
    )
)

app.add_handler(
    MessageHandler(
        filters.Regex("^➕ کار جدید$"),
        buttons
    )
)

app.add_handler(
    MessageHandler(
        filters.Regex(".*فرمان صوتی.*"),
        buttons
    )
)

app.add_handler(
    MessageHandler(
        filters.Regex("^⏱ پیگیری$"),
        buttons
    )
)

app.add_handler(
    MessageHandler(
        filters.Regex("^🧠 تحلیل چت$"),
        buttons
    )
)

app.add_handler(
    MessageHandler(
        filters.Regex("^🧠 مدیر هوشمند$"),
        buttons
    )
)

app.add_handler(
    MessageHandler(
        filters.Regex("^⏱ یک ساعت اخیر$"),
        buttons
    )
)

app.add_handler(
    MessageHandler(
        filters.Regex("^⏱ دو ساعت اخیر$"),
        buttons
    )
)

app.add_handler(
    MessageHandler(
        filters.Regex("^📅 دیروز$"),
        buttons
    )
)

app.add_handler(
    MessageHandler(
        filters.Regex("^📊 ۷ روز اخیر$"),
        buttons
    )
)

app.add_handler(
    MessageHandler(
        filters.Regex("^⬅️بازگشت$"),
        buttons
    )
)

app.add_handler(
    MessageHandler(
        filters.VOICE,
        voice_task_handler
    ),
    group=0
)

app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        task_draft_text_input
    ),
    group=0
)

app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        ai_chat
    ),
    group=1
)

app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        silent_message_watcher
    ),
    group=2
)

app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        collaboration_text_watcher
    ),
    group=3
)

if __name__ == "__main__":

    print("SAM PRO Team Manager Started...")

    job_queue = app.job_queue

    job_queue.run_repeating(
        check_tasks,
        interval=3600,
        first=10
    )

    app.job_queue.run_daily(
    send_daily_report_job,
    time=datetime.strptime("07:40", "%H:%M").time(),
    name="daily_report"
)
    app.run_polling(drop_pending_updates=True)
т
