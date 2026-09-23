"""Synthetic, disposable data used only by the public portfolio demo."""

import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from database import Database
from security import hash_password


DEMO_USERNAME = "demo_admin"
DEMO_PASSWORD = "TicketXDemo!2026"
DEMO_EMAIL_DOMAIN = "milosradojkovic.dev"
_DEMO_DATABASE_LOCK = threading.Lock()


def ensure_demo_database(db_path, username=DEMO_USERNAME, password=DEMO_PASSWORD):
    """Create or restore the deterministic disposable demo database."""
    with _DEMO_DATABASE_LOCK:
        return _ensure_demo_database(db_path, username, password)


def _ensure_demo_database(db_path, username, password):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            with closing(sqlite3.connect(path)) as connection:
                user_count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                ticket_count = connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
                emails = [row[0] for row in connection.execute("SELECT email FROM users")]
            if (
                user_count >= 5
                and ticket_count >= 6
                and emails
                and all(email.endswith(f"@{DEMO_EMAIL_DOMAIN}") for email in emails)
            ):
                return path
        except sqlite3.Error:
            path.unlink(missing_ok=True)
        else:
            _reset_demo_content(path)

    Database(str(path))
    connection = sqlite3.connect(path)
    cursor = connection.cursor()

    departments = {
        name: department_id
        for department_id, name in cursor.execute("SELECT id, name FROM departments")
    }
    categories = {
        name: category_id
        for category_id, name in cursor.execute("SELECT id, name FROM categories")
    }

    password_hash = hash_password(password)
    cursor.executemany(
        """
        INSERT INTO users
            (id, username, password, email, full_name, role, department_id, is_department_head)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, username, password_hash, f"alex.carter@{DEMO_EMAIL_DOMAIN}", "Alex Carter", "admin", departments["IT Department"], 0),
            (2, "morgan.reed", password_hash, f"morgan.reed@{DEMO_EMAIL_DOMAIN}", "Morgan Reed", "user", departments["Operations"], 1),
            (3, "jamie.chen", password_hash, f"jamie.chen@{DEMO_EMAIL_DOMAIN}", "Jamie Chen", "user", departments["Finance"], 0),
            (4, "taylor.brooks", password_hash, f"taylor.brooks@{DEMO_EMAIL_DOMAIN}", "Taylor Brooks", "user", departments["HR"], 1),
            (5, "sam.rivera", password_hash, f"sam.rivera@{DEMO_EMAIL_DOMAIN}", "Sam Rivera", "admin", departments["IT Department"], 0),
        ],
    )

    cursor.executemany(
        """
        INSERT INTO tickets
            (id, title, description, created_by, priority, status, created_at,
             is_private, due_date, updated_at, category_id, assigned_to)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (101, "VPN access fails after password reset", "The VPN client rejects valid credentials after yesterday's password reset. Web applications work normally. Please refresh the remote-access profile.", 2, "high", "in_progress", "2026-09-22 09:15:00", 0, "2026-09-24", "2026-09-22 11:40:00", categories["Access"], "1"),
            (102, "Prepare laptop and accounts for new starter", "Prepare a standard laptop, email account and project-drive access for a new team member starting next Monday.", 4, "medium", "new", "2026-09-22 10:05:00", 0, "2026-09-28", "2026-09-22 10:05:00", categories["Hardware"], "IT"),
            (103, "Quarterly finance application access review", "Review active roles in the finance application and confirm that access matches the approved responsibility matrix.", 3, "medium", "assigned", "2026-09-21 14:20:00", 1, "2026-09-30", "2026-09-22 08:30:00", categories["Access"], "5"),
            (104, "Warehouse label printer is offline", "The dispatch label printer shows offline on two workstations. Power and network indicators are on.", 2, "high", "awaiting_confirmation", "2026-09-20 08:45:00", 0, "2026-09-23", "2026-09-22 13:10:00", categories["Hardware"], "1"),
            (105, "Install approved design software", "Install the approved design package from the internal software catalog on the marketing workstation.", 4, "low", "closed", "2026-09-18 12:10:00", 0, "2026-09-20", "2026-09-20 16:35:00", categories["Software"], "5"),
            (106, "Guest Wi-Fi drops during meetings", "Guest Wi-Fi disconnects intermittently in the second-floor meeting room during video calls.", 3, "medium", "new", "2026-09-22 15:25:00", 0, None, "2026-09-22 15:25:00", categories["Network"], "IT"),
        ],
    )

    cursor.executemany(
        "INSERT INTO comments (ticket_id, user_id, comment, created_at) VALUES (?, ?, ?, ?)",
        [
            (101, 1, "I reproduced the issue and refreshed the VPN profile. Please test the new connection settings.", "2026-09-22 11:40:00"),
            (101, 2, "The connection now starts, but access to the shared project folder is still unavailable.", "2026-09-22 12:05:00"),
            (104, 1, "The print service was restarted and a test label completed successfully.", "2026-09-22 13:10:00"),
        ],
    )
    cursor.executemany(
        "INSERT INTO ticket_watchers (ticket_id, user_id) VALUES (?, ?)",
        [(101, 2), (101, 3), (103, 3), (103, 5), (104, 2)],
    )
    cursor.executemany(
        """
        INSERT INTO ticket_activity_log
            (ticket_id, user_id, action_type, old_value, new_value, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (101, 1, "status_changed", "assigned", "in_progress", "Work started", "2026-09-22 10:20:00"),
            (101, 1, "reassigned", "IT", "Alex Carter", "Assigned for investigation", "2026-09-22 09:35:00"),
            (104, 1, "status_changed", "in_progress", "awaiting_confirmation", "Fix ready for confirmation", "2026-09-22 13:10:00"),
        ],
    )
    cursor.executemany(
        "INSERT INTO activity_logs (user_id, action, details, ip_address, created_at) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "LOGIN", "Demo administrator signed in", "127.0.0.1", "2026-09-22 09:00:00"),
            (1, "TICKET_UPDATED", "Updated demo ticket #101", "127.0.0.1", "2026-09-22 11:40:00"),
            (5, "TICKET_ASSIGNED", "Assigned demo ticket #103", "127.0.0.1", "2026-09-22 08:30:00"),
        ],
    )
    cursor.execute(
        """
        INSERT INTO ticket_templates
            (user_id, name, title, description, priority, category_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (1, "New starter setup", "Prepare laptop and accounts", "Prepare standard equipment and approved access for a new starter.", "medium", categories["Hardware"]),
    )

    connection.commit()
    connection.close()
    return path


def _reset_demo_content(path):
    """Clear only disposable demo records without replacing a locked SQLite file."""
    tables = (
        "attachments",
        "comments",
        "ticket_activity_log",
        "ticket_watchers",
        "activity_logs",
        "ticket_templates",
        "ticket_muted_users",
        "tickets",
        "users",
    )
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        for table in tables:
            connection.execute(f"DELETE FROM {table}")
        connection.commit()
