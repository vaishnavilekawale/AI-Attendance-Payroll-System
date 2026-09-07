"""
Shared auth-related helper functions, extracted out of app.py so that both
app.py and any blueprint (e.g. the first-run setup wizard) can use them
without a circular import.
"""
from flask import request, flash

from database import db
from models import Admin


def create_admin_from_request_form():
    """
    Shared validation + creation logic for admin account creation, used by
    the first-run /register route, the authenticated /admin/create-admin
    route, and the setup wizard's first step. Flashes a specific error
    message and returns None on any validation failure; returns the newly
    created (already committed) Admin on success.
    """
    username = request.form.get('username')
    email = request.form.get('email')
    password = request.form.get('password')
    confirm_password = request.form.get('confirm_password')

    if not username or not email or not password:
        flash('All fields are required', 'danger')
        return None
    if len(username) < 3:
        flash('Username must be at least 3 characters', 'danger')
        return None
    if len(password) < 6:
        flash('Password must be at least 6 characters', 'danger')
        return None
    if password != confirm_password:
        flash('Passwords do not match', 'danger')
        return None
    if Admin.query.filter_by(username=username).first():
        flash('Username already exists', 'danger')
        return None
    if Admin.query.filter_by(email=email).first():
        flash('Email already registered', 'danger')
        return None

    admin = Admin(username=username, email=email)
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    return admin
