from functools import wraps
from flask import session, redirect, url_for, flash, request
import time
import logging
import os

# Session timeout in seconds (default 1 hour)
SESSION_TIMEOUT = int(os.getenv('SESSION_TIMEOUT_SECONDS', 3600))

def check_session_timeout():
    """
    Check if session has timed out due to inactivity.
    
    Returns:
        bool: True if session is valid, False if timed out
    """
    if 'user_id' not in session:
        return False
    
    # Get last activity time
    last_activity = session.get('last_activity', time.time())
    now = time.time()
    
    # Check if session expired
    if now - last_activity > SESSION_TIMEOUT:
        logging.info(f"Session expired for user {session.get('user_id')} - last activity: {last_activity}, now: {now}")
        session.clear()
        return False
    
    # Update last activity time
    session['last_activity'] = now
    session.modified = True
    
    return True


def login_required(f):
    """
    Decorator to ensure user is logged in.
    Checks session validity and timeout.
    
    Usage:
        @app.route('/dashboard')
        @login_required
        def dashboard():
            ...
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if user is logged in
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'error')
            # Store attempted URL for redirect after login
            session['next_url'] = request.url
            return redirect(url_for('login'))
        
        # Check session timeout
        if not check_session_timeout():
            flash('Your session has expired. Please log in again.', 'warning')
            session['next_url'] = request.url
            return redirect(url_for('login'))
        
        return f(*args, **kwargs)
    
    return decorated_function


def admin_required(f):
    """
    Decorator to ensure user is an admin.
    Also checks login status and session timeout.
    
    Usage:
        @app.route('/admin')
        @admin_required
        def admin_panel():
            ...
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if user is logged in
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'error')
            session['next_url'] = request.url
            return redirect(url_for('login'))
        
        # Check session timeout
        if not check_session_timeout():
            flash('Your session has expired. Please log in again.', 'warning')
            session['next_url'] = request.url
            return redirect(url_for('login'))
        
        # Check if user is admin
        if session.get('role') != 'admin':
            flash('Admin access required.', 'error')
            logging.warning(f"Unauthorized admin access attempt by user {session.get('user_id')} from IP {request.environ.get('REMOTE_ADDR')}")
            return redirect(url_for('dashboard'))
        
        return f(*args, **kwargs)
    
    return decorated_function


def department_head_or_admin_required(f):
    """
    Decorator to ensure user is a department head or admin.
    Also checks login status and session timeout.
    
    Usage:
        @app.route('/department/manage')
        @department_head_or_admin_required
        def manage_department():
            ...
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if user is logged in
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'error')
            session['next_url'] = request.url
            return redirect(url_for('login'))
        
        # Check session timeout
        if not check_session_timeout():
            flash('Your session has expired. Please log in again.', 'warning')
            session['next_url'] = request.url
            return redirect(url_for('login'))
        
        # Check if user is admin or department head
        role = session.get('role')
        is_dept_head = session.get('is_department_head', 0)
        
        if role != 'admin' and not is_dept_head:
            flash('Department head or admin access required.', 'error')
            logging.warning(f"Unauthorized department access attempt by user {session.get('user_id')} from IP {request.environ.get('REMOTE_ADDR')}")
            return redirect(url_for('dashboard'))
        
        return f(*args, **kwargs)
    
    return decorated_function


def role_required(*allowed_roles):
    """
    Generic decorator to check for specific roles.
    
    Args:
        *allowed_roles: Variable number of allowed roles
        
    Usage:
        @app.route('/special')
        @role_required('admin', 'manager')
        def special_page():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Check if user is logged in
            if 'user_id' not in session:
                flash('Please log in to access this page.', 'error')
                session['next_url'] = request.url
                return redirect(url_for('login'))
            
            # Check session timeout
            if not check_session_timeout():
                flash('Your session has expired. Please log in again.', 'warning')
                session['next_url'] = request.url
                return redirect(url_for('login'))
            
            # Check if user has required role
            user_role = session.get('role')
            if user_role not in allowed_roles:
                flash(f'Access denied. Required role: {", ".join(allowed_roles)}', 'error')
                logging.warning(f"Unauthorized access attempt by user {session.get('user_id')} (role: {user_role}) - required: {allowed_roles}")
                return redirect(url_for('dashboard'))
            
            return f(*args, **kwargs)
        
        return decorated_function
    
    return decorator


def is_safe_url(target):
    """
    Check if redirect URL is safe (same domain).
    Prevents open redirect vulnerabilities.
    
    Args:
        target: URL to check
        
    Returns:
        bool: True if safe, False otherwise
    """
    from urllib.parse import urlparse, urljoin
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc


def get_redirect_target():
    """
    Get safe redirect target from session or request args.
    
    Returns:
        str: Safe redirect URL or None
    """
    # Check session first
    target = session.pop('next_url', None)
    if target and is_safe_url(target):
        return target
    
    # Check request args
    target = request.args.get('next')
    if target and is_safe_url(target):
        return target
    
    return None
