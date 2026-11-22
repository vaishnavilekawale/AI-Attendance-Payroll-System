"""
persistent_store.py - keep the app's files alive on hosts with an EPHEMERAL disk
(e.g. Render's free plan) by mirroring them into the database (Neon/Postgres).

Why: face images (.enc / encrypted .jpg), the trained model, uploads/payslips
and backups are plain files under DATA_DIR. On Render free the container's
disk is wiped at every deploy/restart/spin-down, so they vanish. The database
(Neon) is persistent, so we copy the files there and copy them back at startup.

How it works
  * Startup  -> restore_on_startup(): every file stored in the DB is written
                back to DATA_DIR before the app reads anything.
  * Running  -> after any write request (POST/PUT/PATCH/DELETE) a debounced
                background sync pushes new/changed files to the DB and removes
                rows for files that were deleted. A slow periodic sync (default
                120 s) also catches files written by background jobs (the
                backup scheduler, face capture, payslip generation).
  * Shutdown -> one last sync (atexit).

Enable with the environment variable  FILE_STORE=db  (anything else = off, and
the desktop/.exe install behaves exactly as before).

Optional settings
  FILE_STORE_INTERVAL   seconds between periodic syncs        (default 120)
  FILE_STORE_MAX_FILE_MB  skip single files bigger than this   (default 50)
  FILE_STORE_MAX_TOTAL_MB log a warning above this total size  (default 400)

Limits (honest): Neon's free tier is 0.5 GB total, shared with your normal
tables. A write made in the last few seconds before Render kills the container
can still be lost. Run ONE gunicorn worker (the face model lives in memory).
"""
import atexit
import hashlib
import logging
import os
import threading
from datetime import datetime
from typing import Dict, Iterator, Optional, Tuple

logger = logging.getLogger(__name__)

TABLE_NAME = "aiaps_file_store"

# Folders (relative to DATA_DIR) and single files that are mirrored.
TRACKED_DIRS = ("dataset", "uploads", "trained_model", "backups", "instance")
TRACKED_FILES = ("license.lic",)
# Never mirrored: the sqlite DB itself, journals/temp files, placeholders.
SKIP_SUFFIXES = (".db", ".db-journal", ".db-wal", ".db-shm", ".sqlite", ".tmp", ".part", ".before_restore")
SKIP_NAMES = (".gitkeep",)


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


def is_enabled() -> bool:
    return (os.environ.get("FILE_STORE") or "").strip().lower() in ("db", "database", "true", "1", "on")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------
class SqlBackend:
    """Stores files in one table via SQLAlchemy Core (Postgres, MySQL, SQLite)."""

    def __init__(self, uri: str):
        from sqlalchemy import (Column, DateTime, Integer, LargeBinary, MetaData,
                                String, Table, create_engine)
        self._engine = create_engine(uri, pool_pre_ping=True, pool_recycle=240, pool_size=2, max_overflow=0) \
            if not uri.startswith("sqlite") else create_engine(uri)
        meta = MetaData()
        self._table = Table(
            TABLE_NAME, meta,
            Column("path", String(500), primary_key=True),
            Column("data", LargeBinary, nullable=False),
            Column("size", Integer, nullable=False),
            Column("sha256", String(64), nullable=False),
            Column("updated_at", DateTime, nullable=False),
        )
        meta.create_all(self._engine)

    def list_meta(self) -> Dict[str, str]:
        t = self._table
        with self._engine.connect() as conn:
            return {r[0]: r[1] for r in conn.execute(t.select().with_only_columns(t.c.path, t.c.sha256))}

    def get(self, path: str) -> Optional[bytes]:
        t = self._table
        with self._engine.connect() as conn:
            row = conn.execute(t.select().with_only_columns(t.c.data).where(t.c.path == path)).first()
        return bytes(row[0]) if row else None

    def put(self, path: str, data: bytes, sha: str) -> None:
        t = self._table
        values = dict(data=data, size=len(data), sha256=sha, updated_at=datetime.utcnow())
        with self._engine.begin() as conn:
            res = conn.execute(t.update().where(t.c.path == path).values(**values))
            if res.rowcount == 0:
                conn.execute(t.insert().values(path=path, **values))

    def delete(self, paths) -> None:
        paths = list(paths)
        if not paths:
            return
        t = self._table
        with self._engine.begin() as conn:
            conn.execute(t.delete().where(t.c.path.in_(paths)))


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------
class PersistentStore:
    def __init__(self, data_dir: str, backend, max_file_mb: int = 50, max_total_mb: int = 400):
        self.data_dir = os.path.abspath(data_dir)
        self.backend = backend
        self.max_file_bytes = max_file_mb * 1024 * 1024
        self.max_total_bytes = max_total_mb * 1024 * 1024
        self._lock = threading.Lock()
        self._restored_ok = False
        self._last_scan: Dict[str, Tuple[int, int]] = {}   # rel -> (mtime_ns, size) at last successful sync
        self._sha_cache: Dict[str, Tuple[Tuple[int, int], str]] = {}

    # ---- path helpers ----
    def _abs(self, rel: str) -> str:
        target = os.path.abspath(os.path.join(self.data_dir, *rel.split("/")))
        if target != self.data_dir and not target.startswith(self.data_dir + os.sep):
            raise ValueError(f"unsafe path: {rel}")
        return target

    def _tracked(self) -> Iterator[Tuple[str, str]]:
        for rel in TRACKED_FILES:
            p = os.path.join(self.data_dir, rel)
            if os.path.isfile(p):
                yield rel, p
        for d in TRACKED_DIRS:
            root = os.path.join(self.data_dir, d)
            for dirpath, _dirs, files in os.walk(root):
                for name in files:
                    if name in SKIP_NAMES or name.lower().endswith(SKIP_SUFFIXES):
                        continue
                    full = os.path.join(dirpath, name)
                    rel = os.path.relpath(full, self.data_dir).replace(os.sep, "/")
                    yield rel, full

    # ---- restore ----
    def restore(self) -> int:
        """DB -> disk. The DB is the source of truth at startup. Returns files written."""
        written = 0
        try:
            remote = self.backend.list_meta()
            for rel, sha in remote.items():
                try:
                    target = self._abs(rel)
                except ValueError:
                    logger.warning("file_store: skipping unsafe path %r", rel)
                    continue
                if os.path.isfile(target):
                    with open(target, "rb") as f:
                        if _sha256(f.read()) == sha:
                            continue
                data = self.backend.get(rel)
                if data is None:
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                tmp = target + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, target)
                written += 1
            self._restored_ok = True
            logger.info("file_store: restore complete (%d of %d files written)", written, len(remote))
        except Exception:
            # Never sync (and especially never delete remote rows) after a failed restore.
            self._restored_ok = False
            logger.exception("file_store: restore FAILED - syncing is disabled this run")
        return written

    # ---- sync ----
    def sync(self) -> Dict[str, int]:
        """disk -> DB. Returns {'pushed': n, 'deleted': n}."""
        result = {"pushed": 0, "deleted": 0}
        if not self._restored_ok:
            return result
        with self._lock:
            try:
                scan: Dict[str, Tuple[int, int]] = {}
                paths: Dict[str, str] = {}
                for rel, full in self._tracked():
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue
                    if st.st_size > self.max_file_bytes:
                        logger.warning("file_store: %s is %.1f MB, over the per-file limit - not mirrored",
                                       rel, st.st_size / 1048576)
                        continue
                    scan[rel] = (st.st_mtime_ns, st.st_size)
                    paths[rel] = full
                if scan == self._last_scan:
                    return result  # nothing changed - do not wake the database

                remote = self.backend.list_meta()
                for rel, full in paths.items():
                    sig = scan[rel]
                    cached = self._sha_cache.get(rel)
                    if cached and cached[0] == sig:
                        sha, data = cached[1], None
                    else:
                        with open(full, "rb") as f:
                            data = f.read()
                        sha = _sha256(data)
                        self._sha_cache[rel] = (sig, sha)
                    if remote.get(rel) != sha:
                        if data is None:
                            with open(full, "rb") as f:
                                data = f.read()
                        self.backend.put(rel, data, sha)
                        result["pushed"] += 1

                gone = [r for r in remote if r not in scan]
                if gone and not scan:
                    # Local looks completely empty while the DB has data: far more likely a
                    # wrong DATA_DIR / failed restore than a real "delete everything".
                    logger.error("file_store: local tracked folders are empty but the DB holds %d files - "
                                 "refusing to delete anything", len(remote))
                else:
                    self.backend.delete(gone)
                    result["deleted"] = len(gone)

                total = sum(s for _m, s in scan.values())
                if total > self.max_total_bytes:
                    logger.warning("file_store: mirrored files total %.0f MB (limit hint %.0f MB) - "
                                   "Neon free tier is 512 MB in total", total / 1048576, self.max_total_bytes / 1048576)
                self._last_scan = scan
                if result["pushed"] or result["deleted"]:
                    logger.info("file_store: synced (%d pushed, %d deleted)", result["pushed"], result["deleted"])
            except Exception:
                logger.exception("file_store: sync failed (will retry)")
        return result


# --------------------------------------------------------------------------
# Module-level wiring
# --------------------------------------------------------------------------
_store: Optional[PersistentStore] = None
_timer: Optional[threading.Timer] = None
_timer_lock = threading.Lock()


def _build_store() -> Optional[PersistentStore]:
    from config import Config, DATA_DIR
    uri = Config.SQLALCHEMY_DATABASE_URI
    if uri.startswith("sqlite"):
        logger.warning("file_store: FILE_STORE=db but DATABASE_URL is SQLite - on an ephemeral host the "
                       "SQLite file is lost too. Set DATABASE_URL to your Neon connection string.")
    return PersistentStore(DATA_DIR, SqlBackend(uri),
                           max_file_mb=_env_int("FILE_STORE_MAX_FILE_MB", 50),
                           max_total_mb=_env_int("FILE_STORE_MAX_TOTAL_MB", 400))


def restore_on_startup() -> None:
    """Call once, very early in app.py (before anything reads dataset/ or trained_model/)."""
    global _store
    if not is_enabled():
        return
    try:
        _store = _build_store()
        _store.restore()
    except Exception:
        logger.exception("file_store: could not initialise - files will NOT persist")
        _store = None


def request_sync(delay: float = 5.0) -> None:
    """Debounced: many requests in a short time cause a single sync."""
    global _timer
    if _store is None:
        return
    with _timer_lock:
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(delay, _store.sync)
        _timer.daemon = True
        _timer.start()


def init_app(app) -> None:
    """Hook into Flask: sync after write requests, periodically, and at exit."""
    if _store is None:
        return

    @app.after_request
    def _sync_after_write(response):
        from flask import request
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            request_sync()
        return response

    interval = max(30, _env_int("FILE_STORE_INTERVAL", 120))

    def _loop():
        stop = threading.Event()
        while not stop.wait(interval):
            _store.sync()

    threading.Thread(target=_loop, name="file-store-sync", daemon=True).start()
    atexit.register(lambda: _store and _store.sync())
    logger.info("file_store: enabled (interval %ss)", interval)
