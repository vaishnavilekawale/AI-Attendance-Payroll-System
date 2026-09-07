"""
Session-based auth decorators, extracted out of app.py so that both app.py
and every blueprint can import them without a circular import.

These only depend on Flask's `session`/`redirect`/`url_for`/`flash` and
Python's `functools.wraps` - no dependency on app.py itself, which is what
makes them safe to import from anywhere.
"""
from functools import wraps
from flask import session, redirect, url_for, flash


def login_required(f):
    """Require either an admin or an employee session (any authenticated user)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session and 'employee_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Require an admin session specifically."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            flash('Access Denied. Admin access required.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def employee_required(f):
    """Require an employee session specifically, and that any employee_id
    passed as a URL kwarg matches the logged-in employee's own id."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'employee_id' not in session:
            flash('Access Denied. Employee access required.', 'danger')
            return redirect(url_for('employee_login'))
        if 'employee_id' in kwargs and kwargs['employee_id'] != session['employee_id']:
            flash('Access Denied. You can only view your own information.', 'danger')
            return redirect(url_for('employee_dashboard'))
        return f(*args, **kwargs)
    return decorated_function
