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
import os
import json


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
        SELECT id, title, assigned_to, assigned_by, priority, status
        FROM tasks
        WHERE status NOT IN ('done', 'cancelled')
    """)

    tasks = cur.fetchall()
    conn.close()

    for task in tasks:

        task_id, title, assigned_to, assigned_by, priority, status = task

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
            ]
        ])

        try:
            await context.bot.send_message(
                chat_id=assigned_to,
                text=f"""
⏰ یادآوری کار انجام‌نشده

🆔 شناسه کار: {task_id}

📌 عنوان:
{title}

🔥 اولویت:
{priority}

📍 وضعیت فعلی:
{status_fa}

لطفاً وضعیت کار را مشخص کن:
""",
                reply_markup=keyboard
            )

        except Exception as e:
            print(f"Reminder send error for task {task_id}: {e}")

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

print("BOT_TOKEN loaded:", bool(TOKEN), TOKEN[-6:] if TOKEN else "NO TOKEN")

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

CREATE_TITLE = 1
CREATE_MEMBER = 2
CREATE_PRIORITY = 3
CREATE_REMINDER = 4


def db():
    return sqlite3.connect("sam_pro.db")


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
    ["📋 کارها"],
    ["🧠 تحلیل چت"],
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

    user = get_user(
        update.effective_user.id
    )

    if user[3] != "admin":

        await update.message.reply_text(
            "فقط مدیر می‌تواند کار ایجاد کند."
        )

        return ConversationHandler.END

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

    create_task(
        title=title,
        assigned_to=assigned_to,
        assigned_by=update.effective_user.id,
        priority=priority,
        reminder_time=reminder_time,
        created_at=datetime.now().strftime(
            "%Y-%m-%d %H:%M"
        )
    )

    await update.message.reply_text(
        f"""
✅ کار ثبت شد

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

    elif text == "⏱ پیگیری":

        await open_tasks_panel(update, context)
        return

    elif text == "🧠 تحلیل چت":

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

    elif text == "⏱ یک ساعت اخیر":

        await summary_command(update, context, "1h")
        return

    elif text == "⏱ دو ساعت اخیر":

        await summary_command(update, context, "2h")
        return

    elif text == "📅 دیروز":

        await summary_command(update, context, "yesterday")
        return

    elif text == "📊 ۷ روز اخیر":

        await summary_command(update, context, "7d")
        return

    elif text == "⬅️بازگشت":

        await start(update, context)
        return

    elif text == "🤖 دستیار هوشمند":

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

    elif text == "👥 اعضا":

        await members(update, context)
        return

    elif text == "📊 آمار":

        await stats(update, context)
        return

    elif text == "👤 پروفایل":

        await whoami(update, context)
        return

    elif text == "➕ کار جدید":

        await open_task_panel(update, context)
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
            model="gpt-5",
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

        for admin_id in admins:

            try:
                await context.bot.send_message(
                    chat_id=admin_id,
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

    user = get_user(query.from_user.id)

    if not user or user[3] != "admin":
        await query.edit_message_text(
            "فقط مدیر می‌تواند این پیشنهاد را ثبت یا رد کند."
        )
        return

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

        create_task(
            title=title,
            assigned_to=assigned_to,
            assigned_by=query.from_user.id,
            priority=priority,
            reminder_time=reminder_time,
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M")
        )

        update_ai_suggestion_status(suggestion_id, "approved")

        await query.edit_message_text(
            f"""
✅ کار ثبت شد

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

        create_task(
            title=draft["title"],
            assigned_to=draft["assigned_to"],
            assigned_by=query.from_user.id,
            priority=draft["priority"],
            reminder_time=draft["reminder_time"],
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M")
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

⏰ یادآوری:
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

🔥 اولویت:
{draft["priority"]}

⏰ یادآوری:
{draft["reminder_text"]}
"""
            )
        except:
            pass

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
        SELECT id, title, assigned_to, assigned_by, priority, status, reminder_time, created_at
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
        SELECT id, title, assigned_to, assigned_by, priority, status, reminder_time, created_at
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

    status_fa = STATUS_TEXT.get(status, status)

    reminder_text = (
        "بدون یادآوری"
        if reminder_time == "none"
        else reminder_time
    )

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

📍 وضعیت:
{status_fa}

⏰ یادآوری:
{reminder_text}

🕒 تاریخ ثبت:
{created_at}
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

    if len(data) >= 4 and data[1] == "status":

        task_id = int(data[2])
        new_status = data[3]

        conn = sqlite3.connect("sam_pro.db")
        cur = conn.cursor()

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

init_db()
init_silent_ai_tables()

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
        "summary",
        summary_command
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

if __name__ == "__main__":

    print("SAM PRO Team Manager Started...")

    job_queue = app.job_queue

    job_queue.run_repeating(
        check_tasks,
        interval=3600,
        first=10
    )

    app.run_polling(drop_pending_updates=True)
