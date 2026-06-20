import sqlite3

conn = sqlite3.connect("tasks.db", check_same_thread=False)

conn.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    status TEXT,
    created_at TEXT,
    next_followup TEXT,
    last_update TEXT
)
""")

conn.commit()
