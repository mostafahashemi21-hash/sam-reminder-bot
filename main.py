from database import (
    init_db,
    add_user,
    get_user,
    get_users,
    count_admins
)
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)

import os
import json
from datetime import datetime

TOKEN = os.getenv("BOT_TOKEN")
TASKS_FILE = "tasks.json"


def load_tasks():
    if not os.path.exists(TASKS_FILE):
        return []

    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


def save_tasks(tasks):
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


def get_next_id(tasks):
    if not tasks:
        return 1

    return max(task["id"] for task in tasks) + 1


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    keyboard = [
        ["➕ کار جدید", "📋 کارها"],
        ["🔎 جستجو", "📊 آمار"],
        ["❓ راهنما"]
    ]

    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )

    await update.message.reply_text(
        """
🤖 SAM PRO

سیستم مدیریت کارها

دستورات مهم:

/newtask متن کار
/tasks
/done شماره
/delete شماره
/search کلمه
/stats
""",
        reply_markup=reply_markup
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        """
📘 راهنما

➕ افزودن کار:
/newtask تماس با مشتری

📋 نمایش کارها:
/tasks

✅ اتمام کار:
/done 1

🗑 حذف:
/delete 1

🔎 جستجو:
/search مشتری

📊 آمار:
/stats
"""
    )


async def newtask(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = " ".join(context.args)

    if not text:
        await update.message.reply_text(
            "مثال:\n/newtask خرید لپتاپ"
        )
        return

    tasks = load_tasks()

    task = {
        "id": get_next_id(tasks),
        "title": text,
        "status": "pending",
        "created": datetime.now().strftime("%Y-%m-%d %H:%M")
    }

    tasks.append(task)
    save_tasks(tasks)

    await update.message.reply_text(
        f"✅ کار ثبت شد\n\n{text}"
    )


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    tasks = load_tasks()

    if not tasks:
        await update.message.reply_text(
            "📭 هیچ کاری ثبت نشده"
        )
        return

    msg = "📋 لیست کارها\n\n"

    for task in tasks:

        status = (
            "✅"
            if task["status"] == "done"
            else "⏳"
        )

        msg += (
            f"{task['id']}. {status} {task['title']}\n"
            f"📅 {task['created']}\n\n"
        )

    await update.message.reply_text(msg)


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        await update.message.reply_text(
            "مثال:\n/done 1"
        )
        return

    try:
        task_id = int(context.args[0])
    except:
        await update.message.reply_text(
            "شناسه نامعتبر است"
        )
        return

    tasks = load_tasks()

    found = False

    for task in tasks:

        if task["id"] == task_id:

            task["status"] = "done"
            found = True
            break

    save_tasks(tasks)

    if found:
        await update.message.reply_text(
            "✅ کار انجام شد"
        )
    else:
        await update.message.reply_text(
            "کار پیدا نشد"
        )


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        await update.message.reply_text(
            "مثال:\n/delete 1"
        )
        return

    try:
        task_id = int(context.args[0])
    except:
        await update.message.reply_text(
            "شناسه نامعتبر است"
        )
        return

    tasks = load_tasks()

    new_tasks = [
        t for t in tasks
        if t["id"] != task_id
    ]

    if len(tasks) == len(new_tasks):
        await update.message.reply_text(
            "کار پیدا نشد"
        )
        return

    save_tasks(new_tasks)

    await update.message.reply_text(
        "🗑 کار حذف شد"
    )


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = " ".join(context.args)

    if not query:
        await update.message.reply_text(
            "مثال:\n/search مشتری"
        )
        return

    tasks = load_tasks()

    result = []

    for task in tasks:

        if query.lower() in task["title"].lower():
            result.append(task)

    if not result:
        await update.message.reply_text(
            "چیزی پیدا نشد"
        )
        return

    msg = "🔎 نتایج جستجو\n\n"

    for task in result:

        status = (
            "✅"
            if task["status"] == "done"
            else "⏳"
        )

        msg += (
            f"{task['id']}. {status} "
            f"{task['title']}\n"
        )

    await update.message.reply_text(msg)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):

    tasks = load_tasks()

    total = len(tasks)

    done_count = len(
        [
            t
            for t in tasks
            if t["status"] == "done"
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


async def buttons(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = update.message.text

    if text == "📋 کارها":
        await tasks_command(update, context)

    elif text == "📊 آمار":
        await stats(update, context)

    elif text == "❓ راهنما":
        await help_command(update, context)

    elif text == "➕ کار جدید":
        await update.message.reply_text(
            "دستور زیر را وارد کن:\n\n/newtask عنوان کار"
        )

    elif text == "🔎 جستجو":
        await update.message.reply_text(
            "مثال:\n/search مشتری"
        )


app = Application.builder().token(TOKEN).build()

app.add_handler(
    CommandHandler("start", start)
)

app.add_handler(
    CommandHandler("help", help_command)
)

app.add_handler(
    CommandHandler("newtask", newtask)
)

app.add_handler(
    CommandHandler("tasks", tasks_command)
)

app.add_handler(
    CommandHandler("done", done)
)

app.add_handler(
    CommandHandler("delete", delete)
)

app.add_handler(
    CommandHandler("search", search)
)

app.add_handler(
    CommandHandler("stats", stats)
)

app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        buttons
    )
)

init_db()
if __name__ == "__main__":
    print("SAM PRO Started...")
    app.run_polling(drop_pending_updates=True)

