# -*- coding: utf-8 -*-
"""
SAM PRO Team Manager - database layer
SQLite storage with automatic migrations for tasks, meetings, messages,
notes, files, checklist, history, reminders and UI mappings.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

DB_PATH = os.getenv("DB_PATH", "sam_pro_team_manager.db")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)


def _json_loads(value: Any, default: Any = None) -> Any:
    if default is None:
        default = []
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                role TEXT DEFAULT 'member',
                private_chat_id INTEGER,
                created_at TEXT,
                last_seen TEXT
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                project TEXT DEFAULT 'غیره',
                status TEXT DEFAULT 'باز',
                priority TEXT DEFAULT 'متوسط',
                assigned_to INTEGER,
                assigned_to_name TEXT,
                assigned_by INTEGER,
                assigned_by_name TEXT,
                chat_id INTEGER,
                reminder_at TEXT,
                reminder_repeat TEXT DEFAULT 'none',
                reminder_sent INTEGER DEFAULT 0,
                description TEXT,
                pinned INTEGER DEFAULT 0,
                deleted INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS task_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                note TEXT NOT NULL,
                user_id INTEGER,
                full_name TEXT,
                source TEXT DEFAULT 'manual',
                created_at TEXT,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS task_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                file_id TEXT NOT NULL,
                file_unique_id TEXT,
                file_type TEXT,
                caption TEXT,
                user_id INTEGER,
                full_name TEXT,
                created_at TEXT,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS task_checklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                done INTEGER DEFAULT 0,
                user_id INTEGER,
                full_name TEXT,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS task_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                actor_id INTEGER,
                actor_name TEXT,
                actor_type TEXT DEFAULT 'user',
                created_at TEXT,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                message_id INTEGER,
                user_id INTEGER,
                full_name TEXT,
                username TEXT,
                text TEXT,
                reply_to_message_id INTEGER,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS ui_messages (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                created_at TEXT,
                PRIMARY KEY(chat_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                project TEXT DEFAULT 'غیره',
                status TEXT DEFAULT 'برنامه‌ریزی‌شده',
                start_at TEXT,
                end_at TEXT,
                location TEXT,
                participants TEXT DEFAULT '[]',
                created_by INTEGER,
                created_by_name TEXT,
                chat_id INTEGER,
                reminder_at TEXT,
                reminder_sent INTEGER DEFAULT 0,
                pinned INTEGER DEFAULT 0,
                deleted INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS meeting_minutes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id INTEGER NOT NULL,
                minutes TEXT NOT NULL,
                summary TEXT,
                decisions TEXT,
                user_id INTEGER,
                full_name TEXT,
                source TEXT DEFAULT 'manual',
                created_at TEXT,
                FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS meeting_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id INTEGER NOT NULL,
                file_id TEXT NOT NULL,
                file_unique_id TEXT,
                file_type TEXT,
                caption TEXT,
                user_id INTEGER,
                full_name TEXT,
                created_at TEXT,
                FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS meeting_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                actor_id INTEGER,
                actor_name TEXT,
                actor_type TEXT DEFAULT 'user',
                created_at TEXT,
                FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
            );
            """
        )

        # migrations for older databases
        for table, cols in {
            "users": {
                "private_chat_id": "INTEGER",
                "role": "TEXT DEFAULT 'member'",
                "created_at": "TEXT",
                "last_seen": "TEXT",
            },
            "tasks": {
                "assigned_to_name": "TEXT",
                "assigned_by_name": "TEXT",
                "chat_id": "INTEGER",
                "reminder_repeat": "TEXT DEFAULT 'none'",
                "reminder_sent": "INTEGER DEFAULT 0",
                "description": "TEXT",
                "pinned": "INTEGER DEFAULT 0",
                "deleted": "INTEGER DEFAULT 0",
                "updated_at": "TEXT",
                "completed_at": "TEXT",
            },
            "meetings": {
                "project": "TEXT DEFAULT 'غیره'",
                "status": "TEXT DEFAULT 'برنامه‌ریزی‌شده'",
                "end_at": "TEXT",
                "participants": "TEXT DEFAULT '[]'",
                "chat_id": "INTEGER",
                "reminder_at": "TEXT",
                "reminder_sent": "INTEGER DEFAULT 0",
                "pinned": "INTEGER DEFAULT 0",
                "deleted": "INTEGER DEFAULT 0",
                "updated_at": "TEXT",
                "completed_at": "TEXT",
            },
        }.items():
            if _table_exists(conn, table):
                for col, definition in cols.items():
                    _add_column(conn, table, col, definition)

        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_tasks_status_deleted ON tasks(status, deleted);
            CREATE INDEX IF NOT EXISTS idx_tasks_reminder ON tasks(reminder_at, reminder_sent, deleted);
            CREATE INDEX IF NOT EXISTS idx_meetings_start ON meetings(start_at, deleted);
            CREATE INDEX IF NOT EXISTS idx_messages_chat_created ON chat_messages(chat_id, created_at);
            """
        )


# ---------- users ----------
def upsert_user(user_id: int, username: Optional[str], full_name: Optional[str], private_chat_id: Optional[int] = None, role: str = "member") -> None:
    ts = now_iso()
    with get_connection() as conn:
        old = conn.execute("SELECT user_id, role, private_chat_id FROM users WHERE user_id=?", (user_id,)).fetchone()
        if old:
            keep_role = old["role"] or role
            keep_private = private_chat_id or old["private_chat_id"]
            conn.execute(
                "UPDATE users SET username=?, full_name=?, role=?, private_chat_id=?, last_seen=? WHERE user_id=?",
                (username, full_name, keep_role, keep_private, ts, user_id),
            )
        else:
            conn.execute(
                "INSERT INTO users(user_id, username, full_name, role, private_chat_id, created_at, last_seen) VALUES(?,?,?,?,?,?,?)",
                (user_id, username, full_name, role, private_chat_id, ts, ts),
            )


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_members(limit: int = 80) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY COALESCE(last_seen, created_at) DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_admin_ids() -> List[int]:
    with get_connection() as conn:
        rows = conn.execute("SELECT user_id FROM users WHERE role='admin'").fetchall()
        return [int(r[0]) for r in rows]


# ---------- messages ----------
def log_message(chat_id: int, message_id: int, user_id: Optional[int], full_name: str, username: Optional[str], text: str, reply_to_message_id: Optional[int]) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO chat_messages(chat_id, message_id, user_id, full_name, username, text, reply_to_message_id, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (chat_id, message_id, user_id, full_name, username, text, reply_to_message_id, now_iso()),
        )


def get_recent_messages(chat_id: int, limit: int = 80, since_iso: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        if since_iso:
            rows = conn.execute(
                "SELECT * FROM chat_messages WHERE chat_id=? AND created_at>=? ORDER BY id DESC LIMIT ?",
                (chat_id, since_iso, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM chat_messages WHERE chat_id=? ORDER BY id DESC LIMIT ?", (chat_id, limit)
            ).fetchall()
        return [dict(r) for r in reversed(rows)]


# ---------- UI mapping ----------
def save_ui_message(chat_id: int, message_id: int, entity_type: str, entity_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO ui_messages(chat_id, message_id, entity_type, entity_id, created_at) VALUES(?,?,?,?,?)",
            (chat_id, message_id, entity_type, entity_id, now_iso()),
        )


def find_ui_entity(chat_id: int, message_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM ui_messages WHERE chat_id=? AND message_id=?", (chat_id, message_id)
        ).fetchone()
        return dict(row) if row else None


# ---------- history ----------
def add_task_history(task_id: int, action: str, old_value: Any = None, new_value: Any = None, actor_id: Optional[int] = None, actor_name: str = "", actor_type: str = "user") -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO task_history(task_id, action, old_value, new_value, actor_id, actor_name, actor_type, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (task_id, action, str(old_value) if old_value is not None else None, str(new_value) if new_value is not None else None, actor_id, actor_name, actor_type, now_iso()),
        )


def list_task_history(task_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM task_history WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def add_meeting_history(meeting_id: int, action: str, old_value: Any = None, new_value: Any = None, actor_id: Optional[int] = None, actor_name: str = "", actor_type: str = "user") -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO meeting_history(meeting_id, action, old_value, new_value, actor_id, actor_name, actor_type, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (meeting_id, action, str(old_value) if old_value is not None else None, str(new_value) if new_value is not None else None, actor_id, actor_name, actor_type, now_iso()),
        )


def list_meeting_history(meeting_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM meeting_history WHERE meeting_id=? ORDER BY id DESC LIMIT ?", (meeting_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- tasks ----------
def create_task(title: str, project: str = "غیره", priority: str = "متوسط", assigned_to: Optional[int] = None, assigned_to_name: Optional[str] = None, assigned_by: Optional[int] = None, assigned_by_name: Optional[str] = None, chat_id: Optional[int] = None, reminder_at: Optional[str] = None, description: Optional[str] = None, actor_type: str = "user") -> int:
    ts = now_iso()
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO tasks(title, project, status, priority, assigned_to, assigned_to_name, assigned_by, assigned_by_name, chat_id, reminder_at, description, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (title, project or "غیره", "باز", priority or "متوسط", assigned_to, assigned_to_name, assigned_by, assigned_by_name, chat_id, reminder_at, description, ts, ts),
        )
        task_id = int(cur.lastrowid)
    add_task_history(task_id, "ساخته شد", None, title, assigned_by, assigned_by_name or "", actor_type)
    return task_id


def get_task(task_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None


def list_tasks(include_done: bool = False, include_deleted: bool = False, limit: int = 80, project: Optional[str] = None, assigned_to: Optional[int] = None) -> List[Dict[str, Any]]:
    where = []
    params: List[Any] = []
    if not include_deleted:
        where.append("deleted=0")
    if not include_done:
        where.append("status NOT IN ('انجام شد','لغو شد')")
    if project:
        where.append("project=?")
        params.append(project)
    if assigned_to:
        where.append("assigned_to=?")
        params.append(assigned_to)
    sql = "SELECT * FROM tasks"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY pinned DESC, CASE priority WHEN 'زیاد' THEN 0 WHEN 'متوسط' THEN 1 ELSE 2 END, id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def update_task_field(task_id: int, field: str, value: Any, actor_id: Optional[int] = None, actor_name: str = "", actor_type: str = "user") -> bool:
    allowed = {"title", "project", "status", "priority", "assigned_to", "assigned_to_name", "reminder_at", "reminder_repeat", "description", "pinned", "deleted", "reminder_sent"}
    if field not in allowed:
        return False
    task = get_task(task_id)
    if not task:
        return False
    old = task.get(field)
    completed_at = now_iso() if field == "status" and value == "انجام شد" else task.get("completed_at")
    with get_connection() as conn:
        conn.execute(f"UPDATE tasks SET {field}=?, updated_at=?, completed_at=? WHERE id=?", (value, now_iso(), completed_at, task_id))
    add_task_history(task_id, f"تغییر {field}", old, value, actor_id, actor_name, actor_type)
    return True


def update_task_status(task_id: int, status: str, actor_id: Optional[int] = None, actor_name: str = "", actor_type: str = "user") -> bool:
    return update_task_field(task_id, "status", status, actor_id, actor_name, actor_type)


def set_task_reminder(task_id: int, reminder_at: Optional[str], repeat: str = "none", actor_id: Optional[int] = None, actor_name: str = "", actor_type: str = "user") -> bool:
    task = get_task(task_id)
    if not task:
        return False
    with get_connection() as conn:
        conn.execute("UPDATE tasks SET reminder_at=?, reminder_repeat=?, reminder_sent=0, updated_at=? WHERE id=?", (reminder_at, repeat, now_iso(), task_id))
    add_task_history(task_id, "تغییر یادآوری", task.get("reminder_at"), reminder_at, actor_id, actor_name, actor_type)
    return True


def mark_task_reminder_sent(task_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE tasks SET reminder_sent=1 WHERE id=?", (task_id,))


def due_tasks(now_text: str) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE deleted=0 AND status NOT IN ('انجام شد','لغو شد')
              AND reminder_at IS NOT NULL AND reminder_at!=''
              AND reminder_at<=? AND reminder_sent=0
            ORDER BY reminder_at ASC LIMIT 50
            """,
            (now_text,),
        ).fetchall()
        return [dict(r) for r in rows]


def add_task_note(task_id: int, note: str, user_id: Optional[int] = None, full_name: str = "", source: str = "manual", actor_type: str = "user") -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO task_notes(task_id, note, user_id, full_name, source, created_at) VALUES(?,?,?,?,?,?)",
            (task_id, note, user_id, full_name, source, now_iso()),
        )
        note_id = int(cur.lastrowid)
    add_task_history(task_id, "شرح اضافه شد", None, note, user_id, full_name, actor_type)
    return note_id


def list_task_notes(task_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM task_notes WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def add_task_file(task_id: int, file_id: str, file_unique_id: Optional[str], file_type: str, caption: Optional[str], user_id: Optional[int], full_name: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO task_files(task_id, file_id, file_unique_id, file_type, caption, user_id, full_name, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (task_id, file_id, file_unique_id, file_type, caption, user_id, full_name, now_iso()),
        )
        fid = int(cur.lastrowid)
    add_task_history(task_id, "فایل اضافه شد", None, file_type, user_id, full_name)
    return fid


def list_task_files(task_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM task_files WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def add_checklist_item(task_id: int, text: str, user_id: Optional[int], full_name: str) -> int:
    ts = now_iso()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO task_checklist(task_id, text, done, user_id, full_name, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (task_id, text, 0, user_id, full_name, ts, ts),
        )
        cid = int(cur.lastrowid)
    add_task_history(task_id, "چک‌لیست اضافه شد", None, text, user_id, full_name)
    return cid


def list_checklist(task_id: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM task_checklist WHERE task_id=? ORDER BY id ASC", (task_id,)).fetchall()
        return [dict(r) for r in rows]


def toggle_checklist_item(item_id: int, user_id: Optional[int] = None, full_name: str = "") -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM task_checklist WHERE id=?", (item_id,)).fetchone()
        if not row:
            return None
        new_done = 0 if row["done"] else 1
        conn.execute("UPDATE task_checklist SET done=?, updated_at=? WHERE id=?", (new_done, now_iso(), item_id))
    add_task_history(int(row["task_id"]), "چک‌لیست تغییر کرد", row["done"], new_done, user_id, full_name)
    return {**dict(row), "done": new_done}


def delete_task(task_id: int, actor_id: Optional[int] = None, actor_name: str = "") -> bool:
    ok = update_task_field(task_id, "deleted", 1, actor_id, actor_name)
    if ok:
        update_task_status(task_id, "لغو شد", actor_id, actor_name)
    return ok


def restore_task(task_id: int, actor_id: Optional[int] = None, actor_name: str = "") -> bool:
    ok = update_task_field(task_id, "deleted", 0, actor_id, actor_name)
    if ok:
        update_task_status(task_id, "باز", actor_id, actor_name)
    return ok


def count_task_notes(task_id: int) -> int:
    with get_connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM task_notes WHERE task_id=?", (task_id,)).fetchone()[0])


def count_task_files(task_id: int) -> int:
    with get_connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM task_files WHERE task_id=?", (task_id,)).fetchone()[0])


def checklist_counts(task_id: int) -> Tuple[int, int]:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) total, SUM(done) done FROM task_checklist WHERE task_id=?", (task_id,)).fetchone()
        return int(row["done"] or 0), int(row["total"] or 0)


def task_stats() -> Dict[str, Any]:
    with get_connection() as conn:
        rows = conn.execute("SELECT status, COUNT(*) c FROM tasks WHERE deleted=0 GROUP BY status").fetchall()
        by_status = {r["status"]: r["c"] for r in rows}
        rows2 = conn.execute("SELECT project, COUNT(*) c FROM tasks WHERE deleted=0 GROUP BY project").fetchall()
        by_project = {r["project"]: r["c"] for r in rows2}
        total = int(conn.execute("SELECT COUNT(*) FROM tasks WHERE deleted=0").fetchone()[0])
        urgent = int(conn.execute("SELECT COUNT(*) FROM tasks WHERE deleted=0 AND priority='زیاد' AND status NOT IN ('انجام شد','لغو شد')").fetchone()[0])
        open_count = int(conn.execute("SELECT COUNT(*) FROM tasks WHERE deleted=0 AND status NOT IN ('انجام شد','لغو شد')").fetchone()[0])
        return {"total": total, "open": open_count, "urgent": urgent, "by_status": by_status, "by_project": by_project}


def export_task_rows() -> List[Dict[str, Any]]:
    rows = list_tasks(include_done=True, include_deleted=True, limit=10000)
    for r in rows:
        r["notes_count"] = count_task_notes(int(r["id"]))
        r["files_count"] = count_task_files(int(r["id"]))
        done, total = checklist_counts(int(r["id"]))
        r["checklist"] = f"{done}/{total}"
    return rows


# ---------- meetings ----------
def create_meeting(title: str, project: str = "غیره", start_at: Optional[str] = None, location: Optional[str] = None, participants: Optional[List[Dict[str, Any]]] = None, created_by: Optional[int] = None, created_by_name: Optional[str] = None, chat_id: Optional[int] = None, reminder_at: Optional[str] = None, actor_type: str = "user") -> int:
    ts = now_iso()
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO meetings(title, project, status, start_at, location, participants, created_by, created_by_name, chat_id, reminder_at, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (title, project or "غیره", "برنامه‌ریزی‌شده", start_at, location, _json_dumps(participants or []), created_by, created_by_name, chat_id, reminder_at, ts, ts),
        )
        meeting_id = int(cur.lastrowid)
    add_meeting_history(meeting_id, "ملاقات ساخته شد", None, title, created_by, created_by_name or "", actor_type)
    return meeting_id


def get_meeting(meeting_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["participants_list"] = _json_loads(d.get("participants"), [])
        return d


def list_meetings(include_deleted: bool = False, upcoming_only: bool = False, limit: int = 80) -> List[Dict[str, Any]]:
    where = []
    params: List[Any] = []
    if not include_deleted:
        where.append("deleted=0")
    if upcoming_only:
        where.append("status='برنامه‌ریزی‌شده'")
    sql = "SELECT * FROM meetings"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY pinned DESC, start_at IS NULL, start_at ASC, id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["participants_list"] = _json_loads(d.get("participants"), [])
            out.append(d)
        return out


def update_meeting_field(meeting_id: int, field: str, value: Any, actor_id: Optional[int] = None, actor_name: str = "", actor_type: str = "user") -> bool:
    allowed = {"title", "project", "status", "start_at", "end_at", "location", "participants", "reminder_at", "reminder_sent", "pinned", "deleted"}
    if field not in allowed:
        return False
    meeting = get_meeting(meeting_id)
    if not meeting:
        return False
    old = meeting.get(field)
    completed_at = now_iso() if field == "status" and value == "برگزار شد" else meeting.get("completed_at")
    if field == "participants" and not isinstance(value, str):
        value = _json_dumps(value)
    with get_connection() as conn:
        conn.execute(f"UPDATE meetings SET {field}=?, updated_at=?, completed_at=? WHERE id=?", (value, now_iso(), completed_at, meeting_id))
    add_meeting_history(meeting_id, f"تغییر {field}", old, value, actor_id, actor_name, actor_type)
    return True


def set_meeting_reminder(meeting_id: int, reminder_at: Optional[str], actor_id: Optional[int] = None, actor_name: str = "", actor_type: str = "user") -> bool:
    meeting = get_meeting(meeting_id)
    if not meeting:
        return False
    with get_connection() as conn:
        conn.execute("UPDATE meetings SET reminder_at=?, reminder_sent=0, updated_at=? WHERE id=?", (reminder_at, now_iso(), meeting_id))
    add_meeting_history(meeting_id, "تغییر یادآوری", meeting.get("reminder_at"), reminder_at, actor_id, actor_name, actor_type)
    return True


def due_meetings(now_text: str) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM meetings
            WHERE deleted=0 AND status='برنامه‌ریزی‌شده'
              AND reminder_at IS NOT NULL AND reminder_at!=''
              AND reminder_at<=? AND reminder_sent=0
            ORDER BY reminder_at ASC LIMIT 50
            """,
            (now_text,),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_meeting_reminder_sent(meeting_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE meetings SET reminder_sent=1 WHERE id=?", (meeting_id,))


def add_meeting_minutes(meeting_id: int, minutes: str, summary: Optional[str] = None, decisions: Optional[str] = None, user_id: Optional[int] = None, full_name: str = "", source: str = "manual", actor_type: str = "user") -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO meeting_minutes(meeting_id, minutes, summary, decisions, user_id, full_name, source, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (meeting_id, minutes, summary, decisions, user_id, full_name, source, now_iso()),
        )
        mid = int(cur.lastrowid)
    add_meeting_history(meeting_id, "صورتجلسه اضافه شد", None, minutes[:200], user_id, full_name, actor_type)
    return mid


def list_meeting_minutes(meeting_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM meeting_minutes WHERE meeting_id=? ORDER BY id DESC LIMIT ?", (meeting_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def add_meeting_file(meeting_id: int, file_id: str, file_unique_id: Optional[str], file_type: str, caption: Optional[str], user_id: Optional[int], full_name: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO meeting_files(meeting_id, file_id, file_unique_id, file_type, caption, user_id, full_name, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (meeting_id, file_id, file_unique_id, file_type, caption, user_id, full_name, now_iso()),
        )
        fid = int(cur.lastrowid)
    add_meeting_history(meeting_id, "فایل ملاقات اضافه شد", None, file_type, user_id, full_name)
    return fid


def list_meeting_files(meeting_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM meeting_files WHERE meeting_id=? ORDER BY id DESC LIMIT ?", (meeting_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def meeting_stats() -> Dict[str, Any]:
    with get_connection() as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM meetings WHERE deleted=0").fetchone()[0])
        upcoming = int(conn.execute("SELECT COUNT(*) FROM meetings WHERE deleted=0 AND status='برنامه‌ریزی‌شده'").fetchone()[0])
        done = int(conn.execute("SELECT COUNT(*) FROM meetings WHERE deleted=0 AND status='برگزار شد'").fetchone()[0])
        rows = conn.execute("SELECT project, COUNT(*) c FROM meetings WHERE deleted=0 GROUP BY project").fetchall()
        return {"total": total, "upcoming": upcoming, "done": done, "by_project": {r["project"]: r["c"] for r in rows}}


def export_meeting_rows() -> List[Dict[str, Any]]:
    meetings = list_meetings(include_deleted=True, upcoming_only=False, limit=10000)
    with get_connection() as conn:
        for m in meetings:
            mid = int(m["id"])
            m["minutes_count"] = int(conn.execute("SELECT COUNT(*) FROM meeting_minutes WHERE meeting_id=?", (mid,)).fetchone()[0])
            m["files_count"] = int(conn.execute("SELECT COUNT(*) FROM meeting_files WHERE meeting_id=?", (mid,)).fetchone()[0])
    return meetings


init_db()
