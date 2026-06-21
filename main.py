
"""
mermaid
timeline
    title DB lifecycle
    section startup
      init_db : create schema
    section runtime
      upsert_user : on every interaction
      create_task : insert task + assignees + reminder
      add_task_comment : quick comments
      list_due_reminders : scheduler polling
      mark_reminder_status : reminder bookkeeping
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Iterable, Any

DB_PATH = os.getenv("DB_PATH", "sam_pro.db")

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"

STATUS_PENDING = "pending"
STATUS_DONE = "done"

REMINDER_KIND_TASK_DUE = "task_due"

DB_DT_FORMAT = "%Y-%m-%d %H:%M:%S"
UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_db_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime(DB_DT_FORMAT)


def from_db_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.strptime(value, DB_DT_FORMAT).replace(tzinfo=UTC)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def log_event(level: str, event: str, details: Optional[Any] = None) -> None:
    details_json = None
    if details is not None:
        try:
            details_json = json.dumps(details, ensure_ascii=False)
        except Exception:
            details_json = json.dumps({"raw": str(details)}, ensure_ascii=False)

    conn = db()
    conn.execute(
        """
        INSERT INTO bot_logs(level, event, details_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (level, event, details_json, to_db_datetime(utc_now())),
    )
    conn.commit()
    conn.close()


def init_db() -> None:
    conn = db()
    cur = conn.cursor()

    # SQL schema summary:
    # users -> operators and members
    # teams -> group/channel/private containers
    # team_members -> seen users in chats
    # tasks -> main task objects
    # task_assignees -> multiple assignees
    # task_comments -> button-based comments + system notes
    # reminders -> APScheduler due reminders bookkeeping
    # bot_logs -> structured app logs

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            language_code TEXT,
            is_bot INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS teams (
            chat_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            chat_type TEXT NOT NULL,
            username TEXT,
            created_by INTEGER,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (created_by) REFERENCES users(user_id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS team_members (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            member_role TEXT NOT NULL DEFAULT 'member',
            joined_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, user_id),
            FOREIGN KEY (chat_id) REFERENCES teams(chat_id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            priority TEXT NOT NULL DEFAULT 'medium',
            created_by INTEGER NOT NULL,
            source_chat_id INTEGER NOT NULL,
            reminder_at TEXT,
            timezone_name TEXT NOT NULL DEFAULT 'UTC',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT,
            FOREIGN KEY (created_by) REFERENCES users(user_id) ON DELETE RESTRICT,
            FOREIGN KEY (source_chat_id) REFERENCES teams(chat_id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS task_assignees (
            task_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            assigned_at TEXT NOT NULL,
            PRIMARY KEY (task_id, user_id),
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS task_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            author_user_id INTEGER,
            comment_type TEXT NOT NULL DEFAULT 'quick',
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
            FOREIGN KEY (author_user_id) REFERENCES users(user_id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            remind_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            payload_json TEXT,
            sent_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS bot_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT NOT NULL,
            event TEXT NOT NULL,
            details_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
        CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen_at);

        CREATE INDEX IF NOT EXISTS idx_teams_active ON teams(active);
        CREATE INDEX IF NOT EXISTS idx_team_members_chat ON team_members(chat_id);

        CREATE INDEX IF NOT EXISTS idx_tasks_source_chat ON tasks(source_chat_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_deleted ON tasks(deleted_at);
        CREATE INDEX IF NOT EXISTS idx_tasks_reminder_at ON tasks(reminder_at);

        CREATE INDEX IF NOT EXISTS idx_task_assignees_user ON task_assignees(user_id);
        CREATE INDEX IF NOT EXISTS idx_task_comments_task ON task_comments(task_id);

        CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, remind_at, kind);
        """
    )

    conn.commit()
    conn.close()


def upsert_user(
    user_id: int,
    username: Optional[str],
    full_name: str,
    role: str,
    language_code: Optional[str] = None,
    is_bot: bool = False,
):
    now = to_db_datetime(utc_now())
    conn = db()
    conn.execute(
        """
        INSERT INTO users(user_id, username, full_name, role, language_code, is_bot, created_at, updated_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            full_name = excluded.full_name,
            language_code = excluded.language_code,
            is_bot = excluded.is_bot,
            updated_at = excluded.updated_at,
            last_seen_at = excluded.last_seen_at
        """,
        (user_id, username, full_name, role, language_code, 1 if is_bot else 0, now, now, now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row


def get_user(user_id: int):
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row


def list_users(active_only: bool = True) -> list:
    conn = db()
    if active_only:
        rows = conn.execute(
            """
            SELECT * FROM users
            WHERE is_bot = 0
            ORDER BY CASE WHEN role = 'admin' THEN 0 ELSE 1 END, full_name COLLATE NOCASE ASC
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM users
            ORDER BY CASE WHEN role = 'admin' THEN 0 ELSE 1 END, full_name COLLATE NOCASE ASC
            """
        ).fetchall()
    conn.close()
    return rows


def count_admins() -> int:
    conn = db()
    value = conn.execute("SELECT COUNT(*) FROM users WHERE role = ?", (ROLE_ADMIN,)).fetchone()[0]
    conn.close()
    return int(value)


def set_user_role(user_id: int, role: str) -> None:
    conn = db()
    conn.execute(
        """
        UPDATE users
        SET role = ?, updated_at = ?
        WHERE user_id = ?
        """,
        (role, to_db_datetime(utc_now()), user_id),
    )
    conn.commit()
    conn.close()


def upsert_team(
    chat_id: int,
    title: str,
    chat_type: str,
    username: Optional[str] = None,
    created_by: Optional[int] = None,
):
    now = to_db_datetime(utc_now())
    conn = db()
    conn.execute(
        """
        INSERT INTO teams(chat_id, title, chat_type, username, created_by, active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET
            title = excluded.title,
            chat_type = excluded.chat_type,
            username = excluded.username,
            active = 1,
            updated_at = excluded.updated_at
        """,
        (chat_id, title, chat_type, username, created_by, now, now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM teams WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return row


def get_team(chat_id: int):
    conn = db()
    row = conn.execute("SELECT * FROM teams WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return row


def list_teams(active_only: bool = True) -> list:
    conn = db()
    sql = "SELECT * FROM teams"
    params: tuple = ()
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY title COLLATE NOCASE ASC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def add_team_member(chat_id: int, user_id: int, member_role: str = "member") -> None:
    now = to_db_datetime(utc_now())
    conn = db()
    conn.execute(
        """
        INSERT INTO team_members(chat_id, user_id, member_role, joined_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET
            member_role = excluded.member_role,
            updated_at = excluded.updated_at
        """,
        (chat_id, user_id, member_role, now, now),
    )
    conn.commit()
    conn.close()


def list_team_members(chat_id: int) -> list:
    conn = db()
    rows = conn.execute(
        """
        SELECT
            u.*,
            tm.member_role
        FROM team_members tm
        JOIN users u ON u.user_id = tm.user_id
        WHERE tm.chat_id = ?
        ORDER BY CASE WHEN u.role = 'admin' THEN 0 ELSE 1 END, u.full_name COLLATE NOCASE ASC
        """,
        (chat_id,),
    ).fetchall()
    conn.close()
    return rows


def _replace_due_reminder(cur: sqlite3.Cursor, task_id: int, remind_at: datetime) -> None:
    now = to_db_datetime(utc_now())
    cur.execute(
        """
        UPDATE reminders
        SET status = 'cancelled', updated_at = ?
        WHERE task_id = ? AND kind = ? AND status = 'pending'
        """,
        (now, task_id, REMINDER_KIND_TASK_DUE),
    )
    cur.execute(
        """
        INSERT INTO reminders(task_id, kind, remind_at, status, payload_json, sent_at, created_at, updated_at)
        VALUES (?, ?, ?, 'pending', NULL, NULL, ?, ?)
        """,
        (task_id, REMINDER_KIND_TASK_DUE, to_db_datetime(remind_at), now, now),
    )


def create_task(
    title: str,
    created_by: int,
    source_chat_id: int,
    priority: str = "medium",
    reminder_at: Optional[datetime] = None,
    timezone_name: str = "UTC",
    assignee_ids: Optional[Iterable[int]] = None,
) -> int:
    assignee_ids = list(dict.fromkeys(assignee_ids or []))
    now = to_db_datetime(utc_now())

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO tasks(
            title, status, priority, created_by, source_chat_id,
            reminder_at, timezone_name, created_at, updated_at, deleted_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            title.strip(),
            STATUS_PENDING,
            priority,
            created_by,
            source_chat_id,
            to_db_datetime(reminder_at),
            timezone_name,
            now,
            now,
        ),
    )

    task_id = int(cur.lastrowid)

    for user_id in assignee_ids:
        cur.execute(
            """
            INSERT OR IGNORE INTO task_assignees(task_id, user_id, assigned_at)
            VALUES (?, ?, ?)
            """,
            (task_id, user_id, now),
        )

    if reminder_at is not None:
        _replace_due_reminder(cur, task_id, reminder_at)

    conn.commit()
    conn.close()
    return task_id


def get_task(task_id: int):
    conn = db()
    row = conn.execute(
        """
        SELECT
            t.*,
            u.full_name AS creator_name,
            tm.title AS source_chat_title,
            (
                SELECT GROUP_CONCAT(u2.full_name, '، ')
                FROM task_assignees ta
                JOIN users u2 ON u2.user_id = ta.user_id
                WHERE ta.task_id = t.id
            ) AS assignee_names,
            (
                SELECT COUNT(*)
                FROM task_comments tc
                WHERE tc.task_id = t.id
            ) AS comment_count
        FROM tasks t
        LEFT JOIN users u ON u.user_id = t.created_by
        LEFT JOIN teams tm ON tm.chat_id = t.source_chat_id
        WHERE t.id = ?
        """,
        (task_id,),
    ).fetchone()
    conn.close()
    return row


def list_tasks(
    source_chat_id: Optional[int] = None,
    assignee_user_id: Optional[int] = None,
    include_done: bool = True,
    include_deleted: bool = False,
    limit: int = 20,
) -> list:
    where = []
    params: list = []

    if not include_deleted:
        where.append("t.deleted_at IS NULL")
    if source_chat_id is not None:
        where.append("t.source_chat_id = ?")
        params.append(source_chat_id)
    if assignee_user_id is not None:
        where.append(
            "EXISTS (SELECT 1 FROM task_assignees ta WHERE ta.task_id = t.id AND ta.user_id = ?)"
        )
        params.append(assignee_user_id)
    if not include_done:
        where.append("t.status != ?")
        params.append(STATUS_DONE)

    where_sql = "WHERE " + " AND ".join(where) if where else ""
    params.append(limit)

    conn = db()
    rows = conn.execute(
        f"""
        SELECT
            t.*,
            u.full_name AS creator_name,
            tm.title AS source_chat_title,
            (
                SELECT GROUP_CONCAT(u2.full_name, '، ')
                FROM task_assignees ta
                JOIN users u2 ON u2.user_id = ta.user_id
                WHERE ta.task_id = t.id
            ) AS assignee_names,
            (
                SELECT COUNT(*)
                FROM task_comments tc
                WHERE tc.task_id = t.id
            ) AS comment_count
        FROM tasks t
        LEFT JOIN users u ON u.user_id = t.created_by
        LEFT JOIN teams tm ON tm.chat_id = t.source_chat_id
        {where_sql}
        ORDER BY
            CASE WHEN t.deleted_at IS NULL THEN 0 ELSE 1 END,
            CASE WHEN t.status = 'pending' THEN 0 ELSE 1 END,
            t.id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    conn.close()
    return rows


def get_task_assignees(task_id: int) -> list:
    conn = db()
    rows = conn.execute(
        """
        SELECT
            u.*,
            ta.assigned_at
        FROM task_assignees ta
        JOIN users u ON u.user_id = ta.user_id
        WHERE ta.task_id = ?
        ORDER BY u.full_name COLLATE NOCASE ASC
        """,
        (task_id,),
    ).fetchall()
    conn.close()
    return rows


def add_task_comment(task_id: int, author_user_id: Optional[int], text: str, comment_type: str = "quick") -> int:
    now = to_db_datetime(utc_now())
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO task_comments(task_id, author_user_id, comment_type, text, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (task_id, author_user_id, comment_type, text.strip(), now),
    )
    comment_id = int(cur.lastrowid)
    cur.execute(
        """
        UPDATE tasks
        SET updated_at = ?
        WHERE id = ?
        """,
        (now, task_id),
    )
    conn.commit()
    conn.close()
    return comment_id


def list_task_comments(task_id: int, limit: int = 10) -> list:
    conn = db()
    rows = conn.execute(
        """
        SELECT
            tc.*,
            u.full_name AS author_name
        FROM task_comments tc
        LEFT JOIN users u ON u.user_id = tc.author_user_id
        WHERE tc.task_id = ?
        ORDER BY tc.id DESC
        LIMIT ?
        """,
        (task_id, limit),
    ).fetchall()
    conn.close()
    return rows


def _cancel_pending_due_reminders(cur: sqlite3.Cursor, task_id: int) -> None:
    cur.execute(
        """
        UPDATE reminders
        SET status = 'cancelled', updated_at = ?
        WHERE task_id = ? AND kind = ? AND status = 'pending'
        """,
        (to_db_datetime(utc_now()), task_id, REMINDER_KIND_TASK_DUE),
    )


def set_task_status(task_id: int, status: str) -> None:
    now = to_db_datetime(utc_now())
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE tasks
        SET status = ?, updated_at = ?
        WHERE id = ? AND deleted_at IS NULL
        """,
        (status, now, task_id),
    )
    if status == STATUS_DONE:
        _cancel_pending_due_reminders(cur, task_id)
    conn.commit()
    conn.close()


def delete_task(task_id: int, soft: bool = True) -> None:
    now = to_db_datetime(utc_now())
    conn = db()
    cur = conn.cursor()

    if soft:
        cur.execute(
            """
            UPDATE tasks
            SET deleted_at = ?, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (now, now, task_id),
        )
    else:
        cur.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    _cancel_pending_due_reminders(cur, task_id)
    conn.commit()
    conn.close()


def list_due_reminders(limit: int = 50) -> list:
    now = to_db_datetime(utc_now())
    conn = db()
    rows = conn.execute(
        """
        SELECT
            r.id AS reminder_id,
            r.task_id AS task_id,
            r.remind_at AS remind_at,
            r.status AS reminder_status,
            t.title AS task_title,
            t.status AS task_status
        FROM reminders r
        JOIN tasks t ON t.id = r.task_id
        WHERE r.kind = ?
          AND r.status = 'pending'
          AND r.remind_at <= ?
          AND t.deleted_at IS NULL
          AND t.status != ?
        ORDER BY r.remind_at ASC, r.id ASC
        LIMIT ?
        """,
        (REMINDER_KIND_TASK_DUE, now, STATUS_DONE, limit),
    ).fetchall()
    conn.close()
    return rows


def mark_reminder_status(reminder_id: int, status: str, payload: Optional[Any] = None) -> None:
    now = to_db_datetime(utc_now())
    payload_json = json.dumps(payload, ensure_ascii=False
