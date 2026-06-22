from openai import OpenAI
import os
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile
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
import tempfile


STATUS_TEXT = {
    "pending": "⏳ باز",
    "in_progress": "🔄 در حال پیگیری",
    "waiting": "⏳ منتظر پاسخ",
    "done": "✅ انجام شد",
    "cancelled": "⛔ لغو شد"
}


async def check_tasks(context: ContextTypes.DEFAULT_TYPE):

    """ارسال پیگیری سه‌ساعته به گروه.

    قبلاً این تابع هر ۶۰ ثانیه برای تک‌تک کارهای باز پیام می‌فرستاد
    و باعث اسپم می‌شد. الان فقط یک لیست کلی از کارهای باز می‌فرستد؛
    روی هر کار که زده شود، منوی همان کار باز می‌شود.
    """

    if not GROUP_CHAT_ID:
        print("GROUP_CHAT_ID is not set; 3-hour task follow-up skipped.")
        return

    try:
        tasks = get_open_tasks_for_panel()
    except Exception as e:
        print(f"3-hour task follow-up error: {e}")
        return

    if not tasks:
        try:
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text="✅ پیگیری سه‌ساعته\n\nفعلاً کار بازی در لیست وجود ندارد."
            )
        except Exception as e:
            print(f"3-hour empty follow-up send error: {e}")
        return

    text = f"""
⏱ پیگیری سه‌ساعته کارها

📋 تعداد کارهای باز: {len(tasks)}

روی هر کار بزن تا منوی همان کار باز شود و بتوانی وضعیت را تغییر بدهی.
""".strip()

    try:
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=text,
            reply_markup=task_list_keyboard()
        )
    except Exception as e:
        print(f"3-hour task list send error: {e}")


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

GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")

if GROUP_CHAT_ID:
    try:
        GROUP_CHAT_ID = int(GROUP_CHAT_ID)
    except Exception:
        GROUP_CHAT_ID = None

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
        ["➕ کار جدید", "📋 کارها"],
        ["🧠 تحلیل چت", "🧠 مدیر هوشمند"],
        ["🎙 فرمان صوتی", "🤖 دستیار هوشمند"],
        ["📊 گزارش‌ها", "❓ راهنما"],
        ["👥 اعضا", "👤 پروفایل"],
        ["⏱ پیگیری"]
    ]
    await update.message.reply_text(
        "🤖 SAM PRO Team Manager V5 Real Merge\n\nکد اصلی حفظ شده و امکانات هوشمند اضافه شده است.",
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
            context.user_data.pop("waiting_custom_reminder", None)
            await update.message.reply_text(
                """
❌ فرمت زمان اشتباه است.

مثال درست:
2026-06-25 18:00

حالت تنظیم زمان بسته شد تا ربات روی پیام‌های بعدی گیر نکند.
"""
            )
            return ConversationHandler.END

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

        context.user_data.pop("waiting_custom_reminder", None)
        await update.message.reply_text(
            "❌ انتخاب یادآوری نامعتبر بود. حالت ساخت کار بسته شد. برای ساخت دوباره /newtask را بزن."
        )

        return ConversationHandler.END

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

        create_task(
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
            WHERE id = (
                SELECT id
                FROM tasks
                WHERE title=?
                AND assigned_to=?
                AND assigned_by=?
                ORDER BY id DESC
                LIMIT 1
            )
        """, (
            draft["project"],
            draft["tag"],
            draft["title"],
            draft["assigned_to"],
            query.from_user.id
        ))

        conn.commit()
        conn.close()

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
            context.user_data.pop("task_draft_waiting", None)
            await update.message.reply_text(
                """
❌ فرمت زمان اشتباه است.

مثال درست:
2026-06-25 18:00

حالت تنظیم زمان بسته شد تا ربات روی پیام‌های بعدی گیر نکند.
"""
            )
            return

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

    for admin_id in admin_ids:

        try:

            await context.bot.send_message(
                chat_id=admin_id,
                text=report
            )

        except Exception as e:

            print(f"Daily report send error for {admin_id}: {e}")


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
    

# ===================== V5 REAL MERGE ADDON =====================
# این بخش به آخر فایل اصلی اضافه شده و کد ۳۶۰۰ خطی قبلی را حذف نمی‌کند.

V5_NOTE_WAIT = "v5_wait_note"
V5_CHECK_WAIT = "v5_wait_check"

def v5_now(): return datetime.now().strftime("%Y-%m-%d %H:%M")
def v5_conn(): return sqlite3.connect("sam_pro.db")

def v5_col(cur, table, col):
    cur.execute(f"PRAGMA table_info({table})")
    return any(r[1] == col for r in cur.fetchall())

def v5_add_col(cur, table, col, definition):
    if not v5_col(cur, table, col):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")

def v5_init_db():
    conn=v5_conn(); cur=conn.cursor()
    v5_add_col(cur,"tasks","description","TEXT DEFAULT ''")
    v5_add_col(cur,"tasks","project","TEXT DEFAULT 'عمومی'")
    v5_add_col(cur,"tasks","tag","TEXT DEFAULT 'عمومی'")
    v5_add_col(cur,"tasks","reminder_repeat","TEXT DEFAULT 'none'")
    v5_add_col(cur,"tasks","deleted","INTEGER DEFAULT 0")
    v5_add_col(cur,"tasks","pinned","INTEGER DEFAULT 0")
    cur.execute("CREATE TABLE IF NOT EXISTS task_notes (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER, user_id INTEGER, full_name TEXT, note TEXT, source TEXT, message_id INTEGER, created_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS task_history (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER, user_id INTEGER, full_name TEXT, action TEXT, old_value TEXT, new_value TEXT, created_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS task_checklist (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER, item_text TEXT, is_done INTEGER DEFAULT 0, created_by INTEGER, created_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS task_files (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER, user_id INTEGER, full_name TEXT, file_id TEXT, file_type TEXT, caption TEXT, created_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS task_message_links (chat_id INTEGER, message_id INTEGER, task_id INTEGER, created_at TEXT, PRIMARY KEY(chat_id,message_id))")
    cur.execute("CREATE TABLE IF NOT EXISTS v5_ai_suggestions (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, suggestion_type TEXT, task_id INTEGER, title TEXT, note TEXT, new_status TEXT, priority TEXT, assigned_to INTEGER, assigned_to_name TEXT, reason TEXT, confidence REAL, payload TEXT, status TEXT DEFAULT 'pending', created_at TEXT, decided_by INTEGER, decided_at TEXT)")
    conn.commit(); conn.close()

def v5_task(task_id):
    conn=v5_conn(); cur=conn.cursor()
    cur.execute("SELECT id,title,assigned_to,assigned_by,status,priority,reminder_time,created_at,completed_at,description,project,tag,reminder_repeat,deleted,pinned FROM tasks WHERE id=?",(task_id,))
    r=cur.fetchone(); conn.close()
    if not r: return None
    k=["id","title","assigned_to","assigned_by","status","priority","reminder_time","created_at","completed_at","description","project","tag","reminder_repeat","deleted","pinned"]
    return dict(zip(k,r))

def v5_tasks(open_only=False, limit=50):
    conn=v5_conn(); cur=conn.cursor(); where="COALESCE(deleted,0)=0"
    if open_only: where += " AND status NOT IN ('done','cancelled')"
    cur.execute(f"SELECT id,title,assigned_to,assigned_by,status,priority,reminder_time,created_at,completed_at,description,project,tag,reminder_repeat,deleted,pinned FROM tasks WHERE {where} ORDER BY COALESCE(pinned,0) DESC, id DESC LIMIT ?",(limit,))
    rows=cur.fetchall(); conn.close()
    k=["id","title","assigned_to","assigned_by","status","priority","reminder_time","created_at","completed_at","description","project","tag","reminder_repeat","deleted","pinned"]
    return [dict(zip(k,r)) for r in rows]

def v5_update(task_id, field, value):
    if field not in {"status","priority","reminder_time","completed_at","description","project","tag","reminder_repeat","deleted","pinned","assigned_to","title"}: return
    conn=v5_conn(); cur=conn.cursor(); cur.execute(f"UPDATE tasks SET {field}=? WHERE id=?",(value,task_id)); conn.commit(); conn.close()

def v5_history(task_id,user_id,full_name,action,old='',new=''):
    conn=v5_conn(); cur=conn.cursor(); cur.execute("INSERT INTO task_history (task_id,user_id,full_name,action,old_value,new_value,created_at) VALUES (?,?,?,?,?,?,?)",(task_id,user_id,full_name,action,str(old),str(new),v5_now())); conn.commit(); conn.close()

def v5_note(task_id,user_id,full_name,note,source='manual',message_id=None):
    conn=v5_conn(); cur=conn.cursor(); cur.execute("INSERT INTO task_notes (task_id,user_id,full_name,note,source,message_id,created_at) VALUES (?,?,?,?,?,?,?)",(task_id,user_id,full_name,note,source,message_id,v5_now())); conn.commit(); conn.close()

def v5_notes(task_id,limit=20):
    conn=v5_conn(); cur=conn.cursor(); cur.execute("SELECT created_at,full_name,note,source FROM task_notes WHERE task_id=? ORDER BY id DESC LIMIT ?",(task_id,limit)); rows=cur.fetchall(); conn.close(); return rows

def v5_histories(task_id,limit=30):
    conn=v5_conn(); cur=conn.cursor(); cur.execute("SELECT created_at,full_name,action,old_value,new_value FROM task_history WHERE task_id=? ORDER BY id DESC LIMIT ?",(task_id,limit)); rows=cur.fetchall(); conn.close(); return rows

def v5_link(chat_id,message_id,task_id):
    conn=v5_conn(); cur=conn.cursor(); cur.execute("INSERT OR REPLACE INTO task_message_links (chat_id,message_id,task_id,created_at) VALUES (?,?,?,?)",(chat_id,message_id,task_id,v5_now())); conn.commit(); conn.close()

def v5_task_by_msg(chat_id,message_id):
    conn=v5_conn(); cur=conn.cursor(); cur.execute("SELECT task_id FROM task_message_links WHERE chat_id=? AND message_id=?",(chat_id,message_id)); r=cur.fetchone(); conn.close(); return r[0] if r else None

def v5_status_from_text(t):
    if any(w in t for w in ["انجام شد","انجام دادم","فرستادم","تمام شد","حل شد"]): return "done"
    if any(w in t for w in ["منتظر","جواب","خبر بده","پاسخ"]): return "waiting"
    if any(w in t for w in ["پیگیری","در حال","شروع کردم"]): return "in_progress"
    if any(w in t for w in ["لغو","حذف","کنسل"]): return "cancelled"
    return None

def v5_priority(t):
    if any(w in t for w in ["فوری","ضروری","مهم","بالا","زیاد"]): return "🔴 زیاد"
    if any(w in t for w in ["کم","بعدا","پایین"]): return "🟢 کم"
    return "🟡 متوسط"

def v5_member_name(user_id):
    u=get_user(user_id) if user_id else None
    return (u[2] if u and len(u)>2 and u[2] else str(user_id or "نامشخص"))

def v5_task_text(t):
    st=STATUS_TEXT.get(t['status'],t['status']); rem=t.get('reminder_time') or 'none'
    rem='ندارد' if rem=='none' else rem
    pin='📌 ' if t.get('pinned') else ''
    return f"""📂 پرونده کار #{t['id']}

{pin}📌 عنوان:
{t['title']}

👤 مسئول:
{v5_member_name(t.get('assigned_to'))}

📍 وضعیت:
{st}

🔥 اولویت:
{t.get('priority') or '🟡 متوسط'}

🏗 پروژه: {t.get('project') or 'عمومی'}
🏷 دسته: {t.get('tag') or 'عمومی'}
⏰ یادآوری: {rem}

📝 توضیح:
{t.get('description') or '-'}"""

def v5_keyboard(task_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ انجام شد",callback_data=f"v5:status:{task_id}:done"), InlineKeyboardButton("🔄 پیگیری",callback_data=f"v5:status:{task_id}:in_progress")],
        [InlineKeyboardButton("⏳ منتظر",callback_data=f"v5:status:{task_id}:waiting"), InlineKeyboardButton("⛔ لغو",callback_data=f"v5:status:{task_id}:cancelled")],
        [InlineKeyboardButton("📝 شرح‌ها",callback_data=f"v5:notes:{task_id}"), InlineKeyboardButton("➕ شرح",callback_data=f"v5:addnote:{task_id}")],
        [InlineKeyboardButton("☑️ چک‌لیست",callback_data=f"v5:check:{task_id}"), InlineKeyboardButton("➕ چک‌لیست",callback_data=f"v5:addcheck:{task_id}")],
        [InlineKeyboardButton("📎 فایل‌ها",callback_data=f"v5:files:{task_id}"), InlineKeyboardButton("🧾 تاریخچه",callback_data=f"v5:history:{task_id}")],
        [InlineKeyboardButton("📌 پین",callback_data=f"v5:pin:{task_id}"), InlineKeyboardButton("🗑 حذف",callback_data=f"v5:delete:{task_id}")]
    ])

async def v5_send_card(context,chat_id,task_id):
    t=v5_task(task_id)
    if not t: return
    msg=await context.bot.send_message(chat_id=chat_id,text=v5_task_text(t),reply_markup=v5_keyboard(task_id),parse_mode="HTML")
    v5_link(chat_id,msg.message_id,task_id)

async def v5_add_note_reply(update,task_id,note,source='manual'):
    t=v5_task(task_id)
    if not t:
        await update.message.reply_text("❌ کار پیدا نشد."); return
    v5_note(task_id,update.effective_user.id,update.effective_user.full_name,note,source,update.message.message_id)
    v5_history(task_id,update.effective_user.id,update.effective_user.full_name,"add_note","",note)
    status=v5_status_from_text(note)
    if status:
        old=t['status']; v5_update(task_id,'status',status)
        if status=='done': v5_update(task_id,'completed_at',v5_now())
        v5_history(task_id,update.effective_user.id,update.effective_user.full_name,"auto_status",old,status)
        await update.message.reply_text(f"📝 شرح ثبت شد و وضعیت کار #{task_id} به {STATUS_TEXT.get(status,status)} تغییر کرد.")
    else:
        await update.message.reply_text(f"📝 شرح برای کار #{task_id} ثبت شد.")

async def v5_create_from_text(update,context,raw):
    assigned=update.effective_user.id; name=update.effective_user.full_name
    for u in get_users():
        uid, username, full_name, role = u[:4]
        if (full_name and full_name.split()[0] in raw) or (username and '@'+username in raw):
            assigned=uid; name=full_name; break
    title=re.sub(r"^(کار جدید|تسک جدید|وظیفه جدید)\s*[:：\-]?\s*",'',raw).strip()[:180]
    conn=v5_conn(); cur=conn.cursor()
    cur.execute("INSERT INTO tasks (title,assigned_to,assigned_by,priority,reminder_time,created_at,description) VALUES (?,?,?,?,?,?,?)",(title,assigned,update.effective_user.id,v5_priority(raw),'none',v5_now(),f"ثبت از پیام: {raw}"))
    task_id=cur.lastrowid; conn.commit(); conn.close()
    v5_history(task_id,update.effective_user.id,update.effective_user.full_name,'create','',title)
    v5_note(task_id,update.effective_user.id,update.effective_user.full_name,f"متن اولیه: {raw}",'auto_create',update.message.message_id)
    await update.message.reply_text(f"✅ کار جدید ثبت شد\n\n🆔 #{task_id}\n📌 {title}\n👤 مسئول: {name}")
    await v5_send_card(context,update.effective_chat.id,task_id)

async def v5_tasks_cmd(update,context):
    await register_user(update)
    tasks=v5_tasks(False,30)
    if not tasks: await update.message.reply_text("📋 کاری ثبت نشده."); return
    await update.message.reply_text("📋 لیست کارها")
    for t in tasks[:12]: await v5_send_card(context,update.effective_chat.id,t['id'])

async def v5_help_cmd(update,context):
    await update.message.reply_text("""❓ راهنمای V5 Real Merge

کار جدید: رضا فردا قیمت چوب روسیه را پیگیری کند
کار 12: مشتری گفت فردا خبر می‌دهد
کار 12 انجام شد
/smart
/summary
/export_excel
/export_pdf""", reply_markup=ReplyKeyboardMarkup([["➕ کار جدید","📋 کارها"],["🧠 تحلیل چت","🧠 مدیر هوشمند"],["🎙 فرمان صوتی","🤖 دستیار هوشمند"],["📊 گزارش‌ها","❓ راهنما"],["👥 اعضا","👤 پروفایل"],["⏱ پیگیری"]],resize_keyboard=True))

async def v5_callback(update,context):
    q=update.callback_query; await q.answer(); parts=q.data.split(':')
    if parts[0]!='v5': return
    action=parts[1]; task_id=int(parts[2]); t=v5_task(task_id)
    if not t: await q.message.reply_text("❌ کار پیدا نشد."); raise ApplicationHandlerStop
    if action=='status':
        new=parts[3]; old=t['status']; v5_update(task_id,'status',new)
        if new=='done': v5_update(task_id,'completed_at',v5_now())
        v5_history(task_id,q.from_user.id,q.from_user.full_name,'status',old,new)
        v5_note(task_id,q.from_user.id,q.from_user.full_name,f"وضعیت تغییر کرد: {STATUS_TEXT.get(old,old)} → {STATUS_TEXT.get(new,new)}",'button')
        await q.edit_message_text(v5_task_text(v5_task(task_id)),reply_markup=v5_keyboard(task_id),parse_mode='HTML')
    elif action=='notes':
        rows=v5_notes(task_id,30); text=f"📝 شرح‌های کار #{task_id}\n\n"+("شرحی ثبت نشده." if not rows else "\n\n".join(f"{r[0]} | {r[1]}:\n{r[2]}" for r in rows))
        await q.message.reply_text(text)
    elif action=='addnote':
        context.user_data[V5_NOTE_WAIT]=task_id; await q.message.reply_text(f"📝 شرح جدید برای کار #{task_id} را بنویس:")
    elif action=='history':
        rows=v5_histories(task_id,30); text=f"🧾 تاریخچه کار #{task_id}\n\n"+("تاریخچه‌ای ثبت نشده." if not rows else "\n".join(f"{r[0]} | {r[1]} | {r[2]}: {r[3]} → {r[4]}" for r in rows))
        await q.message.reply_text(text)
    elif action=='pin':
        v5_update(task_id,'pinned',0 if t.get('pinned') else 1); await q.edit_message_text(v5_task_text(v5_task(task_id)),reply_markup=v5_keyboard(task_id),parse_mode='HTML')
    elif action=='delete':
        v5_update(task_id,'deleted',1); v5_update(task_id,'status','cancelled'); v5_history(task_id,q.from_user.id,q.from_user.full_name,'delete','','deleted'); await q.edit_message_text(f"🗑 کار #{task_id} حذف/لغو شد.")
    else:
        await q.message.reply_text("این گزینه در نسخه بعدی تکمیل می‌شود.")
    raise ApplicationHandlerStop

async def v5_text(update,context):
    if not update.message or not update.message.text: return
    await register_user(update); text=update.message.text.strip()
    if context.user_data.get(V5_NOTE_WAIT):
        tid=context.user_data.pop(V5_NOTE_WAIT); await v5_add_note_reply(update,tid,text,'button'); raise ApplicationHandlerStop
    if text in ['🧠 مدیر هوشمند','مدیر هوشمند']:
        await v5_smart_cmd(update,context); raise ApplicationHandlerStop
    if text in ['❓ راهنما','راهنما']:
        await v5_help_cmd(update,context); raise ApplicationHandlerStop
    if text in ['📋 کارها','لیست کارها']:
        await v5_tasks_cmd(update,context); raise ApplicationHandlerStop
    if text in ['📊 گزارش‌ها','گزارش‌ها']:
        await update.message.reply_text('📊 گزارش‌ها',reply_markup=ReplyKeyboardMarkup([["📊 آمار","📅 گزارش روزانه"],["📤 خروجی اکسل","📄 خروجی PDF"],["⬅️بازگشت"]],resize_keyboard=True)); raise ApplicationHandlerStop
    if text=='📤 خروجی اکسل': await v5_export_excel_cmd(update,context); raise ApplicationHandlerStop
    if text=='📄 خروجی PDF': await v5_export_pdf_cmd(update,context); raise ApplicationHandlerStop
    rid=update.message.reply_to_message.message_id if update.message.reply_to_message else None
    if rid:
        tid=v5_task_by_msg(update.effective_chat.id,rid)
        if tid: await v5_add_note_reply(update,tid,text,'reply'); raise ApplicationHandlerStop
    m=re.search(r"کار\s*([0-9۰-۹٠-٩]+)\s*[:：\-]\s*(.+)",text)
    if m: await v5_add_note_reply(update,int(fa_to_en_digits(m.group(1))),m.group(2).strip(),'group_pattern'); raise ApplicationHandlerStop
    m=re.search(r"کار\s*([0-9۰-۹٠-٩]+).*(انجام|تمام|لغو|منتظر|پیگیری|در حال)",text)
    if m:
        tid=int(fa_to_en_digits(m.group(1))); st=v5_status_from_text(text) or 'in_progress'
        fake=type('X',(),{})(); await v5_set_status_from_text(update,context,tid,st); raise ApplicationHandlerStop
    if text.startswith(('کار جدید','تسک جدید','وظیفه جدید')):
        raw=re.sub(r"^(کار جدید|تسک جدید|وظیفه جدید)\s*[:：\-]?\s*",'',text).strip(); await v5_create_from_text(update,context,raw); raise ApplicationHandlerStop

async def v5_set_status_from_text(update,context,task_id,status):
    t=v5_task(task_id)
    if not t: await update.message.reply_text('❌ کار پیدا نشد.'); return
    old=t['status']; v5_update(task_id,'status',status)
    if status=='done': v5_update(task_id,'completed_at',v5_now())
    v5_history(task_id,update.effective_user.id,update.effective_user.full_name,'status_text',old,status)
    await update.message.reply_text(f"✅ وضعیت کار #{task_id} شد: {STATUS_TEXT.get(status,status)}")

async def v5_file(update,context):
    if not update.message: return
    rid=update.message.reply_to_message.message_id if update.message.reply_to_message else None
    if not rid: return
    tid=v5_task_by_msg(update.effective_chat.id,rid)
    if not tid: return
    file_id=file_type=None
    if update.message.document: file_id=update.message.document.file_id; file_type='document'
    elif update.message.photo: file_id=update.message.photo[-1].file_id; file_type='photo'
    elif update.message.video: file_id=update.message.video.file_id; file_type='video'
    elif update.message.audio: file_id=update.message.audio.file_id; file_type='audio'
    if file_id:
        conn=v5_conn(); cur=conn.cursor(); cur.execute("INSERT INTO task_files (task_id,user_id,full_name,file_id,file_type,caption,created_at) VALUES (?,?,?,?,?,?,?)",(tid,update.effective_user.id,update.effective_user.full_name,file_id,file_type,update.message.caption or '',v5_now())); conn.commit(); conn.close()
        v5_history(tid,update.effective_user.id,update.effective_user.full_name,'add_file','',file_type); await update.message.reply_text(f"📎 فایل داخل پرونده کار #{tid} ذخیره شد."); raise ApplicationHandlerStop

async def v5_export_excel_cmd(update,context):
    try:
        import openpyxl
        wb=openpyxl.Workbook(); ws=wb.active; ws.append(['ID','Title','Status','Priority','Project','Tag','Reminder','Created'])
        for t in v5_tasks(False,10000): ws.append([t['id'],t['title'],STATUS_TEXT.get(t['status'],t['status']),t['priority'],t['project'],t['tag'],t['reminder_time'],t['created_at']])
        path=tempfile.mktemp(suffix='.xlsx'); wb.save(path); await update.message.reply_document(document=open(path,'rb'),filename='sam_tasks_report.xlsx')
    except Exception as e: await update.message.reply_text(f"خطای خروجی اکسل: {e}")

async def v5_export_pdf_cmd(update,context):
    path=tempfile.mktemp(suffix='.txt')
    with open(path,'w',encoding='utf-8') as f:
        f.write('SAM PRO Team Manager Report\n\n')
        for t in v5_tasks(False,500): f.write(f"#{t['id']} | {t['status']} | {t['priority']} | {t['title']}\n")
    await update.message.reply_document(document=open(path,'rb'),filename='sam_tasks_report.txt')

async def v5_smart_cmd(update,context):
    if not client:
        await update.message.reply_text('OPENAI_API_KEY تنظیم نشده.'); return
    await update.message.reply_text('🧠 مدیر هوشمند در حال بررسی چت و کارهای باز است...')
    # از تحلیل قبلی موجود در فایل اصلی هم استفاده می‌کنیم تا چیزی حذف نشود
    try:
        await silent_ai_analyze(update, context)
    except Exception as e:
        await update.message.reply_text(f'تحلیل هوشمند ساده اجرا شد، ولی خطای بخش قدیمی: {e}')



# =================== V5.2 COMMAND + STUCK STATE FIX ===================

def v52_clear_waiting_states(context):
    """پاک کردن حالت‌های نیمه‌کاره تا هر پیام معمولی به خطای زمان تبدیل نشود."""
    for key in [
        "waiting_custom_reminder",
        "task_draft_waiting",
        "waiting_note_task_id",
        "waiting_check_task_id",
        V5_NOTE_WAIT,
    ]:
        try:
            context.user_data.pop(key, None)
        except Exception:
            pass


def v52_parse_reminder_text(raw: str):
    raw = (raw or "").strip()
    raw_l = raw.lower()
    now = datetime.now()

    if raw_l in ["none", "no", "off", "disable"] or raw in ["بدون", "بدون یادآوری", "حذف", "خاموش"]:
        return "none"

    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s+(\d{1,2})[:.](\d{2})", fa_to_en_digits(raw))
    if m:
        y, mo, d, h, mi = map(int, m.groups())
        return datetime(y, mo, d, h, mi).strftime("%Y-%m-%d %H:%M")

    hm = re.search(r"(\d{1,2})[:.](\d{2})", fa_to_en_digits(raw))
    if "فردا" in raw:
        base = now + timedelta(days=1)
        if hm:
            return base.replace(hour=int(hm.group(1)), minute=int(hm.group(2)), second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
        return base.replace(hour=10, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
    if "امروز" in raw:
        if hm:
            return now.replace(hour=int(hm.group(1)), minute=int(hm.group(2)), second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
        return now.strftime("%Y-%m-%d %H:%M")
    if "یک ساعت" in raw:
        return (now + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
    if "دو ساعت" in raw:
        return (now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
    if "سه ساعت" in raw:
        return (now + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M")

    return None


async def v52_start_cmd(update, context):
    v52_clear_waiting_states(context)
    await start(update, context)
    raise ApplicationHandlerStop


async def v52_help_cmd(update, context):
    v52_clear_waiting_states(context)
    await update.message.reply_text(
        """❓ راهنمای سریع SAM PRO

📋 لیست کارها:
/tasks

✅ انجام کار:
/done 1
یا: کار 1 انجام شد

🗑 حذف کار:
/delete 1

⏰ تنظیم یادآوری:
/remind 1 2026-06-25 18:00
/remind 1 فردا 10:00
/remind 1 none

🧠 تحلیل چت:
/summary

🧠 مدیر هوشمند:
/smart

📤 خروجی:
/export_excel
/export_pdf""",
        reply_markup=ReplyKeyboardMarkup(
            [["➕ کار جدید", "📋 کارها"], ["🧠 تحلیل چت", "🧠 مدیر هوشمند"], ["📊 آمار", "👥 اعضا"], ["👤 پروفایل", "❓ راهنما"]],
            resize_keyboard=True,
        ),
    )
    raise ApplicationHandlerStop


async def v52_tasks_panel_cmd(update, context):
    v52_clear_waiting_states(context)
    await update.message.reply_text(
        "📋 لیست کارهای باز:\n\nروی هر کار بزن تا منوی همان کار باز شود.",
        reply_markup=task_list_keyboard(),
    )
    raise ApplicationHandlerStop


async def v52_today_cmd(update, context):
    v52_clear_waiting_states(context)
    today = datetime.now().strftime("%Y-%m-%d")
    tasks = []
    for t in v5_tasks(False, 200):
        if str(t.get("created_at") or "").startswith(today) or str(t.get("reminder_time") or "").startswith(today):
            tasks.append(t)
    if not tasks:
        await update.message.reply_text("📅 برای امروز کاری پیدا نشد.")
    else:
        await update.message.reply_text("📅 کارهای امروز")
        for t in tasks[:15]:
            await v5_send_card(context, update.effective_chat.id, t["id"])
    raise ApplicationHandlerStop


async def v52_done_cmd(update, context):
    v52_clear_waiting_states(context)
    if not context.args:
        await update.message.reply_text("مثال درست:\n/done 1")
        raise ApplicationHandlerStop
    task_id = extract_task_id(" ".join(context.args))
    t = v5_task(task_id) if task_id else None
    if not t:
        await update.message.reply_text("❌ کار پیدا نشد. شماره کار را بفرست. مثال: /done 1")
        raise ApplicationHandlerStop
    old = t["status"]
    v5_update(task_id, "status", "done")
    v5_update(task_id, "completed_at", v5_now())
    v5_history(task_id, update.effective_user.id, update.effective_user.full_name, "done_command", old, "done")
    await update.message.reply_text(f"✅ کار #{task_id} انجام‌شده شد.")
    raise ApplicationHandlerStop


async def v52_delete_cmd(update, context):
    v52_clear_waiting_states(context)
    if not context.args:
        await update.message.reply_text("مثال درست:\n/delete 1")
        raise ApplicationHandlerStop
    task_id = extract_task_id(" ".join(context.args))
    t = v5_task(task_id) if task_id else None
    if not t:
        await update.message.reply_text("❌ کار پیدا نشد. شماره کار را بفرست. مثال: /delete 1")
        raise ApplicationHandlerStop
    v5_update(task_id, "deleted", 1)
    v5_update(task_id, "status", "cancelled")
    v5_history(task_id, update.effective_user.id, update.effective_user.full_name, "delete_command", "", "deleted")
    await update.message.reply_text(f"🗑 کار #{task_id} حذف/لغو شد.")
    raise ApplicationHandlerStop


async def v52_remind_cmd(update, context):
    v52_clear_waiting_states(context)
    if len(context.args) < 2:
        await update.message.reply_text(
            """⏰ برای تنظیم یادآوری اینطوری بزن:

/remind 1 2026-06-25 18:00
/remind 1 فردا 10:00
/remind 1 امروز 22:30
/remind 1 سه ساعت بعد
/remind 1 none"""
        )
        raise ApplicationHandlerStop

    task_id = extract_task_id(context.args[0])
    t = v5_task(task_id) if task_id else None
    if not t:
        await update.message.reply_text("❌ کار پیدا نشد. مثال: /remind 1 2026-06-25 18:00")
        raise ApplicationHandlerStop

    raw_time = " ".join(context.args[1:])
    reminder_time = v52_parse_reminder_text(raw_time)
    if reminder_time is None:
        await update.message.reply_text("❌ فرمت زمان اشتباه است. مثال درست:\n/remind 1 2026-06-25 18:00")
        raise ApplicationHandlerStop

    old = t.get("reminder_time")
    v5_update(task_id, "reminder_time", reminder_time)
    v5_history(task_id, update.effective_user.id, update.effective_user.full_name, "remind_command", old, reminder_time)
    shown = "بدون یادآوری" if reminder_time == "none" else reminder_time
    await update.message.reply_text(f"⏰ یادآوری کار #{task_id} تنظیم شد:\n{shown}")
    raise ApplicationHandlerStop


async def v52_followup_cmd(update, context):
    v52_clear_waiting_states(context)
    await update.message.reply_text(
        "⏱ پیگیری کارهای باز:\n\nروی هر کار بزن تا منوی همان کار باز شود.",
        reply_markup=task_list_keyboard(),
    )
    raise ApplicationHandlerStop


async def v52_smart_cmd(update, context):
    v52_clear_waiting_states(context)
    await v5_smart_cmd(update, context)
    raise ApplicationHandlerStop

# =================== END V5.2 COMMAND + STUCK STATE FIX ===================

# =================== END V5 REAL MERGE ADDON ===================

init_db()
v5_init_db()
init_silent_ai_tables()
init_task_metadata_columns()

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


# V5.2 priority command handlers: these run before old handlers and stop duplicate replies
app.add_handler(CommandHandler("start", v52_start_cmd), group=-2)
app.add_handler(CommandHandler("help", v52_help_cmd), group=-2)
app.add_handler(CommandHandler("tasks", v52_tasks_panel_cmd), group=-2)
app.add_handler(CommandHandler("today", v52_today_cmd), group=-2)
app.add_handler(CommandHandler("done", v52_done_cmd), group=-2)
app.add_handler(CommandHandler("delete", v52_delete_cmd), group=-2)
app.add_handler(CommandHandler("remind", v52_remind_cmd), group=-2)
app.add_handler(CommandHandler("followup", v52_followup_cmd), group=-2)
app.add_handler(CommandHandler("smart", v52_smart_cmd), group=-2)

# V5 Real Merge handlers
app.add_handler(CommandHandler("v5help", v5_help_cmd))
app.add_handler(CommandHandler("smart", v5_smart_cmd))
app.add_handler(CommandHandler("v5tasks", v5_tasks_cmd))
app.add_handler(CommandHandler("export_excel", v5_export_excel_cmd))
app.add_handler(CommandHandler("export_pdf", v5_export_pdf_cmd))
app.add_handler(CallbackQueryHandler(v5_callback, pattern="^v5:"), group=-1)
app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO, v5_file), group=-1)
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, v5_text), group=-1)

if __name__ == "__main__":

    print("SAM PRO Team Manager Started...")

    job_queue = app.job_queue

    job_queue.run_repeating(
        check_tasks,
        interval=3 * 60 * 60,
        first=3 * 60 * 60,
        name="three_hour_task_followup"
    )

    app.job_queue.run_daily(
    send_daily_report_job,
    time=datetime.strptime("07:40", "%H:%M").time(),
    name="daily_report"
)
    app.run_polling(drop_pending_updates=True)
