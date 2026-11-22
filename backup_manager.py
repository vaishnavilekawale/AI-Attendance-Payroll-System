"""
Backup Manager - single source of truth for creating, verifying, encrypting,
storing, pruning and RESTORING backups of the AI Attendance & Payroll System.

What it does
------------
* DAILY backup at a time the admin picks in Settings -> Data Backup
  (default 02:00). Stored in instance/backup_settings.json.
* CATCH-UP: if the PC/server was off at the scheduled time, the first
  startup afterwards (about 60 s later) runs the missed backup
  (is_backup_overdue()).
* RETENTION: the newest N automated backups are kept per folder
  (default 7 = one week of daily backups; admin-configurable 1-60).
* TWO COPIES: a guaranteed local copy in <BASE_DIR>/backups/ (outside
  uploads/ so backups never nest) plus an external copy when available
  (BACKUP_DIR in .env, otherwise the first usable non-system FIXED drive).
  An external-copy failure never fails the backup.
* CONTENT: instance/attendance.db (consistent SQLite snapshot), uploads/,
  dataset/, license.lic and - for automated backups - .env (it holds the
  face-data encryption key, needed to read the encrypted face images).
* ENCRYPTION: when the admin sets a backup password, every automated backup
  is written as <name>.zip.enc (AES-256-GCM, key from PBKDF2-SHA256).
  Without a password the backup is a plain .zip - treat it like a password.
* VERIFICATION: every backup is re-opened before it counts as a success
  (zip CRC test, database presence, PRAGMA integrity_check; encrypted files
  are fully decrypted/authenticated). A backup that fails verification is
  deleted and recorded as a failure.
* HEALTH: backup_status() / backup_health() feed the Settings page and the
  dashboard warning banner (failed / never run / too old).
* RESTORE: restore_backup() (Settings page and restore_backup.py CLI).
  It validates the file, takes a pre_restore_* safety backup first, then
  restores database, uploads/, dataset/ and optionally .env / license.lic.

Settings (instance/backup_settings.json, edited from the Settings page):
    time      "HH:MM" daily backup time (default 02:00)
    keep      number of automated backups to keep per folder (default 7)
    password  optional backup password (enables encryption)
Optional .env overrides / fallbacks:
    BACKUP_DIR             extra external backup folder, e.g. D:\\AIAPS_Backups
    BACKUP_AUTO_EXTERNAL   "true"/"false" (default true)
    BACKUP_TIME, BACKUP_KEEP   fallbacks used when the settings file has none

Legacy note: older versions wrote backups to uploads/backups/. Those files
are moved into the dedicated folder the first time a backup runs.
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
from typing import Dict, List, Optional, Tuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from config import Config, BASE_DIR, DATA_DIR

logger = logging.getLogger(__name__)

AUTOMATED_PREFIX = "automated_backup_"
PRE_RESTORE_PREFIX = "pre_restore_"
PLAIN_SUFFIX = ".zip"
ENCRYPTED_SUFFIX = ".zip.enc"
BACKUP_SUFFIXES = (ENCRYPTED_SUFFIX, PLAIN_SUFFIX)
DEDICATED_DIR_NAME = "backups"
EXTERNAL_SUBFOLDER_NAME = "AttendancePayrollSystem_Backups"
STATE_FILE_NAME = "backup_state.json"
SETTINGS_FILE_NAME = "backup_settings.json"
LICENSE_FILE_NAME = "license.lic"

DEFAULT_TIME = "02:00"
DEFAULT_KEEP = 7
MAX_KEEP = 60
MIN_PASSWORD_LENGTH = 8
PRE_RESTORE_KEEP = 3

# Encrypted-file format: MAGIC | salt(16) | nonce_prefix(4) | chunks...
# chunk = length(4, big endian) | AES-GCM ciphertext (+16 byte tag) of <= CHUNK bytes.
# Nonce = nonce_prefix + chunk index (8 bytes). The AAD marks the last chunk, so
# truncating, re-ordering or editing the file is detected.
ENC_MAGIC = b"AIAPSBK1"
ENC_CHUNK = 4 * 1024 * 1024
ENC_KDF_ITERATIONS = 390_000

_backup_lock = threading.Lock()


class BackupError(Exception):
    """A backup/restore problem whose message is safe to show to the admin."""


class BackupPasswordError(BackupError):
    """Wrong backup password (or the encrypted file is damaged)."""


# ---------------------------------------------------------------------------
# Settings (instance/backup_settings.json)
# ---------------------------------------------------------------------------
def _settings_path() -> str:
    return os.path.join(DATA_DIR, "instance", SETTINGS_FILE_NAME)


def _read_json(path: str) -> Dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: str, data: Dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def parse_backup_time(value: str) -> Tuple[int, int]:
    """'HH:MM' -> (hour, minute). Raises ValueError for anything else."""
    try:
        hh, mm = str(value).strip().split(":")
        hour, minute = int(hh), int(mm)
    except (ValueError, AttributeError):
        raise ValueError("Backup time must look like HH:MM, e.g. 02:00 or 22:30.")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Backup time must be between 00:00 and 23:59.")
    return hour, minute


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


def schedule_time_str() -> str:
    """The daily backup time as 'HH:MM' (settings file > BACKUP_TIME > 02:00)."""
    for candidate in (_read_json(_settings_path()).get("time"), os.environ.get("BACKUP_TIME")):
        if candidate:
            try:
                hour, minute = parse_backup_time(candidate)
                return f"{hour:02d}:{minute:02d}"
            except ValueError:
                continue
    return DEFAULT_TIME


def schedule_hm() -> Tuple[int, int]:
    return parse_backup_time(schedule_time_str())


def backup_keep_count() -> int:
    """How many automated backups to keep per folder (default 7)."""
    raw = _read_json(_settings_path()).get("keep")
    try:
        value = int(raw)
        if 1 <= value <= MAX_KEEP:
            return value
    except (TypeError, ValueError):
        pass
    value = _env_int("BACKUP_KEEP", DEFAULT_KEEP)
    return min(value, MAX_KEEP)


def get_backup_password() -> Optional[str]:
    password = _read_json(_settings_path()).get("password")
    return password if isinstance(password, str) and password else None


def save_backup_settings(time_str: Optional[str] = None, keep: Optional[int] = None,
                         new_password: Optional[str] = None,
                         clear_password: bool = False) -> Dict:
    """
    Validate and persist the schedule / retention / password. Raises
    BackupError (message safe to flash) on invalid input. Returns the saved
    non-secret settings.
    """
    data = _read_json(_settings_path())

    if time_str is not None:
        try:
            hour, minute = parse_backup_time(time_str)
        except ValueError as exc:
            raise BackupError(str(exc))
        data["time"] = f"{hour:02d}:{minute:02d}"

    if keep is not None:
        try:
            keep = int(keep)
        except (TypeError, ValueError):
            raise BackupError("Number of backups to keep must be a whole number.")
        if not (1 <= keep <= MAX_KEEP):
            raise BackupError(f"Number of backups to keep must be between 1 and {MAX_KEEP}.")
        data["keep"] = keep

    if clear_password:
        data.pop("password", None)
    elif new_password:
        if len(new_password) < MIN_PASSWORD_LENGTH:
            raise BackupError(f"Backup password must be at least {MIN_PASSWORD_LENGTH} characters.")
        data["password"] = new_password

    try:
        _write_json(_settings_path(), data)
    except OSError as exc:
        raise BackupError(f"Could not save backup settings: {exc}")
    return {"time": schedule_time_str(), "keep": backup_keep_count(),
            "encrypted": get_backup_password() is not None}


# ---------------------------------------------------------------------------
# Encryption (AES-256-GCM, chunked so large backups never sit in memory)
# ---------------------------------------------------------------------------
def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=ENC_KDF_ITERATIONS)
    return kdf.derive(password.encode("utf-8"))


def is_encrypted_file(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(ENC_MAGIC)) == ENC_MAGIC
    except OSError:
        return False


def encrypt_file(src_path: str, dst_path: str, password: str) -> None:
    salt, prefix = os.urandom(16), os.urandom(4)
    aes = AESGCM(_derive_key(password, salt))
    with open(src_path, "rb") as fin, open(dst_path, "wb") as fout:
        fout.write(ENC_MAGIC + salt + prefix)
        index = 0
        current = fin.read(ENC_CHUNK)
        while True:
            upcoming = fin.read(ENC_CHUNK) if current else b""
            final = not upcoming
            nonce = prefix + index.to_bytes(8, "big")
            ciphertext = aes.encrypt(nonce, current, b"L" if final else b"M")
            fout.write(len(ciphertext).to_bytes(4, "big") + ciphertext)
            if final:
                break
            current, index = upcoming, index + 1


def iter_decrypt(path: str, password: str):
    """Yield the plaintext chunks of an encrypted backup (authenticating each)."""
    with open(path, "rb") as f:
        header = f.read(len(ENC_MAGIC) + 16 + 4)
        if len(header) != len(ENC_MAGIC) + 20 or not header.startswith(ENC_MAGIC):
            raise BackupError("This file is not an encrypted AIAPS backup.")
        salt = header[len(ENC_MAGIC):len(ENC_MAGIC) + 16]
        prefix = header[-4:]
        aes = AESGCM(_derive_key(password, salt))

        length_bytes = f.read(4)
        index = 0
        while True:
            if len(length_bytes) != 4:
                raise BackupError("The encrypted backup is truncated or damaged.")
            n = int.from_bytes(length_bytes, "big")
            if n < 16 or n > ENC_CHUNK + 16:
                raise BackupError("The encrypted backup is damaged.")
            ciphertext = f.read(n)
            if len(ciphertext) != n:
                raise BackupError("The encrypted backup is truncated or damaged.")
            length_bytes = f.read(4)
            final = length_bytes == b""
            nonce = prefix + index.to_bytes(8, "big")
            try:
                yield aes.decrypt(nonce, ciphertext, b"L" if final else b"M")
            except InvalidTag:
                raise BackupPasswordError(
                    "Wrong backup password, or the backup file is damaged.")
            if final:
                return
            index += 1


def decrypt_file(src_path: str, dst_path: str, password: str) -> None:
    with open(dst_path, "wb") as fout:
        for chunk in iter_decrypt(src_path, password):
            fout.write(chunk)


def verify_encrypted_file(path: str, password: str) -> None:
    """Decrypt (and authenticate) the whole file without writing it anywhere."""
    for _ in iter_decrypt(path, password):
        pass


# ---------------------------------------------------------------------------
# Directory resolution
# ---------------------------------------------------------------------------
def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def local_backup_dir() -> str:
    """The guaranteed, dedicated local folder (outside uploads/)."""
    return os.path.join(DATA_DIR, DEDICATED_DIR_NAME)


def legacy_backup_dir() -> str:
    """Where older versions stored backups (inside uploads/)."""
    return os.path.join(Config.UPLOAD_FOLDER, "backups")


def _configured_dir() -> str:
    return (os.environ.get("BACKUP_DIR") or "").strip().strip('"')


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
    configured = _configured_dir()
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
    if _configured_dir():
        dirs.append(_configured_dir())
    seen, unique = set(), []
    for d in dirs:
        key = _norm(d)
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


def backup_search_dirs() -> List[str]:
    """Folders searched for restorable backup files (existing folders only)."""
    dirs = list(all_backup_dirs())
    auto = _second_fixed_drive_dir()
    if auto and os.path.isdir(auto):
        dirs.append(auto)
    seen, unique = set(), []
    for d in dirs:
        key = _norm(d)
        if key not in seen and os.path.isdir(d):
            seen.add(key)
            unique.append(d)
    return unique


def _excluded_dirs() -> List[str]:
    """Folders that must NEVER be zipped into a backup (prevents nesting)."""
    dirs = [local_backup_dir(), legacy_backup_dir()]
    if _configured_dir():
        dirs.append(_configured_dir())
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
    return os.path.join(DATA_DIR, "instance", "attendance.db")


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
        zf.write(snapshot, arcname="instance/attendance.db")
    except Exception as exc:
        logger.warning("backup: SQLite snapshot failed (%s); copying the file directly", exc)
        zf.write(db_path, arcname="instance/attendance.db")
    finally:
        if snapshot and os.path.exists(snapshot):
            try:
                os.remove(snapshot)
            except OSError:
                pass


def write_backup_zip(fileobj, include_env: bool = False) -> None:
    """
    Write a backup zip into ``fileobj`` (a path or a binary file-like object,
    e.g. io.BytesIO): instance/attendance.db, uploads/, dataset/, license.lic
    (when present) and, optionally, .env. Backup folders are excluded so
    backups never contain other backups.
    """
    excluded = _excluded_dirs()
    with zipfile.ZipFile(fileobj, "w", zipfile.ZIP_DEFLATED) as zf:
        _add_database(zf)
        _add_tree(zf, Config.UPLOAD_FOLDER, "uploads", excluded)
        _add_tree(zf, Config.DATASET_FOLDER, "dataset", excluded)
        license_path = os.path.join(DATA_DIR, LICENSE_FILE_NAME)
        if os.path.exists(license_path):
            zf.write(license_path, arcname=LICENSE_FILE_NAME)
        if include_env:
            env_path = os.path.join(BASE_DIR, ".env")
            if os.path.exists(env_path):
                zf.write(env_path, arcname=".env")


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def _check_db_integrity(db_file: str) -> None:
    try:
        conn = sqlite3.connect(db_file)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise BackupError(f"The database inside the backup cannot be opened ({exc}).")
    if not row or str(row[0]).lower() != "ok":
        raise BackupError("The database inside the backup failed its integrity check.")


def verify_backup_zip(zip_path: str, expect_db: bool = True) -> List[str]:
    """
    Re-open a finished zip and make sure it is usable: every entry passes its
    CRC check, the database is present (when expected) and passes SQLite's
    integrity check. Returns the entry names. Raises BackupError otherwise.
    """
    try:
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad:
                raise BackupError(f"The backup file is damaged (bad entry: {bad}).")
            names = zf.namelist()
            if "instance/attendance.db" in names:
                tmpdir = tempfile.mkdtemp(prefix="aiaps_verify_")
                try:
                    target = os.path.join(tmpdir, "attendance.db")
                    with zf.open("instance/attendance.db") as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    _check_db_integrity(target)
                finally:
                    shutil.rmtree(tmpdir, ignore_errors=True)
            elif expect_db:
                raise BackupError("The backup does not contain the database (instance/attendance.db).")
            return names
    except zipfile.BadZipFile:
        raise BackupError("The file is not a valid backup zip.")


# ---------------------------------------------------------------------------
# State / last-backup detection
# ---------------------------------------------------------------------------
def _state_path() -> str:
    return os.path.join(DATA_DIR, "instance", STATE_FILE_NAME)


def _read_state() -> Dict:
    return _read_json(_state_path())


def _update_state(**changes) -> None:
    state = _read_state()
    state.update(changes)
    try:
        _write_json(_state_path(), state)
    except OSError as exc:
        logger.warning("Could not write backup state file: %s", exc)


def _backup_files(directory: str, prefix: str = AUTOMATED_PREFIX) -> List[str]:
    """Backup file names with the given prefix, newest first (names carry a timestamp)."""
    try:
        return sorted(
            (f for f in os.listdir(directory)
             if f.startswith(prefix) and f.endswith(BACKUP_SUFFIXES)),
            reverse=True,
        )
    except OSError:
        return []


_automated_files = _backup_files  # backwards-compatible name


def last_backup_time() -> Optional[datetime]:
    """
    UTC time of the newest successful automated backup, or None. Uses the
    state file first, then falls back to the newest automated file's mtime
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
        for name in _backup_files(directory)[:1]:
            try:
                mtime = os.path.getmtime(os.path.join(directory, name))
                candidates.append(datetime.fromtimestamp(mtime, tz=timezone.utc))
            except OSError:
                pass

    return max(candidates) if candidates else None


def most_recent_slot(now: Optional[datetime] = None) -> datetime:
    """The latest scheduled backup moment that is <= now (naive local time)."""
    now = now or datetime.now()
    hour, minute = schedule_hm()
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot > now:
        slot -= timedelta(days=1)
    return slot


def next_slot(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now()
    return most_recent_slot(now) + timedelta(days=1)


def is_backup_overdue(now: Optional[datetime] = None) -> bool:
    """
    True when the scheduled backup was missed: there is no backup yet, or the
    newest one is older than the most recent scheduled slot. At startup this
    is what triggers the catch-up backup after the PC/server was switched off.
    """
    now = now or datetime.now()
    last = last_backup_time()
    if last is None:
        return True
    last_local = last.astimezone().replace(tzinfo=None)
    return last_local < most_recent_slot(now)


def backup_status() -> Dict:
    """Everything the Settings page shows about backups (no secrets)."""
    state = _read_state()
    last = last_backup_time()
    last_local = last.astimezone().replace(tzinfo=None) if last else None
    ext = None
    try:
        ext = external_backup_dir()
    except Exception:
        ext = None
    return {
        "time": schedule_time_str(),
        "keep": backup_keep_count(),
        "encrypted": get_backup_password() is not None,
        "last_success": last_local,
        "last_path": state.get("last_path"),
        "last_external_path": state.get("last_external_path"),
        "last_error": state.get("last_error"),
        "last_error_utc": state.get("last_error_utc"),
        "last_external_error": state.get("last_external_error"),
        "external_dir": ext,
        "local_dir": local_backup_dir(),
        "overdue": is_backup_overdue(),
        "next_run": next_slot(),
    }


def backup_health() -> Optional[Dict]:
    """
    None when backups look healthy; otherwise {"level": "danger"|"warning",
    "message": str} for the dashboard banner.
    """
    state = _read_state()
    last = last_backup_time()

    err_raw = state.get("last_error_utc")
    if state.get("last_error") and err_raw:
        try:
            err_time = datetime.fromisoformat(err_raw)
            if err_time.tzinfo is None:
                err_time = err_time.replace(tzinfo=timezone.utc)
            if last is None or err_time > last:
                return {"level": "danger",
                        "message": f"The last automatic backup FAILED: {state['last_error']}"}
        except ValueError:
            pass

    if last is None:
        return {"level": "warning",
                "message": "No backup has been made yet. A backup runs automatically at "
                           f"{schedule_time_str()} every day, or use Settings -> Data Backup -> Run backup now."}

    age = datetime.now(timezone.utc) - last
    if age > timedelta(days=2):
        return {"level": "warning",
                "message": f"The last successful backup was {age.days} days ago. "
                           "Keep the app/PC running at the scheduled time, or run a backup now."}
    return None


# ---------------------------------------------------------------------------
# Creating a backup file (shared by run_backup and the pre-restore safety copy)
# ---------------------------------------------------------------------------
def _remove_quietly(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _cleanup_stale_tmp(directory: str) -> None:
    """Remove half-written *.tmp files left by a crash (they may hold .env in plain text)."""
    try:
        for name in os.listdir(directory):
            if name.endswith(".tmp") and (name.startswith(AUTOMATED_PREFIX)
                                          or name.startswith(PRE_RESTORE_PREFIX)):
                _remove_quietly(os.path.join(directory, name))
    except OSError:
        pass


def _create_backup_file(directory: str, prefix: str, stamp: str) -> Tuple[str, bool]:
    """
    Build, verify and (when a backup password is set) encrypt one backup in
    ``directory``. Returns (final_path, encrypted). Raises on any failure and
    never leaves a half-written file behind.
    """
    password = get_backup_password()
    base = f"{prefix}{stamp}"
    plain_tmp = os.path.join(directory, base + ".zip.tmp")
    final_path = os.path.join(directory, base + (ENCRYPTED_SUFFIX if password else PLAIN_SUFFIX))
    enc_tmp = final_path + ".tmp"
    expect_db = os.path.exists(_sqlite_db_path())

    try:
        write_backup_zip(plain_tmp, include_env=True)
        verify_backup_zip(plain_tmp, expect_db=expect_db)
        if password:
            encrypt_file(plain_tmp, enc_tmp, password)
            verify_encrypted_file(enc_tmp, password)
            os.replace(enc_tmp, final_path)
            _remove_quietly(plain_tmp)
        else:
            os.replace(plain_tmp, final_path)
    except Exception:
        _remove_quietly(plain_tmp)
        _remove_quietly(enc_tmp)
        _remove_quietly(final_path)
        raise
    return final_path, bool(password)


# ---------------------------------------------------------------------------
# Running a backup
# ---------------------------------------------------------------------------
def _migrate_legacy_backups(target_dir: str) -> None:
    """Move automated backups from the old uploads/backups/ into the dedicated folder."""
    legacy = legacy_backup_dir()
    if not os.path.isdir(legacy) or _norm(legacy) == _norm(target_dir):
        return
    for name in _backup_files(legacy):
        src = os.path.join(legacy, name)
        dst = os.path.join(target_dir, name)
        try:
            if os.path.exists(dst):
                continue
            shutil.move(src, dst)
            logger.info("Moved legacy backup %s to %s", name, target_dir)
        except OSError as exc:
            logger.warning("Could not move legacy backup %s: %s", name, exc)


def _prune(directory: str, keep: int, prefix: str = AUTOMATED_PREFIX) -> None:
    for old in _backup_files(directory, prefix)[keep:]:
        try:
            os.remove(os.path.join(directory, old))
            logger.info("Removed old backup: %s", os.path.join(directory, old))
        except OSError as exc:
            logger.warning("Failed to remove old backup %s: %s", old, exc)


def run_backup(now_stamp: Optional[str] = None) -> Dict:
    """
    Create an automated backup (with .env and license.lic) in the dedicated
    local folder, verify it, encrypt it when a backup password is set, copy it
    to the external folder when available, prune old copies and record the
    outcome (success time, or the error for the dashboard warning).

    Returns: {"success", "path", "external_path", "external_error", "encrypted", "error"}
    Safe to call from any thread; concurrent calls are serialised.
    """
    result = {"success": False, "path": None, "external_path": None,
              "external_error": None, "encrypted": False, "error": None}

    if not _backup_lock.acquire(blocking=False):
        result["error"] = "another backup is already running"
        logger.info("Backup skipped: %s", result["error"])
        return result

    try:
        local_dir = local_backup_dir()
        os.makedirs(local_dir, exist_ok=True)
        _cleanup_stale_tmp(local_dir)
        _migrate_legacy_backups(local_dir)

        if now_stamp is None:
            now_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        final_path, encrypted = _create_backup_file(local_dir, AUTOMATED_PREFIX, now_stamp)
        filename = os.path.basename(final_path)
        result["path"], result["encrypted"] = final_path, encrypted
        logger.info("Automated backup created and verified: %s (encrypted=%s)", final_path, encrypted)

        keep = backup_keep_count()
        _prune(local_dir, keep)

        # Second copy off the primary location, when available.
        try:
            ext_dir = external_backup_dir()
            if ext_dir and _norm(ext_dir) != _norm(local_dir):
                _cleanup_stale_tmp(ext_dir)
                ext_path = os.path.join(ext_dir, filename)
                shutil.copy2(final_path, ext_path + ".tmp")
                if os.path.getsize(ext_path + ".tmp") != os.path.getsize(final_path):
                    raise OSError("copied file size does not match the original")
                os.replace(ext_path + ".tmp", ext_path)
                result["external_path"] = ext_path
                logger.info("Backup copied to external location: %s", ext_path)
                _prune(ext_dir, keep)
            else:
                logger.info("No external backup location available - local copy only. "
                            "Set BACKUP_DIR in .env to protect against disk failure.")
        except Exception as exc:
            result["external_error"] = str(exc)
            logger.warning("External backup copy failed (local copy is safe): %s", exc)

        _update_state(
            last_success_utc=datetime.now(timezone.utc).isoformat(),
            last_path=final_path,
            last_external_path=result["external_path"],
            last_external_error=result["external_error"],
            last_encrypted=encrypted,
            last_error=None,
            last_error_utc=None,
        )
        result["success"] = True
        return result

    except Exception as exc:
        result["error"] = str(exc) or exc.__class__.__name__
        logger.error("Error in automated backup: %s", exc)
        import traceback
        logger.error(traceback.format_exc())
        _update_state(last_error=result["error"],
                      last_error_utc=datetime.now(timezone.utc).isoformat())
        return result
    finally:
        _backup_lock.release()


# ---------------------------------------------------------------------------
# Listing backups (for the Restore dropdown)
# ---------------------------------------------------------------------------
def list_backups() -> List[Dict]:
    """
    Every restorable backup file (*.zip / *.zip.enc) in the backup folders,
    newest first. ``ref`` is "<folder index>|<file name>" and is what the
    restore form posts; resolve_backup() turns it back into a safe path.
    """
    items = []
    for index, directory in enumerate(backup_search_dirs()):
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            if not name.endswith(BACKUP_SUFFIXES):
                continue
            path = os.path.join(directory, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            items.append({
                "ref": f"{index}|{name}",
                "name": name,
                "folder": directory,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime),
                "encrypted": name.endswith(ENCRYPTED_SUFFIX),
                "is_safety_copy": name.startswith(PRE_RESTORE_PREFIX),
            })
    items.sort(key=lambda i: i["modified"], reverse=True)
    return items


def resolve_backup(ref: str) -> str:
    """Turn a posted ``ref`` into a real path, refusing anything outside the backup folders."""
    try:
        index_str, name = str(ref).split("|", 1)
        index = int(index_str)
    except ValueError:
        raise BackupError("Please choose a backup from the list.")
    dirs = backup_search_dirs()
    if not (0 <= index < len(dirs)) or os.path.basename(name) != name or \
            not name.endswith(BACKUP_SUFFIXES):
        raise BackupError("Please choose a backup from the list.")
    path = os.path.join(dirs[index], name)
    if not os.path.isfile(path):
        raise BackupError("That backup file no longer exists.")
    return path


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------
def _safe_member_parts(name: str) -> Optional[List[str]]:
    """Path parts of a zip entry, or None if it must be ignored. Raises on path tricks."""
    normalized = name.replace("\\", "/")
    if normalized.endswith("/"):
        return None
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if normalized.startswith("/") or ".." in parts or (parts and ":" in parts[0]):
        raise BackupError(f"The backup contains an unsafe path ({name}); restore refused.")
    return parts or None


def _extract_member(zf: zipfile.ZipFile, member: str, parts: List[str]) -> str:
    # .env stays next to the app; everything else lives under DATA_DIR.
    root = BASE_DIR if parts == [".env"] else DATA_DIR
    target = os.path.join(root, *parts)
    if not _is_within(target, _norm(root)):
        raise BackupError(f"The backup contains an unsafe path ({member}); restore refused.")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with zf.open(member) as src, open(target, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return target


def _restore_database(extracted_db: str, replace_file: bool) -> None:
    live = _sqlite_db_path()
    os.makedirs(os.path.dirname(live), exist_ok=True)
    if replace_file:
        # App is stopped (command-line restore): swap the file, drop WAL leftovers.
        for suffix in ("-wal", "-shm", "-journal"):
            _remove_quietly(live + suffix)
        shutil.copyfile(extracted_db, live)
        return
    # App is running: copy page-by-page through SQLite's backup API, which
    # works while the file is open (a plain file replace fails on Windows).
    try:
        src = sqlite3.connect(extracted_db)
        try:
            dst = sqlite3.connect(live, timeout=30)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except sqlite3.Error as exc:
        raise BackupError(
            f"The database could not be replaced while the app is running ({exc}). "
            "Nothing was lost - a safety backup was made first. Close other browser "
            "tabs and try again, or stop the app and use restore_backup.py.")


def restore_backup(path: str, password: Optional[str] = None, restore_env: bool = True,
                   replace_db_file: bool = False) -> Dict:
    """
    Restore a backup file (.zip or encrypted .zip.enc).

    Steps: decrypt -> validate (CRC, DB integrity, safe paths) -> pre_restore_*
    safety backup of the CURRENT data -> restore database, uploads/, dataset/
    -> optionally .env (the old one is kept as .env.before_restore) and
    license.lic (only when this install has none).

    Returns {"success", "error", "safety_backup", "files_restored", "env_restored",
             "license_restored", "restart_required"}. Never raises for expected problems.
    """
    result = {"success": False, "error": None, "safety_backup": None, "files_restored": 0,
              "env_restored": False, "license_restored": False, "restart_required": False}

    if not os.path.isfile(path):
        result["error"] = "The backup file was not found."
        return result
    if not _backup_lock.acquire(blocking=False):
        result["error"] = "A backup is running right now - please try again in a minute."
        return result

    tmpdir = tempfile.mkdtemp(prefix="aiaps_restore_")
    try:
        zip_path = path
        if is_encrypted_file(path):
            if not password:
                raise BackupPasswordError("This backup is encrypted - enter the backup password.")
            zip_path = os.path.join(tmpdir, "backup.zip")
            decrypt_file(path, zip_path, password)

        names = verify_backup_zip(zip_path, expect_db=True)
        plan = []  # (member, parts)
        for member in names:
            parts = _safe_member_parts(member)
            if parts:
                plan.append((member, parts))

        # Safety copy of what is on this machine right now.
        local_dir = local_backup_dir()
        os.makedirs(local_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            safety_path, _ = _create_backup_file(local_dir, PRE_RESTORE_PREFIX, stamp)
            result["safety_backup"] = safety_path
            _prune(local_dir, PRE_RESTORE_KEEP, PRE_RESTORE_PREFIX)
        except Exception as exc:
            raise BackupError(f"Could not make the safety backup of the current data first ({exc}). "
                              "Restore cancelled - nothing was changed.")

        with zipfile.ZipFile(zip_path) as zf:
            for member, parts in plan:
                top = parts[0]
                if parts == ["instance", "attendance.db"]:
                    extracted = os.path.join(tmpdir, "attendance.db")
                    with zf.open(member) as src, open(extracted, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    _restore_database(extracted, replace_db_file)
                elif top in ("uploads", "dataset") and len(parts) > 1:
                    _extract_member(zf, member, parts)
                    result["files_restored"] += 1
                elif parts == [".env"] and restore_env:
                    env_path = os.path.join(BASE_DIR, ".env")
                    if os.path.exists(env_path):
                        shutil.copyfile(env_path, env_path + ".before_restore")
                    _extract_member(zf, member, parts)
                    result["env_restored"] = True
                elif parts == [LICENSE_FILE_NAME]:
                    if not os.path.exists(os.path.join(DATA_DIR, LICENSE_FILE_NAME)):
                        _extract_member(zf, member, parts)
                        result["license_restored"] = True

        result["restart_required"] = True
        result["success"] = True
        logger.warning("RESTORE completed from %s (safety backup: %s)", path, result["safety_backup"])
        return result

    except BackupError as exc:
        result["error"] = str(exc)
        logger.error("Restore failed: %s", exc)
        return result
    except Exception as exc:
        result["error"] = f"Restore failed: {exc}"
        logger.error("Restore failed: %s", exc, exc_info=True)
        return result
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        _backup_lock.release()
