import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'  # Suppress TensorFlow warnings

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file, send_from_directory, flash, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
import cv2
import numpy as np
from datetime import datetime, date, timedelta, time
import json

from config import config, Config
from database import db, init_db
from models import Admin, Employee, Attendance, Payroll, Settings, EmployeeLogin, AttendanceActivity, PayrollSettings, CompanySettings, LogoutApprovalRequest
from ai_engine import FaceRecognitionEngine, FaceDetectionEngine, FaceCapture, train_all_employees, get_recognition_tolerance, presence_tracker, preload_employee_embeddings
from attendance import AttendanceManager
from payroll import PayrollCalculator
from email_service import EmailService
from pdf_generator import PDFGenerator, generate_payslip_password
from scheduler_service import payroll_scheduler
from services.attendance_stats import has_rejected_approval, normalize_attendance_status
import logging
import atexit
import threading
import time as time_module

# ============================================================
# LOGGING CONFIGURATION
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    force=True
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
# INFO + WARNING बंद, पण ERROR दिसतील
logging.getLogger("services.attendance_calculator").setLevel(logging.ERROR)

# ============================================================
# REPORT STATUS HELPER FUNCTIONS
# ============================================================

def get_effective_report_status(attendance):
    """
    Return the canonical attendance status used by reports and PDFs.
    Finalized attendance uses the status already calculated and stored
    on the Attendance record.

    Rules:
    - REJECTED logout approval requests are ALWAYS treated as ABSENT.
    - Today's attendance is not finalized while the work day is ongoing,
      so it stays 'pending' (NOT_MARKED) until the day ends at midnight.
    - Past dates without an IN time are ABSENT.
    """
    from datetime import date

    # REJECTED logout approvals are treated strictly as ABSENT
    if has_rejected_approval(attendance):
        return 'absent'

    # Today's attendance without OUT is still pending / NOT_MARKED
    if not attendance.out_time and attendance.date >= date.today():
        return 'pending'

    # No IN = Absent (applies to finalized past dates only)
    if not attendance.in_time:
        return 'absent'

    # For finalized/past attendance, use the stored final status
    status = (attendance.status or '').lower().strip()

    if status in ('present', 'half_day', 'absent'):
        return status

    # Safety fallback
    return 'pending'

# def get_effective_report_status(attendance):
#     """
#     Calculate effective report status using existing attendance business rules.
    
#     This function uses the existing AttendanceCalculator to determine the correct
#     status for reporting, ensuring consistency with the application's existing
#     Half Day/Present/Absent calculation logic.
    
#     Args:
#         attendance: Attendance object
        
#     Returns:
#         str: Effective status ('present', 'half_day', 'absent', 'pending')
#     """
#     from services.attendance_calculator import AttendanceCalculator
    
#     # If no IN time, status is absent
#     if not attendance.in_time:
#         return 'absent'
    
#     # If no OUT time and not past date, status is pending
#     from datetime import date
#     if not attendance.out_time and attendance.date >= date.today():
#         return 'pending'
    
#     # For past records with IN time, use existing calculator to determine status
#     calculator = AttendanceCalculator()
    
#     # Recalculate working hours if needed
#     if not attendance.total_hours:
#         working_hours = calculator.calculate_working_hours(attendance)
#     else:
#         working_hours = attendance.total_hours
    
#     # Use existing calculate_status with is_final_calculation=True
#     # This applies the same Half Day/Present/Absent rules as the rest of the system
#     effective_status = calculator.calculate_status(
#         attendance, 
#         working_hours=working_hours, 
#         is_final_calculation=True
#     )
    
#     return effective_status


def is_payroll_eligible(employee, month, year):
    """
    Check if an employee is eligible for payroll for a given month/year.
    
    Payroll eligibility is based on the employee's joining date.
    An employee is eligible for payroll only from the month they joined.
    
    Args:
        employee: Employee object
        month: Month (1-12)
        year: Year
        
    Returns:
        bool: True if eligible, False otherwise
    """
    if not employee.joining_date:
        # If no joining date, assume eligible for backward compatibility
        return True
    
    return (
        year > employee.joining_date.year
        or (
            year == employee.joining_date.year
            and month >= employee.joining_date.month
        )
    )


# ============================================================
# APPROVALS DATE-FILTER HELPER FUNCTIONS
# ============================================================
# Manager/Admin Approvals pages default to showing only TODAY's
# requests, with an optional ?date=YYYY-MM-DD query param to view a
# different day. Request/attendance timestamps are stored as naive UTC
# (datetime.utcnow()) but displayed to users in IST elsewhere on these
# pages (see the `created_at_ist` conversion in manager_approvals /
# admin_approvals below) - these helpers keep the "which day is this
# record on" check consistent with that same IST conversion.

IST_OFFSET = timedelta(hours=5, minutes=30)


def parse_approvals_filter_date():
    """
    Read the `date` query parameter (format 'YYYY-MM-DD') used by the
    Manager/Admin Approvals pages. Defaults to today when the parameter is
    missing, blank, or not a valid date, so the pages always load safely.
    """
    raw_value = request.args.get('date', '').strip()
    if not raw_value:
        return date.today()
    try:
        return datetime.strptime(raw_value, '%Y-%m-%d').date()
    except ValueError:
        return date.today()


def matches_ist_date(utc_dt, selected_date):
    """
    True if a naive-UTC datetime (e.g. LogoutApprovalRequest.created_at or
    Attendance.submission_timestamp) falls on `selected_date` once shifted
    to IST.
    """
    if not utc_dt:
        return False
    return (utc_dt + IST_OFFSET).date() == selected_date


app = Flask(__name__)
app.config.from_object(config['default'])

dataset_folder = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'dataset'
)

if not os.path.exists(dataset_folder):
    os.makedirs(dataset_folder)


init_db(app)

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

# Initialize services (will be initialized within app context)
attendance_manager = None
payroll_calculator = None
email_service = None
pdf_generator = None

# Global face recognition engine - created once at startup to avoid repeated model loading
# This prevents "Loaded face recognition data with X employees" log appearing multiple times
face_recognizer = None

# ============================================================
# EMPLOYEE LOGIN ATTENDANCE - FRAME-PRESENCE LOCK
# ============================================================
# This tracker is SEPARATE from the Admin Attendance auto-scan tracker
# (ai_engine.presence_tracker) and is used ONLY by the Employee Login
# attendance stream (/api/employee-attendance). It intentionally does not
# touch ai_engine.py or any admin-attendance code path.
#
# Purpose: once a logged-in employee's attendance is successfully marked,
# lock/freeze further attendance attempts for that employee while their
# face remains detected in front of the camera - even if that lasts for
# hours. A new attempt is only permitted after their face goes undetected
# for EMPLOYEE_PRESENCE_TIMEOUT_SECONDS (frame loss / they step away) and
# is then detected again.
class EmployeeAttendancePresenceTracker:
    # 5-10s of "frame loss" before we consider the employee to have left.
    PRESENCE_TIMEOUT_SECONDS = 8

    def __init__(self):
        self._lock = threading.Lock()
        self._last_seen = {}   # employee_id (str) -> monotonic timestamp of last successful face match
        self._logged = set()   # employee_id (str) currently locked (already marked for this presence)

    def note_face_seen(self, employee_id):
        """
        Call every time this employee's OWN face is successfully verified
        in a captured frame - whether or not attendance ends up being
        written - so the tracker knows they are still physically present.
        """
        employee_id = str(employee_id)
        with self._lock:
            self._last_seen[employee_id] = time_module.monotonic()

    def should_attempt_mark(self, employee_id):
        """
        Returns True if this employee is allowed to attempt an attendance
        mark right now: either this is their first detection, or they were
        previously marked but have since been undetected for longer than
        PRESENCE_TIMEOUT_SECONDS (i.e. they left the frame and came back).

        Returns False if they are still within an already-logged,
        continuous presence - the caller MUST NOT write attendance again
        in that case.
        """
        employee_id = str(employee_id)
        with self._lock:
            now = time_module.monotonic()
            last_seen = self._last_seen.get(employee_id)
            has_left_and_returned = (
                last_seen is not None
                and (now - last_seen) > self.PRESENCE_TIMEOUT_SECONDS
            )
            return employee_id not in self._logged or has_left_and_returned

    def lock(self, employee_id):
        """Call immediately after successfully marking attendance, to
        freeze further attempts for this continuous presence."""
        employee_id = str(employee_id)
        with self._lock:
            self._logged.add(employee_id)
            self._last_seen[employee_id] = time_module.monotonic()

    def sweep(self):
        """Housekeeping: drop employees not seen for a while so memory
        doesn't grow unbounded; their next detection is naturally treated
        as a fresh presence via should_attempt_mark()."""
        now = time_module.monotonic()
        with self._lock:
            stale = [
                emp for emp, last in self._last_seen.items()
                if (now - last) > self.PRESENCE_TIMEOUT_SECONDS
            ]
            for emp in stale:
                self._last_seen.pop(emp, None)
                self._logged.discard(emp)


# Single shared instance for the Employee Login attendance stream only.
employee_attendance_presence_tracker = EmployeeAttendancePresenceTracker()

def get_services():
    global attendance_manager, payroll_calculator, email_service, pdf_generator
    if attendance_manager is None:
        attendance_manager = AttendanceManager()
    if payroll_calculator is None:
        payroll_calculator = PayrollCalculator()
    if email_service is None:
        email_service = EmailService()
    if pdf_generator is None:
        pdf_generator = PDFGenerator()
    return attendance_manager, payroll_calculator, email_service, pdf_generator

def get_face_recognizer():
    """Get or create the global face recognition engine instance"""
    global face_recognizer
    if face_recognizer is None:
        face_recognizer = FaceRecognitionEngine()
    return face_recognizer

# Allowed file extensions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# ==================== AUTH DECORATORS ====================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session and 'employee_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            flash('Access Denied. Admin access required.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def employee_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'employee_id' not in session:
            flash('Access Denied. Employee access required.', 'danger')
            return redirect(url_for('employee_login'))
        # Ensure employee can only access their own data
        # Check if any kwargs contain employee_id that doesn't match session
        if 'employee_id' in kwargs and kwargs['employee_id'] != session['employee_id']:
            flash('Access Denied. You can only view your own information.', 'danger')
            return redirect(url_for('employee_dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# ==================== AUTH ROUTES ====================

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    """Serve files from uploads folder for profile photos and payslips"""
    uploads_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
    file_path = os.path.join(uploads_folder, filename)
    
    # If file doesn't exist at the expected path, try to find it in the uploads root
    # This handles the case where payslips are stored directly in uploads/ instead of payrolls/ subdirectory
    if not os.path.exists(file_path):
        # Extract just the filename from the path
        filename_only = os.path.basename(filename)
        alternative_path = os.path.join(uploads_folder, filename_only)
        
        if os.path.exists(alternative_path):
            file_path = alternative_path
        else:
            # Try alternative naming convention for payslips (e.g., payslip_EMP0001_8_2026.pdf vs payslip_EMP0001_2026_8.pdf)
            if 'payslip' in filename_only:
                # Extract employee_id, month, year from filename
                parts = filename_only.replace('payslip_', '').replace('.pdf', '').split('_')
                if len(parts) == 3:
                    employee_id, month, year = parts
                    # Try swapping month and year
                    alt_filename = f"payslip_{employee_id}_{year}_{month}.pdf"
                    alt_path = os.path.join(uploads_folder, alt_filename)
                    if os.path.exists(alt_path):
                        file_path = alt_path
    
    return send_file(file_path)


@app.route('/dataset/<path:filename>')
def serve_dataset(filename):
    """Serve files from dataset folder for face image thumbnails"""
    dataset_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dataset')
    return send_file(os.path.join(dataset_folder, filename))

@app.route('/')
def index():
    """
    Public kiosk landing page.

    The core Face Recognition / Mark Attendance interface is now the
    application's default entry point, bypassing the login screen
    entirely - anyone can walk up and mark attendance immediately. Admin
    and Employee login remain one click away via the unobtrusive login
    link/sidebar rendered on this same page (see home_attendance.html),
    rather than gating the root route behind a session check.
    """
    return render_template('home_attendance.html')

@app.route("/test-log")
def test_log():
    app.logger.info("APP LOGGER WORKING")
    return "OK"

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Unified login for both Admin and Employee"""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        role = request.form.get('role', 'admin')  # Default to admin if not specified
        
        if not username or not password:
            flash('Please provide both username and password', 'danger')
            return render_template('login.html')
        
        if role == 'admin':
            # Admin authentication
            admin = Admin.query.filter_by(username=username).first()
            
            if not admin:
                flash('Invalid Username', 'danger')
                return render_template('login.html')
            
            if admin.check_password(password):
                session['admin_id'] = admin.id
                session['admin_username'] = admin.username
                session['user_role'] = 'admin'
                admin.last_login = datetime.utcnow()
                db.session.commit()
                
                return redirect(url_for('dashboard'))
            elif admin.check_temporary_password(password):
                # Check if temporary password is still valid (not expired)
                if not admin.is_temporary_password_valid():
                    flash('Temporary password has expired. Please request a new one.', 'danger')
                    return render_template('login.html')
                
                session['admin_id'] = admin.id
                session['admin_username'] = admin.username
                session['user_role'] = 'admin'
                admin.last_login = datetime.utcnow()
                db.session.commit()
                
                # Redirect to change password
                flash('You must change your temporary password before continuing.', 'info')
                return redirect(url_for('change_password'))
            else:
                flash('Invalid Password', 'danger')
                return render_template('login.html')
        
        elif role == 'employee':
            # Employee authentication using EmployeeLogin table
            employee = Employee.query.filter_by(employee_id=username).first()
            
            if not employee:
                flash('Invalid Employee ID', 'danger')
                return render_template('login.html')
            
            login_creds = EmployeeLogin.query.filter_by(employee_id=employee.id).first()
            
            if not login_creds:
                # Create login credentials with default password (mobile number)
                login_creds = EmployeeLogin(
                    employee_id=employee.id,
                    username=username,
                    first_login=True,
                    force_password_change=True,
                    is_active=True
                )
                # Use employee's phone number as default password
                default_password = employee.phone if employee.phone else username
                login_creds.set_password(default_password)
                db.session.add(login_creds)
                db.session.commit()
            
            # Check if account is active
            if not login_creds.is_active:
                flash('Your account is inactive. Please contact administrator.', 'danger')
                return render_template('login.html')
            
            # Verify password
            main_password_check = login_creds.check_password(password)
            
            if main_password_check:
                session['employee_id'] = employee.id
                session['employee_username'] = employee.employee_id
                session['user_role'] = 'employee'
                login_creds.last_login = datetime.utcnow()
                db.session.commit()
                
                # Check if first login or force password change - redirect to change password
                if login_creds.first_login or login_creds.force_password_change:
                    flash('Please change your default password before continuing.', 'info')
                    return redirect(url_for('employee_change_password'))
                
                return redirect(url_for('employee_dashboard'))
            elif login_creds.check_temporary_password(password):
                # Check if temporary password is still valid (not expired)
                if not login_creds.is_temporary_password_valid():
                    flash('Temporary password has expired. Please request a new one.', 'danger')
                    return render_template('login.html')
                
                session['employee_id'] = employee.id
                session['employee_username'] = employee.employee_id
                session['user_role'] = 'employee'
                login_creds.last_login = datetime.utcnow()
                db.session.commit()
                
                # Redirect to change password
                flash('You must change your temporary password before continuing.', 'info')
                return redirect(url_for('employee_change_password'))
            else:
                flash('Invalid Password', 'danger')
                return render_template('login.html')
        
        else:
            flash('Invalid role selected', 'danger')
            return render_template('login.html')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Unified logout for both Admin and Employee"""
    session.clear()
    # Land back on the public kiosk attendance page rather than the login
    # form, consistent with the attendance page being the app's default
    # entry point.
    return redirect(url_for('index'))

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Handle forgot password - send temporary password via email for Admin"""
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        
        if not email:
            flash('Please provide your registered email address', 'danger')
            return render_template('forgot_password.html')
        
        admin = Admin.query.filter_by(email=email).first()
        
        if not admin:
            flash('No account found with this email address', 'danger')
            return render_template('forgot_password.html')
        
        # Generate secure temporary password (8-12 characters with uppercase, lowercase, numbers, and special characters)
        import secrets
        import string
        
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        temp_password = ''.join(secrets.choice(alphabet) for _ in range(10))
        
        # Send email with temporary password FIRST (before updating database)
        try:
            from email_service import EmailService
            es = EmailService()
            result = es.send_admin_temp_password(admin.email, admin.username, temp_password)
            
            if not result['success']:
                flash('Unable to send temporary password. Your existing password has not been changed.', 'danger')
                return render_template('forgot_password.html')
        except Exception as e:
            flash('Unable to send temporary password. Your existing password has not been changed.', 'danger')
            return render_template('forgot_password.html')
        
        # Only update database AFTER email is successfully sent
        # Store temporary password in separate fields, do NOT overwrite main password
        try:
            admin.set_temporary_password(temp_password)
            admin.force_password_change = True
            db.session.commit()
            flash('A temporary password has been sent to your registered email. Please check your inbox and change your password after logging in.', 'success')
        except Exception as e:
            flash('Failed to save temporary password. Please contact administrator.', 'danger')
            return render_template('forgot_password.html')
    
    return render_template('forgot_password.html')

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    """Change password for logged-in admin or employee"""
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        # Check if admin or employee
        if 'admin_id' in session:
            # Admin password change
            admin = Admin.query.get(session['admin_id'])
            
            if not admin:
                flash('Admin not found', 'danger')
                return redirect(url_for('login'))
            
            # Validate current password (accept both main and temporary password)
            if not admin.check_password(current_password) and not admin.check_temporary_password(current_password):
                flash('Current password is incorrect', 'danger')
                return render_template('change_password.html')
            
            # Password strength validation
            if len(new_password) < 6:
                flash('New password must be at least 6 characters', 'danger')
                return render_template('change_password.html')
            
            # Confirm password validation
            if new_password != confirm_password:
                flash('New password and confirm password do not match', 'danger')
                return render_template('change_password.html')
            
            # Hash new password and update
            admin.set_password(new_password)
            admin.clear_temporary_password()
            admin.force_password_change = False
            db.session.commit()
            
            flash('Password changed successfully.', 'success')
            return redirect(url_for('dashboard'))
        
        elif 'employee_id' in session:
            # Employee password change using EmployeeLogin table
            employee = Employee.query.get(session['employee_id'])
            login_creds = EmployeeLogin.query.filter_by(employee_id=employee.id).first()
            
            if not employee or not login_creds:
                flash('Employee not found', 'danger')
                return redirect(url_for('login'))
            
            if not login_creds.check_password(current_password) and not login_creds.check_temporary_password(current_password):
                flash('Current password is incorrect.', 'danger')
            elif len(new_password) < 6:
                flash('New password must be at least 6 characters long.', 'danger')
            elif new_password != confirm_password:
                flash('New password and confirm password do not match.', 'danger')
            else:
                login_creds.set_password(new_password)
                login_creds.clear_temporary_password()
                login_creds.first_login = False
                login_creds.force_password_change = False
                db.session.commit()
                flash('Password changed successfully.', 'success')
                return redirect(url_for('employee_dashboard'))
    
    return render_template('change_password.html')

# ==================== EMPLOYEE AUTHENTICATION ROUTES ====================

@app.route('/employee-forgot-password', methods=['GET', 'POST'])
def employee_forgot_password():
    """Employee forgot password - send temporary password via email"""
    if request.method == 'POST':
        employee_id = request.form.get('employee_id', '').strip()
        email = request.form.get('email', '').strip()
        
        if not employee_id or not email:
            flash('Please provide both Employee ID and registered email address.', 'danger')
            return render_template('employee_forgot_password.html')
        
        # Find employee by employee_id
        employee = Employee.query.filter_by(employee_id=employee_id).first()
        
        if not employee:
            flash('Invalid Employee ID or email address. Please check your credentials.', 'danger')
            return render_template('employee_forgot_password.html')
        
        # Case-insensitive email comparison
        if employee.email.lower() != email.lower():
            flash('Invalid Employee ID or email address. Please check your credentials.', 'danger')
            return render_template('employee_forgot_password.html')
        
        # Check if employee has login credentials
        login_creds = EmployeeLogin.query.filter_by(employee_id=employee.id).first()
        
        if not login_creds:
            flash('Login credentials not found. Please contact administrator.', 'danger')
            return render_template('employee_forgot_password.html')
        
        # Generate secure temporary password (8-12 characters with uppercase, lowercase, numbers, and special characters)
        import secrets
        import string
        
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        temp_password = ''.join(secrets.choice(alphabet) for _ in range(10))
        
        # Send email with temporary password FIRST (before updating database)
        try:
            es = EmailService()
            result = es.send_employee_temp_password(employee.email, employee.name, employee.employee_id, temp_password)
            
            if not result['success']:
                flash('Unable to send temporary password. Your existing password has not been changed.', 'danger')
                return render_template('employee_forgot_password.html')
        except Exception as e:
            flash('Unable to send temporary password. Your existing password has not been changed.', 'danger')
            return render_template('employee_forgot_password.html')
        
        # Only update database AFTER email is successfully sent
        # Store temporary password in separate fields, do NOT overwrite main password
        try:
            login_creds.set_temporary_password(temp_password)
            login_creds.force_password_change = True
            db.session.commit()
            flash('A temporary password has been sent to your registered email. Please check your inbox and change your password after logging in.', 'success')
        except Exception as e:
            flash('Failed to save temporary password. Please contact administrator.', 'danger')
            return render_template('employee_forgot_password.html')
    
    return render_template('employee_forgot_password.html')

@app.route('/employee-change-password', methods=['GET', 'POST'])
@login_required
def employee_change_password():
    """Change password for logged-in employee"""
    if 'employee_id' not in session:
        return redirect(url_for('employee_login'))
    
    employee_id = session['employee_id']
    login_creds = EmployeeLogin.query.filter_by(employee_id=employee_id).first()
    
    if not login_creds:
        flash('Login credentials not found', 'danger')
        return redirect(url_for('employee_login'))
    
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        # For first login, current password might not be required
        if login_creds.first_login:
            if len(new_password) < 6:
                flash('New password must be at least 6 characters long', 'danger')
            elif new_password != confirm_password:
                flash('Passwords do not match', 'danger')
            else:
                login_creds.set_password(new_password)
                login_creds.first_login = False
                login_creds.force_password_change = False
                login_creds.clear_temporary_password()
                db.session.commit()
                flash('Password changed successfully', 'success')
                return redirect(url_for('employee_dashboard'))
        else:
            # Normal password change - require current password
            if not login_creds.check_password(current_password) and not login_creds.check_temporary_password(current_password):
                flash('Current password is incorrect', 'danger')
            elif len(new_password) < 6:
                flash('New password must be at least 6 characters long', 'danger')
            elif new_password != confirm_password:
                flash('Passwords do not match', 'danger')
            else:
                login_creds.set_password(new_password)
                login_creds.force_password_change = False
                login_creds.clear_temporary_password()
                db.session.commit()
                flash('Password changed successfully', 'success')
                return redirect(url_for('employee_dashboard'))
    
    return render_template('change_password.html', first_login=login_creds.first_login)

@app.route('/register', methods=['GET', 'POST'])
def register():
    """Register a new admin user"""
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        # Validation
        if not username or not email or not password:
            flash('All fields are required', 'danger')
        elif len(username) < 3:
            flash('Username must be at least 3 characters', 'danger')
        elif len(password) < 6:
            flash('Password must be at least 6 characters', 'danger')
        elif password != confirm_password:
            flash('Passwords do not match', 'danger')
        else:
            # Check for duplicate username or email
            if Admin.query.filter_by(username=username).first():
                flash('Username already exists', 'danger')
            elif Admin.query.filter_by(email=email).first():
                flash('Email already registered', 'danger')
            else:
                # Create new admin
                admin = Admin(
                    username=username,
                    email=email
                )
                admin.set_password(password)
                db.session.add(admin)
                db.session.commit()
                
                flash('Registration successful! Please login.', 'success')
                return redirect(url_for('login'))
    
    return render_template('register.html')

# ==================== DASHBOARD ROUTES ====================

@app.route('/dashboard')
@login_required
def dashboard():
    today = date.today()
    
    # Initialize services
    am, _, _, _ = get_services()
    
    # Use centralized attendance calculation for today
    all_attendance_data = am.calculate_attendance_with_absent(today)
    
    # Calculate stats from centralized data
    # Present includes only status='present' (late employees are still present)
    # Half Day is counted separately
    present = len([a for a in all_attendance_data if a.status == 'present'])
    absent = len([a for a in all_attendance_data if a.status == 'absent'])
    half_day = len([a for a in all_attendance_data if a.status == 'half_day'])
    late = len([a for a in all_attendance_data if a.late_entry])
    total_employees = len(all_attendance_data)
    
    # Payroll status
    current_month = datetime.now().month
    current_year = datetime.now().year
    payroll_generated = Payroll.query.filter_by(month=current_month, year=current_year).count()
    
    # Recent attendance - use centralized data, sort by created_at if available
    recent_attendance = []
    for att in all_attendance_data:
        # Add created_at for dummy records for sorting
        if not hasattr(att, 'created_at') or att.created_at is None:
            att.created_at = datetime.now() if hasattr(att, 'is_dummy') and att.is_dummy else datetime.min
        recent_attendance.append(att)
    
    # Sort by created_at (most recent first) and limit to 10
    recent_attendance.sort(key=lambda x: x.created_at if hasattr(x, 'created_at') and x.created_at else datetime.min, reverse=True)
    recent_attendance = recent_attendance[:10]
    
    # Get today's activities for each employee
    today_activities = {}
    for att in all_attendance_data:
        if att.employee and not hasattr(att, 'is_dummy'):
            activities = AttendanceActivity.query.filter_by(
                employee_id=att.employee.id,
                attendance_date=today
            ).order_by(AttendanceActivity.activity_time).all()
            today_activities[att.employee.id] = activities
    
    # Department stats
    dept_stats = am.get_department_stats(today, today)
    
    return render_template('dashboard.html',
                         present=present,
                         absent=absent,
                         half_day=half_day,
                         late=late,
                         total_employees=total_employees,
                         payroll_generated=payroll_generated,
                         recent_attendance=recent_attendance,
                         dept_stats=dept_stats,
                         today_activities=today_activities)

@app.route('/employee-dashboard')
@login_required
@employee_required
def employee_dashboard():
    """Employee dashboard showing only their own information"""
    employee_id = session['employee_id']
    employee = Employee.query.get(employee_id)
    today = date.today()
    
    # Initialize services
    am, _, _, _ = get_services()
    
    # Get today's attendance for this employee (exclude pending manual attendance)
    today_attendance = Attendance.query.filter_by(employee_id=employee_id, date=today).filter(
        db.or_(
            Attendance.attendance_type != 'MANUAL_PASSWORD',
            Attendance.approval_status == 'approved'
        )
    ).first()

    # Add display_out_time for UI
    if today_attendance:
        am._add_display_out_time(today_attendance, today)
    
    # Pass current_user to template for manager check
    current_user = employee
        
    # Add display_out_time for UI (show "-" after new IN until next OUT)
    # if today_attendance:
    #     logger.info(f"employee_dashboard - Processing today's attendance ID: {today_attendance.id}")
    #     logger.info(f"  IN Time: {today_attendance.in_time}")
    #     logger.info(f"  OUT Time: {today_attendance.out_time}")
    #     logger.info(f"  Total Hours: {today_attendance.total_hours}")
    #     am._add_display_out_time(today_attendance, today)
    #     logger.info(f"  Display OUT Time after _add_display_out_time: {today_attendance.display_out_time if hasattr(today_attendance, 'display_out_time') else 'N/A'}")
    
    # Get attendance summary for this employee (last 30 days)
    # Use centralized attendance calculation to include generated absent records
    thirty_days_ago = today - timedelta(days=30)
    current_date = thirty_days_ago
    all_attendance = []
    
    # Exclude today from cumulative historical stats - today is finalized
    # only after the day ends (evaluated starting midnight / next day).
    while current_date < today:
        # Use calculate_attendance_with_absent to get attendance including generated absent records
        daily_attendance = am.calculate_attendance_with_absent(current_date)
        employee_attendance = [att for att in daily_attendance if att.employee.id == employee_id]
        all_attendance.extend(employee_attendance)
        current_date += timedelta(days=1)
    
    # Count statuses using effective report status for accuracy
    present_days = 0
    absent_days = 0
    half_days = 0
    late_days = 0
    
    for att in all_attendance:
        effective_status = get_effective_report_status(att)
        
        if effective_status == 'present':
            present_days += 1
        elif effective_status == 'absent':
            absent_days += 1
        elif effective_status == 'half_day':
            half_days += 1
        
        if att.late_entry:
            late_days += 1
    
    logger.info("EMPLOYEE DASHBOARD ABSENT COUNT: %s", absent_days)
    
    # Get recent attendance for this employee (last 10 records, exclude pending manual attendance)
    recent_attendance = Attendance.query.filter_by(employee_id=employee_id).filter(
        db.or_(
            Attendance.attendance_type != 'MANUAL_PASSWORD',
            Attendance.approval_status == 'approved'
        )
    ).order_by(
        Attendance.date.desc()
    ).limit(10).all()

    # Apply display-only auto checkout for past records missing OUT time
    for att in recent_attendance:
        # logger.info(f"employee_dashboard - Processing recent record ID: {att.id}, Date: {att.date}")
        # logger.info(f"  IN Time: {att.in_time}")
        # logger.info(f"  OUT Time: {att.out_time}")
        # CRITICAL: Recalculate status for past records using the calculator
        # This ensures past records show correct Present/Half Day/Absent status
        if att.in_time and att.date < today:
            am.calculator.recalculate_attendance(att, is_final_calculation=True)
        # Add display_out_time for UI (show "-" after new IN until next OUT)
        am._add_display_out_time(att, att.date)
        # logger.info(f"  Display OUT Time after _add_display_out_time: {att.display_out_time if hasattr(att, 'display_out_time') else 'N/A'}")
    
    # Get today's activities for this employee
    today_activities = AttendanceActivity.query.filter_by(
        employee_id=employee_id,
        attendance_date=today
    ).order_by(AttendanceActivity.activity_time).all()
    
    return render_template('employee_dashboard.html',
                         employee=employee,
                         current_user=current_user,
                         today=today,
                         today_attendance=today_attendance,
                         present_days=present_days,
                         absent_days=absent_days,
                         half_days=half_days,
                         late_days=late_days,
                         recent_attendance=recent_attendance,
                         today_activities=today_activities)

# ==================== EMPLOYEE ROUTES ====================

@app.route('/employees')
@login_required
@admin_required
def employees():
    search = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    per_page = 10
    
    query = Employee.query.filter_by(status='active')
    
    if search:
        query = query.filter(
            (Employee.name.contains(search)) |
            (Employee.employee_id.contains(search)) |
            (Employee.department.contains(search))
        )
    
    employees = query.order_by(Employee.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    # Get minimum face images required from Settings
    settings = Settings.get_settings()
    min_face_images = settings.min_face_images_required if settings else 20
    
    # Calculate actual face image count for each employee from dataset folder
    dataset_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dataset')
    
    employee_image_counts = {}
    
    for emp in employees.items:
        emp_folder = os.path.join(dataset_folder, str(emp.id))
        if os.path.exists(emp_folder):
            # Count actual images in folder
            image_files = [f for f in os.listdir(emp_folder) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
            employee_image_counts[emp.id] = len(image_files)
        else:
            employee_image_counts[emp.id] = 0
    
    return render_template('add_employee.html', 
                         employees=employees, 
                         search=search,
                         min_face_images=min_face_images,
                         employee_image_counts=employee_image_counts)

@app.route('/employees/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_employee():
    if request.method == 'POST':
        # Generate employee ID
        last_employee = Employee.query.order_by(Employee.id.desc()).first()
        if last_employee:
            new_id = f"EMP{last_employee.id + 1:04d}"
        else:
            new_id = "EMP0001"
        
        # Get form data
        name = request.form.get('name')
        department = request.form.get('department')
        designation = request.form.get('designation')
        basic_salary = float(request.form.get('basic_salary'))
        joining_date = datetime.strptime(request.form.get('joining_date'), '%Y-%m-%d').date()
        email = request.form.get('email')
        phone = request.form.get('phone')
        address = request.form.get('address')
        office_location = request.form.get('office_location')
        bank_name = request.form.get('bank_name')
        bank_account_number = request.form.get('bank_account_number')

        # Statutory details - all optional plain-text identifiers, no
        # numeric parsing needed. Stored as-is (stripped), None if blank
        # so templates/PDF can cleanly fall back to "N/A".
        pan_number = request.form.get('pan_number', '').strip() or None
        uan_number = request.form.get('uan_number', '').strip() or None
        pf_number = request.form.get('pf_number', '').strip() or None

        # Date of birth - optional, but used to build the payslip PDF
        # password (First 4 letters of name + DOB DDMM). If left blank,
        # PDFGenerator.generate_payslip_password() falls back to the
        # employee's joining_date automatically, so this is never
        # required for the system to keep working.
        dob_raw = request.form.get('dob')
        dob = datetime.strptime(dob_raw, '%Y-%m-%d').date() if dob_raw else None

        # Allowances - all optional in the form, always stored as floats
        # defaulting to 0.0 so downstream payroll math never sees a None.
        def _parse_allowance(field_name):
            raw_value = request.form.get(field_name, '').strip()
            if not raw_value:
                return 0.0
            try:
                return float(raw_value)
            except ValueError:
                return 0.0

        hra = _parse_allowance('hra')
        da = _parse_allowance('da')
        medical_allowance = _parse_allowance('medical_allowance')
        travel_allowance = _parse_allowance('travel_allowance')
        special_allowance = _parse_allowance('special_allowance')
        other_allowances = _parse_allowance('other_allowances')

        # Salary Deductions - same safe-parsing pattern as allowances.
        # employee_pf_percentage and esic_percentage are PERCENTAGES (applied to
        # earned gross salary inside payroll.compute_payroll_amounts); tds_percentage
        # is also a PERCENTAGE; bus_charges and other_deduction are flat monthly amounts.
        employee_pf_percentage = _parse_allowance('employee_pf_percentage')
        esic_percentage = _parse_allowance('esic_percentage')
        tds_percentage = _parse_allowance('tds_percentage')
        bus_charges = _parse_allowance('bus_charges')
        other_deduction = _parse_allowance('other_deduction')
        
        # Unique validation
        if Employee.query.filter_by(name=name).first():
            flash('This Employee Name already exists.', 'danger')
            return redirect(url_for('employees'))
        
        if Employee.query.filter_by(phone=phone).first():
            flash('This Mobile Number already exists.', 'danger')
            return redirect(url_for('employees'))
        
        if Employee.query.filter_by(email=email).first():
            flash('This Email ID already exists.', 'danger')
            return redirect(url_for('employees'))
        
        # Handle profile photo
        profile_photo = None
        if 'profile_photo' in request.files:
            file = request.files['profile_photo']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{new_id}_{file.filename}")
                profile_photo_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(profile_photo_path)
                profile_photo = filename
            else:
                pass
        else:
            pass
        
        employee = Employee(
            employee_id=new_id,
            username=new_id,
            password_hash=generate_password_hash(phone),
            name=name,
            department=department,
            designation=designation,
            basic_salary=basic_salary,
            joining_date=joining_date,
            dob=dob,
            email=email,
            phone=phone,
            address=address,
            profile_photo=profile_photo,
            office_location=office_location,
            bank_name=bank_name,
            bank_account_number=bank_account_number,
            pan_number=pan_number,
            uan_number=uan_number,
            pf_number=pf_number,
            hra=hra,
            da=da,
            medical_allowance=medical_allowance,
            travel_allowance=travel_allowance,
            special_allowance=special_allowance,
            other_allowances=other_allowances,
            employee_pf_percentage=employee_pf_percentage,
            esic_percentage=esic_percentage,
            tds_percentage=tds_percentage,
            bus_charges=bus_charges,
            other_deduction=other_deduction,
            status='active',
            role='employee',
            must_change_password=True
        )
        
        db.session.add(employee)
        db.session.flush()  # Flush to get the employee ID
        
        # Create EmployeeLogin record with mobile number as default password
        existing_login = EmployeeLogin.query.filter_by(employee_id=employee.id).first()
        if not existing_login:
            login_creds = EmployeeLogin(
                employee_id=employee.id,
                username=new_id,
                first_login=True,
                force_password_change=True,
                is_active=True
            )
            login_creds.set_password(phone)  # Default password is mobile number
            employee.password_hash = generate_password_hash(phone)  # Default password mobile number set kela
            employee.username = new_id
            employee.role = 'employee'
            db.session.add(login_creds)
        else:
            pass
        
        db.session.commit()
        
        # Send welcome email with credentials
        try:
            email_service = EmailService()
            result = email_service.send_welcome_email(email, name, new_id, phone)
        except Exception as e:
            pass
        
        flash(f'Employee {new_id} added successfully', 'success')
        return redirect(url_for('employees'))
    
    return render_template('add_employee.html')

@app.route('/employees/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_employee(id):
    employee = Employee.query.get_or_404(id)
    employees = Employee.query.filter_by(status='active').order_by(Employee.created_at.desc()).all()
    
    # Get minimum face images required from Settings
    settings = Settings.get_settings()
    min_face_images = settings.min_face_images_required if settings else 20
    
    # Calculate face image counts for all employees
    dataset_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dataset')
    employee_image_counts = {}
    
    for emp in employees:
        emp_folder = os.path.join(dataset_folder, str(emp.id))
        if os.path.exists(emp_folder):
            image_files = [f for f in os.listdir(emp_folder) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
            employee_image_counts[emp.id] = len(image_files)
        else:
            employee_image_counts[emp.id] = 0
    
    if request.method == 'POST':
        name = request.form.get('name')
        department = request.form.get('department')
        designation = request.form.get('designation')
        basic_salary = float(request.form.get('basic_salary'))
        email = request.form.get('email')
        phone = request.form.get('phone')
        address = request.form.get('address')
        office_location = request.form.get('office_location')
        bank_name = request.form.get('bank_name')
        bank_account_number = request.form.get('bank_account_number')

        # Date of birth - same optional field used by add_employee. Only
        # overwrite employee.dob if the form actually sent a value, so a
        # blank field on an edit form never accidentally wipes out a DOB
        # that was already saved.
        dob_raw = request.form.get('dob', '').strip()
        dob = datetime.strptime(dob_raw, '%Y-%m-%d').date() if dob_raw else None

        # Statutory details - same pattern as add_employee: optional,
        # stored as-is, None if blank so the PDF cleanly falls back to N/A.
        pan_number = request.form.get('pan_number', '').strip() or None
        uan_number = request.form.get('uan_number', '').strip() or None
        pf_number = request.form.get('pf_number', '').strip() or None

        # Allowances - same safe-parsing helper used by add_employee: any
        # blank/invalid value defaults to 0.0 rather than raising or
        # silently keeping a stale value.
        def _parse_allowance(field_name):
            raw_value = request.form.get(field_name, '').strip()
            if not raw_value:
                return 0.0
            try:
                return float(raw_value)
            except ValueError:
                return 0.0

        hra = _parse_allowance('hra')
        da = _parse_allowance('da')
        medical_allowance = _parse_allowance('medical_allowance')
        travel_allowance = _parse_allowance('travel_allowance')
        special_allowance = _parse_allowance('special_allowance')
        other_allowances = _parse_allowance('other_allowances')

        # Salary Deductions - same safe-parsing pattern as allowances.
        # employee_pf_percentage and esic_percentage are PERCENTAGES (applied to
        # earned gross salary inside payroll.compute_payroll_amounts); tds_percentage
        # is also a PERCENTAGE; bus_charges and other_deduction are flat monthly amounts.
        employee_pf_percentage = _parse_allowance('employee_pf_percentage')
        esic_percentage = _parse_allowance('esic_percentage')
        tds_percentage = _parse_allowance('tds_percentage')
        bus_charges = _parse_allowance('bus_charges')
        other_deduction = _parse_allowance('other_deduction')
        
        # Unique validation (exclude current employee)
        if Employee.query.filter(Employee.name == name, Employee.id != id).first():
            flash('This Employee Name already exists.', 'danger')
            return redirect(url_for('edit_employee', id=id))
        
        if Employee.query.filter(Employee.phone == phone, Employee.id != id).first():
            flash('This Mobile Number already exists.', 'danger')
            return redirect(url_for('edit_employee', id=id))
        
        if Employee.query.filter(Employee.email == email, Employee.id != id).first():
            flash('This Email ID already exists.', 'danger')
            return redirect(url_for('edit_employee', id=id))
        
        employee.name = name
        employee.department = department
        employee.designation = designation
        employee.basic_salary = basic_salary
        employee.email = email
        employee.phone = phone
        employee.address = address
        employee.office_location = office_location
        employee.bank_name = bank_name
        employee.bank_account_number = bank_account_number
        employee.pan_number = pan_number
        employee.uan_number = uan_number
        employee.pf_number = pf_number

        # Only overwrite dob if a value was actually submitted - keeps a
        # previously-saved DOB intact if the edit form is ever submitted
        # without that field populated.
        if dob is not None:
            employee.dob = dob

        employee.hra = hra
        employee.da = da
        employee.medical_allowance = medical_allowance
        employee.travel_allowance = travel_allowance
        employee.special_allowance = special_allowance
        employee.other_allowances = other_allowances
        employee.employee_pf_percentage = employee_pf_percentage
        employee.esic_percentage = esic_percentage
        employee.tds_percentage = tds_percentage
        employee.bus_charges = bus_charges
        employee.other_deduction = other_deduction
        
        # Handle profile photo
        if 'profile_photo' in request.files:
            file = request.files['profile_photo']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{employee.employee_id}_{file.filename}")
                profile_photo = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(profile_photo)
                employee.profile_photo = filename
        
        db.session.commit()
        flash('Employee updated successfully', 'success')
        return redirect(url_for('employees'))
    
    return render_template('add_employee.html', employee=employee, employees=employees, edit_mode=True, min_face_images=min_face_images, employee_image_counts=employee_image_counts)

@app.route('/employees/delete/<int:id>')
@login_required
@admin_required
def delete_employee(id):
    employee = Employee.query.get_or_404(id)
    
    # Remove from face recognition using global instance
    recognizer = get_face_recognizer()
    recognizer.remove_employee(str(employee.id))

    # ------------------------------------------------------------------
    # Ordered, safe cleanup of every table with a foreign key back to this
    # employee (or to that employee's attendance rows), BEFORE the
    # Employee row itself is deleted. This fixes the SQLite NOT NULL /
    # FK IntegrityError that previously occurred because
    # LogoutApprovalRequest and AttendanceActivity records were never
    # cleaned up:
    #   - LogoutApprovalRequest.attendance_id  -> attendance.id   (NOT NULL)
    #   - LogoutApprovalRequest.employee_id    -> employees.id   (NOT NULL)
    #   - LogoutApprovalRequest.manager_id     -> employees.id   (NOT NULL)
    #   - LogoutApprovalRequest.approved_by    -> employees.id   (nullable)
    #   - AttendanceActivity.employee_id       -> employees.id   (NOT NULL)
    # Order matters: LogoutApprovalRequest references Attendance, so it
    # must be deleted before Attendance rows are deleted.
    # ------------------------------------------------------------------

    # 1. Collect this employee's attendance record IDs up front - needed
    #    to also catch any LogoutApprovalRequest rows that reference this
    #    employee's attendance via attendance_id even in edge cases.
    attendance_ids = [
        att_id for (att_id,) in
        db.session.query(Attendance.id).filter_by(employee_id=id).all()
    ]

    # 2. Delete LogoutApprovalRequest rows referencing this employee in
    #    ANY foreign key column, or referencing one of their attendance
    #    rows. Using a single OR'd query avoids missing edge cases (e.g.
    #    this employee being the manager/approver on someone else's
    #    request would otherwise be missed).
    logout_approval_filters = [
        LogoutApprovalRequest.employee_id == id,
        LogoutApprovalRequest.manager_id == id,
        LogoutApprovalRequest.approved_by == id,
    ]
    if attendance_ids:
        logout_approval_filters.append(LogoutApprovalRequest.attendance_id.in_(attendance_ids))

    logout_approval_requests = LogoutApprovalRequest.query.filter(
        db.or_(*logout_approval_filters)
    ).all()
    for request_row in logout_approval_requests:
        db.session.delete(request_row)

    # 3. Delete AttendanceActivity rows (IN/OUT punch log) for this employee.
    attendance_activities = AttendanceActivity.query.filter_by(employee_id=id).all()
    for activity in attendance_activities:
        db.session.delete(activity)

    # 4. Delete EmployeeLogin record.
    login_creds = EmployeeLogin.query.filter_by(employee_id=id).first()
    if login_creds:
        db.session.delete(login_creds)
    
    # 5. Delete attendance records (safe now that step 2 removed anything
    #    referencing them).
    attendance_records = Attendance.query.filter_by(employee_id=id).all()
    for record in attendance_records:
        db.session.delete(record)
    
    # 6. Delete payroll records
    payroll_records = Payroll.query.filter_by(employee_id=id).all()
    for record in payroll_records:
        db.session.delete(record)

    # Flush the deletions so the DB session is consistent before we
    # delete the Employee row itself, and so any error surfaces here
    # (before file cleanup) rather than after files are already removed.
    db.session.flush()
    
    # Delete profile photo file
    if employee.profile_photo:
        try:
            profile_photo_path = os.path.join(app.config['UPLOAD_FOLDER'], employee.profile_photo.split('/')[-1])
            if os.path.exists(profile_photo_path):
                os.remove(profile_photo_path)
        except Exception as e:
            pass
    
    # Delete dataset images
    dataset_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dataset', str(id))
    if os.path.exists(dataset_folder):
        try:
            import shutil
            shutil.rmtree(dataset_folder)
        except Exception as e:
            pass
    
    # Delete employee
    db.session.delete(employee)
    db.session.commit()
    
    flash('Employee deleted successfully', 'success')
    return redirect(url_for('employees'))

@app.route('/employees/<int:id>')
@login_required
@admin_required
def view_employee(id):
    employee = Employee.query.get_or_404(id)
    attendance = Attendance.query.filter_by(employee_id=id).order_by(Attendance.date.desc()).limit(30).all()
    employees = Employee.query.filter_by(status='active').order_by(Employee.created_at.desc()).all()
    
    # Get minimum face images required from Settings
    settings = Settings.get_settings()
    min_face_images = settings.min_face_images_required if settings else 20
    
    # Calculate face image counts for all employees
    dataset_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dataset')
    employee_image_counts = {}
    
    for emp in employees:
        emp_folder = os.path.join(dataset_folder, str(emp.id))
        if os.path.exists(emp_folder):
            image_files = [f for f in os.listdir(emp_folder) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
            employee_image_counts[emp.id] = len(image_files)
        else:
            employee_image_counts[emp.id] = 0
    
    # Get face images from dataset folder for the specific employee
    emp_folder = os.path.join(dataset_folder, str(employee.id))
    
    face_images = []
    current_count = 0
    if os.path.exists(emp_folder):
        image_files = [f for f in os.listdir(emp_folder) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
        image_files.sort()  # Sort to ensure consistent ordering
        current_count = len(image_files)
        face_images = image_files
    
    return render_template('add_employee.html', 
                         employee=employee, 
                         attendance=attendance, 
                         employees=employees,
                         view_mode=True,
                         face_images=face_images,
                         current_face_images=current_count,
                         min_face_images=min_face_images,
                         employee_image_counts=employee_image_counts)

# ==================== FACE REGISTRATION ROUTES ====================

@app.route('/face-registration/<int:id>')
@login_required
@admin_required
def face_registration(id):
    employee = Employee.query.get_or_404(id)
    employees = Employee.query.filter_by(status='active').order_by(Employee.created_at.desc()).all()
    
    # Get minimum face images required from Settings
    settings = Settings.get_settings()
    min_face_images = settings.min_face_images_required if settings else 20
    
    # Calculate face image counts for all employees
    dataset_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dataset')
    employee_image_counts = {}
    
    for emp in employees:
        emp_folder = os.path.join(dataset_folder, str(emp.id))
        if os.path.exists(emp_folder):
            image_files = [f for f in os.listdir(emp_folder) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
            employee_image_counts[emp.id] = len(image_files)
        else:
            employee_image_counts[emp.id] = 0
    
    # Calculate current face image count from dataset folder for the specific employee
    emp_folder = os.path.join(dataset_folder, str(employee.id))
    
    current_count = 0
    if os.path.exists(emp_folder):
        image_files = [f for f in os.listdir(emp_folder) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
        current_count = len(image_files)
    
    remaining_images = max(0, min_face_images - current_count)
    
    return render_template('add_employee.html', 
                         employee=employee, 
                         employees=employees, 
                         face_registration=True,
                         min_face_images=min_face_images,
                         current_face_images=current_count,
                         remaining_images=remaining_images,
                         employee_image_counts=employee_image_counts)

@app.route('/capture-face/<int:id>', methods=['POST'])
@login_required
@admin_required
def capture_face(id):
    employee = Employee.query.get_or_404(id)
    
    # Get minimum face images required from Settings
    settings = Settings.get_settings()
    min_face_images = settings.min_face_images_required if settings else 20
    
    # Calculate current face image count from dataset folder
    dataset_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dataset')
    emp_folder = os.path.join(dataset_folder, str(employee.id))
    
    current_count = 0
    if os.path.exists(emp_folder):
        image_files = [f for f in os.listdir(emp_folder) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
        current_count = len(image_files)
    
    # Calculate remaining images to capture
    remaining_images = max(0, min_face_images - current_count)
    
    # If already have enough images, don't capture more
    if remaining_images == 0:
        flash(f'Employee already has {current_count} face images. No additional capture needed.', 'info')
        return redirect(url_for('view_employee', id=id))
    
    capture = FaceCapture(str(employee.id), remaining_images)
    
    try:
        cap = capture.start_capture()
        captured = 0
        
        while captured < remaining_images:
            ret, frame = capture.capture_frame()
            if ret:
                captured = capture.captured_count
            
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
        
        capture.stop_capture()
        
        # Update employee with total count (current + newly captured)
        total_count = current_count + captured
        employee.face_images_count = total_count
        db.session.commit()
        
        # Train AI using global instance
        recognizer = get_face_recognizer()
        image_paths = capture.get_captured_images()
        trained_count = recognizer.train_employee(str(employee.id), employee.name, image_paths)
        
        flash(f'Captured {captured} additional images. Total: {total_count}/{min_face_images}. Trained {trained_count} encodings', 'success')
        return redirect(url_for('view_employee', id=id))
    
    except Exception as e:
        capture.stop_capture()
        flash(f'Error capturing faces: {str(e)}', 'danger')
        return redirect(url_for('face_registration', id=id))

@app.route('/train-ai')
@login_required
@admin_required
def train_ai():
    try:
        train_all_employees()
        flash('AI model trained successfully for all employees', 'success')
    except Exception as e:
        flash(f'Error training AI: {str(e)}', 'danger')
    
    return redirect(url_for('dashboard'))

@app.route('/delete-face-image/<int:employee_id>/<string:image_name>', methods=['POST'])
@login_required
@admin_required
def delete_face_image(employee_id, image_name):
    """Delete a single face image for an employee"""
    employee = Employee.query.get_or_404(employee_id)
    
    # Secure the filename
    image_name = secure_filename(image_name)
    
    # Construct the full path to the image
    dataset_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dataset')
    emp_folder = os.path.join(dataset_folder, str(employee_id))
    image_path = os.path.join(emp_folder, image_name)
    
    # Verify the image exists and is within the employee's folder
    if not os.path.exists(image_path) or not os.path.abspath(image_path).startswith(os.path.abspath(emp_folder)):
        return jsonify({'success': False, 'message': 'Image not found or invalid path'}), 404
    
    try:
        # Delete ONLY the specific image file - do NOT use directory-level deletion
        os.remove(image_path)
        
        # Count remaining images after deletion
        remaining_images = []
        if os.path.exists(emp_folder):
            remaining_images = [f for f in os.listdir(emp_folder) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
        
        remaining_count = len(remaining_images)
        
        # Retrain the face recognition model with remaining images
        # Do NOT remove the entire employee - only retrain with remaining images
        if remaining_images:
            recognizer = get_face_recognizer()
            image_paths = [os.path.join(emp_folder, img) for img in remaining_images]
            recognizer.train_employee(str(employee_id), employee.name, image_paths)
        else:
            # If no images remain, remove employee from face recognition
            recognizer = get_face_recognizer()
            recognizer.remove_employee(str(employee_id))
        
        return jsonify({
            'success': True, 
            'message': 'Face image deleted successfully',
            'remaining_count': remaining_count
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== ATTENDANCE ROUTES ====================

@app.route('/attendance')
@login_required
@admin_required
def attendance():
    today = date.today()
    attendances = Attendance.query.filter_by(date=today).filter(
        db.or_(
            Attendance.attendance_type != 'MANUAL_PASSWORD',
            Attendance.approval_status == 'approved'
        )
    ).order_by(Attendance.in_time.desc()).all()
    
    # Add display_out_time for UI (show "-" after new IN until next OUT)
    am, _, _, _ = get_services()
    for att in attendances:
        pass
        # logger.info(f"attendance - Processing admin view record ID: {att.id}, Date: {att.date}")
        # logger.info(f"  IN Time: {att.in_time}")
        # logger.info(f"  OUT Time: {att.out_time}")
        # am._add_display_out_time(att, today)
        # logger.info(f"  Display OUT Time after _add_display_out_time: {att.display_out_time if hasattr(att, 'display_out_time') else 'N/A'}")
    
    return render_template('attendance.html', attendances=attendances, today=today)

@app.route('/attendance/mark', methods=['POST'])
@login_required
def mark_attendance():
    employee_id = int(request.form.get('employee_id'))
    confidence = float(request.form.get('confidence', 0.0)) if request.form.get('confidence') else None
    
    am, _, _, _ = get_services()
    result = am.mark_attendance(employee_id, confidence)
    
    return jsonify(result)

@app.route('/attendance/history')
@login_required
@admin_required
def attendance_history():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    employee_id = request.args.get('employee_id')
    
    am, _, _, _ = get_services()
    
    if start_date and end_date:
        start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        attendances = am.get_attendance_by_date_range(start_date, end_date, employee_id)
    else:
        attendances = Attendance.query.filter(
            db.or_(
                Attendance.attendance_type != 'MANUAL_PASSWORD',
                Attendance.approval_status == 'approved'
            )
        ).order_by(Attendance.date.desc()).limit(100).all()
    
    # Apply display-only auto checkout for past attendance records with missing OUT times
    today = date.today()
    for att in attendances:
        # logger.info(f"attendance_history - Processing record ID: {att.id}, Date: {att.date}")
        # logger.info(f"  IN Time: {att.in_time}")
        # logger.info(f"  OUT Time: {att.out_time}")
        # Add display_out_time for UI (show "-" after new IN until next OUT)
        am._add_display_out_time(att, att.date)
        # logger.info(f"  Display OUT Time after _add_display_out_time: {att.display_out_time if hasattr(att, 'display_out_time') else 'N/A'}")
    
    employees = Employee.query.filter_by(status='active').all()
    
    return render_template('attendance.html', attendances=attendances, employees=employees, today=today)

# ==================== PAYROLL ROUTES ====================

@app.route('/payroll')
@login_required
@admin_required
def payroll():
    month = request.args.get('month', datetime.now().month, type=int)
    year = request.args.get('year', datetime.now().year, type=int)
    
    _, pc, _, _ = get_services()
    payrolls = Payroll.query.filter_by(month=month, year=year).order_by(Payroll.net_salary.desc()).all()
    summary = pc.get_payroll_summary(year, month)
    
    return render_template('payroll.html', payrolls=payrolls, summary=summary, month=month, year=year)

@app.route('/payroll/calculate', methods=['POST'])
@login_required
@admin_required
def calculate_payroll():
    month = int(request.form.get('month'))
    year = int(request.form.get('year'))
    employee_id = request.form.get('employee_id')
    
    try:
        _, pc, _, _ = get_services()
        
        # Check if payroll already exists for the employee and month
        if employee_id and employee_id.strip():
            employee = Employee.query.get(int(employee_id))
            if employee and not is_payroll_eligible(employee, month, year):
                flash(f'Employee {employee.name} is not eligible for payroll for {month}/{year} (joined on {employee.joining_date}).', 'warning')
                return redirect(url_for('payroll', month=month, year=year))

        payroll_records = pc.calculate_monthly_payroll(
            year,
            month,
            int(employee_id) if employee_id and employee_id.strip() else None,
        )
        flash(f'Payroll calculated for {len(payroll_records)} employee(s) for {month}/{year}', 'success')
    except Exception as e:
        flash(f'Error calculating payroll: {str(e)}', 'danger')
    
    return redirect(url_for('payroll', month=month, year=year))

@app.route('/payroll/payslip/<int:id>')
@login_required
def generate_payslip(id):
    payroll = Payroll.query.get_or_404(id)
    employee = Employee.query.get_or_404(payroll.employee_id)

    # Re-fetch from DB so PDF numbers match the latest saved payroll record.
    db.session.refresh(payroll)

    filename = f"payslip_{employee.employee_id}_{payroll.month}_{payroll.year}.pdf"
    
    # Use the same path structure as scheduler_service for consistency
    from models import PayrollSettings
    payroll_settings = PayrollSettings.get_settings()
    base_path = payroll_settings.payslip_storage_path or 'payrolls'
    payslip_dir = os.path.join(app.config['UPLOAD_FOLDER'], base_path, str(payroll.year), f"{payroll.month:02d}")
    
    # Create directory if it doesn't exist
    os.makedirs(payslip_dir, exist_ok=True)
    
    output_path = os.path.join(payslip_dir, filename)

    _, _, _, pg = get_services()
    company_settings = CompanySettings.query.first()

    # Password-protect the payslip PDF. The password is derived
    # deterministically (first 4 letters of name + DOB DDMM, falling back
    # to Employee ID + DOB DDMM) - see pdf_generator.generate_payslip_password().
    # It is never stored in the database; it's recomputed on demand.
    payslip_password = generate_payslip_password(employee)

    if os.path.exists(output_path):
        os.remove(output_path)

    pg.generate_payslip(
        payroll,
        employee,
        company_settings,
        output_path,
        password=payslip_password
    )

    payroll.payslip_generated = True
    
    # Ha line badal:
    # payroll.payslip_path = f"uploads/{filename}"
    
    # Asya padhhati ne purna relative path save kar:
    payroll.payslip_path = f"payrolls/{payroll.year}/{payroll.month:02d}/{filename}"
    
    db.session.commit()
    # payroll.payslip_generated = True
    # # Tip: Path madhe jar nested folders astil tar yevaji full relative path save kela tari chalel, 
    # # pan sathyala ha code run karun bagh.
    # payroll.payslip_path = f"uploads/{filename}"
    # db.session.commit()

    # 2. Browser cache disable karanyasathi he use kara
    response = make_response(send_file(output_path, as_attachment=True, download_name=filename))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.route('/payroll/send-email/<int:id>')
@login_required
@admin_required
def send_payslip_email(id):
    payroll = Payroll.query.get_or_404(id)
    employee = Employee.query.get_or_404(payroll.employee_id)
    
    if not payroll.payslip_path:
        flash('Please generate payslip first', 'warning')
        return redirect(url_for('payroll'))
    
    payslip_path = os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(payroll.payslip_path))

    # Same deterministic password used to protect the PDF at generation
    # time - recomputed here (never persisted) so the email can tell the
    # employee how to open their attachment.
    payslip_password = generate_payslip_password(employee)
    
    _, _, es, _ = get_services()
    result = es.send_payslip(
        employee.email,
        employee.name,
        payslip_path,
        payroll.month,
        payroll.year,
        pdf_password=payslip_password
    )
    
    if result['success']:
        payroll.email_sent = True
        db.session.commit()
        flash(f'Payslip sent to {employee.email} (PDF open password: {payslip_password})', 'success')
    else:
        flash(f'Error sending email: {result["message"]}', 'danger')
    
    return redirect(url_for('payroll'))

# ==================== REPORTS ROUTES ====================

@app.route('/reports')
@login_required
@admin_required
def reports():
    """Display attendance report in HTML with enhanced filtering and analytics
    
    Uses AdminReportsService for centralized data generation.
    Ensures consistent data between screen display and PDF export.
    """
    from services.admin_reports_service import AdminReportsService
    
    # Get filter parameters
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    employee_id = request.args.get('employee_id')
    department = request.args.get('department')
    designation = request.args.get('designation')
    status = request.args.get('status')
    report_type = request.args.get('type', 'daily')
    
    # Parse dates
    start_date = None
    end_date = None
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except Exception as e:
            logger.error(f"Error parsing start_date: {e}")
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except Exception as e:
            logger.error(f"Error parsing end_date: {e}")
    
    # Convert employee_id to int if provided
    employee_id_int = None
    if employee_id and employee_id.strip():
        try:
            employee_id_int = int(employee_id)
        except Exception as e:
            logger.error(f"Error parsing employee_id: {e}")
    
    # Build filters dict
    filters = {
        'start_date': start_date,
        'end_date': end_date,
        'employee_id': employee_id_int,
        'department': department if department else None,
        'designation': designation if designation else None,
        'status': status if status else None
    }
    
    # Get all employees for dropdowns
    all_employees = Employee.query.filter_by(status='active').all()
    all_departments = sorted(set(emp.department for emp in all_employees))
    all_designations = sorted(set(emp.designation for emp in all_employees))
    
    # Generate report data using centralized service
    service = AdminReportsService()
    report_data = service.generate_report_data(filters)
    
    # Get activities for each attendance record
    am, _, _, _ = get_services()
    activities_by_attendance = {}
    for att in report_data['attendances']:
        if att.employee and not hasattr(att, 'is_dummy'):
            activities = AttendanceActivity.query.filter_by(
                employee_id=att.employee.id,
                attendance_date=att.date
            ).order_by(AttendanceActivity.activity_time).all()
            activities_by_attendance[(att.employee.id, att.date)] = activities
            
            # Add display_out_time for UI
            am._add_display_out_time(att, att.date)
    
    # Determine employee object if specific employee selected
    employee = None
    if employee_id_int:
        employee = Employee.query.get(employee_id_int)
    
    return render_template('reports.html',
                         attendances=report_data['attendances'],
                         employees=all_employees,
                         all_departments=all_departments,
                         all_designations=all_designations,
                         employee=employee,
                         employee_id=employee_id,
                         department=department,
                         designation=designation,
                         status=status,
                         start_date=start_date_str,
                         end_date=end_date_str,
                         report_type=report_type,
                         summary=report_data['summary'],
                         department_analytics=report_data['department_analytics'],
                         rankings=report_data['rankings'],
                         employee_summary=report_data['employee_summary'],
                         late_analysis=report_data['late_analysis'],
                         daily_trend=report_data['daily_trend'],
                         activities_by_attendance=activities_by_attendance)

@app.route('/reports/export')
@login_required
@admin_required
def export_report():
    """Export Admin Reports PDF using same filtered dataset as screen
    
    Uses AdminReportsService to ensure PDF contains exactly the same data
    as displayed on the Admin Reports page with applied filters.
    Uses dedicated generate_admin_reports_pdf() function for Admin Reports,
    separate from the employee attendance report generator.
    """
    from services.admin_reports_service import AdminReportsService
    
    # Get filter parameters (same as screen)
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    employee_id = request.args.get('employee_id')
    department = request.args.get('department')
    designation = request.args.get('designation')
    status = request.args.get('status')
    report_type = request.args.get('type', 'pdf')
    
    # Parse dates
    start_date = None
    end_date = None
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except Exception as e:
            logger.error(f"Error parsing start_date: {e}")
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except Exception as e:
            logger.error(f"Error parsing end_date: {e}")
    
    # Convert employee_id to int if provided
    employee_id_int = None
    if employee_id and employee_id.strip():
        try:
            employee_id_int = int(employee_id)
        except Exception as e:
            logger.error(f"Error parsing employee_id: {e}")
    
    # Build filters dict (same as screen)
    filters = {
        'start_date': start_date,
        'end_date': end_date,
        'employee_id': employee_id_int,
        'department': department if department else None,
        'designation': designation if designation else None,
        'status': status if status else None
    }
    
    # Generate report data using same service as screen
    service = AdminReportsService()
    report_data = service.generate_report_data(filters)
    
    # Generate filename
    if employee_id_int:
        filename = f"admin_report_employee_{employee_id_int}_{start_date_str}_to_{end_date_str}.pdf"
    else:
        filename = f"admin_report_{start_date_str}_to_{end_date_str}.pdf"
    
    output_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    # Generate PDF using dedicated Admin Reports PDF generator
    _, _, _, pg = get_services()
    company_settings = Settings.get_settings()
    pg.generate_admin_reports_pdf(report_data, filters, output_path, company_settings=company_settings)
    
    if not os.path.exists(output_path):
        logger.error("[ADMIN REPORT PDF] PDF generation failed - file not created")
        return jsonify({'success': False, 'message': 'PDF generation failed'})
    
    return send_file(output_path, as_attachment=True, download_name=filename)

@app.route('/employee-reports/export')
@login_required
@employee_required
def employee_export_report():
    """Export attendance report as PDF for logged-in employee only
    Uses the same filters as the displayed report page for consistency
    """
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    status_filter = request.args.get('status', '')
    
    if not start_date or not end_date:
        return jsonify({'success': False, 'message': 'Start date and end date are required'})
    
    employee_id = session['employee_id']
    employee = Employee.query.get(employee_id)
    
    if not employee:
        return jsonify({'success': False, 'message': 'Employee not found'})
    
    am, _, _, pg = get_services()
    
    start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
    end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
    
    # Adjust start_date to employee's joining date if needed
    effective_start_date = start_date
    if employee.joining_date:
        effective_start_date = max(start_date, employee.joining_date)
    
    # Check if entire report period is before joining date
    if employee.joining_date and end_date < employee.joining_date:
        return jsonify({'success': False, 'message': f"No attendance records are available. You joined on {employee.joining_date.strftime('%d-%m-%Y')}."})
    
    from datetime import timedelta
    
    # Exclude today from the historical report window - today is only
    # finalized after the day ends (evaluated starting midnight / next day).
    end_date = min(end_date, date.today() - timedelta(days=1))
    
    attendances = []
    current_date = effective_start_date
    while current_date <= end_date:
        # Get attendance data for this date using centralized function
        daily_attendance = am.calculate_attendance_with_absent(current_date)
        # Filter to only include the logged-in employee
        employee_attendance = [att for att in daily_attendance if att.employee.id == employee.id]
        attendances.extend(employee_attendance)
        current_date += timedelta(days=1)
    
    # Apply status filter if provided (same logic as employee_reports route)
    if status_filter and status_filter.strip():
        if status_filter == 'late':
            # Special handling for Late filter - use late_entry field instead of status
            attendances = [att for att in attendances if att.late_entry]
        else:
            # For other status filters, use status field
            attendances = [att for att in attendances if att.status == status_filter]
    
    # Add display_out_time for PDF export (show "-" after new IN until next OUT)
    # for att in attendances:
    #     am._add_display_out_time(att, att.date)
    
    # Add display_out_time for PDF export using the actual Attendance record
    for att in attendances:
        actual_attendance = Attendance.query.filter_by(
            employee_id=employee.id,
            date=att.date
        ).first()

        if actual_attendance:
            am._add_display_out_time(actual_attendance, att.date)
            att.display_out_time = actual_attendance.display_out_time
        else:
            att.display_out_time = None

    filename = f"attendance_report_{employee.employee_id}_{start_date}_to_{end_date}.pdf"
    output_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    company_settings = Settings.get_settings()
    pg.generate_attendance_report(attendances, employee, str(effective_start_date), str(end_date), output_path, company_settings=company_settings)
    
    if not os.path.exists(output_path):
        return jsonify({'success': False, 'message': 'PDF generation failed'})
    
    return send_file(output_path, as_attachment=True, download_name=filename)

# ==================== SETTINGS ROUTES ====================

@app.route('/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def settings():
    settings = Settings.get_settings()
    
    if request.method == 'POST':
        settings.company_name = request.form.get('company_name')
        settings.office_start_time = request.form.get('office_start_time')
        settings.office_end_time = request.form.get('office_end_time')
        
        # Safe parsing for numeric fields
        def _parse_int(field_name, default=0):
            value = request.form.get(field_name, '').strip()
            if not value:
                return default
            try:
                return int(value)
            except ValueError:
                return default
        
        def _parse_float(field_name, default=0.0):
            value = request.form.get(field_name, '').strip()
            if not value:
                return default
            try:
                return float(value)
            except ValueError:
                return default
        
        settings.grace_period_minutes = _parse_int('grace_period_minutes')
        settings.working_hours_per_day = _parse_float('working_hours_per_day')
        settings.late_deduction_enabled = request.form.get('late_deduction_enabled') == 'on'
        settings.late_deduction_per_occurrence = _parse_float('late_deduction_per_occurrence')
        settings.half_day_deduction_enabled = request.form.get('half_day_deduction_enabled') == 'on'
        settings.half_day_deduction_per_occurrence = _parse_float('half_day_deduction_per_occurrence')
        settings.absent_deduction_enabled = request.form.get('absent_deduction_enabled') == 'on'
        settings.absent_deduction_per_occurrence = _parse_float('absent_deduction_per_occurrence')
        settings.overtime_enabled = request.form.get('overtime_enabled') == 'on'
        settings.overtime_rate = _parse_float('overtime_rate')
        settings.face_recognition_tolerance = _parse_float('face_recognition_tolerance')
        settings.min_face_images_required = _parse_int('min_face_images_required')
        
        # Handle logo upload
        if 'company_logo' in request.files:
            file = request.files['company_logo']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"company_logo_{file.filename}")
                logo_path = os.path.join('static/images', filename)
                file.save(logo_path)
                settings.company_logo = f"static/images/{filename}"

        db.session.commit()

        # Create AttendanceSettingsHistory record for historical accuracy
        # This ensures old attendance records use old settings, new records use new settings
        from models import AttendanceSettingsHistory
        from datetime import datetime

        # Check if history already exists for this exact timestamp
        current_timestamp = datetime.now()
        existing_history = AttendanceSettingsHistory.query.filter_by(effective_from=current_timestamp).first()
        if not existing_history:
            # Create new history record effective from NOW (precise timestamp)
            history = AttendanceSettingsHistory(
                effective_from=current_timestamp,
                office_start_time=settings.office_start_time,
                office_end_time=settings.office_end_time,
                working_hours_per_day=settings.working_hours_per_day,
                half_day_hours=settings.half_day_hours,
                grace_period_minutes=settings.grace_period_minutes,
                created_by=session.get('admin_id')
            )
            db.session.add(history)
            db.session.commit()
            logger.info(f"ATTENDANCE SETTINGS HISTORY CREATED - Effective From: {current_timestamp}")

        flash("Settings updated successfully")
        return redirect(url_for("settings"))
    
    # Test email connection
    email_test = None
    if request.args.get('test_email'):
        _, _, es, _ = get_services()
        email_test = es.test_email_connection()
    
    return render_template('settings.html', settings=settings, email_test=email_test)

@app.route('/payroll-settings', methods=['GET', 'POST'])
@login_required
@admin_required
def payroll_settings():
    """Payroll automation and company settings for professional payslip generation"""
    payroll_settings = PayrollSettings.get_settings()
    company_settings = CompanySettings.get_settings()
    
    if request.method == 'POST':
        # Update payroll settings
        payroll_settings.payroll_generation_day = int(request.form.get('payroll_generation_day', 31))
        payroll_settings.payroll_generation_time = request.form.get('payroll_generation_time', '18:00')
        payroll_settings.auto_generate_payroll = request.form.get('auto_generate_payroll') == 'on'
        payroll_settings.auto_send_payslip_email = request.form.get('auto_send_payslip_email') == 'on'
        payroll_settings.payslip_storage_path = request.form.get('payslip_storage_path', 'payrolls')
        
        # Professional tax settings
        payroll_settings.professional_tax_jan = float(request.form.get('professional_tax_jan', 200.0))
        payroll_settings.professional_tax_feb = float(request.form.get('professional_tax_feb', 300.0))
        payroll_settings.professional_tax_mar = float(request.form.get('professional_tax_mar', 200.0))
        payroll_settings.professional_tax_apr = float(request.form.get('professional_tax_apr', 200.0))
        payroll_settings.professional_tax_may = float(request.form.get('professional_tax_may', 200.0))
        payroll_settings.professional_tax_jun = float(request.form.get('professional_tax_jun', 200.0))
        payroll_settings.professional_tax_jul = float(request.form.get('professional_tax_jul', 200.0))
        payroll_settings.professional_tax_aug = float(request.form.get('professional_tax_aug', 200.0))
        payroll_settings.professional_tax_sep = float(request.form.get('professional_tax_sep', 200.0))
        payroll_settings.professional_tax_oct = float(request.form.get('professional_tax_oct', 200.0))
        payroll_settings.professional_tax_nov = float(request.form.get('professional_tax_nov', 200.0))
        payroll_settings.professional_tax_dec = float(request.form.get('professional_tax_dec', 200.0))
        
        # Update company settings
        company_settings.company_name = request.form.get('company_name', company_settings.company_name)
        company_settings.company_address = request.form.get('company_address', company_settings.company_address)
        company_settings.company_phone = request.form.get('company_phone', company_settings.company_phone)
        company_settings.company_email = request.form.get('company_email', company_settings.company_email)
        company_settings.company_website = request.form.get('company_website', company_settings.company_website)
        
        # Handle company logo upload
        if 'company_logo' in request.files:
            file = request.files['company_logo']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"company_logo_{file.filename}")
                logo_path = os.path.join('static/images', filename)
                file.save(logo_path)
                company_settings.company_logo = f"static/images/{filename}"
        
        db.session.commit()
        
        # Reschedule payroll generation if settings changed
        payroll_scheduler.reschedule_payroll_generation()
        
        flash('Payroll settings updated successfully', 'success')
        return redirect(url_for('payroll_settings'))
    
    return render_template('payroll_settings.html', 
                         payroll_settings=payroll_settings,
                         company_settings=company_settings)

# ==================== EMPLOYEE-SPECIFIC ROUTES ====================

@app.route('/employee-attendance')
@login_required
@employee_required
def employee_attendance():
    """Employee attendance page showing only their own attendance with check-in/out functionality"""
    employee_id = session['employee_id']
    employee = Employee.query.get(employee_id)
    today = date.today()
    
    # Initialize attendance manager services
    am, _, _, _ = get_services()

    # Get today's attendance for this employee (exclude pending manual attendance)
    today_attendance = Attendance.query.filter_by(employee_id=employee_id, date=today).filter(
        db.or_(
            Attendance.attendance_type != 'MANUAL_PASSWORD',
            Attendance.approval_status == 'approved'
        )
    ).first()
    
    # Pass current_user to template for manager check
    current_user = employee
    
    # Add display_out_time for today's attendance (show "-" after new IN until next OUT)
    if today_attendance:
        # logger.info(f"employee_attendance - Processing today's attendance ID: {today_attendance.id}")
        # logger.info(f"  IN Time: {today_attendance.in_time}")
        # logger.info(f"  OUT Time: {today_attendance.out_time}")
        # logger.info(f"  Total Hours: {today_attendance.total_hours}")
        am._add_display_out_time(today_attendance, today)
        # logger.info(f"  Display OUT Time after _add_display_out_time: {today_attendance.display_out_time if hasattr(today_attendance, 'display_out_time') else 'N/A'}")
    
    # Determine attendance status
    attendance_status = {
        'can_check_in': False,
        'can_check_out': False,
        'completed': False,
        'in_time': None,
        'out_time': None,
        'working_hours': None,
        'status': None
    }
    
    if today_attendance:
        attendance_status['in_time'] = today_attendance.in_time.strftime('%H:%M:%S') if today_attendance.in_time else None
        # Use display_out_time for UI - shows "-" after new IN until next OUT
        if hasattr(today_attendance, 'display_out_time') and today_attendance.display_out_time:
            attendance_status['out_time'] = today_attendance.display_out_time.strftime('%H:%M:%S')
        else:
            attendance_status['out_time'] = None
            
        # Only show working hours if display_out_time exists (real manual checkout)
        if hasattr(today_attendance, 'display_out_time') and today_attendance.display_out_time:
            attendance_status['working_hours'] = round(today_attendance.total_hours, 2) if today_attendance.total_hours else 0.0
        else:
            attendance_status['working_hours'] = None
            
        attendance_status['status'] = today_attendance.status
        
        if today_attendance.in_time and today_attendance.out_time:
            attendance_status['completed'] = True
        elif today_attendance.in_time and not today_attendance.out_time:
            attendance_status['can_check_out'] = True
    else:
        attendance_status['can_check_in'] = True
    
    # Get attendance history for this employee (exclude pending manual attendance)
    attendance_records = Attendance.query.filter_by(employee_id=employee_id).filter(
        db.or_(
            Attendance.attendance_type != 'MANUAL_PASSWORD',
            Attendance.approval_status == 'approved'
        )
    ).order_by(
        Attendance.date.desc()
    ).limit(100).all()

    # Apply display-only auto checkout for past records missing OUT time
    for att in attendance_records:
        # logger.info(f"employee_attendance - Processing historical record ID: {att.id}, Date: {att.date}")
        # logger.info(f"  IN Time: {att.in_time}")
        # logger.info(f"  OUT Time: {att.out_time}")
        # CRITICAL: Recalculate status for past records using the calculator
        # This ensures past records show correct Present/Half Day/Absent status
        if att.in_time and att.date < today:
            am.calculator.recalculate_attendance(att, is_final_calculation=True)
            # logger.info(f"  Status recalculated: {att.status}, Hours: {att.total_hours}")
        # Add display_out_time for UI (show "-" after new IN until next OUT)
        am._add_display_out_time(att, att.date)
        # logger.info(f"  Display OUT Time after _add_display_out_time: {att.display_out_time if hasattr(att, 'display_out_time') else 'N/A'}")
    
    return render_template('employee_attendance.html', 
                         employee=employee, 
                         current_user=current_user,
                         attendance_records=attendance_records,
                         attendance_status=attendance_status,
                         today=today)

@app.route('/employee-profile', methods=['GET', 'POST'])
@login_required
def employee_profile():
    """Employee profile page - view and edit profile"""
    if 'employee_id' not in session:
        return redirect(url_for('employee_login'))
    
    employee_id = session['employee_id']
    employee = Employee.query.get(employee_id)
    login_creds = EmployeeLogin.query.filter_by(employee_id=employee_id).first()
    current_user = employee
    
    if not employee:
        flash('Employee not found', 'danger')
        return redirect(url_for('employee_login'))
    
    if request.method == 'POST':
        # Handle profile editing
        phone = request.form.get('phone')
        email = request.form.get('email')
        
        # Validation
        if not phone or not email:
            flash('Phone and Email are required', 'danger')
        elif len(phone) < 10:
            flash('Phone number must be at least 10 digits', 'danger')
        elif '@' not in email or '.' not in email:
            flash('Invalid email format', 'danger')
        else:
            # Update employee information
            employee.phone = phone
            employee.email = email
            employee.updated_at = datetime.utcnow()
            db.session.commit()
            flash('Profile updated successfully', 'success')
            return redirect(url_for('employee_profile'))
    
    return render_template('employee_profile.html', employee=employee, current_user=current_user, login_creds=login_creds)

@app.route('/employee-payroll')
@login_required
@employee_required
def employee_payroll():
    """Employee payroll page showing only their own payroll"""
    employee_id = session['employee_id']
    employee = Employee.query.get(employee_id)

    current_user = employee
    
    # Get payroll records for this employee
    payroll_records = Payroll.query.filter_by(employee_id=employee_id).order_by(
        Payroll.year.desc(),
        Payroll.month.desc()
    ).all()
    
    return render_template('employee_payroll.html', 
                         employee=employee, 
                         current_user=current_user,
                         payroll_records=payroll_records)

@app.route('/employee-reports')
@login_required
@employee_required
def employee_reports():
    """Employee reports page showing only their own reports"""
    employee_id = session['employee_id']
    employee = Employee.query.get(employee_id)

    current_user = employee
    
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    status_filter = request.args.get('status')
    
    am, _, _, _ = get_services()
    
    # Validation messages
    validation_error = None
    
    # Default to joining date to today if no filters provided (initial page load)
    if not start_date and not end_date and not status_filter:
        start_date = employee.joining_date if employee.joining_date else date.today()
        end_date = date.today()
    elif start_date or end_date or status_filter:
        # User clicked filter button - validate and apply filters
        if start_date:
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
                # Validate against joining date
                if employee.joining_date and start_date < employee.joining_date:
                    validation_error = "You cannot view attendance records before your joining date."
                    start_date = employee.joining_date
            except ValueError:
                validation_error = "Invalid From Date format."
                start_date = employee.joining_date if employee.joining_date else date.today()
        else:
            start_date = employee.joining_date if employee.joining_date else date.today()
        
        if end_date:
            try:
                end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
                # Validate end date is not before start date
                if end_date < start_date:
                    validation_error = "To Date cannot be earlier than From Date."
                    end_date = start_date
            except ValueError:
                validation_error = "Invalid To Date format."
                end_date = date.today()
        else:
            end_date = date.today()
    else:
        # Default case
        start_date = employee.joining_date if employee.joining_date else date.today()
        end_date = date.today()
    
    # Exclude today from the historical report window - today is only
    # finalized after the day ends (evaluated starting midnight / next day).
    end_date = min(end_date, date.today() - timedelta(days=1))
    
    # Get attendance for this employee only
    attendances = []
    current_date = start_date
    while current_date <= end_date:
        daily_attendance = am.calculate_attendance_with_absent(current_date)
        employee_attendance = [att for att in daily_attendance if att.employee.id == employee_id]
        attendances.extend(employee_attendance)
        current_date += timedelta(days=1)
    
    # Apply status filter if provided
    if status_filter and status_filter.strip():
        # Normalize the requested filter status
        requested_status = normalize_attendance_status(status_filter)
        
        if status_filter == 'late':
            # Special handling for Late filter - use late_entry field instead of status
            attendances = [att for att in attendances if att.late_entry]
        else:
            # For other status filters, use effective report status
            # This ensures Half Day records with raw status='Pending' are correctly filtered
            attendances = [
                att for att in attendances 
                if get_effective_report_status(att) == requested_status
            ]
    
    # For template, pass empty strings for date fields if no filter was applied
    # This ensures date fields are empty on initial page load
    template_start_date = request.args.get('start_date', '')
    template_end_date = request.args.get('end_date', '')
    
    # Get activities for each attendance record
    activities_by_attendance = {}
    for att in attendances:
        if att.employee and not hasattr(att, 'is_dummy'):
            activities = AttendanceActivity.query.filter_by(
                employee_id=att.employee.id,
                attendance_date=att.date
            ).order_by(AttendanceActivity.activity_time).all()
            activities_by_attendance[(att.employee.id, att.date)] = activities
            
            # CRITICAL: Apply same status recalculation logic as Employee Dashboard
            # This ensures past records show correct Present/Half Day/Absent status
            # matching the Dashboard exactly
            if att.in_time and att.date < date.today():
                am.calculator.recalculate_attendance(att, is_final_calculation=True)
            
            # Add display_out_time for UI (show "-" after new IN until next OUT)
            am._add_display_out_time(att, att.date)
    
    return render_template('employee_reports.html', 
                         employee=employee, 
                         current_user=current_user,
                         attendances=attendances,
                         start_date=template_start_date,
                         end_date=template_end_date,
                         status_filter=status_filter,
                         validation_error=validation_error,
                         activities_by_attendance=activities_by_attendance)

@app.route('/api/employee-attendance', methods=['POST'])
@login_required
@employee_required
def employee_attendance_api():
    """API endpoint for employee to mark their own attendance using face recognition"""
    employee_id = session['employee_id']
    employee = Employee.query.get(employee_id)

    # Cheap housekeeping for the frame-presence lock (see tracker class above).
    employee_attendance_presence_tracker.sweep()

    if not employee:
        return jsonify({'success': False, 'message': 'Employee not found'})
    
    if 'image' not in request.files:
        return jsonify({'success': False, 'message': 'No image provided'})
    
    file = request.files['image']
    
    # Save temporary image
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_file:
        temp_file.write(file.read())
        temp_image = temp_file.name
    
    try:
        # Read the image file and convert to numpy array for face recognition
        import cv2
        frame = cv2.imread(temp_image)
        
        if frame is None:
            os.unlink(temp_image)
            return jsonify({'success': False, 'message': 'Failed to read image file'})
        
        # Use face recognition to verify employee identity using global instance
        # Only compare against this employee's face images
        recognizer = get_face_recognizer()
        results = recognizer.recognize_face(
            frame,
            target_employee_id=str(employee.id)
        )
                        
        # Clean up temp file
        os.unlink(temp_image)
        
        # recognize_face returns a list, extract the first/best match
        if not results or len(results) == 0:
            return jsonify({'success': False, 'message': 'Face recognition failed. No matches found.'})
        
        # Get the first (best) match from the list
        result = results[0] if isinstance(results, list) else results
        
        # Check if the result has required fields (employee_id and confidence)
        if not result or not isinstance(result, dict):
            return jsonify({'success': False, 'message': 'Face recognition failed. Invalid result format.'})
        
        # if not result.get('employee_id') or not result.get('confidence'):
        #     return jsonify({'success': False, 'message': 'Face recognition failed. Missing required fields.'})
        if not result.get('employee_id'):
            return jsonify({
                'success': False,
                'message': 'Face recognition failed. Employee ID missing.',
                'debug_result': result
            })
        # Verify the recognized employee matches the logged-in employee
        if result.get('employee_id') != str(employee.id):
            return jsonify({'success': False, 'message': 'Face does not match your profile. Please try again.'})

        # ------------------------------------------------------------------
        # FRAME-PRESENCE LOCK (Employee Login stream ONLY)
        # ------------------------------------------------------------------
        # The face has just been verified as belonging to this employee -
        # record that they are currently visible in the camera, then check
        # whether they're still within an already-marked, continuous
        # presence. If so, do NOT touch the database again; simply report
        # that attendance is already locked in for this presence.
        employee_attendance_presence_tracker.note_face_seen(employee_id)

        if not employee_attendance_presence_tracker.should_attempt_mark(employee_id):
            return jsonify({
                'success': True,
                'locked': True,
                'message': 'Attendance already marked. Step out of camera view and return to mark again.'
            })
        # ------------------------------------------------------------------

        # Use existing attendance logic to mark attendance
        am, _, _, _ = get_services()
        
        # Check if employee has pending logout approval requests
        from services.approval_service import approval_service
        if approval_service.has_pending_approval(employee_id):
            return jsonify({'success': False, 'message': 'Your previous day\'s logout is pending Manager approval. Please contact your Manager.'})
        
        # Automatically determine if check-in or check-out based on today's attendance
        today = date.today()
        existing_attendance = Attendance.query.filter_by(employee_id=employee_id, date=today).first()
        
        if not existing_attendance:
            # No attendance today - perform check-in
            confidence = result.get('confidence') if isinstance(result, dict) else None
            mark_result = am.mark_attendance(employee_id, confidence)
            
            if mark_result.get('success'):
                attendance = Attendance.query.filter_by(employee_id=employee_id, date=today).first()
                # Freeze further attempts for this continuous presence.
                employee_attendance_presence_tracker.lock(employee_id)
                # CRITICAL: Return the status to verify it's saved correctly
                return jsonify({
                    'success': True,
                    'message': 'Check in successful',
                    'in_time': attendance.in_time.strftime('%H:%M:%S') if attendance.in_time else None,
                    'status': attendance.status,  # Return status for verification
                    'is_late': attendance.late_entry  # Return late flag for verification
                })
            else:
                return jsonify({'success': False, 'message': mark_result.get('message', 'Check in failed')})
        
        elif existing_attendance.in_time and not existing_attendance.out_time:
            # Checked in but not out - perform manual check-out with actual time
            mark_result = am.mark_out(existing_attendance, result.get('confidence'))
            
            if mark_result.get('success'):
                # Freeze further attempts for this continuous presence.
                employee_attendance_presence_tracker.lock(employee_id)
                return jsonify({
                    'success': True,
                    'message': 'Check out successful',
                    'out_time': existing_attendance.out_time.strftime('%H:%M:%S') if existing_attendance.out_time else None,
                    'working_hours': round(existing_attendance.total_hours, 2) if existing_attendance.total_hours else 0.0
                })
            else:
                return jsonify({'success': False, 'message': mark_result.get('message', 'Check out failed')})
        
        else:
            # Already checked out - log additional activity
            mark_result = am.mark_attendance(employee_id, result.get('confidence'))
            
            if mark_result.get('success'):
                # Freeze further attempts for this continuous presence.
                employee_attendance_presence_tracker.lock(employee_id)
                return jsonify({
                    'success': True,
                    'message': mark_result.get('message'),
                    'action': mark_result.get('action'),
                    'time': mark_result.get('time')
                })
            else:
                return jsonify({'success': False, 'message': mark_result.get('message', 'Activity logging failed')})
    
    except Exception as e:
        # Clean up temp file if error
        if os.path.exists(temp_image):
            os.unlink(temp_image)
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})

# ==================== API ROUTES ====================

@app.route('/api/recognize-face', methods=['POST'])
@login_required
def recognize_face_api():
    """
    API endpoint for face recognition

    Supports optimized recognition when employee_id is provided.
    If employee_id is provided, only compares against that employee's images.
    """

    if 'image' not in request.files:
        return jsonify({
            'success': False,
            'message': 'No image provided'
        })

    file = request.files['image']

    # Employee Code from frontend (Example: EMP0002)
    employee_code = request.form.get('employee_id')

    # Internal Database ID (Example: 2)
    target_employee_id = None

    if employee_code:
        employee = Employee.query.filter_by(employee_id=employee_code).first()

        if not employee:
            return jsonify({
                'success': False,
                'message': 'Employee ID not found.'
            })

        target_employee_id = str(employee.id)

    if file:
        try:
            # Read image
            npimg = np.frombuffer(file.read(), np.uint8)
            frame = cv2.imdecode(npimg, cv2.IMREAD_COLOR)

            if frame is None:
                return jsonify({
                    'success': False,
                    'message': 'Invalid image'
                })

            # Face Recognition using global instance
            recognizer = get_face_recognizer()

            results = recognizer.recognize_face(
                frame,
                target_employee_id=target_employee_id
            )

            if results and results[0]["name"] != "Unknown":

                employee = Employee.query.get(results[0]["employee_id"])

                if employee:
                    return jsonify({
                        "success": True,
                        "employee_id": employee.id,
                        "employee_id_str": employee.employee_id,
                        "name": employee.name,
                        "department": employee.department,
                        "confidence": results[0]["confidence"]
                    })

            return jsonify({
                "success": False,
                "message": "Face does not match Employee ID."
            })

        except Exception as e:
            return jsonify({
                "success": False,
                "message": str(e)
            })

    return jsonify({
        "success": False,
        "message": "No file uploaded"
    })

@app.route('/api/auto-scan-attendance', methods=['POST'])
def auto_scan_attendance_api():
    """
    Real-time, no-emp_id attendance scanning endpoint.

    Intentionally PUBLIC (no @login_required / @admin_required): this is
    the endpoint the public kiosk landing page ('/') polls continuously so
    anyone can walk up and be recognized with no login step at all. Identity
    is never taken from a session - it comes purely from face recognition
    against the trained employee dataset, so there is nothing an
    authenticated session would add here. The kiosk device itself should
    still be physically/network secured (e.g. deployed on a trusted LAN or
    behind a reverse-proxy allow-list) since this endpoint accepts frames
    from anyone who can reach it.

    Designed to be called continuously (every ~1.5-2s) by the camera feed
    on the Attendance page. For every frame it:
      1. Detects & recognizes EVERY face in the frame (multi-person aware).
      2. For each recognized employee, consults the server-side
         AttendancePresenceTracker so attendance is logged EXACTLY ONCE
         per continuous presence - repeated detections of the same person
         while they remain in frame are reported as 'already_logged' and
         do NOT touch the database again. A new log is only permitted
         after the tracker sees them go missing from frames for longer
         than its presence timeout (i.e. they actually left) and then
         return.
      3. Unrecognized / low-confidence / ambiguous faces are reported as
         'unknown' and never logged.

    No employee_id / emp_id is required or accepted from the client -
    identity is determined purely from the face itself.
    """
    if 'image' not in request.files:
        return jsonify({'success': False, 'message': 'No image provided', 'faces': []})

    file = request.files['image']

    try:
        npimg = np.frombuffer(file.read(), np.uint8)
        frame = cv2.imdecode(npimg, cv2.IMREAD_COLOR)
    except Exception as e:
        return jsonify({'success': False, 'message': f'Invalid image: {e}', 'faces': []})

    if frame is None:
        return jsonify({'success': False, 'message': 'Invalid image', 'faces': []})

    # Periodic housekeeping - cheap, and keeps the tracker's memory bounded.
    presence_tracker.sweep()

    recognizer = get_face_recognizer()

    try:
        # target_employee_id intentionally omitted: scan against ALL
        # registered employees, and analyze every face found in the frame.
        detections = recognizer.recognize_face(frame)
    except Exception as e:
        logger.exception(f"auto_scan_attendance_api recognition error: {e}")
        return jsonify({'success': False, 'message': f'Recognition error: {e}', 'faces': []})

    am, _, _, _ = get_services()
    from services.approval_service import approval_service

    face_results = []

    for detection in detections:
        bbox = detection.get('bbox')

        # --- Face not matched to any employee: report and skip logging ---
        if not detection.get('employee_id'):
            face_results.append({
                'status': 'unknown',
                'name': 'Unknown',
                'bbox': bbox,
                'confidence': detection.get('confidence', 0.0)
            })
            continue

        employee_id_int = int(detection['employee_id'])
        employee = Employee.query.get(employee_id_int)
        if not employee or employee.status != 'active':
            # Trained model reference exists but employee record is gone /
            # inactive - treat as unrecognized rather than logging anything.
            face_results.append({
                'status': 'unknown',
                'name': 'Unknown',
                'bbox': bbox,
                'confidence': detection.get('confidence', 0.0)
            })
            continue

        confidence = detection.get('confidence', 0.0)

        # --- Presence / cooldown gate: log at most once per continuous visit ---
        is_new_presence = presence_tracker.register_detection(employee_id_int)

        if not is_new_presence:
            face_results.append({
                'status': 'already_logged',
                'employee_id': employee.id,
                'employee_id_str': employee.employee_id,
                'name': employee.name,
                'department': employee.department,
                'confidence': confidence,
                'bbox': bbox,
                'message': 'Attendance already recorded for this presence. Step out of frame to scan again.'
            })
            continue

        # --- New presence: attempt to log attendance ---
        if approval_service.has_pending_approval(employee_id_int):
            face_results.append({
                'status': 'blocked',
                'employee_id': employee.id,
                'employee_id_str': employee.employee_id,
                'name': employee.name,
                'department': employee.department,
                'confidence': confidence,
                'bbox': bbox,
                'message': "Previous day's logout approval is pending with your manager."
            })
            continue

        today = date.today()
        existing_attendance = Attendance.query.filter_by(
            employee_id=employee_id_int, date=today
        ).first()

        if existing_attendance:
            rejected_request = LogoutApprovalRequest.query.filter_by(
                attendance_id=existing_attendance.id,
                request_type='auto_logout',
                status='rejected'
            ).first()
            if rejected_request:
                face_results.append({
                    'status': 'blocked',
                    'employee_id': employee.id,
                    'employee_id_str': employee.employee_id,
                    'name': employee.name,
                    'department': employee.department,
                    'confidence': confidence,
                    'bbox': bbox,
                    'message': "Today's attendance was marked Absent (logout request rejected)."
                })
                continue

        mark_result = am.mark_attendance(employee_id_int, confidence)

        if mark_result.get('success'):
            face_results.append({
                'status': 'logged',
                'employee_id': employee.id,
                'employee_id_str': employee.employee_id,
                'name': employee.name,
                'department': employee.department,
                'confidence': confidence,
                'bbox': bbox,
                'action': mark_result.get('action'),
                'message': mark_result.get('message'),
                'in_time': mark_result.get('in_time'),
                'out_time': mark_result.get('out_time'),
                'time': mark_result.get('time')
            })
        else:
            # DB-level rule blocked it (e.g. Sunday, attendance closed).
            # Presence has already been marked as "seen" above; since no
            # attendance was actually written, allow another attempt next
            # time they're seen without waiting for the full timeout.
            presence_tracker.reset(employee_id_int)
            face_results.append({
                'status': 'blocked',
                'employee_id': employee.id,
                'employee_id_str': employee.employee_id,
                'name': employee.name,
                'department': employee.department,
                'confidence': confidence,
                'bbox': bbox,
                'message': mark_result.get('message', 'Attendance not marked')
            })

    return jsonify({'success': True, 'faces': face_results})


@app.route('/api/verify-employee-id/<employee_id>', methods=['GET'])
@login_required
def verify_employee_id(employee_id):
    """API endpoint to verify employee ID exists and is active
    
    Used for optimized face recognition - verifies employee before starting camera.
    """
    try:
        # Search by employee_id (e.g., EMP0001)
        employee = Employee.query.filter_by(employee_id=employee_id).first()
        
        if employee:
            # Check if employee is active
            if employee.status != 'active':
                return jsonify({
                    'success': False,
                    'message': f'Employee {employee_id} is not active'
                })
            
            # Check if employee has face images registered
            dataset_folder = os.path.join(Config.DATASET_FOLDER, str(employee.id))
            has_face_images = os.path.exists(dataset_folder) and len([f for f in os.listdir(dataset_folder) if f.endswith(('.jpg', '.jpeg', '.png'))]) > 0
            
            if not has_face_images:
                return jsonify({
                    'success': False,
                    'message': f'Employee {employee_id} has no face images registered. Please register face images first.'
                })
            
            return jsonify({
                'success': True,
                'message': 'Employee ID verified successfully',
                'employee_id': employee.id,
                'name': employee.name,
                'department': employee.department,
                'employee_id_str': employee.employee_id
            })
        else:
            return jsonify({
                'success': False,
                'message': f'Employee ID {employee_id} not found'
            })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error verifying employee ID: {str(e)}'
        })

@app.route('/api/mark-attendance', methods=['POST'])
@login_required
def mark_attendance_api():
    """API endpoint to mark attendance"""
    employee_id = int(request.form.get('employee_id'))
    confidence = float(request.form.get('confidence', 0.0))
    
    # Check if employee has pending logout approval - block attendance marking
    from services.approval_service import approval_service
    if approval_service.has_pending_approval(employee_id):
        logger.warning(f"[Attendance Blocked] Employee ID: {employee_id}, Reason: Pending logout approval")
        return jsonify({
            'success': False,
            'message': "Your previous day's logout approval is pending with your manager. You can login, but attendance marking is temporarily unavailable until the request is approved or rejected."
        })
    
    # Check if employee has a rejected Auto Logout Approval for today - block attendance marking
    today = date.today()
    attendance = Attendance.query.filter_by(employee_id=employee_id, date=today).first()
    if attendance:
        rejected_request = LogoutApprovalRequest.query.filter_by(
            attendance_id=attendance.id,
            request_type='auto_logout',
            status='rejected'
        ).first()
        if rejected_request:
            logger.warning(f"[Attendance Blocked] Employee ID: {employee_id}, Attendance ID: {attendance.id}, Date: {today}, Reason: Rejected Auto Logout Approval")
            return jsonify({
                'success': False,
                'message': "Today's attendance was marked Absent because the logout request was rejected. You cannot mark attendance again today."
            })
    
    am, _, _, _ = get_services()
    result = am.mark_attendance(employee_id, confidence)
    return jsonify(result)

@app.route('/mark_manual_attendance', methods=['POST'])
def mark_manual_attendance():
    """
    Secure manual attendance fallback for the camera page.

    Intentionally PUBLIC (no @login_required): this is the fallback action
    on the public kiosk landing page ('/'), which has no browser session at
    all. It is still fully secured on its own terms - every call is
    authenticated inline against the Employee ID + account password below,
    exactly like the login page, so removing the outer session check does
    not weaken it.

    Used ONLY when face recognition fails to identify someone. Requires
    BOTH the Employee ID and that employee's own account password (the
    same credential used on the Employee Dashboard login page) before
    marking any attendance - a co-worker who only knows someone's
    Employee ID can no longer punch attendance on their behalf ("proxy
    attendance"), since the previous version of this fallback accepted
    the Employee ID alone with no password check at all.

    Expects a JSON body: {"employee_id": "EMP0001", "password": "..."}
    """
    data = request.get_json(silent=True) or {}
    employee_id_str = (data.get('employee_id') or '').strip()
    password = data.get('password') or ''

    if not employee_id_str or not password:
        return jsonify({'success': False, 'message': 'Employee ID and password are required'}), 400

    employee = Employee.query.filter_by(employee_id=employee_id_str).first()

    # Deliberately return the SAME generic message whether the Employee ID
    # doesn't exist, the account has no credentials, or the password is
    # wrong - this endpoint must not let someone probe which Employee IDs
    # are valid.
    invalid_credentials_response = jsonify({
        'success': False,
        'message': 'Invalid Employee ID or password'
    })

    if not employee:
        return invalid_credentials_response

    if employee.status != 'active':
        return jsonify({'success': False, 'message': 'This employee account is not active'})

    login_creds = EmployeeLogin.query.filter_by(employee_id=employee.id).first()
    if not login_creds or not login_creds.is_active:
        return invalid_credentials_response

    # Validate against the LIVE password hash. EmployeeLogin.check_password()
    # wraps werkzeug.security.check_password_hash() against whatever
    # login_creds.password_hash currently holds - nothing here is
    # hardcoded or cached, so if the employee changes their password later
    # (via the normal "change password" flow), this check automatically
    # validates against the new password with zero code changes required.
    # A valid, still-unexpired temporary password (e.g. an admin-issued
    # reset) is also accepted, exactly like the real login page.
    password_ok = login_creds.check_password(password)
    if not password_ok and login_creds.check_temporary_password(password):
        password_ok = login_creds.is_temporary_password_valid()

    if not password_ok:
        return invalid_credentials_response

    # Same anti-abuse safeguards used by the rest of the attendance system.
    from services.approval_service import approval_service
    if approval_service.has_pending_approval(employee.id):
        logger.warning(f"[Manual Attendance Blocked] Employee ID: {employee.id}, Reason: Pending logout approval")
        return jsonify({
            'success': False,
            'message': "Your previous day's logout approval is pending with your manager. "
                       "Attendance marking is temporarily unavailable until it is approved or rejected."
        })

    today = datetime.now().date()
    now = datetime.now()
    existing_attendance = Attendance.query.filter_by(employee_id=employee.id, date=today).first()

    if existing_attendance:
        rejected_request = LogoutApprovalRequest.query.filter_by(
            attendance_id=existing_attendance.id,
            request_type='auto_logout',
            status='rejected'
        ).first()
        if rejected_request:
            logger.warning(f"[Manual Attendance Blocked] Employee ID: {employee.id}, Attendance ID: {existing_attendance.id}, Reason: Rejected Auto Logout Approval")
            return jsonify({
                'success': False,
                'message': "Today's attendance was marked Absent because a logout request was rejected. "
                           "You cannot mark attendance again today."
            })

    am, _, email_service, _ = get_services()

    # Record the exact timestamp when the employee clicked "Mark Attendance"
    submission_timestamp = now

    # `is_manager` drives which email(s) get sent (employee-only vs
    # employee+Admin). `designation` (not `role`) is the field used
    # everywhere else in this app to identify a Manager, so it's used here
    # too for consistency.
    is_manager = (employee.designation == 'Manager')

    # ------------------------------------------------------------------
    # Manual attendance state machine for TODAY's record (if one already
    # exists as a MANUAL_PASSWORD request from an earlier click today).
    # ------------------------------------------------------------------
    if existing_attendance and existing_attendance.attendance_type == 'MANUAL_PASSWORD':

        if existing_attendance.approval_status == 'pending':
            # Pending State Restriction: while the Mark IN request is still
            # awaiting approval, NO further action (OUT or any secondary
            # punch) is allowed for that day.
            logger.warning(
                f"[Manual Attendance Blocked] Employee ID: {employee.id}, "
                f"Attendance ID: {existing_attendance.id}, Reason: Manual attendance still pending approval"
            )
            return jsonify({
                'success': False,
                'message': "Your manual attendance request for today is still pending approval. "
                           "You can't mark OUT or submit another request until it is approved or rejected."
            })

        if existing_attendance.approval_status == 'rejected':
            # Retry Window: a rejected request may be corrected and
            # resubmitted for the SAME day, any time up until office end
            # time.
            settings = Settings.get_settings()
            office_end = am._parse_time(settings.office_end_time)
            if now.time() > office_end:
                logger.warning(
                    f"[Manual Attendance Blocked] Employee ID: {employee.id}, "
                    f"Attendance ID: {existing_attendance.id}, Reason: Retry window closed (past office end time)"
                )
                return jsonify({
                    'success': False,
                    'message': "Your manual attendance request for today was rejected, and the retry window "
                               "(office end time) has passed. Please contact your manager or administrator."
                })

            # Start this day's record fresh: clear the activities logged
            # under the rejected attempt and re-open it as a brand-new
            # pending Mark IN request.
            AttendanceActivity.query.filter_by(
                employee_id=employee.id,
                attendance_date=today
            ).delete()

            existing_attendance.in_time = now
            existing_attendance.out_time = None
            existing_attendance.total_hours = 0.0
            existing_attendance.overtime_hours = 0.0
            existing_attendance.early_exit = False
            existing_attendance.status = 'present'
            existing_attendance.confidence = 1.0
            existing_attendance.attendance_type = 'MANUAL_PASSWORD'
            existing_attendance.approval_status = 'pending'
            existing_attendance.submission_timestamp = submission_timestamp
            existing_attendance.late_entry = am.calculator.calculate_late_status(existing_attendance)

            db.session.add(AttendanceActivity(
                employee_id=employee.id,
                attendance_date=today,
                activity_time=now.time(),
                action='IN'
            ))
            db.session.commit()

            logger.info(
                f"[Manual Password Attendance Retry] Employee ID: {employee.id} "
                f"({employee.employee_id}) resubmitted attendance at {submission_timestamp} after an earlier rejection"
            )

            try:
                if email_service:
                    email_service.send_manual_attendance_submission_notification(
                        employee_email=employee.email,
                        employee_name=employee.name,
                        employee_id=employee.employee_id,
                        submission_timestamp=submission_timestamp,
                        is_manager=is_manager
                    )
            except Exception as e:
                logger.error(f"[Manual Attendance Email] Failed to send notification: {e}")

            return jsonify({
                'success': True,
                'message': f'IN marked successfully for {employee.name}',
                'attendance_id': existing_attendance.id,
                'in_time': now.strftime('%H:%M:%S'),
                'is_late': existing_attendance.late_entry,
                'status': existing_attendance.status
            })

        # approval_status == 'approved' -> Post-Approval Freedom: the day
        # was already vetted and is already visible everywhere, so further
        # IN/OUT/adjustments proceed normally and do NOT get reset back to
        # 'pending'.
        result = am.mark_attendance(employee.id, confidence=1.0)
        if result.get('success'):
            existing_attendance.submission_timestamp = submission_timestamp
            db.session.commit()
            logger.info(
                f"[Manual Password Attendance] Employee ID: {employee.id} "
                f"({employee.employee_id}) recorded a post-approval update at {submission_timestamp}"
            )
        return jsonify(result)

    # ------------------------------------------------------------------
    # No pending/rejected/approved MANUAL_PASSWORD record already governs
    # today - this click starts a brand-new manual attendance request
    # (either the very first attendance of the day, or a manual fallback
    # action on top of an already-approved FACE_RECOGNITION record). Either
    # way it must go through the full Hidden-Until-Approved approval cycle.
    # ------------------------------------------------------------------
    # confidence=1.0: this identity has been verified by password, which is
    # a stronger proof than an unverified camera match, so it's recorded
    # at maximum confidence.
    result = am.mark_attendance(employee.id, confidence=1.0)

    if result.get('success'):
        # Tag how this record was captured without touching the
        # status/late/hours fields - those are correctly computed by the
        # existing attendance calculator (e.g. a manual punch after office
        # hours should still show as "Late", not be force-set to
        # "Present", which is why we don't hardcode status here).
        attendance_row = Attendance.query.filter_by(employee_id=employee.id, date=today).first()
        if attendance_row:
            attendance_row.attendance_type = 'MANUAL_PASSWORD'
            attendance_row.approval_status = 'pending'
            attendance_row.submission_timestamp = submission_timestamp
            db.session.commit()

        logger.info(
            f"[Manual Password Attendance] Employee ID: {employee.id} "
            f"({employee.employee_id}) marked attendance via password fallback at {submission_timestamp}"
        )

        # Send email notification to employee (and Admin, if a Manager)
        # with the exact clicked timestamp as proof of check-in time.
        try:
            if email_service:
                email_service.send_manual_attendance_submission_notification(
                    employee_email=employee.email,
                    employee_name=employee.name,
                    employee_id=employee.employee_id,
                    submission_timestamp=submission_timestamp,
                    is_manager=is_manager
                )
        except Exception as e:
            logger.error(f"[Manual Attendance Email] Failed to send notification: {e}")

    return jsonify(result)

def _count_dataset_images(folder):
    """Recount images straight from disk. Never trust a cached DB value -
    this is what lets the system notice a dataset folder that an admin
    emptied or deleted by hand, without any special "reset" flag."""
    if not os.path.exists(folder):
        return 0
    return len([f for f in os.listdir(folder) if f.lower().endswith(('.jpg', '.jpeg', '.png'))])


def _get_required_face_image_count():
    """Admin-configurable capture/training target, falling back to the
    Config default (20) if Settings hasn't been initialized yet."""
    settings = Settings.get_settings()
    if settings and settings.min_face_images_required:
        return settings.min_face_images_required
    return Config.MIN_FACE_IMAGES_REQUIRED


@app.route('/api/face-dataset-status/<int:employee_id>', methods=['GET'])
@login_required
def face_dataset_status(employee_id):
    """Live status of an employee's face dataset, read fresh from disk on
    every call. The frontend polls/calls this on page load and before each
    capture attempt so it can dynamically enable/disable the Capture button -
    including automatically re-enabling it if an admin manually deleted the
    dataset folder on the server."""
    employee = Employee.query.filter_by(id=employee_id).first()
    if not employee:
        return jsonify({'success': False, 'message': 'Employee not found'}), 404

    required_count = _get_required_face_image_count()
    dataset_folder = os.path.join(Config.DATASET_FOLDER, str(employee.id))
    current_count = _count_dataset_images(dataset_folder)

    # Keep the cached DB counter in sync with reality.
    if employee.face_images_count != current_count:
        employee.face_images_count = current_count
        db.session.commit()

    recognizer = get_face_recognizer()
    is_trained = str(employee.id) in recognizer.known_face_ids

    return jsonify({
        'success': True,
        'employee_id': employee.id,
        'count': current_count,
        'required': required_count,
        'remaining': max(0, required_count - current_count),
        'is_trained': is_trained,
        # Capture is only allowed while the live on-disk count is below the
        # required target. If the folder is emptied/deleted, count drops to
        # 0 and this flips back to True automatically.
        'can_capture': current_count < required_count
    })


@app.route('/api/upload-face-image', methods=['POST'])
@login_required
def upload_face_image():
    """API endpoint to upload a single face image into an employee's
    training dataset. Re-checks the live image count on disk on every
    request (rather than trusting a cached DB value or the frontend), so a
    manually cleared dataset folder is picked up immediately and a full
    dataset can't be exceeded by a stray/duplicate request."""
    if 'image' not in request.files:
        return jsonify({'success': False, 'message': 'No image file provided'}), 400

    file = request.files['image']
    employee_id = request.form.get('employee_id')

    if not employee_id:
        return jsonify({'success': False, 'message': 'Employee ID required'}), 400

    if file.filename == '':
        return jsonify({'success': False, 'message': 'No file selected'}), 400

    try:
        employee = Employee.query.filter_by(id=int(employee_id)).first()
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'Invalid employee ID'}), 400

    if not employee:
        return jsonify({'success': False, 'message': 'Employee not found'}), 404

    required_count = _get_required_face_image_count()

    # Create dataset folder for employee if it doesn't exist (also handles
    # the case where it was deleted entirely - it's simply recreated).
    dataset_folder = os.path.join(Config.DATASET_FOLDER, str(employee.id))
    os.makedirs(dataset_folder, exist_ok=True)

    current_count = _count_dataset_images(dataset_folder)
    recognizer = get_face_recognizer()
    is_trained = str(employee.id) in recognizer.known_face_ids

    # Hard stop once the required count is already on disk. This also
    # blocks re-capturing on top of an already-trained, still-intact
    # dataset. If the folder was reset by an admin, current_count will be
    # below required_count and this check simply won't trigger.
    if current_count >= required_count:
        employee.face_images_count = current_count
        db.session.commit()
        return jsonify({
            'success': False,
            'message': f'Capture limit reached ({current_count}/{required_count} images already saved).',
            'limit_reached': True,
            'count': current_count,
            'required': required_count,
            'is_trained': is_trained
        }), 409

    try:
        # Save image with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{timestamp}.jpg"
        file_path = os.path.join(dataset_folder, filename)
        file.save(file_path)

        # Recount from disk (not count + 1) to stay correct even under
        # concurrent uploads.
        new_count = _count_dataset_images(dataset_folder)
        employee.face_images_count = new_count
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Image saved successfully',
            'count': new_count,
            'required': required_count,
            'limit_reached': new_count >= required_count,
            'is_trained': is_trained
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    
@app.route("/test")
def test():
    raise Exception("TEST EXCEPTION")

# ===== MANAGER APPROVAL ROUTES =====

@app.route('/manager/approvals')
@login_required
def manager_approvals():
    """Manager approval dashboard - shows pending, approved, and rejected requests"""
    # Check if user is a manager
    if session.get('user_role') != 'employee':
        flash('Access denied. Managers only.', 'danger')
        return redirect(url_for('dashboard'))
    
    employee_id = session.get('employee_id')
    employee = Employee.query.get(employee_id)
    
    if not employee or employee.designation != 'Manager':
        flash('Access denied. You are not a manager.', 'danger')
        return redirect(url_for('employee_dashboard'))
    
    from services.approval_service import approval_service

    # Date filter: defaults to today, or the ?date=YYYY-MM-DD query param.
    selected_date = parse_approvals_filter_date()

    # Get all logout requests for this manager
    all_requests = approval_service.get_all_requests_for_manager(employee_id)

    from datetime import timedelta

    for approval_request in all_requests:
        if approval_request.created_at:
            approval_request.created_at_ist = (
                approval_request.created_at + timedelta(hours=5, minutes=30)
            )

    # Separate by status (no date filter - show all requests)
    pending_requests = [r for r in all_requests if r.status == 'pending']
    approved_requests = [r for r in all_requests if r.status == 'approved']
    rejected_requests = [r for r in all_requests if r.status == 'rejected']
    
    # Get pending manual attendance requests for this manager's department
    # No date filter: pending requests should be visible regardless of submission date
    pending_manual_attendance = approval_service.get_pending_manual_attendance_requests(manager_id=employee_id, admin_view=False)

    # Get already-approved/rejected manual attendance requests so rejection
    # remarks are visible in a history table on this dashboard.
    # No date filter: show all processed requests
    approved_manual_attendance, rejected_manual_attendance = approval_service.get_processed_manual_attendance_requests(
        manager_id=employee_id, admin_view=False
    )
    
    return render_template('manager_approvals.html',
                         pending_requests=pending_requests,
                         approved_requests=approved_requests,
                         rejected_requests=rejected_requests,
                         pending_manual_attendance=pending_manual_attendance,
                         approved_manual_attendance=approved_manual_attendance,
                         rejected_manual_attendance=rejected_manual_attendance,
                         manager=employee,
                         selected_date=selected_date,
                         today=date.today())

@app.route('/manager/approve-logout/<int:request_id>', methods=['POST'])
@login_required
def approve_logout_request(request_id):
    """Approve a logout approval request"""
    # Check if user is a manager
    if session.get('user_role') != 'employee':
        return jsonify({'success': False, 'message': 'Access denied. Managers only.'})
    
    employee_id = session.get('employee_id')
    employee = Employee.query.get(employee_id)
    
    if not employee or employee.designation != 'Manager':
        return jsonify({'success': False, 'message': 'Access denied. You are not a manager.'})
    
    from services.approval_service import approval_service
    
    result = approval_service.approve_logout_request(request_id, employee_id)
    
    if result['success']:
        logger.info(f"Logout request {request_id} approved by manager {employee.name}")
    
    return jsonify(result)

@app.route('/manager/reject-logout/<int:request_id>', methods=['POST'])
@login_required
def reject_logout_request(request_id):
    """
    Reject a logout approval request.
    Manager can only reject requests assigned to them.
    Rejected auto-logout attendance is marked Absent.
    """

    try:
        # ============================================================
        # VERIFY MANAGER LOGIN
        # ============================================================

        if session.get('user_role') != 'employee':
            return jsonify({
                'success': False,
                'message': 'Access denied. Managers only.'
            }), 403

        approver_id = session.get('employee_id')

        if not approver_id:
            return jsonify({
                'success': False,
                'message': 'Manager session not found.'
            }), 401

        # Verify logged-in employee is actually a Manager
        manager = Employee.query.get(approver_id)

        if not manager or manager.designation != 'Manager':
            return jsonify({
                'success': False,
                'message': 'Access denied. You are not a manager.'
            }), 403

        # ============================================================
        # GET REMARKS FROM REQUEST
        # ============================================================

        data = request.get_json(silent=True) or {}
        remarks = data.get('remarks')

        logger.info(
            f"MANAGER REJECT REQUEST - "
            f"Request ID: {request_id}, "
            f"Manager ID: {approver_id}"
        )

        # ============================================================
        # FIND APPROVAL REQUEST
        # ============================================================

        approval_request = LogoutApprovalRequest.query.get(request_id)

        if not approval_request:
            return jsonify({
                'success': False,
                'message': 'Approval request not found'
            }), 404

        # ============================================================
        # CHECK REQUEST STATUS
        # ============================================================

        if approval_request.status != 'pending':
            return jsonify({
                'success': False,
                'message': f'Request already {approval_request.status}'
            }), 400

        # ============================================================
        # VERIFY ASSIGNED MANAGER
        # ============================================================

        if approval_request.manager_id != approver_id:
            logger.warning(
                f"UNAUTHORIZED REJECT ATTEMPT - "
                f"Request ID: {request_id}, "
                f"Assigned Manager ID: {approval_request.manager_id}, "
                f"Attempted Manager ID: {approver_id}"
            )

            return jsonify({
                'success': False,
                'message': 'You are not authorized to reject this request'
            }), 403

        # ============================================================
        # REJECT AUTO LOGOUT
        # ============================================================

        approval_request.status = 'rejected'
        approval_request.approved_at = datetime.now()
        approval_request.approved_by = approver_id
        approval_request.remarks = remarks

        # ============================================================
        # GET RELATED ATTENDANCE
        # ============================================================

        attendance = Attendance.query.get(
            approval_request.attendance_id
        )

        if attendance:

            # Mark attendance as ABSENT
            attendance.status = 'Absent'

            # IMPORTANT:
            # DO NOT set OUT time
            # DO NOT calculate working hours
            # DO NOT modify IN time

            logger.info(
                f"Attendance marked ABSENT after logout rejection - "
                f"Attendance ID: {attendance.id}, "
                f"Employee ID: {attendance.employee_id}, "
                f"Date: {attendance.date}"
            )

        else:

            logger.warning(
                f"Attendance not found for rejected approval request - "
                f"Request ID: {request_id}, "
                f"Attendance ID: {approval_request.attendance_id}"
            )

        # ============================================================
        # COMMIT
        # ============================================================

        db.session.commit()

        logger.info(
            f"MANAGER REJECT SUCCESS - "
            f"Request ID: {request_id}, "
            f"Manager ID: {approver_id}, "
            f"Attendance ID: {approval_request.attendance_id}"
        )

        # ============================================================
        # RETURN JSON
        # ============================================================

        return jsonify({
            'success': True,
            'message': 'Logout request rejected and attendance marked as Absent'
        }), 200

    except Exception as e:

        db.session.rollback()

        logger.error(
            f"ERROR REJECTING LOGOUT REQUEST - "
            f"Request ID: {request_id}, "
            f"Error: {e}"
        )

        import traceback
        logger.error(traceback.format_exc())

        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

@app.route('/manual-attendance/approve/<int:attendance_id>', methods=['POST'])
@login_required
def approve_manual_attendance(attendance_id):
    """Approve a manual attendance request"""
    from services.approval_service import approval_service
    
    # Check if user is manager or admin
    user_role = session.get('user_role')
    if user_role not in ['employee', 'admin']:
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    approver_id = None
    if user_role == 'employee':
        approver_id = session.get('employee_id')
        manager = Employee.query.get(approver_id)
        if not manager or manager.designation != 'Manager':
            return jsonify({'success': False, 'message': 'Only managers can approve manual attendance'}), 403

        # A Manager's own manual attendance must ONLY be finalized by the
        # Admin - never by a manager (self or peer).
        target = Attendance.query.get(attendance_id)
        if target and target.employee and target.employee.designation == 'Manager':
            return jsonify({'success': False, 'message': "A Manager's manual attendance can only be approved by the Admin"}), 403
    elif user_role == 'admin':
        approver_id = session.get('admin_id')
    
    result = approval_service.approve_manual_attendance(attendance_id, approver_id)
    
    if result.get('success'):
        return jsonify(result), 200
    else:
        return jsonify(result), 400

@app.route('/manual-attendance/reject/<int:attendance_id>', methods=['POST'])
@login_required
def reject_manual_attendance(attendance_id):
    """Reject a manual attendance request"""
    from services.approval_service import approval_service
    
    # Check if user is manager or admin
    user_role = session.get('user_role')
    if user_role not in ['employee', 'admin']:
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    approver_id = None
    if user_role == 'employee':
        approver_id = session.get('employee_id')
        manager = Employee.query.get(approver_id)
        if not manager or manager.designation != 'Manager':
            return jsonify({'success': False, 'message': 'Only managers can reject manual attendance'}), 403

        # A Manager's own manual attendance must ONLY be finalized by the
        # Admin - never by a manager (self or peer).
        target = Attendance.query.get(attendance_id)
        if target and target.employee and target.employee.designation == 'Manager':
            return jsonify({'success': False, 'message': "A Manager's manual attendance can only be rejected by the Admin"}), 403
    elif user_role == 'admin':
        approver_id = session.get('admin_id')
    
    data = request.get_json(silent=True) or {}
    remarks = data.get('remarks', None)
    
    result = approval_service.reject_manual_attendance(attendance_id, approver_id, remarks)
    
    if result.get('success'):
        return jsonify(result), 200
    else:
        return jsonify(result), 400

@app.route('/manager/pending-manual-attendance')
@login_required
def manager_pending_manual_attendance():
    """Manager view for pending manual attendance requests"""
    # Check if user is a manager
    if session.get('user_role') != 'employee':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    
    employee_id = session.get('employee_id')
    manager = Employee.query.get(employee_id)
    
    if not manager or manager.designation != 'Manager':
        flash('Access denied. Only managers can view pending manual attendance.', 'danger')
        return redirect(url_for('dashboard'))
    
    from services.approval_service import approval_service
    pending_requests = approval_service.get_pending_manual_attendance_requests(manager_id=employee_id, admin_view=False)
    
    return render_template('manager_pending_manual_attendance.html', 
                          pending_requests=pending_requests,
                          manager=manager)

@app.route('/admin/pending-manual-attendance')
@login_required
@admin_required
def admin_pending_manual_attendance():
    """Admin view for all pending manual attendance requests"""
    from services.approval_service import approval_service
    pending_requests = approval_service.get_pending_manual_attendance_requests(admin_view=True)
    
    return render_template('admin_pending_manual_attendance.html', 
                          pending_requests=pending_requests)

@app.route('/manager/edit-attendance/<int:attendance_id>', methods=['GET', 'POST'])
@login_required
def manager_edit_attendance(attendance_id):
    """Manager attendance edit is DISABLED - Managers can only Approve/Reject requests"""
    # Check if user is a manager
    if session.get('user_role') != 'employee':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    
    employee_id = session.get('employee_id')
    manager = Employee.query.get(employee_id)
    
    if not manager or manager.designation != 'Manager':
        flash('Access denied.', 'danger')
        return redirect(url_for('employee_dashboard'))
    
    # BLOCK all manager attendance edit attempts
    logger.warning(f"[Manager Attendance Edit Blocked] Manager ID: {manager.id}, Attempted Attendance ID: {attendance_id}")
    flash('Managers can only Approve or Reject logout requests. Attendance editing is not permitted.', 'danger')
    return redirect(url_for('manager_approvals'))

@app.route('/admin/edit-attendance/<int:attendance_id>', methods=['GET', 'POST'])
@login_required
def admin_edit_attendance(attendance_id):
    """Admin can edit attendance time for any employee"""
    # Check if user is admin
    if session.get('user_role') != 'admin':
        flash('Access denied. Admins only.', 'danger')
        return redirect(url_for('dashboard'))
    
    attendance = Attendance.query.get(attendance_id)
    
    if not attendance:
        flash('Attendance record not found.', 'danger')
        return redirect(url_for('admin_approvals'))
    
    if request.method == 'POST':
        try:
            logger.info("ADMIN ATTENDANCE EDIT - POST Request")
            logger.info(f"Attendance ID: {attendance_id}")
            logger.info(f"Employee ID: {attendance.employee_id}")
            logger.info(f"Employee Name: {attendance.employee.name}")
            logger.info(f"Date: {attendance.date}")
            logger.info(f"OLD IN: {attendance.in_time}")
            logger.info(f"OLD OUT: {attendance.out_time}")
            logger.info(f"OLD STATUS: {attendance.status}")
            logger.info(f"OLD TOTAL HOURS: {attendance.total_hours}")
            
            # Get edited times
            in_time_str = request.form.get('in_time')
            out_time_str = request.form.get('out_time')
            
            logger.info(f"NEW IN (raw): {in_time_str}")
            logger.info(f"NEW OUT (raw): {out_time_str}")
            
            # Parse times
            if in_time_str:
                attendance.in_time = datetime.strptime(in_time_str, '%Y-%m-%dT%H:%M')
            
            if out_time_str:
                attendance.out_time = datetime.strptime(out_time_str, '%Y-%m-%dT%H:%M')
            else:
                attendance.out_time = None
            
            logger.info(f"NEW IN (parsed): {attendance.in_time}")
            logger.info(f"NEW OUT (parsed): {attendance.out_time}")
            
            # Skip AttendanceActivity sync when admin edits attendance
            # The calculation will use attendance.in_time and attendance.out_time directly
            # This preserves cross-day datetime information that AttendanceActivity cannot store
            # (AttendanceActivity only stores time + attendance_date, not full datetime)
            
            # Clear any rejected logout approval request when admin manually edits attendance
            # Admin's manual edit overrides the automatic rejection
            from models import LogoutApprovalRequest
            rejected_request = LogoutApprovalRequest.query.filter_by(
                attendance_id=attendance.id,
                status='rejected'
            ).first()
            if rejected_request:
                logger.info(f"Clearing rejected logout approval request ID: {rejected_request.id} for Attendance ID: {attendance.id}")
                db.session.delete(rejected_request)
                # Flush the deletion so has_rejected_approval doesn't find it during recalculation
                db.session.flush()
            
            # Recalculate attendance fields
            from attendance import AttendanceManager
            am = AttendanceManager()
            am.calculator.recalculate_attendance(attendance, is_final_calculation=True)
            
            logger.info(f"NEW STATUS: {attendance.status}")
            logger.info(f"NEW TOTAL HOURS: {attendance.total_hours}")
            logger.info(f"NEW OVERTIME HOURS: {attendance.overtime_hours}")
            logger.info(f"NEW LATE ENTRY: {attendance.late_entry}")
            
            attendance.updated_at = datetime.utcnow()
            db.session.commit()
            
            logger.info("DATABASE COMMIT SUCCESS")
            logger.info(f"Attendance {attendance_id} edited by admin")
            flash('Attendance updated successfully!', 'success')
            return redirect(url_for('admin_approvals'))
            
        except Exception as e:
            logger.error(f"Error editing attendance: {e}")
            import traceback
            logger.error(traceback.format_exc())
            flash(f'Error updating attendance: {str(e)}', 'danger')
    
    return render_template('admin_edit_attendance.html', attendance=attendance)

@app.route('/admin/approvals')
@login_required
def admin_approvals():
    """Admin approval dashboard - shows manager approval requests and employee approval history"""
    # Check if user is admin
    if session.get('user_role') != 'admin':
        flash('Access denied. Admins only.', 'danger')
        return redirect(url_for('dashboard'))
    
    from services.approval_service import approval_service
    
    # Date filter: defaults to today, or the ?date=YYYY-MM-DD query param.
    selected_date = parse_approvals_filter_date()

    # Get all requests across all managers/departments
    all_requests = approval_service.get_all_requests_for_admin()

    from datetime import timedelta

    for approval_request in all_requests:
        if approval_request.created_at:
            approval_request.created_at_ist = (
                approval_request.created_at + timedelta(hours=5, minutes=30)
            )

    # Filter to show ONLY Manager requests in top sections (employee.designation == 'Manager')
    # No date filter: show all manager requests
    manager_requests = [r for r in all_requests if r.employee.designation == 'Manager']
    
    # Separate by status (only Manager requests)
    pending_requests = [r for r in manager_requests if r.status == 'pending']
    approved_requests = [r for r in manager_requests if r.status == 'approved']
    rejected_requests = [r for r in manager_requests if r.status == 'rejected']
    
    # Get employee approval history (all approval requests for normal employees)
    # This shows which manager handled which employee's approval
    # No date filter: show all employee approval history
    employee_approval_history = [r for r in all_requests if r.employee.designation != 'Manager']
    
    # ============================================================
    # REQUIREMENT 2: ADMIN APPROVAL HISTORY
    # Combined approval history showing ALL completed actions
    # (approved/rejected) across both managers and employees.
    # This is displayed in the 'Approval History' section below.
    # No date filter: show all approval history
    # ============================================================
    approval_history = [r for r in all_requests if r.status in ('approved', 'rejected')]
    
    # Get attendance records for ALL active employees, across ALL dates
    # This is for the "Attendance Records (All Employees)" section
    
    # Get all active employees (not just managers)
    all_active_employees = Employee.query.filter_by(status='active').all()
    all_employee_ids = [emp.id for emp in all_active_employees]
    
    # Get attendance records for all active employees across all dates (no date filter)
    week_attendance = Attendance.query.filter(
        Attendance.employee_id.in_(all_employee_ids)
    ).order_by(Attendance.date.desc(), Attendance.employee_id).all()
    
    # Add display_out_time for UI
    am, _, _, _ = get_services()
    for att in week_attendance:
        am._add_display_out_time(att, att.date)
    
    # Get pending manual attendance requests for admin view (all departments)
    # No date filter: pending requests should be visible regardless of submission date
    pending_manual_attendance = approval_service.get_pending_manual_attendance_requests(admin_view=True)

    # Get already-approved/rejected manual attendance requests (all departments)
    # so rejection remarks are visible in a history table on this dashboard.
    # No date filter: show all processed requests
    approved_manual_attendance, rejected_manual_attendance = approval_service.get_processed_manual_attendance_requests(
        admin_view=True
    )
    
    return render_template('admin_approvals.html',
                         pending_requests=pending_requests,
                         approved_requests=approved_requests,
                         rejected_requests=rejected_requests,
                         employee_approval_history=employee_approval_history,
                         approval_history=approval_history,
                         week_attendance=week_attendance,
                         pending_manual_attendance=pending_manual_attendance,
                         approved_manual_attendance=approved_manual_attendance,
                         rejected_manual_attendance=rejected_manual_attendance,
                         selected_date=selected_date,
                         today=date.today())

@app.route('/admin/approve-logout/<int:request_id>', methods=['POST'])
@login_required
def admin_approve_logout_request(request_id):
    """Approve a logout approval request (Admin only)"""
    # Check if user is admin
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'message': 'Access denied. Admins only.'})
    
    from services.approval_service import approval_service
    
    result = approval_service.approve_logout_request_admin(request_id, session.get('admin_id'))
    
    if result['success']:
        logger.info(f"Logout request {request_id} approved by admin")
    
    return jsonify(result)

# DEVELOPMENT ONLY: Manual trigger for daily approval requests (requires admin login)
@app.route('/admin/dev/trigger-approval-requests', methods=['POST'])
@login_required
def dev_trigger_approval_requests():
    """
    DEVELOPMENT ONLY: Manually trigger daily approval request creation
    This allows testing the approval workflow without waiting for 23:59
    Requires admin login and session.
    """
    # Check if user is admin
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'message': 'Access denied. Admins only.'})
    
    logger.warning("DEVELOPMENT: Manual approval request trigger initiated by admin")
    
    from services.approval_service import approval_service
    result = approval_service.create_daily_approval_requests()
    
    logger.warning(f"DEVELOPMENT: Manual trigger completed - {result}")
    
    return jsonify({
        'success': True,
        'message': f"Approval request creation triggered manually. {result['count']} requests created for {result['date']}",
        'result': result
    })

# DEVELOPMENT ONLY: Manual trigger for daily approval requests (no login required - terminal testing)
@app.route('/admin/dev/trigger-approval-requests-test', methods=['POST'])
def dev_trigger_approval_requests_test():
    """
    DEVELOPMENT ONLY: Manually trigger daily approval request creation without login
    This allows testing the approval workflow from terminal without browser session
    Protected by Flask debug mode check only - DO NOT use in production.
    """
    import os
    
    # Development-only protection: only allow in Flask debug mode
    if not app.debug:
        logger.error("SECURITY: Attempted to access dev test route in non-debug mode")
        return jsonify({'success': False, 'message': 'Development route only available in debug mode'}), 403
    
    logger.warning("DEVELOPMENT TEST ROUTE: Manual approval request trigger initiated (no auth)")
    
    from services.approval_service import approval_service
    result = approval_service.create_daily_approval_requests()
    
    logger.warning(f"DEVELOPMENT TEST ROUTE: Manual trigger completed - {result}")
    
    return jsonify({
        'success': True,
        'message': f"Approval request creation triggered manually. {result['count']} requests created for {result['date']}",
        'count': result['count'],
        'date': str(result['date'])
    })

@app.route('/admin/reject-logout/<int:request_id>', methods=['POST'])
@login_required
def admin_reject_logout_request(request_id):
    """Reject a logout approval request (Admin only)"""
    # Check if user is admin
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'message': 'Access denied. Admins only.'})
    
    from services.approval_service import approval_service
    
    remarks = request.form.get('remarks')
    result = approval_service.reject_logout_request_admin(request_id, session.get('admin_id'), remarks)
    
    if result['success']:
        logger.info(f"Logout request {request_id} rejected by admin")
    
    return jsonify(result)

@app.route('/api/train-face-model', methods=['POST'])
@login_required
def train_face_model():
    """API endpoint to train the face recognition model for an employee.
    Re-validates the live image count on disk before training so this can
    never be triggered against a dataset that was emptied/deleted after the
    capture loop finished."""
    employee_id = request.form.get('employee_id')

    if not employee_id:
        return jsonify({'success': False, 'message': 'Employee ID required'}), 400

    try:
        employee = Employee.query.filter_by(id=int(employee_id)).first()
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'Invalid employee ID'}), 400

    if not employee:
        return jsonify({'success': False, 'message': 'Employee not found'}), 404

    required_count = _get_required_face_image_count()

    try:
        # Get image paths - always read fresh from disk.
        dataset_folder = os.path.join(Config.DATASET_FOLDER, str(employee.id))
        image_paths = [os.path.join(dataset_folder, f) for f in os.listdir(dataset_folder)
                      if f.lower().endswith(('.jpg', '.jpeg', '.png'))] if os.path.exists(dataset_folder) else []

        # Keep the cached DB counter honest too.
        employee.face_images_count = len(image_paths)
        db.session.commit()

        if not image_paths:
            return jsonify({
                'success': False,
                'message': 'No face images found for this employee',
                'count': 0,
                'required': required_count
            }), 400

        if len(image_paths) < required_count:
            return jsonify({
                'success': False,
                'message': f'Need at least {required_count} images, found {len(image_paths)}',
                'count': len(image_paths),
                'required': required_count
            }), 400

        # Train the model using DeepFace with global instance
        recognizer = get_face_recognizer()
        trained_count = recognizer.train_employee(str(employee.id), employee.name, image_paths)

        if trained_count > 0:
            return jsonify({
                'success': True,
                'message': f'Successfully trained with {trained_count} face encodings',
                'count': trained_count,
                'image_count': len(image_paths),
                'required': required_count,
                'is_trained': True
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Training failed - no faces detected in images. Please ensure: 1) Face is clearly visible, 2) Good lighting, 3) Images are not blurry, 4) Try capturing new images with better conditions',
                'count': len(image_paths),
                'required': required_count,
                'is_trained': False
            }), 422

    except Exception as e:
        return jsonify({'success': False, 'message': f'Training error: {str(e)}'}), 500

# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found(error):
    return render_template('login.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('login.html'), 500


if __name__ == '__main__':
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

    print("\n" + "=" * 50)
    print(f"🚀 Attendance & Payroll System")
    print(f"   Running at: {SERVER_URL}")
    print(f"   Host: {SERVER_HOST}  |  Port: {SERVER_PORT}")
    print("=" * 50 + "\n")

    app.run(host=SERVER_HOST, port=SERVER_PORT, debug=True, use_reloader=False)

