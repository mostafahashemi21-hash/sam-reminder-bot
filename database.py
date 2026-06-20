import sqlite3

conn = sqlite3.connect("tasks.db", check_same_thread=False)

cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS tasks (
id INTEGER PRIMARY KEY AUTOINCREMENT,
title TEXT,
owner TEXT,
priority TEXT,
status TEXT,
created_at TEXT,
last_update TEXT,
reminder_hours INTEGER
)
""")

conn.commit()
