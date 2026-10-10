"""
Settings routes: system/company settings (with email-connection test),
recording an employee's biometric consent decision, and the admin
full-data backup (.zip) download.

Sixth blueprint migrated out of app.py.

record_biometric_consent() is grouped here rather than in employees.py
(the existing employee-CRUD blueprint) because it's a compliance/consent
action on an existing employee record, not part of employee
creation/editing - it's invoked from the Settings-adjacent biometric
consent UI trigger, independently of the add/edit employee forms.

Every route here was moved verbatim from app.py - no behavior changes,
with two adaptations required to remove the app.py dependency:
- app.config['UPLOAD_FOLDER']/['DATASET_FOLDER'] equivalents now come
  from Config.UPLOAD_FOLDER / Config.DATASET_FOLDER directly (imported
  from config.py, exactly as app.py itself did for admin_backup - no
  behavior change, just going straight to the source app.py's own
  app.config values are populated from at startup).
- BASE_DIR is imported directly from config.py rather than from app.py.

IMPORTANT - endpoint naming: this Blueprint's endpoints are
"settings.<function_name>". url_for('settings') (used inside the
settings() route's own POST-redirect, and in auth_routes.py's
create_admin()) has been updated to url_for('settings.settings')
everywhere it appeared.
"""
import os
import io
import zipfile

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, session, current_app

from config import Config, BASE_DIR
from database import db
from models import Settings, Employee, AttendanceSettingsHistory, now_ist
from auth_decorators import login_required, admin_required
from file_helpers import allowed_file
from services.app_services import get_services
from licensing.license_manager import get_license_manager
from backup_manager import write_backup_zip

settings_bp = Blueprint('settings', __name__)


def _mask_email(address):
    """lekawale@gmail.com -> l***@gmail.com (never show the full address or any password)."""
    address = (address or '').strip()
    if '@' not in address:
        return address[:1] + '***' if address else ''
    local, domain = address.split('@', 1)
    return f"{local[:1]}***@{domain}"


def _email_config_status(settings):
    """Which email settings are active right now (same priority as EmailService)."""
    if settings.mail_username and settings.mail_password_enc:
        return {'configured': True, 'source': 'Admin Settings',
                'user': _mask_email(settings.mail_username)}
    if Config.MAIL_USERNAME and Config.MAIL_PASSWORD:
        return {'configured': True, 'source': '.env file',
                'user': _mask_email(Config.MAIL_USERNAME)}
    return {'configured': False, 'source': None, 'user': ''}


# ==================== SYSTEM / COMPANY SETTINGS ====================

@settings_bp.route('/settings', methods=['GET', 'POST'])
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
                try:
                    from werkzeug.utils import secure_filename
                    filename = secure_filename(f"company_logo_{file.filename}")
                    # Use BASE_DIR to ensure path is absolute and persists across restarts
                    logo_dir = os.path.join(BASE_DIR, 'static', 'images')
                    os.makedirs(logo_dir, exist_ok=True)
                    logo_path = os.path.join(logo_dir, filename)
                    file.save(logo_path)
                    settings.company_logo = f"static/images/{filename}"
                except Exception as e:
                    current_app.logger.error(f"Failed to save company logo: {e}")
                    flash('Failed to save company logo. Please try again.', 'danger')

        db.session.commit()

        # Create AttendanceSettingsHistory record for historical accuracy
        # This ensures old attendance records use old settings, new records use new settings

        # Check if history already exists for this exact timestamp.
        # BUG FIX: this timestamp is recorded in IST via now_ist() - same
        # convention as every other timestamp in this app (created_at,
        # updated_at, etc.) - rather than the naive server-local
        # datetime.now() this route used previously.
        current_timestamp = now_ist()
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
            current_app.logger.info(f"ATTENDANCE SETTINGS HISTORY CREATED - Effective From: {current_timestamp}")

        flash("Settings updated successfully", "success")
        return redirect(url_for("settings.settings"))

    # Test email connection: show the result as a normal flash message (dismissible,
    # auto-hides) and redirect, so refreshing the page does not run the test again.
    if request.args.get('test_email'):
        _, _, es, _ = get_services()
        result = es.test_email_connection()
        flash(result['message'] if not result['success'] else 'Email connection successful',
              'success' if result['success'] else 'danger')
        return redirect(url_for('settings.settings'))

    email_test = None
    return render_template('settings.html', settings=settings, email_test=email_test,
                           mail_status=_email_config_status(settings))


# ==================== EMAIL (SMTP) SETTINGS ====================

@settings_bp.route('/settings/email', methods=['POST'])
@login_required
@admin_required
def update_email_settings():
    """Save the SMTP settings entered in Admin Settings (password stored encrypted)."""
    from crypto_utils import encrypt_str

    settings = Settings.get_settings()

    server = (request.form.get('mail_server') or '').strip()
    port_raw = (request.form.get('mail_port') or '').strip()
    use_tls = request.form.get('mail_use_tls') == 'on'
    username = (request.form.get('mail_username') or '').strip()
    password = request.form.get('mail_password') or ''
    sender = (request.form.get('mail_default_sender') or '').strip()

    # Empty username = remove the saved email settings (the .env values are used again).
    if not username:
        settings.mail_server = None
        settings.mail_port = None
        settings.mail_use_tls = True
        settings.mail_username = None
        settings.mail_password_enc = None
        settings.mail_default_sender = None
        db.session.commit()
        flash('Email settings removed from Admin Settings. The .env values (if any) will be used.', 'success')
        return redirect(url_for('settings.settings'))

    if '@' not in username or '.' not in username:
        flash('Enter your full email address in "Email / Username" (e.g. name@gmail.com).', 'danger')
        return redirect(url_for('settings.settings'))
    if sender and '@' not in sender:
        flash('"Sender Email" must be a valid email address.', 'danger')
        return redirect(url_for('settings.settings'))

    try:
        port = int(port_raw) if port_raw else 587
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        flash('SMTP port must be a number between 1 and 65535 (e.g. 587).', 'danger')
        return redirect(url_for('settings.settings'))

    server = server or 'smtp.gmail.com'

    if password:
        # Gmail shows App Passwords as "abcd efgh ijkl mnop" - the spaces are not part of it.
        if server.lower() == 'smtp.gmail.com':
            password = ''.join(password.split())
        settings.mail_password_enc = encrypt_str(password)
    elif not settings.mail_password_enc:
        flash('Enter the email password (for Gmail: the 16-character App Password).', 'danger')
        return redirect(url_for('settings.settings'))
    # else: password box left empty -> keep the already saved password

    settings.mail_server = server
    settings.mail_port = port
    settings.mail_use_tls = use_tls
    settings.mail_username = username
    settings.mail_default_sender = sender or username
    db.session.commit()

    if request.form.get('action') == 'save_test':
        flash('Email settings saved. Connection test result is shown below.', 'success')
        return redirect(url_for('settings.settings', test_email=1))

    flash('Email settings saved.', 'success')
    return redirect(url_for('settings.settings'))


# ==================== BIOMETRIC CONSENT ====================

@settings_bp.route('/employees/<int:employee_id>/biometric-consent', methods=['POST'])
@login_required
@admin_required
def record_biometric_consent(employee_id):
    employee = Employee.query.get_or_404(employee_id)

    payload = request.get_json(silent=True) or request.form
    consent_raw = payload.get('consent')
    consent_granted = str(consent_raw).strip().lower() in ('1', 'true', 'yes', 'on')

    policy_version = (payload.get('policy_version') or 'v1.0')

    employee.record_biometric_consent(
        granted=consent_granted,
        ip_address=request.remote_addr,
        policy_version=policy_version,
    )
    db.session.commit()

    return jsonify({
        'success': True,
        'employee_id': employee.id,
        'biometric_consent_given': employee.biometric_consent_given,
        'biometric_consent_timestamp': employee.biometric_consent_timestamp.isoformat() if employee.biometric_consent_timestamp else None,
    })


# ==================== ADMIN DATA BACKUP ====================

@settings_bp.route('/admin/backup', methods=['POST'])
@login_required
@admin_required
def admin_backup():
    """Stream a single .zip download containing the SQLite database,
    uploads/ (profile photos, generated payslips), and dataset/ (captured
    face images) - everything an admin needs to restore the system on a
    new machine. Trained-model embeddings are intentionally excluded
    since they're regenerable from dataset/ via "Train AI".

    Built entirely in memory (io.BytesIO) rather than a temp file on disk,
    so nothing is left behind in the install folder if the request is
    interrupted partway through.
    """
    memory_zip = io.BytesIO()
    # BACKUP FIX: the zip is now built by backup_manager.write_backup_zip(),
    # which excludes the backup folders (uploads/backups/ and the dedicated
    # backups/ folder) from the uploads/ walk, so a downloaded backup can
    # never contain older backups inside it. It also snapshots the SQLite
    # database consistently instead of copying a file that may be mid-write.
    # The .env file is deliberately NOT included in the manual download
    # (unchanged behavior) - only the automated backup includes it.
    write_backup_zip(memory_zip, include_env=False)

    memory_zip.seek(0)
    # BUG FIX: backup filename timestamp now uses now_ist() rather than
    # the naive server-local datetime.now(), consistent with the rest of
    # the app's IST convention.
    timestamp = now_ist().strftime('%Y%m%d_%H%M%S')
    download_name = f'attendance_backup_{timestamp}.zip'

    current_app.logger.info("Admin backup created and sent for download: %s", download_name)

    return send_file(
        memory_zip,
        mimetype='application/zip',
        as_attachment=True,
        download_name=download_name,
    )


# ==================== LICENSE ACTIVATION ====================

@settings_bp.route('/settings/license-activation', methods=['POST'])
@login_required
@admin_required
def activate_license():
    """Activate a license token by pasting it into the UI."""
    license_token = request.form.get('license_token', '').strip()
    
    if not license_token:
        flash('Please enter a license token.', 'danger')
        return redirect(url_for('settings.settings'))
    
    lm = get_license_manager()
    success, message = lm.activate_license_from_string(license_token)
    
    if success:
        flash(f'License activated successfully: {message}', 'success')
        current_app.logger.info(f"License activated successfully by admin {session.get('admin_id')}")
    else:
        flash(f'License activation failed: {message}', 'danger')
        current_app.logger.warning(f"License activation failed: {message}")
    
    return redirect(url_for('settings.settings'))


@settings_bp.route('/settings/license-info', methods=['GET'])
@login_required
@admin_required
def license_info():
    """Get current license information as JSON for display in UI."""
    lm = get_license_manager()
    info = lm.get_license_info()
    return jsonify(info)


# """
# Settings routes: system/company settings (with email-connection test),
# recording an employee's biometric consent decision, and the admin
# full-data backup (.zip) download.

# Sixth blueprint migrated out of app.py.

# record_biometric_consent() is grouped here rather than in employees.py
# (the existing employee-CRUD blueprint) because it's a compliance/consent
# action on an existing employee record, not part of employee
# creation/editing - it's invoked from the Settings-adjacent biometric
# consent UI trigger, independently of the add/edit employee forms.

# Every route here was moved verbatim from app.py - no behavior changes,
# with two adaptations required to remove the app.py dependency:
# - app.config['UPLOAD_FOLDER']/['DATASET_FOLDER'] equivalents now come
#   from Config.UPLOAD_FOLDER / Config.DATASET_FOLDER directly (imported
#   from config.py, exactly as app.py itself did for admin_backup - no
#   behavior change, just going straight to the source app.py's own
#   app.config values are populated from at startup).
# - BASE_DIR is imported directly from config.py rather than from app.py.

# IMPORTANT - endpoint naming: this Blueprint's endpoints are
# "settings.<function_name>". url_for('settings') (used inside the
# settings() route's own POST-redirect, and in auth_routes.py's
# create_admin()) has been updated to url_for('settings.settings')
# everywhere it appeared.
# """
# import os
# import io
# import zipfile

# from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, session, current_app

# from config import Config, BASE_DIR
# from database import db
# from models import Settings, Employee, AttendanceSettingsHistory, now_ist
# from auth_decorators import login_required, admin_required
# from file_helpers import allowed_file
# from services.app_services import get_services
# from licensing.license_manager import get_license_manager

# settings_bp = Blueprint('settings', __name__)


# # ==================== SYSTEM / COMPANY SETTINGS ====================

# @settings_bp.route('/settings', methods=['GET', 'POST'])
# @login_required
# @admin_required
# def settings():
#     settings = Settings.get_settings()

#     if request.method == 'POST':
#         settings.company_name = request.form.get('company_name')
#         settings.office_start_time = request.form.get('office_start_time')
#         settings.office_end_time = request.form.get('office_end_time')

#         # Safe parsing for numeric fields
#         def _parse_int(field_name, default=0):
#             value = request.form.get(field_name, '').strip()
#             if not value:
#                 return default
#             try:
#                 return int(value)
#             except ValueError:
#                 return default

#         def _parse_float(field_name, default=0.0):
#             value = request.form.get(field_name, '').strip()
#             if not value:
#                 return default
#             try:
#                 return float(value)
#             except ValueError:
#                 return default

#         settings.grace_period_minutes = _parse_int('grace_period_minutes')
#         settings.working_hours_per_day = _parse_float('working_hours_per_day')
#         settings.late_deduction_enabled = request.form.get('late_deduction_enabled') == 'on'
#         settings.late_deduction_per_occurrence = _parse_float('late_deduction_per_occurrence')
#         settings.half_day_deduction_enabled = request.form.get('half_day_deduction_enabled') == 'on'
#         settings.half_day_deduction_per_occurrence = _parse_float('half_day_deduction_per_occurrence')
#         settings.absent_deduction_enabled = request.form.get('absent_deduction_enabled') == 'on'
#         settings.absent_deduction_per_occurrence = _parse_float('absent_deduction_per_occurrence')
#         settings.overtime_enabled = request.form.get('overtime_enabled') == 'on'
#         settings.overtime_rate = _parse_float('overtime_rate')
#         settings.face_recognition_tolerance = _parse_float('face_recognition_tolerance')
#         settings.min_face_images_required = _parse_int('min_face_images_required')

#         # Handle logo upload
#         if 'company_logo' in request.files:
#             file = request.files['company_logo']
#             if file and allowed_file(file.filename):
#                 try:
#                     from werkzeug.utils import secure_filename
#                     filename = secure_filename(f"company_logo_{file.filename}")
#                     # Use BASE_DIR to ensure path is absolute and persists across restarts
#                     logo_dir = os.path.join(BASE_DIR, 'static', 'images')
#                     os.makedirs(logo_dir, exist_ok=True)
#                     logo_path = os.path.join(logo_dir, filename)
#                     file.save(logo_path)
#                     settings.company_logo = f"static/images/{filename}"
#                 except Exception as e:
#                     current_app.logger.error(f"Failed to save company logo: {e}")
#                     flash('Failed to save company logo. Please try again.', 'danger')

#         db.session.commit()

#         # Create AttendanceSettingsHistory record for historical accuracy
#         # This ensures old attendance records use old settings, new records use new settings

#         # Check if history already exists for this exact timestamp.
#         # BUG FIX: this timestamp is recorded in IST via now_ist() - same
#         # convention as every other timestamp in this app (created_at,
#         # updated_at, etc.) - rather than the naive server-local
#         # datetime.now() this route used previously.
#         current_timestamp = now_ist()
#         existing_history = AttendanceSettingsHistory.query.filter_by(effective_from=current_timestamp).first()
#         if not existing_history:
#             # Create new history record effective from NOW (precise timestamp)
#             history = AttendanceSettingsHistory(
#                 effective_from=current_timestamp,
#                 office_start_time=settings.office_start_time,
#                 office_end_time=settings.office_end_time,
#                 working_hours_per_day=settings.working_hours_per_day,
#                 half_day_hours=settings.half_day_hours,
#                 grace_period_minutes=settings.grace_period_minutes,
#                 created_by=session.get('admin_id')
#             )
#             db.session.add(history)
#             db.session.commit()
#             current_app.logger.info(f"ATTENDANCE SETTINGS HISTORY CREATED - Effective From: {current_timestamp}")

#         flash("Settings updated successfully")
#         return redirect(url_for("settings.settings"))

#     # Test email connection
#     email_test = None
#     if request.args.get('test_email'):
#         _, _, es, _ = get_services()
#         email_test = es.test_email_connection()

#     return render_template('settings.html', settings=settings, email_test=email_test)


# # ==================== BIOMETRIC CONSENT ====================

# @settings_bp.route('/employees/<int:employee_id>/biometric-consent', methods=['POST'])
# @login_required
# @admin_required
# def record_biometric_consent(employee_id):
#     employee = Employee.query.get_or_404(employee_id)

#     payload = request.get_json(silent=True) or request.form
#     consent_raw = payload.get('consent')
#     consent_granted = str(consent_raw).strip().lower() in ('1', 'true', 'yes', 'on')

#     policy_version = (payload.get('policy_version') or 'v1.0')

#     employee.record_biometric_consent(
#         granted=consent_granted,
#         ip_address=request.remote_addr,
#         policy_version=policy_version,
#     )
#     db.session.commit()

#     return jsonify({
#         'success': True,
#         'employee_id': employee.id,
#         'biometric_consent_given': employee.biometric_consent_given,
#         'biometric_consent_timestamp': employee.biometric_consent_timestamp.isoformat() if employee.biometric_consent_timestamp else None,
#     })


# # ==================== ADMIN DATA BACKUP ====================

# @settings_bp.route('/admin/backup', methods=['POST'])
# @login_required
# @admin_required
# def admin_backup():
#     """Stream a single .zip download containing the SQLite database,
#     uploads/ (profile photos, generated payslips), and dataset/ (captured
#     face images) - everything an admin needs to restore the system on a
#     new machine. Trained-model embeddings are intentionally excluded
#     since they're regenerable from dataset/ via "Train AI".

#     Built entirely in memory (io.BytesIO) rather than a temp file on disk,
#     so nothing is left behind in the install folder if the request is
#     interrupted partway through.
#     """
#     db_path = os.path.join(BASE_DIR, 'instance', 'attendance.db')

#     memory_zip = io.BytesIO()
#     with zipfile.ZipFile(memory_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
#         if os.path.exists(db_path):
#             zf.write(db_path, arcname=os.path.join('instance', 'attendance.db'))
#         else:
#             # Most likely a MySQL deployment (DATABASE_URL set) rather than
#             # the default SQLite file - nothing to include here, but don't
#             # fail the whole backup over it.
#             current_app.logger.warning("admin_backup: no SQLite file found at %s - skipping database in backup", db_path)

#         for folder_name, folder_path in (('uploads', Config.UPLOAD_FOLDER), ('dataset', Config.DATASET_FOLDER)):
#             if not os.path.exists(folder_path):
#                 continue
#             for root, _dirs, files in os.walk(folder_path):
#                 for filename in files:
#                     file_path = os.path.join(root, filename)
#                     arcname = os.path.join(folder_name, os.path.relpath(file_path, folder_path))
#                     zf.write(file_path, arcname=arcname)

#     memory_zip.seek(0)
#     # BUG FIX: backup filename timestamp now uses now_ist() rather than
#     # the naive server-local datetime.now(), consistent with the rest of
#     # the app's IST convention.
#     timestamp = now_ist().strftime('%Y%m%d_%H%M%S')
#     download_name = f'attendance_backup_{timestamp}.zip'

#     current_app.logger.info("Admin backup created and sent for download: %s", download_name)

#     return send_file(
#         memory_zip,
#         mimetype='application/zip',
#         as_attachment=True,
#         download_name=download_name,
#     )


# # ==================== LICENSE ACTIVATION ====================

# @settings_bp.route('/settings/license-activation', methods=['POST'])
# @login_required
# @admin_required
# def activate_license():
#     """Activate a license token by pasting it into the UI."""
#     license_token = request.form.get('license_token', '').strip()
    
#     if not license_token:
#         flash('Please enter a license token.', 'danger')
#         return redirect(url_for('settings.settings'))
    
#     lm = get_license_manager()
#     success, message = lm.activate_license_from_string(license_token)
    
#     if success:
#         flash(f'License activated successfully: {message}', 'success')
#         current_app.logger.info(f"License activated successfully by admin {session.get('admin_id')}")
#     else:
#         flash(f'License activation failed: {message}', 'danger')
#         current_app.logger.warning(f"License activation failed: {message}")
    
#     return redirect(url_for('settings.settings'))


# @settings_bp.route('/settings/license-info', methods=['GET'])
# @login_required
# @admin_required
# def license_info():
#     """Get current license information as JSON for display in UI."""
#     lm = get_license_manager()
#     info = lm.get_license_info()
#     return jsonify(info)
