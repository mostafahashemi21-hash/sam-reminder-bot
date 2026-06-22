# -*- coding: utf-8 -*-
"""
SAM PRO Team Manager - Telegram bot
Persian team task manager with task cards, meeting management, reminders,
voice commands, OpenAI smart manager, chat analysis and exports.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database as db

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None


# ----------------------- config -----------------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
logger = logging.getLogger("sam-pro")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
OPENAI_TRANSCRIBE_MODEL = os.getenv("OPENAI_TRANSCRIBE_MODEL", "whisper-1").strip()
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID", "").strip()
DELETE_USER_MENU_MESSAGES = os.getenv("DELETE_USER_MENU_MESSAGES", "true").lower() in {"1", "true", "yes", "on"}
SMART_AUTO_APPLY = os.getenv("SMART_AUTO_APPLY", "false").lower() in {"1", "true", "yes", "on"}
FOLLOWUP_INTERVAL_HOURS = int(os.getenv("FOLLOWUP_INTERVAL_HOURS", "3") or "3")
AUTO_DELETE_BOT_STATUS = os.getenv("AUTO_DELETE_BOT_STATUS", "true").lower() in {"1", "true", "yes", "on"}
STATUS_DELETE_SECONDS = int(os.getenv("STATUS_DELETE_SECONDS", "5") or "5")
APP_TZ_NAME = os.getenv("APP_TZ", "Europe/Warsaw")
try:
    APP_TZ = ZoneInfo(APP_TZ_NAME)
except Exception:
    APP_TZ = timezone.utc

PROJECTS = ["تخته", "میوه", "پتروشیمی", "مالی", "غلات", "غیره"]
PRIORITIES = ["زیاد", "متوسط", "کم"]
STATUSES = ["باز", "در حال پیگیری", "منتظر پاسخ", "انجام شد", "لغو شد"]

if not BOT_TOKEN:
    logger.warning("BOT_TOKEN is empty. Set it in Railway Variables.")

openai_client = OpenAI(api_key=OPENAI_API_KEY) if OpenAI and OPENAI_API_KEY else None


# ----------------------- text utils -----------------------
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
ARABIC_FIX = str.maketrans({"ي": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه", "ؤ": "و"})


def clean_text(text: Optional[str]) -> str:
    if not text:
        return ""
    text = str(text).strip().translate(PERSIAN_DIGITS).translate(ARABIC_FIX)
    text = re.sub(r"\s+", " ", text)
    return text


def text_key(text: Optional[str]) -> str:
    """Normalize Persian button text. Telegram may send emojis before/after words and may use ZWNJ."""
    t = clean_text(text)
    t = re.sub(r"[➕📋🧠🎙🤖📊👥👤❓🔙⬅️🗓📅📆📝✅❌⏰🔥📁✏️📍📌🗑☑️📎🔄⏳⛔🧾➖⭐️📴🔌]", " ", t)
    t = t.replace("/", " ").replace("‌", " ").replace("-", " ").replace("_", " ")
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def now_local() -> datetime:
    return datetime.now(APP_TZ).replace(microsecond=0)


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def local_to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=APP_TZ)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def iso_to_local_text(value: Optional[str]) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(APP_TZ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return value


def html(text: Any) -> str:
    return escape(str(text) if text is not None else "")


def short(text: Optional[str], n: int = 36) -> str:
    t = clean_text(text)
    return t if len(t) <= n else t[: n - 1] + "…"


def actor(update: Update) -> Tuple[Optional[int], str, Optional[str]]:
    u = update.effective_user
    if not u:
        return None, "ناشناس", None
    name = (u.full_name or u.username or str(u.id)).strip()
    return int(u.id), name, u.username


def chat_id_of(update: Update) -> Optional[int]:
    return update.effective_chat.id if update.effective_chat else None


def parse_task_id(text: str) -> Optional[int]:
    t = clean_text(text)
    patterns = [r"(?:کار|تسک|وظیفه)\s*(?:شماره)?\s*#?\s*(\d+)", r"#\s*(\d+)"]
    for p in patterns:
        m = re.search(p, t, re.I)
        if m:
            return int(m.group(1))
    return None


def parse_meeting_id(text: str) -> Optional[int]:
    t = clean_text(text)
    patterns = [r"(?:ملاقات|جلسه)\s*(?:شماره)?\s*#?\s*(\d+)", r"m#\s*(\d+)"]
    for p in patterns:
        m = re.search(p, t, re.I)
        if m:
            return int(m.group(1))
    return None


def guess_project(text: str) -> str:
    t = clean_text(text).lower()
    buckets = {
        "تخته": ["چوب", "تخته", "روسیه", "بار چوب", "mdf", "ام دی اف"],
        "میوه": ["میوه", "سیب", "پرتقال", "کیوی", "سبزی", "کاهو", "کرفس", "نکتارین"],
        "پتروشیمی": ["پتروشیمی", "پلیمر", "قیر", "شیمی", "مواد", "sbr", "sib", "سیبور"],
        "مالی": ["پرداخت", "بانک", "پول", "حسابدار", "فاکتور", "مالی", "حواله", "دلار", "روبل"],
        "غلات": ["گندم", "جو", "ذرت", "غلات", "نخود", "سویا"],
    }
    for project, words in buckets.items():
        if any(w.lower() in t for w in words):
            return project
    return "غیره"


def parse_datetime_text(text: str) -> Optional[str]:
    """Accept: 2026-06-25 18:00, 2026/06/25 18:00, امروز 18:00, فردا 10:00, یک ساعت دیگر, دو ساعت دیگر."""
    t = clean_text(text).lower()
    base = now_local()
    if any(x in t for x in ["بدون", "ندارد", "نمیخوام", "نمی خوام"]):
        return None
    if "یک ساعت" in t or "1 ساعت" in t or "۱ ساعت" in text:
        return local_to_iso(base + timedelta(hours=1))
    if "دو ساعت" in t or "2 ساعت" in t or "۲ ساعت" in text:
        return local_to_iso(base + timedelta(hours=2))
    if "سه ساعت" in t or "3 ساعت" in t:
        return local_to_iso(base + timedelta(hours=3))
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{2})", t)
    if m:
        y, mo, d, h, mi = map(int, m.groups())
        try:
            return local_to_iso(datetime(y, mo, d, h, mi, tzinfo=APP_TZ))
        except ValueError:
            return None
    m = re.search(r"(?:امروز|فردا)\s*(?:ساعت)?\s*(\d{1,2})(?::(\d{2}))?", t)
    if m:
        h = int(m.group(1)); mi = int(m.group(2) or 0)
        day = base.date() + (timedelta(days=1) if "فردا" in t else timedelta(days=0))
        try:
            return local_to_iso(datetime(day.year, day.month, day.day, h, mi, tzinfo=APP_TZ))
        except ValueError:
            return None
    return None


def reminder_label(iso_value: Optional[str]) -> str:
    return iso_to_local_text(iso_value) if iso_value else "بدون یادآوری"


def is_back(text: str) -> bool:
    return text_key(text) in {"بازگشت", "برگشت", "لیست کارها", "لغو", "cancel", "exit"}


def is_main_menu_intent(text: str) -> Optional[str]:
    k = text_key(text)
    compact = k.replace(" ", "")

    # exact and compact aliases
    mapping = {
        "کار جدید": "new_task", "ایجاد کار": "new_task", "newtask": "new_task", "new": "new_task",
        "کارها": "tasks", "لیست کارها": "tasks", "tasks": "tasks",
        "مدیر هوشمند": "smart", "smart": "smart",
        "ایجنت": "agent_chat", "ایجنت عملیاتی": "agent_chat", "عامل هوشمند": "agent_chat", "agent": "agent_chat",
        "تحلیل چت": "summary", "summary": "summary",
        "فرمان صوتی": "voice_help", "voice": "voice_help",
        "چت جی پی تی": "gpt_chat", "چت جیپی تی": "gpt_chat", "چت جی پیتی": "gpt_chat",
        "دستیار هوشمند": "gpt_chat", "gpt": "gpt_chat",
        "گزارش ها": "reports", "گزارش": "reports", "reports": "reports",
        "اعضا": "members", "پروفایل": "profile", "راهنما": "help",
        "ملاقات ها": "meetings", "ملاقات": "meetings", "جلسات": "meetings", "جلسه": "meetings",
        "بازگشت": "home", "برگشت": "home", "start": "home", "exit": "exit", "خروج": "exit",
        "خروج از چت جی پی تی": "exit", "خروج از چتجیپیتی": "exit", "خروج از ایجنت": "exit", "خاموش": "exit",
    }
    if k in mapping:
        return mapping[k]
    compact_mapping = {
        "کارجدید": "new_task", "ایجادکار": "new_task",
        "لیستکارها": "tasks",
        "مدیرهوشمند": "smart", "ایجنتعملیاتی": "agent_chat", "عامل هوشمند".replace(" ", ""): "agent_chat",
        "تحلیلچت": "summary",
        "فرمانصوتی": "voice_help",
        "چتجیپیتی": "gpt_chat", "چتجیپی تی".replace(" ", ""): "gpt_chat", "دستیارهوشمند": "gpt_chat",
        "گزارشها": "reports", "گزارشات": "reports", "خروجازچتجیپیتی": "exit", "خروجازچتجیپی تی".replace(" ", ""): "exit", "خروجازایجنت": "exit",
        "ملاقاتها": "meetings", "جلسات": "meetings",
    }
    if compact in compact_mapping:
        return compact_mapping[compact]

    # fuzzy fallback for common bottom-keyboard labels
    if "کار" in k and ("جدید" in k or "ایجاد" in k): return "new_task"
    if "کارها" in k or "لیست کار" in k: return "tasks"
    if "مدیر" in k and "هوشمند" in k: return "smart"
    if "ایجنت" in k or "agent" in k or "عامل" in k: return "agent_chat"
    if "تحلیل" in k and "چت" in k: return "summary"
    if "فرمان" in k and "صوت" in k: return "voice_help"
    if ("چت" in k and ("جی" in k or "gpt" in k)) or "دستیار" in k: return "gpt_chat"
    if "گزارش" in k: return "reports"
    if "ملاقات" in k or "جلسه" in k: return "meetings"
    if "اعضا" in k: return "members"
    if "پروفایل" in k: return "profile"
    if "راهنما" in k or "help" in k: return "help"
    if "خروج" in k or "خاموش" in k: return "exit"
    if "بازگشت" in k or "برگشت" in k: return "home"
    return None


# ----------------------- keyboards -----------------------
def main_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton("➕ کار جدید"), KeyboardButton("📋 کارها")],
        [KeyboardButton("🧠 مدیر هوشمند"), KeyboardButton("🧠 تحلیل چت")],
        [KeyboardButton("🗓 ملاقات‌ها"), KeyboardButton("🎙 فرمان صوتی")],
        [KeyboardButton("🤖 چت جی‌پی‌تی"), KeyboardButton("🤖 ایجنت عملیاتی")],
        [KeyboardButton("📊 گزارش‌ها"), KeyboardButton("❓ راهنما")],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False, input_field_placeholder="یک گزینه انتخاب کن…")


def main_inline_keyboard() -> InlineKeyboardMarkup:
    """Inline main menu for groups. Inline callbacks work even when Telegram privacy blocks normal group text."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ کار جدید", callback_data="main:new_task"), InlineKeyboardButton("📋 کارها", callback_data="main:tasks")],
        [InlineKeyboardButton("🧠 مدیر هوشمند", callback_data="main:smart"), InlineKeyboardButton("🧠 تحلیل چت", callback_data="main:summary")],
        [InlineKeyboardButton("🗓 ملاقات‌ها", callback_data="main:meetings"), InlineKeyboardButton("🎙 فرمان صوتی", callback_data="main:voice_help")],
        [InlineKeyboardButton("🤖 چت جی‌پی‌تی", callback_data="main:gpt_chat"), InlineKeyboardButton("🤖 ایجنت عملیاتی", callback_data="main:agent_chat")],
        [InlineKeyboardButton("📊 گزارش‌ها", callback_data="main:reports"), InlineKeyboardButton("❓ راهنما", callback_data="main:help")],
    ])


def chat_is_group(update: Update) -> bool:
    c = update.effective_chat
    return bool(c and c.type in {"group", "supergroup"})


def chat_is_channel(update: Update) -> bool:
    c = update.effective_chat
    return bool(c and c.type == "channel")


def chat_is_public(update: Update) -> bool:
    c = update.effective_chat
    return bool(c and c.type in {"group", "supergroup", "channel"})


def back_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[KeyboardButton("🔙 بازگشت")]], resize_keyboard=True, one_time_keyboard=False)


def gpt_exit_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[KeyboardButton("❌ خروج از چت جی‌پی‌تی")], [KeyboardButton("🔙 بازگشت")]], resize_keyboard=True, one_time_keyboard=False)


def agent_exit_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[KeyboardButton("❌ خروج از ایجنت")], [KeyboardButton("🔙 بازگشت")]], resize_keyboard=True, one_time_keyboard=False)


def task_draft_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ عنوان کار", callback_data="draft_task:title"), InlineKeyboardButton("📁 پروژه", callback_data="draft_task:project")],
        [InlineKeyboardButton("👤 مسئول", callback_data="draft_task:assignee"), InlineKeyboardButton("⏰ یادآوری", callback_data="draft_task:reminder")],
        [InlineKeyboardButton("🔥 اولویت", callback_data="draft_task:priority")],
        [InlineKeyboardButton("✅ ثبت نهایی", callback_data="draft_task:save"), InlineKeyboardButton("❌ لغو", callback_data="draft_task:cancel")],
    ])


def meeting_draft_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ عنوان", callback_data="draft_meeting:title"), InlineKeyboardButton("📁 پروژه", callback_data="draft_meeting:project")],
        [InlineKeyboardButton("🕒 زمان", callback_data="draft_meeting:time"), InlineKeyboardButton("📍 مکان/لینک", callback_data="draft_meeting:location")],
        [InlineKeyboardButton("👥 شرکت‌کنندگان", callback_data="draft_meeting:participants"), InlineKeyboardButton("⏰ یادآوری", callback_data="draft_meeting:reminder")],
        [InlineKeyboardButton("✅ ثبت ملاقات", callback_data="draft_meeting:save"), InlineKeyboardButton("❌ لغو", callback_data="draft_meeting:cancel")],
    ])


def project_keyboard(prefix: str) -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(PROJECTS), 2):
        rows.append([InlineKeyboardButton(p, callback_data=f"{prefix}:project:{p}") for p in PROJECTS[i:i+2]])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"{prefix}:back")])
    return InlineKeyboardMarkup(rows)


def priority_keyboard(prefix: str = "draft_task") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔴 زیاد", callback_data=f"{prefix}:priority:زیاد"), InlineKeyboardButton("🟡 متوسط", callback_data=f"{prefix}:priority:متوسط"), InlineKeyboardButton("🟢 کم", callback_data=f"{prefix}:priority:کم")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"{prefix}:back")],
    ])


def reminder_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("بدون یادآوری", callback_data=f"{prefix}:rem:none")],
        [InlineKeyboardButton("۱ ساعت دیگر", callback_data=f"{prefix}:rem:1h"), InlineKeyboardButton("۲ ساعت دیگر", callback_data=f"{prefix}:rem:2h")],
        [InlineKeyboardButton("امروز ۱۸:۰۰", callback_data=f"{prefix}:rem:today18"), InlineKeyboardButton("فردا ۱۰:۰۰", callback_data=f"{prefix}:rem:tomorrow10")],
        [InlineKeyboardButton("زمان دلخواه", callback_data=f"{prefix}:rem:custom")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"{prefix}:back")],
    ])


def assignee_keyboard(prefix: str = "draft_task") -> InlineKeyboardMarkup:
    members = db.get_members(30)
    rows = [[InlineKeyboardButton("خودم", callback_data=f"{prefix}:assignee:self"), InlineKeyboardButton("بدون مسئول", callback_data=f"{prefix}:assignee:none")]]
    for m in members[:20]:
        name = m.get("full_name") or m.get("username") or str(m.get("user_id"))
        rows.append([InlineKeyboardButton(short(name, 28), callback_data=f"{prefix}:assignee:{m['user_id']}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"{prefix}:back")])
    return InlineKeyboardMarkup(rows)


def participants_keyboard(prefix: str = "draft_meeting") -> InlineKeyboardMarkup:
    members = db.get_members(30)
    rows = [[InlineKeyboardButton("بدون شرکت‌کننده", callback_data=f"{prefix}:participants:none")]]
    for m in members[:20]:
        name = m.get("full_name") or m.get("username") or str(m.get("user_id"))
        rows.append([InlineKeyboardButton(short(name, 28), callback_data=f"{prefix}:participants:add:{m['user_id']}")])
    rows.append([InlineKeyboardButton("✅ پایان انتخاب", callback_data=f"{prefix}:participants:done")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"{prefix}:back")])
    return InlineKeyboardMarkup(rows)


def tasks_list_keyboard(tasks: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    rows = []
    for t in tasks[:40]:
        pin = "📌 " if t.get("pinned") else ""
        rows.append([InlineKeyboardButton(f"{pin}#{t['id']} | {short(t['title'], 32)}", callback_data=f"task:open:{t['id']}")])
    rows.append([InlineKeyboardButton("➕ کار جدید", callback_data="task:new")])
    return InlineKeyboardMarkup(rows)


def task_card_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ انجام شد", callback_data=f"task:status:{task_id}:انجام شد"), InlineKeyboardButton("🔄 پیگیری", callback_data=f"task:status:{task_id}:در حال پیگیری")],
        [InlineKeyboardButton("⏳ منتظر پاسخ", callback_data=f"task:status:{task_id}:منتظر پاسخ"), InlineKeyboardButton("⛔ لغو", callback_data=f"task:status:{task_id}:لغو شد")],
        [InlineKeyboardButton("📝 شرح‌ها", callback_data=f"task:notes:{task_id}"), InlineKeyboardButton("➕ شرح", callback_data=f"task:addnote:{task_id}")],
        [InlineKeyboardButton("☑️ چک‌لیست", callback_data=f"task:checklist:{task_id}"), InlineKeyboardButton("➕ چک‌لیست", callback_data=f"task:addcheck:{task_id}")],
        [InlineKeyboardButton("📎 فایل‌ها", callback_data=f"task:files:{task_id}"), InlineKeyboardButton("⏰ یادآوری", callback_data=f"task:reminder:{task_id}")],
        [InlineKeyboardButton("🧾 تاریخچه", callback_data=f"task:history:{task_id}"), InlineKeyboardButton("📌 پین/برداشتن", callback_data=f"task:pin:{task_id}")],
        [InlineKeyboardButton("🗑 حذف", callback_data=f"task:delete:{task_id}"), InlineKeyboardButton("🔙 لیست کارها", callback_data="task:list")],
    ])


def meetings_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ ملاقات جدید", callback_data="meeting:new")],
        [InlineKeyboardButton("📅 ملاقات‌های امروز", callback_data="meeting:list:today"), InlineKeyboardButton("📆 ملاقات‌های آینده", callback_data="meeting:list:upcoming")],
        [InlineKeyboardButton("📝 صورتجلسه", callback_data="meeting:minutes_help"), InlineKeyboardButton("📊 گزارش ملاقات‌ها", callback_data="meeting:stats")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="home")],
    ])


def meetings_list_keyboard(meetings: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    rows = []
    for m in meetings[:40]:
        pin = "📌 " if m.get("pinned") else ""
        when = iso_to_local_text(m.get("start_at")) if m.get("start_at") else "بدون زمان"
        rows.append([InlineKeyboardButton(f"{pin}M#{m['id']} | {short(m['title'], 24)} | {when}", callback_data=f"meeting:open:{m['id']}")])
    rows.append([InlineKeyboardButton("➕ ملاقات جدید", callback_data="meeting:new")])
    return InlineKeyboardMarkup(rows)


def meeting_card_keyboard(meeting_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ برگزار شد", callback_data=f"meeting:status:{meeting_id}:برگزار شد"), InlineKeyboardButton("⛔ لغو شد", callback_data=f"meeting:status:{meeting_id}:لغو شد")],
        [InlineKeyboardButton("📝 صورتجلسه‌ها", callback_data=f"meeting:minutes:{meeting_id}"), InlineKeyboardButton("➕ صورتجلسه", callback_data=f"meeting:addminutes:{meeting_id}")],
        [InlineKeyboardButton("📎 فایل‌ها", callback_data=f"meeting:files:{meeting_id}"), InlineKeyboardButton("⏰ یادآوری", callback_data=f"meeting:reminder:{meeting_id}")],
        [InlineKeyboardButton("🧾 تاریخچه", callback_data=f"meeting:history:{meeting_id}"), InlineKeyboardButton("📌 پین/برداشتن", callback_data=f"meeting:pin:{meeting_id}")],
        [InlineKeyboardButton("🤖 استخراج کار از صورتجلسه", callback_data=f"meeting:ai_extract:{meeting_id}")],
        [InlineKeyboardButton("🔙 ملاقات‌ها", callback_data="meeting:menu")],
    ])


def summary_range_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("۱ ساعت اخیر", callback_data="summary:1h"), InlineKeyboardButton("۲ ساعت اخیر", callback_data="summary:2h")],
        [InlineKeyboardButton("دیروز", callback_data="summary:yesterday"), InlineKeyboardButton("۷ روز اخیر", callback_data="summary:7d")],
        [InlineKeyboardButton("زمان دلخواه", callback_data="summary:custom")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="home")],
    ])


def reports_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 آمار", callback_data="report:stats"), InlineKeyboardButton("📅 گزارش روزانه", callback_data="report:daily")],
        [InlineKeyboardButton("📈 گزارش هفتگی", callback_data="report:weekly")],
        [InlineKeyboardButton("📤 خروجی اکسل", callback_data="report:excel"), InlineKeyboardButton("📄 خروجی PDF", callback_data="report:pdf")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="home")],
    ])


def confirm_actions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ اعمال", callback_data="agent:apply"), InlineKeyboardButton("❌ رد", callback_data="agent:reject")]])


# ----------------------- cards -----------------------
def draft_task_text(d: Dict[str, Any]) -> str:
    hint = d.get("_hint") or "گزینه موردنظر را انتخاب کن. فقط برای عنوان یا زمان دلخواه لازم است متن بنویسی."
    return (
        "🆕 <b>ایجاد کار جدید</b>\n\n"
        f"عنوان: <b>{html(d.get('title') or 'تعیین نشده')}</b>\n"
        f"پروژه: {html(d.get('project') or 'تعیین نشده')}\n"
        f"مسئول: {html(d.get('assigned_to_name') or 'بدون مسئول')}\n"
        f"یادآوری: {html(reminder_label(d.get('reminder_at')))}\n"
        f"اولویت: {html(d.get('priority') or 'متوسط')}\n\n"
        f"<i>{html(hint)}</i>"
    )


def task_text(t: Dict[str, Any]) -> str:
    done, total = db.checklist_counts(int(t["id"]))
    return (
        f"📂 <b>کار #{t['id']}</b>\n\n"
        f"عنوان: <b>{html(t.get('title'))}</b>\n"
        f"پروژه: {html(t.get('project'))}\n"
        f"وضعیت: {html(t.get('status'))}\n"
        f"اولویت: {html(t.get('priority'))}\n"
        f"مسئول: {html(t.get('assigned_to_name') or 'بدون مسئول')}\n"
        f"یادآوری: {html(reminder_label(t.get('reminder_at')))}\n"
        f"شرح‌ها: {db.count_task_notes(int(t['id']))} | فایل‌ها: {db.count_task_files(int(t['id']))} | چک‌لیست: {done}/{total}"
    )


def draft_meeting_text(d: Dict[str, Any]) -> str:
    participants = d.get("participants") or []
    pname = "، ".join([p.get("name", "-") for p in participants]) if participants else "تعیین نشده"
    hint = d.get("_hint") or "گزینه موردنظر را انتخاب کن. فقط برای عنوان، زمان یا مکان لازم است متن بنویسی."
    return (
        "🗓 <b>ملاقات جدید</b>\n\n"
        f"عنوان: <b>{html(d.get('title') or 'تعیین نشده')}</b>\n"
        f"پروژه: {html(d.get('project') or 'تعیین نشده')}\n"
        f"زمان: {html(iso_to_local_text(d.get('start_at')) if d.get('start_at') else 'تعیین نشده')}\n"
        f"مکان/لینک: {html(d.get('location') or 'تعیین نشده')}\n"
        f"شرکت‌کنندگان: {html(pname)}\n"
        f"یادآوری: {html(reminder_label(d.get('reminder_at')))}\n\n"
        f"<i>{html(hint)}</i>"
    )


def meeting_text(m: Dict[str, Any]) -> str:
    participants = m.get("participants_list") or []
    pname = "، ".join([p.get("name", "-") for p in participants]) if participants else "-"
    return (
        f"🗓 <b>ملاقات #{m['id']}</b>\n\n"
        f"عنوان: <b>{html(m.get('title'))}</b>\n"
        f"پروژه: {html(m.get('project'))}\n"
        f"وضعیت: {html(m.get('status'))}\n"
        f"زمان: {html(iso_to_local_text(m.get('start_at')))}\n"
        f"مکان/لینک: {html(m.get('location') or '-')}\n"
        f"شرکت‌کنندگان: {html(pname)}\n"
        f"یادآوری: {html(reminder_label(m.get('reminder_at')))}"
    )


# ----------------------- DB/user registration -----------------------
async def register_user(update: Update, private: bool = False) -> None:
    u = update.effective_user
    if not u:
        return
    private_chat_id = update.effective_chat.id if private and update.effective_chat else None
    db.upsert_user(u.id, u.username, u.full_name, private_chat_id=private_chat_id)


async def maybe_delete_user_menu(update: Update) -> None:
    if not DELETE_USER_MENU_MESSAGES:
        return
    msg = update.effective_message
    if not msg or not msg.text:
        return
    if is_main_menu_intent(msg.text) or is_back(msg.text):
        try:
            await msg.delete()
        except Exception:
            pass


# ----------------------- core send/edit helpers -----------------------
def is_status_text(text: str) -> bool:
    t = clean_text(re.sub(r"<[^>]+>", " ", str(text or "")))
    if len(t) > 220:
        return False
    keywords = [
        "✅", "⏳", "در حال", "ثبت شد", "ذخیره شد", "انجام شد", "لغو شد", "حذف شد", "برگشت", "رد شد",
        "فرمان واضح نبود", "فرمت زمان", "کار پیدا نشد", "ملاقات پیدا نشد", "OPENAI_API_KEY", "فایل ثبت شد",
        "منوی پایین فعال شد", "منوی تصویری آماده شد", "از این منو استفاده کن",
    ]
    return any(k in str(text) or k in t for k in keywords)


async def auto_delete_later(msg: Any, seconds: Optional[int] = None) -> None:
    if not msg or not AUTO_DELETE_BOT_STATUS:
        return
    try:
        await asyncio.sleep(seconds or STATUS_DELETE_SECONDS)
        await msg.delete()
    except Exception:
        pass


async def safe_reply(update: Update, text: str, reply_markup: Any = None, parse_mode: Optional[str] = ParseMode.HTML):
    src = update.effective_message
    msg = None
    if src:
        msg = await src.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
    elif update.effective_chat:
        msg = await update.effective_chat.send_message(text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
    # Important: messages that carry the lower ReplyKeyboard must NOT be auto-deleted.
    # If Telegram deletes the message that introduced the ReplyKeyboard in a group,
    # many clients hide the lower visual menu again.
    if msg and is_status_text(text) and not isinstance(reply_markup, ReplyKeyboardMarkup):
        asyncio.create_task(auto_delete_later(msg))
    return msg


async def delete_message_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data or {}
    try:
        await context.bot.delete_message(chat_id=data["chat_id"], message_id=data["message_id"])
    except Exception:
        pass


async def temp_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup: Any = None, parse_mode: Optional[str] = ParseMode.HTML, seconds: Optional[int] = None):
    """Short bot status messages such as پردازش / ثبت شد are removed automatically."""
    msg = await safe_reply(update, text, reply_markup=reply_markup, parse_mode=parse_mode)
    # Never delete a message whose only purpose is to install the lower visual keyboard.
    if isinstance(reply_markup, ReplyKeyboardMarkup):
        return msg
    if msg and AUTO_DELETE_BOT_STATUS and context.job_queue:
        context.job_queue.run_once(delete_message_job, when=seconds or STATUS_DELETE_SECONDS, data={"chat_id": msg.chat_id, "message_id": msg.message_id})
    return msg


async def send_bottom_keyboard_anchor(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str = "⌨️ منوی پایین SAM فعال است"):
    """Send a persistent ReplyKeyboard anchor message.

    Telegram lower reply keyboards in groups are attached to a normal bot message.
    If that message is auto-deleted, some Telegram clients remove the lower menu.
    Therefore this message is intentionally NOT auto-deleted.
    """
    src = update.effective_message
    if src:
        return await src.reply_text(text, reply_markup=main_keyboard(), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    if update.effective_chat:
        return await update.effective_chat.send_message(text, reply_markup=main_keyboard(), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    return None


async def edit_or_send(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup: Any = None, parse_mode: Optional[str] = ParseMode.HTML):
    q = update.callback_query
    if q and q.message:
        try:
            await q.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
            return q.message
        except Exception:
            return await q.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
    return await safe_reply(update, text, reply_markup, parse_mode)


async def edit_card_or_send(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup: Any, card_key: str, parse_mode: Optional[str] = ParseMode.HTML):
    """Edit an existing draft card instead of sending new messages while the user types title/time."""
    q = update.callback_query
    if q and q.message:
        try:
            await q.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
            context.user_data[f"{card_key}_chat_id"] = q.message.chat_id
            context.user_data[f"{card_key}_message_id"] = q.message.message_id
            return q.message
        except Exception:
            msg = await q.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
            context.user_data[f"{card_key}_chat_id"] = msg.chat_id
            context.user_data[f"{card_key}_message_id"] = msg.message_id
            return msg
    cid = context.user_data.get(f"{card_key}_chat_id") or chat_id_of(update)
    mid = context.user_data.get(f"{card_key}_message_id")
    if cid and mid:
        try:
            await context.bot.edit_message_text(chat_id=cid, message_id=mid, text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
            return None
        except Exception:
            pass
    msg = await safe_reply(update, text, reply_markup, parse_mode)
    if msg:
        context.user_data[f"{card_key}_chat_id"] = msg.chat_id
        context.user_data[f"{card_key}_message_id"] = msg.message_id
    return msg

async def delete_user_message_if_possible(update: Update) -> None:
    msg = update.effective_message
    if not msg:
        return
    try:
        await msg.delete()
    except Exception:
        pass

async def show_home(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str = "منوی اصلی آماده است.") -> None:
    """Show home in a way that works in private, groups and channels.

    Private/group chats get the bottom ReplyKeyboard (the visual lower menu).
    Public chats also get a pinned-friendly Inline menu because channel posts and
    many group clients do not reliably expose bot reply keyboards to everyone.
    """
    context.user_data.pop("mode", None)
    context.user_data.pop("state", None)
    context.user_data.pop("draft_task", None)
    context.user_data.pop("draft_meeting", None)
    context.user_data.pop("pending_actions", None)
    context.user_data.pop("pending_comment", None)

    if chat_is_channel(update):
        await safe_reply(
            update,
            f"🤖 <b>SAM PRO Team Manager</b>\n{html(text)}\n\nاین پیام را در کانال Pin کن و از دکمه‌های زیر استفاده کن.",
            reply_markup=main_inline_keyboard(),
        )
        return

    if chat_is_group(update):
        # 1) Install the lower visual ReplyKeyboard in the group.
        # This message must remain in the chat; otherwise the lower digital menu
        # disappears on many Telegram clients. Pin/delete manually only if needed.
        await send_bottom_keyboard_anchor(update, context, "⌨️ منوی پایین SAM فعال است")
        # 2) Also send an inline menu that can be pinned. Inline buttons never type text into the group.
        await safe_reply(
            update,
            f"🤖 <b>SAM PRO Team Manager</b>\n{html(text)}\n\nاین منوی تصویری را هم می‌توانی Pin کنی، ولی منوی پایین هم فعال شد.",
            reply_markup=main_inline_keyboard(),
        )
        return

    # Private chat: keep the bottom keyboard and also show an inline card.
    await safe_reply(update, f"🏠 {html(text)}", reply_markup=main_keyboard())
    await safe_reply(update, "🤖 <b>منوی تصویری</b>", reply_markup=main_inline_keyboard())


# ----------------------- commands -----------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update, private=(update.effective_chat.type == "private" if update.effective_chat else False))
    await show_home(update, context, "SAM PRO Team Manager")


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update, private=(update.effective_chat.type == "private" if update.effective_chat else False))
    await show_home(update, context, "منو آماده است")


async def keyboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Only install the lower visual ReplyKeyboard, without extra inline card."""
    await register_user(update, private=(update.effective_chat.type == "private" if update.effective_chat else False))
    if chat_is_channel(update):
        await safe_reply(update, "در کانال منوی پایین تلگرام مثل گروه/چت خصوصی نمایش داده نمی‌شود. از /post_menu استفاده کن.", reply_markup=main_inline_keyboard())
        return
    await send_bottom_keyboard_anchor(update, context, "⌨️ منوی پایین SAM فعال شد")


async def post_menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Post only the inline menu; useful for channels and for pinning in groups."""
    await safe_reply(
        update,
        "🤖 <b>SAM PRO Team Manager</b>\nمنوی تصویری آماده شد. این پیام را Pin کن.",
        reply_markup=main_inline_keyboard(),
    )


async def exit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    await temp_reply(update, context, "✅ خارج شد")
    await safe_reply(update, "🏠 منوی اصلی", reply_markup=main_keyboard())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    text = (
        "❓ <b>راهنمای سریع</b>\n\n"
        "➕ کار جدید: کارت ساخت کار را باز می‌کند.\n"
        "📋 کارها: لیست دکمه‌ای کارهای باز.\n"
        "🗓 ملاقات‌ها: مدیریت جلسات و صورتجلسه.\n"
        "🧠 مدیر هوشمند: پیام‌ها و کارها را تحلیل و پیشنهاد عملیاتی می‌دهد.\n"
        "🤖 چت جی‌پی‌تی: چت آزاد، بدون تغییر دیتابیس.\n"
        "🎙 ویس: تبدیل به متن و اجرای فرمان.\n\n"
        "نمونه متن‌ها:\n"
        "کار 1 انجام شد\n"
        "کار 2: زنگ زدم جواب ندادند\n"
        "ملاقات 3: صورتجلسه ..."
    )
    await safe_reply(update, text, reply_markup=main_keyboard())


async def chatid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_reply(update, f"Chat ID: <code>{update.effective_chat.id}</code>" if update.effective_chat else "Chat ID پیدا نشد")


async def whoami_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update, private=(update.effective_chat.type == "private" if update.effective_chat else False))
    uid, name, username = actor(update)
    await safe_reply(update, f"👤 {html(name)}\nID: <code>{uid}</code>\n@{html(username or '-')}" )


async def members_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    members = db.get_members(50)
    if not members:
        await safe_reply(update, "عضوی ثبت نشده.")
        return
    lines = ["👥 <b>اعضا</b>"]
    for m in members[:30]:
        lines.append(f"• {html(m.get('full_name') or m.get('username') or m.get('user_id'))}")
    await safe_reply(update, "\n".join(lines))


# ----------------------- task draft flow -----------------------
async def start_task_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    uid, name, _ = actor(update)
    context.user_data["draft_task"] = {
        "title": None,
        "project": None,
        "assigned_to": None,
        "assigned_to_name": None,
        "assigned_by": uid,
        "assigned_by_name": name,
        "priority": "متوسط",
        "reminder_at": None,
    }
    msg = await edit_card_or_send(update, context, draft_task_text(context.user_data["draft_task"]), task_draft_keyboard(), "draft_task_card")
    if msg and update.effective_chat:
        # no entity id yet, but useful for reply attachments after save only
        pass


async def refresh_task_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    d = context.user_data.get("draft_task") or {}
    await edit_card_or_send(update, context, draft_task_text(d), task_draft_keyboard(), "draft_task_card")


async def save_task_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    d = context.user_data.get("draft_task") or {}
    title = clean_text(d.get("title"))
    if not title:
        await edit_or_send(update, context, "❌ اول عنوان کار را وارد کن.", task_draft_keyboard())
        return
    uid, name, _ = actor(update)
    task_id = db.create_task(
        title=title,
        project=d.get("project") or guess_project(title),
        priority=d.get("priority") or "متوسط",
        assigned_to=d.get("assigned_to"),
        assigned_to_name=d.get("assigned_to_name"),
        assigned_by=uid,
        assigned_by_name=name,
        chat_id=chat_id_of(update),
        reminder_at=d.get("reminder_at"),
    )
    context.user_data.pop("draft_task", None)
    context.user_data.pop("state", None)
    await edit_or_send(update, context, "✅ ثبت شد", None)
    # show card in a second message, minimal
    task = db.get_task(task_id)
    if task:
        sent = await safe_reply(update, task_text(task), task_card_keyboard(task_id))
        if sent and update.effective_chat:
            db.save_ui_message(update.effective_chat.id, sent.message_id, "task", task_id)


async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("state", None)
    tasks = db.list_tasks(include_done=False, include_deleted=False, limit=80)
    if not tasks:
        await edit_or_send(update, context, "✅ کار بازی وجود ندارد.", InlineKeyboardMarkup([[InlineKeyboardButton("➕ کار جدید", callback_data="task:new")]]))
        return
    await edit_or_send(update, context, "📋 <b>کارهای باز</b>\nروی هر کار بزن تا منوی همان کار باز شود.", tasks_list_keyboard(tasks))


async def show_task_card(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int) -> None:
    task = db.get_task(task_id)
    if not task or task.get("deleted"):
        await edit_or_send(update, context, "❌ کار پیدا نشد.", tasks_list_keyboard(db.list_tasks()))
        return
    msg = await edit_or_send(update, context, task_text(task), task_card_keyboard(task_id))
    if msg and update.effective_chat:
        try:
            db.save_ui_message(update.effective_chat.id, msg.message_id, "task", task_id)
        except Exception:
            pass


# ----------------------- meeting draft flow -----------------------
async def start_meeting_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    uid, name, _ = actor(update)
    context.user_data["draft_meeting"] = {
        "title": None,
        "project": None,
        "start_at": None,
        "location": None,
        "participants": [],
        "created_by": uid,
        "created_by_name": name,
        "reminder_at": None,
    }
    await edit_card_or_send(update, context, draft_meeting_text(context.user_data["draft_meeting"]), meeting_draft_keyboard(), "draft_meeting_card")


async def refresh_meeting_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    d = context.user_data.get("draft_meeting") or {}
    await edit_card_or_send(update, context, draft_meeting_text(d), meeting_draft_keyboard(), "draft_meeting_card")


async def save_meeting_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    d = context.user_data.get("draft_meeting") or {}
    title = clean_text(d.get("title"))
    if not title:
        await edit_or_send(update, context, "❌ اول عنوان ملاقات را وارد کن.", meeting_draft_keyboard())
        return
    uid, name, _ = actor(update)
    meeting_id = db.create_meeting(
        title=title,
        project=d.get("project") or guess_project(title),
        start_at=d.get("start_at"),
        location=d.get("location"),
        participants=d.get("participants") or [],
        created_by=uid,
        created_by_name=name,
        chat_id=chat_id_of(update),
        reminder_at=d.get("reminder_at"),
    )
    context.user_data.pop("draft_meeting", None)
    context.user_data.pop("state", None)
    await edit_or_send(update, context, "✅ ثبت شد", None)
    meeting = db.get_meeting(meeting_id)
    if meeting:
        sent = await safe_reply(update, meeting_text(meeting), meeting_card_keyboard(meeting_id))
        if sent and update.effective_chat:
            db.save_ui_message(update.effective_chat.id, sent.message_id, "meeting", meeting_id)


async def show_meetings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("state", None)
    await edit_or_send(update, context, "🗓 <b>مدیریت ملاقات‌ها</b>", meetings_menu_keyboard())


async def show_meetings_list(update: Update, context: ContextTypes.DEFAULT_TYPE, upcoming_only: bool = True) -> None:
    meetings = db.list_meetings(upcoming_only=upcoming_only, limit=80)
    if not meetings:
        await edit_or_send(update, context, "✅ ملاقاتی ثبت نشده.", InlineKeyboardMarkup([[InlineKeyboardButton("➕ ملاقات جدید", callback_data="meeting:new")]]))
        return
    await edit_or_send(update, context, "📆 <b>ملاقات‌ها</b>\nروی هر ملاقات بزن.", meetings_list_keyboard(meetings))


async def show_meeting_card(update: Update, context: ContextTypes.DEFAULT_TYPE, meeting_id: int) -> None:
    meeting = db.get_meeting(meeting_id)
    if not meeting or meeting.get("deleted"):
        await edit_or_send(update, context, "❌ ملاقات پیدا نشد.", meetings_menu_keyboard())
        return
    msg = await edit_or_send(update, context, meeting_text(meeting), meeting_card_keyboard(meeting_id))
    if msg and update.effective_chat:
        try:
            db.save_ui_message(update.effective_chat.id, msg.message_id, "meeting", meeting_id)
        except Exception:
            pass


# ----------------------- command implementations -----------------------
async def newtask_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    if context.args:
        title = clean_text(" ".join(context.args))
        uid, name, _ = actor(update)
        task_id = db.create_task(title, project=guess_project(title), assigned_by=uid, assigned_by_name=name, chat_id=chat_id_of(update))
        await temp_reply(update, context, "✅ ثبت شد")
        task = db.get_task(task_id)
        if task:
            await safe_reply(update, task_text(task), task_card_keyboard(task_id))
    else:
        await start_task_draft(update, context)


async def tasks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    await show_tasks(update, context)


async def meetings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    await show_meetings_menu(update, context)


async def summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    await edit_or_send(update, context, "🧠 بازه زمانی تحلیل چت را انتخاب کن:", summary_range_keyboard())


async def smart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    await run_smart_manager(update, context)


async def gpt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    context.user_data.clear()
    context.user_data["mode"] = "gpt_chat"
    await safe_reply(update, "🤖 چت جی‌پی‌تی روشن شد. سوالت را بنویس. برای خاموش کردن دکمه خروج را بزن.", reply_markup=gpt_exit_keyboard())

async def agent_mode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    context.user_data.clear()
    context.user_data["mode"] = "agent_chat"
    await safe_reply(
        update,
        "🤖 ایجنت عملیاتی روشن شد. الان می‌توانی مستقیم دستور بدهی؛ مثلاً:\n«برای فردا ساعت ۱۰ ملاقات با موسی بگذار و یک کار پیگیری قیمت چوب برایش بساز.»\nبرای خاموش کردن دکمه خروج را بزن.",
        reply_markup=agent_exit_keyboard(),
    )


async def reports_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await edit_or_send(update, context, "📊 <b>گزارش‌ها</b>", reports_keyboard())


async def export_excel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await export_excel(update, context)


async def export_pdf_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await export_pdf(update, context)


async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await safe_reply(update, "مثال: /delete 3")
        return
    uid, name, _ = actor(update)
    tid = int(context.args[0]) if context.args[0].isdigit() else None
    if tid and db.delete_task(tid, uid, name):
        await temp_reply(update, context, "✅ حذف شد")
    else:
        await safe_reply(update, "❌ کار پیدا نشد")


async def restore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await safe_reply(update, "مثال: /restore 3")
        return
    uid, name, _ = actor(update)
    tid = int(context.args[0]) if context.args[0].isdigit() else None
    if tid and db.restore_task(tid, uid, name):
        await temp_reply(update, context, "✅ بازیابی شد")
    else:
        await safe_reply(update, "❌ کار پیدا نشد")


# ----------------------- callback handler -----------------------
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await register_user(update)
    data = q.data or ""
    try:
        await q.answer()
    except Exception:
        pass
    uid, name, _ = actor(update)

    if data.startswith("main:"):
        await handle_menu_intent(update, context, data.split(":", 1)[1])
        return

    if data == "home":
        await show_home(update, context, "برگشت")
        return

    # value callbacks must be handled before generic draft menu callbacks
    if data.startswith("draft_task:project:"):
        context.user_data.setdefault("draft_task", {})["project"] = data.split(":", 2)[2]
        await refresh_task_draft(update, context); return
    if data.startswith("draft_task:priority:"):
        context.user_data.setdefault("draft_task", {})["priority"] = data.split(":", 2)[2]
        await refresh_task_draft(update, context); return
    if data.startswith("draft_task:assignee:"):
        val = data.split(":", 2)[2]
        d = context.user_data.setdefault("draft_task", {})
        if val == "self":
            d["assigned_to"] = uid; d["assigned_to_name"] = name
        elif val == "none":
            d["assigned_to"] = None; d["assigned_to_name"] = None
        else:
            user = db.get_user(int(val))
            d["assigned_to"] = int(val); d["assigned_to_name"] = (user or {}).get("full_name") or str(val)
        await refresh_task_draft(update, context); return
    if data.startswith("draft_task:rem:"):
        await handle_draft_reminder(update, context, "draft_task", data.split(":", 2)[2]); return

    # meeting draft value callbacks
    # task draft
    if data.startswith("draft_task:"):
        parts = data.split(":")
        action = parts[1]
        d = context.user_data.setdefault("draft_task", {"priority": "متوسط"})
        if action == "cancel":
            context.user_data.clear()
            await edit_or_send(update, context, "✅ لغو شد", None)
            return
        if action == "back":
            await refresh_task_draft(update, context)
            return
        if action == "title":
            d["_hint"] = "✏️ عنوان کار را بفرست. بعد از ذخیره، پیام عنوان از چت حذف می‌شود."
            context.user_data["state"] = "await_task_title"
            await refresh_task_draft(update, context)
            return
        if action == "project":
            await edit_or_send(update, context, "📁 پروژه را انتخاب کن:", project_keyboard("draft_task"))
            return
        if action == "assignee":
            await edit_or_send(update, context, "👤 مسئول را انتخاب کن:", assignee_keyboard("draft_task"))
            return
        if action == "reminder":
            await edit_or_send(update, context, "⏰ یادآوری را انتخاب کن:", reminder_keyboard("draft_task"))
            return
        if action == "priority":
            await edit_or_send(update, context, "🔥 اولویت را انتخاب کن:", priority_keyboard("draft_task"))
            return
        if action == "save":
            await save_task_draft(update, context)
            return
        if action == "project" and len(parts) >= 3:
            return
        if action == "priority" and len(parts) >= 3:
            return
        if action == "assignee" and len(parts) >= 3:
            return
        # value actions
        if action == "project" and len(parts) > 2:
            d["project"] = parts[2]
        return

    # task operations
    if data == "task:new":
        await start_task_draft(update, context); return
    if data == "task:list":
        await show_tasks(update, context); return
    if data.startswith("task:open:"):
        await show_task_card(update, context, int(data.rsplit(":", 1)[1])); return
    if data.startswith("task:status:"):
        _, _, tid, status = data.split(":", 3)
        db.update_task_status(int(tid), status, uid, name)
        await show_task_card(update, context, int(tid)); return
    if data.startswith("task:addnote:"):
        tid = int(data.rsplit(":", 1)[1])
        context.user_data["state"] = "await_task_note"; context.user_data["active_task_id"] = tid
        await edit_or_send(update, context, "➕ شرح را بنویس:", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"task:open:{tid}")]])); return
    if data.startswith("task:notes:"):
        tid = int(data.rsplit(":", 1)[1]); await show_task_notes(update, context, tid); return
    if data.startswith("task:files:"):
        tid = int(data.rsplit(":", 1)[1])
        context.user_data["state"] = "await_task_file"
        context.user_data["active_task_id"] = tid
        await show_task_files(update, context, tid)
        return
    if data.startswith("task:addcheck:"):
        tid = int(data.rsplit(":", 1)[1])
        context.user_data["state"] = "await_check_item"; context.user_data["active_task_id"] = tid
        await edit_or_send(update, context, "☑️ آیتم چک‌لیست را بنویس:", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"task:open:{tid}")]])); return
    if data.startswith("task:checklist:"):
        tid = int(data.rsplit(":", 1)[1]); await show_checklist(update, context, tid); return
    if data.startswith("check:toggle:"):
        item_id = int(data.rsplit(":", 1)[1])
        row = db.toggle_checklist_item(item_id, uid, name)
        if row: await show_checklist(update, context, int(row["task_id"]))
        return
    if data.startswith("task:reminder:"):
        tid = int(data.rsplit(":", 1)[1]); context.user_data["active_task_id"] = tid
        await edit_or_send(update, context, "⏰ یادآوری کار را انتخاب کن:", reminder_keyboard(f"taskrem:{tid}")); return
    if data.startswith("taskrem:"):
        _, tid, _, choice = data.split(":", 3)
        await handle_existing_task_reminder(update, context, int(tid), choice); return
    if data.startswith("task:history:"):
        tid = int(data.rsplit(":", 1)[1]); await show_task_history(update, context, tid); return
    if data.startswith("task:pin:"):
        tid = int(data.rsplit(":", 1)[1]); t = db.get_task(tid); db.update_task_field(tid, "pinned", 0 if t.get("pinned") else 1, uid, name); await show_task_card(update, context, tid); return
    if data.startswith("task:delete:"):
        tid = int(data.rsplit(":", 1)[1]); db.delete_task(tid, uid, name); await edit_or_send(update, context, "✅ حذف شد", tasks_list_keyboard(db.list_tasks())); return

    # meeting menu and draft
    if data == "meeting:menu":
        await show_meetings_menu(update, context); return
    if data == "meeting:new":
        await start_meeting_draft(update, context); return
    if data.startswith("meeting:list:"):
        await show_meetings_list(update, context, upcoming_only=True); return
    if data.startswith("meeting:open:"):
        await show_meeting_card(update, context, int(data.rsplit(":", 1)[1])); return
    if data.startswith("meeting:status:"):
        _, _, mid, status = data.split(":", 3)
        db.update_meeting_field(int(mid), "status", status, uid, name)
        await show_meeting_card(update, context, int(mid)); return
    if data.startswith("meeting:addminutes:"):
        mid = int(data.rsplit(":", 1)[1]); context.user_data["state"] = "await_meeting_minutes"; context.user_data["active_meeting_id"] = mid
        await edit_or_send(update, context, "📝 صورتجلسه را بنویس:", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"meeting:open:{mid}")]])); return
    if data.startswith("meeting:minutes:"):
        mid = int(data.rsplit(":", 1)[1]); await show_meeting_minutes(update, context, mid); return
    if data.startswith("meeting:files:"):
        mid = int(data.rsplit(":", 1)[1])
        context.user_data["state"] = "await_meeting_file"
        context.user_data["active_meeting_id"] = mid
        await show_meeting_files(update, context, mid)
        return
    if data.startswith("meeting:reminder:"):
        mid = int(data.rsplit(":", 1)[1])
        await edit_or_send(update, context, "⏰ یادآوری ملاقات را انتخاب کن:", reminder_keyboard(f"meetingrem:{mid}")); return
    if data.startswith("meetingrem:"):
        _, mid, _, choice = data.split(":", 3)
        await handle_existing_meeting_reminder(update, context, int(mid), choice); return
    if data.startswith("meeting:history:"):
        mid = int(data.rsplit(":", 1)[1]); await show_meeting_history(update, context, mid); return
    if data.startswith("meeting:pin:"):
        mid = int(data.rsplit(":", 1)[1]); m = db.get_meeting(mid); db.update_meeting_field(mid, "pinned", 0 if m.get("pinned") else 1, uid, name); await show_meeting_card(update, context, mid); return
    if data.startswith("meeting:ai_extract:"):
        mid = int(data.rsplit(":", 1)[1]); await extract_tasks_from_meeting(update, context, mid); return
    if data == "meeting:stats":
        await show_meeting_stats(update, context); return
    if data == "meeting:minutes_help":
        await edit_or_send(update, context, "برای ثبت صورتجلسه بنویس:\n<code>ملاقات 3: متن صورتجلسه</code>", meetings_menu_keyboard()); return

    # meeting draft value callbacks must be before generic draft_meeting menu
    if data.startswith("draft_meeting:project:"):
        context.user_data.setdefault("draft_meeting", {})["project"] = data.split(":", 2)[2]
        context.user_data.setdefault("draft_meeting", {}).pop("_hint", None)
        await refresh_meeting_draft(update, context); return
    if data.startswith("draft_meeting:rem:"):
        await handle_draft_reminder(update, context, "draft_meeting", data.split(":", 2)[2]); return
    if data.startswith("draft_meeting:participants:"):
        await handle_participant_callback(update, context, data); return

    if data.startswith("draft_meeting:"):
        parts = data.split(":")
        action = parts[1]
        if action == "cancel":
            context.user_data.clear(); await edit_or_send(update, context, "✅ لغو شد", None); return
        if action == "back":
            await refresh_meeting_draft(update, context); return
        if action == "title":
            d = context.user_data.setdefault("draft_meeting", {})
            d["_hint"] = "✏️ عنوان ملاقات را بفرست. بعد از ذخیره، پیام عنوان از چت حذف می‌شود."
            context.user_data["state"] = "await_meeting_title"
            await refresh_meeting_draft(update, context); return
        if action == "time":
            d = context.user_data.setdefault("draft_meeting", {})
            d["_hint"] = "🕒 زمان ملاقات را بفرست. مثال: فردا 10:00 یا 2026-06-25 18:00"
            context.user_data["state"] = "await_meeting_time"
            await refresh_meeting_draft(update, context); return
        if action == "location":
            d = context.user_data.setdefault("draft_meeting", {})
            d["_hint"] = "📍 مکان یا لینک ملاقات را بفرست. بعد از ذخیره، پیام حذف می‌شود."
            context.user_data["state"] = "await_meeting_location"
            await refresh_meeting_draft(update, context); return
        if action == "project":
            await edit_or_send(update, context, "📁 پروژه را انتخاب کن:", project_keyboard("draft_meeting")); return
        if action == "participants":
            await edit_or_send(update, context, "👥 شرکت‌کننده‌ها را انتخاب کن:", participants_keyboard("draft_meeting")); return
        if action == "reminder":
            await edit_or_send(update, context, "⏰ یادآوری را انتخاب کن:", reminder_keyboard("draft_meeting")); return
        if action == "save":
            await save_meeting_draft(update, context); return
    # summary
    if data.startswith("summary:"):
        await run_summary_range(update, context, data.split(":", 1)[1]); return

    # reports
    if data == "report:stats": await show_stats(update, context); return
    if data == "report:daily": await show_daily_report(update, context); return
    if data == "report:weekly": await show_weekly_report(update, context); return
    if data == "report:excel": await export_excel(update, context); return
    if data == "report:pdf": await export_pdf(update, context); return

    # agent confirmations
    if data == "agent:apply": await apply_pending_actions(update, context); return
    if data == "agent:reject": context.user_data.pop("pending_actions", None); await edit_or_send(update, context, "❌ رد شد", None); return


async def handle_draft_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE, draft_key: str, choice: str) -> None:
    d = context.user_data.setdefault(draft_key, {})
    base = now_local()
    if choice == "none":
        d["reminder_at"] = None
    elif choice == "1h":
        d["reminder_at"] = local_to_iso(base + timedelta(hours=1))
    elif choice == "2h":
        d["reminder_at"] = local_to_iso(base + timedelta(hours=2))
    elif choice == "today18":
        dt = base.replace(hour=18, minute=0, second=0)
        if dt < base: dt += timedelta(days=1)
        d["reminder_at"] = local_to_iso(dt)
    elif choice == "tomorrow10":
        day = base.date() + timedelta(days=1)
        d["reminder_at"] = local_to_iso(datetime(day.year, day.month, day.day, 10, 0, tzinfo=APP_TZ))
    elif choice == "custom":
        context.user_data["state"] = "await_task_custom_reminder" if draft_key == "draft_task" else "await_meeting_custom_reminder"
        await edit_or_send(update, context, "زمان را بنویس:\nمثال: <code>فردا 10:00</code>\nیا: <code>2026-06-25 18:00</code>", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"{draft_key}:back")]])); return
    if draft_key == "draft_task": await refresh_task_draft(update, context)
    else: await refresh_meeting_draft(update, context)


async def handle_existing_task_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int, choice: str) -> None:
    uid, name, _ = actor(update); base = now_local(); reminder = None
    if choice == "none": reminder = None
    elif choice == "1h": reminder = local_to_iso(base + timedelta(hours=1))
    elif choice == "2h": reminder = local_to_iso(base + timedelta(hours=2))
    elif choice == "today18":
        dt = base.replace(hour=18, minute=0, second=0)
        if dt < base: dt += timedelta(days=1)
        reminder = local_to_iso(dt)
    elif choice == "tomorrow10":
        day = base.date() + timedelta(days=1)
        reminder = local_to_iso(datetime(day.year, day.month, day.day, 10, 0, tzinfo=APP_TZ))
    elif choice == "custom":
        context.user_data["state"] = "await_existing_task_reminder"; context.user_data["active_task_id"] = task_id
        await edit_or_send(update, context, "زمان یادآوری را بنویس. مثال: فردا 10:00", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"task:open:{task_id}")]])); return
    db.set_task_reminder(task_id, reminder, actor_id=uid, actor_name=name)
    await show_task_card(update, context, task_id)


async def handle_existing_meeting_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE, meeting_id: int, choice: str) -> None:
    uid, name, _ = actor(update); base = now_local(); reminder = None
    if choice == "none": reminder = None
    elif choice == "1h": reminder = local_to_iso(base + timedelta(hours=1))
    elif choice == "2h": reminder = local_to_iso(base + timedelta(hours=2))
    elif choice == "today18":
        dt = base.replace(hour=18, minute=0, second=0)
        if dt < base: dt += timedelta(days=1)
        reminder = local_to_iso(dt)
    elif choice == "tomorrow10":
        day = base.date() + timedelta(days=1)
        reminder = local_to_iso(datetime(day.year, day.month, day.day, 10, 0, tzinfo=APP_TZ))
    elif choice == "custom":
        context.user_data["state"] = "await_existing_meeting_reminder"; context.user_data["active_meeting_id"] = meeting_id
        await edit_or_send(update, context, "زمان یادآوری ملاقات را بنویس. مثال: فردا 10:00", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"meeting:open:{meeting_id}")]])); return
    db.set_meeting_reminder(meeting_id, reminder, uid, name)
    await show_meeting_card(update, context, meeting_id)


async def handle_participant_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    d = context.user_data.setdefault("draft_meeting", {})
    parts = data.split(":")
    if parts[-1] == "none":
        d["participants"] = []
        await refresh_meeting_draft(update, context); return
    if parts[-1] == "done":
        await refresh_meeting_draft(update, context); return
    user_id = int(parts[-1])
    user = db.get_user(user_id) or {"user_id": user_id, "full_name": str(user_id)}
    participants = d.setdefault("participants", [])
    if not any(int(p.get("user_id", 0)) == user_id for p in participants):
        participants.append({"user_id": user_id, "name": user.get("full_name") or user.get("username") or str(user_id)})
    await edit_or_send(update, context, draft_meeting_text(d), participants_keyboard("draft_meeting"))


# ----------------------- detail views -----------------------
async def show_task_notes(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int) -> None:
    notes = db.list_task_notes(task_id)
    if not notes:
        text = "شرحی ثبت نشده."
    else:
        lines = [f"📝 <b>شرح‌های کار #{task_id}</b>"]
        for n in notes[:15]:
            lines.append(f"• {html(n.get('note'))}\n  <small>{html(n.get('full_name') or '-')}</small>")
        text = "\n".join(lines)
    await edit_or_send(update, context, text, InlineKeyboardMarkup([[InlineKeyboardButton("➕ شرح", callback_data=f"task:addnote:{task_id}"), InlineKeyboardButton("🔙 کار", callback_data=f"task:open:{task_id}")]]))


async def show_task_files(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int) -> None:
    files = db.list_task_files(task_id)
    if not files:
        text = "فایلی ثبت نشده. همین الان فایل/عکس را بفرست تا به این کار وصل شود. همچنین می‌توانی روی کارت کار Reply کنی."
    else:
        lines = [f"📎 <b>فایل‌های کار #{task_id}</b>"]
        for f in files[:20]:
            lines.append(f"• {html(f.get('file_type'))} | {html(f.get('caption') or '-')}")
        text = "\n".join(lines)
    await edit_or_send(update, context, text, InlineKeyboardMarkup([[InlineKeyboardButton("🔙 کار", callback_data=f"task:open:{task_id}")]]))


async def show_checklist(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int) -> None:
    items = db.list_checklist(task_id)
    if not items:
        text = "چک‌لیستی ثبت نشده."
        rows = []
    else:
        lines = [f"☑️ <b>چک‌لیست کار #{task_id}</b>"]
        rows = []
        for it in items:
            mark = "☑" if it.get("done") else "☐"
            lines.append(f"{mark} {html(it.get('text'))}")
            rows.append([InlineKeyboardButton(f"{mark} {short(it.get('text'), 28)}", callback_data=f"check:toggle:{it['id']}")])
        text = "\n".join(lines)
    rows.append([InlineKeyboardButton("➕ چک‌لیست", callback_data=f"task:addcheck:{task_id}"), InlineKeyboardButton("🔙 کار", callback_data=f"task:open:{task_id}")])
    await edit_or_send(update, context, text, InlineKeyboardMarkup(rows))


async def show_task_history(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int) -> None:
    hist = db.list_task_history(task_id)
    if not hist:
        text = "تاریخچه‌ای وجود ندارد."
    else:
        lines = [f"🧾 <b>تاریخچه کار #{task_id}</b>"]
        for h in hist[:20]:
            lines.append(f"• {html(h.get('action'))}: {html(h.get('new_value') or '')}\n  {html(h.get('actor_name') or h.get('actor_type') or '-')} | {html(h.get('created_at'))}")
        text = "\n".join(lines)
    await edit_or_send(update, context, text, InlineKeyboardMarkup([[InlineKeyboardButton("🔙 کار", callback_data=f"task:open:{task_id}")]]))


async def show_meeting_minutes(update: Update, context: ContextTypes.DEFAULT_TYPE, meeting_id: int) -> None:
    mins = db.list_meeting_minutes(meeting_id)
    if not mins:
        text = "صورتجلسه‌ای ثبت نشده."
    else:
        lines = [f"📝 <b>صورتجلسه‌های ملاقات #{meeting_id}</b>"]
        for m in mins[:15]:
            lines.append(f"• {html(m.get('minutes'))}\n  {html(m.get('full_name') or '-')}")
        text = "\n".join(lines)
    await edit_or_send(update, context, text, InlineKeyboardMarkup([[InlineKeyboardButton("➕ صورتجلسه", callback_data=f"meeting:addminutes:{meeting_id}"), InlineKeyboardButton("🔙 ملاقات", callback_data=f"meeting:open:{meeting_id}")]]))


async def show_meeting_files(update: Update, context: ContextTypes.DEFAULT_TYPE, meeting_id: int) -> None:
    files = db.list_meeting_files(meeting_id)
    if not files:
        text = "فایلی برای ملاقات ثبت نشده. همین الان فایل/عکس را بفرست تا به این ملاقات وصل شود."
    else:
        lines = [f"📎 <b>فایل‌های ملاقات #{meeting_id}</b>"]
        for f in files[:20]:
            lines.append(f"• {html(f.get('file_type'))} | {html(f.get('caption') or '-')}")
        text = "\n".join(lines)
    await edit_or_send(update, context, text, InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ملاقات", callback_data=f"meeting:open:{meeting_id}")]]))


async def show_meeting_history(update: Update, context: ContextTypes.DEFAULT_TYPE, meeting_id: int) -> None:
    hist = db.list_meeting_history(meeting_id)
    if not hist:
        text = "تاریخچه‌ای وجود ندارد."
    else:
        lines = [f"🧾 <b>تاریخچه ملاقات #{meeting_id}</b>"]
        for h in hist[:20]:
            lines.append(f"• {html(h.get('action'))}: {html(h.get('new_value') or '')}\n  {html(h.get('actor_name') or h.get('actor_type') or '-')} | {html(h.get('created_at'))}")
        text = "\n".join(lines)
    await edit_or_send(update, context, text, InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ملاقات", callback_data=f"meeting:open:{meeting_id}")]]))


# ----------------------- text message handler -----------------------
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update, private=(update.effective_chat.type == "private" if update.effective_chat else False))
    msg = update.effective_message
    if not msg or not msg.text:
        return
    text = clean_text(msg.text)
    uid, name, username = actor(update)
    cid = chat_id_of(update)
    if cid:
        try:
            db.log_message(cid, msg.message_id, uid, name, username, text, msg.reply_to_message.message_id if msg.reply_to_message else None)
        except Exception as e:
            logger.debug("log message failed: %s", e)

    # menu buttons must be handled before state to avoid creating tasks titled "کارها"
    intent = is_main_menu_intent(text)
    if intent:
        await maybe_delete_user_menu(update)
        await handle_menu_intent(update, context, intent)
        return

    state = context.user_data.get("state")
    mode = context.user_data.get("mode")

    if mode == "gpt_chat":
        await run_gpt_chat(update, context, text)
        return
    if mode == "agent_chat":
        await run_agent_chat(update, context, text)
        return

    if state:
        await handle_state_text(update, context, state, text)
        return

    # reply note/file-style text to task/meeting card
    if msg.reply_to_message and cid:
        ent = db.find_ui_entity(cid, msg.reply_to_message.message_id)
        if ent and ent["entity_type"] == "task":
            db.add_task_note(int(ent["entity_id"]), text, uid, name, source="reply")
            await temp_reply(update, context, "✅ ثبت شد")
            return
        if ent and ent["entity_type"] == "meeting":
            db.add_meeting_minutes(int(ent["entity_id"]), text, user_id=uid, full_name=name, source="reply")
            await temp_reply(update, context, "✅ ثبت شد")
            return

    # natural commands
    if await handle_natural_text(update, context, text):
        return

    # Do not create random tasks and do not spam unknown text.
    # Unknown normal messages are kept as chat history for Smart Manager, but no warning is sent.
    return


async def handle_menu_intent(update: Update, context: ContextTypes.DEFAULT_TYPE, intent: str) -> None:
    context.user_data.pop("state", None)
    if intent == "new_task": await start_task_draft(update, context)
    elif intent == "tasks": await show_tasks(update, context)
    elif intent == "smart": await run_smart_manager(update, context)
    elif intent == "summary": await summary_cmd(update, context)
    elif intent == "voice_help": await safe_reply(update, "🎙 ویس بفرست. مثال: «کار شماره یک انجام شد» یا «برای کار سه بنویس...»", reply_markup=main_inline_keyboard() if chat_is_group(update) else main_keyboard())
    elif intent == "gpt_chat": await gpt_cmd(update, context)
    elif intent == "agent_chat": await agent_mode_cmd(update, context)
    elif intent == "reports": await reports_cmd(update, context)
    elif intent == "members": await members_cmd(update, context)
    elif intent == "profile": await whoami_cmd(update, context)
    elif intent == "help": await help_cmd(update, context)
    elif intent == "meetings": await show_meetings_menu(update, context)
    elif intent == "home": await show_home(update, context, "برگشت")
    elif intent == "exit": await exit_cmd(update, context)


async def handle_state_text(update: Update, context: ContextTypes.DEFAULT_TYPE, state: str, text: str) -> None:
    uid, name, _ = actor(update)
    if is_back(text):
        context.user_data.pop("state", None)
        if context.user_data.get("draft_task"):
            await temp_reply(update, context, "✅ برگشت")
            await refresh_task_draft(update, context)
        elif context.user_data.get("draft_meeting"):
            await temp_reply(update, context, "✅ برگشت")
            await refresh_meeting_draft(update, context)
        else:
            await show_home(update, context, "برگشت")
        return

    if state == "await_task_title":
        await delete_user_message_if_possible(update)
        d = context.user_data.setdefault("draft_task", {})
        d["title"] = text
        d.pop("_hint", None)
        context.user_data.pop("state", None)
        await refresh_task_draft(update, context)
        return
    if state == "await_task_custom_reminder":
        await delete_user_message_if_possible(update)
        dt = parse_datetime_text(text)
        if dt is None and "بدون" not in text:
            context.user_data.pop("state", None)
            d = context.user_data.setdefault("draft_task", {})
            d["_hint"] = "❌ فرمت زمان اشتباه است. دوباره از دکمه یادآوری، زمان دلخواه را انتخاب کن."
            await refresh_task_draft(update, context)
            return
        d = context.user_data.setdefault("draft_task", {})
        d["reminder_at"] = dt
        d.pop("_hint", None)
        context.user_data.pop("state", None)
        await refresh_task_draft(update, context)
        return
    if state == "await_existing_task_reminder":
        tid = int(context.user_data.get("active_task_id") or 0)
        dt = parse_datetime_text(text)
        context.user_data.pop("state", None)
        if not tid or (dt is None and "بدون" not in text):
            await safe_reply(update, "❌ فرمت زمان اشتباه است. مثال: 2026-06-25 18:00")
            return
        db.set_task_reminder(tid, dt, actor_id=uid, actor_name=name)
        await temp_reply(update, context, "✅ ذخیره شد")
        await show_task_card(update, context, tid)
        return
    if state == "await_task_note":
        tid = int(context.user_data.get("active_task_id") or 0)
        context.user_data.pop("state", None)
        if tid:
            db.add_task_note(tid, text, uid, name)
            await temp_reply(update, context, "✅ ثبت شد")
            await show_task_card(update, context, tid)
        return
    if state == "await_check_item":
        tid = int(context.user_data.get("active_task_id") or 0)
        context.user_data.pop("state", None)
        if tid:
            db.add_checklist_item(tid, text, uid, name)
            await temp_reply(update, context, "✅ ثبت شد")
            await show_checklist(update, context, tid)
        return

    # meeting states
    if state == "await_meeting_title":
        await delete_user_message_if_possible(update)
        d = context.user_data.setdefault("draft_meeting", {})
        d["title"] = text
        d.pop("_hint", None)
        context.user_data.pop("state", None)
        await refresh_meeting_draft(update, context)
        return
    if state == "await_meeting_time":
        await delete_user_message_if_possible(update)
        dt = parse_datetime_text(text)
        context.user_data.pop("state", None)
        d = context.user_data.setdefault("draft_meeting", {})
        if dt is None:
            d["_hint"] = "❌ فرمت زمان اشتباه است. دوباره دکمه زمان را بزن. مثال: فردا 10:00"
            await refresh_meeting_draft(update, context)
            return
        d["start_at"] = dt
        d.pop("_hint", None)
        await refresh_meeting_draft(update, context)
        return
    if state == "await_meeting_location":
        await delete_user_message_if_possible(update)
        d = context.user_data.setdefault("draft_meeting", {})
        d["location"] = text
        d.pop("_hint", None)
        context.user_data.pop("state", None)
        await refresh_meeting_draft(update, context)
        return
    if state == "await_meeting_custom_reminder":
        dt = parse_datetime_text(text)
        context.user_data.pop("state", None)
        if dt is None and "بدون" not in text:
            await safe_reply(update, "❌ فرمت زمان اشتباه است. مثال: فردا 10:00")
        else:
            context.user_data.setdefault("draft_meeting", {})["reminder_at"] = dt
            await temp_reply(update, context, "✅ ذخیره شد")
        await refresh_meeting_draft(update, context)
        return
    if state == "await_existing_meeting_reminder":
        mid = int(context.user_data.get("active_meeting_id") or 0)
        dt = parse_datetime_text(text)
        context.user_data.pop("state", None)
        if not mid or (dt is None and "بدون" not in text):
            await safe_reply(update, "❌ فرمت زمان اشتباه است. مثال: فردا 10:00")
            return
        db.set_meeting_reminder(mid, dt, uid, name)
        await temp_reply(update, context, "✅ ذخیره شد")
        await show_meeting_card(update, context, mid)
        return
    if state == "await_meeting_minutes":
        mid = int(context.user_data.get("active_meeting_id") or 0)
        context.user_data.pop("state", None)
        if mid:
            db.add_meeting_minutes(mid, text, user_id=uid, full_name=name)
            await temp_reply(update, context, "✅ ثبت شد")
            await show_meeting_card(update, context, mid)
        return

    context.user_data.pop("state", None)
    await temp_reply(update, context, "✅ برگشت")


async def handle_natural_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    uid, name, _ = actor(update)
    cid = chat_id_of(update)
    # task note: کار 3: text
    m = re.match(r"(?:کار|تسک|وظیفه)\s*(?:شماره)?\s*#?\s*(\d+)\s*[:：\-]\s*(.+)$", text)
    if m:
        tid = int(m.group(1)); note = m.group(2).strip()
        if db.get_task(tid):
            db.add_task_note(tid, note, uid, name, source="text")
            await temp_reply(update, context, "✅ ثبت شد")
            return True
    # status
    tid = parse_task_id(text)
    if tid:
        status = None
        if any(w in text for w in ["انجام شد", "تموم شد", "تمام شد", "done", "فرستادم"]): status = "انجام شد"
        elif "منتظر" in text: status = "منتظر پاسخ"
        elif any(w in text for w in ["پیگیری", "در حال"]): status = "در حال پیگیری"
        elif any(w in text for w in ["لغو", "کنسل"]): status = "لغو شد"
        if status and db.update_task_status(tid, status, uid, name):
            await temp_reply(update, context, "✅ انجام شد" if status == "انجام شد" else "✅ ذخیره شد")
            return True
    # meeting minutes: ملاقات 3: text
    m = re.match(r"(?:ملاقات|جلسه)\s*(?:شماره)?\s*#?\s*(\d+)\s*[:：\-]\s*(.+)$", text)
    if m:
        mid = int(m.group(1)); minutes = m.group(2).strip()
        if db.get_meeting(mid):
            db.add_meeting_minutes(mid, minutes, user_id=uid, full_name=name, source="text")
            await temp_reply(update, context, "✅ ثبت شد")
            return True
    # create task by free text only if explicit
    for prefix in ["کار جدید:", "تسک جدید:", "وظیفه جدید:", "کار جدید", "تسک جدید", "وظیفه جدید"]:
        if text.startswith(prefix):
            title = clean_text(text.replace(prefix, "", 1))
            if title:
                task_id = db.create_task(title, project=guess_project(title), assigned_by=uid, assigned_by_name=name, chat_id=cid)
                await temp_reply(update, context, "✅ ثبت شد")
                t = db.get_task(task_id)
                if t: await safe_reply(update, task_text(t), task_card_keyboard(task_id))
                return True
    # create meeting explicit
    for prefix in ["ملاقات جدید:", "جلسه جدید:", "ملاقات جدید", "جلسه جدید"]:
        if text.startswith(prefix):
            title = clean_text(text.replace(prefix, "", 1))
            if title:
                mid = db.create_meeting(title, project=guess_project(title), created_by=uid, created_by_name=name, chat_id=cid)
                await temp_reply(update, context, "✅ ثبت شد")
                m = db.get_meeting(mid)
                if m: await safe_reply(update, meeting_text(m), meeting_card_keyboard(mid))
                return True
    return False


# ----------------------- attachments -----------------------
async def on_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg:
        return
    await register_user(update)
    uid, name, _ = actor(update)
    cid = chat_id_of(update)
    caption = clean_text(msg.caption or "")

    target_type, target_id = None, None

    # 1) If user tapped "files" button, attach the next uploaded file directly.
    state = context.user_data.get("state")
    if state == "await_task_file" and context.user_data.get("active_task_id"):
        target_type, target_id = "task", int(context.user_data.get("active_task_id"))
        context.user_data.pop("state", None)
    elif state == "await_meeting_file" and context.user_data.get("active_meeting_id"):
        target_type, target_id = "meeting", int(context.user_data.get("active_meeting_id"))
        context.user_data.pop("state", None)

    # 2) Reply to a saved task/meeting card.
    if target_id is None and msg.reply_to_message and cid:
        ent = db.find_ui_entity(cid, msg.reply_to_message.message_id)
        if ent:
            target_type, target_id = ent["entity_type"], int(ent["entity_id"])

    # 3) Caption with کار 3 / #3 / ملاقات 2.
    if target_id is None:
        tid = parse_task_id(caption)
        mid = parse_meeting_id(caption)
        if tid:
            target_type, target_id = "task", tid
        elif mid:
            target_type, target_id = "meeting", mid

    if target_id is None:
        await temp_reply(update, context, "برای اتصال فایل، اول از منوی کار/ملاقات دکمه 📎 فایل‌ها را بزن یا روی کارت کار Reply کن.")
        return

    file_id, unique, ftype = None, None, "document"
    if msg.photo:
        ph = msg.photo[-1]; file_id, unique, ftype = ph.file_id, ph.file_unique_id, "photo"
    elif msg.document:
        file_id, unique, ftype = msg.document.file_id, msg.document.file_unique_id, "document"
    elif msg.video:
        file_id, unique, ftype = msg.video.file_id, msg.video.file_unique_id, "video"
    elif msg.audio:
        file_id, unique, ftype = msg.audio.file_id, msg.audio.file_unique_id, "audio"
    elif msg.voice:
        file_id, unique, ftype = msg.voice.file_id, msg.voice.file_unique_id, "voice"
    if not file_id:
        return

    if target_type == "task" and db.get_task(int(target_id)):
        db.add_task_file(int(target_id), file_id, unique, ftype, caption, uid, name)
        await delete_user_message_if_possible(update)
        await temp_reply(update, context, "✅ فایل ثبت شد")
    elif target_type == "meeting" and db.get_meeting(int(target_id)):
        db.add_meeting_file(int(target_id), file_id, unique, ftype, caption, uid, name)
        await delete_user_message_if_possible(update)
        await temp_reply(update, context, "✅ فایل ثبت شد")
    else:
        await temp_reply(update, context, "❌ کار یا ملاقات پیدا نشد.")


# ----------------------- voice -----------------------
async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    msg = update.effective_message
    if not msg or not msg.voice:
        return
    if not openai_client:
        await temp_reply(update, context, "❌ OPENAI_API_KEY تنظیم نشده.")
        return
    try:
        await msg.chat.send_action(ChatAction.TYPING)
        f = await context.bot.get_file(msg.voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg") as tmp:
            await f.download_to_drive(tmp.name)
            with open(tmp.name, "rb") as audio:
                tr = openai_client.audio.transcriptions.create(model=OPENAI_TRANSCRIBE_MODEL, file=audio, language="fa")
        text = clean_text(getattr(tr, "text", "") or "")
        if not text:
            await temp_reply(update, context, "❌ فرمان واضح نبود.")
            return
        await safe_reply(update, f"📝 متن ویس:\n{html(text)}")
        handled = await handle_voice_intent(update, context, text)
        if not handled:
            await temp_reply(update, context, "❌ فرمان واضح نبود.")
    except Exception as e:
        logger.exception("voice failed")
        await safe_reply(update, f"❌ خطای ویس: {html(str(e)[:120])}")


async def handle_voice_intent(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    k = text_key(text)
    if "لیست" in k and "کار" in k:
        await show_tasks(update, context); return True
    if "مدیر هوشمند" in k:
        await run_smart_manager(update, context); return True
    if "تحلیل" in k and "چت" in k:
        await summary_cmd(update, context); return True
    if "ملاقات" in k or "جلسه" in k:
        if "جدید" in k:
            title = re.sub(r".*(?:ملاقات|جلسه)\s*جدید", "", text).strip(" :،") or "ملاقات جدید"
            uid, name, _ = actor(update)
            mid = db.create_meeting(title, project=guess_project(title), created_by=uid, created_by_name=name, chat_id=chat_id_of(update))
            await temp_reply(update, context, "✅ ثبت شد")
            m = db.get_meeting(mid)
            if m: await safe_reply(update, meeting_text(m), meeting_card_keyboard(mid))
            return True
    if await handle_natural_text(update, context, text):
        return True
    if "کار جدید" in k or "تسک جدید" in k:
        title = re.sub(r".*(?:کار|تسک|وظیفه)\s*جدید", "", text).strip(" :،") or "کار جدید"
        uid, name, _ = actor(update)
        task_id = db.create_task(title, project=guess_project(title), assigned_by=uid, assigned_by_name=name, chat_id=chat_id_of(update))
        await temp_reply(update, context, "✅ ثبت شد")
        return True
    return False


# ----------------------- OpenAI -----------------------
def openai_available() -> bool:
    return openai_client is not None


async def ask_openai(prompt: str, system: str = "", temperature: float = 0.2) -> str:
    if not openai_client:
        return "❌ OPENAI_API_KEY تنظیم نشده."
    def _call():
        resp = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system or "You are a helpful Persian business assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""
    try:
        return await asyncio.to_thread(_call)
    except Exception as e:
        logger.exception("OpenAI request failed")
        return f"❌ خطای OpenAI: {str(e)[:180]}"


async def run_gpt_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if not openai_client:
        await temp_reply(update, context, "❌ OPENAI_API_KEY تنظیم نشده.")
        return
    await temp_reply(update, context, "⏳ در حال پردازش…", seconds=3)
    answer = await ask_openai(text, system="تو دستیار فارسی هستی. در این حالت فقط جواب متنی بده و هیچ کاری در دیتابیس انجام نده.")
    await safe_reply(update, html(answer), reply_markup=gpt_exit_keyboard(), parse_mode=ParseMode.HTML)


async def run_agent_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if not openai_client:
        await temp_reply(update, context, "❌ OPENAI_API_KEY تنظیم نشده.")
        return
    cid = chat_id_of(update)
    uid, name, _ = actor(update)
    tasks = db.list_tasks(include_done=False, include_deleted=False, limit=50)
    meetings = db.list_meetings(upcoming_only=True, limit=30)
    messages = db.get_recent_messages(cid, limit=30) if cid else []
    prompt = (
        "دستور مستقیم کاربر را به اکشن‌های عملیاتی تبدیل کن. اگر لازم است کار یا ملاقات بساز، شرح اضافه کن، وضعیت تغییر بده یا یادآوری تنظیم کن. "
        "فقط JSON معتبر بده. اگر دستور فقط سؤال/مشورت است و اکشنی ندارد، comment بده.\n\n"
        f"دستور کاربر: {text}\n\n"
        f"کارهای باز: {json.dumps(tasks, ensure_ascii=False, default=str)}\n\n"
        f"ملاقات‌های آینده: {json.dumps(meetings, ensure_ascii=False, default=str)}\n\n"
        f"پیام‌های اخیر: {json.dumps(messages[-20:], ensure_ascii=False, default=str)}"
    )
    await temp_reply(update, context, "⏳ ایجنت در حال بررسی…", seconds=3)
    raw = await ask_openai(prompt, system=AGENT_SYSTEM, temperature=0.1)
    actions, comment = parse_agent_json(raw)
    if not actions and comment:
        await safe_reply(update, html(comment), reply_markup=agent_exit_keyboard(), parse_mode=ParseMode.HTML)
        return
    if not actions:
        await temp_reply(update, context, "اطلاعات کافی برای انجام کار پیدا نکردم.")
        return
    if SMART_AUTO_APPLY:
        summary = await execute_actions(update, context, actions, actor_type="AI")
        await safe_reply(update, summary or "✅ اعمال شد", reply_markup=agent_exit_keyboard())
    else:
        context.user_data["pending_actions"] = actions
        context.user_data["pending_comment"] = comment
        lines = ["🤖 ایجنت می‌خواهد این کارها را انجام دهد:"]
        for i, a in enumerate(actions, 1):
            lines.append(f"{i}. {html(action_label(a))}")
        if comment:
            lines.append(f"\n🧠 {html(comment)}")
        await safe_reply(update, "\n".join(lines), reply_markup=confirm_actions_keyboard())


async def run_summary_range(update: Update, context: ContextTypes.DEFAULT_TYPE, range_key: str) -> None:
    cid = chat_id_of(update)
    if not cid:
        await edit_or_send(update, context, "❌ چت پیدا نشد.")
        return
    now = datetime.now(timezone.utc)
    if range_key == "1h": since = now - timedelta(hours=1)
    elif range_key == "2h": since = now - timedelta(hours=2)
    elif range_key == "yesterday": since = now - timedelta(days=1)
    elif range_key == "7d": since = now - timedelta(days=7)
    else:
        since = now - timedelta(hours=2)
    messages = db.get_recent_messages(cid, limit=120, since_iso=since.replace(microsecond=0).isoformat())
    if not messages:
        await edit_or_send(update, context, "پیامی برای تحلیل پیدا نشد.")
        return
    prompt = "پیام‌های زیر را خلاصه و تحلیل کن. خروجی فارسی بده با بخش‌های: خلاصه گفتگو، تصمیم‌ها، کارهای قابل پیگیری، موارد مبهم، ریسک‌ها، اشتباهات برنامه‌ریزی، پیشنهاد مدیریتی.\n\n"
    prompt += "\n".join([f"{m.get('full_name')}: {m.get('text')}" for m in messages[-80:]])
    await temp_reply(update, context, "⏳ در حال تحلیل…", seconds=3)
    answer = await ask_openai(prompt, system="تو مدیر عملیات فارسی هستی. کوتاه، دقیق و عملیاتی تحلیل کن.")
    await safe_reply(update, html(answer), parse_mode=ParseMode.HTML)


async def run_smart_manager(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cid = chat_id_of(update)
    if not cid:
        await safe_reply(update, "❌ چت پیدا نشد.")
        return
    if not openai_client:
        await temp_reply(update, context, "❌ OPENAI_API_KEY تنظیم نشده.")
        return
    messages = db.get_recent_messages(cid, limit=80)
    tasks = db.list_tasks(include_done=False, include_deleted=False, limit=50)
    meetings = db.list_meetings(upcoming_only=True, limit=30)
    prompt = build_agent_prompt(messages, tasks, meetings)
    await temp_reply(update, context, "⏳ مدیر هوشمند در حال بررسی…", seconds=3)
    raw = await ask_openai(prompt, system=AGENT_SYSTEM, temperature=0.1)
    actions, comment = parse_agent_json(raw)
    if not actions and not comment:
        await temp_reply(update, context, "اطلاعات کافی برای اعمال خودکار وجود ندارد.")
        return
    if actions and SMART_AUTO_APPLY:
        summary = await execute_actions(update, context, actions, actor_type="AI")
        if comment:
            await safe_reply(update, "🧠 کامنت مدیریتی دارم. اجازه می‌دهی نشان بدهم؟", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("نمایش کامنت", callback_data="agent:comment:show")]]))
        await temp_reply(update, context, summary or "✅ اعمال شد")
    else:
        context.user_data["pending_actions"] = actions
        context.user_data["pending_comment"] = comment
        lines = ["🤖 پیشنهادهای عملیاتی:"]
        for i, a in enumerate(actions, 1):
            lines.append(f"{i}. {html(action_label(a))}")
        if comment:
            lines.append("\n🧠 کامنت مدیریتی هم دارم.")
        await safe_reply(update, "\n".join(lines), reply_markup=confirm_actions_keyboard())


AGENT_SYSTEM = """
تو مدیر عملیاتی فارسی برای ربات تلگرام SAM PRO Team Manager هستی.
فقط JSON معتبر بده، بدون markdown و بدون توضیح بیرون JSON.

قانون‌های مهم:
1) دستور کاربر را تبدیل به actionهای دقیق کن.
2) اگر کاربر گفت «بساز/بگذار/یادآوری کن/اضافه کن/انجام شد/شرح بده»، باید action مناسب بدهی.
3) اگر نام پروژه از متن مشخص بود از یکی از این‌ها استفاده کن: تخته، میوه، پتروشیمی، مالی، غلات، غیره.
4) وضعیت‌های مجاز کار: باز، در حال پیگیری، منتظر پاسخ، انجام شد، لغو شد.
5) زمان‌های نسبی مثل «فردا ساعت ۱۰»، «یک ساعت دیگر»، «دوشنبه» را در reminder_text یا start_text همان‌طور متنی بده؛ ربات خودش تبدیل می‌کند.
6) اگر task_id را دقیق نمی‌دانی ولی عنوان کار خیلی شبیه یکی از open_tasks است، task_id همان کار را استفاده کن. اگر مطمئن نیستی، action نده و manager_comment سوال بپرس.
7) برای متن‌های عمومی و حرف‌های معمولی action نساز.

اکشن‌های مجاز:
create_task(title, project, priority, assigned_to_name, reminder_text)
update_task_status(task_id, status)
add_task_note(task_id, note)
set_task_reminder(task_id, reminder_text)
create_meeting(title, project, start_text, location, participants)
add_meeting_minutes(meeting_id, minutes)
manager_comment(text)

JSON schema دقیق:
{"actions":[{"type":"create_task","title":"...","project":"...","priority":"متوسط","assigned_to_name":"...","reminder_text":"..."}],"manager_comment":""}
"""


def build_agent_prompt(messages: List[Dict[str, Any]], tasks: List[Dict[str, Any]], meetings: List[Dict[str, Any]]) -> str:
    return json.dumps({
        "instruction": "پیام‌ها و کارها را تحلیل کن و فقط اکشن‌های مطمئن و قابل اجرا بده.",
        "open_tasks": [{"id": t["id"], "title": t["title"], "project": t.get("project"), "status": t.get("status"), "assignee": t.get("assigned_to_name")} for t in tasks],
        "upcoming_meetings": [{"id": m["id"], "title": m["title"], "project": m.get("project"), "start_at": m.get("start_at")} for m in meetings],
        "messages": [{"from": m.get("full_name"), "text": m.get("text"), "time": m.get("created_at")} for m in messages[-60:]],
    }, ensure_ascii=False)


def parse_agent_json(raw: str) -> Tuple[List[Dict[str, Any]], str]:
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?", "", text).strip()
            text = re.sub(r"```$", "", text).strip()
        data = json.loads(text)
        return data.get("actions") or [], data.get("manager_comment") or ""
    except Exception:
        return [], raw[:1200] if raw else ""


def action_label(a: Dict[str, Any]) -> str:
    t = a.get("type")
    if t == "create_task": return f"ساخت کار: {a.get('title')}"
    if t == "update_task_status": return f"تغییر وضعیت کار #{a.get('task_id')} به {a.get('status')}"
    if t == "add_task_note": return f"افزودن شرح به کار #{a.get('task_id')}: {short(a.get('note'), 60)}"
    if t == "create_meeting": return f"ساخت ملاقات: {a.get('title')}"
    if t == "add_meeting_minutes": return f"افزودن صورتجلسه به ملاقات #{a.get('meeting_id')}"
    return str(a)


async def apply_pending_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actions = context.user_data.pop("pending_actions", [])
    comment = context.user_data.pop("pending_comment", "")
    summary = await execute_actions(update, context, actions, actor_type="AI")
    if comment:
        summary += "\n\n🧠 کامنت مدیریتی:\n" + html(comment)
    await edit_or_send(update, context, summary or "✅ اعمال شد", None)


async def execute_actions(update: Update, context: ContextTypes.DEFAULT_TYPE, actions: List[Dict[str, Any]], actor_type: str = "AI") -> str:
    uid, name, _ = actor(update)
    cid = chat_id_of(update)
    done = []
    for a in actions[:15]:
        typ = a.get("type")
        try:
            if typ == "create_task":
                title = clean_text(a.get("title") or "")
                if not title: continue
                rem = parse_datetime_text(a.get("reminder_text") or "") if a.get("reminder_text") else None
                assignee_name = clean_text(a.get("assigned_to_name") or "")
                assignee_id = None
                if assignee_name:
                    for mem in db.get_members(100):
                        mn = clean_text(mem.get("full_name") or mem.get("username") or "")
                        if assignee_name in mn or mn in assignee_name:
                            assignee_id = mem.get("user_id"); assignee_name = mem.get("full_name") or mem.get("username") or assignee_name; break
                tid = db.create_task(title, project=a.get("project") or guess_project(title), priority=a.get("priority") or "متوسط", assigned_by=uid, assigned_by_name=name, assigned_to=assignee_id, assigned_to_name=assignee_name or None, chat_id=cid, reminder_at=rem, actor_type=actor_type)
                done.append(f"✅ کار #{tid} ساخته شد")
            elif typ == "update_task_status":
                tid = int(a.get("task_id")); status = a.get("status") or "باز"
                if db.update_task_status(tid, status, uid, name, actor_type): done.append(f"✅ کار #{tid} → {status}")
            elif typ == "add_task_note":
                tid = int(a.get("task_id")); note = a.get("note") or ""
                if db.get_task(tid) and note:
                    db.add_task_note(tid, note, uid, name, source="AI", actor_type=actor_type); done.append(f"✅ شرح به کار #{tid} اضافه شد")
            elif typ == "set_task_reminder":
                tid = int(a.get("task_id")); rem = parse_datetime_text(a.get("reminder_text") or "")
                if db.set_task_reminder(tid, rem, actor_id=uid, actor_name=name, actor_type=actor_type): done.append(f"✅ یادآوری کار #{tid} تنظیم شد")
            elif typ == "create_meeting":
                title = clean_text(a.get("title") or "")
                if not title: continue
                start = parse_datetime_text(a.get("start_text") or "") if a.get("start_text") else None
                participants = [{"name": p} for p in (a.get("participants") or [])]
                mid = db.create_meeting(title, project=a.get("project") or guess_project(title), start_at=start, location=a.get("location"), participants=participants, created_by=uid, created_by_name=name, chat_id=cid, actor_type=actor_type)
                done.append(f"✅ ملاقات #{mid} ساخته شد")
            elif typ == "add_meeting_minutes":
                mid = int(a.get("meeting_id")); minutes = a.get("minutes") or ""
                if db.get_meeting(mid) and minutes:
                    db.add_meeting_minutes(mid, minutes, user_id=uid, full_name=name, source="AI", actor_type=actor_type); done.append(f"✅ صورتجلسه به ملاقات #{mid} اضافه شد")
        except Exception as e:
            logger.warning("agent action failed %s: %s", a, e)
    return "\n".join(done)


async def extract_tasks_from_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE, meeting_id: int) -> None:
    mins = db.list_meeting_minutes(meeting_id, limit=10)
    if not mins:
        await edit_or_send(update, context, "صورتجلسه‌ای برای استخراج وجود ندارد.", meeting_card_keyboard(meeting_id)); return
    prompt = "از صورتجلسه‌های زیر اکشن‌های create_task و add_task_note استخراج کن. فقط JSON schema مدیر هوشمند را بده.\n" + "\n".join([m["minutes"] for m in mins])
    raw = await ask_openai(prompt, system=AGENT_SYSTEM)
    actions, comment = parse_agent_json(raw)
    context.user_data["pending_actions"] = actions
    context.user_data["pending_comment"] = comment
    await edit_or_send(update, context, "🤖 کارهای استخراج‌شده از صورتجلسه آماده اعمال هستند.", confirm_actions_keyboard())


# ----------------------- reports/exports -----------------------
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = db.task_stats(); ms = db.meeting_stats()
    lines = [
        "📊 <b>آمار</b>",
        f"کل کارها: {s['total']}",
        f"کارهای باز: {s['open']}",
        f"کارهای فوری: {s['urgent']}",
        f"ملاقات‌ها: {ms['total']} | آینده: {ms['upcoming']} | برگزار شد: {ms['done']}",
        "\nپروژه‌ها:",
    ]
    for p, c in s["by_project"].items(): lines.append(f"• {html(p)}: {c}")
    await edit_or_send(update, context, "\n".join(lines), reports_keyboard())


async def show_meeting_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = db.meeting_stats()
    lines = ["📊 <b>گزارش ملاقات‌ها</b>", f"کل: {s['total']}", f"آینده: {s['upcoming']}", f"برگزار شده: {s['done']}"]
    for p, c in s["by_project"].items(): lines.append(f"• {html(p)}: {c}")
    await edit_or_send(update, context, "\n".join(lines), meetings_menu_keyboard())


async def show_daily_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tasks = db.list_tasks(include_done=True, include_deleted=False, limit=100)
    meetings = db.list_meetings(include_deleted=False, upcoming_only=False, limit=50)
    today = now_local().date()
    lines = [f"📅 <b>گزارش روزانه {today}</b>", "\nکارهای باز:"]
    for t in [x for x in tasks if x.get("status") not in ("انجام شد", "لغو شد")][:10]:
        lines.append(f"• #{t['id']} {html(t['title'])}")
    lines.append("\nملاقات‌های آینده:")
    for m in meetings[:10]:
        lines.append(f"• M#{m['id']} {html(m['title'])} | {html(iso_to_local_text(m.get('start_at')))}")
    await edit_or_send(update, context, "\n".join(lines), reports_keyboard())


async def show_weekly_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = db.task_stats(); ms = db.meeting_stats()
    text = f"📈 <b>گزارش هفتگی</b>\n\nکارهای باز: {s['open']}\nکارهای فوری: {s['urgent']}\nملاقات‌های آینده: {ms['upcoming']}\n\nبرای تحلیل مدیریتی دقیق‌تر از 🧠 مدیر هوشمند استفاده کن."
    await edit_or_send(update, context, text, reports_keyboard())


async def export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
    except Exception:
        await safe_reply(update, "openpyxl نصب نیست. requirements.txt را به‌روزرسانی کن.")
        return
    wb = Workbook()
    ws = wb.active
    ws.title = "Tasks"
    headers = ["ID", "Title", "Project", "Status", "Priority", "Assigned To", "Assigned By", "Reminder", "Repeat", "Created At", "Completed At", "Deleted", "Pinned", "Description", "Notes Count", "Files Count", "Checklist Done/Total"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True); cell.alignment = Alignment(horizontal="center"); cell.fill = PatternFill("solid", fgColor="D9EAF7")
    for r in db.export_task_rows():
        ws.append([r.get("id"), r.get("title"), r.get("project"), r.get("status"), r.get("priority"), r.get("assigned_to_name"), r.get("assigned_by_name"), iso_to_local_text(r.get("reminder_at")), r.get("reminder_repeat"), r.get("created_at"), r.get("completed_at"), r.get("deleted"), r.get("pinned"), r.get("description"), r.get("notes_count"), r.get("files_count"), r.get("checklist")])
    ws2 = wb.create_sheet("Meetings")
    h2 = ["ID", "Title", "Project", "Status", "Start", "Location", "Participants", "Reminder", "Created By", "Minutes Count", "Files Count"]
    ws2.append(h2)
    for cell in ws2[1]:
        cell.font = Font(bold=True); cell.fill = PatternFill("solid", fgColor="D9EAD3")
    for m in db.export_meeting_rows():
        participants = "، ".join([p.get("name", "") for p in (m.get("participants_list") or [])])
        ws2.append([m.get("id"), m.get("title"), m.get("project"), m.get("status"), iso_to_local_text(m.get("start_at")), m.get("location"), participants, iso_to_local_text(m.get("reminder_at")), m.get("created_by_name"), m.get("minutes_count"), m.get("files_count")])
    for sheet in wb.worksheets:
        for col in sheet.columns:
            max_len = max(len(str(c.value or "")) for c in col)
            sheet.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 12), 50)
    bio = io.BytesIO()
    wb.save(bio); bio.seek(0)
    await update.effective_chat.send_document(document=bio, filename=f"sam_pro_export_{now_local().strftime('%Y%m%d_%H%M')}.xlsx", caption="📤 خروجی اکسل")


async def export_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tasks = db.list_tasks(include_done=True, include_deleted=False, limit=200)
    meetings = db.list_meetings(include_deleted=False, upcoming_only=False, limit=100)
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        y = height - 40
        c.setFont("Helvetica-Bold", 14)
        c.drawString(40, y, "SAM PRO Team Manager Report")
        y -= 30
        c.setFont("Helvetica", 10)
        c.drawString(40, y, f"Generated: {now_local().strftime('%Y-%m-%d %H:%M')}")
        y -= 30
        c.drawString(40, y, "Tasks:"); y -= 20
        for t in tasks[:60]:
            line = f"#{t['id']} | {t.get('status')} | {t.get('project')} | {t.get('title')}"
            c.drawString(40, y, line[:110]); y -= 16
            if y < 60: c.showPage(); y = height - 40; c.setFont("Helvetica", 10)
        y -= 10; c.drawString(40, y, "Meetings:"); y -= 20
        for m in meetings[:40]:
            line = f"M#{m['id']} | {m.get('status')} | {m.get('project')} | {m.get('title')}"
            c.drawString(40, y, line[:110]); y -= 16
            if y < 60: c.showPage(); y = height - 40; c.setFont("Helvetica", 10)
        c.save(); buffer.seek(0)
        await update.effective_chat.send_document(document=buffer, filename="sam_pro_report.pdf", caption="📄 گزارش PDF")
    except Exception:
        text = "SAM PRO Team Manager Report\n\nTasks:\n" + "\n".join([f"#{t['id']} | {t.get('status')} | {t.get('title')}" for t in tasks])
        text += "\n\nMeetings:\n" + "\n".join([f"M#{m['id']} | {m.get('status')} | {m.get('title')}" for m in meetings])
        bio = io.BytesIO(text.encode("utf-8")); bio.seek(0)
        await update.effective_chat.send_document(document=bio, filename="sam_pro_report.txt", caption="📄 reportlab نصب نبود؛ خروجی TXT ارسال شد")


# ----------------------- reminder jobs -----------------------
async def reminders_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for t in db.due_tasks(now_iso):
        text = f"⏰ یادآوری کار #{t['id']}\n{html(t.get('title'))}"
        try:
            if t.get("chat_id"):
                await context.bot.send_message(int(t["chat_id"]), text, parse_mode=ParseMode.HTML, reply_markup=task_card_keyboard(int(t["id"])))
            if t.get("assigned_to"):
                u = db.get_user(int(t["assigned_to"]))
                if u and u.get("private_chat_id"):
                    await context.bot.send_message(int(u["private_chat_id"]), text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.warning("task reminder failed: %s", e)
        db.mark_task_reminder_sent(int(t["id"]))
    for m in db.due_meetings(now_iso):
        text = f"⏰ یادآوری ملاقات #{m['id']}\n{html(m.get('title'))}\n{html(iso_to_local_text(m.get('start_at')))}"
        try:
            if m.get("chat_id"):
                await context.bot.send_message(int(m["chat_id"]), text, parse_mode=ParseMode.HTML, reply_markup=meeting_card_keyboard(int(m["id"])))
        except Exception as e:
            logger.warning("meeting reminder failed: %s", e)
        db.mark_meeting_reminder_sent(int(m["id"]))


async def followup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not GROUP_CHAT_ID:
        return
    tasks = db.list_tasks(include_done=False, include_deleted=False, limit=20)
    if not tasks:
        return
    text = "⏱ <b>پیگیری سه‌ساعته</b>\nکارهای باز را بررسی کنید."
    try:
        await context.bot.send_message(int(GROUP_CHAT_ID), text, parse_mode=ParseMode.HTML, reply_markup=tasks_list_keyboard(tasks))
    except Exception as e:
        logger.warning("followup failed: %s", e)


# ----------------------- error handler -----------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("❌ خطای داخلی رخ داد، ولی ربات متوقف نشد.")
    except Exception:
        pass


# ----------------------- app -----------------------
def build_app() -> Application:
    db.init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    # commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("keyboard", keyboard_cmd))
    app.add_handler(CommandHandler("post_menu", post_menu_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("exit", exit_cmd))
    app.add_handler(CommandHandler("cancel", exit_cmd))
    app.add_handler(CommandHandler("tasks", tasks_cmd))
    app.add_handler(CommandHandler("newtask", newtask_cmd))
    app.add_handler(CommandHandler("new", newtask_cmd))
    app.add_handler(CommandHandler("meetings", meetings_cmd))
    app.add_handler(CommandHandler("meeting", meetings_cmd))
    app.add_handler(CommandHandler("summary", summary_cmd))
    app.add_handler(CommandHandler("smart", smart_cmd))
    app.add_handler(CommandHandler("agent", agent_mode_cmd))
    app.add_handler(CommandHandler("gpt", gpt_cmd))
    app.add_handler(CommandHandler("reports", reports_cmd))
    app.add_handler(CommandHandler("report", reports_cmd))
    app.add_handler(CommandHandler("stats", show_stats))
    app.add_handler(CommandHandler("export_excel", export_excel_cmd))
    app.add_handler(CommandHandler("export_pdf", export_pdf_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("restore", restore_cmd))
    app.add_handler(CommandHandler("members", members_cmd))
    app.add_handler(CommandHandler("whoami", whoami_cmd))
    app.add_handler(CommandHandler("chatid", chatid_cmd))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO, on_attachment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(error_handler)

    if app.job_queue:
        app.job_queue.run_repeating(reminders_job, interval=60, first=20, name="reminders")
        app.job_queue.run_repeating(followup_job, interval=max(1, FOLLOWUP_INTERVAL_HOURS) * 3600, first=max(1, FOLLOWUP_INTERVAL_HOURS) * 3600, name="followup")
    return app


if __name__ == "__main__":
    application = build_app()
    print("SAM PRO Team Manager Started...")
    application.run_polling(drop_pending_updates=True)
