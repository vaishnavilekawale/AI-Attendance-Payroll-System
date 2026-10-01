"""
Shared auth-related helper functions, extracted out of app.py so that both
app.py and any blueprint (e.g. the first-run setup wizard) can use them
without a circular import.
"""
import secrets
import string

from flask import request, flash

from database import db
from models import Admin


def generate_secure_temp_password(length: int = 12) -> str:
    """
    Generate a cryptographically random temporary password.

    Used anywhere a NEW login needs a default password before the person
    has chosen their own - e.g. a newly added employee's first login. This
    replaces the previous pattern of seeding a new account's password with
    a piece of that person's own PII (their phone number), which is not a
    secret: it's on file, on their ID, often known to coworkers, and in
    plenty of places outside the company entirely. Anyone who knew (or
    guessed) an employee's phone number could log in as them.

    Guarantees at least one lowercase letter, one uppercase letter, one
    digit and one symbol are present (uses secrets.choice throughout, so
    it's suitable for this purpose unlike the `random` module).
    """
    if length < 8:
        length = 8

    lower, upper, digits, symbols = (
        string.ascii_lowercase, string.ascii_uppercase, string.digits, "!@#$%^&*"
    )
    alphabet = lower + upper + digits + symbols

    password_chars = [
        secrets.choice(lower), secrets.choice(upper),
        secrets.choice(digits), secrets.choice(symbols),
    ]
    password_chars += [secrets.choice(alphabet) for _ in range(length - len(password_chars))]
    secrets.SystemRandom().shuffle(password_chars)
    return "".join(password_chars)


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