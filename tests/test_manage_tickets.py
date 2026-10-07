import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from database import Database

ROOT = Path(__file__).resolve().parent.parent


class ManageTicketsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "t.db")
        Database(self.db_path)  # creates the schema
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("INSERT INTO users (id, username, password, full_name, role) VALUES (1,'u','x','U','admin')")
            conn.execute("INSERT INTO categories (id, name) VALUES (100,'Posao'), (101,'Licno')")
            for tid, cat, title in ((1, 100, "a"), (2, 100, "b"), (3, 101, "c")):
                conn.execute("INSERT INTO tickets (id, title, description, created_by, category_id) VALUES (?,?,?,1,?)",
                             (tid, title, "d", cat))
            conn.execute("INSERT INTO comments (ticket_id, user_id, comment) VALUES (1, 1, 'x'), (3, 1, 'y')")
            conn.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args):
        env = dict(os.environ, DB_PATH=self.db_path, UPLOAD_FOLDER=str(Path(self.tmp.name) / "up"))
        return subprocess.run([sys.executable, str(ROOT / "manage_tickets.py"), *args],
                              capture_output=True, text=True, env=env, cwd=str(ROOT))

    def count(self, table):
        with closing(sqlite3.connect(self.db_path)) as conn:
            return conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def test_dry_run_changes_nothing(self):
        result = self.run_tool("delete", "--project", "posao")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 ticket(s) match", result.stdout)
        self.assertIn("Dry run", result.stdout)
        self.assertEqual(self.count("tickets"), 3)

    def test_delete_removes_project_tickets_comments_and_makes_backup(self):
        result = self.run_tool("delete", "--project", "Posao", "--drop-project", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.count("tickets"), 1)
        self.assertEqual(self.count("comments"), 1)  # the comment of the kept ticket stays
        with closing(sqlite3.connect(self.db_path)) as conn:
            names = [r[0] for r in conn.execute("SELECT name FROM categories WHERE id IN (100,101)")]
        self.assertEqual(names, ["Licno"])
        backups = list((Path(self.db_path).parent / "backups").glob("pre-delete-*.db"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0], 3)

    def test_requires_selector(self):
        self.assertNotEqual(self.run_tool("delete", "--yes").returncode, 0)
        self.assertEqual(self.count("tickets"), 3)


if __name__ == "__main__":
    unittest.main()
