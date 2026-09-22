import sqlite3
import os
import threading
from contextlib import contextmanager
import logging

class Database:
    """SQLite database access with one short-lived connection per operation."""

    _initialized_paths = set()
    _initialization_lock = threading.Lock()
    
    def __init__(self, db_path=None):
        """
        Initialize database with path from environment or default.
        
        Args:
            db_path: Optional database path. If None, uses DB_PATH from .env or 'tickets.db'
        """
        if db_path is None:
            db_path = os.getenv('DB_PATH', 'tickets.db')
        
        self.db_path = os.path.abspath(db_path)
        with self._initialization_lock:
            if self.db_path not in self._initialized_paths or not os.path.exists(self.db_path):
                self.init_db()
                self._initialized_paths.add(self.db_path)
    
    @classmethod
    def _create_connection(cls, db_path):
        """
        Create a new optimized SQLite connection.
        
        Args:
            db_path: Path to database file
            
        Returns:
            sqlite3.Connection: Configured connection object
        """
        conn = sqlite3.connect(
            db_path,
            check_same_thread=False,  # Allow multi-threading
            timeout=30.0,  # 30 second timeout for locked database
            isolation_level=None  # Autocommit mode
        )
        
        # Enable WAL mode for better concurrency
        conn.execute('PRAGMA journal_mode=WAL')
        # Optimize synchronization for performance
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA busy_timeout=30000')
        # Increase cache size to 64MB
        conn.execute('PRAGMA cache_size=-64000')
        # Enable foreign keys
        conn.execute('PRAGMA foreign_keys=ON')
        # Set row factory for dict-like access
        # conn.row_factory = sqlite3.Row
        
        return conn
    
    @staticmethod
    def _close_connection(conn):
        """Close a connection, including already-closed connections."""
        try:
            conn.close()
        except Exception:
            pass
    
    def get_connection(self):
        """
        Get a database connection.

        Ako se poziva unutar Flask zahteva, konekcija se registruje da bi je
        teardown handler sigurno zatvorio na kraju zahteva (i kod izuzetka /
        ranog return-a). Rucni conn.close() i dalje radi (dvostruko zatvaranje
        je bezopasno).

        Returns:
            sqlite3.Connection: Database connection
        """
        conn = self._create_connection(self.db_path)
        try:
            from flask import g, has_app_context
            if has_app_context():
                if not hasattr(g, '_db_connections'):
                    g._db_connections = []
                g._db_connections.append(conn)
        except Exception:
            pass
        return conn
    
    @contextmanager
    def get_connection_context(self):
        """
        Context manager for automatic connection handling.
        
        Usage:
            with db.get_connection_context() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users")
        
        Yields:
            sqlite3.Connection: Database connection
        """
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logging.error(f"Database error: {str(e)}")
            raise
        finally:
            self._close_connection(conn)
    
    def init_db(self):
        """
        Initialize database schema. Creates all tables if they don't exist.
        Safe to run multiple times - only creates missing tables.
        """
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
                description TEXT,
                created_by INTEGER NOT NULL,
                priority TEXT DEFAULT 'medium',
                status TEXT DEFAULT 'new',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                is_private INTEGER DEFAULT 0,
                due_date TEXT,
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                category_id INTEGER,
                assigned_to TEXT,  -- Changed from INTEGER to TEXT to allow 'IT' or user ID
                FOREIGN KEY (created_by) REFERENCES users(id),
                FOREIGN KEY (category_id) REFERENCES categories(id)
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
                FOREIGN KEY (ticket_id) REFERENCES tickets (id) ON DELETE CASCADE,
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
                FOREIGN KEY (ticket_id) REFERENCES tickets (id) ON DELETE CASCADE,
                FOREIGN KEY (uploaded_by) REFERENCES users (id)
            )
        """)
        
        # Activity Log table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ticket_activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                action_type TEXT NOT NULL,  -- 'reassigned', 'status_changed', 'watcher_added', 'watcher_removed', 'attachment_uploaded'
                old_value TEXT,
                new_value TEXT,
                details TEXT,  -- JSON or additional info
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ticket_id) REFERENCES tickets (id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)


        # Ticket watchers table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ticket_watchers (
                ticket_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (ticket_id, user_id),
                FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # Activity logs table
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

        # Ticket templates table
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

        # Ticket muted users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ticket_muted_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                muted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(ticket_id, user_id)
            )
        """)
        
        conn.commit()

        # Migration: Add columns if they don't exist
        self._run_migrations(cursor, conn)

        # Insert default reference data (NOT admin user)
        self._insert_default_data(cursor)

        # Normalize the legacy representation used for the shared IT queue.
        cursor.execute("UPDATE tickets SET assigned_to = 'IT' WHERE assigned_to IS NULL")

        self._create_indexes(cursor)

        conn.commit()
        self._close_connection(conn)
    
    def _run_migrations(self, cursor, conn):
        """
        Run database migrations for schema updates.
        
        Args:
            cursor: Database cursor
            conn: Database connection
        """
        migrations = [
            # Add is_private column if missing
            ("SELECT is_private FROM tickets LIMIT 1", 
             "ALTER TABLE tickets ADD COLUMN is_private INTEGER DEFAULT 0"),
            
            # Add due_date column if missing
            ("SELECT due_date FROM tickets LIMIT 1",
             "ALTER TABLE tickets ADD COLUMN due_date TEXT"),
        ]
        
        for check_query, alter_query in migrations:
            try:
                cursor.execute(check_query)
            except sqlite3.OperationalError:
                # Column doesn't exist, add it
                cursor.execute(alter_query)
                conn.commit()
    
    def _insert_default_data(self, cursor):
        """
        Insert default categories and departments if they don't exist.
        Does NOT create admin user - use ensure_initial_admin() in app.py instead.
        
        Args:
            cursor: Database cursor
        """
        # Default categories
        default_categories = [
            ('Hardware', 'Hardware related issues'),
            ('Software', 'Software and application issues'),
            ('Network', 'Network connectivity issues'),
            ('Access', 'Access rights and permissions'),
            ('Other', 'Other issues')
        ]
        
        for name, description in default_categories:
            cursor.execute(
                "INSERT OR IGNORE INTO categories (name, description) VALUES (?, ?)",
                (name, description)
            )
        
        # Default departments
        default_departments = [
            ('IT Department', 'Information Technology Department'),
            ('HR', 'Human Resources'),
            ('Finance', 'Finance Department'),
            ('Operations', 'Operations Department')
        ]
        
        for name, description in default_departments:
            cursor.execute(
                "INSERT OR IGNORE INTO departments (name, description) VALUES (?, ?)",
                (name, description)
            )

    def _create_indexes(self, cursor):
        """Create indexes used by the dashboard, search and audit views."""
        statements = (
            "CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)",
            "CREATE INDEX IF NOT EXISTS idx_tickets_assigned_to ON tickets(assigned_to)",
            "CREATE INDEX IF NOT EXISTS idx_tickets_created_by ON tickets(created_by)",
            "CREATE INDEX IF NOT EXISTS idx_tickets_updated_at ON tickets(updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_tickets_category ON tickets(category_id)",
            "CREATE INDEX IF NOT EXISTS idx_tickets_status_assigned ON tickets(status, assigned_to)",
            "CREATE INDEX IF NOT EXISTS idx_watchers_user ON ticket_watchers(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_comments_ticket ON comments(ticket_id)",
            "CREATE INDEX IF NOT EXISTS idx_attachments_ticket ON attachments(ticket_id)",
            "CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_logs(created_at DESC)",
        )
        for statement in statements:
            cursor.execute(statement)

# Initialize database when module is run directly
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db = Database()
