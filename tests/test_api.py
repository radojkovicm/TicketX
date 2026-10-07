import sqlite3
import unittest
from contextlib import closing

# Reuse the app instance and temporary database of the security tests: the models in
# app.py bind to the database path that was configured when `app` was first imported.
try:  # `unittest discover -s tests` imports test modules as top-level names
    import test_security as base  # noqa: E402
except ImportError:  # `python -m unittest tests.test_api` from the repository root
    from tests import test_security as base  # noqa: E402

ticketx = base.ticketx
import api as ticketx_api  # noqa: E402
from database import Database  # noqa: E402
from security import hash_password  # noqa: E402


def db_path():
    return str(base.TEST_DB)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # CSRF stays enabled on purpose: the token API must work without CSRF tokens.
        ticketx.app.config.update(TESTING=True)

    def setUp(self):
        Database()  # make sure the schema exists for the current DB_PATH
        with closing(sqlite3.connect(db_path())) as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            for table in ("api_tokens", "ticket_activity_log", "comments", "tickets", "users"):
                conn.execute("DELETE FROM " + table)
            conn.executemany(
                "INSERT INTO users (id, username, password, email, full_name, role) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (1, "owner", hash_password("OwnerPassword123!"), "o@example.com", "Owner", "admin"),
                    (2, "plain", hash_password("PlainPassword123!"), "p@example.com", "Plain User", "user"),
                    (3, "other", hash_password("OtherPassword123!"), "x@example.com", "Other User", "user"),
                ],
            )
            conn.commit()
        self.client = ticketx.app.test_client()
        self.write = {"Authorization": "Bearer " + ticketx_api.create_token(1, "claude-code")}
        self.read = {"Authorization": "Bearer " + ticketx_api.create_token(1, "viewer", "read")}

    def create(self, **overrides):
        body = {"project": "GetSuper", "title": "Fix login", "description": "details", "priority": "high"}
        body.update(overrides)
        return self.client.post("/api/v1/tickets", json=body, headers=self.write)

    def test_requires_valid_token(self):
        self.assertEqual(self.client.get("/api/v1/tickets").status_code, 401)
        bad = {"Authorization": "Bearer tx_nope"}
        self.assertEqual(self.client.get("/api/v1/tickets", headers=bad).status_code, 401)
        self.assertEqual(self.client.get("/api/v1/health", headers=self.write).status_code, 200)

    def test_token_is_stored_hashed(self):
        token = self.write["Authorization"].split()[1]
        with closing(sqlite3.connect(db_path())) as conn:
            hashes = [r[0] for r in conn.execute("SELECT token_hash FROM api_tokens")]
        self.assertNotIn(token, hashes)
        self.assertIn(ticketx_api.hash_token(token), hashes)

    def test_revoked_token_stops_working(self):
        token_id = ticketx_api.list_tokens()[0][0]
        self.assertTrue(ticketx_api.revoke_token(token_id))
        self.assertEqual(self.client.get("/api/v1/tickets", headers=self.write).status_code, 401)

    def test_read_only_token_cannot_write(self):
        response = self.client.post("/api/v1/tickets", json={"project": "P", "title": "T"}, headers=self.read)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/api/v1/tickets", headers=self.read).status_code, 200)

    def test_create_creates_project_and_ticket(self):
        response = self.create()
        self.assertEqual(response.status_code, 201)
        ticket = response.get_json()
        self.assertEqual((ticket["project"], ticket["status"], ticket["priority"]), ("GetSuper", "new", "high"))
        projects = self.client.get("/api/v1/projects", headers=self.write).get_json()
        self.assertEqual({p["name"]: p["open_tickets"] for p in projects}["GetSuper"], 1)
        # project names are matched case-insensitively, no duplicate project
        self.create(project="getsuper", title="Second")
        projects = self.client.get("/api/v1/projects", headers=self.write).get_json()
        names = [p["name"] for p in projects]
        self.assertEqual(sum(1 for n in names if n.lower() == "getsuper"), 1)
        self.assertEqual({p["name"]: p["open_tickets"] for p in projects}["GetSuper"], 2)

    def test_validation_errors(self):
        self.assertEqual(self.create(title="").status_code, 400)
        self.assertEqual(self.create(priority="urgent").status_code, 400)
        self.assertEqual(self.create(project="").status_code, 400)
        self.assertEqual(self.create(due_date="2026-13-45").status_code, 400)
        self.assertEqual(self.create(due_date="soon").status_code, 400)
        self.assertEqual(self.create(title="x" * 201).status_code, 400)

    def test_update_status_and_activity_log(self):
        ticket_id = self.create().get_json()["id"]
        response = self.client.patch(f"/api/v1/tickets/{ticket_id}", json={"status": "in_progress"}, headers=self.write)
        self.assertEqual(response.get_json()["status"], "in_progress")
        bad = self.client.patch(f"/api/v1/tickets/{ticket_id}", json={"status": "done"}, headers=self.write)
        self.assertEqual(bad.status_code, 400)
        detail = self.client.get(f"/api/v1/tickets/{ticket_id}", headers=self.write).get_json()
        actions = [(a["action"], a["from"], a["to"], a["details"]) for a in detail["activity"]]
        self.assertIn(("status_changed", "new", "in_progress", "claude-code"), actions)

    def test_open_filter_hides_closed(self):
        open_id = self.create(title="Open one").get_json()["id"]
        closed_id = self.create(title="Closed one").get_json()["id"]
        self.client.patch(f"/api/v1/tickets/{closed_id}", json={"status": "closed"}, headers=self.write)
        ids = lambda q: {t["id"] for t in self.client.get("/api/v1/tickets" + q, headers=self.write).get_json()}
        self.assertEqual(ids(""), {open_id})
        self.assertEqual(ids("?status=all"), {open_id, closed_id})
        self.assertEqual(ids("?status=closed"), {closed_id})
        self.assertEqual(ids("?q=Closed"), set())
        self.assertEqual(ids("?status=all&q=Closed"), {closed_id})

    def test_comment_is_attributed_to_the_token_name(self):
        ticket_id = self.create().get_json()["id"]
        response = self.client.post(f"/api/v1/tickets/{ticket_id}/comments",
                                    json={"comment": "Deployed to staging"}, headers=self.write)
        self.assertEqual(response.status_code, 201)
        detail = self.client.get(f"/api/v1/tickets/{ticket_id}", headers=self.write).get_json()
        self.assertEqual(detail["comments"][0]["comment"], "[claude-code] Deployed to staging")
        empty = self.client.post(f"/api/v1/tickets/{ticket_id}/comments", json={"comment": " "}, headers=self.write)
        self.assertEqual(empty.status_code, 400)

    def test_non_admin_token_cannot_see_foreign_private_tickets(self):
        plain = {"Authorization": "Bearer " + ticketx_api.create_token(2, "plain")}
        other = {"Authorization": "Bearer " + ticketx_api.create_token(3, "other")}
        private = self.client.post("/api/v1/tickets", json={"project": "P", "title": "Secret", "is_private": True},
                                   headers=plain).get_json()["id"]
        self.assertEqual(self.client.get(f"/api/v1/tickets/{private}", headers=other).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/tickets", headers=other).get_json(), [])
        self.assertEqual(self.client.post(f"/api/v1/tickets/{private}/comments", json={"comment": "x"},
                                          headers=other).status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/tickets/{private}", headers=self.write).status_code, 200)

    def test_context_markdown(self):
        first = self.create(title="Fix login", due_date="2026-10-20").get_json()["id"]
        self.create(project="Homelab", title="Backup VPS", priority="medium")
        self.client.post(f"/api/v1/tickets/{first}/comments", json={"comment": "Started"}, headers=self.write)
        response = self.client.get("/api/v1/context.md", headers=self.write)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.mimetype.startswith("text/markdown"))
        text = response.get_data(as_text=True)
        for expected in ("# TicketX context", "## GetSuper (1)", "## Homelab (1)", "### #%d Fix login" % first,
                         "due 2026-10-20", "[claude-code] Started"):
            self.assertIn(expected, text)
        only = self.client.get("/api/v1/context.md?project=homelab", headers=self.write).get_data(as_text=True)
        self.assertIn("Backup VPS", only)
        self.assertNotIn("Fix login", only)

    def test_session_cookie_does_not_authenticate_api(self):
        with self.client.session_transaction() as session:
            session["user_id"] = 1
            session["role"] = "admin"
        self.assertEqual(self.client.get("/api/v1/tickets").status_code, 401)

    def test_new_tickets_are_assigned_to_the_token_user(self):
        ticket = self.create().get_json()
        self.assertEqual(ticket["assigned_to"], "1")

    def test_similar_ticket_is_rejected_with_candidates(self):
        first = self.create(title="Uvesti backup za VPS").get_json()["id"]
        for title in ("uvesti backup za VPS", "Backup za VPS - uvesti", "Uvesti  BACKUP za vps!"):
            response = self.create(title=title)
            self.assertEqual(response.status_code, 409, title)
            self.assertEqual(response.get_json()["similar"][0]["id"], first)
        self.assertEqual(len(self.client.get("/api/v1/tickets", headers=self.write).get_json()), 1)

    def test_unrelated_ticket_is_allowed(self):
        self.create(title="Uvesti backup za VPS")
        self.assertEqual(self.create(title="Podesiti DNS za getsuper.si").status_code, 201)

    def test_force_creates_despite_similar_ticket(self):
        self.create(title="Uvesti backup za VPS")
        self.assertEqual(self.create(title="Uvesti backup za VPS", force=True).status_code, 201)

    def test_diacritics_do_not_hide_duplicates(self):
        self.create(title="Podesiti đurđevak čitač")
        self.assertEqual(self.create(title="podesiti djurdjevak citac").status_code, 409)

    def test_recently_closed_ticket_still_counts_as_duplicate(self):
        ticket_id = self.create(title="Uvesti backup za VPS").get_json()["id"]
        self.client.patch(f"/api/v1/tickets/{ticket_id}", json={"status": "closed"}, headers=self.write)
        self.assertEqual(self.create(title="Uvesti backup za VPS").status_code, 409)
        with closing(sqlite3.connect(db_path())) as conn:
            conn.execute("UPDATE tickets SET updated_at = datetime('now', 'localtime', '-30 days') WHERE id = ?",
                         (ticket_id,))
            conn.commit()
        self.assertEqual(self.create(title="Uvesti backup za VPS").status_code, 201)

    def test_find_matches_all_words_in_any_order(self):
        self.create(title="Uvesti backup za VPS", description="Vaultwarden prvo")
        found = lambda q: self.client.get("/api/v1/tickets?status=all&q=" + q, headers=self.write).get_json()
        self.assertEqual(len(found("vps+backup")), 1)
        self.assertEqual(len(found("vaultwarden+backup")), 1)
        self.assertEqual(len(found("backup+nextcloud")), 0)

    def test_no_delete_endpoint(self):
        ticket_id = self.create().get_json()["id"]
        self.assertEqual(self.client.delete(f"/api/v1/tickets/{ticket_id}", headers=self.write).status_code, 405)


if __name__ == "__main__":
    unittest.main()
