import sqlite3

DB_NAME = "sam_pro.db"

def get_connection():
return sqlite3.connect(DB_NAME)

def init_db():
conn = get_connection()
cur = conn.cursor()

```
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    role TEXT DEFAULT 'member',
    joined_at TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    assigned_to INTEGER,
    assigned_by INTEGER,
    status TEXT DEFAULT 'pending',
    priority TEXT DEFAULT 'medium',
    reminder_time TEXT,
    created_at TEXT,
    completed_at TEXT
)
""")

conn.commit()
conn.close()
```

def add_user(user_id, username, full_name, role="member", joined_at=""):
conn = get_connection()
cur = conn.cursor()

```
cur.execute("""
INSERT OR IGNORE INTO users
(user_id, username, full_name, role, joined_at)
VALUES (?, ?, ?, ?, ?)
""", (user_id, username, full_name, role, joined_at))

conn.commit()
conn.close()
```

def get_user(user_id):
conn = get_connection()
cur = conn.cursor()

```
cur.execute(
    "SELECT * FROM users WHERE user_id=?",
    (user_id,)
)

row = cur.fetchone()
conn.close()

return row
```

def get_users():
conn = get_connection()
cur = conn.cursor()

```
cur.execute("""
SELECT user_id, username, full_name, role
FROM users
ORDER BY full_name
""")

rows = cur.fetchall()
conn.close()

return rows
```

def count_admins():
conn = get_connection()
cur = conn.cursor()

```
cur.execute("""
SELECT COUNT(*)
FROM users
WHERE role='admin'
""")

count = cur.fetchone()[0]

conn.close()

return count
```
