from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
import os
from werkzeug.utils import secure_filename
from models import UserModel, TicketModel, CategoryModel
from auth import login_required, admin_required
from database import Database
import sys
import sqlite3
import pandas as pd
import hashlib
from datetime import datetime, timedelta
import time

from dotenv import load_dotenv
load_dotenv()

import logging
logging.basicConfig(level=logging.INFO)  # Dodaj ovo na vrhu fajla

app = Flask(__name__)

app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-insecure-only-change-me")

# Na localhost-u koristimo HTTP, pa Secure mora biti False
IS_HTTPS = (os.getenv("IS_HTTPS", "false").strip().lower() == "true")

app.config['UPLOAD_FOLDER'] = 'static/uploads'

def get_upload_path():
    """Generate upload path organized by year/month"""
    now = datetime.now()
    year_month = now.strftime('%Y/%m')
    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], year_month)
    os.makedirs(upload_path, exist_ok=True)
    return upload_path

app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB

app.config.update(
    SESSION_COOKIE_SECURE=IS_HTTPS,    # False na localhost
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=("None" if IS_HTTPS else "Lax"),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=1),
    SESSION_REFRESH_EACH_REQUEST=True,    # “rolling” obnavljanje
)



sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Notifications
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

# Initialize models
user_model = UserModel()
ticket_model = TicketModel()
category_model = CategoryModel()


def _ensure_upload_dir():
    try:
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    except Exception:
        pass


@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        user = user_model.authenticate(username, password)

        if user:
            session.clear()
            session.permanent = True
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['full_name'] = user[4]
            session['role'] = user[5]
            session['department_id'] = user[6]
            session['is_department_head'] = user[7]
            session['_fresh'] = True
            session['last_activity'] = time.time()
            flash(f"Welcome, {user[4]}!", 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password.', 'error')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

from werkzeug.security import check_password_hash, generate_password_hash
import re

def valid_email(email):
    if not email:
        return True
    # very simple email pattern
    return re.match(r"^[^@\s]+@[^@\s]+.[^@\s]+$", email) is not None

def get_departments():
    conn = Database().get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM departments ORDER BY name")
    departments = cursor.fetchall()
    conn.close()
    return departments

@app.route('/my_profile', methods=['GET', 'POST'])
@login_required
def my_profile():
    user_id = session.get('user_id')

    conn = Database().get_connection()
    cursor = conn.cursor()

    # Učitaj podatke o korisniku
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

    # Departments (za drop-down)
    departments = get_departments()

    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip()
        department_id_raw = request.form.get('department_id')  # može biti None
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()

        # Validacije
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

        # Department: dozvoljeno menjanje (po tvom zahtevu)
        department_id = None
        if department_id_raw and department_id_raw.strip() != '':
            try:
                department_id = int(department_id_raw)
            except ValueError:
                flash('Invalid department.', 'error')
                return render_template('my_profile.html',
                                    user=user,
                                    departments=departments)

        # Password: opciono
        update_password = False
        hashed_password = user[2]  # stari hash iz baze
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
            # Zadrži SHA-256 koji već koristiš u importu i dodavanju korisnika
            hashed_password = hashlib.sha256(new_password.encode()).hexdigest()
            update_password = True

        # Update Prepared
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

        # Refresh session (ako menja full_name ili department)
        session['full_name'] = full_name
        session['department_id'] = department_id

        # GDPR log (ako koristiš)
        try:
            log_activity(user_id, 'PROFILE_UPDATED', f'User updated own profile (full_name/email/department/password)')
        except:
            pass

        conn.close()
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('my_profile'))

    # GET
    conn.close()
    return render_template('my_profile.html',
                        user=user,
                        departments=departments)

@app.route('/dashboard')
@login_required
def dashboard():
    role = session.get('role')
    user_id = session.get('user_id')

    current_filter = request.args.get('filter', 'active')
    assigned_filter = request.args.get('assigned', 'me')
    sort_param = request.args.get('sort', 'updated_at_desc')

    sort_options = {
        'updated_at_desc': 't.updated_at DESC',
        'updated_at_asc': 't.updated_at ASC',
        'created_at_desc': 't.created_at DESC',
        'created_at_asc': 't.created_at ASC',
        'priority_desc': "CASE t.priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END ASC",
        'priority_asc': "CASE t.priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END DESC",
        'due_date_desc': 'CASE WHEN t.due_date IS NULL OR t.due_date = "" THEN 0 ELSE 1 END DESC, t.due_date DESC',
        'due_date_asc': 'CASE WHEN t.due_date IS NULL OR t.due_date = "" THEN 0 ELSE 1 END DESC, t.due_date ASC'
    }
    order_by = sort_options.get(sort_param, 't.updated_at DESC')

    conn = Database().get_connection()
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

    if role != 'admin':
        # Assigned to me
        query_assigned = f"""
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
        cursor.execute(query_assigned, (user_id,))
        tickets_assigned_to_me = cursor.fetchall()

        # My created
        query_created = f"""
            SELECT t.id, t.title, t.priority, t.status, t.created_at, t.updated_at, t.created_by, t.assigned_to, t.due_date,
                   c.name as category_name, u.full_name as created_by_name,
                   CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name
            FROM tickets t
            LEFT JOIN categories c ON t.category_id = c.id
            LEFT JOIN users u ON t.created_by = u.id
            LEFT JOIN users a ON t.assigned_to = a.id
            WHERE t.created_by = ? AND {status_condition}
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

        # Assigned to me
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

        # My created (admins)
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

        # Tickets per admin
        for admin_user in it_admins:
            query_by_admin = f"""
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
            cursor.execute(query_by_admin, (admin_user[0],))
            tickets_by_admin[admin_user[0]] = cursor.fetchall()

        # Closed tickets per admin (only when filter closed)
        if current_filter == 'closed':
            for admin_user in it_admins:
                query_closed_by_admin = """
                    SELECT t.id, t.title, t.priority, t.status, t.created_at, t.updated_at, t.created_by, t.assigned_to, t.due_date,
                           c.name as category_name, u.full_name as created_by_name,
                           CASE WHEN t.assigned_to = 'IT' THEN 'IT' ELSE a.full_name END as assigned_to_name
                    FROM tickets t
                    LEFT JOIN categories c ON t.category_id = c.id
                    LEFT JOIN users u ON t.created_by = u.id
                    LEFT JOIN users a ON t.assigned_to = a.id
                    WHERE t.assigned_to = ? AND t.status = 'closed'
                    ORDER BY t.updated_at DESC
                """
                cursor.execute(query_closed_by_admin, (admin_user[0],))
                tickets_closed_by_admin[admin_user[0]] = cursor.fetchall()

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
        tickets_closed_by_admin=tickets_closed_by_admin
    )


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

            # Parse assigned_to properly: int user_id, "IT" string, or None
            assigned_to_raw = request.form.get('assigned_to')
            assigned_to_db = None
            if assigned_to_raw and assigned_to_raw.strip() != '':
                try:
                    assigned_to_db = int(assigned_to_raw)
                except ValueError:
                    # Allow special label "IT" to pass through as string (if used in your UI)
                    assigned_to_db = assigned_to_raw.strip()

            watchers_input = request.form.get('watchers', '').strip()

            if not title or not description or not priority or not category_id:
                flash('Please fill in all required fields.', 'error')
                return render_template(
                    'create_ticket.html',
                    categories=get_categories(),
                    users=get_users(),
                    it_admins=get_it_admins(),
                    templates=get_user_templates(user_id),
                    form=request.form
                )

            cursor.execute("""
                INSERT INTO tickets (title, description, priority, category_id, due_date, created_by, assigned_to, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'new', datetime('now', 'localtime'), datetime('now', 'localtime'))
            """, (title, description, priority, category_id, due_date, user_id, assigned_to_db))
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

            # Attachments
            if 'attachments' in request.files:
                files = request.files.getlist('attachments')
                _ensure_upload_dir()
                for file in files:
                    if file and file.filename:
                        import uuid
                        _, ext = os.path.splitext(file.filename)
                        unique_filename = f"{ticket_id}_{uuid.uuid4().hex}{ext}"
                        upload_path = get_upload_path()
                        file_path = os.path.join(upload_path, unique_filename)
                        file.save(file_path)
                        # Store relative path in database
                        relative_path = os.path.join(upload_path.replace('static/', ''), unique_filename)
                        cursor.execute("""
                        INSERT INTO attachments (ticket_id, filename, original_filename, file_path, uploaded_by)
                        VALUES (?, ?, ?, ?, ?)
                        """, (ticket_id, unique_filename, file.filename, relative_path, user_id))
                conn.commit()

            log_activity(user_id, 'TICKET_CREATED', f'Ticket ID: {ticket_id}, Title: {title}')

            # Notify: only assignee or IT mailbox, per notifications.py routing
            notify_on_ticket_created(ticket_id, actor_user_id=user_id)

            flash('Ticket created successfully.', 'success')
            return redirect(url_for('dashboard'))

        # GET
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
    conn = Database().get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM categories ORDER BY name")
    categories = cursor.fetchall()
    conn.close()
    return categories


def get_users():
    conn = Database().get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, full_name FROM users WHERE role != 'admin' ORDER BY full_name")
    users = cursor.fetchall()
    conn.close()
    return users


def get_it_admins():
    conn = Database().get_connection()
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
        SELECT t.*, c.name as category_name, u.full_name as created_by_name, a.full_name as assigned_to_name
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

    # Access check: creator, assignee or watcher, or admin
    if role != 'admin' and user_id != ticket[6] and user_id != ticket[7]:
        cursor.execute("SELECT 1 FROM ticket_watchers WHERE ticket_id = ? AND user_id = ?", (ticket_id, user_id))
        watcher_check = cursor.fetchone()
        if not watcher_check:
            flash('You do not have permission to view this ticket.', 'error')
            conn.close()
            return redirect(url_for('dashboard'))

    # Watchers (with IDs)
    cursor.execute("""
    SELECT u.id, u.full_name FROM users u
    JOIN ticket_watchers tw ON u.id = tw.user_id
    WHERE tw.ticket_id = ?
    """, (ticket_id,))
    watchers = cursor.fetchall()  # List of tuples: [(id, full_name), ...]

    # Comments
    cursor.execute("""
        SELECT c.id, c.comment, c.created_at, u.full_name
        FROM comments c
        JOIN users u ON c.user_id = u.id
        WHERE c.ticket_id = ?
        ORDER BY c.created_at DESC
    """, (ticket_id,))
    comments = cursor.fetchall()

    # Attachments
    cursor.execute("""
        SELECT a.id, a.filename, a.original_filename, a.created_at, a.uploaded_by, u.full_name
        FROM attachments a
        LEFT JOIN users u ON a.uploaded_by = u.id
        WHERE a.ticket_id = ?
        ORDER BY a.created_at DESC
    """, (ticket_id,))
    attachments = cursor.fetchall()

    # Users (non-admins)
    cursor.execute("SELECT id, full_name FROM users WHERE role != 'admin' ORDER BY full_name")
    users = cursor.fetchall()

    can_start_work = False
    can_confirm = False
    if role == 'admin' and ticket[5] in ['assigned', 'new']:
        can_start_work = True
    if user_id == ticket[6] and ticket[5] == 'awaiting_confirmation':
        can_confirm = True

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
        all_users=users  # Add this line
    )


@app.route('/update_ticket_status', methods=['POST'])
@login_required
def update_ticket_status():
    ticket_id = request.form['ticket_id']
    new_status = request.form['status']
    actor_id = session.get('user_id')

    conn = Database().get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM tickets WHERE id=?", (ticket_id,))
    row = cursor.fetchone()
    old_status = row[0] if row else None
    conn.close()

    ticket_model.update_ticket_status(ticket_id, new_status)

    # Notify after status change
    if old_status is not None:
        notify_on_status_change(ticket_id, old_status, new_status, actor_user_id=actor_id)

    flash('Ticket status updated!', 'success')
    return redirect(url_for('dashboard'))


@app.route('/admin')
@admin_required
def admin_panel():
    conn = Database().get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM categories ORDER BY name")
    categories = cursor.fetchall()

    cursor.execute("SELECT * FROM departments ORDER BY name")
    departments = cursor.fetchall()

    cursor.execute("""
        SELECT u.id, u.username, u.password, u.email, u.full_name, u.role, 
               u.department_id, u.is_department_head, u.created_at, d.name as department_name
        FROM users u
        LEFT JOIN departments d ON u.department_id = d.id
        ORDER BY u.full_name
    """)
    users = cursor.fetchall()

    # Count tickets
    cursor.execute("SELECT COUNT(*) FROM tickets")
    ticket_count = cursor.fetchone()[0]

    conn.close()

    return render_template('admin_panel.html', 
                         categories=categories, 
                         departments=departments, 
                         users=users,
                         tickets=[None] * ticket_count)  # Dummy list for count


@app.route('/add_category', methods=['POST'])
@admin_required
def add_category():
    name = request.form['name']
    description = request.form.get('description', '')

    conn = Database().get_connection()
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
    conn = Database().get_connection()
    cursor = conn.cursor()

    try:
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
    conn = Database().get_connection()
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
    conn = Database().get_connection()
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

    conn = Database().get_connection()
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

    conn = Database().get_connection()
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
    
    conn = Database().get_connection()
    cursor = conn.cursor()
    
    # Get ticket info
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
    
    # Check if ticket is closed
    if status != 'closed':
        conn.close()
        flash('Only closed tickets can be reopened.', 'error')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    
    # Permission check: creator, assignee, watcher, or admin
    can_reopen = False
    if role == 'admin' or user_id == created_by or user_id == assigned_to:
        can_reopen = True
    else:
        # Check if user is watcher
        cursor.execute("SELECT 1 FROM ticket_watchers WHERE ticket_id = ? AND user_id = ?", (ticket_id, user_id))
        if cursor.fetchone():
            can_reopen = True
    
    if not can_reopen:
        conn.close()
        flash('You do not have permission to reopen this ticket.', 'error')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    
    # Reopen ticket (set status to 'in_progress')
    cursor.execute("""
        UPDATE tickets 
        SET status = 'in_progress', updated_at = datetime('now', 'localtime')
        WHERE id = ?
    """, (ticket_id,))
    
    # Add comment about reopening
    cursor.execute("""
        INSERT INTO comments (ticket_id, user_id, comment)
        VALUES (?, ?, ?)
    """, (ticket_id, user_id, '🔄 Ticket reopened'))
    
    conn.commit()
    conn.close()
    
    # Notify about reopening
    notify_on_status_change(ticket_id, 'closed', 'in_progress', actor_user_id=user_id)
    
    log_activity(user_id, 'TICKET_REOPENED', f'Ticket ID: {ticket_id}')
    flash('Ticket reopened successfully!', 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))

@app.route('/delete_attachment/<int:attachment_id>', methods=['POST'])
@login_required
def delete_attachment(attachment_id):
    user_id = session.get('user_id')
    role = session.get('role')
    
    conn = Database().get_connection()
    cursor = conn.cursor()
    
    # Get attachment info
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
    
    # Permission check: only uploader, ticket creator, or admin can delete
    if role != 'admin' and user_id != uploaded_by and user_id != ticket_creator:
        conn.close()
        flash('You do not have permission to delete this attachment.', 'error')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
    
    # Delete from database
    cursor.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
    conn.commit()
    conn.close()
    
    # Delete file from filesystem
    try:
        # Convert to absolute path if needed
        if not os.path.isabs(file_path):
            file_path = os.path.join(os.path.abspath(app.config['UPLOAD_FOLDER']), os.path.basename(file_path))
        
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        flash(f'Error deleting file from server: {str(e)}', 'warning')
    
    log_activity(user_id, 'ATTACHMENT_DELETED', f'Attachment ID: {attachment_id}, Ticket ID: {ticket_id}')
    flash('Attachment deleted successfully!', 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))

@app.route('/get_all_users')
@login_required
def get_all_users():
    """Return all users as JSON for autocomplete"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT full_name FROM users ORDER BY full_name')
    users = cursor.fetchall()
    conn.close()
    
    user_names = [user[0] for user in users]
    return jsonify({'users': user_names})

@app.route('/add_comment', methods=['POST'])
@login_required
def add_comment():
    ticket_id = request.form['ticket_id']
    comment = request.form['comment']
    user_id = session.get('user_id')

    conn = Database().get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO comments (ticket_id, user_id, comment)
        VALUES (?, ?, ?)
    """, (ticket_id, user_id, comment))

    cursor.execute("""
        UPDATE tickets
        SET updated_at = datetime('now', 'localtime')
        WHERE id = ?
    """, (ticket_id,))

    log_activity(user_id, 'COMMENT_ADDED', f'Ticket ID: {ticket_id}')
    conn.commit()
    conn.close()

    # Notify
    notify_on_comment(ticket_id, actor_user_id=user_id, comment_preview=comment)

    flash('Comment added successfully!', 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))


@app.route('/upload_attachment', methods=['POST'])
@login_required
def upload_attachment():
    ticket_id = request.form['ticket_id']
    user_id = session.get('user_id')

    if 'file' not in request.files:
        flash('No file selected!', 'error')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    file = request.files['file']
    if file.filename == '':
        flash('No file selected!', 'error')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    try:
        _ensure_upload_dir()
        import uuid
        _, ext = os.path.splitext(file.filename)
        unique_filename = f"{ticket_id}_{uuid.uuid4().hex}{ext}"
        upload_path = get_upload_path()
        file_path = os.path.join(upload_path, unique_filename)
        file.save(file_path)
        # Store relative path in database
        relative_path = os.path.join(upload_path.replace('static/', ''), unique_filename)

        conn = Database().get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO attachments (ticket_id, filename, original_filename, file_path, uploaded_by)
        VALUES (?, ?, ?, ?, ?)
        """, (ticket_id, unique_filename, file.filename, relative_path, user_id))
        conn.commit()
        conn.close()

        # Notify
        notify_on_attachment(ticket_id, actor_user_id=user_id, filename=file.filename)

        flash('File uploaded successfully!', 'success')
    except Exception as e:
        flash(f'Error uploading file: {str(e)}', 'error')

    return redirect(url_for('ticket_detail', ticket_id=ticket_id))


@app.route('/import_users', methods=['GET', 'POST'])
@admin_required
def import_users():
    if request.method == 'GET':
        conn = Database().get_connection()
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

    try:
        df = pd.read_excel(file)

        conn = Database().get_connection()
        cursor = conn.cursor()

        imported_count = 0
        errors = []

        for index, row in df.iterrows():
            try:
                username = row['username']
                password = hashlib.sha256(str(row['password']).encode()).hexdigest()
                full_name = row['full_name']
                email = row.get('email', '')
                department_name = row.get('department_name', '')
                role = row.get('role', 'user')

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
                    INSERT INTO users (username, password, full_name, email, department_name, department_id, role)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (username, password, full_name, email, department_name, department_id, role))

                imported_count += 1
            except Exception as e:
                errors.append(f"Row {index + 2}: {str(e)}")

        conn.commit()
        conn.close()

        if imported_count > 0:
            flash(f'Successfully imported {imported_count} users!', 'success')
        if errors:
            flash(f'Errors: {"; ".join(errors[:5])}', 'warning')

    except Exception as e:
        flash(f'Error reading Excel file: {str(e)}', 'error')

    return redirect(url_for('admin_panel'))


@app.route('/add_user_manual', methods=['POST'])
@admin_required
def add_user_manual():
    username = request.form['username']
    password = request.form['password']
    full_name = request.form['full_name']
    email = request.form.get('email', '')
    department_id = request.form.get('department_id')
    role = request.form.get('role', 'user')
    is_department_head = 1 if request.form.get('is_department_head') else 0

    # Convert empty string to None for department_id
    if department_id == '':
        department_id = None

    conn = Database().get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        existing_user = cursor.fetchone()
        if existing_user:
            flash(f'Username "{username}" already exists! Please choose a different username.', 'error')
            conn.close()
            return redirect(url_for('admin_panel'))

        hashed_password = hashlib.sha256(password.encode()).hexdigest()
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

@app.route('/debug_users')
@admin_required
def debug_users():
    conn = Database().get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT u.id, u.username, u.full_name, u.department_id, d.name as dept_name
        FROM users u
        LEFT JOIN departments d ON u.department_id = d.id
        ORDER BY u.id
    """)
    users = cursor.fetchall()
    conn.close()
    
    output = "<h2>Users Debug</h2><table border='1'><tr><th>ID</th><th>Username</th><th>Full Name</th><th>Dept ID</th><th>Dept Name</th></tr>"
    for user in users:
        output += f"<tr><td>{user[0]}</td><td>{user[1]}</td><td>{user[2]}</td><td>{user[3]}</td><td>{user[4]}</td></tr>"
    output += "</table>"
    
    return output

@app.route('/create_it_admins')
@admin_required
def create_it_admins():
    try:
        conn = Database().get_connection()
        cursor = conn.cursor()

        it_admins = [
            ('it.admin1', 'change-me', 'IT Admin 1', 'admin1@example.com'),
            ('milos.radojkovic', 'change-me', 'Miloš R.', 'milos@company.com'),
            ('it.admin2', 'change-me', 'IT Admin 2', 'admin2@example.com')
        ]

        for username, password, full_name, email in it_admins:
            cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
            if not cursor.fetchone():
                hashed = hashlib.sha256(password.encode()).hexdigest()
                cursor.execute("""
                    INSERT INTO users (username, password, full_name, email, role, department_name, department_id, is_department_head)
                    VALUES (?, ?, ?, ?, ?, 'admin', NULL, 0)
                """, (username, hashed, full_name, email))

        conn.commit()
        conn.close()

        flash('IT Admins created successfully! (password: change-me)', 'success')
    except Exception as e:
        flash(f'Error creating IT admins: {str(e)}', 'error')

    return redirect(url_for('admin_panel'))


@app.route('/assign_department', methods=['POST'])
@admin_required
def assign_department():
    user_id = request.form['user_id']
    department_id = request.form.get('department_id') or None

    conn = Database().get_connection()
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

        if request.method == 'POST':
            cursor.execute("SELECT created_by, assigned_to FROM tickets WHERE id = ?", (ticket_id,))
            row = cursor.fetchone()
            if not row:
                flash('Ticket not found.', 'error')
                conn.close()
                return redirect(url_for('dashboard'))

            created_by = row[0]
            assigned_to = row[1]

            if user_id == created_by or role == 'admin':
                title = request.form.get('title')
                description = request.form.get('description')
                priority = request.form.get('priority')
                category_id = request.form.get('category_id')
                due_date = request.form.get('due_date')
                assigned_to_form = request.form.get('assigned_to') or None
                watchers_input = request.form.get('watchers', '').strip()

                if not title or not description or not priority or not category_id:
                    flash('Please fill in all required fields.', 'error')
                    return render_template(
                        'edit_ticket.html',
                        ticket=ticket,
                        categories=get_categories(),
                        users=get_users(),
                        it_admins=get_it_admins(),
                        watchers=request.form.get('watchers', ''),
                        form=request.form
                    )

                cursor.execute("""
                    UPDATE tickets SET title = ?, description = ?, priority = ?, category_id = ?, due_date = ?, assigned_to = ?, updated_at = datetime('now', 'localtime')
                    WHERE id = ?
                """, (title, description, priority, category_id, due_date, assigned_to_form, ticket_id))
                conn.commit()

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
                            flash(f'Watcher user "{watcher_name}" does not exist.', 'error')
                conn.commit()

                # Notify watchers newly added (only those)
                if added_watchers_ids:
                    notify_on_watchers_added(ticket_id, actor_user_id=user_id, watcher_user_ids=added_watchers_ids)

                flash('Ticket updated successfully.', 'success')
                conn.close()
                return redirect(url_for('ticket_detail', ticket_id=ticket_id))

            elif user_id == assigned_to:
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
                            flash(f'Watcher user "{watcher_name}" does not exist.', 'error')
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

        # GET
        cursor.execute("""
            SELECT u.full_name FROM users u
            JOIN ticket_watchers tw ON u.id = tw.user_id
            WHERE tw.ticket_id = ?
        """, (ticket_id,))
        watchers = [row[0] for row in cursor.fetchall()]
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
        flash(f'Error updating ticket: {str(e)}', 'error')
        return render_template(
            'edit_ticket.html',
            ticket=ticket,
            categories=get_categories(),
            users=get_users(),
            it_admins=get_it_admins(),
            watchers=request.form.get('watchers', ''),
            form=request.form
        )


@app.route('/manage_department_users/<int:dept_id>')
@admin_required
def manage_department_users(dept_id):
    conn = Database().get_connection()
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

    conn = Database().get_connection()
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

    conn = Database().get_connection()
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
    conn = Database().get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM tickets WHERE id = ? AND created_by = ?", (ticket_id, session.get('user_id')))
    ticket = cursor.fetchone()

    if ticket:
        cursor.execute("UPDATE tickets SET status = 'closed', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (ticket_id,))
        conn.commit()
        conn.close()

        # Notify
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

    # Check if user is admin or watcher
    conn = Database().get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT COUNT(*) FROM ticket_watchers 
        WHERE ticket_id = ? AND user_id = ?
    """, (ticket_id, current_user_id))
    is_watcher = cursor.fetchone()[0] > 0
    
    if user_role != 'admin' and not is_watcher:
        flash('Access denied.', 'error')
        conn.close()
        return redirect(url_for('dashboard'))

    assigned_to_db = None
    if assigned_to and assigned_to.strip() != '':
        try:
            assigned_to_db = int(assigned_to)
        except ValueError:
            flash('Invalid assigned user.', 'error')
            conn.close()
            return redirect(url_for('dashboard'))

    cursor.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,))
    row = cursor.fetchone()
    if not row:
        flash('Ticket not found.', 'error')
        conn.close()
        return redirect(url_for('dashboard'))

    current_status = row[0]
    new_status = 'assigned' if current_status == 'new' and assigned_to_db is not None else current_status

    cursor.execute("""
        UPDATE tickets
        SET assigned_to = ?, status = ?
        WHERE id = ?
    """, (assigned_to_db, new_status, ticket_id))

    conn.commit()
    conn.close()

    # Notify
    notify_on_assigned(ticket_id, actor_user_id=current_user_id, assigned_to_id=assigned_to_db)

    flash('Ticket assignment updated.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/download_attachment/<int:attachment_id>')
@login_required
def download_attachment(attachment_id):
    conn = Database().get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, ticket_id, filename, original_filename, file_path, uploaded_by FROM attachments WHERE id = ?", (attachment_id,))
    attachment = cursor.fetchone()
    conn.close()

    if not attachment:
        flash('File not found!', 'error')
        return redirect(url_for('dashboard'))

    file_path = attachment[4]
    original_name = attachment[3] or attachment[2]

    # Convert relative path to absolute if needed
    if not os.path.isabs(file_path):
        file_path = os.path.join('static', file_path)

    # Comments in English as requested.
    if not os.path.isfile(file_path):
        flash('The file is missing on the server.', 'error')
        return redirect(url_for('dashboard'))

    return send_file(file_path, as_attachment=True, download_name=original_name)


@app.route('/edit_user/<int:user_id>')
@admin_required
def edit_user(user_id):
    conn = Database().get_connection()
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
    username = request.form['username']
    full_name = request.form['full_name']
    email = request.form.get('email', '')
    role = request.form.get('role', 'user')
    department_id = request.form.get('department_id') or None
    is_department_head = 1 if request.form.get('is_department_head') else 0

    password = request.form.get('password')

    conn = Database().get_connection()
    cursor = conn.cursor()

    try:
        if password:
            hashed_password = hashlib.sha256(password.encode()).hexdigest()
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
        flash(f'Error updating user: {str(e)}', 'error')
    finally:
        conn.close()

    return redirect(url_for('admin_panel'))


@app.route('/edit_category/<int:cat_id>', methods=['GET', 'POST'])
@admin_required
def edit_category(cat_id):
    conn = Database().get_connection()
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
    conn = Database().get_connection()
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
    user_id = session.get('user_id')
    role = session.get('role')

    new_assigned_to = request.form.get('new_assigned_to')
    if not new_assigned_to:
        flash('Please select a user to assign the ticket.', 'error')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    conn = Database().get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT assigned_to FROM tickets WHERE id = ?", (ticket_id,))
    row = cursor.fetchone()
    if not row:
        flash('Ticket not found.', 'error')
        conn.close()
        return redirect(url_for('dashboard'))

    current_assigned_to = row[0]
    
    # Check if user is watcher
    cursor.execute("""
        SELECT COUNT(*) FROM ticket_watchers 
        WHERE ticket_id = ? AND user_id = ?
    """, (ticket_id, user_id))
    is_watcher = cursor.fetchone()[0] > 0
    
    # Allow admin, current assignee, or watcher
    if role != 'admin' and current_assigned_to != user_id and not is_watcher:
        flash('You do not have permission to reassign this ticket.', 'error')
        conn.close()
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    cursor.execute("""
        UPDATE tickets SET assigned_to = ?, updated_at = datetime('now', 'localtime')
        WHERE id = ?
    """, (new_assigned_to, ticket_id))

    cursor.execute("""
        INSERT OR IGNORE INTO ticket_watchers (ticket_id, user_id)
        VALUES (?, ?)
    """, (ticket_id, user_id))

    conn.commit()
    conn.close()

    # Notify
    try:
        assigned_to_int = int(new_assigned_to)
    except Exception:
        assigned_to_int = new_assigned_to  # if any string (e.g., 'IT')
    notify_on_reassigned(ticket_id, actor_user_id=user_id, new_assigned_to_id=assigned_to_int)

    flash('Ticket reassigned successfully.', 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))

@app.route('/add_watchers/<int:ticket_id>', methods=['POST'])
@login_required
def add_watchers(ticket_id):
    user_id = session.get('user_id')
    role = session.get('role')

    watchers_input = request.form.get('watchers', '').strip()
    if not watchers_input:
        flash('Please enter at least one watcher.', 'error')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    conn = Database().get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT assigned_to FROM tickets WHERE id = ?", (ticket_id,))
    row = cursor.fetchone()
    if not row:
        flash('Ticket not found.', 'error')
        conn.close()
        return redirect(url_for('dashboard'))

    assigned_to = row[0]
    # Check permission: admin, assigned_to, or creator
    # Check permission: admin, assigned_to, creator, or existing watcher
    cursor.execute("SELECT created_by FROM tickets WHERE id = ?", (ticket_id,))
    creator_row = cursor.fetchone()
    creator_id = creator_row[0] if creator_row else None

    # Check if user is a watcher
    cursor.execute("SELECT 1 FROM ticket_watchers WHERE ticket_id = ? AND user_id = ?", (ticket_id, user_id))
    is_watcher = cursor.fetchone() is not None

    if role != 'admin' and user_id != assigned_to and user_id != creator_id and not is_watcher:
        flash('You do not have permission to add watchers to this ticket.', 'error')
        conn.close()
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))

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
    # Email notifications disabled for adding watchers
    # if added_watchers_ids:
    #     notify_on_watchers_added(ticket_id, actor_user_id=user_id, watcher_user_ids=added_watchers_ids)

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
    
    conn = Database().get_connection()
    cursor = conn.cursor()
    
    # Check if ticket exists
    cursor.execute("SELECT id FROM tickets WHERE id = ?", (ticket_id,))
    if not cursor.fetchone():
        conn.close()
        flash('Ticket not found.', 'error')
        return redirect(url_for('dashboard'))
    
    # Remove watcher
    cursor.execute("""
        DELETE FROM ticket_watchers 
        WHERE ticket_id = ? AND user_id = ?
    """, (ticket_id, watcher_id_to_remove))
    
    conn.commit()
    conn.close()
    
    log_activity(user_id, 'WATCHER_REMOVED', f'Ticket ID: {ticket_id}, Watcher ID: {watcher_id_to_remove}')
    flash('Watcher removed successfully!', 'success')
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))

@app.before_request
def manage_session():

    # Only enforce idle timeout for authenticated users
    if 'user_id' not in session:
        return

    idle_seconds = 60 * 60  # 1 hour idle timeout
    now = time.time()
    last_active = session.get('last_activity', now)

    if now - last_active > idle_seconds:
        logging.info(f"Session expired for user {session.get('user_id')}, last_active: {last_active}, now: {now}")
        session.clear()
        flash('Session expired. Please log in again.', 'warning')
        return redirect(url_for('login'))

    # Rolling refresh of activity + cookie
    session['last_activity'] = now
    session.permanent = True
    session.modified = True

    # Don’t interfere with static assets
    if request.endpoint and request.endpoint.startswith('static'):
        return

def log_activity(user_id, action, details=None):
    try:
        conn = Database().get_connection()
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


@app.route('/admin/logs')
@admin_required
def view_logs():
    conn = Database().get_connection()
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

    conn = Database().get_connection()
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

    conn = Database().get_connection()
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

    conn = Database().get_connection()
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

    conn = Database().get_connection()
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
    conn = Database().get_connection()
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
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

    _ensure_upload_dir()
    app.run(debug=False, host='0.0.0.0', port=5000)