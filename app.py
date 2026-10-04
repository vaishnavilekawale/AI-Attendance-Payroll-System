import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'  # Suppress TensorFlow warnings

import atexit
import logging

from flask import Flask, render_template

from config import config, Config, BASE_DIR
from database import db, init_db
from ai_engine import preload_employee_embeddings
from face_recognition_singleton import get_face_recognizer
from scheduler_service import payroll_scheduler
from licensing.client_security import init_license_gate, get_access_state, MODE_LOCKED

# ------------------------------------------------------------------
# Deliberate re-exports (NOT used directly in this file any more).
#
# Other modules import these names FROM `app` at call time
# (services/admin_reports_service.py and pdf_generator.py do
# `from app import get_effective_report_status` / `get_services`), and
# 20+ tests patch 'app.get_effective_report_status'. The real
# implementations live in services/attendance_stats.py and
# services/app_services.py; importing them here keeps those existing
# `app.<name>` access paths - and the test patch targets - working.
# ------------------------------------------------------------------
from services.attendance_stats import get_effective_report_status  # noqa: F401
from services.app_services import get_services  # noqa: F401


# ============================================================
# LOGGING CONFIGURATION
#
# Real logging setup (RotatingFileHandler at BASE_DIR/logs/app.log, plus
# a console handler only when a console actually exists) now happens in
# config.py, as early as possible in the import chain - see the comments
# there for why it has to be there and not here: config.py is imported
# well before this point (transitively, via crypto_utils and others),
# and its own startup diagnostics (BASE_DIR, resolved database path)
# need handlers attached before they run, not after.
#
# This call is deliberately WITHOUT force=True: basicConfig() is a no-op
# if the root logger already has handlers attached, which is exactly the
# case here (config.py already set them up by the time this module-level
# code runs). Kept mainly so this file still behaves sanely if it's ever
# imported in a context where config.py's setup didn't run first (e.g. a
# future test harness that stubs config.py out).
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

logger = logging.getLogger(__name__)

# GET / POST request logs
logging.getLogger("werkzeug").setLevel(logging.INFO)

# Face recognition / AVD logs
logging.getLogger("ai_engine").setLevel(logging.INFO)

# Attendance marking logs
logging.getLogger("attendance").setLevel(logging.INFO)

# Scheduler logs
logging.getLogger("apscheduler").setLevel(logging.INFO)
logging.getLogger("apscheduler.scheduler").setLevel(logging.INFO)
logging.getLogger("scheduler_service").setLevel(logging.INFO)

# Attendance calculator:

logging.getLogger("services.attendance_calculator").setLevel(logging.ERROR)


# Explicit, BASE_DIR-anchored instance_path - do not rely on Flask's own
# default instance_path detection here. Flask computes a default
# instance_path from the app's import machinery, and has special-case
# logic for frozen/zipped apps that can resolve it against sys.prefix -
# which under PyInstaller points inside the EPHEMERAL `_MEI...`
# extraction folder, not next to the .exe. This bit a real deployment:
# a relative `DATABASE_URL=sqlite:///attendance.db` in .env (see
# config.py's _resolve_database_uri() for the primary fix) got resolved
# against that temp instance_path, so the database silently reset on
# every restart even though BASE_DIR itself was correct everywhere else.
# Pinning instance_path here explicitly means ANY code path that ever
# resolves a relative path against it - Flask's own, Flask-SQLAlchemy's,
# a future extension's - lands next to the .exe instead of in a temp
# folder, regardless of how that code path computes its default.
_instance_dir = os.path.join(BASE_DIR, 'instance')
os.makedirs(_instance_dir, exist_ok=True)

app = Flask(__name__, instance_path=_instance_dir)

# ------------------------------------------------------------------
# Environment-driven configuration.
#
# Previously this always loaded `config['default']` (DevelopmentConfig,
# DEBUG=True) regardless of how the app was actually deployed, which
# meant ProductionConfig was dead code and the app always ran with the
# Werkzeug debugger enabled. FLASK_ENV now controls which config class
# is loaded, and defaults to 'production' - a deployment has to opt IN
# to debug mode explicitly, rather than opt out of it.
# ------------------------------------------------------------------
env_name = os.environ.get('FLASK_ENV', 'production').lower()
app.config.from_object(config.get(env_name, config['production']))

if app.config['DEBUG']:
    logger.warning(
        "Starting with FLASK_ENV=%s (DEBUG=True). Never run with debug mode "
        "enabled on a deployment reachable by anyone other than the "
        "developer - the interactive debugger allows remote code execution.",
        env_name,
    )

# CSRF protection for every state-changing (POST/PUT/PATCH/DELETE) request.
# WTF_CSRF_ENABLED was already set in Config, but no CSRFProtect instance
# was ever created, so it had no effect. This also exposes `csrf_token()`
# as a Jinja global automatically, for use in templates.
#
# csrf/limiter are shared, uninitialized instances from extensions.py
# (rather than being constructed here directly) specifically so that
# blueprints - like blueprints/setup_wizard.py - can import the exact same
# instances without causing a circular import with app.py.
from extensions import csrf, limiter
csrf.init_app(app)

# Rate limiting - primarily to slow down credential-stuffing / brute-force
# attempts against /login. Uses in-memory storage by default, which is
# fine for a single-process desktop/local deployment; point
# RATELIMIT_STORAGE_URI at Redis for a multi-worker/production deployment.
app.config.setdefault('RATELIMIT_STORAGE_URI', os.environ.get('RATELIMIT_STORAGE_URI', 'memory://'))
limiter.init_app(app)

# License gate + "License Activation" lock screen. Registered BEFORE any
# blueprint so its before_request hook runs ahead of everything else: once
# the 30-day trial has ended without a valid licence, every page except the
# lock screen (/license) is blocked. See licensing/client_security.py.
init_license_gate(app)

dataset_folder = Config.DATASET_FOLDER

if not os.path.exists(dataset_folder):
    os.makedirs(dataset_folder)


init_db(app)

# ------------------------------------------------------------------
# Blueprints. Every route in the application now lives in a blueprint;
# this file is only the application entry point (app creation/config,
# extension init, blueprint registration, template filter, error
# handlers, startup). Each blueprint imports shared collaborators
# (extensions.py, auth_decorators.py, services/*, models.py, ...) from
# neutral modules rather than from app.py, so there are no circular
# imports.
# ------------------------------------------------------------------

# First migrated blueprint - guided first-run setup wizard.
from setup_wizard import setup_bp
app.register_blueprint(setup_bp)

# Second migrated blueprint - employee CRUD (list/add/edit/delete/view).
# See employees.py.
from employees import employees_bp
app.register_blueprint(employees_bp)

# Third migrated blueprint - unified login/logout, forgot/change password
# (admin + employee), legacy /register forwarding, authenticated admin
# creation. See auth_routes.py.
from auth_routes import auth_bp
app.register_blueprint(auth_bp)

# Fourth migrated blueprint - admin payroll (list/calculate/payslip
# generate+email), payroll automation settings, and employee-self-service
# payroll + custom payslip password. See payroll_routes.py.
from payroll_routes import payroll_bp
app.register_blueprint(payroll_bp)

# Fifth migrated blueprint - admin Reports page (filterable list + PDF
# export) and the employee-self-service Reports page + its own PDF
# export. See reports_routes.py.
from reports_routes import reports_bp
app.register_blueprint(reports_bp)

# Sixth migrated blueprint - system/company settings, biometric consent
# recording, and the admin full-data backup download. See settings_routes.py.
from settings_routes import settings_bp
app.register_blueprint(settings_bp)

# Seventh migrated blueprint - manager/admin logout-approval workflow,
# manual-attendance approve/reject, attendance edit reached from the
# approvals dashboards, and the two dev-only manual-trigger endpoints.
# See approvals_routes.py.
from approvals_routes import approvals_bp
app.register_blueprint(approvals_bp)

# Eighth (final) migrated blueprint - kiosk landing page, dashboards,
# face registration/capture/training, attendance pages + APIs (camera /
# OpenCV / face recognition / presence trackers), employee self-service
# attendance + profile, and protected uploads/dataset file serving.
# See attendance_routes.py.
from attendance_routes import attendance_bp
app.register_blueprint(attendance_bp)

with app.app_context():
    logger.info(
        "DATABASE URI: %s",
        app.config["SQLALCHEMY_DATABASE_URI"]
    )

    logger.info(
        "DATABASE FILE: %s",
        db.engine.url
    )


# ============================================================
# PAYROLL SCHEDULER
# ============================================================

payroll_scheduler.init_app(app)
atexit.register(payroll_scheduler.shutdown)

logger.info("Payroll scheduler initialized")

# AUTO LOGOUT RECONCILIATION
# ============================================================
with app.app_context():
    from services.approval_service import approval_service
    approval_service.reconcile_missed_approval_requests()

# PAYROLL RECONCILIATION
# ============================================================
with app.app_context():
    payroll_scheduler.reconcile_missed_payroll()

# Custom Jinja2 filters
@app.template_filter('month_name')
def month_name_filter(month_num):
    """Convert month number to month name"""
    months = {
        1: 'January', 2: 'February', 3: 'March', 4: 'April',
        5: 'May', 6: 'June', 7: 'July', 8: 'August',
        9: 'September', 10: 'October', 11: 'November', 12: 'December'
    }
    return months.get(month_num, 'Unknown')

@app.errorhandler(404)
def not_found(error):
    return render_template('login.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('login.html'), 500


if __name__ == '__main__':
    # ============================================================
    # LICENSE VALIDATION
    # ============================================================
    print("\n" + "=" * 50)
    print("[LICENSE] Checking license status...")
    print("=" * 50)

    # The app no longer exits when the licence is missing/expired: it starts
    # normally and the license gate (init_license_gate above) shows the
    # "License Activation" lock screen in the browser instead, where the user
    # can copy their Machine Fingerprint, buy a licence and paste the key.
    # force=True also starts the trial clock on the very first launch.
    access = get_access_state(force=True)

    if access.mode == MODE_LOCKED:
        print("\n" + "!" * 50)
        print("[LOCKED] Trial ended / no valid license")
        print(f"   {access.lock_reason or access.message}")
        print(f"   Machine fingerprint: {access.machine_fingerprint}")
        print("   The application will open on the License Activation screen.")
        print("!" * 50 + "\n")
        logger.warning(f"Application starting LOCKED: {access.message}")
    else:
        print(f"✅ License check passed: {access.message}")
        print("=" * 50 + "\n")
        logger.info(f"License check passed ({access.mode}): {access.message}")
    
    # ============================================================
    # DIRECTORY SETUP
    # ============================================================
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['DATASET_FOLDER'], exist_ok=True)
    os.makedirs(app.config['TRAINED_MODEL_FOLDER'], exist_ok=True)

    # Load face recognition model and employee face data on startup.
    # preload_employee_embeddings() encodes every employee photo in
    # parallel (ThreadPoolExecutor) and caches the result to disk, so
    # every restart after the first is near-instant.
    print("\n" + "=" * 50)
    print("🔍 Loading face recognition model and employee face data...")
    print("=" * 50)
    recognizer = get_face_recognizer()
    logger.info(f"Face recognition engine initialized with {len(recognizer.known_face_ids)} registered employees")
    print(f"✅ Face recognition loaded: {len(recognizer.known_face_ids)} employees registered")

    embed_stats = preload_employee_embeddings(max_workers=8)
    print(
        f"✅ Face embeddings ready: {embed_stats['total']} image(s) "
        f"| {embed_stats['already_cached']} from disk cache "
        f"| {embed_stats['encoded']} newly encoded "
        f"| {embed_stats['errors']} error(s) "
        f"| {embed_stats['seconds']}s"
    )
    print("=" * 50 + "\n")

    # Use explicit variables so the printed URL always matches the actual
    # server address that Flask binds to.
    SERVER_HOST = '127.0.0.1'
    SERVER_PORT = 5000
    SERVER_URL = f'http://{SERVER_HOST}:{SERVER_PORT}/'

    # Never hardcode debug=True here - it must follow the environment-driven
    # config (see FLASK_ENV / app.config.from_object above). The interactive
    # Werkzeug debugger allows arbitrary remote code execution if reachable,
    # so it should only ever be on when a developer explicitly opts in via
    # FLASK_ENV=development.
    debug_mode = app.config.get('DEBUG', False)

    print("\n" + "=" * 50)
    print(f"🚀 Attendance & Payroll System")
    print(f"   Running at: {SERVER_URL}")
    print(f"   Host: {SERVER_HOST}  |  Port: {SERVER_PORT}")
    print(f"   Mode: {'DEVELOPMENT (debug=True)' if debug_mode else 'PRODUCTION (debug=False)'}")
    print("=" * 50 + "\n")

    # Auto-open the default browser once the server is actually listening -
    # a packaged desktop .exe has no terminal for the customer to read a
    # URL from, so this is what makes "double-click the exe" behave like a
    # normal desktop app rather than requiring them to know to open a
    # browser and type in an address manually. Skipped when Werkzeug's
    # reloader would otherwise cause this to run twice (not relevant here
    # since use_reloader=False, but guarded defensively) and can be
    # disabled entirely via SKIP_BROWSER_AUTOLAUNCH=true (e.g. for
    # automated/CI runs of the packaged build).
    if os.environ.get('SKIP_BROWSER_AUTOLAUNCH', '').lower() != 'true':
        import threading
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(SERVER_URL)).start()

    # ============================================================
    # WSGI SERVER
    # ============================================================
    # Flask's own app.run() is a development server: it is single-threaded
    # by default (so concurrent kiosk check-ins queue up behind each other
    # instead of being served in parallel), is explicitly documented by
    # Flask/Werkzeug as not designed to be particularly efficient, stable,
    # or secure, and is what previously ran here unconditionally. Waitress
    # is a production-grade, pure-Python WSGI server with solid Windows
    # support (no extra native build step, unlike gunicorn) - a good fit
    # for a kiosk deployment that must handle several simultaneous
    # check-ins without dropping or serializing requests.
    #
    # debug_mode (FLASK_ENV=development, opted into explicitly - see
    # app.config.from_object above) still uses Flask's dev server, since
    # that's what provides the interactive debugger/auto-reload a
    # developer actually wants; every other case - including every
    # packaged customer build - now runs on waitress.
    if debug_mode:
        logger.warning(
            "FLASK_ENV=development: using Flask's development server, not waitress. "
            "Never deploy a customer build this way."
        )
        app.run(host=SERVER_HOST, port=SERVER_PORT, debug=True, use_reloader=False)
    else:
        from waitress import serve
        logger.info(f"Starting production WSGI server (waitress) on {SERVER_HOST}:{SERVER_PORT}")
        serve(app, host=SERVER_HOST, port=SERVER_PORT, threads=8)