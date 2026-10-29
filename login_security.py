"""
Login security helpers: lockout with escalating, auto-expiring locks, and
an audit trail of login events.

Used by auth_routes.login() and the public kiosk manual-attendance
endpoint, so both share the same failed-attempt counter (otherwise the
kiosk endpoint would be a way around the /login lockout).

Policy
------
* 5 wrong passwords for the same (role, username, IP) -> locked.
* Lock length escalates: 15 min, then 30 min, then 60 min (60 repeats).
* Locks always expire by themselves - nobody is locked out permanently.
* The escalation level resets after 24 h without a failed attempt.
* Locks are per username+IP, so an attacker only locks their own IP.
* An admin can clear an employee's lock early (see employees.unlock_employee).
* Another admin can unlock a locked admin (Manage Admins page), and a locked
  admin can unlock themselves with a one-time link sent to their registered
  email. Neither path touches the password, so neither is a way around it.
"""
import hashlib
import logging
import secrets
from datetime import timedelta

from flask import request

from database import db
from models import LoginLockout, LoginAudit, AdminUnlockToken, now_ist

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
LOCK_MINUTES = (15, 30, 60)          # escalating; the last value repeats
LEVEL_RESET_AFTER = timedelta(hours=24)

UNLOCK_TOKEN_MINUTES = 30            # how long an "Unlock via email" link works
UNLOCK_EMAIL_COOLDOWN_MINUTES = 5    # at most one unlock email per admin per 5 min


def client_ip():
    return request.remote_addr or 'unknown'


def _key(username):
    return (username or '').strip().lower()[:80]


def _get_record(role, username, create=False):
    key = _key(username)
    ip = client_ip()
    rec = LoginLockout.query.filter_by(role=role, username=key, ip=ip).first()
    if rec is None and create:
        rec = LoginLockout(role=role, username=key, ip=ip, failed_count=0, lock_level=0)
        db.session.add(rec)
    return rec


def log_event(role, username, event):
    """Append one row to the audit log. Never lets an audit failure break a login."""
    try:
        db.session.add(LoginAudit(
            role=role, username=(username or '').strip()[:80], ip=client_ip(), event=event,
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Could not write login audit row")


def minutes_left(role, username):
    """Whole minutes (rounded up) until this username+IP may try again; 0 if not locked."""
    rec = _get_record(role, username)
    if rec and rec.locked_until and rec.locked_until > now_ist():
        seconds = (rec.locked_until - now_ist()).total_seconds()
        return max(1, int(seconds // 60) + (1 if seconds % 60 else 0))
    return 0


def record_failure(role, username):
    """
    Count one failed attempt. Returns the lock length in minutes if THIS
    failure caused a lock, otherwise 0.
    """
    rec = _get_record(role, username, create=True)
    now = now_ist()

    if rec.last_failed_at and now - rec.last_failed_at > LEVEL_RESET_AFTER:
        rec.lock_level = 0
        rec.failed_count = 0

    rec.failed_count = (rec.failed_count or 0) + 1
    rec.last_failed_at = now

    locked_for = 0
    if rec.failed_count >= MAX_ATTEMPTS:
        level = rec.lock_level or 0
        locked_for = LOCK_MINUTES[min(level, len(LOCK_MINUTES) - 1)]
        rec.locked_until = now + timedelta(minutes=locked_for)
        rec.lock_level = level + 1
        rec.failed_count = 0

    db.session.commit()
    log_event(role, username, 'locked' if locked_for else 'failed')
    return locked_for


def record_success(role, username):
    """Successful login: clear the counter for this username+IP and log it."""
    rec = _get_record(role, username)
    if rec is not None:
        db.session.delete(rec)
        db.session.commit()
    log_event(role, username, 'success')


def unlock(role, username, by=None):
    """
    Clear every lock/counter for a username (all IPs). Used by the admin
    Unlock buttons and the email unlock link. `by` (e.g. "admin2" or "email")
    is added to the audit log row so you can see who unlocked whom.
    """
    LoginLockout.query.filter_by(role=role, username=_key(username)).delete()
    db.session.commit()
    label = f"{username} (by {by})" if by else username
    log_event(role, label, 'unlocked')


def locked_until_by_username(role):
    """{lower-cased username: latest locked_until} for every active lock of this role (any IP)."""
    result = {}
    now = now_ist()
    for rec in LoginLockout.query.filter(LoginLockout.role == role,
                                         LoginLockout.locked_until > now).all():
        if rec.username not in result or rec.locked_until > result[rec.username]:
            result[rec.username] = rec.locked_until
    return result


# ---------------------------------------------------------------- email unlock

def _hash_token(raw):
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def create_unlock_token(admin):
    """
    Create a one-time unlock token for this admin and return the raw token
    (to put in the email link). Returns None when an unlock email was already
    requested in the last UNLOCK_EMAIL_COOLDOWN_MINUTES (anti-spam).
    """
    now = now_ist()
    recent = AdminUnlockToken.query.filter(
        AdminUnlockToken.admin_id == admin.id,
        AdminUnlockToken.created_at > now - timedelta(minutes=UNLOCK_EMAIL_COOLDOWN_MINUTES),
    ).first()
    if recent:
        return None

    raw = secrets.token_urlsafe(32)
    db.session.add(AdminUnlockToken(
        admin_id=admin.id,
        token_hash=_hash_token(raw),
        expires_at=now + timedelta(minutes=UNLOCK_TOKEN_MINUTES),
    ))
    db.session.commit()
    return raw


def discard_unlock_token(raw):
    """Delete a token whose email could not be sent, so the cooldown does not block a retry."""
    AdminUnlockToken.query.filter_by(token_hash=_hash_token(raw)).delete()
    db.session.commit()


def find_valid_unlock_token(raw):
    """The AdminUnlockToken row if `raw` is known, unused and not expired; otherwise None."""
    if not raw:
        return None
    tok = AdminUnlockToken.query.filter_by(token_hash=_hash_token(raw)).first()
    if tok is None or tok.used_at is not None or tok.expires_at < now_ist():
        return None
    return tok


def consume_unlock_token(tok):
    """Mark a token as used (one-time) and drop any other tokens still open for that admin."""
    tok.used_at = now_ist()
    AdminUnlockToken.query.filter(
        AdminUnlockToken.admin_id == tok.admin_id,
        AdminUnlockToken.used_at.is_(None),
    ).delete(synchronize_session=False)
    db.session.commit()
