"""
Backup Manager - single source of truth for creating, storing and pruning
backups of the AI Attendance & Payroll System.

Why this module exists
----------------------
Backups used to be built in two places (the scheduler's weekly job and the
admin "Backup Now" route) with three problems:

1. SNOWBALLING. Automated backups were saved to uploads/backups/, and the
   backup zipped the whole uploads/ folder - including uploads/backups/
   itself - so every backup contained all the previous ones and grew
   without bound. Both code paths now use write_backup_zip(), which
   prunes the backup folder(s) from the directory walk.

2. SAME-DISK ONLY. Backups lived next to the data they protect. Now:
     - the guaranteed local copy goes to a DEDICATED folder,
       <BASE_DIR>/backups/ (outside uploads/, so it can never nest), and
     - a second copy is written to an external location when one is
       available: the folder named by BACKUP_DIR in .env if set,
       otherwise the first usable non-system FIXED drive (e.g. D:\\) when
       BACKUP_AUTO_EXTERNAL is not disabled. Removable USB drives are never
       chosen automatically.
   An external-copy failure is logged as a warning; it never fails the
   backup, because the local copy already succeeded.

3. NO CATCH-UP. If the PC was off at the Sunday 02:00 slot, nothing ran
   until the following week. is_backup_overdue() /
   last_backup_time() let the scheduler run a catch-up backup at startup
   when the newest successful backup is older than BACKUP_CATCHUP_DAYS
   (default 7).

Settings (all optional, read from the environment / .env):
    BACKUP_DIR             extra external backup folder, e.g. D:\\AIAPS_Backups
    BACKUP_AUTO_EXTERNAL   "true"/"false" (default true) - auto-detect a
                           second fixed drive when BACKUP_DIR is not set
    BACKUP_KEEP            how many automated backups to keep per folder (default 4)
    BACKUP_CATCHUP_DAYS    age in days after which a startup catch-up
                           backup is triggered (default 7)

Security reminder: automated backups include the .env file (which holds
the face-data encryption key). Treat backup folders like passwords.

Legacy note: older versions wrote backups to uploads/backups/. Those files
are moved into the new dedicated folder the first time a backup runs, so
retention and "last backup" detection keep working.
"""
import os
import json
import shutil
import sqlite3
import tempfile
import threading
import logging
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from config import Config, BASE_DIR

logger = logging.getLogger(__name__)

AUTOMATED_PREFIX = "automated_backup_"
AUTOMATED_SUFFIX = ".zip"
DEDICATED_DIR_NAME = "backups"
EXTERNAL_SUBFOLDER_NAME = "AttendancePayrollSystem_Backups"
STATE_FILE_NAME = "backup_state.json"

_backup_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------
def _env_int(name: str, default: int) -> int:
    try:
        value = int(str(os.environ.get(name, "")).strip())
        return value if value > 0 else default
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def backup_keep_count() -> int:
    return _env_int("BACKUP_KEEP", 4)


def catchup_days() -> int:
    return _env_int("BACKUP_CATCHUP_DAYS", 7)


# ---------------------------------------------------------------------------
# Directory resolution
# ---------------------------------------------------------------------------
def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def local_backup_dir() -> str:
    """The guaranteed, dedicated local folder (outside uploads/)."""
    return os.path.join(BASE_DIR, DEDICATED_DIR_NAME)


def legacy_backup_dir() -> str:
    """Where older versions stored backups (inside uploads/)."""
    return os.path.join(Config.UPLOAD_FOLDER, "backups")


def _is_usable_dir(path: str) -> bool:
    """Create the folder if needed and confirm we can write to it."""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_test.tmp")
        with open(probe, "wb") as f:
            f.write(b"ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def _second_fixed_drive_dir() -> Optional[str]:
    """
    Windows only: the first fixed (non-USB, non-network, non-CD) drive that is
    NOT the drive the app is installed on, with a little free space.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        bitmask = kernel32.GetLogicalDrives()
        app_drive = os.path.splitdrive(os.path.abspath(BASE_DIR))[0].upper()
        system_drive = os.environ.get("SystemDrive", "C:").upper()
        DRIVE_FIXED = 3
        for i in range(26):
            if not bitmask & (1 << i):
                continue
            letter = chr(ord("A") + i)
            drive = f"{letter}:"
            if drive in (app_drive, system_drive):
                continue
            root = drive + "\\"
            if kernel32.GetDriveTypeW(root) != DRIVE_FIXED:
                continue
            try:
                if shutil.disk_usage(root).free < 1024 ** 3:  # need >= 1 GB free
                    continue
            except OSError:
                continue
            return os.path.join(root, EXTERNAL_SUBFOLDER_NAME)
    except Exception as exc:
        logger.debug("Second-drive detection failed: %s", exc)
    return None


def external_backup_dir() -> Optional[str]:
    """
    The secondary (off-primary-disk) backup folder, or None when there is
    none available. BACKUP_DIR wins; otherwise a second fixed drive.
    """
    configured = (os.environ.get("BACKUP_DIR") or "").strip().strip('"')
    if configured:
        if _is_usable_dir(configured):
            return configured
        logger.warning("BACKUP_DIR '%s' is not writable/available - no external copy this run.", configured)
        return None

    if _env_bool("BACKUP_AUTO_EXTERNAL", True):
        candidate = _second_fixed_drive_dir()
        if candidate and _is_usable_dir(candidate):
            return candidate
    return None


def all_backup_dirs() -> List[str]:
    """Every folder that may hold automated backups (for last-backup detection)."""
    dirs = [local_backup_dir(), legacy_backup_dir()]
    configured = (os.environ.get("BACKUP_DIR") or "").strip().strip('"')
    if configured:
        dirs.append(configured)
    seen, unique = set(), []
    for d in dirs:
        key = _norm(d)
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


def _excluded_dirs() -> List[str]:
    """Folders that must NEVER be zipped into a backup (prevents nesting)."""
    dirs = [local_backup_dir(), legacy_backup_dir()]
    configured = (os.environ.get("BACKUP_DIR") or "").strip().strip('"')
    if configured:
        dirs.append(configured)
    ext = _second_fixed_drive_dir()
    if ext:
        dirs.append(ext)
    return [_norm(d) for d in dirs]


def _is_within(path: str, parent: str) -> bool:
    path_n, parent_n = _norm(path), parent
    return path_n == parent_n or path_n.startswith(parent_n + os.sep)


# ---------------------------------------------------------------------------
# Zip creation
# ---------------------------------------------------------------------------
def _sqlite_db_path() -> str:
    return os.path.join(BASE_DIR, "instance", "attendance.db")


def _add_tree(zf: zipfile.ZipFile, folder_path: str, arc_root: str, excluded: List[str]) -> int:
    """Add a folder to the zip, skipping excluded (backup) directories."""
    added = 0
    if not os.path.exists(folder_path):
        return 0
    for root, dirs, files in os.walk(folder_path):
        # Prune excluded dirs IN PLACE so os.walk never descends into them.
        dirs[:] = [d for d in dirs
                   if not any(_is_within(os.path.join(root, d), ex) for ex in excluded)]
        for filename in files:
            file_path = os.path.join(root, filename)
            if any(_is_within(file_path, ex) for ex in excluded):
                continue
            arcname = os.path.join(arc_root, os.path.relpath(file_path, folder_path))
            try:
                zf.write(file_path, arcname=arcname)
                added += 1
            except OSError as exc:
                logger.warning("Backup: skipped unreadable file %s (%s)", file_path, exc)
    return added


def _add_database(zf: zipfile.ZipFile) -> None:
    """Add a consistent snapshot of the SQLite DB (falls back to a plain copy)."""
    db_path = _sqlite_db_path()
    if not os.path.exists(db_path):
        logger.warning("backup: no SQLite file found at %s - skipping database", db_path)
        return

    snapshot = None
    try:
        fd, snapshot = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        src = sqlite3.connect(db_path, timeout=10)
        try:
            dst = sqlite3.connect(snapshot)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        zf.write(snapshot, arcname=os.path.join("instance", "attendance.db"))
    except Exception as exc:
        logger.warning("backup: SQLite snapshot failed (%s); copying the file directly", exc)
        zf.write(db_path, arcname=os.path.join("instance", "attendance.db"))
    finally:
        if snapshot and os.path.exists(snapshot):
            try:
                os.remove(snapshot)
            except OSError:
                pass


def write_backup_zip(fileobj, include_env: bool = False) -> None:
    """
    Write a backup zip into ``fileobj`` (a path or a binary file-like object,
    e.g. io.BytesIO): instance/attendance.db, uploads/, dataset/ and,
    optionally, .env. Backup folders are excluded so backups never contain
    other backups.
    """
    excluded = _excluded_dirs()
    with zipfile.ZipFile(fileobj, "w", zipfile.ZIP_DEFLATED) as zf:
        _add_database(zf)
        _add_tree(zf, Config.UPLOAD_FOLDER, "uploads", excluded)
        _add_tree(zf, Config.DATASET_FOLDER, "dataset", excluded)
        if include_env:
            env_path = os.path.join(BASE_DIR, ".env")
            if os.path.exists(env_path):
                zf.write(env_path, arcname=".env")


# ---------------------------------------------------------------------------
# State / last-backup detection
# ---------------------------------------------------------------------------
def _state_path() -> str:
    return os.path.join(BASE_DIR, "instance", STATE_FILE_NAME)


def _read_state() -> Dict:
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state: Dict) -> None:
    try:
        os.makedirs(os.path.dirname(_state_path()), exist_ok=True)
        tmp = _state_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, _state_path())
    except OSError as exc:
        logger.warning("Could not write backup state file: %s", exc)


def _automated_files(directory: str) -> List[str]:
    try:
        return sorted(
            (f for f in os.listdir(directory)
             if f.startswith(AUTOMATED_PREFIX) and f.endswith(AUTOMATED_SUFFIX)),
            reverse=True,
        )
    except OSError:
        return []


def last_backup_time() -> Optional[datetime]:
    """
    UTC time of the newest successful automated backup, or None. Uses the
    state file first, then falls back to the newest automated zip's mtime
    across all known backup folders (covers upgrades and lost state files).
    """
    candidates: List[datetime] = []

    raw = _read_state().get("last_success_utc")
    if raw:
        try:
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            candidates.append(parsed.astimezone(timezone.utc))
        except ValueError:
            pass

    for directory in all_backup_dirs():
        for name in _automated_files(directory)[:1]:
            try:
                mtime = os.path.getmtime(os.path.join(directory, name))
                candidates.append(datetime.fromtimestamp(mtime, tz=timezone.utc))
            except OSError:
                pass

    return max(candidates) if candidates else None


def is_backup_overdue(max_age_days: Optional[int] = None) -> bool:
    """True if there is no backup yet, or the newest one is older than max_age_days."""
    max_age = timedelta(days=max_age_days if max_age_days is not None else catchup_days())
    last = last_backup_time()
    if last is None:
        return True
    return datetime.now(timezone.utc) - last > max_age


# ---------------------------------------------------------------------------
# Running a backup
# ---------------------------------------------------------------------------
def _migrate_legacy_backups(target_dir: str) -> None:
    """Move automated zips from the old uploads/backups/ into the dedicated folder."""
    legacy = legacy_backup_dir()
    if not os.path.isdir(legacy) or _norm(legacy) == _norm(target_dir):
        return
    for name in _automated_files(legacy):
        src = os.path.join(legacy, name)
        dst = os.path.join(target_dir, name)
        try:
            if os.path.exists(dst):
                continue
            shutil.move(src, dst)
            logger.info("Moved legacy backup %s to %s", name, target_dir)
        except OSError as exc:
            logger.warning("Could not move legacy backup %s: %s", name, exc)


def _prune(directory: str, keep: int) -> None:
    for old in _automated_files(directory)[keep:]:
        try:
            os.remove(os.path.join(directory, old))
            logger.info("Removed old backup: %s", os.path.join(directory, old))
        except OSError as exc:
            logger.warning("Failed to remove old backup %s: %s", old, exc)


def run_backup(now_stamp: Optional[str] = None) -> Dict:
    """
    Create an automated backup (with .env) in the dedicated local folder,
    copy it to the external folder when available, prune old copies and
    record the success time.

    Returns a dict: {"success": bool, "path": str|None, "external_path": str|None,
                     "error": str|None}
    Safe to call from any thread; concurrent calls are serialised.
    """
    result = {"success": False, "path": None, "external_path": None, "error": None}

    if not _backup_lock.acquire(blocking=False):
        result["error"] = "another backup is already running"
        logger.info("Backup skipped: %s", result["error"])
        return result

    try:
        local_dir = local_backup_dir()
        os.makedirs(local_dir, exist_ok=True)
        _migrate_legacy_backups(local_dir)

        if now_stamp is None:
            now_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{AUTOMATED_PREFIX}{now_stamp}{AUTOMATED_SUFFIX}"
        final_path = os.path.join(local_dir, filename)
        tmp_path = final_path + ".tmp"

        # Write to a temp file first so a crash never leaves a half-written
        # zip that looks like a valid backup.
        try:
            write_backup_zip(tmp_path, include_env=True)
            os.replace(tmp_path, final_path)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise

        result["path"] = final_path
        logger.info("Automated backup created successfully: %s", final_path)

        keep = backup_keep_count()
        _prune(local_dir, keep)

        # Second copy off the primary location, when available.
        try:
            ext_dir = external_backup_dir()
            if ext_dir and _norm(ext_dir) != _norm(local_dir):
                ext_path = os.path.join(ext_dir, filename)
                shutil.copy2(final_path, ext_path + ".tmp")
                os.replace(ext_path + ".tmp", ext_path)
                result["external_path"] = ext_path
                logger.info("Backup copied to external location: %s", ext_path)
                _prune(ext_dir, keep)
            else:
                logger.info("No external backup location available - local copy only. "
                            "Set BACKUP_DIR in .env to protect against disk failure.")
        except Exception as exc:
            logger.warning("External backup copy failed (local copy is safe): %s", exc)

        state = {
            "last_success_utc": datetime.now(timezone.utc).isoformat(),
            "last_path": final_path,
            "last_external_path": result["external_path"],
        }
        _write_state(state)
        result["success"] = True
        return result

    except Exception as exc:
        result["error"] = str(exc)
        logger.error("Error in automated backup: %s", exc)
        import traceback
        logger.error(traceback.format_exc())
        return result
    finally:
        _backup_lock.release()