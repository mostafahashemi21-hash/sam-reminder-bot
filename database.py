import sqlite3
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

DB_NAME = "sam_pro.db"


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def _dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


def _dicts(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


def _table_columns(cur: sqlite3.Cursor, table: str) -> set:
    cur.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in cur.fetchall()}


def _add_column(cur: sqlite3.Cursor, table: str, column: str, definition: str) -> None:
    cols = _table_columns(cur, table)
    if column not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        role TEXT DEFAULT 'member',
        joined_at TEXT,
        last_seen TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        assigned_to INTEGER,
        assigned_by INTEGER,
        status TEXT DEFAULT 'pending',
        priority TEXT DEFAULT 'متوسط',
        project TEXT DEFAULT 'غیره',
        tag TEXT DEFAULT '',
        reminder_time TEXT DEFAULT 'none',
        reminder_repeat TEXT DEFAULT 'none',
        created_at TEXT,
        updated_at TEXT,
        completed_at TEXT,
        deleted INTEGER DEFAULT 0,
        pinned INTEGER DEFAULT 0
    )
    """)

    # Safe migration for older databases.
    for col, definition in {
        "description": "TEXT DEFAULT ''",
        "project": "TEXT DEFAULT 'غیره'",
        "tag": "TEXT DEFAULT ''",
        "reminder_repeat": "TEXT DEFAULT 'none'",
        "updated_at": "TEXT",
        "deleted": "INTEGER DEFAULT 0",
        "pinned": "INTEGER DEFAULT 0",
    }.items():
        _add_column(cur, "tasks", col, definition)

    cur.execute("""
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
    """)

    cur.execute("""
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
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS checklist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        item_text TEXT NOT NULL,
        is_done INTEGER DEFAULT 0,
        created_by INTEGER,
        created_at TEXT
    )
    """)

    cur.execute("""
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
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS task_messages (
        chat_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        task_id INTEGER NOT NULL,
        PRIMARY KEY (chat_id, message_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        user_id INTEGER,
        full_name TEXT,
        username TEXT,
        text TEXT,
        message_id INTEGER,
        reply_to_message_id INTEGER,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS ai_suggestions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        suggestion_type TEXT,
        task_id INTEGER,
        title TEXT,
        note TEXT,
        new_status TEXT,
        priority TEXT,
        project TEXT,
        assigned_to INTEGER,
        assigned_to_name TEXT,
        reason TEXT,
        confidence REAL DEFAULT 0,
        status TEXT DEFAULT 'pending',
        raw_json TEXT,
        created_at TEXT,
        decided_by INTEGER,
        decided_at TEXT
    )
    """)

    conn.commit()
    conn.close()


def add_user(user_id: int, username: str, full_name: str, role: str = "member", joined_at: Optional[str] = None) -> None:
    joined_at = joined_at or now_str()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT role FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if row:
        cur.execute("""
        UPDATE users
        SET username=?, full_name=?, last_seen=?
        WHERE user_id=?
        """, (username, full_name, now_str(), user_id))
    else:
        cur.execute("""
        INSERT INTO users (user_id, username, full_name, role, joined_at, last_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, username, full_name, role, joined_at, now_str()))
    conn.commit()
    conn.close()


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = _dict(cur.fetchone())
    conn.close()
    return row


def get_users() -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users ORDER BY full_name")
    rows = _dicts(cur.fetchall())
    conn.close()
    return rows


def count_admins() -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE role='admin'")
    count = int(cur.fetchone()[0])
    conn.close()
    return count


def get_admin_ids() -> List[int]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE role='admin'")
    rows = [int(r[0]) for r in cur.fetchall()]
    conn.close()
    return rows


def create_task(
    title: str,
    assigned_to: Optional[int],
    assigned_by: Optional[int],
    priority: str = "متوسط",
    reminder_time: str = "none",
    created_at: Optional[str] = None,
    description: str = "",
    project: str = "غیره",
    tag: str = "",
    reminder_repeat: str = "none",
) -> int:
    created_at = created_at or now_str()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO tasks (
        title, description, assigned_to, assigned_by, status, priority, project, tag,
        reminder_time, reminder_repeat, created_at, updated_at, deleted, pinned
    )
    VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, 0, 0)
    """, (title, description, assigned_to, assigned_by, priority, project, tag,
          reminder_time or "none", reminder_repeat or "none", created_at, created_at))
    task_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return task_id


def get_task(task_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
    row = _dict(cur.fetchone())
    conn.close()
    return row


def get_tasks(include_done: bool = True, include_deleted: bool = False, limit: int = 100, project: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    where = []
    params: List[Any] = []
    if not include_done:
        where.append("status NOT IN ('done', 'cancelled')")
    if not include_deleted:
        where.append("COALESCE(deleted,0)=0")
    if project:
        where.append("project=?")
        params.append(project)
    sql = "SELECT * FROM tasks"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(pinned,0) DESC, id DESC LIMIT ?"
    params.append(limit)
    cur.execute(sql, params)
    rows = _dicts(cur.fetchall())
    conn.close()
    return rows


def get_open_tasks(limit: int = 100) -> List[Dict[str, Any]]:
    return get_tasks(include_done=False, include_deleted=False, limit=limit)


def complete_task(task_id: int) -> None:
    update_task_field(task_id, "status", "done")
    update_task_field(task_id, "completed_at", now_str())


_TASK_FIELDS = {
    "title", "description", "assigned_to", "assigned_by", "status", "priority", "project", "tag",
    "reminder_time", "reminder_repeat", "created_at", "updated_at", "completed_at", "deleted", "pinned"
}


def update_task_field(task_id: int, field: str, value: Any) -> None:
    if field not in _TASK_FIELDS:
        raise ValueError(f"Invalid task field: {field}")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"UPDATE tasks SET {field}=?, updated_at=? WHERE id=?", (value, now_str(), task_id))
    conn.commit()
    conn.close()


def add_task_note(task_id: int, user_id: Optional[int], full_name: str, note: str, source: str = "manual", message_id: Optional[int] = None) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO task_notes (task_id, user_id, full_name, note, source, message_id, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (task_id, user_id, full_name, note, source, message_id, now_str()))
    note_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return note_id


def get_task_notes(task_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM task_notes WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, limit))
    rows = _dicts(cur.fetchall())
    conn.close()
    return rows


def add_history(task_id: int, user_id: Optional[int], full_name: str, action: str, old_value: Any = "", new_value: Any = "") -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO task_history (task_id, user_id, full_name, action, old_value, new_value, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (task_id, user_id, full_name, action, str(old_value or ""), str(new_value or ""), now_str()))
    hid = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return hid


def get_task_history(task_id: int, limit: int = 40) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM task_history WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, limit))
    rows = _dicts(cur.fetchall())
    conn.close()
    return rows


def add_checklist_item(task_id: int, item_text: str, created_by: Optional[int]) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO checklist (task_id, item_text, is_done, created_by, created_at)
    VALUES (?, ?, 0, ?, ?)
    """, (task_id, item_text, created_by, now_str()))
    cid = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return cid


def get_checklist(task_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM checklist WHERE task_id=? ORDER BY id", (task_id,))
    rows = _dicts(cur.fetchall())
    conn.close()
    return rows


def toggle_checklist_item(item_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_done FROM checklist WHERE id=?", (item_id,))
    row = cur.fetchone()
    if row is None:
        conn.close()
        return 0
    new_val = 0 if int(row[0]) else 1
    cur.execute("UPDATE checklist SET is_done=? WHERE id=?", (new_val, item_id))
    conn.commit()
    conn.close()
    return new_val


def add_task_file(task_id: int, user_id: Optional[int], full_name: str, file_id: str, file_type: str, caption: str = "") -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO task_files (task_id, user_id, full_name, file_id, file_type, caption, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (task_id, user_id, full_name, file_id, file_type, caption, now_str()))
    fid = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return fid


def get_task_files(task_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM task_files WHERE task_id=? ORDER BY id DESC", (task_id,))
    rows = _dicts(cur.fetchall())
    conn.close()
    return rows


def link_task_message(chat_id: int, message_id: int, task_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT OR REPLACE INTO task_messages (chat_id, message_id, task_id)
    VALUES (?, ?, ?)
    """, (chat_id, message_id, task_id))
    conn.commit()
    conn.close()


def get_task_id_by_message(chat_id: int, message_id: int) -> Optional[int]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT task_id FROM task_messages WHERE chat_id=? AND message_id=?", (chat_id, message_id))
    row = cur.fetchone()
    conn.close()
    return int(row[0]) if row else None


def save_chat_message(chat_id: int, user_id: Optional[int], full_name: str, username: str, text: str, message_id: int, reply_to_message_id: Optional[int]) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO chat_messages (chat_id, user_id, full_name, username, text, message_id, reply_to_message_id, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (chat_id, user_id, full_name, username, text, message_id, reply_to_message_id, now_str()))
    mid = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return mid


def get_recent_chat_messages(chat_id: int, limit: int = 80) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM chat_messages WHERE chat_id=? ORDER BY id DESC LIMIT ?", (chat_id, limit))
    rows = list(reversed(_dicts(cur.fetchall())))
    conn.close()
    return rows


def get_chat_messages_between(chat_id: int, start: str, end: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    SELECT * FROM chat_messages
    WHERE chat_id=? AND created_at BETWEEN ? AND ?
    ORDER BY id
    """, (chat_id, start, end))
    rows = _dicts(cur.fetchall())
    conn.close()
    return rows


def save_ai_suggestion(
    chat_id: int,
    suggestion_type: str,
    task_id: Optional[int] = None,
    title: str = "",
    note: str = "",
    new_status: str = "",
    priority: str = "متوسط",
    project: str = "غیره",
    assigned_to: Optional[int] = None,
    assigned_to_name: str = "",
    reason: str = "",
    confidence: float = 0.0,
    raw_json: str = "",
) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO ai_suggestions (
        chat_id, suggestion_type, task_id, title, note, new_status, priority, project,
        assigned_to, assigned_to_name, reason, confidence, status, raw_json, created_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
    """, (chat_id, suggestion_type, task_id, title, note, new_status, priority, project,
          assigned_to, assigned_to_name, reason, confidence, raw_json, now_str()))
    sid = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return sid


def get_ai_suggestion(suggestion_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM ai_suggestions WHERE id=?", (suggestion_id,))
    row = _dict(cur.fetchone())
    conn.close()
    return row


def update_ai_suggestion_status(suggestion_id: int, status: str, decided_by: Optional[int] = None) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    UPDATE ai_suggestions
    SET status=?, decided_by=?, decided_at=?
    WHERE id=?
    """, (status, decided_by, now_str(), suggestion_id))
    conn.commit()
    conn.close()
