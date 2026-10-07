"""Token-authenticated JSON API for TicketX (used by CLI tools and AI agents).

Authentication is a bearer token (`Authorization: Bearer tx_...`). Only the
SHA-256 hash of a token is stored. A token acts as the user it was created
for, and its name (for example `claude-code` or `codex`) is written in front
of every comment so the history shows who did what.

There is intentionally no delete endpoint.
"""
import hashlib
import re
import secrets
import threading
import time
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime
from functools import wraps

from flask import Blueprint, Response, g, jsonify, request

from database import Database

TOKEN_PREFIX = "tx_"
VALID_SCOPES = {"read", "write"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_TITLE = 200
MAX_DESCRIPTION = 20_000
MAX_COMMENT = 10_000

_failures = []
_failures_lock = threading.Lock()
FAILURE_WINDOW = 60
FAILURE_LIMIT = 30


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_token(user_id, name, scope="write", db=None):
    """Create a token and return its plaintext value (shown only once)."""
    if not NAME_RE.match(name):
        raise ValueError("Token name: 1-40 chars of a-z, 0-9, '.', '_', '-'.")
    if scope not in VALID_SCOPES:
        raise ValueError("Scope must be 'read' or 'write'.")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    conn = (db or Database()).get_connection()
    try:
        conn.execute(
            "INSERT INTO api_tokens (user_id, name, token_hash, scope) VALUES (?, ?, ?, ?)",
            (user_id, name, hash_token(token), scope),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def list_tokens(db=None):
    conn = (db or Database()).get_connection()
    try:
        return conn.execute(
            """SELECT t.id, u.username, t.name, t.scope, t.created_at, t.last_used_at, t.revoked_at
               FROM api_tokens t JOIN users u ON u.id = t.user_id ORDER BY t.id"""
        ).fetchall()
    finally:
        conn.close()


def revoke_token(token_id, db=None):
    conn = (db or Database()).get_connection()
    try:
        cur = conn.execute(
            "UPDATE api_tokens SET revoked_at = datetime('now', 'localtime') "
            "WHERE id = ? AND revoked_at IS NULL",
            (token_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _rate_limited():
    now = time.monotonic()
    with _failures_lock:
        _failures[:] = [t for t in _failures if now - t < FAILURE_WINDOW]
        return len(_failures) >= FAILURE_LIMIT


def _record_failure():
    with _failures_lock:
        _failures.append(time.monotonic())


def _error(status, message):
    response = jsonify({"error": message})
    response.status_code = status
    return response


def _authenticate():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header[7:].strip()
    if not token.startswith(TOKEN_PREFIX) or len(token) > 200:
        return None
    conn = Database().get_connection()
    try:
        row = conn.execute(
            """SELECT t.id, t.name, t.scope, u.id, u.username, u.full_name, u.role
               FROM api_tokens t JOIN users u ON u.id = t.user_id
               WHERE t.token_hash = ? AND t.revoked_at IS NULL""",
            (hash_token(token),),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE api_tokens SET last_used_at = datetime('now', 'localtime') WHERE id = ?",
            (row[0],),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "token_id": row[0], "actor": row[1], "scope": row[2],
        "user_id": row[3], "username": row[4], "full_name": row[5], "role": row[6],
    }


def token_required(write=False):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if _rate_limited():
                return _error(429, "Too many failed authentication attempts.")
            auth = _authenticate()
            if not auth:
                _record_failure()
                return _error(401, "Missing or invalid API token.")
            if write and auth["scope"] != "write":
                return _error(403, "This token is read-only.")
            g.api = auth
            return view(*args, **kwargs)
        return wrapper
    return decorator


TICKET_SELECT = """
    SELECT t.id, t.title, t.description, c.name, t.priority, t.status, t.due_date,
           t.assigned_to, t.created_at, t.updated_at, u.full_name, t.is_private
    FROM tickets t
    LEFT JOIN categories c ON c.id = t.category_id
    LEFT JOIN users u ON u.id = t.created_by
"""


def _ticket_dict(row):
    return {
        "id": row[0], "title": row[1], "description": row[2] or "", "project": row[3],
        "priority": row[4], "status": row[5], "due_date": row[6], "assigned_to": row[7],
        "created_at": row[8], "updated_at": row[9], "created_by": row[10],
        "is_private": bool(row[11]),
    }


def _visible(auth):
    """SQL fragment limiting tickets to what the token's user may see."""
    if auth["role"] == "admin":
        return "1=1", []
    return "(t.created_by = ? OR t.is_private = 0)", [auth["user_id"]]


def _comments(conn, ticket_id, limit=None):
    sql = """SELECT cm.id, cm.comment, cm.created_at, u.full_name
             FROM comments cm JOIN users u ON u.id = cm.user_id
             WHERE cm.ticket_id = ? ORDER BY cm.id DESC"""
    params = [ticket_id]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [
        {"id": r[0], "comment": r[1], "created_at": r[2], "author": r[3]}
        for r in reversed(rows)
    ]


def _fetch_ticket(conn, auth, ticket_id):
    clause, params = _visible(auth)
    row = conn.execute(
        TICKET_SELECT + " WHERE t.id = ? AND " + clause, [ticket_id] + params
    ).fetchone()
    return row


def _project_id(conn, name, create):
    name = (name or "").strip()
    if not name or len(name) > 100:
        return None
    row = conn.execute("SELECT id FROM categories WHERE lower(name) = lower(?)", (name,)).fetchone()
    if row:
        return row[0]
    if not create:
        return None
    cur = conn.execute("INSERT INTO categories (name, description) VALUES (?, '')", (name,))
    return cur.lastrowid


STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "into", "ticket", "task", "add", "fix",
    "za", "na", "da", "se", "je", "su", "sa", "od", "do", "po", "ili", "kao", "pa", "ali", "koji",
    "dodaj", "dodati", "napravi", "napraviti", "uradi", "uraditi",
}
SIMILARITY_THRESHOLD = 0.72
RECENTLY_CLOSED_DAYS = 14


def _normalize(text):
    """Lowercase, strip diacritics and punctuation, so 'Backup-ovi VPS-a' ~ 'backup ovi vps a'."""
    text = unicodedata.normalize("NFKD", text.lower().replace("đ", "dj").replace("Đ", "dj"))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _tokens(normalized):
    return {w for w in normalized.split() if len(w) > 2 and w not in STOPWORDS}


def title_similarity(a, b):
    """0..1: the larger of the character-level ratio and the word overlap (Jaccard)."""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    ta, tb = _tokens(na), _tokens(nb)
    jaccard = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    # one title fully contained in the other (e.g. "Backup VPS" in "Backup VPS: Vaultwarden") counts too
    containment = len(ta & tb) / min(len(ta), len(tb)) if ta and tb and min(len(ta), len(tb)) >= 2 else 0.0
    return max(ratio, jaccard, containment * 0.9)


def find_similar_tickets(conn, auth, title, limit=5):
    clause, params = _visible(auth)
    rows = conn.execute(
        TICKET_SELECT + " WHERE " + clause +
        " AND (t.status != 'closed' OR t.updated_at >= datetime('now', 'localtime', ?))",
        params + ["-%d days" % RECENTLY_CLOSED_DAYS],
    ).fetchall()
    scored = []
    for row in rows:
        score = title_similarity(title, row[1])
        if score >= SIMILARITY_THRESHOLD:
            scored.append((score, row))
    scored.sort(key=lambda item: -item[0])
    return [
        {"id": r[0], "title": r[1], "project": r[3], "status": r[5], "similarity": round(score, 2)}
        for score, r in scored[:limit]
    ]


def _status_filter(value):
    if value in (None, "", "open"):
        return "t.status != 'closed'", []
    if value == "all":
        return "1=1", []
    return "t.status = ?", [value]


def create_api_blueprint(valid_statuses, valid_priorities, log_ticket_activity, timezone_name):
    bp = Blueprint("api", __name__, url_prefix="/api/v1")

    @bp.after_request
    def no_store(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @bp.route("/health")
    @token_required()
    def health():
        a = g.api
        return jsonify({"ok": True, "user": a["username"], "actor": a["actor"], "scope": a["scope"]})

    @bp.route("/projects")
    @token_required()
    def projects():
        conn = Database().get_connection()
        try:
            rows = conn.execute(
                """SELECT c.id, c.name, c.description,
                          SUM(CASE WHEN t.id IS NOT NULL AND t.status != 'closed' THEN 1 ELSE 0 END)
                   FROM categories c LEFT JOIN tickets t ON t.category_id = c.id
                   GROUP BY c.id ORDER BY c.name"""
            ).fetchall()
        finally:
            conn.close()
        return jsonify([
            {"id": r[0], "name": r[1], "description": r[2] or "", "open_tickets": r[3] or 0}
            for r in rows
        ])

    @bp.route("/tickets", methods=["GET"])
    @token_required()
    def list_tickets():
        clause, params = _visible(g.api)
        status_clause, status_params = _status_filter(request.args.get("status"))
        where = [clause, status_clause]
        params = params + status_params
        project = request.args.get("project")
        if project:
            where.append("lower(c.name) = lower(?)")
            params.append(project.strip())
        for word in request.args.get("q", "").split()[:8]:
            where.append("(t.title LIKE ? OR t.description LIKE ?)")
            params += ["%" + word + "%"] * 2
        conn = Database().get_connection()
        try:
            rows = conn.execute(
                TICKET_SELECT + " WHERE " + " AND ".join(where) + " ORDER BY t.updated_at DESC LIMIT 500",
                params,
            ).fetchall()
        finally:
            conn.close()
        return jsonify([_ticket_dict(r) for r in rows])

    @bp.route("/tickets/<int:ticket_id>", methods=["GET"])
    @token_required()
    def get_ticket(ticket_id):
        conn = Database().get_connection()
        try:
            row = _fetch_ticket(conn, g.api, ticket_id)
            if not row:
                return _error(404, "Ticket not found.")
            data = _ticket_dict(row)
            data["comments"] = _comments(conn, ticket_id)
            data["activity"] = [
                {"created_at": r[0], "action": r[1], "from": r[2], "to": r[3], "details": r[4]}
                for r in conn.execute(
                    """SELECT created_at, action_type, old_value, new_value, details
                       FROM ticket_activity_log WHERE ticket_id = ? ORDER BY id""",
                    (ticket_id,),
                ).fetchall()
            ]
        finally:
            conn.close()
        return jsonify(data)

    def _validated_fields(body, partial):
        """Return (fields, error). Fields only has keys that were provided."""
        fields = {}
        if "title" in body or not partial:
            title = str(body.get("title", "")).strip()
            if not title or len(title) > MAX_TITLE:
                return None, "title is required (max %d chars)." % MAX_TITLE
            fields["title"] = title
        if "description" in body or not partial:
            description = str(body.get("description", "")).strip()
            if len(description) > MAX_DESCRIPTION:
                return None, "description is too long (max %d chars)." % MAX_DESCRIPTION
            fields["description"] = description or "(no description)"
        if "priority" in body or not partial:
            priority = body.get("priority", "medium")
            if priority not in valid_priorities:
                return None, "priority must be one of: " + ", ".join(sorted(valid_priorities))
            fields["priority"] = priority
        if "status" in body:
            if body["status"] not in valid_statuses:
                return None, "status must be one of: " + ", ".join(sorted(valid_statuses))
            fields["status"] = body["status"]
        if "due_date" in body:
            due = body["due_date"]
            if due in (None, ""):
                fields["due_date"] = None
            elif isinstance(due, str) and DATE_RE.match(due):
                try:
                    datetime.strptime(due, "%Y-%m-%d")
                except ValueError:
                    return None, "due_date must be a real date (YYYY-MM-DD)."
                fields["due_date"] = due
            else:
                return None, "due_date must be YYYY-MM-DD or null."
        if "is_private" in body:
            fields["is_private"] = 1 if body["is_private"] else 0
        return fields, None

    @bp.route("/tickets", methods=["POST"])
    @token_required(write=True)
    def create_ticket():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _error(400, "JSON body required.")
        fields, error = _validated_fields(body, partial=False)
        if error:
            return _error(400, error)
        conn = Database().get_connection()
        try:
            project_id = _project_id(conn, body.get("project"), create=True)
            if not project_id:
                return _error(400, "project is required (max 100 chars).")
            if not body.get("force"):
                similar = find_similar_tickets(conn, g.api, fields["title"])
                if similar:
                    response = jsonify({
                        "error": "Similar ticket(s) already exist. Comment on one of them, or send "
                                 "\"force\": true if this is really a different task.",
                        "similar": similar,
                    })
                    response.status_code = 409
                    return response
            cur = conn.execute(
                """INSERT INTO tickets (title, description, priority, category_id, due_date,
                                        created_by, assigned_to, is_private, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new')""",
                (fields["title"], fields["description"], fields["priority"], project_id,
                 fields.get("due_date"), g.api["user_id"], str(g.api["user_id"]),
                 fields.get("is_private", 0)),
            )
            ticket_id = cur.lastrowid
            conn.commit()
            row = _fetch_ticket(conn, g.api, ticket_id)
        finally:
            conn.close()
        log_ticket_activity(ticket_id, g.api["user_id"], "created_via_api", None, None, g.api["actor"])
        response = jsonify(_ticket_dict(row))
        response.status_code = 201
        return response

    @bp.route("/tickets/<int:ticket_id>", methods=["PATCH"])
    @token_required(write=True)
    def update_ticket(ticket_id):
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not body:
            return _error(400, "JSON body with fields to change required.")
        fields, error = _validated_fields(body, partial=True)
        if error:
            return _error(400, error)
        conn = Database().get_connection()
        try:
            row = _fetch_ticket(conn, g.api, ticket_id)
            if not row:
                return _error(404, "Ticket not found.")
            before = _ticket_dict(row)
            if "project" in body:
                project_id = _project_id(conn, body.get("project"), create=True)
                if not project_id:
                    return _error(400, "project must be a non-empty name (max 100 chars).")
                fields["category_id"] = project_id
            if not fields:
                return _error(400, "No changeable fields provided.")
            sets = ", ".join(k + " = ?" for k in fields)
            conn.execute(
                "UPDATE tickets SET " + sets + ", updated_at = datetime('now', 'localtime') WHERE id = ?",
                list(fields.values()) + [ticket_id],
            )
            conn.commit()
            row = _fetch_ticket(conn, g.api, ticket_id)
        finally:
            conn.close()
        after = _ticket_dict(row)
        for key in ("status", "priority", "title", "due_date", "project"):
            if before[key] != after[key]:
                action = "status_changed" if key == "status" else key + "_changed"
                log_ticket_activity(ticket_id, g.api["user_id"], action,
                                    str(before[key]), str(after[key]), g.api["actor"])
        return jsonify(after)

    @bp.route("/tickets/<int:ticket_id>/comments", methods=["POST"])
    @token_required(write=True)
    def add_comment(ticket_id):
        body = request.get_json(silent=True)
        text = str(body.get("comment", "")).strip() if isinstance(body, dict) else ""
        if not text or len(text) > MAX_COMMENT:
            return _error(400, "comment is required (max %d chars)." % MAX_COMMENT)
        stamped = "[%s] %s" % (g.api["actor"], text)
        conn = Database().get_connection()
        try:
            if not _fetch_ticket(conn, g.api, ticket_id):
                return _error(404, "Ticket not found.")
            cur = conn.execute(
                "INSERT INTO comments (ticket_id, user_id, comment) VALUES (?, ?, ?)",
                (ticket_id, g.api["user_id"], stamped),
            )
            conn.execute(
                "UPDATE tickets SET updated_at = datetime('now', 'localtime') WHERE id = ?",
                (ticket_id,),
            )
            conn.commit()
            comment_id = cur.lastrowid
        finally:
            conn.close()
        response = jsonify({"id": comment_id, "ticket_id": ticket_id, "comment": stamped})
        response.status_code = 201
        return response

    @bp.route("/context.md")
    @token_required()
    def context_markdown():
        """One Markdown file with the current state: open tickets by project, recent comments."""
        project = request.args.get("project", "").strip()
        status_clause, status_params = _status_filter(request.args.get("status"))
        try:
            comment_count = max(0, min(int(request.args.get("comments", 3)), 20))
        except ValueError:
            comment_count = 3
        clause, params = _visible(g.api)
        where = [clause, status_clause]
        params = params + status_params
        if project:
            where.append("lower(c.name) = lower(?)")
            params.append(project)
        conn = Database().get_connection()
        try:
            rows = conn.execute(
                TICKET_SELECT + " WHERE " + " AND ".join(where) +
                " ORDER BY c.name, CASE t.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, t.updated_at DESC",
                params,
            ).fetchall()
            tickets = []
            for row in rows:
                data = _ticket_dict(row)
                data["comments"] = _comments(conn, data["id"], comment_count) if comment_count else []
                tickets.append(data)
        finally:
            conn.close()
        return Response(_render_context(tickets, project, request.args.get("status") or "open",
                                        timezone_name), mimetype="text/markdown; charset=utf-8")

    return bp


def _render_context(tickets, project, status, timezone_name):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# TicketX context",
        "",
        "Generated %s (%s) | filter: status=%s%s | %d ticket(s)" % (
            now, timezone_name, status, ", project=" + project if project else "", len(tickets)),
        "",
        "Rules: read this before working; add a comment on the ticket with what was done, decided and what "
        "is next; change status when it changes (new, assigned, in_progress, awaiting_confirmation, closed). "
        "Comments are prefixed with the actor name in brackets.",
        "",
    ]
    current = object()
    for t in tickets:
        if t["project"] != current:
            current = t["project"]
            count = sum(1 for x in tickets if x["project"] == current)
            lines += ["## %s (%d)" % (current or "(no project)", count), ""]
        due = ", due %s" % t["due_date"] if t["due_date"] else ""
        lines.append("### #%d %s" % (t["id"], t["title"]))
        lines.append("- status: %s | priority: %s%s | updated: %s" % (
            t["status"], t["priority"], due, t["updated_at"]))
        description = t["description"].strip()
        if description and description != "(no description)":
            if len(description) > 800:
                description = description[:800].rstrip() + " ... (truncated; use `ticketx show %d`)" % t["id"]
            lines += ["", description]
        if t["comments"]:
            lines += ["", "Recent comments:"]
            for c in t["comments"]:
                text = " ".join(c["comment"].split())
                if len(text) > 400:
                    text = text[:400].rstrip() + " ..."
                lines.append("- %s %s" % (c["created_at"][:16], text))
        lines.append("")
    if not tickets:
        lines += ["_No tickets match._", ""]
    return "\n".join(lines)
