"""
Client-side license gate and "License Activation" lock screen (CUSTOMER side).

This module is what turns licensing/license_manager.py (fingerprint, Ed25519
verification, tamper-resistant trial) into something the user actually meets:

  * a gate that runs before EVERY request. While the user is licensed or the
    30-day trial is still running, requests pass straight through. Once the
    trial has expired (or was tampered with / the clock was wound back) and no
    valid license exists - or the license has expired - every page except the
    lock screen is blocked: browsers are redirected to /license, API/AJAX calls
    get a 403 JSON "license_required" answer.
  * the lock screen itself (templates/license_lock.html): shows the machine
    fingerprint with a Copy button, a link to the purchase page, and a box to
    paste the license key and press Activate.
  * POST /license/activate: verifies the pasted token locally (Ed25519
    signature + machine fingerprint + expiry) via LicenseManager, stores it as
    license.lic, and unlocks the app without a restart.

Fail-closed: if the license check itself crashes, the app stays locked.

Wiring (see app.py):
    from licensing.client_security import init_license_gate
    init_license_gate(app)

Optional environment variables (.env next to the application):
    LICENSE_PURCHASE_URL   your vendor portal's address, e.g. https://buy.example.com
                           (the lock screen's "Buy a license" button; the machine
                           fingerprint is appended as ?fp=... so the form is pre-filled)
    SUPPORT_EMAIL          shown on the lock screen
    LICENSE_SERVER_URL     if set, a successful activation is reported (best effort,
                           in the background) to <url>/vendor/activate so you can see
                           which paying customers installed. Activation never depends on it.

HONEST LIMIT: this gate is Python code running on the customer's machine, so a
determined reverse-engineer of the packaged .exe can always patch it out. It
stops every non-technical route (editing files, registry, .env, clock). See the
hardening notes in license_manager.py.
"""
import logging
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

from flask import (
    Blueprint, current_app, jsonify, redirect, render_template, request, url_for,
)

from extensions import limiter
from .license_manager import (
    LICENSE_FILE_NAME, TRIAL_DAYS, get_license_manager,
)

logger = logging.getLogger(__name__)

license_gate_bp = Blueprint('license_gate', __name__)

# How long a computed state is reused. Keeps per-request cost near zero while
# still noticing an expiry or a deleted license file within half a minute.
_STATE_TTL_SECONDS = 30
_MAX_TOKEN_LENGTH = 4000

MODE_LICENSED = 'licensed'
MODE_TRIAL = 'trial'
MODE_LOCKED = 'locked'

# Endpoints reachable while the app is locked.
_OPEN_ENDPOINTS = {
    'license_gate.lock_screen',
    'license_gate.activate',
    'license_gate.status',
    'static',
}


@dataclass
class AccessState:
    mode: str
    message: str
    machine_fingerprint: str = ''
    days_remaining: Optional[int] = None      # trial days, or days until license expiry
    customer_name: Optional[str] = None
    edition: Optional[str] = None
    expires_at: Optional[str] = None          # ISO string, None = perpetual
    lock_title: str = ''
    lock_reason: str = ''
    computed_at: float = field(default_factory=time.monotonic)

    @property
    def allowed(self) -> bool:
        return self.mode in (MODE_LICENSED, MODE_TRIAL)

    def to_public_dict(self) -> Dict[str, object]:
        return {
            'mode': self.mode,
            'allowed': self.allowed,
            'message': self.message,
            'machine_fingerprint': self.machine_fingerprint,
            'days_remaining': self.days_remaining,
            'trial_total_days': TRIAL_DAYS,
            'customer_name': self.customer_name,
            'edition': self.edition,
            'expires_at': self.expires_at,
        }


_state_lock = threading.Lock()
_cached_state: Optional[AccessState] = None


def invalidate_access_state() -> None:
    """Forget the cached state so the next request re-checks everything."""
    global _cached_state
    with _state_lock:
        _cached_state = None


def _compute_access_state() -> AccessState:
    """Decide licensed / trial / locked. Never raises: any error => locked."""
    fingerprint = ''
    try:
        lm = get_license_manager()
        fingerprint = lm.machine_fingerprint

        license_ok, license_message = lm.is_license_valid()
        if license_ok:
            payload = lm.get_license_payload() or {}
            lm.touch_last_seen()
            expires_at = payload.get('expires_at')
            days_left = None
            if expires_at:
                try:
                    expiry = datetime.fromisoformat(expires_at)
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=timezone.utc)
                    days_left = max(0, (expiry - lm._effective_now_utc()).days)
                except ValueError:
                    days_left = None
            return AccessState(
                mode=MODE_LICENSED, message='License is valid',
                machine_fingerprint=fingerprint, days_remaining=days_left,
                customer_name=payload.get('customer_name'), edition=payload.get('edition'),
                expires_at=expires_at,
            )

        if lm.start_trial():
            days = lm.get_trial_days_remaining()
            return AccessState(
                mode=MODE_TRIAL, message=f'Trial mode: {days} day(s) remaining',
                machine_fingerprint=fingerprint, days_remaining=days,
            )

        block_reason = getattr(lm, '_trial_block_reason', None) or 'The evaluation period has ended.'
        if 'expired on' in (license_message or '').lower():
            title = 'Your license has expired'
            reason = f'{license_message}. Renew your plan to keep using the application.'
        elif 'different machine' in (license_message or '').lower():
            title = 'License Activation required'
            reason = (f'{license_message}. Send the Machine Fingerprint below to the vendor '
                      f'to get a key for this computer.')
        elif lm.get_trial_days_remaining() == 0 and 'expired' in block_reason.lower():
            title = 'Your 30-day trial has ended'
            reason = 'Activate a license to continue using the application. Your data is safe and will be there when you activate.'
        else:
            title = 'License Activation required'
            reason = block_reason
        return AccessState(
            mode=MODE_LOCKED, message=block_reason, machine_fingerprint=fingerprint,
            lock_title=title, lock_reason=reason,
        )
    except Exception as e:  # fail closed
        logger.exception('License check failed - keeping the application locked: %s', e)
        return AccessState(
            mode=MODE_LOCKED, message='The license status could not be determined.',
            machine_fingerprint=fingerprint, lock_title='License Activation required',
            lock_reason='The license status could not be determined. Please activate a license.',
        )


def get_access_state(force: bool = False) -> AccessState:
    """Current access state (cached for a short time unless force=True)."""
    global _cached_state
    with _state_lock:
        cached = _cached_state
        if not force and cached and (time.monotonic() - cached.computed_at) < _STATE_TTL_SECONDS:
            return cached
        state = _compute_access_state()
        _cached_state = state
        return state


# ---------------------------------------------------------------------------
# Gate (before_request) + trial / expiry reminder pill
# ---------------------------------------------------------------------------
def _gate_disabled_for_tests() -> bool:
    """The test-suite runs with TESTING=True and no real license. Packaged builds never skip."""
    if getattr(sys, 'frozen', False):
        return False
    return bool(current_app.config.get('TESTING')) and not current_app.config.get('LICENSE_GATE_IN_TESTS')


def _wants_json() -> bool:
    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    if request.path.startswith('/api/'):
        return True
    best = request.accept_mimetypes.best_match(['text/html', 'application/json'])
    return best == 'application/json' and request.accept_mimetypes[best] > request.accept_mimetypes['text/html']


def _license_gate():
    if _gate_disabled_for_tests():
        return None
    if request.endpoint in _OPEN_ENDPOINTS or request.path.startswith('/static/'):
        return None

    state = get_access_state()
    if state.allowed:
        return None

    if _wants_json():
        return jsonify({'success': False, 'error': 'license_required',
                        'message': 'A valid license is required. Open /license to activate.'}), 403
    return redirect(url_for('license_gate.lock_screen'))


_REMINDER_TEMPLATE = (
    '<a href="{href}" style="position:fixed;right:14px;bottom:14px;z-index:2147483000;'
    'background:{bg};color:#fff;padding:8px 14px;border-radius:999px;font:600 13px/1.2 '
    '\'Segoe UI\',system-ui,sans-serif;text-decoration:none;box-shadow:0 4px 14px rgba(0,0,0,.25)">'
    '{text}</a>'
)


def _reminder_pill(app_state: AccessState) -> Optional[str]:
    href = '/license'
    if app_state.mode == MODE_TRIAL:
        days = app_state.days_remaining or 0
        colour = '#c62828' if days <= 5 else '#4f46e5'
        return _REMINDER_TEMPLATE.format(
            href=href, bg=colour, text=f'Trial: {days} day{"s" if days != 1 else ""} left &middot; Activate')
    if app_state.mode == MODE_LICENSED and app_state.days_remaining is not None and app_state.days_remaining <= 14:
        return _REMINDER_TEMPLATE.format(
            href=href, bg='#b26a00',
            text=f'License expires in {app_state.days_remaining} day{"s" if app_state.days_remaining != 1 else ""} &middot; Renew')
    return None


def _inject_reminder(response):
    """Add the small trial / renewal pill to ordinary HTML pages."""
    try:
        if (_gate_disabled_for_tests() or response.direct_passthrough
                or response.status_code != 200 or response.mimetype != 'text/html'
                or request.endpoint in _OPEN_ENDPOINTS):
            return response
        pill = _reminder_pill(get_access_state())
        if not pill:
            return response
        body = response.get_data(as_text=True)
        if '</body>' not in body:
            return response
        response.set_data(body.replace('</body>', pill + '</body>', 1))
    except Exception as e:  # cosmetic only - never break a page over it
        logger.debug('Could not add license reminder: %s', e)
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
def _purchase_url(fingerprint: str) -> str:
    import os
    base = (os.environ.get('LICENSE_PURCHASE_URL') or '').strip()
    if not base:
        return ''
    separator = '&' if '?' in base else '?'
    return f'{base}{separator}fp={fingerprint}'


@license_gate_bp.route('/license', methods=['GET'])
def lock_screen():
    """The License Activation screen (also reachable while licensed, to renew)."""
    import os
    state = get_access_state(force=True)
    return render_template(
        'license_lock.html',
        state=state,
        fingerprint=state.machine_fingerprint,
        purchase_url=_purchase_url(state.machine_fingerprint),
        support_email=os.environ.get('SUPPORT_EMAIL', ''),
        company_name=os.environ.get('COMPANY_NAME', ''),
        license_file_name=LICENSE_FILE_NAME,
    )


@license_gate_bp.route('/license/status', methods=['GET'])
def status():
    """Machine-readable status. Contains nothing secret (the fingerprint is meant to be shared)."""
    return jsonify(get_access_state().to_public_dict())


def _clean_token(raw: str) -> str:
    """Pasted keys often pick up quotes, spaces or line breaks from emails."""
    return re.sub(r'\s+', '', (raw or '').strip().strip('"\'`'))


@license_gate_bp.route('/license/activate', methods=['POST'])
@limiter.limit('10 per minute')
def activate():
    """Verify a pasted license key locally and, if valid, unlock the application."""
    if request.is_json:
        raw = (request.get_json(silent=True) or {}).get('license_key', '')
    else:
        raw = request.form.get('license_key', '')
    token = _clean_token(raw)

    if not token:
        return jsonify({'success': False, 'message': 'Please paste your license key first.'}), 400
    if len(token) > _MAX_TOKEN_LENGTH or '.' not in token:
        return jsonify({'success': False,
                        'message': 'That does not look like a license key. Copy the whole key from the email.'}), 400

    lm = get_license_manager()
    ok, message = lm.activate_license_from_string(token)
    if not ok:
        logger.warning('License activation failed: %s', message)
        return jsonify({'success': False, 'message': message}), 400

    invalidate_access_state()
    state = get_access_state(force=True)
    if not state.allowed:
        # Token verified but the app still says locked - should not happen; fail loudly.
        logger.error('License verified but state is still locked: %s', state.message)
        return jsonify({'success': False, 'message': state.message}), 400

    logger.info('License activated for %s (expires %s)', state.customer_name, state.expires_at or 'never')
    _report_activation_async(token, state.machine_fingerprint)

    detail = 'Lifetime license activated.' if not state.expires_at else \
        f'License activated - valid until {state.expires_at[:10]}.'
    return jsonify({'success': True, 'message': detail, 'redirect': '/'})


def _report_activation_async(token: str, fingerprint: str) -> None:
    """Tell the vendor portal (if configured) that this license was activated. Best effort."""
    import os
    base = (os.environ.get('LICENSE_SERVER_URL') or '').strip().rstrip('/')
    if not base:
        return

    def _post():
        try:
            import requests
            requests.post(f'{base}/vendor/activate',
                          json={'license_key': token, 'machine_fingerprint': fingerprint},
                          timeout=6)
        except Exception as e:
            logger.debug('Activation report not delivered (ignored): %s', e)

    threading.Thread(target=_post, name='license-activation-report', daemon=True).start()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def init_license_gate(app) -> None:
    """Register the gate, the lock-screen routes and the reminder pill on `app`."""
    app.register_blueprint(license_gate_bp)
    app.before_request(_license_gate)
    app.after_request(_inject_reminder)
    logger.info('License gate installed')
