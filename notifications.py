""" Notification system with async email delivery and beautiful HTML templates.
Supports background email sending for improved performance.
"""
import os
import logging
from database import Database
from threading import Thread
from functools import wraps
from datetime import datetime

# Configuration from environment
def _bool_env(name, default=False):
    """Parse boolean environment variable"""
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() == "true"

EXCLUDE_ACTOR = _bool_env("EXCLUDE_ACTOR_FROM_NOTIFICATIONS", True)
NOTIFY_IT_ADMINS_IF_IT = _bool_env("NOTIFY_IT_ADMINS_WHEN_ASSIGNED_TO_IT", True)
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5000")
IT_MAILBOX = os.getenv("IT_MAILBOX", "").strip()
ENABLE_ASYNC_EMAIL = _bool_env("ENABLE_ASYNC_EMAIL", True)

# ==================== ASYNC EMAIL DECORATOR ====================
def async_send(f):
    """ Decorator to send emails asynchronously in background thread.
    Prevents blocking the main request thread.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if ENABLE_ASYNC_EMAIL:
            thread = Thread(target=f, args=args, kwargs=kwargs)
            thread.daemon = True
            thread.start()
            logging.info(f"Email queued for async delivery: {f.__name__}")
        else:
            # Synchronous mode for debugging
            f(*args, **kwargs)
    return wrapper

# ==================== DATABASE HELPERS ====================
def _get_user_email(cursor, user_id):
    """Get user email by ID"""
    if not user_id or isinstance(user_id, str):
        return None
    cursor.execute("SELECT email FROM users WHERE id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row and row[0] else None

def _get_user_name(cursor, user_id):
    """Get user full name by ID"""
    if not user_id:
        return None
    if isinstance(user_id, str):
        return str(user_id).strip()
    cursor.execute("SELECT full_name FROM users WHERE id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else None

def _get_admin_emails(cursor):
    """Get all admin emails"""
    cursor.execute("""
        SELECT email FROM users
        WHERE role='admin' AND email IS NOT NULL AND email != ''
    """)
    return [r[0] for r in cursor.fetchall()]

def _get_ticket_core(cursor, ticket_id):
    """Get core ticket information"""
    cursor.execute("""
        SELECT t.id, t.title, t.priority, t.status, t.created_by, t.assigned_to,
               t.description, c.name as category_name, t.created_at, t.updated_at
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
        "created_at": row[8],
        "updated_at": row[9],
    }

def _get_watchers_emails(cursor, ticket_id):
    """Get all watcher emails for a ticket"""
    cursor.execute("""
        SELECT u.email FROM users u
        JOIN ticket_watchers tw ON u.id = tw.user_id
        WHERE tw.ticket_id = ? AND u.email IS NOT NULL AND u.email != ''
    """, (ticket_id,))
    return [r[0] for r in cursor.fetchall()]

def _get_watchers_list(cursor, ticket_id):
    """Get list of watcher user IDs for a ticket"""
    cursor.execute("""
        SELECT user_id FROM ticket_watchers
        WHERE ticket_id = ?
    """, (ticket_id,))
    return [r[0] for r in cursor.fetchall()]

def _build_recipient_reasons(cursor, recipient_email, ticket_id):
    """Build list of reasons why recipient receives this email"""
    reasons = []
    ticket = _get_ticket_core(cursor, ticket_id)
    if not ticket:
        return reasons

    cursor.execute("SELECT id FROM users WHERE email=?", (recipient_email,))
    user_row = cursor.fetchone()
    if not user_row:
        return reasons

    user_id = user_row[0]

    if ticket["created_by"] == user_id:
        reasons.append("the ticket creator")
    if ticket["assigned_to"] == user_id:
        reasons.append("the assigned user")

    watchers = _get_watchers_list(cursor, ticket_id)
    if user_id in watchers:
        reasons.append("a watcher")

    return reasons

def _get_change_icon(change_type):
    """Get emoji icon for change type"""
    icons = {
        'status_change': '🔄',
        'new_comment': '💬',
        'assigned': '👤',
        'reassigned': '🔄',
        'closed': '✅',
        'attachment': '📎',
        'creation': '🆕',
        'watchers_added': '👁️'
    }
    return icons.get(change_type, '📌')

def _format_timestamp(timestamp_str):
    """Format timestamp to readable format: YYYY-MM-DD HH:MM:SS"""
    if not timestamp_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return timestamp_str

# ==================== RECIPIENT BUILDING ====================
def _build_recipients(ticket_id, actor_user_id=None):
    """
    Build list of unique recipient emails for ticket notifications.
    Args:
        ticket_id: Ticket ID
        actor_user_id: User who performed the action (excluded if EXCLUDE_ACTOR=True)
    Returns:
        Sorted list of unique email addresses
    """
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        ticket = _get_ticket_core(cursor, ticket_id)
        if not ticket:
            return []
        recipients = set()
        # Add creator
        creator_email = _get_user_email(cursor, ticket["created_by"])
        if creator_email:
            recipients.add(creator_email)
        # Add assigned user or IT admins
        assigned = ticket["assigned_to"]
        if isinstance(assigned, str) and assigned.strip().upper() == "IT":
            if IT_MAILBOX:
                recipients.add(IT_MAILBOX)
            elif NOTIFY_IT_ADMINS_IF_IT:
                recipients.update(_get_admin_emails(cursor))
        elif assigned:
            assigned_email = _get_user_email(cursor, assigned)
            if assigned_email:
                recipients.add(assigned_email)
        # Add watchers
        recipients.update(_get_watchers_emails(cursor, ticket_id))
        # Exclude actor if configured
        if EXCLUDE_ACTOR and actor_user_id:
            actor_email = _get_user_email(cursor, actor_user_id)
            if actor_email in recipients:
                recipients.remove(actor_email)
        return sorted(recipients)
    finally:
        conn.close()

# ==================== HTML FORMATTING ====================
def _ticket_url(ticket_id):
    """Generate full URL to ticket"""
    return f"{APP_BASE_URL}/ticket/{ticket_id}"

def _priority_badge(priority):
    """Generate HTML badge for priority"""
    colors = {
        'high': '#dc3545',
        'medium': '#ffc107',
        'low': '#28a745'
    }
    color = colors.get(priority, '#6c757d')
    return f'<span style="background-color: {color}; color: white; padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: 500;">{priority.upper()}</span>'

def _status_badge(status):
    """Generate HTML badge for status"""
    colors = {
        'new': '#17a2b8',
        'assigned': '#007bff',
        'in_progress': '#ffc107',
        'awaiting_confirmation': '#fd7e14',
        'closed': '#28a745'
    }
    color = colors.get(status, '#6c757d')
    return f'<span style="background-color: {color}; color: white; padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: 500;">{status.replace("_", " ").title()}</span>'

def _build_html_email(ticket, change_label, change_message_html, change_icon='📌', actor_name=None, recipient_email=None):
    """
    Build clean, professional HTML email template.

    Args:
        ticket: Ticket dictionary (may include 'creator_name', 'assigned_name', 'updated_at')
        change_label: Label describing the change
        change_message_html: HTML content describing the change
        change_icon: Emoji icon for change type
        actor_name: Name of user who made the change
        recipient_email: Email of recipient (for footer reasons)

    Returns:
        Complete HTML email string
    """
    ticket_url = _ticket_url(ticket['id'])
    priority_colors = {
        'high': '#dc3545',
        'medium': '#ffc107',
        'low': '#28a745'
    }
    priority_color = priority_colors.get(ticket.get('priority', 'medium'), '#6c757d')
    status_colors = {
        'new': '#17a2b8',
        'assigned': '#0d6efd',
        'in_progress': '#ffc107',
        'awaiting_confirmation': '#fd7e14',
        'closed': '#28a745'
    }
    status_color = status_colors.get(ticket.get('status', 'new'), '#6c757d')
    status_text = ticket.get('status', 'new').replace('_', ' ').title()
    creator_name = ticket.get('creator_name', 'Unknown')
    assigned_name = ticket.get('assigned_name', 'IT')
    changed_by_name = actor_name or 'System'
    last_updated = _format_timestamp(ticket.get('updated_at'))

    # Short description preview
    description = ticket.get('description') or ''
    desc_preview = (description[:200] + ("..." if len(description) > 200 else "")) if description else "No description provided"

    # Recipient reasons footer
    recipient_reasons_html = ""
    if recipient_email and ticket.get('_cursor'):
        cursor = ticket['_cursor']
        reasons = _build_recipient_reasons(cursor, recipient_email, ticket['id'])
        if reasons:
            reasons_list = ", ".join(reasons)
            recipient_reasons_html = f"""
            <tr>
                <td style="background-color: #e9ecef; padding: 15px 30px; text-align: center; border-top: 1px solid #dee2e6;">
                    <p style="margin: 0; font-size: 12px; color: #495057;">
                        You're receiving this email because you are: <strong>{reasons_list}</strong>
                    </p>
                </td>
            </tr>
            """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ticket #{ticket['id']} - {change_icon} {change_label}</title>
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; background-color: #f5f5f5;">

    <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #f5f5f5; padding: 20px 0;">
        <tr>
            <td align="center">
                <table width="600" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08);">
                    <tr>
                        <td style="background-color: #2c3e50; padding: 20px 30px; text-align: center;">
                            <h1 style="margin: 0; color: #ffffff; font-size: 20px; font-weight: 600;">Ticket System</h1>
                            <p style="margin: 5px 0 0 0; color: #ecf0f1; font-size: 13px;">{change_icon} {change_label}</p>
                        </td>
                    </tr>
                    <!-- Change Summary -->
                    <tr>
                        <td style="padding: 20px 30px 0 30px;">
                            <div style="background-color: #e7f3ff; border-left: 4px solid #0d6efd; padding: 15px; margin-bottom: 15px; border-radius: 4px;">
                                {change_message_html}
                            </div>
                        </td>
                    </tr>
                    <!-- Ticket Info Card -->
                    <tr>
                        <td style="padding: 0 30px 30px 30px;">
                            <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #f8f9fa; border-radius: 6px; margin-bottom: 20px;">
                                <tr>
                                    <td style="padding: 20px;">
                                        <table width="100%" cellpadding="0" cellspacing="0">
                                            <tr>
                                                <td style="padding: 5px 0; font-size: 14px; color: #6c757d;">
                                                    <strong style="color: #2c3e50;">Ticket #:</strong>
                                                </td>
                                                <td style="padding: 5px 0; font-size: 14px; color: #2c3e50; text-align: right;">#{ticket['id']}</td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 5px 0; font-size: 14px; color: #6c757d;">
                                                    <strong style="color: #2c3e50;">Title:</strong>
                                                </td>
                                                <td style="padding: 5px 0; font-size: 14px; color: #2c3e50; text-align: right;">{ticket.get('title', 'N/A')}</td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 5px 0; font-size: 14px; color: #6c757d;">
                                                    <strong style="color: #2c3e50;">Created by:</strong>
                                                </td>
                                                <td style="padding: 5px 0; font-size: 14px; color: #2c3e50; text-align: right;">{creator_name}</td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 5px 0; font-size: 14px; color: #6c757d;">
                                                    <strong style="color: #2c3e50;">Assigned to:</strong>
                                                </td>
                                                <td style="padding: 5px 0; font-size: 14px; color: #2c3e50; text-align: right;">{assigned_name}</td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 5px 0; font-size: 14px; color: #6c757d;">
                                                    <strong style="color: #2c3e50;">Changed by:</strong>
                                                </td>
                                                <td style="padding: 5px 0; font-size: 14px; color: #2c3e50; text-align: right;">{changed_by_name}</td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 5px 0; font-size: 14px; color: #6c757d;">
                                                    <strong style="color: #2c3e50;">Last updated at:</strong>
                                                </td>
                                                <td style="padding: 5px 0; font-size: 14px; color: #2c3e50; text-align: right;">{last_updated}</td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 8px 0; font-size: 14px; color: #6c757d;">
                                                    <strong style="color: #2c3e50;">Priority:</strong>
                                                </td>
                                                <td style="padding: 8px 0; text-align: right;">
                                                    <span style="background-color: {priority_color}; color: white; padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: 500;">{ticket.get('priority', 'medium').upper()}</span>
                                                </td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 8px 0; font-size: 14px; color: #6c757d;">
                                                    <strong style="color: #2c3e50;">Status:</strong>
                                                </td>
                                                <td style="padding: 8px 0; text-align: right;">
                                                    <span style="background-color: {status_color}; color: white; padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: 500;">{status_text}</span>
                                                </td>
                                            </tr>
                                            {f'''<tr>
                                                <td style="padding: 5px 0; font-size: 14px; color: #6c757d;">
                                                    <strong style="color: #2c3e50;">Category:</strong>
                                                </td>
                                                <td style="padding: 5px 0; font-size: 14px; color: #2c3e50; text-align: right;">{ticket.get("category", "N/A")}</td>
                                            </tr>''' if ticket.get('category') else ''}
                                        </table>
                                    </td>
                                </tr>
                            </table>
                            <!-- Description -->
                            {f'''<div style="background-color: #ffffff; border-left: 4px solid #2c3e50; padding: 15px; margin-bottom: 20px; border-radius: 4px;">
                                <p style="margin: 0 0 5px 0; font-size: 12px; color: #6c757d; text-transform: uppercase; font-weight: 600;">Description:</p>
                                <p style="margin: 0; font-size: 14px; color: #495057; line-height: 1.6;">{desc_preview}</p>
                            </div>''' if description else ''}
                            <!-- Action Button -->
                            <table width="100%" cellpadding="0" cellspacing="0">
                                <tr>
                                    <td align="center" style="padding: 10px 0;">
                                        <a href="{ticket_url}" style="display: inline-block; background-color: #0d6efd; color: white; padding: 12px 30px; text-decoration: none; border-radius: 6px; font-weight: 500; font-size: 14px;">View Ticket Details</a>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    {recipient_reasons_html}
                    <tr>
                        <td style="background-color: #f8f9fa; padding: 20px 30px; text-align: center; border-top: 1px solid #dee2e6;">
                            <p style="margin: 0 0 5px 0; font-size: 12px; color: #6c757d;">This is an automated notification from the Ticket System</p>
                            <p style="margin: 0; font-size: 12px;"><a href="{ticket_url}" style="color: #0d6efd; text-decoration: none;">Direct link to ticket #{ticket['id']}</a></p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""

# ==================== MAIN NOTIFY FUNCTION ====================
@async_send
def _notify(ticket_id, change_label, subject_text, change_message_html, actor_user_id=None, change_type='status_change'):
    try:
        recipients = _build_recipients(ticket_id, actor_user_id)
        if not recipients:
            logging.info(f"No recipients for ticket #{ticket_id} notification")
            return

        # Get ticket info and user details
        db = Database()
        conn = db.get_connection()
        cursor = conn.cursor()
        try:
            ticket = _get_ticket_core(cursor, ticket_id)
            if not ticket:
                logging.error(f"Ticket #{ticket_id} not found for notification")
                return

            creator_name = _get_user_name(cursor, ticket.get('created_by')) or 'Unknown'
            assigned = ticket.get('assigned_to')
            if isinstance(assigned, str) and assigned.strip().upper() == 'IT':
                assigned_name = 'IT'
            else:
                assigned_name = _get_user_name(cursor, assigned) or 'IT'
            actor_name = _get_user_name(cursor, actor_user_id) or 'System'

            ticket['creator_name'] = creator_name
            ticket['assigned_name'] = assigned_name
            ticket['_cursor'] = cursor

            change_icon = _get_change_icon(change_type)

            from mailer import send_email
            for recipient in recipients:
                # Proveri da li je korisnik mute-ovao ovaj tiket
                cursor.execute("SELECT id FROM users WHERE email=?", (recipient,))
                user_row = cursor.fetchone()
                if user_row:
                    user_id = user_row[0]
                    # Proveri da li je muted
                    cursor.execute("""
                        SELECT 1 FROM ticket_muted_users
                        WHERE ticket_id=? AND user_id=?
                    """, (ticket_id, user_id))
                    if cursor.fetchone():
                        continue  # Skip - korisnik je mute-ovao ovaj tiket
                subject = f"[Ticket #{ticket['id']}] {subject_text} | {ticket.get('title', 'N/A')}"
                html_body = _build_html_email(
                    ticket,
                    change_label,
                    change_message_html,
                    change_icon=change_icon,
                    actor_name=actor_name,
                    recipient_email=recipient
                )
                send_email([recipient], subject, html_body)

            logging.info(f"Email sent for ticket #{ticket_id} to {len(recipients)} recipient(s)")

        finally:
            conn.close()

    except Exception as e:
        logging.error(f"Error sending notification for ticket #{ticket_id}: {str(e)}")

# ==================== PUBLIC NOTIFICATION FUNCTIONS ====================
def notify_on_ticket_created(ticket_id, actor_user_id):
    change_label = "New Ticket Created"
    subject_text = "New Ticket"
    change_message_html = """
        <p style="margin: 0; color: #333; font-size: 15px;">
            <strong>🎉 A new ticket has been created and assigned to you.</strong>
        </p>
        <p style="margin: 10px 0 0 0; color: #666; font-size: 14px;">
            Please review the details and take appropriate action.
        </p>
    """
    _notify(ticket_id, change_label, subject_text, change_message_html, actor_user_id, change_type='creation')

def notify_on_status_change(ticket_id, old_status, new_status, actor_user_id):
    change_label = f"Status Changed: {old_status} → {new_status}"
    subject_text = "Status Updated"
    change_message_html = f"""
        <p style="margin: 0; color: #333; font-size: 15px;">
            <strong>🔄 Ticket status has been updated:</strong>
        </p>
        <p style="margin: 10px 0 0 0; color: #666; font-size: 14px;">
            {old_status.replace('_', ' ').title()} → {new_status.replace('_', ' ').title()}
        </p>
    """
    _notify(ticket_id, change_label, subject_text, change_message_html, actor_user_id, change_type='status_change')

def notify_on_comment(ticket_id, actor_user_id, comment_preview):
    safe_preview = comment_preview[:200] + "..." if len(comment_preview) > 200 else comment_preview
    change_label = "New Comment Added"
    subject_text = "New Comment"
    change_message_html = f"""
        <p style="margin: 0; color: #333; font-size: 15px;">
            <strong>💬 A new comment has been added to this ticket:</strong>
        </p>
        <p style="margin: 10px 0 0 0; color: #666; font-size: 14px; font-style: italic;">
            "{safe_preview}"
        </p>
    """
    _notify(ticket_id, change_label, subject_text, change_message_html, actor_user_id, change_type='new_comment')

def notify_on_attachment(ticket_id, actor_user_id, filename):
    change_label = "New Attachment Added"
    subject_text = "New Attachment"
    change_message_html = f"""
        <p style="margin: 0; color: #333; font-size: 15px;">
            <strong>📎 A new file has been attached to this ticket:</strong>
        </p>
        <p style="margin: 10px 0 0 0; color: #666; font-size: 14px;">
            {filename}
        </p>
    """
    _notify(ticket_id, change_label, subject_text, change_message_html, actor_user_id, change_type='attachment')

def notify_on_assigned(ticket_id, actor_user_id, assigned_to_id):
    change_label = "Ticket Assigned"
    subject_text = "Assigned"
    change_message_html = """
        <p style="margin: 0; color: #333; font-size: 15px;">
            <strong>👤 This ticket has been assigned to you.</strong>
        </p>
        <p style="margin: 10px 0 0 0; color: #666; font-size: 14px;">
            Please review and begin working on it at your earliest convenience.
        </p>
    """
    _notify(ticket_id, change_label, subject_text, change_message_html, actor_user_id, change_type='assigned')

def notify_on_reassigned(ticket_id, actor_user_id, new_assigned_to_id):
    change_label = "Ticket Reassigned"
    subject_text = "Reassigned"
    change_message_html = """
        <p style="margin: 0; color: #333; font-size: 15px;">
            <strong>🔄 This ticket has been reassigned to you.</strong>
        </p>
        <p style="margin: 10px 0 0 0; color: #666; font-size: 14px;">
            Please review the ticket history and continue working on it.
        </p>
    """
    _notify(ticket_id, change_label, subject_text, change_message_html, actor_user_id, change_type='reassigned')

def notify_on_closed(ticket_id, actor_user_id):
    change_label = "Ticket Closed"
    subject_text = "Closed"
    change_message_html = """
        <p style="margin: 0; color: #333; font-size: 15px;">
            <strong>✅ This ticket has been marked as closed.</strong>
        </p>
        <p style="margin: 10px 0 0 0; color: #666; font-size: 14px;">
            If you believe this was closed in error, please reopen it or contact support.
        </p>
    """
    _notify(ticket_id, change_label, subject_text, change_message_html, actor_user_id, change_type='closed')

def notify_on_watchers_added(ticket_id, actor_user_id, watcher_user_ids):
    change_label = "Added as Watcher"
    subject_text = "Watching"
    change_message_html = """
        <p style="margin: 0; color: #333; font-size: 15px;">
            <strong>👁️ You have been added as a watcher to this ticket.</strong>
        </p>
        <p style="margin: 10px 0 0 0; color: #666; font-size: 14px;">
            You will receive notifications about all future updates to this ticket.
        </p>
    """
    _notify(ticket_id, change_label, subject_text, change_message_html, actor_user_id, change_type='watchers_added')
