import sys
import os
import tempfile
import logging
import secrets as _secrets_module
from datetime import timedelta
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler

logger = logging.getLogger(__name__)

# BASE_DIR must be where WRITABLE, PERSISTENT data lives - never
# sys._MEIPASS. In a frozen PyInstaller build, `__file__` resolves
# inside a temp extraction folder (onefile) or the bundle's internal
# folder (onedir) - NOT a location you can rely on for persistent data.
# Anything written under a __file__-derived path in that case - the
# database, uploaded photos, trained face embeddings, generated
# payslips - would be silently lost every time the app closes. When
# frozen, redirect to the folder containing the actual .exe instead,
# which persists across runs exactly like a normal installed app.
#
# IMPORTANT PACKAGING NOTE: this resolution is correct in BOTH PyInstaller
# onefile and onedir modes (sys.executable always points at the real,
# persistent .exe location in both), but onedir is the recommended build
# mode for this app - see attendance_app.spec. Onefile re-extracts the
# entire bundle (TensorFlow, MediaPipe, etc.) to a brand-new temp folder
# on every single launch, which is slower and gives antivirus/temp-cleanup
# tools far more opportunity to interfere with a launch in progress.
# BASE_DIR itself is unaffected either way, but onedir is the safer,
# simpler architecture for an app that must reliably persist local state.
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ENV_FILE = os.path.join(BASE_DIR, '.env')
load_dotenv(ENV_FILE)

# SQLite will not create a missing parent directory on its own - without
# this, a fresh install (first double-click, nothing exists yet) fails
# with "unable to open database file" the moment SQLAlchemy tries to
# open instance/attendance.db.
os.makedirs(os.path.join(BASE_DIR, 'instance'), exist_ok=True)

# ---------------------------------------------------------------------
# LOGGING - configured HERE, not in app.py, and as the very first thing
# after BASE_DIR is known. Two real bugs this fixes:
#
# 1. ORDERING BUG: config.py is imported very early in app.py's import
#    chain (e.g. transitively via `import crypto_utils`, which does
#    `from config import BASE_DIR`) - well before app.py's own
#    `logging.basicConfig(...)` call further down that file. That meant
#    every diagnostic logger.info() call below (BASE_DIR resolved to...,
#    the SQLite path, etc.) was previously emitted to a root logger with
#    NO handlers attached yet, so Python's logging module silently
#    dropped them - they never appeared anywhere, console or file, even
#    in a normal `python app.py` dev run. Configuring handlers here,
#    before those calls, is what makes them actually show up.
# 2. VISIBILITY IN A WINDOWED BUILD: attendance_app.spec ships with
#    console=False (intentional - see that file's comments), which on
#    Windows means the process has no console and sys.stdout/sys.stderr
#    are None. A StreamHandler alone is therefore useless for a
#    customer's windowed .exe - there is nothing for it to write to, and
#    logging silently swallows the failure rather than crashing. Adding
#    a RotatingFileHandler pointed at BASE_DIR/logs/app.log means these
#    diagnostics - most importantly the resolved database path below,
#    which is the single fastest way to diagnose a "my data disappeared"
#    report - are always readable by opening a plain text file next to
#    the .exe, with no console or debugger required.
#
# app.py's own "LOGGING CONFIGURATION" section now just calls
# logging.basicConfig() WITHOUT force=True, which is a harmless no-op
# once handlers already exist (exactly this case) - so it no longer
# wipes out the file handler configured here.
# ---------------------------------------------------------------------
_LOG_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG_FILE = os.path.join(_LOG_DIR, 'app.log')

_log_handlers = [
    # 5 MB per file, keep 3 old ones - bounded disk usage on a machine
    # that may run unattended for months between IT visits.
    RotatingFileHandler(_LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'),
]
# Only attach a console handler if a real console is actually attached
# (sys.stdout is None in a windowed/console=False frozen build) - writing
# to a None stream raises, and there is nothing useful to show anyway.
if sys.stdout is not None:
    _log_handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=_log_handlers,
    force=True,
)

# Loud, unambiguous startup diagnostics. If a customer ever reports "my
# data disappeared", the FIRST thing to check is BASE_DIR/logs/app.log
# for these two lines, on TWO separate launches - if the resolved path
# differs between launches, or sits under a temp folder (see the check
# further below), that IS the bug, and it's an environment/launch issue
# rather than something wrong with this code.
logger.info("BASE_DIR resolved to: %s", BASE_DIR)
logger.info("Frozen (PyInstaller build): %s", getattr(sys, 'frozen', False))
logger.info("Log file: %s", _LOG_FILE)


def _warn_if_base_dir_is_unsafe():
    """
    Detect the single most common real-world cause of "my database resets
    every time I close the app": the customer never actually extracted
    the distributed .zip, and is running the .exe directly from inside
    Windows Explorer's compressed-folder view (or from another
    self-extracting/temp-mounted source).

    Windows Explorer supports opening files inside a .zip without a
    manual "Extract All" step - but to actually RUN an .exe found that
    way, Explorer silently extracts just that one file into a per-session
    temp folder (typically under
    C:\\Users\\<user>\\AppData\\Local\\Temp\\Temp1_<zipname>\\...) and
    launches it from there. sys.executable then genuinely, correctly
    points inside that temp folder - BASE_DIR resolves "correctly" by
    every rule in this file - but the folder itself is deleted by Windows
    once Explorer's temp session ends, taking instance/attendance.db,
    dataset/, uploads/, and .env with it. Every relaunch from the zip
    repeats this: brand-new empty temp folder, brand-new empty database,
    setup wizard again. This is an environment/usage issue, not a code
    bug - but it is worth detecting and surfacing loudly rather than
    letting the customer conclude the software is broken.
    """
    system_temp = os.path.normcase(os.path.abspath(tempfile.gettempdir()))
    resolved = os.path.normcase(os.path.abspath(BASE_DIR))
    looks_temporary = (
        resolved.startswith(system_temp)
        or '\\temp1_' in resolved.lower()
        or os.sep + 'temp' + os.sep in resolved.lower()
    )
    if not looks_temporary:
        return

    message = (
        "STARTUP WARNING: the application's data folder is resolving "
        f"to a TEMPORARY location:\n    {BASE_DIR}\n\n"
        "This almost always means the .exe is being run directly from "
        "inside a .zip file (double-clicked in Explorer's compressed-"
        "folder view) rather than from a fully extracted folder. "
        "Windows deletes this temporary location automatically, which "
        "means the database, employee photos, and .env file will ALL "
        "be lost the moment this window/app is closed - the app will "
        "look 'reset' on every relaunch even though nothing is actually "
        "broken.\n\n"
        "FIX: close this app, right-click the original .zip file, choose "
        "'Extract All...', pick a permanent folder (e.g. Desktop or "
        "Documents), and always run AttendancePayrollSystem.exe from "
        "that extracted folder from now on."
    )
    logger.critical(message)

    # A frozen, windowed (console=False) build has no console for the
    # message above to appear in interactively - only the log file. Show
    # a real, blocking message box too, using tkinter (part of the
    # standard library, so no extra bundling/hiddenimports needed) so a
    # non-technical customer sees this immediately instead of just
    # silently getting an empty setup wizard again.
    if getattr(sys, 'frozen', False):
        try:
            import tkinter
            from tkinter import messagebox
            _root = tkinter.Tk()
            _root.withdraw()
            messagebox.showwarning("Data folder is temporary!", message)
            _root.destroy()
        except Exception:
            # Never let a diagnostics-only convenience feature prevent
            # the app itself from starting.
            logger.exception("Could not display the temp-folder warning dialog.")


_warn_if_base_dir_is_unsafe()


def _get_or_create_secret_key():
    """
    Return SECRET_KEY from the environment, or generate and persist a new
    one to .env on first run.

    Mirrors crypto_utils.py's FACE_DATA_ENCRYPTION_KEY handling exactly,
    for the same reason: a hardcoded fallback value (the old
    'your-secret-key-change-in-production' default) means every install
    that forgets to set SECRET_KEY shares the SAME, publicly-known key -
    which breaks session-cookie and CSRF-token integrity for every one of
    those installs. Auto-generating a random, per-install key and saving
    it to .env removes the "forgot to set it" failure mode entirely,
    without requiring the non-technical customers this app ships to, to
    understand what a SECRET_KEY even is.

    Uses BASE_DIR (already frozen-aware, see above) rather than __file__
    to locate .env, for the same reason crypto_utils.py does - so the
    generated key is written next to the .exe and survives a restart,
    not into a temporary PyInstaller extraction folder.
    """
    key = os.environ.get('SECRET_KEY')
    if key:
        return key

    key = _secrets_module.token_hex(32)
    env_path = os.path.join(BASE_DIR, '.env')
    try:
        with open(env_path, 'a', encoding='utf-8') as f:
            f.write(
                "\n# Auto-generated by config.py on first run - back this up "
                "along with the rest of .env.\n"
                f"SECRET_KEY={key}\n"
            )
        logger.warning(
            "No SECRET_KEY found in .env - generated a new one and saved it "
            "to .env. Back up .env: if this key changes, all existing "
            "logged-in sessions are invalidated and CSRF tokens issued "
            "before the change will be rejected."
        )
    except OSError as e:
        logger.error(
            "Could not persist a generated SECRET_KEY to .env (%s). It will "
            "still work for THIS run, but a new random key will be "
            "generated on every restart, invalidating all sessions each "
            "time, unless you set SECRET_KEY manually as an environment "
            "variable or fix the .env write permission.", e,
        )

    os.environ['SECRET_KEY'] = key
    return key


def _resolve_database_uri():
    """
    Build SQLALCHEMY_DATABASE_URI, defending against the exact bug that
    caused a customer's database to "reset" on every .exe restart:

    .env shipped with `DATABASE_URL=sqlite:///attendance.db` - a RELATIVE
    sqlite URI, left over from an example/default value. Because
    `os.environ.get('DATABASE_URL') or <safe default>` treats any
    non-empty DATABASE_URL as an explicit override, this relative value
    silently WON over the safe, absolute, BASE_DIR-anchored default just
    below. Flask-SQLAlchemy resolves a relative sqlite:/// path against
    Flask's `app.instance_path` - and Flask's own instance_path detection
    has special-case handling for frozen/zipped apps that resolves it
    against sys.prefix, which under PyInstaller points inside the
    EPHEMERAL `_MEI...` extraction folder. Every restart got a brand new
    temp folder, hence a brand new empty database, hence the setup wizard
    running again every time - despite BASE_DIR itself (used everywhere
    else in this file) being completely correct.

    Fix: still respect an explicit DATABASE_URL (e.g. a real MySQL URI
    for a multi-seat deployment), but if it's a sqlite URI, require it to
    be absolute. A relative sqlite DATABASE_URL is almost certainly a
    leftover default/example rather than an intentional choice, so rather
    than silently repeating this exact failure for the next person who
    edits .env, resolve it against BASE_DIR ourselves and log loudly that
    this happened.
    """
    database_url = os.environ.get('DATABASE_URL')
    default_uri = 'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'attendance.db').replace('\\', '/')

    if not database_url:
        return default_uri

    if database_url.startswith('sqlite:///'):
        raw_path = database_url[len('sqlite:///'):]
        # An absolute path looks like `/foo/bar` (POSIX, sqlite:////foo/bar
        # after the scheme) or `C:/foo/bar` / `C:\foo\bar` (Windows drive
        # letter). Anything else is relative and is exactly the footgun
        # described above.
        is_absolute = (
            raw_path.startswith('/')
            or (len(raw_path) > 1 and raw_path[1] == ':')
        )
        if not is_absolute:
            resolved = 'sqlite:///' + os.path.join(BASE_DIR, raw_path).replace('\\', '/')
            logger.warning(
                "DATABASE_URL in .env is a RELATIVE sqlite path (%s), which "
                "resolves unpredictably in a packaged .exe (see config.py "
                "comments). Treating it as relative to BASE_DIR instead: "
                "%s. To silence this warning, either remove DATABASE_URL "
                "from .env entirely (recommended - the safe default is "
                "identical), or set it to this same absolute path yourself.",
                database_url, resolved,
            )
            return resolved

    return database_url


class Config:
    SECRET_KEY = _get_or_create_secret_key()

    # Database Configuration - see _resolve_database_uri() above for why
    # this is not simply `os.environ.get('DATABASE_URL') or <default>`.
    SQLALCHEMY_DATABASE_URI = _resolve_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Session Configuration
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    
    # Upload Configuration - all writable/persistent, so these must use
    # BASE_DIR (the exe's own folder when frozen), never a __file__-
    # derived path, or data gets wiped on every restart of a packaged build.
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    DATASET_FOLDER = os.path.join(BASE_DIR, 'dataset')
    TRAINED_MODEL_FOLDER = os.path.join(BASE_DIR, 'trained_model')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max file size
    
    # Allowed Extensions
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    
    # Email Configuration (SMTP)
    MAIL_SERVER = os.environ.get('MAIL_SERVER') or 'smtp.gmail.com'
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or 587)
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() in ['true', 'on', '1']
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER')
    
    # Company Settings
    COMPANY_NAME = os.environ.get('COMPANY_NAME') or 'AI Attendance System'
    COMPANY_LOGO = os.environ.get('COMPANY_LOGO') or 'static/images/company_logo.png'
    
    # Office Timing
    OFFICE_START_TIME = os.environ.get('OFFICE_START_TIME') or '09:00'
    OFFICE_END_TIME = os.environ.get('OFFICE_END_TIME') or '18:00'
    GRACE_PERIOD_MINUTES = int(os.environ.get('GRACE_PERIOD_MINUTES') or 15)
    
    # Working Hours
    WORKING_HOURS_PER_DAY = float(os.environ.get('WORKING_HOURS_PER_DAY') or 9.0)
    
    # Salary Calculation
    LATE_DEDUCTION_ENABLED = os.environ.get('LATE_DEDUCTION_ENABLED', 'false').lower() in ['true', 'on', '1']
    LATE_DEDUCTION_PER_OCCURRENCE = float(os.environ.get('LATE_DEDUCTION_PER_OCCURRENCE') or 0.0)
    
    # Overtime Calculation
    OVERTIME_ENABLED = os.environ.get('OVERTIME_ENABLED', 'true').lower() in ['true', 'on', '1']
    OVERTIME_RATE = float(os.environ.get('OVERTIME_RATE') or 1.5)
    
    # Face Recognition Settings
    FACE_RECOGNITION_TOLERANCE = float(os.environ.get('FACE_RECOGNITION_TOLERANCE') or 0.6)
    MIN_FACE_IMAGES_REQUIRED = int(os.environ.get('MIN_FACE_IMAGES_REQUIRED') or 20)
    
    # Security
    CSRF_ENABLED = True
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None

    # Rate limiting (Flask-Limiter). 'memory://' is fine for a single-process
    # desktop/local install; use a shared Redis URI for a multi-worker
    # production deployment so limits are enforced across all workers.
    RATELIMIT_STORAGE_URI = os.environ.get('RATELIMIT_STORAGE_URI') or 'memory://'

# Log the resolved database location immediately at import time, in
# addition to app.py's own startup logging - if a customer ever reports
# "my data disappeared", this line (visible in the log file even for a
# console=False windowed build, since logging is configured independently
# of the console) is the fastest way to confirm whether BASE_DIR resolved
# to a stable, persistent folder or something else.
logger.info("SQLite database path (if using default): %s",
            os.path.join(BASE_DIR, 'instance', 'attendance.db'))

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = False  # Set to True if using HTTPS in production

class TestingConfig(Config):
    """
    Used by the pytest suite (see tests/conftest.py, which sets
    FLASK_ENV=testing before importing app.py). Points at a throwaway
    file-based SQLite database rather than the real one, disables CSRF by
    default (individual tests that specifically need to exercise CSRF
    protection can flip WTF_CSRF_ENABLED back on for that test), and keeps
    rate limiting ENABLED (unlike CSRF) since testing rate limits is one of
    the suite's explicit goals.
    """
    TESTING = True
    DEBUG = False
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(BASE_DIR, 'test_attendance.db')
    SESSION_COOKIE_SECURE = False

config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    # Defaulting to the hardened production config means a deployment has
    # to explicitly opt IN to debug mode via FLASK_ENV=development, rather
    # than silently running with the debugger enabled by default.
    'default': ProductionConfig
}

