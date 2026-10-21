"""
Customer-side trial registration with the VENDOR server.

Why this exists
---------------
The local trial markers (registry + encrypted file + .env, see
license_manager.py) stop casual resets, but they all live on the customer's
own computer, so uninstalling, deleting the leftovers and installing again
used to start a brand-new 30 days. The vendor server is the memory that
survives a reinstall:

  1. When the admin finishes setup, this module sends the vendor the admin
     name, email, company name and this computer's machine fingerprint
     (POST <server>/api/trial/start). The vendor sees the trial in its
     "trials" list (GET /vendor/trials).
  2. The server stores the FIRST start date for that fingerprint for ever.
     A reinstall on the same laptop sends the same fingerprint and is told
     the original date, in a token signed with the vendor key. The app
     adopts it (earliest date wins), so the trial just continues - or is
     already over.
  3. No internet: nothing breaks. The local trial runs as before and the
     app retries in the background (every 15 minutes until it succeeds,
     then once a day). The first time the server is reachable, an
     offline-started trial is reconciled with the vendor's record.

Setting the server address
--------------------------
Put your portal's address in EMBEDDED_LICENSE_SERVER_URL below before you
build the release, e.g. "https://license.yourcompany.com". In a packaged
(.exe) build the LICENSE_SERVER_URL value in .env is then IGNORED, so a
customer cannot point the app at a fake server. In a development checkout
LICENSE_SERVER_URL in .env is used when nothing is embedded.
If neither is set, trial registration is simply switched off.

HONEST LIMIT: someone who is offline on every start, and wipes all local
markers before each start, can still get a fresh local trial. Closing that
completely would mean requiring an internet connection to start a trial.
"""
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# >>> SET THIS before building a customer release (no trailing slash). <<<
EMBEDDED_LICENSE_SERVER_URL = ""

_REQUEST_TIMEOUT = (3.0, 6.0)          # (connect, read) seconds - never hang the wizard
_RETRY_AFTER_SECONDS = 15 * 60         # while not yet synced
_REFRESH_AFTER_SECONDS = 24 * 3600     # once synced


def get_license_server_url() -> str:
    """The vendor portal address, or '' when trial registration is switched off."""
    embedded = (EMBEDDED_LICENSE_SERVER_URL or "").strip().rstrip("/")
    from_env = (os.environ.get("LICENSE_SERVER_URL") or "").strip().rstrip("/")
    if embedded and getattr(sys, "frozen", False):
        return embedded            # packaged build: .env cannot redirect us
    return from_env or embedded


@dataclass
class TrialSyncResult:
    # 'started'  vendor created the record      'existing'  vendor already knew this computer
    # 'disabled' no server configured            'offline'   server not reachable
    # 'rejected' server refused the details      'error'     anything else
    status: str
    message: str = ""
    days_remaining: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.status in ("started", "existing")

    @property
    def trial_over(self) -> bool:
        return self.ok and (self.days_remaining or 0) <= 0


def register_trial(admin_name: str, email: str, company: str, lm=None) -> TrialSyncResult:
    """Tell the vendor this admin started a trial, and adopt the date it has on record."""
    base = get_license_server_url()
    if not base:
        return TrialSyncResult("disabled", "No license server configured")

    if lm is None:
        from .license_manager import get_license_manager
        lm = get_license_manager()

    body = {
        "admin_name": admin_name,
        "email": email,
        "company": company,
        "machine_fingerprint": lm.machine_fingerprint,
    }
    local_start = lm.get_trial_start_date()
    if local_start:
        body["local_trial_start"] = local_start

    try:
        import requests
        response = requests.post(f"{base}/api/trial/start", json=body, timeout=_REQUEST_TIMEOUT)
    except Exception as e:  # requests.RequestException and anything odd (DNS, TLS, proxy)
        logger.info("Trial registration postponed - vendor server not reachable: %s", e)
        return TrialSyncResult("offline", "The license server could not be reached")

    try:
        data = response.json()
    except ValueError:
        data = {}

    if response.status_code == 400:
        return TrialSyncResult("rejected", str(data.get("error") or "The license server refused the details"))
    if response.status_code != 200 or not data.get("success"):
        logger.warning("Trial registration failed: HTTP %s", response.status_code)
        return TrialSyncResult("error", "The license server returned an error")

    ok, message = lm.apply_vendor_trial(data.get("trial_token", ""))
    if not ok:
        logger.warning("Vendor trial record rejected: %s", message)
        return TrialSyncResult("error", message)

    status = "started" if data.get("status") == "started" else "existing"
    return TrialSyncResult(status, "Trial recorded", lm.get_trial_days_remaining())


# ---------------------------------------------------------------------------
# Background retry (offline installs, existing installs that never registered)
# ---------------------------------------------------------------------------
_state_lock = threading.Lock()
_last_attempt = 0.0       # time.monotonic()
_in_flight = False


def sync_due(lm) -> bool:
    """True when it is time to (re)talk to the vendor. Cheap - safe to call per request."""
    if not get_license_server_url():
        return False
    with _state_lock:
        if _in_flight:
            return False
        if _last_attempt and (time.monotonic() - _last_attempt) < _RETRY_AFTER_SECONDS:
            return False
    last = lm.get_vendor_sync_time()
    if last is None:
        return True
    return datetime.now(timezone.utc) - last > timedelta(seconds=_REFRESH_AFTER_SECONDS)


def _local_contact():
    """(admin_name, email, company) from the local database, or None before setup is done."""
    from models import Admin, Settings
    admin = Admin.query.order_by(Admin.id.asc()).first()
    if admin is None:
        return None
    settings = Settings.get_settings()
    return admin.username, admin.email, (settings.company_name or "")


def sync_in_background(app, lm=None) -> bool:
    """
    Start one background attempt to register / refresh this install's trial.
    Returns False if one is already running. Never raises, never blocks.
    """
    global _in_flight, _last_attempt
    with _state_lock:
        if _in_flight:
            return False
        _in_flight = True
        _last_attempt = time.monotonic()

    def _run():
        global _in_flight
        try:
            with app.app_context():
                contact = _local_contact()
                if contact is None:
                    return                     # wizard not finished - it registers the trial itself
                result = register_trial(*contact, lm=lm)
            if result.ok:
                from .client_security import invalidate_access_state
                invalidate_access_state()      # a vendor "already used" answer must lock immediately
        except Exception as e:
            logger.debug("Background trial sync failed (ignored): %s", e)
        finally:
            with _state_lock:
                _in_flight = False

    threading.Thread(target=_run, name="trial-sync", daemon=True).start()
    return True
