# -*- coding: utf-8 -*-
"""
SAM PRO Team Manager - database layer
SQLite helpers for Telegram bot.
"""

import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

DB_PATH = os.getenv("DB_PATH", "sam_pro_team_manager.db")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _exec(sql: str, params: tuple = ()) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return int(last_id or 0)


def _fetchone(sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return dict(row) if row else None


def _fetchall(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _column_exists(table: str, column: str) -> bool:
    conn = get_connection()
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    conn.close()
    return any(r[1] == column for r in rows)


def _add_column_if_missing(table: str, column: str, definition: str) -> None:
    if not _column_exists(table, column):
        conn = get_connection()
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()
        conn.close()


def init_db() -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            role TEXT DEFAULT 'member',
            created_at TEXT,
            last_seen TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            project TEXT DEFAULT 'غیره',
            assigned_to INTEGER,
            assigned_by INTEGER,
            priority TEXT DEFAULT 'متوسط',
            status TEXT DEFAULT 'open',
            reminder_time TEXT DEFAULT '',
            reminder_repeat TEXT DEFAULT 'none',
            pinned INTEGER DEFAULT 0,
            deleted INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT,
            completed_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS task_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            user_id INTEGER,
            full_name TEXT,
            note TEXT NOT NULL,
            source TEXT DEFAULT 'manual',
            message_id INTEGER,
            created_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS task_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            user_id INTEGER,
            full_name TEXT,
            action TEXT,
            old_value TEXT,
            new_value TEXT,
            created_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS task_checklist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            item_text TEXT NOT NULL,
            is_done INTEGER DEFAULT 0,
            created_by INTEGER,
            created_at TEXT,
            completed_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS task_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            user_id INTEGER,
            full_name TEXT,
            file_id TEXT NOT NULL,
            file_type TEXT,
            caption TEXT,
            created_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS task_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            created_at TEXT,
            UNIQUE(chat_id, message_id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER,
            full_name TEXT,
            username TEXT,
            text TEXT,
            message_id INTEGER,
            reply_to_message_id INTEGER,
            created_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            suggestion_type TEXT,
            task_id INTEGER,
            title TEXT,
            note TEXT,
            new_status TEXT,
            project TEXT,
            priority TEXT,
            assigned_to INTEGER,
            assigned_to_name TEXT,
            reason TEXT,
            confidence REAL DEFAULT 0,
            raw_json TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            decided_by INTEGER,
            decided_at TEXT
        )
        """
    )

    conn.commit()
    conn.close()

    # Lightweight migrations for older deployed databases.
    for table, cols in {
        "tasks": {
            "description": "TEXT DEFAULT ''",
            "project": "TEXT DEFAULT 'غیره'",
            "reminder_repeat": "TEXT DEFAULT 'none'",
            "pinned": "INTEGER DEFAULT 0",
            "deleted": "INTEGER DEFAULT 0",
            "updated_at": "TEXT",
            "completed_at": "TEXT",
        },
        "users": {"last_seen": "TEXT"},
        "ai_suggestions": {
            "project": "TEXT",
            "decided_by": "INTEGER",
            "decided_at": "TEXT",
        },
    }.items():
        for col, definition in cols.items():
            _add_column_if_missing(table, col, definition)


# ------------------------- users -------------------------

def add_user(user_id: int, username: Optional[str], full_name: Optional[str], role: Optional[str] = None, created_at: Optional[str] = None) -> None:
    existing = get_user(user_id)
    ts = created_at or now_str()
    if existing:
        _exec(
            "UPDATE users SET username=?, full_name=?, last_seen=? WHERE user_id=?",
            (username or '', full_name or '', ts, int(user_id)),
        )
        return
    if not role:
        role = 'admin' if count_admins() == 0 else 'member'
    _exec(
        "INSERT OR REPLACE INTO users(user_id, username, full_name, role, created_at, last_seen) VALUES(?,?,?,?,?,?)",
        (int(user_id), username or '', full_name or '', role, ts, ts),
    )


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    return _fetchone("SELECT * FROM users WHERE user_id=?", (int(user_id),))


def get_users() -> List[Dict[str, Any]]:
    return _fetchall("SELECT * FROM users ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, full_name")


def count_admins() -> int:
    row = _fetchone("SELECT COUNT(*) AS c FROM users WHERE role='admin'")
    return int(row['c']) if row else 0


def get_admin_ids() -> List[int]:
    rows = _fetchall("SELECT user_id FROM users WHERE role='admin'")
    return [int(r['user_id']) for r in rows]


def set_user_role(user_id: int, role: str) -> None:
    _exec("UPDATE users SET role=? WHERE user_id=?", (role, int(user_id)))


# ------------------------- tasks -------------------------

def create_task(
    title: str,
    assigned_to: Optional[int] = None,
    assigned_by: Optional[int] = None,
    priority: str = 'متوسط',
    reminder_time: str = '',
    created_at: Optional[str] = None,
    description: str = '',
    project: str = 'غیره',
    status: str = 'open',
    reminder_repeat: str = 'none',
) -> int:
    ts = created_at or now_str()
    return _exec(
        """
        INSERT INTO tasks(title, description, project, assigned_to, assigned_by, priority, status,
                          reminder_time, reminder_repeat, pinned, deleted, created_at, updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            title.strip(), description or '', project or 'غیره', assigned_to, assigned_by,
            priority or 'متوسط', status or 'open', reminder_time or '', reminder_repeat or 'none',
            0, 0, ts, ts,
        ),
    )


def get_task(task_id: int) -> Optional[Dict[str, Any]]:
    if not task_id:
        return None
    return _fetchone("SELECT * FROM tasks WHERE id=?", (int(task_id),))


def get_tasks(include_done: bool = True, include_deleted: bool = False, limit: int = 100) -> List[Dict[str, Any]]:
    where = []
    params: List[Any] = []
    if not include_deleted:
        where.append("deleted=0")
    if not include_done:
        where.append("status NOT IN ('done','cancelled')")
    sql = "SELECT * FROM tasks"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY pinned DESC, CASE status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 WHEN 'waiting' THEN 2 ELSE 9 END, id DESC LIMIT ?"
    params.append(int(limit))
    return _fetchall(sql, tuple(params))


def get_open_tasks(limit: int = 100) -> List[Dict[str, Any]]:
    return _fetchall(
        """
        SELECT * FROM tasks
        WHERE deleted=0 AND status NOT IN ('done','cancelled')
        ORDER BY pinned DESC, id DESC
        LIMIT ?
        """,
        (int(limit),),
    )


def complete_task(task_id: int) -> None:
    update_task_field(task_id, 'status', 'done')
    update_task_field(task_id, 'completed_at', now_str())


def update_task_field(task_id: int, field: str, value: Any) -> None:
    allowed = {
        'title', 'description', 'project', 'assigned_to', 'assigned_by', 'priority', 'status',
        'reminder_time', 'reminder_repeat', 'pinned', 'deleted', 'completed_at', 'updated_at'
    }
    if field not in allowed:
        raise ValueError(f"invalid task field: {field}")
    _exec(f"UPDATE tasks SET {field}=?, updated_at=? WHERE id=?", (value, now_str(), int(task_id)))


def search_tasks(query: str, limit: int = 30) -> List[Dict[str, Any]]:
    q = f"%{query}%"
    return _fetchall(
        "SELECT * FROM tasks WHERE deleted=0 AND (title LIKE ? OR description LIKE ? OR project LIKE ?) ORDER BY id DESC LIMIT ?",
        (q, q, q, int(limit)),
    )


# ------------------------- notes/history/checklist/files -------------------------

def add_task_note(task_id: int, user_id: Optional[int], full_name: Optional[str], note: str, source: str = 'manual', message_id: Optional[int] = None) -> int:
    return _exec(
        "INSERT INTO task_notes(task_id, user_id, full_name, note, source, message_id, created_at) VALUES(?,?,?,?,?,?,?)",
        (int(task_id), user_id, full_name or '', note or '', source or 'manual', message_id, now_str()),
    )


def get_task_notes(task_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    return _fetchall("SELECT * FROM task_notes WHERE task_id=? ORDER BY id DESC LIMIT ?", (int(task_id), int(limit)))


def add_history(task_id: int, user_id: Optional[int], full_name: Optional[str], action: str, old_value: Any = '', new_value: Any = '') -> int:
    return _exec(
        "INSERT INTO task_history(task_id, user_id, full_name, action, old_value, new_value, created_at) VALUES(?,?,?,?,?,?,?)",
        (int(task_id), user_id, full_name or '', action or '', str(old_value or ''), str(new_value or ''), now_str()),
    )


def get_task_history(task_id: int, limit: int = 40) -> List[Dict[str, Any]]:
    return _fetchall("SELECT * FROM task_history WHERE task_id=? ORDER BY id DESC LIMIT ?", (int(task_id), int(limit)))


def add_checklist_item(task_id: int, item_text: str, created_by: Optional[int] = None) -> int:
    return _exec(
        "INSERT INTO task_checklist(task_id, item_text, is_done, created_by, created_at) VALUES(?,?,?,?,?)",
        (int(task_id), item_text.strip(), 0, created_by, now_str()),
    )


def get_checklist(task_id: int) -> List[Dict[str, Any]]:
    return _fetchall("SELECT * FROM task_checklist WHERE task_id=? ORDER BY id", (int(task_id),))


def toggle_checklist_item(item_id: int) -> int:
    row = _fetchone("SELECT * FROM task_checklist WHERE id=?", (int(item_id),))
    if not row:
        return 0
    new_val = 0 if int(row['is_done'] or 0) else 1
    completed_at = now_str() if new_val else None
    _exec("UPDATE task_checklist SET is_done=?, completed_at=? WHERE id=?", (new_val, completed_at, int(item_id)))
    return new_val


def add_task_file(task_id: int, user_id: Optional[int], full_name: Optional[str], file_id: str, file_type: str, caption: str = '') -> int:
    return _exec(
        "INSERT INTO task_files(task_id, user_id, full_name, file_id, file_type, caption, created_at) VALUES(?,?,?,?,?,?,?)",
        (int(task_id), user_id, full_name or '', file_id, file_type or '', caption or '', now_str()),
    )


def get_task_files(task_id: int) -> List[Dict[str, Any]]:
    return _fetchall("SELECT * FROM task_files WHERE task_id=? ORDER BY id DESC", (int(task_id),))


# ------------------------- message links / chat logs -------------------------

def link_task_message(chat_id: int, message_id: int, task_id: int) -> None:
    _exec(
        "INSERT OR REPLACE INTO task_messages(chat_id, message_id, task_id, created_at) VALUES(?,?,?,?)",
        (int(chat_id), int(message_id), int(task_id), now_str()),
    )


def get_task_id_by_message(chat_id: int, message_id: int) -> Optional[int]:
    row = _fetchone("SELECT task_id FROM task_messages WHERE chat_id=? AND message_id=?", (int(chat_id), int(message_id)))
    return int(row['task_id']) if row else None


def save_chat_message(chat_id: int, user_id: Optional[int], full_name: Optional[str], username: Optional[str], text: str, message_id: Optional[int], reply_to_message_id: Optional[int] = None) -> int:
    return _exec(
        """
        INSERT INTO chat_messages(chat_id, user_id, full_name, username, text, message_id, reply_to_message_id, created_at)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (int(chat_id), user_id, full_name or '', username or '', text or '', message_id, reply_to_message_id, now_str()),
    )


def get_recent_chat_messages(chat_id: int, limit: int = 80) -> List[Dict[str, Any]]:
    rows = _fetchall(
        "SELECT * FROM chat_messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",
        (int(chat_id), int(limit)),
    )
    return list(reversed(rows))


def get_chat_messages_between(chat_id: int, start: str, end: str) -> List[Dict[str, Any]]:
    return _fetchall(
        "SELECT * FROM chat_messages WHERE chat_id=? AND created_at BETWEEN ? AND ? ORDER BY id",
        (int(chat_id), start, end),
    )


# ------------------------- AI suggestions -------------------------

def save_ai_suggestion(
    chat_id: int,
    suggestion_type: str,
    task_id: Optional[int] = None,
    title: str = '',
    note: str = '',
    new_status: str = '',
    priority: str = '',
    assigned_to: Optional[int] = None,
    assigned_to_name: str = '',
    reason: str = '',
    confidence: float = 0.0,
    raw_json: str = '',
    project: str = '',
) -> int:
    return _exec(
        """
        INSERT INTO ai_suggestions(chat_id, suggestion_type, task_id, title, note, new_status, project,
                                   priority, assigned_to, assigned_to_name, reason, confidence, raw_json, status, created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            chat_id, suggestion_type, task_id, title or '', note or '', new_status or '', project or '',
            priority or '', assigned_to, assigned_to_name or '', reason or '', float(confidence or 0),
            raw_json or '', 'pending', now_str(),
        ),
    )


def get_ai_suggestion(suggestion_id: int) -> Optional[Dict[str, Any]]:
    return _fetchone("SELECT * FROM ai_suggestions WHERE id=?", (int(suggestion_id),))


def update_ai_suggestion_status(suggestion_id: int, status: str, decided_by: Optional[int] = None) -> None:
    _exec(
        "UPDATE ai_suggestions SET status=?, decided_by=?, decided_at=? WHERE id=?",
        (status, decided_by, now_str(), int(suggestion_id)),
    )
