import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from demo_data import DEMO_EMAIL_DOMAIN, ensure_demo_database


class DemoDataTests(unittest.TestCase):
    def test_demo_database_is_restored_with_branded_synthetic_emails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "ticketx-demo.db"

            ensure_demo_database(db_path)
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("DELETE FROM tickets")
                connection.execute("UPDATE users SET email = 'old@example.com'")
                connection.commit()

            ensure_demo_database(db_path)
            with closing(sqlite3.connect(db_path)) as connection:
                emails = [row[0] for row in connection.execute("SELECT email FROM users")]
                ticket_count = connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]

            self.assertEqual(ticket_count, 6)
            self.assertEqual(len(emails), 5)
            self.assertTrue(all(email.endswith(f"@{DEMO_EMAIL_DOMAIN}") for email in emails))


if __name__ == "__main__":
    unittest.main()
