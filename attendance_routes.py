"""
Attendance routes: everything camera / face-recognition / attendance
related, plus the dashboards, kiosk landing page, employee self-service
attendance + profile pages, and the protected file-serving routes for
uploads and the (encrypted) face dataset.

Eighth and FINAL blueprint migrated out of app.py. After this migration
app.py is only the Flask application entry point: app creation and
configuration, extension initialization, blueprint registration, the
month_name template filter, error handlers, and the __main__ startup
block. Every route now lives in a blueprint.

Everything here was moved verbatim from app.py - no behavior changes.
The only adaptations required by moving out of the module that owns the
Flask `app` object:
  - @app.route(...)      -> @attendance_bp.route(...)
  - app.logger           -> current_app.logger  (test_log only)
  - url_for('<route>')   -> url_for('attendance.<route>') for the routes
                            that moved here (see endpoint naming below)

Shared collaborators are imported from already-neutral modules
(ai_engine.py, face_recognition_singleton.py, services/app_services.py,
services/attendance_stats.py, auth_decorators.py, models.py, ...), never
from app.py, so there is no circular import.

Camera / OpenCV: cv2 is imported with the same graceful-degradation
pattern app.py used (cv2 = None when OpenCV isn't installed), and is only
touched inside route bodies, never at import time.

Presence trackers / background state:
  - ai_engine.presence_tracker (admin kiosk auto-scan) is imported from
    ai_engine.py exactly as before.
  - EmployeeAttendancePresenceTracker (employee-login stream) moved here
    together with its single shared instance, since /api/employee-attendance
    is its only user. It is a process-wide singleton created at import
    time, exactly as it was in app.py (threading.Lock + monotonic clock).

Datetime handling: deliberately untouched. attendance.py / ai_engine.py
are unchanged, so the test suite's frozen_weekday_datetime fixture (which
patches attendance.py's own `datetime` name) keeps working. The
datetime.now() calls in these routes are moved verbatim; that fixture
never touched app.py's namespace either.

IMPORTANT - endpoint naming: this Blueprint's endpoints are
"attendance.<function_name>" (e.g. url_for('attendance.dashboard'),
url_for('attendance.employee_dashboard'), url_for('attendance.index')).
Every url_for() referencing these 25 routes, in Python and templates,
was updated project-wide.
"""
import os
import io
import logging
import threading
import time as time_module
from datetime import datetime, date, timedelta

from flask import (
    Blueprint, render_template, request, redirect, url_for, session,
    jsonify, send_file, send_from_directory, flash, current_app,
)
from werkzeug.utils import secure_filename, safe_join

try:
    import cv2
except ImportError:
    # Same graceful-degradation pattern as ai_engine.py / the old app.py:
    # cv2 is only ever touched inside specific route bodies (webcam frame
    # capture/decoding), never at import time, so a missing OpenCV install
    # does not prevent unrelated routes from loading (and lets the test
    # suite import the app without the full OpenCV/ML stack installed).
    cv2 = None
import numpy as np

import crypto_utils
from auth_decorators import login_required, admin_required, employee_required
from config import Config
from database import db
from models import (
    Employee, Attendance, Payroll, Settings, EmployeeLogin,
    AttendanceActivity, LogoutApprovalRequest, now_ist,
)
from ai_engine import FaceCapture, train_all_employees, presence_tracker
from face_recognition_singleton import get_face_recognizer
from services.app_services import get_services
from services.attendance_stats import get_effective_report_status

logger = logging.getLogger(__name__)

attendance_bp = Blueprint('attendance', __name__)


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


# ============================================================
# PROTECTED FILE SERVING (UPLOADS / FACE DATASET)
# ============================================================

@attendance_bp.route('/uploads/<path:filename>')
@login_required
def serve_upload(filename):
    """
    Serve files from the uploads folder (profile photos and payslips).

    Security:
      - login_required: no anonymous access at all (previously this route
        had NO auth check, so payslips - which contain salary data - and
        profile photos were downloadable by anyone who could guess/enumerate
        a filename).
      - Employees may only fetch files that are theirs (filename contains
        their own employee_id, matching how payslip_<employee_id>_...pdf and
        <employee_id>_<photo> filenames are generated elsewhere in this
        file). Admins may access any file in uploads/.
      - Only the basename is ever passed to the filesystem, and it is
        resolved via send_from_directory() (Werkzeug's safe_join), so a
        filename containing '..' or an absolute path cannot escape the
        uploads directory - the previous os.path.join()+send_file()
        combination had no such protection.
    """
    uploads_folder = Config.UPLOAD_FOLDER
    filename_only = os.path.basename(filename)

    if 'admin_id' not in session:
        employee = db.session.get(Employee, session.get('employee_id'))
        if not employee or employee.employee_id not in filename_only:
            flash('Access Denied. You can only view your own files.', 'danger')
            return redirect(url_for('attendance.employee_dashboard'))

    def _exists(name):
        return os.path.isfile(os.path.join(uploads_folder, name))

    resolved_name = filename_only if _exists(filename_only) else None

    # Fallback lookups preserved from the original implementation, e.g. for
    # payslips saved with a swapped month/year naming convention
    # (payslip_EMP0001_8_2026.pdf vs payslip_EMP0001_2026_8.pdf).
    if resolved_name is None and 'payslip' in filename_only:
        parts = filename_only.replace('payslip_', '').replace('.pdf', '').split('_')
        if len(parts) == 3:
            employee_id, month, year = parts
            alt_filename = secure_filename(f"payslip_{employee_id}_{year}_{month}.pdf")
            if _exists(alt_filename):
                resolved_name = alt_filename

    if resolved_name is None:
        # Preserve original filename for the 404 Werkzeug will raise - it
        # still resolves safely (no traversal) even though it won't exist.
        resolved_name = filename_only

    return send_from_directory(uploads_folder, resolved_name)


@attendance_bp.route('/dataset/<path:filename>')
@admin_required
def serve_dataset(filename):
    """
    Serve face-image thumbnails from the dataset folder.

    Only used by the admin "manage employee" screen (add_employee.html),
    so this is admin-only rather than login_required - there's no reason
    for an ordinary employee login to browse other employees' biometric
    photos.

    Dataset images are encrypted at rest (see FaceCapture.capture_frame in
    ai_engine.py / crypto_utils.py), so this can no longer stream the file
    straight off disk with send_from_directory() - it has to decrypt into
    memory first. safe_join() is still used to resolve the (possibly
    nested, e.g. '<employee_id>/<image>.jpg') path against dataset_folder
    and reject any attempt to traverse outside of it, preserving the same
    traversal protection send_from_directory() provided.
    """
    dataset_folder = Config.DATASET_FOLDER
    safe_path = safe_join(dataset_folder, filename)
    if safe_path is None or not os.path.isfile(safe_path):
        return jsonify({'error': 'Not found'}), 404

    with open(safe_path, 'rb') as f:
        raw = f.read()

    if crypto_utils.is_encrypted(raw):
        try:
            raw = crypto_utils.decrypt_bytes(raw)
        except Exception:
            logger.error(f"Failed to decrypt dataset image: {safe_path}")
            return jsonify({'error': 'Could not decrypt image'}), 500

    return send_file(io.BytesIO(raw), mimetype='image/jpeg')




# ============================================================
# LANDING PAGE / DIAGNOSTICS
# ============================================================

@attendance_bp.route('/')
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


@attendance_bp.route("/test-log")
def test_log():
    current_app.logger.info("APP LOGGER WORKING")
    return "OK"




# ============================================================
# DASHBOARDS
# ============================================================

@attendance_bp.route('/dashboard')
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
            ).filter(AttendanceActivity.hidden_by_admin.isnot(True)).order_by(AttendanceActivity.activity_time).all()
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


@attendance_bp.route('/employee-dashboard')
@login_required
@employee_required
def employee_dashboard():
    """Employee dashboard showing only their own information"""
    employee_id = session['employee_id']
    employee = db.session.get(Employee, employee_id)
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
    
    # Get attendance summary for this employee for the CURRENT CALENDAR MONTH
    # (1st of the month up to yesterday). This matches payroll/reports, which
    # are monthly. Future days are never counted.
    # Use centralized attendance calculation to include generated absent records
    month_start = today.replace(day=1)
    month_label = today.strftime('%b %Y').upper()
    current_date = month_start
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
        # If both IN and OUT are set (admin edit), use database values - don't recalculate
        if att.in_time and att.date < today:
            if not (att.in_time and att.out_time):
                # Only recalculate if using activities (not admin-edited)
                am.calculator.recalculate_attendance(att, is_final_calculation=True, use_activities=True)
        # Add display_out_time for UI (show "-" after new IN until next OUT)
        am._add_display_out_time(att, att.date)
        # logger.info(f"  Display OUT Time after _add_display_out_time: {att.display_out_time if hasattr(att, 'display_out_time') else 'N/A'}")
    
    # Get today's activities for this employee
    today_activities = AttendanceActivity.query.filter_by(
        employee_id=employee_id,
        attendance_date=today
    ).filter(AttendanceActivity.hidden_by_admin.isnot(True)).order_by(AttendanceActivity.activity_time).all()
    
    return render_template('employee_dashboard.html',
                         employee=employee,
                         current_user=current_user,
                         today=today,
                         today_attendance=today_attendance,
                         present_days=present_days,
                         absent_days=absent_days,
                         half_days=half_days,
                         late_days=late_days,
                         month_label=month_label,
                         recent_attendance=recent_attendance,
                         today_activities=today_activities)




# ============================================================
# FACE REGISTRATION / CAPTURE / TRAINING (ADMIN)
# ============================================================

@attendance_bp.route('/face-registration/<int:id>')
@login_required
@admin_required
def face_registration(id):
    employee = Employee.query.get_or_404(id)
    employees = Employee.query.filter_by(status='active').order_by(Employee.created_at.desc()).all()
    
    # Get minimum face images required from Settings
    settings = Settings.get_settings()
    min_face_images = settings.min_face_images_required if settings else 20
    
    # Calculate face image counts for all employees
    dataset_folder = Config.DATASET_FOLDER
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
                         employee_image_counts=employee_image_counts,
                         biometric_consent_given=employee.biometric_consent_given)


@attendance_bp.route('/capture-face/<int:id>', methods=['POST'])
@login_required
@admin_required
def capture_face(id):
    employee = Employee.query.get_or_404(id)
    
    # Biometric consent gate: do not capture or store any face data for an
    # employee who has not (or no longer) given consent. This must be
    # checked before any camera access or file writes happen below.
    if not employee.biometric_consent_given:
        flash(f'Cannot capture face images: {employee.name} has not given biometric consent.', 'danger')
        return redirect(url_for('attendance.face_registration', id=id))
    
    # Get minimum face images required from Settings
    settings = Settings.get_settings()
    min_face_images = settings.min_face_images_required if settings else 20
    
    # Calculate current face image count from dataset folder
    dataset_folder = Config.DATASET_FOLDER
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
        return redirect(url_for('employees.view_employee', id=id))
    
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
        return redirect(url_for('employees.view_employee', id=id))
    
    except Exception as e:
        capture.stop_capture()
        flash(f'Error capturing faces: {str(e)}', 'danger')
        return redirect(url_for('attendance.face_registration', id=id))


@attendance_bp.route('/train-ai')
@login_required
@admin_required
def train_ai():
    try:
        train_all_employees()
        flash('AI model trained successfully for all employees', 'success')
    except Exception as e:
        flash(f'Error training AI: {str(e)}', 'danger')
    
    # The button lives on the Settings page, which shows (and auto-hides) the message.
    # (The dashboard does not render flash messages, so the text used to leak onto a later page.)
    return redirect(url_for('settings.settings'))


@attendance_bp.route('/delete-face-image/<int:employee_id>/<string:image_name>', methods=['POST'])
@login_required
@admin_required
def delete_face_image(employee_id, image_name):
    """Delete a single face image for an employee"""
    employee = Employee.query.get_or_404(employee_id)
    
    # Secure the filename
    image_name = secure_filename(image_name)
    
    # Construct the full path to the image
    dataset_folder = Config.DATASET_FOLDER
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




# ============================================================
# ADMIN ATTENDANCE PAGES
# ============================================================

@attendance_bp.route('/attendance')
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


@attendance_bp.route('/attendance/mark', methods=['POST'])
@login_required
@admin_required
def mark_attendance():
    # Admin-only: this endpoint trusts the employee_id/confidence it is sent, so it
    # must never be callable by an employee session (they could mark a colleague
    # present without any face check). Employees use /api/employee-attendance.
    employee_id = int(request.form.get('employee_id'))
    confidence = float(request.form.get('confidence', 0.0)) if request.form.get('confidence') else None
    
    am, _, _, _ = get_services()
    result = am.mark_attendance(employee_id, confidence)
    
    return jsonify(result)


@attendance_bp.route('/attendance/history')
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
        # Recalculate total_hours/status/overtime/late from the fixed,
        # break-aware calculator before display. Without this, the table
        # shows whatever total_hours happened to be stored on the row -
        # which for any record computed before the calculator fix (or
        # edited directly) can be the old gross first-IN-to-last-OUT
        # figure instead of the correct net active time.
        # If both IN and OUT are set (admin edit), use database values - don't recalculate
        if att.in_time and att.date < today:
            if not (att.in_time and att.out_time):
                # Only recalculate if using activities (not admin-edited)
                am.calculator.recalculate_attendance(att, is_final_calculation=True, use_activities=True)
        # Add display_out_time for UI (show "-" after new IN until next OUT)
        am._add_display_out_time(att, att.date)
    
    employees = Employee.query.filter_by(status='active').all()
    
    return render_template('attendance.html', attendances=attendances, employees=employees, today=today)




# ============================================================
# EMPLOYEE SELF-SERVICE (ATTENDANCE + PROFILE)
# ============================================================

@attendance_bp.route('/employee-attendance')
@login_required
@employee_required
def employee_attendance():
    """Employee attendance page showing only their own attendance with check-in/out functionality"""
    employee_id = session['employee_id']
    employee = db.session.get(Employee, employee_id)
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
        # If both IN and OUT are set (admin edit), use database values - don't recalculate
        if att.in_time and att.date < today:
            if not (att.in_time and att.out_time):
                # Only recalculate if using activities (not admin-edited)
                am.calculator.recalculate_attendance(att, is_final_calculation=True, use_activities=True)
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


@attendance_bp.route('/employee-profile', methods=['GET', 'POST'])
@login_required
def employee_profile():
    """Employee profile page - view and edit profile"""
    if 'employee_id' not in session:
        return redirect(url_for('auth.login'))
    
    employee_id = session['employee_id']
    employee = db.session.get(Employee, employee_id)
    login_creds = EmployeeLogin.query.filter_by(employee_id=employee_id).first()
    current_user = employee
    
    if not employee:
        flash('Employee not found', 'danger')
        return redirect(url_for('auth.login'))
    
    edit_error = None
    form_phone = None
    form_email = None

    if request.method == 'POST':
        # Handle profile editing
        phone = (request.form.get('phone') or '').strip()
        email = (request.form.get('email') or '').strip()
        form_phone, form_email = phone, email

        # Validation
        if not phone or not email:
            edit_error = 'Phone and Email are required'
        elif len(phone) < 10:
            edit_error = 'Phone number must be at least 10 digits'
        elif '@' not in email or '.' not in email:
            edit_error = 'Invalid email format'
        elif Employee.query.filter(Employee.phone == phone, Employee.id != employee.id).first():
            edit_error = 'This Mobile Number already exists.'
        elif Employee.query.filter(db.func.lower(Employee.email) == email.lower(),
                                   Employee.id != employee.id).first():
            edit_error = 'This Email ID already exists.'
        else:
            # Update employee information
            employee.phone = phone
            employee.email = email
            employee.updated_at = now_ist()
            db.session.commit()
            flash('Profile updated successfully', 'success')
            return redirect(url_for('attendance.employee_profile'))

    # On a validation error the page is re-rendered (not redirected) so the
    # Edit Profile modal re-opens with what the employee typed and the error
    # shown inside it.
    return render_template('employee_profile.html', employee=employee, current_user=current_user,
                           login_creds=login_creds, edit_error=edit_error,
                           form_phone=form_phone, form_email=form_email)




# ============================================================
# ATTENDANCE / FACE RECOGNITION APIs
# ============================================================

@attendance_bp.route('/api/employee-attendance', methods=['POST'])
@login_required
@employee_required
def employee_attendance_api():
    """API endpoint for employee to mark their own attendance using face recognition"""
    employee_id = session['employee_id']
    employee = db.session.get(Employee, employee_id)

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


@attendance_bp.route('/api/recognize-face', methods=['POST'])
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

                employee = db.session.get(Employee, results[0]["employee_id"])

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


@attendance_bp.route('/api/auto-scan-attendance', methods=['POST'])
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

    # Periodic housekeeping - cheap, and keeps the trackers' memory bounded.
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
        employee = db.session.get(Employee, employee_id_int)
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


@attendance_bp.route('/api/verify-employee-id/<employee_id>', methods=['GET'])
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


@attendance_bp.route('/api/mark-attendance', methods=['POST'])
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


@attendance_bp.route('/mark_manual_attendance', methods=['POST'])
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




# ============================================================
# FACE DATASET HELPERS + APIs
# ============================================================

# Face-image upload validation (see upload_face_image).
_MAX_FACE_IMAGE_BYTES = 10 * 1024 * 1024
_JPEG_MAGIC = b'\xff\xd8\xff'
_PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


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


@attendance_bp.route('/api/face-dataset-status/<int:employee_id>', methods=['GET'])
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


@attendance_bp.route('/api/upload-face-image', methods=['POST'])
@login_required
def upload_face_image():
    """API endpoint to upload a single face image into an employee's
    training dataset. Re-checks the live image count on disk on every
    request (rather than trusting a cached DB value or the frontend), so a
    manually cleared dataset folder is picked up immediately and a full
    dataset can't be exceeded by a stray/duplicate request.

    Security / compliance guarantees:
      * Biometric consent is a strict opt-in - 403 (and nothing written) if
        the employee has not consented or has withdrawn consent.
      * Only genuine JPEG/PNG content is accepted (magic-byte check).
      * Images are encrypted at rest before touching disk and written
        atomically (crypto_utils.write_encrypted_file); serve_dataset and
        the other readers decrypt transparently."""
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

    # Biometric consent gate (strict opt-in). Face data must never be stored
    # for an employee whose consent has not been recorded, or has been
    # withdrawn. This runs BEFORE anything is read from the upload or
    # written to disk, so a refused request creates no file and no folder.
    if not employee.biometric_consent_given:
        return jsonify({
            'success': False,
            'consent_required': True,
            'message': f'Biometric consent has not been given by {employee.name}. '
                       'Record consent before uploading face images.'
        }), 403

    # Validate the upload itself before touching the filesystem: it must be
    # non-empty, within the size limit, and start with a real JPEG or PNG
    # signature (magic bytes - the client-supplied filename / content type
    # is not trusted). The bytes are held in memory only.
    image_bytes = file.read(_MAX_FACE_IMAGE_BYTES + 1)
    if not image_bytes:
        return jsonify({'success': False, 'message': 'Uploaded image is empty'}), 400
    if len(image_bytes) > _MAX_FACE_IMAGE_BYTES:
        return jsonify({
            'success': False,
            'message': f'Image is too large (limit {_MAX_FACE_IMAGE_BYTES // (1024 * 1024)} MB)'
        }), 413
    if not (image_bytes.startswith(_JPEG_MAGIC) or image_bytes.startswith(_PNG_MAGIC)):
        return jsonify({
            'success': False,
            'message': 'Unsupported file type. Only JPEG and PNG images are accepted.'
        }), 400

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
        # Save image with timestamp - same "<timestamp>.jpg" naming as the
        # live-capture path (FaceCapture.capture_frame), whatever the
        # uploaded format was, so every reader treats these files alike.
        # The image is encrypted (Fernet, via crypto_utils) BEFORE it
        # touches disk and written atomically: there is no plaintext
        # fallback, and a failure leaves no partial or temp file behind.
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{timestamp}.jpg"
        file_path = os.path.join(dataset_folder, filename)
        crypto_utils.write_encrypted_file(file_path, image_bytes)

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




# ============================================================
# DIAGNOSTICS / MODEL TRAINING API
# ============================================================

@attendance_bp.route("/test")
def test():
    raise Exception("TEST EXCEPTION")


@attendance_bp.route('/api/train-face-model', methods=['POST'])
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