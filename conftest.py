"""
Shared pytest fixtures for the whole suite.

CRITICAL ORDERING NOTE: everything in the "stub heavy ML/CV modules"
section below MUST run before anything imports `app` (directly or
transitively via ai_engine.py). pytest imports conftest.py before
collecting any test module, so as long as the stubbing happens at module
level here (not inside a fixture function), it's guaranteed to run first.

Why stub at all, given ai_engine.py and app.py already guard these imports
with try/except ImportError?
    Those guards mean a MISSING library degrades gracefully (cv2/mediapipe/
    DeepFace become None) - they don't stop the real, multi-gigabyte
    TensorFlow/DeepFace/OpenCV/MediaPipe stack from being imported if it
    *is* installed, which is slow, and not something a test runner or CI
    box should be required to have installed just to test auth/rate-
    limiting/payroll-math logic that never touches face recognition at
    all. Pre-registering lightweight fake modules in sys.modules forces
    the fast, deterministic path unconditionally, regardless of what's
    actually pip-installed in the environment running these tests.
"""
import os
import sys
import types

# ---------------------------------------------------------------------
# 1) Stub heavy ML/CV modules in sys.modules BEFORE any project import.
# ---------------------------------------------------------------------


def _make_stub_module(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def _noop(*args, **kwargs):
    return None


if 'cv2' not in sys.modules:
    _make_stub_module(
        'cv2',
        imread=_noop,
        imwrite=_noop,
        imdecode=lambda *a, **k: None,
        imencode=lambda *a, **k: (True, b''),
        cvtColor=_noop,
        resize=_noop,
        waitKey=lambda *a, **k: -1,
        VideoCapture=lambda *a, **k: types.SimpleNamespace(read=lambda: (False, None), release=_noop),
        IMREAD_COLOR=1,
        COLOR_BGR2RGB=4,
        COLOR_BGR2GRAY=6,
        CascadeClassifier=lambda *a, **k: types.SimpleNamespace(detectMultiScale=lambda *a, **k: []),
    )

if 'mediapipe' not in sys.modules:
    _mp_solutions = types.SimpleNamespace(
        face_detection=types.SimpleNamespace(
            FaceDetection=lambda *a, **k: types.SimpleNamespace(
                process=lambda *a, **k: types.SimpleNamespace(detections=None)
            )
        ),
    )
    _make_stub_module('mediapipe', solutions=_mp_solutions)

if 'tensorflow' not in sys.modules:
    _make_stub_module('tensorflow', __version__='0.0.0-stub')

if 'deepface' not in sys.modules:
    class _StubDeepFace:
        @staticmethod
        def represent(*args, **kwargs):
            return []

        @staticmethod
        def extract_faces(*args, **kwargs):
            return []

        @staticmethod
        def verify(*args, **kwargs):
            return {'verified': False, 'distance': 1.0}

    _make_stub_module('deepface', DeepFace=_StubDeepFace)

# ---------------------------------------------------------------------
# 2) Force the app into 'testing' config BEFORE importing it.
# ---------------------------------------------------------------------
os.environ['FLASK_ENV'] = 'testing'

TEST_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_attendance.db')
TEST_DB_PATH = os.path.abspath(TEST_DB_PATH)

import pytest


@pytest.fixture(autouse=True)
def _no_real_email_sending(monkeypatch):
    """
    Prevent any test from ever attempting a real SMTP connection.

    EmailService.send_email() only skips sending if MAIL_USERNAME/
    MAIL_PASSWORD are unset - but this project's actual .env (loaded via
    python-dotenv at import time, same as a real deployment) has real
    SMTP credentials configured for the developer's own account. Without
    this fixture, any route that sends an email during a test (e.g.
    add_employee's welcome email) attempts a genuine connection to
    smtp.gmail.com:587, which then hangs for 100+ seconds before timing
    out in a sandboxed/offline test environment - this was measured
    directly (a single small test file took 138 seconds instead of
    under 2). Test speed and reliability must never depend on whatever
    secrets happen to be sitting in a developer's .env file.

    send_email() is the one low-level chokepoint every send_welcome_email/
    send_payslip/send_password_reset/etc. method calls through, so
    patching only this one method neutralizes all of them at once.
    """
    import email_service
    monkeypatch.setattr(
        email_service.EmailService,
        'send_email',
        lambda self, *args, **kwargs: {'success': True, 'message': 'Suppressed during tests'},
    )


@pytest.fixture(scope='session')
def flask_app():
    """
    Import and configure the real app.py exactly once for the whole test
    session (importing it more than once is a no-op after the first time
    anyway, due to Python's module cache - but this also gives us one
    place to do setup/teardown of the throwaway test database file).

    NOTE ON THE BACKGROUND SCHEDULER: importing app.py also starts a real
    APScheduler background thread (see scheduler_service.py /
    payroll_scheduler.init_app(app) in app.py). It's scheduled to run
    monthly, so it will not fire during a short test run, but it is real
    background state - this is a known, accepted side effect of importing
    the app as a whole rather than refactoring scheduler startup to be
    lazy/opt-in (a good candidate for a future cleanup once app.py is
    split into blueprints).
    """
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)

    import app as app_module
    application = app_module.app
    application.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SQLALCHEMY_DATABASE_URI=f'sqlite:///{TEST_DB_PATH}',
    )

    yield application

    # Explicitly dispose of the SQLAlchemy engine's pooled connections
    # before deleting the file. On Windows, SQLite keeps the underlying
    # file handle open for as long as any pooled connection exists, and
    # Windows (unlike Linux/Mac) refuses to delete a file that still has
    # an open handle - without this, os.remove() below raises
    # PermissionError: [WinError 32] on Windows even though the exact
    # same fixture works fine on Linux/Mac.
    from database import db
    with application.app_context():
        db.session.remove()
        db.engine.dispose()

    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except OSError as e:
            # Best-effort cleanup only - a leftover throwaway test DB file
            # (e.g. because antivirus or another process briefly holds a
            # lock on Windows) should never fail the test run itself. It
            # gets removed automatically at the start of the next run.
            print(f"Warning: could not remove {TEST_DB_PATH} ({e}); it will be cleaned up on the next test run.")


@pytest.fixture
def app_context(flask_app):
    """Push an app context and give each test a clean set of tables."""
    from database import db
    with flask_app.app_context():
        db.create_all()
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app_context):
    """A Flask test client, with a clean DB (via app_context) for every test."""
    return app_context.test_client()


# @pytest.fixture(autouse=True)
# def _reset_rate_limits():
#     """
#     Flask-Limiter's in-memory storage is a process-wide singleton (see
#     extensions.py) shared by every test in the session, so without this,
#     tests would either falsely trip a 429 because an earlier, unrelated
#     test already used up that route's quota, or (for the dedicated
#     rate-limiting tests) start from a dirty counter. Resetting before
#     every single test keeps each test's view of its rate limit clean.
#     """
#     from extensions import limiter
#     limiter.reset()
#     yield
#     limiter.reset()


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """
    Flask-Limiter's in-memory storage is a process-wide singleton (see
    extensions.py) shared by every test in the session, so without this,
    tests would either falsely trip a 429 because an earlier, unrelated
    test already used up that route's quota, or (for the dedicated
    rate-limiting tests) start from a dirty counter. Resetting before
    every single test keeps each test's view of its rate limit clean.
    """
    from extensions import limiter
    try:
        if getattr(limiter, "_storage", None) is not None:
            limiter.reset()
    except Exception:
        pass
    
    yield
    
    try:
        if getattr(limiter, "_storage", None) is not None:
            limiter.reset()
    except Exception:
        pass


@pytest.fixture
def make_admin(app_context):
    """Factory fixture: create and commit an Admin row, returning it."""
    from database import db
    from models import Admin

    def _make(username='testadmin', email='testadmin@example.com', password='testpass123',
              force_password_change=False):
        admin = Admin(username=username, email=email)
        admin.set_password(password)
        admin.force_password_change = force_password_change
        db.session.add(admin)
        db.session.commit()
        return admin

    return _make


@pytest.fixture
def make_employee(app_context):
    """Factory fixture: create and commit an Employee row (+ login credentials), returning it."""
    from datetime import date
    from database import db
    from models import Employee

    def _make(employee_id='EMP0001', name='Test Employee', password='testpass123', **overrides):
        defaults = dict(
            employee_id=employee_id,
            name=name,
            username=employee_id,
            department='Engineering',
            designation='Software Engineer',
            basic_salary=50000,
            joining_date=date(2024, 1, 1),
            dob=date(1998, 3, 15),
            email=f'{employee_id.lower()}@example.com',
            phone='9876543210',
            address='123 Test Street',
            hra=10000,
            da=2000,
            medical_allowance=1000,
            travel_allowance=1000,
            special_allowance=0,
            other_allowances=0,
            bus_charges=0,
            other_deduction=0,
        )
        defaults.update(overrides)
        employee = Employee(**defaults)
        employee.set_password(password)
        db.session.add(employee)
        db.session.commit()
        return employee

    return _make
