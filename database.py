import sqlite3
from datetime import datetime
import hashlib

class Database:
    def __init__(self, db_path='database.db'):
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        return sqlite3.connect(self.db_path)

    def init_db(self):
        conn = self.get_connection()
        cursor = conn.cursor()

        # Users table
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

        # Departments table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS departments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Categories table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Tickets table
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
                is_private INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (created_by) REFERENCES users (id),
                FOREIGN KEY (assigned_to) REFERENCES users (id),
                FOREIGN KEY (category_id) REFERENCES categories (id)
            )
        """)

        # Comments table
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

        # Attachments table
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
        
        # Dodaj ovo u deo gde se kreiraju tabele, npr. u funkciji za inicijalizaciju baze

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS ticket_watchers (
            ticket_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (ticket_id, user_id),
            FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """)
        
        # DActivity logs tabela
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                details TEXT,
                ip_address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)
        
        # template table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ticket_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                priority TEXT NOT NULL,
                category_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (category_id) REFERENCES categories (id)
            )
        """)

        conn.commit()
        
        # Migration: Add is_private column if it doesn't exist
        try:
            cursor.execute("SELECT is_private FROM tickets LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE tickets ADD COLUMN is_private INTEGER DEFAULT 0")
            conn.commit()

        # Insert default data
        self.insert_default_data(cursor)
        conn.commit()
        conn.close()

    def insert_default_data(self, cursor):
        # Default admin user
        admin_password = hashlib.sha256('admin123'.encode()).hexdigest()
        cursor.execute("""
            INSERT OR IGNORE INTO users (username, password, full_name, role) 
            VALUES ('admin', ?, 'System Administrator', 'admin')
        """, (admin_password,))

        # Default categories
        cursor.execute("INSERT OR IGNORE INTO categories (name, description) VALUES ('Hardware', 'Hardware related issues')")
        cursor.execute("INSERT OR IGNORE INTO categories (name, description) VALUES ('Network', 'Network connectivity issues')")

        # Default department
        cursor.execute("INSERT OR IGNORE INTO departments (name, description) VALUES ('IT Department', 'Information Technology Department')")

# Initialize database
if __name__ == "__main__":
    db = Database()
    print("Database initialized!")
