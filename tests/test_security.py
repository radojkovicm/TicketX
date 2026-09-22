import io
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from openpyxl import Workbook


TEST_ROOT = tempfile.TemporaryDirectory()
TEST_DB = Path(TEST_ROOT.name) / "ticketx-test.db"
TEST_UPLOADS = Path(TEST_ROOT.name) / "uploads"

os.environ["FLASK_SECRET_KEY"] = "unit-test-secret-key-with-sufficient-length"
os.environ["DISABLE_INITIAL_ADMIN_CREATION"] = "true"
os.environ["DB_PATH"] = str(TEST_DB)
os.environ["UPLOAD_FOLDER"] = str(TEST_UPLOADS)
os.environ["ENABLE_ASYNC_EMAIL"] = "false"

import app as ticketx  # noqa: E402
from security import hash_password, is_legacy_hash, verify_password  # noqa: E402


class TicketXSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ticketx.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        ticketx.app.config["UPLOAD_FOLDER"] = str(TEST_UPLOADS)
        TEST_UPLOADS.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        TEST_ROOT.cleanup()

    def setUp(self):
        self.client = ticketx.app.test_client()
        self.notification_patches = [
            patch.object(ticketx, name)
            for name in (
                "notify_on_attachment",
                "notify_on_closed",
                "notify_on_comment",
                "notify_on_reassigned",
                "notify_on_status_change",
                "notify_on_watchers_added",
            )
        ]
        for notification_patch in self.notification_patches:
            notification_patch.start()
            self.addCleanup(notification_patch.stop)

        with closing(sqlite3.connect(TEST_DB)) as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            for table in (
                "ticket_muted_users",
                "ticket_templates",
                "activity_logs",
                "ticket_activity_log",
                "attachments",
                "comments",
                "ticket_watchers",
                "tickets",
                "users",
            ):
                conn.execute(f"DELETE FROM {table}")
            conn.executemany(
                """
                INSERT INTO users
                    (id, username, password, email, full_name, role, department_id, is_department_head)
                VALUES (?, ?, ?, ?, ?, ?, NULL, 0)
                """,
                [
                    (1, "admin", hash_password("AdminPassword123!"), "admin@example.com", "Admin User", "admin"),
                    (2, "creator", hash_password("CreatorPassword123!"), "creator@example.com", "Ticket Creator", "user"),
                    (3, "other", hash_password("OtherPassword123!"), "other@example.com", "Other User", "user"),
                    (4, "assignee", hash_password("AssigneePassword123!"), "assignee@example.com", "Assigned User", "user"),
                ],
            )
            conn.executemany(
                """
                INSERT INTO tickets
                    (id, title, description, created_by, priority, status, is_private, category_id, assigned_to)
                VALUES (?, ?, ?, ?, 'medium', ?, ?, 1, ?)
                """,
                [
                    (1, "Private ticket", "Private details", 2, "assigned", 1, "4"),
                    (2, "Public ticket", "Public details", 2, "new", 0, "IT"),
                ],
            )
            conn.commit()

    def login_as(self, user_id):
        with closing(sqlite3.connect(TEST_DB)) as conn:
            user = conn.execute(
                "SELECT username, full_name, role, department_id, is_department_head FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["username"] = user[0]
            session["full_name"] = user[1]
            session["role"] = user[2]
            session["department_id"] = user[3]
            session["is_department_head"] = user[4]
            session["last_activity"] = __import__("time").time()

    def test_private_ticket_is_hidden_from_unrelated_user(self):
        self.login_as(3)
        self.assertEqual(self.client.get("/ticket/1").status_code, 403)
        self.assertEqual(self.client.get("/edit_ticket/1").status_code, 403)

    def test_private_ticket_mutations_are_forbidden(self):
        self.login_as(3)
        self.assertEqual(
            self.client.post("/add_comment", data={"ticket_id": 1, "comment": "Injected"}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/upload_attachment",
                data={"ticket_id": 1, "file": (io.BytesIO(b"data"), "proof.txt")},
                content_type="multipart/form-data",
            ).status_code,
            403,
        )

    def test_public_ticket_can_be_viewed_and_commented_on(self):
        self.login_as(3)
        self.assertEqual(self.client.get("/ticket/2").status_code, 200)
        response = self.client.post(
            "/add_comment",
            data={"ticket_id": 2, "comment": "A useful comment"},
        )
        self.assertEqual(response.status_code, 302)
        with closing(sqlite3.connect(TEST_DB)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM comments WHERE ticket_id = 2").fetchone()[0], 1)

    def test_only_valid_status_transition_is_accepted(self):
        self.login_as(3)
        self.assertEqual(
            self.client.post("/update_ticket_status", data={"ticket_id": 1, "status": "closed"}).status_code,
            403,
        )

        self.client.post("/logout")
        self.login_as(4)
        response = self.client.post(
            "/update_ticket_status",
            data={"ticket_id": 1, "status": "in_progress"},
        )
        self.assertEqual(response.status_code, 302)
        with closing(sqlite3.connect(TEST_DB)) as conn:
            self.assertEqual(conn.execute("SELECT status FROM tickets WHERE id = 1").fetchone()[0], "in_progress")

    def test_attachment_download_checks_ticket_access_and_path(self):
        stored_file = TEST_UPLOADS / "private.txt"
        stored_file.write_text("private", encoding="utf-8")
        with closing(sqlite3.connect(TEST_DB)) as conn:
            conn.execute(
                """
                INSERT INTO attachments
                    (id, ticket_id, filename, original_filename, file_path, uploaded_by)
                VALUES (1, 1, 'private.txt', 'private.txt', ?, 2)
                """,
                (str(stored_file),),
            )
            conn.execute(
                """
                INSERT INTO attachments
                    (id, ticket_id, filename, original_filename, file_path, uploaded_by)
                VALUES (2, 2, 'outside.txt', 'outside.txt', ?, 2)
                """,
                (str(Path(TEST_ROOT.name).parent / "outside.txt"),),
            )
            conn.commit()

        self.login_as(3)
        self.assertEqual(self.client.get("/download_attachment/1").status_code, 403)
        self.assertEqual(self.client.get("/download_attachment/2").status_code, 404)

    def test_disallowed_upload_is_rejected(self):
        self.login_as(3)
        response = self.client.post(
            "/upload_attachment",
            data={"ticket_id": 2, "file": (io.BytesIO(b"<html>"), "payload.html")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        with closing(sqlite3.connect(TEST_DB)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 0)

    def test_allowed_upload_is_private_and_downloadable_by_authorized_user(self):
        self.login_as(3)
        response = self.client.post(
            "/upload_attachment",
            data={"ticket_id": 2, "file": (io.BytesIO(b"evidence"), "evidence.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        with closing(sqlite3.connect(TEST_DB)) as conn:
            attachment = conn.execute(
                "SELECT id, file_path FROM attachments WHERE ticket_id = 2"
            ).fetchone()
        self.assertIsNotNone(attachment)
        self.assertTrue(Path(attachment[1]).is_absolute() or not str(attachment[1]).startswith("static/"))
        download = self.client.get(f"/download_attachment/{attachment[0]}")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data, b"evidence")
        download.close()

    def test_admin_password_update_uses_modern_hash(self):
        self.login_as(1)
        response = self.client.post(
            "/update_user/3",
            data={
                "username": "other",
                "password": "NewSecurePassword123!",
                "full_name": "Other User",
                "email": "other@example.com",
                "role": "user",
                "department_id": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        with closing(sqlite3.connect(TEST_DB)) as conn:
            stored_hash = conn.execute("SELECT password FROM users WHERE id = 3").fetchone()[0]
        self.assertFalse(is_legacy_hash(stored_hash))
        self.assertTrue(verify_password(stored_hash, "NewSecurePassword123!"))

    def test_admin_can_import_users_from_xlsx(self):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append(["username", "password", "full_name", "email", "role"])
        worksheet.append(
            ["imported", "ImportedPassword123!", "Imported User", "imported@example.com", "user"]
        )
        spreadsheet = io.BytesIO()
        workbook.save(spreadsheet)
        workbook.close()
        spreadsheet.seek(0)

        self.login_as(1)
        response = self.client.post(
            "/import_users",
            data={"excel_file": (spreadsheet, "users.xlsx")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        with closing(sqlite3.connect(TEST_DB)) as conn:
            imported = conn.execute(
                "SELECT password, role FROM users WHERE username = 'imported'"
            ).fetchone()
        self.assertIsNotNone(imported)
        self.assertEqual(imported[1], "user")
        self.assertTrue(verify_password(imported[0], "ImportedPassword123!"))

    def test_logout_requires_post_and_security_headers_are_present(self):
        self.login_as(3)
        self.assertEqual(self.client.get("/logout").status_code, 405)
        response = self.client.get("/dashboard")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(self.client.post("/logout").status_code, 302)

    def test_last_administrator_cannot_be_demoted(self):
        self.login_as(1)
        response = self.client.post(
            "/update_user/1",
            data={
                "username": "admin",
                "full_name": "Admin User",
                "email": "admin@example.com",
                "role": "user",
                "department_id": "",
            },
        )
        self.assertEqual(response.status_code, 409)

    def test_login_is_throttled_after_repeated_failures(self):
        for _ in range(ticketx.LOGIN_MAX_FAILURES):
            response = self.client.post(
                "/login",
                data={"username": "rate-limit-user", "password": "wrong"},
            )
            self.assertEqual(response.status_code, 200)
        response = self.client.post(
            "/login",
            data={"username": "rate-limit-user", "password": "wrong"},
        )
        self.assertEqual(response.status_code, 429)


if __name__ == "__main__":
    unittest.main()
