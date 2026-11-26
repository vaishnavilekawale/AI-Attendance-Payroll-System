"""
Persistent file storage for hosts with an EPHEMERAL disk (Render free, etc.).

The problem
-----------
On Render's free plan the filesystem is wiped on every redeploy, restart and
spin-down. AIAPS keeps several things as plain files next to the app:

    dataset/<employee_id>/*.jpg     face photos (already encrypted at rest)
    trained_model/*.pkl             face_data.pkl + embeddings_cache.pkl
    uploads/**                      payslip PDFs, reports, profile photos
    license.lic                     activated licence

All of that vanished on every restart. This module keeps those files in a
table of the SAME database as the rest of the data (e.g. a free Neon
Postgres), so one database holds everything and a single ``pg_dump`` backs
up the whole application.

How it works
------------
* ``restore_all()``  - at startup, every stored file that is missing on disk
  is written back to disk (with its original modification time), so the rest
  of the application keeps using plain files exactly as before.
* ``sync_now()``     - compares the files on disk with the table (by SHA-256)
  and uploads new/changed files and removes deleted ones. Only changed files
  are transferred, so it is cheap.
* When does it run? (a) a few seconds after any POST/PUT/PATCH/DELETE request
  (debounced), (b) every STORAGE_SYNC_INTERVAL seconds in a background
  thread (catches scheduler-generated payslips), (c) at interpreter exit and
  (d) whenever /cron/<token>?task=sync is called.

Turned OFF by default. Enable with ``STORAGE_BACKEND=db`` in the environment
(Render -> Environment). A normal desktop install is not affected at all.

Safety rules (they exist to make data loss impossible):
* Nothing is ever deleted from the database until the startup restore has
  finished WITHOUT errors, and never when the local disk looks empty.
* A restored file is only written if its SHA-256 matches the stored hash.
* When the storage limit is reached, new files are NOT uploaded (nothing is
  deleted) and a warning is exposed through storage_status().
* .env, the SQLite database and backup zips are never stored.

Environment variables
---------------------
STORAGE_BACKEND         "db" to enable (anything else = disabled)
STORAGE_LIMIT_MB        max total size of stored files, default 700
                        (Neon free = 1 GB for everything, keep headroom)
STORAGE_SYNC_INTERVAL   seconds between background syncs, default 300
STORAGE_SYNC_DEBOUNCE   seconds to wait after a write request, default 3

Run with ONE worker process (e.g. ``gunicorn -w 1 --threads 4 app:app``): the
face-recognition stack needs the RAM anyway, and two workers would run two
independent sync loops.
"""
import atexit
import hashlib
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from config import Config, BASE_DIR

logger = logging.getLogger(__name__)

# Top-level folders that are mirrored (relative to BASE_DIR) and, for
# trained_model, which file extensions. license.lic is handled separately.
_SYNC_DIRS = ("dataset", "uploads", "trained_model")
_TRAINED_MODEL_EXTS = (".pkl",)
_SINGLE_FILES = ("license.lic",)
_SKIP_SUFFIXES = (".tmp", ".db", ".db-wal", ".db-shm", "-journal")
_SKIP_DIR_NAMES = ("backups", "__pycache__")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def is_enabled() -> bool:
    return os.environ.get("STORAGE_BACKEND", "").strip().lower() == "db"


def _env_number(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
        return value if value > 0 else default
    except ValueError:
        return default


def limit_bytes() -> int:
    return int(_env_number("STORAGE_LIMIT_MB", 700) * 1024 * 1024)


def _interval() -> float:
    return _env_number("STORAGE_SYNC_INTERVAL", 300)


def _debounce() -> float:
    return _env_number("STORAGE_SYNC_DEBOUNCE", 3)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
class _State:
    app = None
    store = None
    restored = False          # startup restore completed (uploads may run)
    deletes_allowed = False   # restore had ZERO errors (deletes may run)
    last_sync_utc: Optional[str] = None
    last_error: Optional[str] = None
    last_stats: Dict = {}
    timer: Optional[threading.Timer] = None
    thread_started = False
    hooks_registered = False


_state = _State()
_sync_lock = threading.Lock()
_timer_lock = threading.Lock()
_manifest: Dict[str, Tuple[int, int, str]] = {}   # rel -> (size, mtime_ns, sha256)


# ---------------------------------------------------------------------------
# Storage backends. The sync logic only needs these four calls, so it can be
# tested without a database (see tests/test_storage_sync.py).
# ---------------------------------------------------------------------------
class SqlStore:
    """stored_files table in the application's own database (SQLAlchemy Core)."""

    def __init__(self, engine_getter):
        import sqlalchemy as sa
        self._sa = sa
        self._engine_getter = engine_getter
        self._metadata = sa.MetaData()   # separate from db.metadata on purpose:
        # Flask-Migrate must not try to manage or drop this table.
        self.table = sa.Table(
            "stored_files", self._metadata,
            sa.Column("path", sa.String(500), primary_key=True),
            sa.Column("data", sa.LargeBinary, nullable=False),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("size", sa.Integer, nullable=False),
            sa.Column("mtime", sa.Float, nullable=False, default=0.0),
            sa.Column("updated_at", sa.DateTime, nullable=False),
        )

    def _engine(self):
        return self._engine_getter()

    def ensure_table(self):
        self._metadata.create_all(self._engine(), tables=[self.table], checkfirst=True)

    def list_rows(self) -> Dict[str, Tuple[str, int]]:
        t = self.table
        with self._engine().connect() as conn:
            rows = conn.execute(self._sa.select(t.c.path, t.c.sha256, t.c.size)).fetchall()
        return {r[0]: (r[1], int(r[2])) for r in rows}

    def get(self, path: str) -> Optional[Tuple[bytes, float]]:
        t = self.table
        with self._engine().connect() as conn:
            row = conn.execute(
                self._sa.select(t.c.data, t.c.mtime).where(t.c.path == path)).fetchone()
        if row is None:
            return None
        return bytes(row[0]), float(row[1] or 0.0)

    def put(self, path: str, data: bytes, sha: str, size: int, mtime: float) -> None:
        t = self.table
        values = dict(data=data, sha256=sha, size=size, mtime=mtime,
                      updated_at=datetime.now(timezone.utc).replace(tzinfo=None))
        with self._engine().begin() as conn:
            res = conn.execute(t.update().where(t.c.path == path).values(**values))
            if res.rowcount == 0:
                conn.execute(t.insert().values(path=path, **values))

    def delete(self, path: str) -> None:
        t = self.table
        with self._engine().begin() as conn:
            conn.execute(t.delete().where(t.c.path == path))


def _get_store():
    return _state.store


# ---------------------------------------------------------------------------
# Local file helpers
# ---------------------------------------------------------------------------
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rel_ok(rel: str) -> bool:
    """True when a relative path belongs to the mirrored set."""
    parts = rel.split("/")
    if not parts or any(p in ("", ".", "..") for p in parts):
        return False
    name = parts[-1]
    if rel in _SINGLE_FILES:
        return True
    if parts[0] not in _SYNC_DIRS or len(parts) < 2:
        return False
    if any(p in _SKIP_DIR_NAMES for p in parts[:-1]):
        return False
    if name.lower().endswith(_SKIP_SUFFIXES) or name.startswith("."):
        return False
    if parts[0] == "trained_model" and not name.lower().endswith(_TRAINED_MODEL_EXTS):
        return False
    return True


def _abs_for(rel: str) -> Optional[str]:
    """Absolute path for a stored relative path, or None if it is unsafe."""
    if not _rel_ok(rel):
        return None
    base = os.path.realpath(BASE_DIR)
    target = os.path.realpath(os.path.join(base, *rel.split("/")))
    if os.path.commonpath([base, target]) != base:
        return None
    return target


def _scan_local() -> Dict[str, Tuple[str, int, float]]:
    """{rel_path: (abs_path, size, mtime)} of every file that should be mirrored."""
    found: Dict[str, Tuple[str, int, float]] = {}
    base = os.path.realpath(BASE_DIR)
    for top in _SYNC_DIRS:
        root = os.path.join(base, top)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
            for fname in filenames:
                absf = os.path.join(dirpath, fname)
                rel = os.path.relpath(absf, base).replace(os.sep, "/")
                if not _rel_ok(rel):
                    continue
                try:
                    st = os.stat(absf)
                except OSError:
                    continue
                found[rel] = (absf, st.st_size, st.st_mtime)
    for single in _SINGLE_FILES:
        absf = os.path.join(base, single)
        if os.path.isfile(absf):
            st = os.stat(absf)
            found[single] = (absf, st.st_size, st.st_mtime)
    return found


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Restore (database -> disk)
# ---------------------------------------------------------------------------
def restore_all() -> Dict:
    """Write every stored file that is missing/different on disk back to disk."""
    stats = {"restored": 0, "unchanged": 0, "errors": 0, "files": 0}
    store = _get_store()
    rows = store.list_rows()
    stats["files"] = len(rows)
    for rel, (sha, size) in sorted(rows.items()):
        target = _abs_for(rel)
        if target is None:
            logger.warning("storage_sync: ignoring unsafe/unknown stored path %r", rel)
            continue
        try:
            if os.path.isfile(target) and os.path.getsize(target) == size:
                existing = _read(target)
                if _sha256(existing) == sha:
                    st = os.stat(target)
                    _manifest[rel] = (st.st_size, st.st_mtime_ns, sha)
                    stats["unchanged"] += 1
                    continue
            got = store.get(rel)
            if got is None:
                raise RuntimeError("row vanished while restoring")
            data, mtime = got
            if _sha256(data) != sha:
                raise RuntimeError("stored data does not match its checksum")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            tmp = target + ".restore.tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            if mtime:
                os.utime(tmp, (mtime, mtime))
            os.replace(tmp, target)
            st = os.stat(target)
            _manifest[rel] = (st.st_size, st.st_mtime_ns, sha)
            stats["restored"] += 1
        except Exception as exc:  # keep going: one bad file must not block the rest
            stats["errors"] += 1
            logger.error("storage_sync: could not restore %s: %s", rel, exc)
    _state.restored = True
    _state.deletes_allowed = stats["errors"] == 0
    logger.info("storage_sync: restore finished %s", stats)
    _refresh_face_engine()
    return stats


def _refresh_face_engine() -> None:
    """If the face engine was created before the restore, make it re-read its files."""
    try:
        import face_recognition_singleton as frs
        engine = getattr(frs, "_face_recognizer", None)
        if engine is not None:
            engine.load_model()
    except Exception as exc:  # never let this break startup
        logger.debug("storage_sync: face engine refresh skipped: %s", exc)


# ---------------------------------------------------------------------------
# Sync (disk -> database)
# ---------------------------------------------------------------------------
def sync_now(reason: str = "") -> Dict:
    """Upload new/changed files, remove deleted ones. Safe to call from any thread."""
    result = {"skipped": None, "uploaded": 0, "deleted": 0, "unchanged": 0,
              "deferred": 0, "over_limit": 0, "errors": 0}
    if not is_enabled() or _get_store() is None:
        result["skipped"] = "disabled"
        return result
    if not _state.restored:
        result["skipped"] = "restore not finished"
        return result
    if not _sync_lock.acquire(blocking=False):
        result["skipped"] = "another sync is running"
        return result
    try:
        app = _state.app
        ctx = app.app_context() if app is not None else None
        if ctx is not None:
            ctx.push()
        try:
            _do_sync(result)
        finally:
            if ctx is not None:
                ctx.pop()
        _state.last_sync_utc = datetime.now(timezone.utc).isoformat()
        if result["errors"] == 0 and result["over_limit"] == 0:
            _state.last_error = None
    except Exception as exc:
        result["errors"] += 1
        _state.last_error = f"{exc.__class__.__name__}: {exc}"
        logger.error("storage_sync: sync failed (%s): %s", reason or "-", exc, exc_info=True)
    finally:
        _sync_lock.release()
    _state.last_stats = result
    if result["uploaded"] or result["deleted"]:
        logger.info("storage_sync: %s -> %s", reason or "sync", result)
    return result


def _do_sync(result: Dict) -> None:
    store = _get_store()
    remote = store.list_rows()
    local = _scan_local()
    used = sum(size for _sha, size in remote.values())
    limit = limit_bytes()
    now = time.time()

    for rel in sorted(local):
        absf, size, mtime = local[rel]
        if now - mtime < 1.0:          # probably still being written
            result["deferred"] += 1
            continue
        try:
            st = os.stat(absf)
            key = (st.st_size, st.st_mtime_ns)
            cached = _manifest.get(rel)
            data = None
            if cached and cached[:2] == key:
                sha = cached[2]
            else:
                data = _read(absf)
                sha = _sha256(data)
                _manifest[rel] = (key[0], key[1], sha)
            entry = remote.get(rel)
            if entry and entry[0] == sha:
                result["unchanged"] += 1
                continue
            if data is None:
                data = _read(absf)
                sha = _sha256(data)
                _manifest[rel] = (key[0], key[1], sha)
            new_total = used - (entry[1] if entry else 0) + len(data)
            if new_total > limit:
                result["over_limit"] += 1
                _state.last_error = (
                    f"Storage limit reached ({limit // (1024 * 1024)} MB): new files are NOT "
                    "being saved to the database. Delete old files or raise STORAGE_LIMIT_MB.")
                logger.warning("storage_sync: %s", _state.last_error)
                continue
            store.put(rel, data, sha, len(data), mtime)
            used = new_total
            remote[rel] = (sha, len(data))
            result["uploaded"] += 1
        except Exception as exc:
            result["errors"] += 1
            _state.last_error = f"{exc.__class__.__name__}: {exc}"
            logger.error("storage_sync: could not upload %s: %s", rel, exc)

    # Deletions - only when we are certain the disk really is the truth.
    if not _state.deletes_allowed:
        return
    if remote and not local:
        logger.warning("storage_sync: local disk looks empty but %d files are stored - "
                       "refusing to delete anything.", len(remote))
        return
    base = os.path.realpath(BASE_DIR)
    for rel in sorted(set(remote) - set(local)):
        top = rel.split("/")[0]
        if rel not in _SINGLE_FILES and not os.path.isdir(os.path.join(base, top)):
            continue                       # whole folder missing -> not a real delete
        try:
            store.delete(rel)
            _manifest.pop(rel, None)
            result["deleted"] += 1
        except Exception as exc:
            result["errors"] += 1
            logger.error("storage_sync: could not delete %s: %s", rel, exc)


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------
def schedule_sync(delay: Optional[float] = None) -> None:
    """Debounced: (re)start a timer; the sync runs once things go quiet."""
    if not is_enabled() or not _state.restored:
        return
    delay = _debounce() if delay is None else delay
    with _timer_lock:
        if _state.timer is not None:
            _state.timer.cancel()
        t = threading.Timer(delay, sync_now, kwargs={"reason": "after write"})
        t.daemon = True
        _state.timer = t
        t.start()


def _background_loop() -> None:
    while True:
        time.sleep(_interval())
        try:
            if not _state.restored:
                _try_restore()
            else:
                sync_now(reason="periodic")
        except Exception as exc:
            logger.error("storage_sync: background loop error: %s", exc)


def _try_restore() -> None:
    try:
        with _state.app.app_context():
            _state.store.ensure_table()
            restore_all()
    except Exception as exc:
        _state.last_error = f"restore failed: {exc.__class__.__name__}: {exc}"
        logger.error("storage_sync: restore failed (will retry): %s", exc)


def _on_exit() -> None:
    try:
        if _state.timer is not None:
            _state.timer.cancel()
        sync_now(reason="shutdown")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Status (for the admin page / cron endpoint)
# ---------------------------------------------------------------------------
def storage_status() -> Dict:
    status = {
        "enabled": is_enabled(),
        "restored": _state.restored,
        "limit_mb": round(limit_bytes() / (1024 * 1024), 1),
        "used_mb": None, "percent": None, "files": None,
        "last_sync_utc": _state.last_sync_utc,
        "last_error": _state.last_error,
        "last_stats": _state.last_stats,
    }
    if not is_enabled() or _state.store is None:
        return status
    try:
        app = _state.app
        ctx = app.app_context() if app is not None else None
        if ctx is not None:
            ctx.push()
        try:
            rows = _state.store.list_rows()
        finally:
            if ctx is not None:
                ctx.pop()
        used = sum(size for _sha, size in rows.values())
        status["files"] = len(rows)
        status["used_mb"] = round(used / (1024 * 1024), 2)
        status["percent"] = round(100.0 * used / limit_bytes(), 1)
        if status["percent"] >= 80 and not status["last_error"]:
            status["last_error"] = (f"Storage is {status['percent']}% full - free some space soon.")
    except Exception as exc:
        status["last_error"] = f"{exc.__class__.__name__}: {exc}"
    return status


# ---------------------------------------------------------------------------
# Flask wiring
# ---------------------------------------------------------------------------
def init_app(app, db=None) -> bool:
    """
    Call once, right after init_db(app). Returns True when cloud storage is
    active. Does nothing (and costs nothing) unless STORAGE_BACKEND=db.
    """
    if not is_enabled():
        return False
    if not os.environ.get("FACE_DATA_ENCRYPTION_KEY"):
        logger.warning(
            "storage_sync: FACE_DATA_ENCRYPTION_KEY is not set as an environment variable. "
            "A new key would be generated on every restart and stored face photos could "
            "never be decrypted. Set it (and SECRET_KEY) in the hosting dashboard.")

    _state.app = app
    if _state.store is None:
        if db is None:
            from database import db as _db
            db = _db
        _state.store = SqlStore(lambda: db.engine)

    _try_restore_blocking(app)
    if _state.restored:
        # Upload anything that exists only on disk and stamp "last sync" right away.
        threading.Thread(target=sync_now, kwargs={"reason": "startup"},
                         name="storage-sync-startup", daemon=True).start()

    if not _state.hooks_registered:
        @app.after_request
        def _sync_after_write(response):
            try:
                if request_is_write() and response.status_code < 500:
                    schedule_sync()
            except Exception:
                pass
            return response
        atexit.register(_on_exit)
        _state.hooks_registered = True

    if not _state.thread_started:
        threading.Thread(target=_background_loop, name="storage-sync", daemon=True).start()
        _state.thread_started = True
    logger.info("storage_sync: ACTIVE (files are mirrored into the database)")
    return True


def request_is_write() -> bool:
    from flask import request
    return request.method in ("POST", "PUT", "PATCH", "DELETE")


def _try_restore_blocking(app) -> None:
    try:
        with app.app_context():
            _state.store.ensure_table()
            restore_all()
    except Exception as exc:
        _state.last_error = f"restore failed: {exc.__class__.__name__}: {exc}"
        logger.error("storage_sync: startup restore failed - will retry in the "
                     "background; nothing will be uploaded or deleted until it succeeds: %s", exc)
