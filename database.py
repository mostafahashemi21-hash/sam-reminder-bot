import sqlite3

DB_NAME = "sam_pro.db"


def get_connection():
    return sqlite3.connect(DB_NAME)


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        role TEXT DEFAULT 'member'
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        assigned_to INTEGER,
        status TEXT DEFAULT 'pending',
        priority TEXT DEFAULT 'medium',
        reminder_time TEXT,
        created_at TEXT,
        completed_at TEXT
    )
    """)

    conn.commit()
    conn.close()
