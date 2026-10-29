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
"""
import logging
from datetime import timedelta

from flask import request

from database import db
from models import LoginLockout, LoginAudit, now_ist

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
LOCK_MINUTES = (15, 30, 60)          # escalating; the last value repeats
LEVEL_RESET_AFTER = timedelta(hours=24)


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


def unlock(role, username):
    """Clear every lock/counter for a username (all IPs). Used by the admin Unlock button."""
    LoginLockout.query.filter_by(role=role, username=_key(username)).delete()
    db.session.commit()
    log_event(role, username, 'unlocked')
