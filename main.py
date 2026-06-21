from openai import OpenAI
import os
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters
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

from datetime import datetime

import sqlite3
import os


TOKEN = os.getenv("BOT_TOKEN")
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
        ["👥 اعضا"],
        ["📊 آمار"],
        ["👤 پروفایل"]
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

    await update.message.reply_text(
        """
⏰ زمان یادآوری را وارد کن

مثال:

2026-06-25 18:00
"""
    )

    return CREATE_REMINDER


async def create_task_reminder(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    reminder_time = (
        update.message.text
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
{reminder_time}
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
{reminder_time}
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

    text = update.message.text

    if text == "📋 کارها":
        await list_tasks(update, context)

    elif text == "👥 اعضا":
        await members(update, context)

    elif text == "📊 آمار":
        await stats(update, context)

    elif text == "👤 پروفایل":
        await whoami(update, context)

    elif text == "➕ کار جدید":

        user = get_user(
            update.effective_user.id
        )

        if user[3] != "admin":

            await update.message.reply_text(
                "فقط مدیر می‌تواند کار ایجاد کند."
            )

            return

        await update.message.reply_text(
            "برای شروع ایجاد کار از دستور زیر استفاده کن:\n\n/newtask"
        )


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


init_db()

app = (
    Application
    .builder()
    .token(TOKEN)
    .build()
)



app.add_handler(
    CommandHandler(
        "start",
        start
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
    MessageHandler(
        filters.TEXT &
        ~filters.COMMAND,
        buttons
    )
)

if __name__ == "__main__":

    print(
        "SAM PRO Team Manager Started..."
    )

    app.run_polling(
        drop_pending_updates=True
    )
