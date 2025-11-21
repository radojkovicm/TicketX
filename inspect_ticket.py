# Comments in English as requested.
import sqlite3

db_path = "database.db"  # prilagodi putanju ako treba
conn = sqlite3.connect(db_path)
cur = conn.cursor()

def show_user(uid):
    cur.execute("SELECT id, username, full_name, email FROM users WHERE id=?", (uid,))
    print(cur.fetchall())

# Proveri kreatora i asigniranog:
show_user(195)
show_user(47)

conn.close()