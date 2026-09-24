import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_wtf.csrf import CSRFProtect
from openpyxl import load_workbook
from werkzeug.exceptions import HTTPException

load_dotenv()

DEMO_MODE = os.getenv("DEMO_MODE", "false").strip().lower() == "true"
DEMO_USERNAME = os.getenv("DEMO_USERNAME", "demo_admin")
DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "TicketXDemo!2026")

if DEMO_MODE:
    os.environ["DB_PATH"] = os.getenv("DEMO_DB_PATH", "/tmp/ticketx-demo.db")
    os.environ["UPLOAD_FOLDER"] = os.getenv("DEMO_UPLOAD_FOLDER", "/tmp/ticketx-uploads")
    os.environ["DISABLE_INITIAL_ADMIN_CREATION"] = "true"
    os.environ["ENABLE_ASYNC_EMAIL"] = "false"

from auth import admin_required, get_redirect_target, login_required  # noqa: E402
from database import Database  # noqa: E402
from demo_data import ensure_demo_database  # noqa: E402
from models import CategoryModel, TicketModel, UserModel  # noqa: E402
from security import hash_password  # noqa: E402

if DEMO_MODE:
    ensure_demo_database(os.environ["DB_PATH"], DEMO_USERNAME, DEMO_PASSWORD)

logging.basicConfig(level=logging.INFO)
logging.getLogger('werkzeug').setLevel(logging.ERROR)
logger = logging.getLogger(__name__)

app = Flask(__name__)

app.secret_key = os.getenv("FLASK_SECRET_KEY")
if not app.secret_key or app.secret_key == "change-me-generate-a-random-64-hex-string":
    raise RuntimeError(
        "FLASK_SECRET_KEY is required. Copy .env.example to .env and generate a random key."
    )

# CSRF protection for every state-changing request.
csrf = CSRFProtect(app)


@app.teardown_appcontext
def _close_db_connections(exc):
    """Sigurno zatvori sve DB konekcije otvorene tokom zahteva.

    Mreza zastite protiv curenja konekcija: radi cak i kada ruta zaboravi
    conn.close() ili kada dodje do izuzetka usred rute.
    """
    from flask import g
    for conn in getattr(g, '_db_connections', []):
        try:
            conn.close()
        except Exception:
            pass

IS_HTTPS = (os.getenv("IS_HTTPS", "false").strip().lower() == "true")

APP_ROOT = Path(__file__).resolve().parent
upload_setting = os.getenv("UPLOAD_FOLDER", "uploads")
upload_path = Path(upload_setting)
if not upload_path.is_absolute():
    upload_path = APP_ROOT / upload_path
app.config['UPLOAD_FOLDER'] = str(upload_path.resolve())

# ==== FILE UPLOAD SECURITY ====
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx', 'txt', 'xlsx', 'xls', 'zip', 'rar'}
ALLOWED_MIME_TYPES = {
    'image/png', 'image/jpeg', 'image/gif', 
    'application/pdf', 
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'text/plain',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-excel',
    'application/zip',
    'application/x-rar-compressed'
}

def log_ticket_activity(ticket_id, user_id, action_type, old_value=None, new_value=None, details=None):
    """Log ticket activity for audit trail"""
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
        INSERT INTO ticket_activity_log (ticket_id, user_id, action_type, old_value, new_value, details)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (ticket_id, user_id, action_type, old_value, new_value, details))
        conn.commit()
    except Exception as e:
        print(f"Error logging activity: {e}")
    finally:
        conn.close()

VALID_PRIORITIES = {'low', 'medium', 'high'}
VALID_STATUSES = {'new', 'assigned', 'in_progress', 'awaiting_confirmation', 'closed'}
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 5
_login_failures = {}
_login_failures_lock = threading.Lock()


def _login_key(username):
    return (request.remote_addr or 'unknown', username.strip().lower())


def _is_login_rate_limited(key):
    now = time.monotonic()
    with _login_failures_lock:
        recent = [stamp for stamp in _login_failures.get(key, []) if now - stamp < LOGIN_WINDOW_SECONDS]
        if recent:
            _login_failures[key] = recent
        else:
            _login_failures.pop(key, None)
        return len(recent) >= LOGIN_MAX_FAILURES


def _record_login_failure(key):
    with _login_failures_lock:
        _login_failures.setdefault(key, []).append(time.monotonic())


def _clear_login_failures(key):
    with _login_failures_lock:
        _login_failures.pop(key, None)


def allowed_file(filename, mimetype):
    """Validate an attachment using a conservative extension/MIME allowlist."""
    if '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return ext in ALLOWED_EXTENSIONS and mimetype in ALLOWED_MIME_TYPES
# ==== END FILE UPLOAD SECURITY ====

def get_upload_path():
    """Return the private monthly attachment directory."""
    monthly_path = Path(app.config['UPLOAD_FOLDER']) / datetime.now().strftime('%Y-%m')
    monthly_path.mkdir(parents=True, exist_ok=True)
    return monthly_path


def _normalize_user_id(value):
    if value in (None, '', 'IT'):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_ticket_access(cursor, ticket_id, user_id=None):
    """Load the minimum ticket data required for authorization decisions."""
    cursor.execute(
        """
        SELECT t.id, t.created_by, t.assigned_to, t.is_private, t.status,
               EXISTS(
                   SELECT 1 FROM ticket_watchers tw
                   WHERE tw.ticket_id = t.id AND tw.user_id = ?
               ) AS is_watcher
        FROM tickets t
        WHERE t.id = ?
        """,
        (user_id, ticket_id),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {
        'id': row[0],
        'created_by': row[1],
        'assigned_to': _normalize_user_id(row[2]),
        'is_private': bool(row[3]),
        'status': row[4],
        'is_watcher': bool(row[5]),
    }


def _can_view_ticket(access, user_id, role):
    if not access:
        return False
    return (
        role == 'admin'
        or not access['is_private']
        or access['created_by'] == user_id
        or access['assigned_to'] == user_id
        or access['is_watcher']
    )


def _can_manage_ticket(access, user_id, role):
    if not access:
        return False
    return (
        role == 'admin'
        or access['created_by'] == user_id
        or access['assigned_to'] == user_id
    )


def _can_change_status(access, user_id, role, new_status):
    """Enforce the workflow even when a request bypasses the UI."""
    if not access or new_status not in VALID_STATUSES:
        return False
    if role == 'admin':
        return True
    current = access['status']
    if access['assigned_to'] == user_id:
        return (current, new_status) in {
            ('new', 'in_progress'),
            ('assigned', 'in_progress'),
            ('in_progress', 'awaiting_confirmation'),
        }
    if access['created_by'] == user_id:
        return (current, new_status) == ('awaiting_confirmation', 'closed')
    return False


def _save_attachment(file, ticket_id, user_id, cursor):
    """Validate, privately store and register one attachment."""
    if not file or not file.filename:
        return False
    if not allowed_file(file.filename, file.content_type):
        raise ValueError(f"File type not allowed: {file.filename}")

    suffix = Path(file.filename).suffix.lower()
    stored_name = f"{ticket_id}_{uuid.uuid4().hex}{suffix}"
    destination = get_upload_path() / stored_name
    file.save(destination)
    try:
        stored_path = destination.relative_to(APP_ROOT).as_posix()
    except ValueError:
        stored_path = str(destination)
    cursor.execute(
        """
        INSERT INTO attachments
            (ticket_id, filename, original_filename, file_path, uploaded_by)
        VALUES (?, ?, ?, ?, ?)
        """,
        (ticket_id, stored_name, Path(file.filename).name, stored_path, user_id),
    )
    return True


def _resolve_attachment_path(stored_path):
    """Resolve current and legacy attachment paths without allowing traversal."""
    path = Path(stored_path)
    allowed_roots = [Path(app.config['UPLOAD_FOLDER']).resolve(), (APP_ROOT / 'static' / 'uploads').resolve()]
    candidates = [path.resolve()] if path.is_absolute() else [(APP_ROOT / path).resolve()]
    if not path.is_absolute() and path.parts and path.parts[0].lower() == 'uploads':
        candidates.append((APP_ROOT / 'static' / path).resolve())

    safe_candidates = [
        candidate
        for candidate in candidates
        if any(candidate == root or root in candidate.parents for root in allowed_roots)
    ]
    return next((candidate for candidate in safe_candidates if candidate.exists()), safe_candidates[0] if safe_candidates else None)

app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB

app.config.update(
    SESSION_COOKIE_SECURE=IS_HTTPS,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_NAME="ticketx_session",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=1),
    SESSION_REFRESH_EACH_REQUEST=True,
    MAX_FORM_MEMORY_SIZE=12 * 1024 * 1024,
)


@app.after_request
def add_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' blob: data:; "
        "font-src 'self' https://cdn.jsdelivr.net; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
    )
    if IS_HTTPS:
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    if session.get('user_id'):
        response.headers.setdefault('Cache-Control', 'no-store, private')
    return response

from notifications import (
    notify_on_ticket_created,
    notify_on_status_change,
    notify_on_comment,
    notify_on_attachment,
    notify_on_assigned,
    notify_on_reassigned,
    notify_on_closed,
    notify_on_watchers_added,
)

user_model = UserModel()
ticket_model = TicketModel()
category_model = CategoryModel()


@app.before_request
def restore_public_demo_data():
    """Recreate disposable Vercel demo data if a serverless instance loses /tmp."""
    if DEMO_MODE:
        ensure_demo_database(os.environ["DB_PATH"], DEMO_USERNAME, DEMO_PASSWORD)


@app.before_request
def protect_public_demo():
    """Keep the shared portfolio demo deterministic and safe for every visitor."""
    if not DEMO_MODE or request.method in {"GET", "HEAD", "OPTIONS"}:
        return None
    if request.endpoint in {"login", "logout"}:
        return None

    flash("This public portfolio demo is read-only. Run TicketX locally to test changes.", "info")
    target = request.referrer or url_for("dashboard" if session.get("user_id") else "login")
    return redirect(target, code=303)


@app.before_request
def refresh_authenticated_user():
    """Keep authorization data in the signed session synchronized with the database."""
    user_id = session.get('user_id')
    if not user_id or request.endpoint == 'static':
        return
    user = user_model.get_user_by_id(user_id)
    if not user:
        session.clear()
        return
    session['username'] = user[1]
    session['full_name'] = user[4]
    session['role'] = user[5]
    session['department_id'] = user[6]
    session['is_department_head'] = user[7]

@app.context_processor
def inject_now():
    """Make datetime.now() available in all templates"""
    return {
        'now': datetime.now,
        'datetime': datetime,
        'demo_mode': DEMO_MODE,
        'demo_username': DEMO_USERNAME,
        'demo_password': DEMO_PASSWORD,
    }
# ==== END JINJA2 GLOBALS ====

def _ensure_upload_dir():
    try:
        Path(app.config['UPLOAD_FOLDER']).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

_ensure_upload_dir()
# ==== EXISTING CODE ABOVE (imports, config, etc.) ====

def ensure_initial_admin():
    """
    Create initial admin user if no admin exists in database.
    Runs only once on first startup.
    Credentials are loaded from .env file for security.
    """
    
    if os.getenv('DISABLE_INITIAL_ADMIN_CREATION', 'false').lower() == 'true':
        return
    
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    # Check whether an administrator already exists.
    cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
    admin_count = cursor.fetchone()[0]
    
    if admin_count == 0:
        # No administrator exists; bootstrap one from local configuration.
        admin_username = os.getenv('INITIAL_ADMIN_USERNAME')
        admin_password = os.getenv('INITIAL_ADMIN_PASSWORD')
        admin_fullname = os.getenv('INITIAL_ADMIN_FULLNAME', 'System Administrator')
        admin_email = os.getenv('INITIAL_ADMIN_EMAIL', '')
        
        password_is_placeholder = (
            not admin_password
            or admin_password == 'replace-with-a-strong-unique-password'
            or admin_password.lower().startswith('changeme')
            or len(admin_password) < 12
        )
        if not admin_username or password_is_placeholder:
            logger.warning(
                "No administrator exists. Set INITIAL_ADMIN_USERNAME and a unique "
                "INITIAL_ADMIN_PASSWORD of at least 12 characters."
            )
            conn.close()
            return
        
        # Create initial admin
        hashed_password = hash_password(admin_password)
        
        try:
            cursor.execute("""
            INSERT INTO users (username, password, full_name, email, role, department_id, is_department_head)
            VALUES (?, ?, ?, ?, 'admin', NULL, 0)
            """, (admin_username, hashed_password, admin_fullname, admin_email))
            conn.commit()
            logger.info("Initial administrator created: %s", admin_username)
            logger.warning("Change the initial administrator password after first login.")
        except Exception as e:
            logger.exception("Could not create the initial administrator: %s", e)
    else:
        logger.info("Administrator bootstrap skipped; %d administrator(s) exist.", admin_count)

    conn.close()


# Run bootstrap once at application import, including under WSGI/IIS.
try:
    ensure_initial_admin()
except Exception as e:
    logger.exception("Initial administrator setup failed: %s", e)

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']

        login_key = _login_key(username)
        if _is_login_rate_limited(login_key):
            abort(429, description='Too many failed login attempts. Try again in 15 minutes.')

        user = user_model.authenticate(username, password)

        if user:
            _clear_login_failures(login_key)
            session.clear()
            session.permanent = True
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['full_name'] = user[4]
            session['role'] = user[5]
            session['department_id'] = user[6]
            session['is_department_head'] = user[7]
            session['_fresh'] = True
            session['last_activity'] = time.time()  # DODAJ OVO
            
            flash(f"Welcome, {user[4]}!", 'success')
            
            # Redirect to original page or dashboard
            next_url = get_redirect_target()
            if next_url:
                return redirect(next_url)
            return redirect(url_for('dashboard'))
        else:
            _record_login_failure(login_key)
            flash('Invalid username or password.', 'error')

    return render_template('login.html')

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

def valid_email(email):
    if not email:
        return True
    return re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) is not None

def get_departments():
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM departments ORDER BY name")
    departments = cursor.fetchall()
    conn.close()
    return departments

@app.route('/my_profile', methods=['GET', 'POST'])
@login_required
def my_profile():
    user_id = session.get('user_id')

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT id, username, password, email, full_name, role, department_id, is_department_head
    FROM users
    WHERE id = ?
    """, (user_id,))
    user = cursor.fetchone()

    if not user:
        conn.close()
        flash('User not found.', 'error')
        return redirect(url_for('dashboard'))

    departments = get_departments()

    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip()
        department_id_raw = request.form.get('department_id')
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()

        if not full_name:
            flash('Full name is required.', 'error')
            return render_template('my_profile.html',
                user=user,
                departments=departments)

        if email and not valid_email(email):
            flash('Invalid email format.', 'error')
            return render_template('my_profile.html',
                user=user,
                departments=departments)

        department_id = None
        if department_id_raw and department_id_raw.strip() != '':
            try:
                department_id = int(department_id_raw)
            except ValueError:
                flash('Invalid department.', 'error')
                return render_template('my_profile.html',
                    user=user,
                    departments=departments)

        update_password = False
        hashed_password = user[2]
        if new_password or confirm_password:
            if new_password != confirm_password:
                flash('Passwords do not match.', 'error')
                return render_template('my_profile.html',
                    user=user,
                    departments=departments)
            if len(new_password) < 8:
                flash('Password must be at least 8 characters.', 'error')
                return render_template('my_profile.html',
                    user=user,
                    departments=departments)
            hashed_password = hash_password(new_password)
            update_password = True

        if update_password:
            cursor.execute("""
            UPDATE users
            SET full_name = ?, email = ?, department_id = ?, password = ?
            WHERE id = ?
            """, (full_name, email if email != '' else None, department_id, hashed_password, user_id))
        else:
            cursor.execute("""
            UPDATE users
            SET full_name = ?, email = ?, department_id = ?
            WHERE id = ?
            """, (full_name, email if email != '' else None, department_id, user_id))

        conn.commit()

        session['full_name'] = full_name
        session['department_id'] = department_id

        try:
            log_activity(user_id, 'PROFILE_UPDATED', f'User updated own profile (full_name/email/department/password)')
        except:
            pass

        conn.close()
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('my_profile'))

    conn.close()
    return render_template('my_profile.html',
        user=user,
        departments=departments)

# ==== WHITELIST FOR SORTING (SQL INJECTION PROTECTION) ====
ALLOWED_SORT_OPTIONS = {
    'updated_at_desc': 't.updated_at DESC',
    'updated_at_asc': 't.updated_at ASC',
    'created_at_desc': 't.created_at DESC',
    'created_at_asc': 't.created_at ASC',
    'priority_desc': "CASE t.priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END ASC",
    'priority_asc': "CASE WHEN t.priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END DESC",
    'due_date_desc': 'CASE WHEN t.due_date IS NULL OR t.due_date = "" THEN 0 ELSE 1 END DESC, t.due_date DESC',
    'due_date_asc': 'CASE WHEN t.due_date IS NULL OR t.due_date = "" THEN 0 ELSE 1 END DESC, t.due_date ASC'
}
# ==== END WHITELIST ====


@app.route('/dashboard')
@login_required
def dashboard():
    role = session.get('role')
    user_id = session.get('user_id')

    current_filter = request.args.get('filter', 'active')
    assigned_filter = request.args.get('assigned', 'me')
    sort_param = request.args.get('sort', 'updated_at_desc')

    # Validate sort parameter against whitelist
    if sort_param not in ALLOWED_SORT_OPTIONS:
        sort_param = 'updated_at_desc'
    order_by = ALLOWED_SORT_OPTIONS[sort_param]

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    # Load IT admins
    cursor.execute("""
    SELECT id, full_name FROM users
    WHERE role = 'admin' AND LOWER(full_name) != 'admin'
    ORDER BY full_name
    """)
    it_admins = cursor.fetchall()
    it_admin_ids = [admin[0] for admin in it_admins]
    placeholders_admins = ','.join('?' for _ in it_admin_ids) if it_admin_ids else 'NULL'

    # Status condition
    status_condition = "1=1"
    if current_filter == 'active':
        status_condition = "t.status != 'closed'"
    elif current_filter == 'closed':
        status_condition = "t.status = 'closed'"

    tickets = []
    tickets_assigned_to_me = []
    tickets_my_created = []
    tickets_watched = []
    tickets_all_for_admin = []
    tickets_watched_admin = []
    tickets_by_admin = {}
    tickets_closed_by_admin = {}
    tickets_browse_all_public = []
    tickets_browse_department = []

    if role != 'admin':
        # All Public Tickets (non-admin users)
        # Dynamic fragments come only from ALLOWED_SORT_OPTIONS and fixed status clauses.
        query_browse_all = f"""
        SELECT t.id, t.title, t.priority, t.status, t.created_at, t.updated_at, t.created_by, t.assigned_to, t.due_date,
        c.name as category_name, u.full_name as created_by_name,
        CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name
        FROM tickets t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN users u ON t.created_by = u.id
        LEFT JOIN users a ON t.assigned_to = a.id
        WHERE t.is_private = 0 AND {status_condition}
        ORDER BY {order_by}
        """
        cursor.execute(query_browse_all)
        tickets_browse_all_public = cursor.fetchall()

        # My Department Tickets
        user_department_id = session.get('department_id')
        if user_department_id:
            query_browse_dept = f"""
            SELECT DISTINCT t.id, t.title, t.priority, t.status, t.created_at, t.updated_at, t.created_by, t.assigned_to, t.due_date,
            c.name as category_name, u.full_name as created_by_name,
            CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name
            FROM tickets t
            LEFT JOIN categories c ON t.category_id = c.id
            LEFT JOIN users u ON t.created_by = u.id
            LEFT JOIN users a ON t.assigned_to = a.id
            LEFT JOIN ticket_watchers tw ON t.id = tw.ticket_id
            WHERE (u.department_id = ? OR a.department_id = ?)
            AND (t.is_private = 0 OR t.created_by = ? OR t.assigned_to = ? OR tw.user_id = ?)
            AND {status_condition}
            ORDER BY {order_by}
            """
            cursor.execute(query_browse_dept, (user_department_id, user_department_id, user_id, user_id, user_id))
            tickets_browse_department = cursor.fetchall()
    else:
        # Admins see all tickets in Browse
        query_browse_all_admin = f"""
        SELECT t.id, t.title, t.priority, t.status, t.created_at, t.updated_at, t.created_by, t.assigned_to, t.due_date,
        c.name as category_name, u.full_name as created_by_name,
        CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name
        FROM tickets t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN users u ON t.created_by = u.id
        LEFT JOIN users a ON t.assigned_to = a.id
        WHERE {status_condition}
        ORDER BY {order_by}
        """
        cursor.execute(query_browse_all_admin)
        tickets_browse_all_public = cursor.fetchall()
        tickets_browse_department = []

    if role != 'admin':
        # ==== REGULAR USER QUERIES ====
        
        # Browse Tickets: All Public & My Department
        query_browse_all = f"""
        SELECT t.id, t.title, t.priority, t.status, t.created_at, t.updated_at, t.created_by, t.assigned_to, t.due_date,
        c.name as category_name, u.full_name as created_by_name,
        CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name
        FROM tickets t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN users u ON t.created_by = u.id
        LEFT JOIN users a ON t.assigned_to = a.id
        WHERE t.is_private = 0 AND {status_condition}
        ORDER BY {order_by}
        """
        cursor.execute(query_browse_all)
        tickets_browse_all_public = cursor.fetchall()
        
        user_department_id = session.get('department_id')
        if user_department_id:
            query_browse_dept = f"""
            SELECT DISTINCT t.id, t.title, t.priority, t.status, t.created_at, t.updated_at, t.created_by, t.assigned_to, t.due_date,
            c.name as category_name, u.full_name as created_by_name,
            CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name
            FROM tickets t
            LEFT JOIN categories c ON t.category_id = c.id
            LEFT JOIN users u ON t.created_by = u.id
            LEFT JOIN users a ON t.assigned_to = a.id
            LEFT JOIN ticket_watchers tw ON t.id = tw.ticket_id
            WHERE (
            (u.department_id = ? OR a.department_id = ?) AND t.is_private = 0
            OR t.created_by = ?
            OR t.assigned_to = ?
            OR tw.user_id = ?
            )
            AND {status_condition}
            ORDER BY {order_by}

            """
            cursor.execute(query_browse_dept, (user_department_id, user_department_id, user_id, user_id, user_id))
            tickets_browse_department = cursor.fetchall()
        
        # === REGULAR USERS QUERIES ===
        if role != 'admin':
            # 1. Assigned to me
            if current_filter == 'closed':
                # Show ONLY closed tickets
                query_assigned = f"""
                SELECT t.id, t.title, t.priority, t.status, t.created_at, t.updated_at, 
                t.created_by, t.assigned_to, t.due_date,
                c.name as category_name,
                u.full_name as created_by_name,
                CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name
                FROM tickets t
                LEFT JOIN categories c ON t.category_id = c.id
                LEFT JOIN users u ON t.created_by = u.id
                LEFT JOIN users a ON t.assigned_to = a.id
                WHERE t.assigned_to = ? AND t.status = 'closed'
                ORDER BY {order_by}
                """
            else:
                # Show active or all (excluding closed for active)
                status_cond = "t.status != 'closed'" if current_filter == 'active' else "1=1"
                query_assigned = f"""
                SELECT t.id, t.title, t.priority, t.status, t.created_at, t.updated_at, 
                t.created_by, t.assigned_to, t.due_date,
                c.name as category_name,
                u.full_name as created_by_name,
                CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name
                FROM tickets t
                LEFT JOIN categories c ON t.category_id = c.id
                LEFT JOIN users u ON t.created_by = u.id
                LEFT JOIN users a ON t.assigned_to = a.id
                WHERE t.assigned_to = ? AND {status_cond}
                ORDER BY {order_by}
                """
                
            cursor.execute(query_assigned, (user_id,))
            tickets_assigned_to_me = cursor.fetchall()
            
            # 2. My created tickets (same fix)
            if current_filter == 'closed':
                query_created = f"""
                SELECT t.id, t.title, t.priority, t.status, t.created_at, t.updated_at, 
                t.created_by, t.assigned_to, t.due_date,
                c.name as category_name,
                u.full_name as created_by_name,
                CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name
                FROM tickets t
                LEFT JOIN categories c ON t.category_id = c.id
                LEFT JOIN users u ON t.created_by = u.id
                LEFT JOIN users a ON t.assigned_to = a.id
                WHERE t.created_by = ? AND t.status = 'closed'
                ORDER BY {order_by}
                """
            else:
                status_cond = "t.status != 'closed'" if current_filter == 'active' else "1=1"
                query_created = f"""
                SELECT t.id, t.title, t.priority, t.status, t.created_at, t.updated_at, 
                t.created_by, t.assigned_to, t.due_date,
                c.name as category_name,
                u.full_name as created_by_name,
                CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name
                FROM tickets t
                LEFT JOIN categories c ON t.category_id = c.id
                LEFT JOIN users u ON t.created_by = u.id
                LEFT JOIN users a ON t.assigned_to = a.id
                WHERE t.created_by = ? AND {status_cond}
                ORDER BY {order_by}
                """
                
            cursor.execute(query_created, (user_id,))
            tickets_my_created = cursor.fetchall()
            
            # Watched
            query_watched = f"""
            SELECT DISTINCT t.id, t.title, t.priority, t.status, t.created_at, t.updated_at, t.created_by, t.assigned_to, t.due_date,
            c.name as category_name, u.full_name as created_by_name,
            CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name
            FROM tickets t
            JOIN ticket_watchers tw ON t.id = tw.ticket_id
            LEFT JOIN categories c ON t.category_id = c.id
            LEFT JOIN users u ON t.created_by = u.id
            LEFT JOIN users a ON t.assigned_to = a.id
            WHERE tw.user_id = ? AND {status_condition}
            ORDER BY {order_by}
            """
            cursor.execute(query_watched, (user_id,))
            tickets_watched = cursor.fetchall()

    else:
        # ==== ADMIN QUERIES ====
        
        # Admins see all tickets in Browse
        query_browse_all_admin = f"""
        SELECT t.id, t.title, t.priority, t.status, t.created_at, t.updated_at, t.created_by, t.assigned_to, t.due_date,
        c.name as category_name, u.full_name as created_by_name,
        CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name
        FROM tickets t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN users u ON t.created_by = u.id
        LEFT JOIN users a ON t.assigned_to = a.id
        WHERE {status_condition}
        ORDER BY {order_by}
        """
        cursor.execute(query_browse_all_admin)
        tickets_browse_all_public = cursor.fetchall()
        tickets_browse_department = []
        
        # Admin: All tickets assigned to IT or any admin
        query_all_for_admin = f"""
        SELECT DISTINCT t.id, t.title, t.priority, t.status, t.created_at, t.updated_at, t.created_by, t.assigned_to, t.due_date,
        c.name as category_name, u.full_name as created_by_name,
        CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name,
        CASE WHEN t.created_by = ? THEN 'own' ELSE 'team' END as ticket_type
        FROM tickets t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN users u ON t.created_by = u.id
        LEFT JOIN users a ON t.assigned_to = a.id
        WHERE (t.assigned_to = 'IT' OR t.assigned_to IN ({placeholders_admins}))
        AND {status_condition}
        ORDER BY {order_by}
        """
        params_all_for_admin = [user_id] + it_admin_ids
        cursor.execute(query_all_for_admin, params_all_for_admin)
        tickets_all_for_admin = cursor.fetchall()
        
        # ==== ADMIN: Assigned to ME specifically (INCLUDES IT tickets) ====
        query_assigned_to_me = f"""
        SELECT t.id, t.title, t.priority, t.status, t.created_at, t.updated_at, t.created_by, t.assigned_to, t.due_date,
        c.name as category_name, u.full_name as created_by_name,
        CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name
        FROM tickets t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN users u ON t.created_by = u.id
        LEFT JOIN users a ON t.assigned_to = a.id
        WHERE t.assigned_to = ? AND {status_condition}
        ORDER BY {order_by}
        """
        cursor.execute(query_assigned_to_me, (user_id,))
        tickets_assigned_to_me = cursor.fetchall()
        
        # Admin: My created (admins)
        placeholders = ','.join('?' for _ in it_admin_ids) if it_admin_ids else 'NULL'
        query_my_created = f"""
        SELECT t.id, t.title, t.priority, t.status, t.created_at, t.updated_at, t.created_by, t.assigned_to, t.due_date,
        c.name as category_name, u.full_name as created_by_name,
        CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name
        FROM tickets t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN users u ON t.created_by = u.id
        LEFT JOIN users a ON t.assigned_to = a.id
        WHERE t.created_by IN ({placeholders}) AND {status_condition}
        ORDER BY {order_by}
        """
        if it_admin_ids:
            cursor.execute(query_my_created, it_admin_ids)
            tickets_my_created = cursor.fetchall()
        else:
            tickets_my_created = []
        
        # Watched by admins (distinct)
        query_watched_admin = f"""
        SELECT DISTINCT t.id, t.title, t.priority, t.status, t.created_at, t.updated_at, t.created_by, t.assigned_to, t.due_date,
        c.name as category_name, u.full_name as created_by_name,
        CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name,
        CASE WHEN t.created_by = ? THEN 'own' ELSE 'team' END as ticket_type
        FROM tickets t
        JOIN ticket_watchers tw ON t.id = tw.ticket_id
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN users u ON t.created_by = u.id
        LEFT JOIN users a ON t.assigned_to = a.id
        WHERE tw.user_id IN ({placeholders_admins})
        """
        params_watched = [user_id] + it_admin_ids
        
        if current_filter == 'active':
            query_watched_admin += " AND t.status != 'closed'"
        elif current_filter == 'closed':
            query_watched_admin += " AND t.status = 'closed'"
        
        query_watched_admin += f" ORDER BY {order_by}"
        cursor.execute(query_watched_admin, params_watched)
        tickets_watched_admin = cursor.fetchall()

        # ==== OPTIMIZED: ONE QUERY FOR ALL ADMINS (N+1 FIX) ====
        if it_admin_ids:
            # Active/all tickets grouped by admin
            if current_filter != 'closed':
                placeholders_batch = ','.join('?' for _ in it_admin_ids)
                query_all_admin_tickets = f"""
                SELECT t.assigned_to, t.id, t.title, t.priority, t.status, 
                t.created_at, t.updated_at, t.created_by, t.due_date,
                c.name as category_name, u.full_name as created_by_name,
                CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name
                FROM tickets t
                LEFT JOIN categories c ON t.category_id = c.id
                LEFT JOIN users u ON t.created_by = u.id
                LEFT JOIN users a ON t.assigned_to = a.id
                WHERE t.assigned_to IN ({placeholders_batch}) AND {status_condition}
                ORDER BY t.assigned_to, {order_by}
                """
                cursor.execute(query_all_admin_tickets, it_admin_ids)
                all_admin_tickets = cursor.fetchall()
                
                # Group by admin_id
                for admin_id in it_admin_ids:
                    tickets_by_admin[admin_id] = [
                        ticket for ticket in all_admin_tickets 
                        if ticket[0] == admin_id
                    ]
                
            # Closed tickets grouped by admin
            if current_filter == 'closed':
                placeholders_batch = ','.join('?' for _ in it_admin_ids)
                query_closed_admin = f"""
                SELECT t.assigned_to, t.id, t.title, t.priority, t.status, 
                t.created_at, t.updated_at, t.created_by, t.due_date,
                c.name as category_name, u.full_name as created_by_name,
                CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name
                FROM tickets t
                LEFT JOIN categories c ON t.category_id = c.id
                LEFT JOIN users u ON t.created_by = u.id
                LEFT JOIN users a ON t.assigned_to = a.id
                WHERE t.assigned_to IN ({placeholders_batch}) AND t.status = 'closed'
                ORDER BY t.assigned_to, {order_by}
                """
                cursor.execute(query_closed_admin, it_admin_ids)
                all_closed = cursor.fetchall()
                
                for admin_id in it_admin_ids:
                    tickets_closed_by_admin[admin_id] = [
                        ticket for ticket in all_closed 
                        if ticket[0] == admin_id
                    ]
            # ==== END OPTIMIZED N+1 FIX ====

    # Get all users for Browse filter
    cursor.execute("SELECT id, full_name FROM users ORDER BY full_name")
    all_users_for_filter = cursor.fetchall()

    stats = _dashboard_stats(cursor, role, user_id)

    conn.close()

    return render_template(
        'dashboard.html',
        tickets=tickets,
        tickets_assigned_to_me=tickets_assigned_to_me,
        tickets_my_created=tickets_my_created,
        tickets_watched=tickets_watched,
        role=role,
        it_admins=it_admins,
        current_filter=current_filter,
        assigned_filter=assigned_filter,
        sort_param=sort_param,
        tickets_watched_admin=tickets_watched_admin,
        tickets_all_for_admin=tickets_all_for_admin,
        tickets_by_admin=tickets_by_admin,
        tickets_closed_by_admin=tickets_closed_by_admin,
        tickets_browse_all_public=tickets_browse_all_public,
        tickets_browse_department=tickets_browse_department,
        all_users_for_filter=all_users_for_filter,
        stats=stats,
    )


def _dashboard_stats(cursor, role, user_id):
    """Summary counts for the dashboard header, independent of the active filter.

    Admins see the whole helpdesk; everyone else sees tickets they created,
    are assigned to, or watch.
    """
    scope_sql = ""
    params = []
    if role != 'admin':
        scope_sql = """
        AND (t.created_by = ? OR t.assigned_to = ?
             OR t.id IN (SELECT ticket_id FROM ticket_watchers WHERE user_id = ?))
        """
        params = [user_id, user_id, user_id]

    today = datetime.now().strftime('%Y-%m-%d')
    week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    cursor.execute(
        f"""
        SELECT
            COALESCE(SUM(t.status != 'closed'), 0),
            COALESCE(SUM(t.status = 'in_progress'), 0),
            COALESCE(SUM(t.status = 'awaiting_confirmation'), 0),
            COALESCE(SUM(t.status != 'closed' AND t.priority = 'high'), 0),
            COALESCE(SUM(t.status != 'closed' AND t.due_date IS NOT NULL AND t.due_date != '' AND t.due_date < ?), 0),
            COALESCE(SUM(t.status = 'closed' AND t.updated_at >= ?), 0)
        FROM tickets t
        WHERE 1=1 {scope_sql}
        """,
        [today, week_ago, *params],
    )
    row = cursor.fetchone() or (0, 0, 0, 0, 0, 0)
    keys = ('open', 'in_progress', 'awaiting', 'high', 'overdue', 'closed_week')
    return dict(zip(keys, (int(v or 0) for v in row)))

@app.route('/create_ticket', methods=['GET', 'POST'])
@login_required
def create_ticket():
    try:
        user_id = session.get('user_id')
        db = Database()
        conn = db.get_connection()
        cursor = conn.cursor()

        if request.method == 'POST':
            title = request.form.get('title')
            description = request.form.get('description')
            priority = request.form.get('priority')
            category_id = request.form.get('category_id')
            due_date = request.form.get('due_date')

            assigned_to_raw = request.form.get('assigned_to')
            assigned_to_db = None

            if assigned_to_raw and assigned_to_raw.strip() != '':
                if assigned_to_raw.strip() == 'IT':
                    assigned_to_db = 'IT'
                else:
                    try:
                        assigned_to_db = int(assigned_to_raw)  # User ID
                    except ValueError:
                        flash('Invalid user selected for assignment.', 'error')
                        conn.close()
                        return redirect(url_for('create_ticket'))

            watchers_input = request.form.get('watchers', '').strip()

            if (
                not title
                or not description
                or priority not in VALID_PRIORITIES
                or not category_id
                or len(title.strip()) > 200
                or len(description.strip()) > 20_000
            ):
                flash('Please fill in all required fields.', 'error')
                return render_template(
                    'create_ticket.html',
                    categories=get_categories(),
                    users=get_users(),
                    it_admins=get_it_admins(),
                    templates=get_user_templates(user_id),
                    form=request.form
                )

            is_private = 1 if request.form.get('is_private') else 0
            cursor.execute("""
            INSERT INTO tickets (title, description, priority, category_id, due_date, created_by, assigned_to, is_private, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new', datetime('now', 'localtime'), datetime('now', 'localtime'))
            """, (title, description, priority, category_id, due_date, user_id, assigned_to_db, is_private))
            conn.commit()

            ticket_id = cursor.lastrowid

            # Watchers
            if watchers_input:
                watcher_names = [w.strip() for w in watchers_input.split(',') if w.strip()]
                for watcher_name in watcher_names:
                    cursor.execute("SELECT id FROM users WHERE full_name = ?", (watcher_name,))
                    watcher = cursor.fetchone()
                    if watcher:
                        watcher_id = watcher[0]
                        cursor.execute(
                            "INSERT OR IGNORE INTO ticket_watchers (ticket_id, user_id) VALUES (?, ?)",
                            (ticket_id, watcher_id)
                        )
                    else:
                        flash(f'Watcher user "{watcher_name}" does not exist.', 'error')
                conn.commit()

            # ==== FILE UPLOAD WITH SECURITY VALIDATION ====
            if 'attachments' in request.files:
                files = request.files.getlist('attachments')
                _ensure_upload_dir()
                for file in files:
                    if file and file.filename:
                        try:
                            _save_attachment(file, ticket_id, user_id, cursor)
                        except ValueError as exc:
                            flash(str(exc), 'error')
                conn.commit()
            # ==== END FILE UPLOAD ====

            log_activity(user_id, 'TICKET_CREATED', f'Ticket ID: {ticket_id}, Title: {title}')
            notify_on_ticket_created(ticket_id, actor_user_id=user_id)

            flash('Ticket created successfully.', 'success')
            return redirect(url_for('dashboard'))

        return render_template(
            'create_ticket.html',
            categories=get_categories(),
            users=get_users(),
            it_admins=get_it_admins(),
            templates=get_user_templates(user_id),
            form={}
        )

    except Exception as e:
        flash(f'Error creating ticket: {str(e)}', 'error')
        return render_template(
            'create_ticket.html',
            categories=get_categories(),
            users=get_users(),
            it_admins=get_it_admins(),
            templates=get_user_templates(user_id),
            form=request.form
        )

def get_categories():
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM categories ORDER BY name")
    categories = cursor.fetchall()
    conn.close()
    return categories

def get_users():
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, full_name FROM users WHERE role != 'admin' ORDER BY full_name")
    users = cursor.fetchall()
    conn.close()
    return users

def get_it_admins():
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, full_name FROM users WHERE role = 'admin' ORDER BY full_name")
    it_admins = cursor.fetchall()
    conn.close()
    return it_admins

@app.route('/ticket/<int:ticket_id>')
@login_required
def ticket_detail(ticket_id):
    user_id = session.get('user_id')
    role = session.get('role')
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT t.id, t.title, t.description, t.category_id, t.priority, t.status, t.created_by, t.assigned_to, t.is_private, t.created_at, t.updated_at, t.due_date,
    c.name as category_name, u.full_name as created_by_name, a.full_name as assigned_to_name
    FROM tickets t
    LEFT JOIN categories c ON t.category_id = c.id
    LEFT JOIN users u ON t.created_by = u.id
    LEFT JOIN users a ON t.assigned_to = a.id
    WHERE t.id = ?
    """, (ticket_id,))
    ticket = cursor.fetchone()
    if not ticket:
        flash('Ticket not found.', 'error')
        conn.close()
        return redirect(url_for('dashboard'))

    access = _get_ticket_access(cursor, ticket_id, user_id)
    if not _can_view_ticket(access, user_id, role):
        conn.close()
        abort(403)

    # Get watchers
    cursor.execute("""
    SELECT tw.user_id, u.full_name
    FROM ticket_watchers tw
    JOIN users u ON tw.user_id = u.id
    WHERE tw.ticket_id = ?
    """, (ticket_id,))
    watchers = list(cursor.fetchall())

    cursor.execute("""
    SELECT c.id, c.comment, c.created_at, u.full_name
    FROM comments c
    JOIN users u ON c.user_id = u.id
    WHERE c.ticket_id = ?
    ORDER BY c.created_at DESC
    """, (ticket_id,))
    comments = cursor.fetchall()

    cursor.execute("""
    SELECT a.id, a.filename, a.original_filename, a.created_at, a.uploaded_by, u.full_name
    FROM attachments a
    LEFT JOIN users u ON a.uploaded_by = u.id
    WHERE a.ticket_id = ?
    ORDER BY a.created_at DESC
    """, (ticket_id,))
    attachments = cursor.fetchall()

    # Get all users for watchers autocomplete (INCLUDING admins)
    cursor.execute("SELECT id, full_name FROM users ORDER BY full_name")
    all_users = cursor.fetchall()

    # Get ONLY non-admin users for reassignment dropdown (EXCLUDING admins)
    cursor.execute("SELECT id, full_name FROM users WHERE role != 'admin' ORDER BY full_name")
    users = cursor.fetchall()


    # Get ONLY non-admin users for reassignment dropdown (EXCLUDING admins)
    cursor.execute("""
    SELECT id, full_name FROM users 
    WHERE role != 'admin' 
    ORDER BY full_name
    """)
    users = cursor.fetchall()

    # Get IT admins for reassignment dropdown
    it_admins = get_it_admins()
    
    # Get activity log
    cursor.execute("""
    SELECT 
    tal.id,
    tal.ticket_id,
    tal.action_type,
    datetime(tal.created_at, 'localtime') as created_at_local,
    u.full_name as user_name,
    tal.old_value,
    tal.new_value,
    tal.details
    FROM ticket_activity_log tal
    JOIN users u ON tal.user_id = u.id
    WHERE tal.ticket_id = ?
    ORDER BY tal.created_at DESC
    LIMIT 50
    """, (ticket_id,))
    activity_log = cursor.fetchall()

    can_start_work = False
    can_confirm = False
    # Allow admin OR the assignee to start work when status is 'assigned' or 'new'
    if (role == 'admin' or str(user_id) == str(ticket[7])) and ticket[5] in ['assigned', 'new']:
        can_start_work = True
    # Allow creator to confirm resolution
    if str(user_id) == str(ticket[6]) and ticket[5] == 'awaiting_confirmation':
        can_confirm = True

    # Proveri da li je trenutni korisnik mute-ovao ovaj tiket
    cursor.execute("""
    SELECT 1 FROM ticket_muted_users
    WHERE ticket_id=? AND user_id=?
    """, (ticket_id, user_id))
    is_muted = cursor.fetchone() is not None
    
    conn.close()

    return render_template(
        'ticket_detail.html',
        ticket=ticket,
        watchers=watchers,
        comments=comments,
        attachments=attachments,
        can_start_work=can_start_work,
        can_confirm=can_confirm,
        users=users,
        all_users=all_users,
        it_admins=it_admins,          
        activity_log=activity_log,
        is_muted=is_muted,
        can_manage=_can_manage_ticket(access, user_id, role)
    )

@app.route('/update_ticket_status', methods=['POST'])
@login_required
def update_ticket_status():
    ticket_id = request.form.get('ticket_id', type=int)
    new_status = request.form.get('status', '').strip()
    actor_id = session.get('user_id')

    if not ticket_id or new_status not in VALID_STATUSES:
        abort(400)

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    access = _get_ticket_access(cursor, ticket_id, actor_id)
    if not access:
        conn.close()
        abort(404)
    if not _can_change_status(access, actor_id, session.get('role'), new_status):
        conn.close()
        abort(403)
    old_status = access['status']
    conn.close()

    ticket_model.update_ticket_status(ticket_id, new_status)

    if old_status != new_status:
        log_ticket_activity(ticket_id, actor_id, 'status_changed', old_status, new_status)
        notify_on_status_change(ticket_id, old_status, new_status, actor_user_id=actor_id)

    flash('Ticket status updated!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/admin')
@admin_required
def admin_panel():
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM categories ORDER BY name")
    categories = cursor.fetchall()

    cursor.execute("SELECT * FROM departments ORDER BY name")
    departments = cursor.fetchall()

    cursor.execute("""
    SELECT u.id, u.username, NULL AS password, u.email, u.full_name, u.role,
    u.department_id, u.is_department_head, u.created_at, d.name as department_name
    FROM users u
    LEFT JOIN departments d ON u.department_id = d.id
    ORDER BY u.full_name
    """)
    users = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) FROM tickets")
    ticket_count = cursor.fetchone()[0]

    conn.close()

    return render_template('admin_panel.html', 
        categories=categories, 
        departments=departments, 
        users=users,
        tickets=[None] * ticket_count)

@app.route('/add_category', methods=['POST'])
@admin_required
def add_category():
    name = request.form['name']
    description = request.form.get('description', '')

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("INSERT INTO categories (name, description) VALUES (?, ?)", (name, description))
        conn.commit()
        flash('Category added successfully!', 'success')
    except Exception as e:
        flash(f'Error adding category: {str(e)}', 'error')
    finally:
        conn.close()

    return redirect(url_for('admin_panel'))

@app.route('/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    try:
        if user_id == session.get('user_id'):
            return jsonify({'success': False, 'error': 'You cannot delete your own account.'}), 400
        cursor.execute("SELECT role FROM users WHERE id = ?", (user_id,))
        target = cursor.fetchone()
        if not target:
            return jsonify({'success': False, 'error': 'User not found.'}), 404
        if target[0] == 'admin':
            cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
            if cursor.fetchone()[0] <= 1:
                return jsonify({'success': False, 'error': 'The last administrator cannot be deleted.'}), 409
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
    finally:
        conn.close()

@app.route('/delete_department/<int:dept_id>', methods=['POST'])
@admin_required
def delete_department(dept_id):
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("UPDATE users SET department_id = NULL WHERE department_id = ?", (dept_id,))
        cursor.execute("DELETE FROM departments WHERE id = ?", (dept_id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
    finally:
        conn.close()

@app.route('/delete_category/<int:cat_id>', methods=['POST'])
@admin_required
def delete_category(cat_id):
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
    finally:
        conn.close()

@app.route('/add_department', methods=['POST'])
@admin_required
def add_department():
    name = request.form['name']
    description = request.form.get('description', '')

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("INSERT INTO departments (name, description) VALUES (?, ?)", (name, description))
        conn.commit()
        flash('Department added successfully!', 'success')
    except Exception as e:
        flash(f'Error adding department: {str(e)}', 'error')
    finally:
        conn.close()

    return redirect(url_for('admin_panel'))

@app.route('/toggle_department_head', methods=['POST'])
@admin_required
def toggle_department_head():
    user_id = request.form['user_id']

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT is_department_head FROM users WHERE id = ?", (user_id,))
    current_status = cursor.fetchone()[0]
    new_status = 0 if current_status else 1

    cursor.execute("UPDATE users SET is_department_head = ? WHERE id = ?", (new_status, user_id))
    conn.commit()
    conn.close()

    flash('Department head status updated!', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/reopen_ticket/<int:ticket_id>', methods=['POST'])
@login_required
def reopen_ticket(ticket_id):
    user_id = session.get('user_id')
    role = session.get('role')
    
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
    SELECT id, status, created_by, assigned_to
    FROM tickets
    WHERE id = ?
    """, (ticket_id,))
    ticket = cursor.fetchone()
    
    if not ticket:
        conn.close()
        flash('Ticket not found.', 'error')
        return redirect(url_for('dashboard'))
    
    ticket_id_db, status, created_by, assigned_to = ticket

    access = _get_ticket_access(cursor, ticket_id, user_id)
    if not _can_manage_ticket(access, user_id, role):
        conn.close()
        abort(403)
    
    if status != 'closed':
        conn.close()
        flash('Only closed tickets can be reopened.', 'error')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    
    # Convert assigned_to from DB to int when possible
    if assigned_to is not None:
        try:
            assigned_to_id = int(assigned_to)
        except (ValueError, TypeError):
            assigned_to_id = None
    else:
        assigned_to_id = None

    cursor.execute("""
    UPDATE tickets 
    SET status = 'in_progress', updated_at = datetime('now', 'localtime')
    WHERE id = ?
    """, (ticket_id,))
    
    cursor.execute("""
    INSERT INTO comments (ticket_id, user_id, comment)
    VALUES (?, ?, ?)
    """, (ticket_id, user_id, '🔄 Ticket reopened'))
    
    conn.commit()
    conn.close()
    
    notify_on_status_change(ticket_id, 'closed', 'in_progress', actor_user_id=user_id)
    
    log_activity(user_id, 'TICKET_REOPENED', f'Ticket ID: {ticket_id}')
    flash('Ticket reopened successfully!', 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))

@app.route('/delete_attachment/<int:attachment_id>', methods=['POST'])
@login_required
def delete_attachment(attachment_id):
    user_id = session.get('user_id')
    role = session.get('role')
    
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
    SELECT a.id, a.ticket_id, a.filename, a.file_path, a.uploaded_by, t.created_by
    FROM attachments a
    JOIN tickets t ON a.ticket_id = t.id
    WHERE a.id = ?
    """, (attachment_id,))
    attachment = cursor.fetchone()
    
    if not attachment:
        conn.close()
        flash('Attachment not found.', 'error')
        return redirect(url_for('dashboard'))
    
    attachment_id_db, ticket_id, filename, file_path, uploaded_by, ticket_creator = attachment

    access = _get_ticket_access(cursor, ticket_id, user_id)
    if not _can_view_ticket(access, user_id, role):
        conn.close()
        abort(403)
    
    if role != 'admin' and user_id != uploaded_by and user_id != ticket_creator:
        conn.close()
        flash('You do not have permission to delete this attachment.', 'error')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    
    cursor.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
    conn.commit()
    conn.close()
    
    try:
        resolved_path = _resolve_attachment_path(file_path)
        if resolved_path and resolved_path.exists():
            resolved_path.unlink()
    except Exception as e:
        flash(f'Error deleting file from server: {str(e)}', 'warning')
    
    log_activity(user_id, 'ATTACHMENT_DELETED', f'Attachment ID: {attachment_id}, Ticket ID: {ticket_id}')
    flash('Attachment deleted successfully!', 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))

@app.route('/get_all_users')
@login_required
def get_all_users():
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT full_name FROM users ORDER BY full_name')
    users = cursor.fetchall()
    conn.close()
    
    user_names = [user[0] for user in users]
    return jsonify({'users': user_names})

@app.route('/add_comment', methods=['POST'])
@login_required
def add_comment():
    ticket_id = request.form.get('ticket_id', type=int)
    comment = request.form.get('comment', '').strip()
    
    if not ticket_id or not comment or len(comment) > 10_000:
        flash('Ticket ID and comment are required.', 'error')
        return redirect(url_for('dashboard'))
    
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    user_id = session.get('user_id')
    access = _get_ticket_access(cursor, ticket_id, user_id)
    if not access:
        conn.close()
        abort(404)
    if not _can_view_ticket(access, user_id, session.get('role')):
        conn.close()
        abort(403)
    
    # Add comment
    cursor.execute("""
    INSERT INTO comments (ticket_id, user_id, comment)
    VALUES (?, ?, ?)
    """, (ticket_id, user_id, comment))
    
    for key in request.files:
        if key.startswith('pasted_file_'):
            try:
                _save_attachment(request.files[key], ticket_id, user_id, cursor)
            except ValueError as exc:
                conn.rollback()
                conn.close()
                flash(str(exc), 'error')
                return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    cursor.execute(
        "UPDATE tickets SET updated_at = datetime('now', 'localtime') WHERE id = ?",
        (ticket_id,),
    )
    
    conn.commit()
    conn.close()
    
    notify_on_comment(ticket_id, user_id, comment)
    
    flash('Comment added successfully.', 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))

@app.route('/upload_attachment', methods=['POST'])
@login_required
def upload_attachment():
    ticket_id = request.form.get('ticket_id', type=int)
    
    if not ticket_id:
        flash('Ticket ID is required.', 'error')
        return redirect(url_for('dashboard'))
    
    # Handle single or multiple files
    files = request.files.getlist('file')
    
    if not files or not files[0].filename:
        flash('No file selected.', 'error')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    user_id = session.get('user_id')
    access = _get_ticket_access(cursor, ticket_id, user_id)
    if not access:
        conn.close()
        abort(404)
    if not _can_view_ticket(access, user_id, session.get('role')):
        conn.close()
        abort(403)

    uploaded_count = 0

    invalid_file = next(
        (file for file in files if file and file.filename and not allowed_file(file.filename, file.content_type)),
        None,
    )
    if invalid_file:
        conn.close()
        flash(f'File type not allowed: {invalid_file.filename}', 'error')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    
    for file in files:
        if file and file.filename:
            try:
                if _save_attachment(file, ticket_id, user_id, cursor):
                    uploaded_count += 1
            except ValueError as exc:
                conn.rollback()
                conn.close()
                flash(str(exc), 'error')
                return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    cursor.execute(
        "UPDATE tickets SET updated_at = datetime('now', 'localtime') WHERE id = ?",
        (ticket_id,),
    )
    
    conn.commit()
    conn.close()
    
    if uploaded_count:
        log_ticket_activity(ticket_id, user_id, 'attachment_uploaded', new_value=f'{uploaded_count} file(s)')
        notify_on_attachment(ticket_id, actor_user_id=user_id, filename=f'{uploaded_count} file(s)')
    flash(f'{uploaded_count} file(s) uploaded successfully.', 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))

@app.route('/import_users', methods=['GET', 'POST'])
@admin_required
def import_users():
    if request.method == 'GET':
        db = Database()
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM departments ORDER BY name")
        departments = cursor.fetchall()
        conn.close()
        return render_template('import_users.html', departments=departments)

    if 'excel_file' not in request.files:
        flash('No file selected!', 'error')
        return redirect(url_for('import_users'))

    file = request.files['excel_file']
    if file.filename == '':
        flash('No file selected!', 'error')
        return redirect(url_for('import_users'))

    workbook = None
    try:
        if not file.filename.lower().endswith('.xlsx'):
            raise ValueError('Only .xlsx files are supported.')

        workbook = load_workbook(file, read_only=True, data_only=True)
        worksheet = workbook.active
        rows = worksheet.iter_rows(values_only=True)
        try:
            headers = [str(value).strip() if value is not None else '' for value in next(rows)]
        except StopIteration as exc:
            raise ValueError('The spreadsheet is empty.') from exc

        required_columns = {'username', 'password', 'full_name'}
        if not required_columns.issubset(headers):
            missing = ', '.join(sorted(required_columns - set(headers)))
            raise ValueError(f'Missing required columns: {missing}')
        if worksheet.max_row > 1_001:
            raise ValueError('A single import is limited to 1,000 users.')

        db = Database()
        conn = db.get_connection()
        cursor = conn.cursor()

        imported_count = 0
        errors = []

        for index, values in enumerate(rows, start=2):
            row = dict(zip(headers, values))
            if not any(value is not None and str(value).strip() for value in values):
                continue
            try:
                username = '' if row.get('username') is None else str(row['username']).strip()
                raw_password = '' if row.get('password') is None else str(row['password'])
                full_name = '' if row.get('full_name') is None else str(row['full_name']).strip()
                email = '' if row.get('email') is None else str(row['email']).strip()
                department_name = '' if row.get('department_name') is None else str(row['department_name']).strip()
                role = 'user' if row.get('role') is None else str(row['role']).strip().lower()

                if not username or len(username) > 80 or not full_name or len(full_name) > 120:
                    raise ValueError('Invalid username or full name')
                if len(raw_password) < 8:
                    raise ValueError('Password must be at least 8 characters')
                if email and not valid_email(email):
                    raise ValueError('Invalid email address')
                if role not in {'user', 'admin'}:
                    raise ValueError('Role must be user or admin')
                password = hash_password(raw_password)

                department_id = None
                if department_name:
                    cursor.execute("SELECT id FROM departments WHERE name = ?", (department_name,))
                    dept = cursor.fetchone()
                    if dept:
                        department_id = dept[0]
                    else:
                        cursor.execute("INSERT INTO departments (name) VALUES (?)", (department_name,))
                        department_id = cursor.lastrowid

                cursor.execute("""
                INSERT INTO users (username, password, full_name, email, department_id, role)
                VALUES (?, ?, ?, ?, ?, ?)
                """, (username, password, full_name, email, department_id, role))

                imported_count += 1
            except Exception as e:
                errors.append(f"Row {index}: {str(e)}")

        conn.commit()
        conn.close()

        if imported_count > 0:
            flash(f'Successfully imported {imported_count} users!', 'success')
        if errors:
            flash(f'Errors: {"; ".join(errors[:5])}', 'warning')

    except Exception as e:
        flash(f'Error reading Excel file: {str(e)}', 'error')
    finally:
        if workbook is not None:
            workbook.close()

    return redirect(url_for('admin_panel'))

@app.route('/add_user_manual', methods=['POST'])
@admin_required
def add_user_manual():
    username = request.form['username'].strip()
    password = request.form['password']
    full_name = request.form['full_name'].strip()
    email = request.form.get('email', '').strip()
    department_id = request.form.get('department_id')
    role = request.form.get('role', 'user').strip().lower()
    is_department_head = 1 if request.form.get('is_department_head') else 0

    if department_id == '':
        department_id = None

    if (
        not username
        or len(username) > 80
        or not full_name
        or len(full_name) > 120
        or len(password) < 8
        or (email and not valid_email(email))
        or role not in {'user', 'admin'}
    ):
        abort(400)

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        existing_user = cursor.fetchone()
        if existing_user:
            flash(f'Username "{username}" already exists! Please choose a different username.', 'error')
            conn.close()
            return redirect(url_for('admin_panel'))

        hashed_password = hash_password(password)
        cursor.execute("""
        INSERT INTO users (username, password, full_name, email, department_id, role, is_department_head)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (username, hashed_password, full_name, email, department_id, role, is_department_head))

        conn.commit()
        flash(f'User "{full_name}" added successfully!', 'success')
    except Exception as e:
        flash(f'Error adding user: {str(e)}', 'error')
    finally:
        conn.close()

    return redirect(url_for('admin_panel'))

@app.route('/assign_department', methods=['POST'])
@admin_required
def assign_department():
    user_id = request.form['user_id']
    department_id = request.form.get('department_id') or None

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute("UPDATE users SET department_id = ? WHERE id = ?", (department_id, user_id))
    conn.commit()
    conn.close()

    flash('Department assignment updated!', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/edit_ticket/<int:ticket_id>', methods=['GET', 'POST'])
@login_required
def edit_ticket(ticket_id):
    try:
        user_id = session.get('user_id')
        role = session.get('role')
        db = Database()
        conn = db.get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        ticket = cursor.fetchone()
        if not ticket:
            flash('Ticket not found.', 'error')
            conn.close()
            return redirect(url_for('dashboard'))

        access = _get_ticket_access(cursor, ticket_id, user_id)
        if not _can_manage_ticket(access, user_id, role):
            conn.close()
            abort(403)

        if request.method == 'POST':
            cursor.execute("SELECT created_by, assigned_to FROM tickets WHERE id = ?", (ticket_id,))
            row = cursor.fetchone()
            if not row:
                flash('Ticket not found.', 'error')
                conn.close()
                return redirect(url_for('dashboard'))

            created_by = row[0]
            assigned_to_raw = row[1]

            # Convert assigned_to from DB to int (if possible) for permission checks
            if assigned_to_raw is not None and assigned_to_raw != 'IT':
                try:
                    assigned_to_val = int(assigned_to_raw)
                except (ValueError, TypeError):
                    assigned_to_val = None
            else:
                assigned_to_val = None

            # Full edit allowed for creator or admin
            if str(user_id) == str(created_by) or role == 'admin':
                title = request.form.get('title')
                description = request.form.get('description')
                priority = request.form.get('priority')
                category_id = request.form.get('category_id')
                due_date = request.form.get('due_date')
                assigned_to_form = request.form.get('assigned_to') or None
                watchers_input = request.form.get('watchers', '').strip()

                if (
                    not title
                    or not description
                    or priority not in VALID_PRIORITIES
                    or not category_id
                    or len(title.strip()) > 200
                    or len(description.strip()) > 20_000
                ):
                    flash('Please fill in all required fields.', 'error')
                    conn.close()
                    return render_template(
                        'edit_ticket.html',
                        ticket=ticket,
                        categories=get_categories(),
                        users=get_users(),
                        it_admins=get_it_admins(),
                        watchers=request.form.get('watchers', ''),
                        form=request.form
                    )

                # Convert assigned_to_form from the form to integer or None (handle 'IT')
                assigned_to_db = None
                if assigned_to_form and assigned_to_form.strip() != '':
                    if assigned_to_form.strip() == 'IT':
                        assigned_to_db = 'IT'
                    else:
                        try:
                            assigned_to_db = int(assigned_to_form)
                        except ValueError:
                            flash('Invalid user selected for assignment.', 'error')
                            conn.close()
                            return redirect(url_for('edit_ticket', ticket_id=ticket_id))
                else:
                    assigned_to_db = None

                is_private = 1 if request.form.get('is_private') else 0
                cursor.execute("""
                    UPDATE tickets SET title = ?, description = ?, priority = ?, category_id = ?, due_date = ?, assigned_to = ?, is_private = ?, updated_at = datetime('now', 'localtime')
                    WHERE id = ?
                """, (title, description, priority, category_id, due_date, assigned_to_db, is_private, ticket_id))
                conn.commit()

                # Replace watchers
                cursor.execute("DELETE FROM ticket_watchers WHERE ticket_id = ?", (ticket_id,))
                added_watchers_ids = []
                if watchers_input:
                    watcher_names = [w.strip() for w in watchers_input.split(',') if w.strip()]
                    for watcher_name in watcher_names:
                        cursor.execute("SELECT id FROM users WHERE full_name = ?", (watcher_name,))
                        watcher = cursor.fetchone()
                        if watcher:
                            watcher_id = watcher[0]
                            cursor.execute("INSERT OR IGNORE INTO ticket_watchers (ticket_id, user_id) VALUES (?, ?)", (ticket_id, watcher_id))
                            added_watchers_ids.append(watcher_id)
                        else:
                            flash(f'Watcher user \"{watcher_name}\" does not exist.', 'error')
                    conn.commit()

                if added_watchers_ids:
                    notify_on_watchers_added(ticket_id, actor_user_id=user_id, watcher_user_ids=added_watchers_ids)

                flash('Ticket updated successfully.', 'success')
                conn.close()
                return redirect(url_for('ticket_detail', ticket_id=ticket_id))

            # Assigned user can only update watchers
            elif str(user_id) == str(assigned_to_val):
                watchers_input = request.form.get('watchers', '').strip()
                cursor.execute("DELETE FROM ticket_watchers WHERE ticket_id = ?", (ticket_id,))
                added_watchers_ids = []
                if watchers_input:
                    watcher_names = [w.strip() for w in watchers_input.split(',') if w.strip()]
                    for watcher_name in watcher_names:
                        cursor.execute("SELECT id FROM users WHERE full_name = ?", (watcher_name,))
                        watcher = cursor.fetchone()
                        if watcher:
                            watcher_id = watcher[0]
                            cursor.execute("INSERT OR IGNORE INTO ticket_watchers (ticket_id, user_id) VALUES (?, ?)", (ticket_id, watcher_id))
                            added_watchers_ids.append(watcher_id)
                        else:
                            flash(f'Watcher user \"{watcher_name}\" does not exist.', 'error')
                    conn.commit()

                if added_watchers_ids:
                    notify_on_watchers_added(ticket_id, actor_user_id=user_id, watcher_user_ids=added_watchers_ids)

                flash('Watchers updated successfully.', 'success')
                conn.close()
                return redirect(url_for('ticket_detail', ticket_id=ticket_id))

            else:
                flash('You do not have permission to edit this ticket.', 'error')
                conn.close()
                return redirect(url_for('ticket_detail', ticket_id=ticket_id))

        # GET: show edit form with current watchers
        cursor.execute("""
            SELECT u.full_name FROM users u
            JOIN ticket_watchers tw ON u.id = tw.user_id
            WHERE tw.ticket_id = ?
        """, (ticket_id,))
        watchers = [r[0] for r in cursor.fetchall()]
        watchers_str = ', '.join(watchers)
        conn.close()

        return render_template(
            'edit_ticket.html',
            ticket=ticket,
            categories=get_categories(),
            users=get_users(),
            it_admins=get_it_admins(),
            watchers=watchers_str,
            form={}
        )

    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        try:
            conn.close()
        except Exception:
            pass
        flash(f'Error updating ticket: {str(e)}', 'error')
        return render_template(
            'edit_ticket.html',
            ticket=ticket if 'ticket' in locals() else None,
            categories=get_categories(),
            users=get_users(),
            it_admins=get_it_admins(),
            watchers=request.form.get('watchers', ''),
            form=request.form
        )

@app.route('/manage_department_users/<int:dept_id>')
@admin_required
def manage_department_users(dept_id):
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM departments WHERE id = ?", (dept_id,))
    department = cursor.fetchone()

    cursor.execute("""
    SELECT id, username, full_name, email, role, is_department_head
    FROM users WHERE department_id = ?
    ORDER BY full_name
    """, (dept_id,))
    dept_users = cursor.fetchall()

    cursor.execute("""
    SELECT id, username, full_name, email, role
    FROM users WHERE department_id IS NULL
    ORDER BY full_name
    """)
    available_users = cursor.fetchall()

    conn.close()
    return render_template(
        'manage_department_users.html',
        department=department,
        dept_users=dept_users,
        available_users=available_users
    )

@app.route('/add_user_to_department', methods=['POST'])
@admin_required
def add_user_to_department():
    user_id = request.form['user_id']
    dept_id = request.form['dept_id']
    is_head = 1 if request.form.get('is_head') else 0

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
        UPDATE users SET department_id = ?, is_department_head = ?
        WHERE id = ?
        """, (dept_id, is_head, user_id))
        conn.commit()
        flash('User added to department successfully!', 'success')
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
    finally:
        conn.close()

    return redirect(url_for('manage_department_users', dept_id=dept_id))

@app.route('/remove_user_from_department', methods=['POST'])
@admin_required
def remove_user_from_department():
    user_id = request.form['user_id']
    dept_id = request.form['dept_id']

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
        UPDATE users SET department_id = NULL, is_department_head = 0
        WHERE id = ?
        """, (user_id,))
        conn.commit()
        flash('User removed from department!', 'success')
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
    finally:
        conn.close()

    return redirect(url_for('manage_department_users', dept_id=dept_id))

@app.route('/mark_resolved/<int:ticket_id>', methods=['POST'])
@login_required
def mark_resolved(ticket_id):
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM tickets WHERE id = ? AND created_by = ?", (ticket_id, session.get('user_id')))
    ticket = cursor.fetchone()

    if ticket:
        cursor.execute("UPDATE tickets SET status = 'closed', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (ticket_id,))
        conn.commit()
        conn.close()

        notify_on_closed(ticket_id, actor_user_id=session.get('user_id'))

        flash('Ticket marked as resolved!', 'success')
    else:
        conn.close()
        flash('Access denied!', 'error')

    return redirect(url_for('dashboard'))

@app.route('/assign_ticket', methods=['POST'])
@login_required
def assign_ticket():
    ticket_id = request.form.get('ticket_id')
    assigned_to = request.form.get('assigned_to')
    current_user_id = session.get('user_id')
    user_role = session.get('role')

    if not ticket_id:
        flash('Invalid ticket.', 'error')
        return redirect(url_for('dashboard'))

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    access = _get_ticket_access(cursor, ticket_id, current_user_id)
    if not access:
        conn.close()
        abort(404)
    if not _can_manage_ticket(access, current_user_id, user_role):
        conn.close()
        abort(403)

    assigned_to_db = None
    if assigned_to and assigned_to.strip() != '':
        try:
            assigned_to_db = int(assigned_to)
        except ValueError:
            flash('Invalid assigned user.', 'error')
            conn.close()
            return redirect(url_for('dashboard'))

        cursor.execute("SELECT 1 FROM users WHERE id = ?", (assigned_to_db,))
        if not cursor.fetchone():
            conn.close()
            abort(400)

    current_status = access['status']
    new_status = 'assigned' if current_status == 'new' and assigned_to_db is not None else current_status

    cursor.execute("""
    UPDATE tickets
    SET assigned_to = ?, status = ?
    WHERE id = ?
    """, (assigned_to_db, new_status, ticket_id))

    conn.commit()
    conn.close()

    notify_on_assigned(ticket_id, actor_user_id=current_user_id, assigned_to_id=assigned_to_db)

    flash('Ticket assignment updated.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/download_attachment/<int:attachment_id>')
@login_required
def download_attachment(attachment_id):
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
    SELECT filename, original_filename, file_path, ticket_id
    FROM attachments
    WHERE id = ?
    """, (attachment_id,))
    
    attachment = cursor.fetchone()
    if not attachment:
        conn.close()
        flash('Attachment not found.', 'error')
        return redirect(url_for('dashboard'))

    access = _get_ticket_access(cursor, attachment[3], session.get('user_id'))
    if not _can_view_ticket(access, session.get('user_id'), session.get('role')):
        conn.close()
        abort(403)
    conn.close()

    original_filename = attachment[1]
    file_path = _resolve_attachment_path(attachment[2])
    if not file_path or not file_path.is_file():
        abort(404)

    return send_file(file_path, as_attachment=True, download_name=original_filename, conditional=True)

@app.route('/edit_user/<int:user_id>')
@admin_required
def edit_user(user_id):
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT u.*, d.name as department_name
    FROM users u
    LEFT JOIN departments d ON u.department_id = d.id
    WHERE u.id = ?
    """, (user_id,))
    user = cursor.fetchone()

    cursor.execute("SELECT * FROM departments ORDER BY name")
    departments = cursor.fetchall()

    conn.close()
    return render_template('edit_user.html', user=user, departments=departments)

@app.route('/update_user/<int:user_id>', methods=['POST'])
@admin_required
def update_user(user_id):
    username = request.form['username'].strip()
    full_name = request.form['full_name'].strip()
    email = request.form.get('email', '').strip()
    role = request.form.get('role', 'user')
    department_id = request.form.get('department_id') or None
    is_department_head = 1 if request.form.get('is_department_head') else 0

    password = request.form.get('password')

    if (
        not username
        or len(username) > 80
        or not full_name
        or len(full_name) > 120
        or (email and not valid_email(email))
        or role not in {'user', 'admin'}
    ):
        abort(400)

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT role FROM users WHERE id = ?", (user_id,))
    existing_user = cursor.fetchone()
    if not existing_user:
        conn.close()
        abort(404)
    if existing_user[0] == 'admin' and role != 'admin':
        cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
        if cursor.fetchone()[0] <= 1:
            conn.close()
            abort(409)

    try:
        if password:
            if len(password) < 8:
                flash('Password must be at least 8 characters.', 'error')
                conn.close()
                return redirect(url_for('edit_user', user_id=user_id))
            hashed_password = hash_password(password)
            cursor.execute("""
            UPDATE users
            SET username=?, password=?, full_name=?, email=?, role=?, department_id=?, is_department_head=?
            WHERE id=?
            """, (username, hashed_password, full_name, email, role, department_id, is_department_head, user_id))
        else:
            cursor.execute("""
            UPDATE users
            SET username=?, full_name=?, email=?, role=?, department_id=?, is_department_head=?
            WHERE id=?
            """, (username, full_name, email, role, department_id, is_department_head, user_id))

        conn.commit()
        flash('User updated successfully!', 'success')
    except Exception as e:
        logger.exception("Could not update user %s: %s", user_id, e)
        flash('Could not update the user.', 'error')
    finally:
        conn.close()

    return redirect(url_for('admin_panel'))

@app.route('/edit_category/<int:cat_id>', methods=['GET', 'POST'])
@admin_required
def edit_category(cat_id):
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    if request.method == 'POST':
        name = request.form['name']
        description = request.form.get('description', '')

        try:
            cursor.execute("""
            UPDATE categories SET name = ?, description = ? WHERE id = ?
            """, (name, description, cat_id))
            conn.commit()
            flash('Category updated successfully!', 'success')
        except Exception as e:
            flash(f'Error updating category: {str(e)}', 'error')
        finally:
            conn.close()
        return redirect(url_for('admin_panel'))

    cursor.execute("SELECT * FROM categories WHERE id = ?", (cat_id,))
    category = cursor.fetchone()
    conn.close()

    if not category:
        flash('Category not found!', 'error')
        return redirect(url_for('admin_panel'))

    return render_template('edit_category.html', category=category)

@app.route('/edit_department/<int:dept_id>', methods=['GET', 'POST'])
@admin_required
def edit_department(dept_id):
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    if request.method == 'POST':
        name = request.form['name']
        description = request.form.get('description', '')

        try:
            cursor.execute("""
            UPDATE departments SET name = ?, description = ? WHERE id = ?
            """, (name, description, dept_id))
            conn.commit()
            flash('Department updated successfully!', 'success')
        except Exception as e:
            flash(f'Error updating department: {str(e)}', 'error')
        finally:
            conn.close()
        return redirect(url_for('admin_panel'))

    cursor.execute("SELECT * FROM departments WHERE id = ?", (dept_id,))
    department = cursor.fetchone()
    conn.close()

    if not department:
        flash('Department not found!', 'error')
        return redirect(url_for('admin_panel'))

    return render_template('edit_department.html', department=department)

@app.route('/api/users')
@login_required
def api_users():
    query = request.args.get('query', '').strip()
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    if not query:
        conn.close()
        return jsonify([])

    like_query = f"%{query}%"
    cursor.execute("""
    SELECT full_name FROM users
    WHERE full_name LIKE ?
    ORDER BY full_name ASC
    LIMIT 10
    """, (like_query,))
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return jsonify(users)

@app.route('/reassign_ticket/<int:ticket_id>', methods=['POST'])
@login_required
def reassign_ticket(ticket_id):
    """Reassign a ticket as an admin, creator or current assignee."""
    user_id = session.get('user_id')
    role = session.get('role')

    new_assigned_to = request.form.get('new_assigned_to')
    if not new_assigned_to:
        flash('Please select a user to assign the ticket.', 'error')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    # Get ticket info
    cursor.execute("SELECT assigned_to, created_by FROM tickets WHERE id = ?", (ticket_id,))
    row = cursor.fetchone()
    if not row:
        flash('Ticket not found.', 'error')
        conn.close()
        return redirect(url_for('dashboard'))

    current_assigned_to_raw = row[0]
    
    # Convert current_assigned_to for permission checks (keep raw for 'IT' detection)
    if current_assigned_to_raw is not None and current_assigned_to_raw != 'IT':
        try:
            current_assigned_to = int(current_assigned_to_raw)
        except (ValueError, TypeError):
            current_assigned_to = None
    else:
        current_assigned_to = None
    
    access = _get_ticket_access(cursor, ticket_id, user_id)
    if not _can_manage_ticket(access, user_id, role):
        conn.close()
        abort(403)

    # ==== ASSIGN - Store NULL for 'IT', integer for users ====
    if new_assigned_to == 'IT':
        cursor.execute("""
        UPDATE tickets 
        SET assigned_to = 'IT',
        status = CASE WHEN status = 'new' THEN 'assigned' ELSE status END,
        updated_at = datetime('now', 'localtime')
        WHERE id = ?
        """, (ticket_id,))
        assigned_to_for_notification = 'IT'
    else:
        # Assign to specific user
        try:
            assigned_to_int = int(new_assigned_to)
            cursor.execute("SELECT 1 FROM users WHERE id = ?", (assigned_to_int,))
            if not cursor.fetchone():
                conn.close()
                abort(400)
            cursor.execute("""
            UPDATE tickets 
            SET assigned_to = ?,
            status = CASE WHEN status = 'new' THEN 'assigned' ELSE status END,
            updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """, (assigned_to_int, ticket_id))
            assigned_to_for_notification = assigned_to_int
        except ValueError:
            flash('Invalid user selected.', 'error')
            conn.close()
            return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    # Add as watcher
    cursor.execute("""
    INSERT OR IGNORE INTO ticket_watchers (ticket_id, user_id)
    VALUES (?, ?)
    """, (ticket_id, user_id))

    # === LOG ACTIVITY - GET ACTUAL NAMES ===
    # Get old assigned name
    if current_assigned_to_raw == 'IT' or current_assigned_to_raw is None:
        old_assigned_name = "IT (Unassigned)"
    else:
        # Only query if we have a valid integer id
        if current_assigned_to is not None:
            cursor.execute("SELECT full_name FROM users WHERE id = ?", (current_assigned_to,))
            old_user = cursor.fetchone()
            old_assigned_name = old_user[0] if old_user else f"User {current_assigned_to}"
        else:
            old_assigned_name = f"User {current_assigned_to_raw}"

    # Get new assigned name
    if new_assigned_to == 'IT':
        new_assigned_name = "IT (Unassigned)"
    else:
        cursor.execute("SELECT full_name FROM users WHERE id = ?", (assigned_to_for_notification,))
        new_user = cursor.fetchone()
        new_assigned_name = new_user[0] if new_user else f"User {assigned_to_for_notification}"

    conn.commit()
    conn.close()
    
    log_ticket_activity(
        ticket_id=ticket_id,
        user_id=user_id,
        action_type='reassigned',
        old_value=old_assigned_name,
        new_value=new_assigned_name
    )

    # Notification
    try:
        notify_on_reassigned(ticket_id, actor_user_id=user_id, new_assigned_to_id=assigned_to_for_notification)
    except Exception as e:
        print(f"Notification error: {e}")

    flash('Ticket reassigned successfully.', 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))

@app.route('/bulk_assign_it_tickets', methods=['POST'])
@admin_required
def bulk_assign_it_tickets():
    """Bulk assign all IT tickets to a specific admin"""
    assign_to = request.form.get('assign_to')
    
    if not assign_to:
        flash('Please select an admin to assign tickets to.', 'error')
        return redirect(url_for('dashboard'))
    
    try:
        assign_to_id = int(assign_to)
    except ValueError:
        flash('Invalid user selected.', 'error')
        return redirect(url_for('dashboard'))
    
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT full_name FROM users WHERE id = ? AND role = 'admin'", (assign_to_id,))
    admin = cursor.fetchone()
    if not admin:
        conn.close()
        abort(400)

    try:
        # Get all IT assigned tickets that are not closed
        cursor.execute("""
        SELECT id FROM tickets 
        WHERE (assigned_to = 'IT' OR assigned_to IS NULL) AND status != 'closed'
        """)
        it_tickets = cursor.fetchall()
        
        if not it_tickets:
            flash('No IT tickets found to assign.', 'info')
            conn.close()
            return redirect(url_for('dashboard'))
        
        # Assign all to selected admin
        ticket_ids = [ticket[0] for ticket in it_tickets]
        placeholders = ','.join('?' * len(ticket_ids))
        
        # The IN list contains one placeholder per integer ID loaded from the database.
        cursor.execute(f"""
        UPDATE tickets 
        SET assigned_to = ?, 
        status = CASE WHEN status = 'new' THEN 'assigned' ELSE status END,
        updated_at = datetime('now', 'localtime')
        WHERE id IN ({placeholders})
        """, [assign_to_id] + ticket_ids)
        
        conn.commit()
        
        # Get assigned admin name
        admin_name = admin[0]
        
        flash(f'Successfully assigned {len(ticket_ids)} IT ticket(s) to {admin_name}!', 'success')
        
        # Send notifications
        for ticket_id in ticket_ids:
            try:
                notify_on_assigned(ticket_id, actor_user_id=session.get('user_id'), assigned_to_id=assign_to_id)
            except:
                pass
        
    except Exception as e:
        flash(f'Error assigning tickets: {str(e)}', 'error')
    finally:
        conn.close()
    
    return redirect(url_for('dashboard'))

@app.route('/add_watchers/<int:ticket_id>', methods=['POST'])
@login_required
def add_watchers(ticket_id):
    user_id = session.get('user_id')
    role = session.get('role')

    watchers_input = request.form.get('watchers', '').strip()
    if not watchers_input:
        flash('Please enter at least one watcher.', 'error')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    access = _get_ticket_access(cursor, ticket_id, user_id)
    if not access:
        flash('Ticket not found.', 'error')
        conn.close()
        return redirect(url_for('dashboard'))
    if not _can_manage_ticket(access, user_id, role):
        conn.close()
        abort(403)

    watcher_names = [w.strip() for w in watchers_input.split(',') if w.strip()]
    added_watchers = []
    added_watchers_ids = []
    for watcher_name in watcher_names:
        cursor.execute("SELECT id, full_name FROM users WHERE full_name = ?", (watcher_name,))
        watcher = cursor.fetchone()
        if watcher:
            watcher_id = watcher[0]
            cursor.execute("INSERT OR IGNORE INTO ticket_watchers (ticket_id, user_id) VALUES (?, ?)", (ticket_id, watcher_id))
            added_watchers.append(watcher_name)
            added_watchers_ids.append(watcher_id)
        else:
            flash(f'Watcher user "{watcher_name}" does not exist.', 'error')

    conn.commit()
    conn.close()

    if added_watchers:
        flash(f'Successfully added {len(added_watchers)} watcher(s): {", ".join(added_watchers)}', 'success')

    return redirect(url_for('ticket_detail', ticket_id=ticket_id))

@app.route('/remove_watcher/<int:ticket_id>', methods=['POST'])
@login_required
def remove_watcher(ticket_id):
    user_id = session.get('user_id')
    watcher_id_to_remove = request.form.get('watcher_id')
    
    if not watcher_id_to_remove:
        flash('Invalid watcher.', 'error')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    
    try:
        watcher_id_to_remove = int(watcher_id_to_remove)
    except ValueError:
        flash('Invalid watcher ID.', 'error')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    access = _get_ticket_access(cursor, ticket_id, user_id)
    if not access:
        conn.close()
        flash('Ticket not found.', 'error')
        return redirect(url_for('dashboard'))

    if watcher_id_to_remove != user_id and not _can_manage_ticket(
        access, user_id, session.get('role')
    ):
        conn.close()
        abort(403)
    
    cursor.execute("""
    DELETE FROM ticket_watchers 
    WHERE ticket_id = ? AND user_id = ?
    """, (ticket_id, watcher_id_to_remove))
    
    conn.commit()
    conn.close()
    
    log_activity(user_id, 'WATCHER_REMOVED', f'Ticket ID: {ticket_id}, Watcher ID: {watcher_id_to_remove}')
    flash('Watcher removed successfully!', 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))

def log_activity(user_id, action, details=None):
    try:
        db = Database()
        conn = db.get_connection()
        cursor = conn.cursor()
        ip_address = request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR', 'Unknown'))
        cursor.execute("""
        INSERT INTO activity_logs (user_id, action, details, ip_address)
        VALUES (?, ?, ?, ?)
        """, (user_id, action, details, ip_address))
        conn.commit()
        conn.close()
    except Exception:
        pass

@app.route('/ticket/<int:ticket_id>/mute', methods=['POST'])
@login_required
def mute_ticket(ticket_id):
    user_id = session.get('user_id')
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    access = _get_ticket_access(cursor, ticket_id, user_id)
    if not access:
        conn.close()
        abort(404)
    if not _can_view_ticket(access, user_id, session.get('role')) or not access['is_watcher']:
        conn.close()
        abort(403)
    
    # Dodaj korisnika u muted listu
    cursor.execute("""
    INSERT OR IGNORE INTO ticket_muted_users (ticket_id, user_id)
    VALUES (?, ?)
    """, (ticket_id, user_id))
    
    conn.commit()
    conn.close()
    
    flash('Notifications muted for this ticket.', 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))


@app.route('/ticket/<int:ticket_id>/unmute', methods=['POST'])
@login_required
def unmute_ticket(ticket_id):
    user_id = session.get('user_id')
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    access = _get_ticket_access(cursor, ticket_id, user_id)
    if not access:
        conn.close()
        abort(404)
    if not _can_view_ticket(access, user_id, session.get('role')):
        conn.close()
        abort(403)
    
    # Ukloni korisnika iz muted liste
    cursor.execute("""
    DELETE FROM ticket_muted_users
    WHERE ticket_id=? AND user_id=?
    """, (ticket_id, user_id))
    
    conn.commit()
    conn.close()
    
    flash('Notifications unmuted for this ticket.', 'info')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))


@app.route('/admin/logs')
@admin_required
def view_logs():
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    cursor.execute("""
    SELECT al.*, u.full_name 
    FROM activity_logs al
    LEFT JOIN users u ON al.user_id = u.id
    ORDER BY al.created_at DESC
    LIMIT ? OFFSET ?
    """, (per_page, offset))
    logs = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) FROM activity_logs")
    total = cursor.fetchone()[0]

    conn.close()

    return render_template('admin_logs.html', logs=logs, page=page, total=total, per_page=per_page)

@app.route('/search')
@login_required
def search():
    query = request.args.get('q', '').strip()
    if not query:
        return redirect(url_for('dashboard'))

    user_id = session.get('user_id')
    role = session.get('role')

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    search_term = f"%{query}%"

    if role == 'admin':
        cursor.execute("""
        SELECT DISTINCT t.id, t.title, t.priority, t.status, t.created_at, t.updated_at, 
        t.created_by, t.assigned_to, t.due_date,
        c.name as category_name, u.full_name as created_by_name,
        CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name
        FROM tickets t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN users u ON t.created_by = u.id
        LEFT JOIN users a ON t.assigned_to = a.id
        LEFT JOIN comments cm ON t.id = cm.ticket_id
        WHERE (t.title LIKE ? OR t.description LIKE ? OR cm.comment LIKE ?)
        ORDER BY t.updated_at DESC
        """, (search_term, search_term, search_term))
    else:
        cursor.execute("""
        SELECT DISTINCT t.id, t.title, t.priority, t.status, t.created_at, t.updated_at, 
        t.created_by, t.assigned_to, t.due_date,
        c.name as category_name, u.full_name as created_by_name,
        CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name
        FROM tickets t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN users u ON t.created_by = u.id
        LEFT JOIN users a ON t.assigned_to = a.id
        LEFT JOIN comments cm ON t.id = cm.ticket_id
        LEFT JOIN ticket_watchers tw ON t.id = tw.ticket_id
        WHERE (t.title LIKE ? OR t.description LIKE ? OR cm.comment LIKE ?)
        AND (t.created_by = ? OR t.assigned_to = ? OR tw.user_id = ?)
        ORDER BY t.updated_at DESC
        """, (search_term, search_term, search_term, user_id, user_id, user_id))

    tickets = cursor.fetchall()
    conn.close()

    log_activity(user_id, 'SEARCH', f'Query: {query}')

    return render_template('search_results.html', tickets=tickets, query=query)

@app.route('/save_template', methods=['POST'])
@login_required
def save_template():
    user_id = session.get('user_id')
    name = request.form.get('template_name')
    title = request.form.get('title')
    description = request.form.get('description')
    priority = request.form.get('priority')
    category_id = request.form.get('category_id')

    if not name or not title or not description:
        flash('Template name, title and description are required.', 'error')
        return redirect(url_for('create_ticket'))

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    INSERT INTO ticket_templates (user_id, name, title, description, priority, category_id)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, name, title, description, priority, category_id))

    conn.commit()
    conn.close()

    log_activity(user_id, 'TEMPLATE_SAVED', f'Template: {name}')
    flash('Template saved successfully!', 'success')
    return redirect(url_for('create_ticket'))

@app.route('/load_template/<int:template_id>')
@login_required
def load_template(template_id):
    user_id = session.get('user_id')

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT * FROM ticket_templates 
    WHERE id = ? AND user_id = ?
    """, (template_id, user_id))

    template = cursor.fetchone()
    conn.close()

    if not template:
        flash('Template not found.', 'error')
        return redirect(url_for('create_ticket'))

    log_activity(user_id, 'TEMPLATE_LOADED', f'Template: {template[2]}')

    return render_template(
        'create_ticket.html',
        categories=get_categories(),
        users=get_users(),
        it_admins=get_it_admins(),
        templates=get_user_templates(user_id),
        form={
            'title': template[3],
            'description': template[4],
            'priority': template[5],
            'category_id': template[6]
        }
    )

@app.route('/delete_template/<int:template_id>', methods=['POST'])
@login_required
def delete_template(template_id):
    user_id = session.get('user_id')

    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    DELETE FROM ticket_templates 
    WHERE id = ? AND user_id = ?
    """, (template_id, user_id))

    conn.commit()
    conn.close()

    log_activity(user_id, 'TEMPLATE_DELETED', f'Template ID: {template_id}')
    flash('Template deleted successfully!', 'success')
    return redirect(url_for('create_ticket'))

def get_user_templates(user_id):
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT * FROM ticket_templates 
    WHERE user_id = ? 
    ORDER BY name
    """, (user_id,))
    templates = cursor.fetchall()
    conn.close()
    return templates

if __name__ == '__main__':
    app.run(
        debug=False,
        host=os.getenv('HOST', '127.0.0.1'),
        port=int(os.getenv('PORT', '5000')),
    )
