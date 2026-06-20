from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes
import os
import json
from datetime import datetime

TOKEN = os.getenv("BOT_TOKEN")
TASKS_FILE = "tasks.json"


def load_tasks():
    if not os.path.exists(TASKS_FILE):
        return []

    with open(TASKS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tasks(tasks):
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    keyboard = [
        ["➕ کار جدید", "📋 کارها"],
        ["📊 آمار", "❓ راهنما"]
    ]

    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )

    await update.message.reply_text(
        "🤖 SAM PRO\n\nبه ربات مدیریت کارها خوش آمدی.",
        reply_markup=reply_markup
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        """
📘 راهنما

ایجاد کار:
/newtask خرید ماشین

نمایش کارها:
/tasks

اتمام کار:
/done 1

حذف کار:
/delete 1

آمار:
/stats
"""
    )


async def newtask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)

    if not text:
        await update.message.reply_text(
            "مثال:\n/newtask تماس با مشتری"
        )
        return

    tasks = load_tasks()

    task = {
        "id": len(tasks) + 1,
        "title": text,
        "status": "pending",
        "created": datetime.now().strftime("%Y-%m-%d %H:%M")
    }

    tasks.append(task)
    save_tasks(tasks)

    await update.message.reply_text(
        f"✅ کار ثبت شد:\n{text}"
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
        status = "✅" if task["status"] == "done" else "⏳"
        msg += f"{task['id']}. {status} {task['title']}\n"

    await update.message.reply_text(msg)


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return

    task_id = int(context.args[0])

    tasks = load_tasks()

    for task in tasks:
        if task["id"] == task_id:
            task["status"] = "done"

    save_tasks(tasks)

    await update.message.reply_text(
        "✅ کار انجام شد"
    )


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return

    task_id = int(context.args[0])

    tasks = load_tasks()

    tasks = [
        t for t in tasks
        if t["id"] != task_id
    ]

    save_tasks(tasks)

    await update.message.reply_text(
        "🗑 کار حذف شد"
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = load_tasks()

    total = len(tasks)
    done_count = len(
        [t for t in tasks if t["status"] == "done"]
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


app = Application.builder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("newtask", newtask))
app.add_handler(CommandHandler("tasks", tasks_command))
app.add_handler(CommandHandler("done", done))
app.add_handler(CommandHandler("delete", delete))
app.add_handler(CommandHandler("stats", stats))

if __name__ == "__main__":
    app.run_polling()
