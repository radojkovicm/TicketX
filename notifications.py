# notifications.py
# All emails in English, with clear structure and explicit change info.

import os
from database import Database
from mailer import send_email

def _bool_env(name, default=False):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() == "true"

EXCLUDE_ACTOR = _bool_env("EXCLUDE_ACTOR_FROM_NOTIFICATIONS", True)
NOTIFY_IT_ADMINS_IF_IT = _bool_env("NOTIFY_IT_ADMINS_WHEN_ASSIGNED_TO_IT", True)
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5000")

# ---------- DB helpers ----------

def _get_user_email(cursor, user_id):
    if not user_id:
        return None
    if isinstance(user_id, str):
        # Assigned to 'IT' (special)
        return None
    cursor.execute("SELECT email FROM users WHERE id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row and row[0] else None

def _get_user_name(cursor, user_id):
    if not user_id:
        return None
    if isinstance(user_id, str):
        return str(user_id).strip()
    cursor.execute("SELECT full_name FROM users WHERE id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row and row[0] else None

def _get_admin_emails(cursor):
    cursor.execute("""
        SELECT email FROM users 
        WHERE role='admin' AND email IS NOT NULL AND email != ''
    """)
    return [r[0] for r in cursor.fetchall()]

def _get_ticket_core(cursor, ticket_id):
    cursor.execute("""
        SELECT 
            t.id, t.title, t.priority, t.status, t.created_by, t.assigned_to,
            t.description,
            c.name as category_name
        FROM tickets t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.id = ?
    """, (ticket_id,))
    row = cursor.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "title": row[1],
        "priority": row[2],
        "status": row[3],
        "created_by": row[4],
        "assigned_to": row[5],
        "description": row[6],
        "category": row[7],
    }

def _get_watchers(cursor, ticket_id):
    # returns list of dicts: [{id, full_name, email}]
    cursor.execute("""
        SELECT u.id, u.full_name, u.email
        FROM users u
        JOIN ticket_watchers tw ON u.id = tw.user_id
        WHERE tw.ticket_id = ?
    """, (ticket_id,))
    out = []
    for r in cursor.fetchall():
        out.append({"id": r[0], "full_name": r[1], "email": r[2]})
    return out

def _get_watchers_emails(cursor, ticket_id):
    cursor.execute("""
        SELECT u.email
        FROM users u
        JOIN ticket_watchers tw ON u.id = tw.user_id
        WHERE tw.ticket_id = ? AND u.email IS NOT NULL AND u.email != ''
    """, (ticket_id,))
    return [r[0] for r in cursor.fetchall()]

# ---------- Recipient building ----------

def _build_recipients(ticket_id, actor_user_id=None):
    
    print(f"[debug][notifications:_build_recipients] ticket_id={ticket_id} actor={actor_user_id}")
    """
    Returns a sorted list of unique recipient emails for a ticket.
    Members are: creator, assigned_to (unless 'IT' special), watchers.
    If assigned_to == 'IT' and NOTIFY_IT_ADMINS_IF_IT is True, add admin emails.
    Optionally exclude actor's email based on EXCLUDE_ACTOR.
    """
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    ticket = _get_ticket_core(cursor, ticket_id)
    if not ticket:
        conn.close()
        return []

    recipients = set()

    # Creator
    creator_email = _get_user_email(cursor, ticket["created_by"])
    if creator_email:
        recipients.add(creator_email)
        
    IT_MAILBOX = os.getenv("IT_MAILBOX", "").strip()
    # Assigned
    assigned = ticket["assigned_to"]
    if isinstance(assigned, str) and assigned.strip().upper() == "IT":
        if IT_MAILBOX:
            recipients.add(IT_MAILBOX)
        elif NOTIFY_IT_ADMINS_IF_IT:
            recipients.update(_get_admin_emails(cursor))

    # Watchers
    for w_email in _get_watchers_emails(cursor, ticket_id):
        if w_email:
            recipients.add(w_email)

    # Exclude actor
    if EXCLUDE_ACTOR and actor_user_id:
        actor_email = _get_user_email(cursor, actor_user_id)
        if actor_email in recipients:
            recipients.remove(actor_email)

    conn.close()
    return sorted(recipients)

# ---------- Formatting helpers ----------

def _ticket_url(ticket_id):
    return f"{APP_BASE_URL}/ticket/{ticket_id}"

def _subject(ticket, change_label):
    change_part = change_label.strip()
    title = ticket.get("title") or "(no title)"
    return f"[Ticket #{ticket['id']}] {change_part} | {title}"

def _sanitize(text):
    if text is None:
        return ""
    return str(text).strip()

def _limit(text, n=5000):
    t = _sanitize(text)
    return t if len(t) <= n else t[:n] + "…"

def _render_people_line(label, name_or_label, email=None):
    if not name_or_label and not email:
        return f"<li><b>{label}:</b> -</li>"
    if name_or_label and email:
        return f"<li><b>{label}:</b> {name_or_label} &lt;{email}&gt;</li>"
    if name_or_label:
        return f"<li><b>{label}:</b> {name_or_label}</li>"
    return f"<li><b>{label}:</b> {email}</li>"

def _compose_details_block(cursor, ticket, recipients_preview=None):
    # Creator
    creator_name = _get_user_name(cursor, ticket["created_by"]) or "-"
    creator_email = _get_user_email(cursor, ticket["created_by"])

    # Assignee
    assigned = ticket["assigned_to"]
    if isinstance(assigned, str) and assigned.strip().upper() == "IT":
        assignee_name = "IT (admins)"
        assignee_email = None
    else:
        assignee_name = _get_user_name(cursor, assigned) or "-"
        assignee_email = _get_user_email(cursor, assigned)

    # Watchers
    watchers = _get_watchers(cursor, ticket["id"])
    watchers_line = ", ".join(
        [f"{_sanitize(w['full_name'])} <{_sanitize(w['email'])}>" if w.get("email") else _sanitize(w["full_name"] or "-")
         for w in watchers]
    ) or "-"

    description = _limit(ticket.get("description") or "")

    # Recipients (optional line for clarity when debugging who receives)
    recipients_html = ""
    if recipients_preview:
        recipients_html = f"""
        <li><b>Recipients:</b> {", ".join(recipients_preview)}</li>
        """

    return f"""
    <ul style="padding-left:18px;margin:10px 0;">
      <li><b>ID:</b> {ticket['id']}</li>
      <li><b>Title:</b> {ticket['title']}</li>
      <li><b>Category:</b> {ticket['category'] or '-'}</li>
      <li><b>Priority:</b> {ticket['priority']}</li>
      <li><b>Status:</b> {ticket['status']}</li>
      { _render_people_line("Creator", creator_name, creator_email) }
      { _render_people_line("Assignee", assignee_name, assignee_email) }
      <li><b>Watchers:</b> {watchers_line}</li>
      {recipients_html}
      <li><b>Description:</b><br/>
        <div style="white-space:pre-wrap;margin-top:6px;color:#222;">{description or "(no description)"} </div>
      </li>
    </ul>
    """

def _wrap_email_html(ticket_id, change_label, change_message_html, details_html, cta_label="Open Ticket"):
    url = _ticket_url(ticket_id)
    return f"""
    <div style="font-family: Arial, sans-serif; line-height:1.5; color:#1a1a1a;">
      <div style="margin-bottom:14px;">
        <a href="{url}" 
           style="display:inline-block;background:#2563eb;color:#fff;text-decoration:none;padding:10px 14px;border-radius:6px;">
           {cta_label}
        </a>
      </div>

      <div style="margin:6px 0 12px 0;">
        <span style="display:inline-block;padding:4px 8px;background:#eef2ff;color:#3730a3;border-radius:6px;font-weight:600;">
          {change_label}
        </span>
      </div>

      <div style="margin:8px 0 10px 0;">
        {change_message_html}
      </div>

      <hr style="border:none;border-top:1px solid #e5e7eb;margin:16px 0;" />

      <div>
        <h3 style="margin:0 0 8px 0;color:#111827;">Ticket details</h3>
        {details_html}
      </div>

      <div style="margin-top:14px;font-size:12px;color:#6b7280;">
        <div>Direct link: <a href="{url}">{url}</a></div>
      </div>
    </div>
    """

def _notify(ticket_id, change_label, subject_text, change_message_html, actor_user_id=None):
    # Build recipients first so we can optionally render them into details block
    recipients = _build_recipients(ticket_id, actor_user_id=actor_user_id)
    if not recipients:
        return

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    ticket = _get_ticket_core(cursor, ticket_id)
    if not ticket:
        conn.close()
        return

    # Compute subject BEFORE any print/log usage
    subject = _subject(ticket, subject_text)

    details_html = _compose_details_block(cursor, ticket, recipients_preview=None)  # keep recipients hidden for end users
    conn.close()

    # Compose full HTML
    html_body = _wrap_email_html(
        ticket_id=ticket_id,
        change_label=change_label,
        change_message_html=change_message_html,
        details_html=details_html,
        cta_label="Open Ticket"
    )

    # Safe debug print now that subject exists
    print(f"[debug][notifications] ticket_id={ticket_id} recipients={recipients} subject={subject}")

    # send_email(recipients, subject, html_body)

# ---------- Public notification helpers ----------

def notify_on_ticket_created(ticket_id, actor_user_id):
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    ticket = _get_ticket_core(cursor, ticket_id)
    conn.close()
    if not ticket:
        return

    change_label = "New Ticket"
    subject_text = "New"
    change_message_html = f"""
      <div>A new ticket has been created.</div>
    """
    _notify(ticket_id, change_label, subject_text, change_message_html, actor_user_id)

def notify_on_status_change(ticket_id, old_status, new_status, actor_user_id):
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    ticket = _get_ticket_core(cursor, ticket_id)
    conn.close()
    if not ticket:
        return

    change_label = "Status Updated"
    subject_text = "Updated"
    change_message_html = f"""
      <div>Status changed from <b>{_sanitize(old_status)}</b> to <b>{_sanitize(new_status)}</b>.</div>
    """
    _notify(ticket_id, change_label, subject_text, change_message_html, actor_user_id)

def notify_on_comment(ticket_id, actor_user_id, comment_preview):
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    ticket = _get_ticket_core(cursor, ticket_id)
    cursor.execute("SELECT full_name FROM users WHERE id=?", (actor_user_id,))
    row = cursor.fetchone()
    actor_name = row[0] if row else "User"
    conn.close()
    if not ticket:
        return

    safe_preview = _sanitize(comment_preview)
    if len(safe_preview) > 600:
        safe_preview = safe_preview[:600] + "…"

    change_label = "New Comment"
    subject_text = "Updated"
    change_message_html = f"""
      <div><b>{_sanitize(actor_name)}</b> added a comment:</div>
      <blockquote style="border-left:3px solid #e5e7eb;padding-left:10px;color:#374151;margin:8px 0;">
        {safe_preview or "(no text)"}
      </blockquote>
    """
    _notify(ticket_id, change_label, subject_text, change_message_html, actor_user_id)

def notify_on_attachment(ticket_id, actor_user_id, filename):
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    ticket = _get_ticket_core(cursor, ticket_id)
    conn.close()
    if not ticket:
        return

    change_label = "Attachment Added"
    subject_text = "Updated"
    change_message_html = f"""
      <div>New attachment added: <b>{_sanitize(filename)}</b>.</div>
    """
    _notify(ticket_id, change_label, subject_text, change_message_html, actor_user_id)

def notify_on_assigned(ticket_id, actor_user_id, assigned_to_id):
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    ticket = _get_ticket_core(cursor, ticket_id)

    assigned_label = None
    if isinstance(assigned_to_id, str) and assigned_to_id.strip().upper() == "IT":
        assigned_label = "IT (admins)"
    elif assigned_to_id:
        cursor.execute("SELECT full_name FROM users WHERE id=?", (assigned_to_id,))
        row = cursor.fetchone()
        assigned_label = row[0] if row else f"#{assigned_to_id}"
    conn.close()
    if not ticket:
        return

    change_label = "Assigned"
    subject_text = "Updated"
    change_message_html = f"""
      <div>Ticket assigned to <b>{_sanitize(assigned_label or '-')}</b>.</div>
    """
    _notify(ticket_id, change_label, subject_text, change_message_html, actor_user_id)

def notify_on_reassigned(ticket_id, actor_user_id, new_assigned_to_id):
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    ticket = _get_ticket_core(cursor, ticket_id)

    assigned_label = None
    if isinstance(new_assigned_to_id, str) and str(new_assigned_to_id).strip().upper() == "IT":
        assigned_label = "IT (admins)"
    elif new_assigned_to_id:
        cursor.execute("SELECT full_name FROM users WHERE id=?", (new_assigned_to_id,))
        row = cursor.fetchone()
        assigned_label = row[0] if row else f"#{new_assigned_to_id}"
    conn.close()
    if not ticket:
        return

    change_label = "Reassigned"
    subject_text = "Updated"
    change_message_html = f"""
      <div>Ticket reassigned to <b>{_sanitize(assigned_label or '-')}</b>.</div>
    """
    _notify(ticket_id, change_label, subject_text, change_message_html, actor_user_id)

def notify_on_closed(ticket_id, actor_user_id):
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    ticket = _get_ticket_core(cursor, ticket_id)
    conn.close()
    if not ticket:
        return

    change_label = "Closed"
    subject_text = "Closed"
    change_message_html = f"""
      <div>The ticket has been closed.</div>
    """
    _notify(ticket_id, change_label, subject_text, change_message_html, actor_user_id)

def notify_on_watchers_added(ticket_id, actor_user_id, watcher_user_ids):
    if not watcher_user_ids:
        return

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    ticket = _get_ticket_core(cursor, ticket_id)
    if not ticket:
        conn.close()
        return

    # Collect only newly-added watcher emails (and show them by name in body)
    emails = []
    names_preview = []
    actor_email = None

    if EXCLUDE_ACTOR and actor_user_id:
        cursor.execute("SELECT email FROM users WHERE id=?", (actor_user_id,))
        row = cursor.fetchone()
        actor_email = row[0] if row and row[0] else None

    for uid in watcher_user_ids:
        cursor.execute("SELECT full_name, email FROM users WHERE id=?", (uid,))
        row = cursor.fetchone()
        if not row:
            continue
        full_name, email = row[0], row[1]
        names_preview.append(_sanitize(full_name) or f"#{uid}")
        if email and (not EXCLUDE_ACTOR or email != actor_email):
            emails.append(email)

    conn.close()

    recipients = sorted(set([e for e in emails if e]))
    if not recipients:
        return

    # Message mentions who got added as watchers.
    change_label = "Watchers Updated"
    subject_text = "Updated"
    names_html = ", ".join(names_preview) if names_preview else "(unknown)"
    change_message_html = f"""
      <div>New watchers added: <b>{names_html}</b>.</div>
    """

    # We send only to newly added watchers, so they know they were added.
    details_html_db = Database()
    conn = details_html_db.get_connection()
    cursor = conn.cursor()
    details_html = _compose_details_block(cursor, ticket, recipients_preview=None)
    conn.close()

    html_body = _wrap_email_html(
        ticket_id=ticket_id,
        change_label=change_label,
        change_message_html=change_message_html,
        details_html=details_html,
        cta_label="Open Ticket"
    )
    subject = _subject(ticket, subject_text)
    # send_email(recipients, subject, html_body)