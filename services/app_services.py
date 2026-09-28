"""
Shared, lazily-initialized singleton services: AttendanceManager,
PayrollCalculator, EmailService, PDFGenerator.

Extracted out of app.py (where it originally lived as a module-level
get_services() function backed by module-level globals) so that
blueprints - payroll_routes.py, and app.py itself - can share the exact
same singleton instances without app.py needing to import the blueprint
and the blueprint needing to import back from app.py (a circular import).
This follows the same pattern already established for auth decorators
(auth_decorators.py), the face recognizer singleton
(face_recognition_singleton.py), and file helpers (file_helpers.py).

Each service is constructed on first use, within whatever request/app
context first calls get_services(), and then reused for the lifetime of
the process - these are inherently single-process, single-worker
services (in-memory scheduler state, SMTP connection details, etc.),
which matches this application's single-process desktop/local deployment
model (see config.py / README.md).
"""
from attendance import AttendanceManager
from payroll import PayrollCalculator
from email_service import EmailService
from pdf_generator import PDFGenerator

_attendance_manager = None
_payroll_calculator = None
_email_service = None
_pdf_generator = None


def get_services():
    """
    Return (attendance_manager, payroll_calculator, email_service,
    pdf_generator), constructing each one the first time it's needed.

    Callers conventionally unpack only what they need, e.g.:
        _, pc, _, _ = get_services()
    """
    global _attendance_manager, _payroll_calculator, _email_service, _pdf_generator
    if _attendance_manager is None:
        _attendance_manager = AttendanceManager()
    if _payroll_calculator is None:
        _payroll_calculator = PayrollCalculator()
    if _email_service is None:
        _email_service = EmailService()
    if _pdf_generator is None:
        _pdf_generator = PDFGenerator()
    return _attendance_manager, _payroll_calculator, _email_service, _pdf_generator
