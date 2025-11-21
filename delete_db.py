# Kreiraj fajl clean_database.py
import sqlite3
import hashlib

def create_clean_database():
    # Obriši postojeću bazu
    import os
    if os.path.exists('database.db'):
        os.remove('database.db')
    
    # Kreiraj novu praznu bazu
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    # Kreiraj sve tabele (kopiraj iz tvog database.py)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            email TEXT,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            department_id INTEGER,
            is_department_head INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (department_id) REFERENCES departments (id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            category_id INTEGER,
            priority TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'new',
            created_by INTEGER NOT NULL,
            assigned_to INTEGER,
            due_date DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (created_by) REFERENCES users (id),
            FOREIGN KEY (assigned_to) REFERENCES users (id),
            FOREIGN KEY (category_id) REFERENCES categories (id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            comment TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (ticket_id) REFERENCES tickets (id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            uploaded_by INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (ticket_id) REFERENCES tickets (id),
            FOREIGN KEY (uploaded_by) REFERENCES users (id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ticket_watchers (
            ticket_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (ticket_id, user_id),
            FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    # Dodaj samo osnovne kategorije i IT admine
    cursor.execute("INSERT INTO categories (name, description) VALUES ('Hardware', 'Hardware related issues')")
    cursor.execute("INSERT INTO categories (name, description) VALUES ('Software', 'Software related issues')")
    cursor.execute("INSERT INTO categories (name, description) VALUES ('Network', 'Network connectivity issues')")
    cursor.execute("INSERT INTO categories (name, description) VALUES ('Security', 'Security related issues')")
    cursor.execute("INSERT INTO categories (name, description) VALUES ('Other', 'Other issues')")
    
    # Dodaj IT admine
    admins = [
        ('it.admin1', 'change-me', 'IT Admin 1', 'admin1@example.com'),
        ('milos.radojkovic', 'change-me', 'Miloš R.', 'milos@company.com'),
        ('it.admin2', 'change-me', 'IT Admin 2', 'admin2@example.com')
    ]
    
    for username, password, full_name, email in admins:
        hashed_password = hashlib.sha256(password.encode()).hexdigest()
        cursor.execute("""
            INSERT INTO users (username, password, full_name, email, role)
            VALUES (?, ?, ?, ?, 'admin')
        """, (username, hashed_password, full_name, email))
    
    conn.commit()
    conn.close()
    print("Empty database created!")

if __name__ == '__main__':
    create_clean_database()