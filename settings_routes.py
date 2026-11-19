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
from datetime import date

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, session, current_app

from config import Config, BASE_DIR
from database import db
from models import (Settings, Employee, AttendanceSettingsHistory, Holiday, Payroll,
                    EmployeeLeave, LeaveQuota, LeaveRequest, now_ist)
from auth_decorators import login_required, admin_required
from file_helpers import allowed_file
from services.app_services import get_services
from licensing.license_manager import get_license_manager
import backup_manager
from backup_manager import write_backup_zip, BackupError

settings_bp = Blueprint('settings', __name__)

# Shown in Settings -> System Info and the page footer. Keep in sync with
# MyAppVersion in installer/AttendancePayrollSystem.iss.
APP_VERSION = '1.0.0'


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
    holidays = Holiday.query.order_by(Holiday.date).all()
    holidays_json = [{'id': h.id, 'date': h.date.isoformat(), 'name': h.name} for h in holidays]
    holiday_years = sorted({h.date.year for h in holidays}, reverse=True)
    this_year = now_ist().year
    if this_year in holiday_years:
        default_holiday_year = this_year
    else:
        default_holiday_year = holiday_years[0] if holiday_years else this_year
    try:
        pending_leave_requests = LeaveRequest.query.filter_by(status='pending').count()
    except Exception:
        db.session.rollback()
        pending_leave_requests = 0
    try:
        backup_info = backup_manager.backup_status()
        backup_files = backup_manager.list_backups()
    except Exception as exc:  # never let the backup card break the Settings page
        current_app.logger.error("Could not read backup status: %s", exc)
        backup_info, backup_files = None, []
    return render_template('settings.html', settings=settings, email_test=email_test,
                           backup_info=backup_info, backup_files=backup_files,
                           mail_status=_email_config_status(settings),
                           pending_leave_requests=pending_leave_requests,
                           holidays=holidays, holidays_json=holidays_json,
                           app_version=APP_VERSION,
                           holiday_years=holiday_years,
                           default_holiday_year=default_holiday_year)


# ==================== HOLIDAY CALENDAR ====================

@settings_bp.route('/settings/holidays/add', methods=['POST'])
@login_required
@admin_required
def add_holidays():
    """Add one or more paid-holiday dates (comma-separated YYYY-MM-DD) sharing one name."""
    from datetime import datetime as _dt
    name = (request.form.get('name') or '').strip()
    raw_dates = [d.strip() for d in (request.form.get('dates') or '').split(',') if d.strip()]

    if not raw_dates:
        flash('Please select at least one date on the calendar.', 'danger')
        return redirect(url_for('settings.settings') + '#holidays')
    if not name:
        flash('Please enter a holiday name (e.g. Diwali).', 'danger')
        return redirect(url_for('settings.settings') + '#holidays')
    name = name[:100]

    added = updated = skipped = 0
    for raw in raw_dates:
        try:
            d = _dt.strptime(raw, '%Y-%m-%d').date()
        except ValueError:
            skipped += 1
            continue
        existing = Holiday.query.filter_by(date=d).first()
        if existing:
            existing.name = name
            updated += 1
        else:
            db.session.add(Holiday(date=d, name=name))
            added += 1
    db.session.commit()

    current_app.logger.info(f"HOLIDAYS saved by admin: added={added}, updated={updated}, skipped={skipped}, name={name}")
    msg = f"{added} holiday(s) added"
    if updated:
        msg += f", {updated} renamed"
    if skipped:
        msg += f", {skipped} invalid date(s) ignored"
    flash(msg + '.', 'success' if (added or updated) else 'danger')
    return redirect(url_for('settings.settings') + '#holidays')


@settings_bp.route('/settings/holidays/<int:holiday_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_holiday(holiday_id):
    holiday = db.session.get(Holiday, holiday_id)
    if not holiday:
        flash('Holiday not found.', 'danger')
    else:
        db.session.delete(holiday)
        db.session.commit()
        current_app.logger.info(f"HOLIDAY removed by admin: {holiday.date} {holiday.name}")
        flash(f"Holiday '{holiday.name}' ({holiday.date.strftime('%d %b %Y')}) removed.", 'success')
    return redirect(url_for('settings.settings') + '#holidays')


@settings_bp.route('/settings/holidays/pdf', methods=['GET'])
@login_required
@admin_required
def download_holidays_pdf():
    """Download the configured holiday list as a PDF (?year=2026 or ?year=all)."""
    from pdf_generator import PDFGenerator

    year_arg = (request.args.get('year') or 'all').strip().lower()
    query = Holiday.query.order_by(Holiday.date)
    if year_arg == 'all':
        year_label = 'All years'
    else:
        try:
            year = int(year_arg)
        except ValueError:
            flash('Invalid year.', 'danger')
            return redirect(url_for('settings.settings') + '#holidays')
        query = query.filter(Holiday.date >= date(year, 1, 1), Holiday.date <= date(year, 12, 31))
        year_label = str(year)

    settings = Settings.query.first()
    pdf_bytes = PDFGenerator().generate_holiday_list_pdf(query.all(), getattr(settings, 'company_name', '') or '', year_label)
    filename = f"holiday_list_{year_label.replace(' ', '_').lower()}.pdf"
    return send_file(io.BytesIO(pdf_bytes), mimetype='application/pdf',
                     as_attachment=True, download_name=filename)


# ==================== EMPLOYEE PAID LEAVE (CL / SL / EL) ====================

def _leave_year(raw):
    try:
        y = int(raw)
        if 2000 <= y <= 2100:
            return y
    except (TypeError, ValueError):
        pass
    return now_ist().year


def _parse_iso_date(raw):
    from datetime import datetime as _dt
    try:
        return _dt.strptime((raw or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def _leave_redirect(year=None, employee_id=None):
    args = {}
    if year:
        args['year'] = year
    if employee_id:
        args['employee_id'] = employee_id
    return redirect(url_for('settings.leaves', **args))


def _leave_report_data(year, employee_id=None):
    """Balance rows + leave records used by both the page and the PDF."""
    from leave_utils import LEAVE_TYPES, get_default_quotas, get_balance

    employees = Employee.query.filter_by(status='active').order_by(Employee.name).all()
    defaults = get_default_quotas()
    balances = []
    for emp in employees:
        if employee_id and emp.id != employee_id:
            continue
        balances.append({'employee': emp, 'balance': get_balance(emp.id, year, defaults)})

    q = EmployeeLeave.query.filter(
        EmployeeLeave.date >= date(year, 1, 1), EmployeeLeave.date <= date(year, 12, 31))
    if employee_id:
        q = q.filter(EmployeeLeave.employee_id == employee_id)
    records = q.order_by(EmployeeLeave.date.desc(), EmployeeLeave.id.desc()).all()
    return employees, defaults, balances, records


@settings_bp.route('/settings/leaves', methods=['GET'])
@login_required
@admin_required
def leaves():
    """Employee paid leave: add leave, quotas, balances and leave records."""
    from leave_utils import LEAVE_TYPES

    year = _leave_year(request.args.get('year'))
    try:
        employee_id = int(request.args.get('employee_id') or 0) or None
    except ValueError:
        employee_id = None

    employees, defaults, balances, records = _leave_report_data(year, employee_id)
    years = sorted({year, now_ist().year} | {r.date.year for r in EmployeeLeave.query.all()}, reverse=True)
    from leave_utils import get_balance, get_employee_overrides
    overrides_json = {str(e.id): get_employee_overrides(e.id) for e in employees}
    balances_json = {str(e.id): get_balance(e.id, year, defaults) for e in employees}
    from leave_utils import leave_working_days
    pending_requests = (LeaveRequest.query.filter_by(status='pending')
                        .order_by(LeaveRequest.created_at).all())
    recent_requests = (LeaveRequest.query.filter(LeaveRequest.status != 'pending')
                       .order_by(LeaveRequest.decided_at.desc(), LeaveRequest.id.desc()).limit(15).all())
    request_info = {}
    for r in pending_requests:
        bal = get_balance(r.employee_id, r.from_date.year, defaults)[r.leave_type]
        request_info[r.id] = {'days': len(leave_working_days(r.employee, r.from_date, r.to_date)),
                              'balance': bal['balance'], 'quota': bal['quota']}
    return render_template('leaves.html', employees=employees, leave_types=LEAVE_TYPES,
                           pending_requests=pending_requests, recent_requests=recent_requests,
                           request_info=request_info,
                           defaults=defaults, balances=balances, records=records,
                           year=year, years=years, employee_id=employee_id,
                           balances_json=balances_json, overrides_json=overrides_json,
                           app_version=APP_VERSION,
                           today_iso=now_ist().date().isoformat())


@settings_bp.route('/settings/leaves/add', methods=['POST'])
@login_required
@admin_required
def add_leave():
    from leave_utils import grant_leave, LEAVE_TYPE_NAMES

    employee = db.session.get(Employee, request.form.get('employee_id', type=int) or 0)
    leave_type = (request.form.get('leave_type') or '').upper()
    start = _parse_iso_date(request.form.get('from_date'))
    end = _parse_iso_date(request.form.get('to_date')) or start
    reason = request.form.get('reason') or ''

    if not employee:
        flash('Please select an employee.', 'danger')
        return _leave_redirect()
    if leave_type not in LEAVE_TYPE_NAMES:
        flash('Please select a leave type.', 'danger')
        return _leave_redirect()
    if not start:
        flash('Please select the leave date.', 'danger')
        return _leave_redirect()

    try:
        res = grant_leave(employee, start, end, leave_type, reason, session.get('admin_id'))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
        return _leave_redirect(start.year)
    db.session.commit()

    fmt = lambda days: ', '.join(d.strftime('%d %b') for d in days)
    granted = res['granted']
    if granted:
        msg = f"{len(granted)} day(s) of {LEAVE_TYPE_NAMES[leave_type]} recorded for {employee.name}."
        category = 'success'
    else:
        msg = f"No leave was recorded for {employee.name}."
        category = 'danger'
    if res['over_quota']:
        msg += (f" {len(res['over_quota'])} day(s) not recorded because the yearly "
                f"{leave_type} quota is used up - they stay Absent / LOP ({fmt(res['over_quota'])}).")
        category = 'warning' if granted else 'danger'
    if res['off_days']:
        msg += f" Skipped Sunday/holiday: {fmt(res['off_days'])}."
    if res['already']:
        msg += f" Already had leave: {fmt(res['already'])}."
    if res['before_joining']:
        msg += f" Before joining date, skipped: {fmt(res['before_joining'])}."

    # Payroll that was already generated for these months does not change by itself.
    months = sorted({(d.year, d.month) for d in granted})
    stale = [m for m in months if Payroll.query.filter_by(
        employee_id=employee.id, year=m[0], month=m[1]).first()]
    if stale:
        msg += (" Payroll for " + ', '.join(date(y, m, 1).strftime('%b %Y') for y, m in stale) +
                " already exists - recalculate it from the Payroll page to include this leave.")
        category = 'warning' if category == 'success' else category

    current_app.logger.info(
        f"LEAVE added by admin: employee={employee.id} type={leave_type} granted={len(granted)} "
        f"over_quota={len(res['over_quota'])} off={len(res['off_days'])} already={len(res['already'])}")
    flash(msg, category)
    return _leave_redirect(start.year)


@settings_bp.route('/settings/leaves/requests/<int:request_id>/approve', methods=['POST'])
@login_required
@admin_required
def approve_leave_request(request_id):
    """Approve an employee's leave request: creates the paid leave days."""
    from leave_utils import grant_leave, LEAVE_TYPE_NAMES

    req = db.session.get(LeaveRequest, request_id)
    if not req or req.status != 'pending':
        flash('This request is no longer pending.', 'warning')
        return _leave_redirect()
    employee = req.employee
    try:
        res = grant_leave(employee, req.from_date, req.to_date, req.leave_type,
                          req.reason or '', session.get('admin_id'))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
        return _leave_redirect()

    fmt = lambda days: ', '.join(d.strftime('%d %b') for d in days)
    granted = res['granted']
    if not granted:
        db.session.rollback()
        why = []
        if res['over_quota']:
            why.append(f"the {req.leave_type} quota is used up ({fmt(res['over_quota'])})")
        if res['already']:
            why.append(f"leave already exists on {fmt(res['already'])}")
        if res['off_days']:
            why.append(f"only Sundays/holidays ({fmt(res['off_days'])})")
        flash(f"Could not approve - no day can be recorded: {'; '.join(why) or 'nothing to record'}. "
              f"Increase the quota or reject the request.", 'danger')
        return _leave_redirect()
    db.session.flush()   # let the quota/leave rows be visible before the payroll check below

    note = ''
    if res['over_quota']:
        note = f"Not paid (quota used up): {fmt(res['over_quota'])}"
    req.status = 'approved'
    req.decided_by = session.get('admin_id')
    req.decided_at = now_ist()
    req.admin_note = (note[:200] or None)
    db.session.commit()

    msg = f"Approved: {len(granted)} day(s) of {LEAVE_TYPE_NAMES[req.leave_type]} for {employee.name}."
    category = 'success'
    if note:
        msg += f" {note} - those days stay Absent / LOP."
        category = 'warning'
    months = sorted({(d.year, d.month) for d in granted})
    stale = [m for m in months if Payroll.query.filter_by(
        employee_id=employee.id, year=m[0], month=m[1]).first()]
    if stale:
        msg += (" Payroll for " + ', '.join(date(y, m, 1).strftime('%b %Y') for y, m in stale) +
                " already exists - recalculate it from the Payroll page to include this leave.")
        category = 'warning'
    current_app.logger.info(f"LEAVE request {req.id} approved: employee={employee.id} granted={len(granted)}")
    flash(msg, category)
    return _leave_redirect(req.from_date.year)


@settings_bp.route('/settings/leaves/requests/<int:request_id>/reject', methods=['POST'])
@login_required
@admin_required
def reject_leave_request(request_id):
    req = db.session.get(LeaveRequest, request_id)
    if not req or req.status != 'pending':
        flash('This request is no longer pending.', 'warning')
        return _leave_redirect()
    req.status = 'rejected'
    req.decided_by = session.get('admin_id')
    req.decided_at = now_ist()
    req.admin_note = ((request.form.get('note') or '').strip()[:200] or None)
    db.session.commit()
    current_app.logger.info(f"LEAVE request {req.id} rejected: employee={req.employee_id}")
    flash(f"Request from {req.employee.name} rejected.", 'success')
    return _leave_redirect()


@settings_bp.route('/settings/leaves/<int:leave_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_leave(leave_id):
    leave = db.session.get(EmployeeLeave, leave_id)
    year = None
    if not leave:
        flash('Leave not found.', 'danger')
    else:
        year = leave.date.year
        name = leave.employee.name if leave.employee else ''
        stale = Payroll.query.filter_by(
            employee_id=leave.employee_id, year=leave.date.year, month=leave.date.month).first()
        d = leave.date
        db.session.delete(leave)
        db.session.commit()
        current_app.logger.info(f"LEAVE removed by admin: employee={leave.employee_id} date={d}")
        msg = f"Leave on {d.strftime('%d %b %Y')} removed for {name}."
        if stale:
            msg += f" Payroll for {d.strftime('%b %Y')} already exists - recalculate it from the Payroll page."
        flash(msg, 'warning' if stale else 'success')
    return _leave_redirect(year)


@settings_bp.route('/settings/leaves/quotas', methods=['POST'])
@login_required
@admin_required
def save_leave_quotas():
    """Save yearly quotas: company default (no employee) or one employee's override."""
    from leave_utils import LEAVE_TYPES

    employee_id = request.form.get('employee_id', type=int)
    employee = db.session.get(Employee, employee_id) if employee_id else None
    if employee_id and not employee:
        flash('Employee not found.', 'danger')
        return _leave_redirect()

    # "Reset to default": drop every custom quota of this employee, so the
    # company default (Yearly Quota - all employees) applies again.
    if employee and request.form.get('reset'):
        LeaveQuota.query.filter_by(employee_id=employee.id).delete()
        db.session.commit()
        flash(f"{employee.name} now uses the default quota again.", 'success')
        return _leave_redirect(request.form.get('year', type=int))

    values = {}
    for code, _ in LEAVE_TYPES:
        raw = (request.form.get(f'quota_{code}') or '').strip()
        if raw == '':
            values[code] = None  # blank: employee -> use the default; default -> keep current
            continue
        try:
            days = float(raw)
        except ValueError:
            flash('Quota must be a number.', 'danger')
            return _leave_redirect()
        if days < 0 or days > 366:
            flash('Quota must be between 0 and 366 days.', 'danger')
            return _leave_redirect()
        values[code] = days

    for code, days in values.items():
        row = LeaveQuota.query.filter_by(employee_id=employee.id if employee else None,
                                         leave_type=code).first()
        if days is None:
            if employee and row:
                db.session.delete(row)  # back to the company default
            continue
        if row:
            row.days_per_year = days
        else:
            db.session.add(LeaveQuota(employee_id=employee.id if employee else None,
                                      leave_type=code, days_per_year=days))
    db.session.commit()
    flash(f"Leave quota saved for {employee.name}." if employee else "Default leave quota saved.", 'success')
    return _leave_redirect(request.form.get('year', type=int))


@settings_bp.route('/settings/leaves/pdf', methods=['GET'])
@login_required
@admin_required
def download_leaves_pdf():
    """Download leave balances + leave records as PDF (?year=2026[&employee_id=5])."""
    from pdf_generator import PDFGenerator

    year = _leave_year(request.args.get('year'))
    employee_id = request.args.get('employee_id', type=int)
    _, _, balances, records = _leave_report_data(year, employee_id)
    settings = Settings.query.first()
    scope = ''
    if employee_id:
        emp = db.session.get(Employee, employee_id)
        scope = emp.name if emp else ''
    pdf_bytes = PDFGenerator().generate_leave_report_pdf(
        year, getattr(settings, 'company_name', '') or '', balances, records, scope)
    filename = f"leave_report_{year}" + (f"_emp{employee_id}" if employee_id else '') + ".pdf"
    return send_file(io.BytesIO(pdf_bytes), mimetype='application/pdf',
                     as_attachment=True, download_name=filename)


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


# ---- Backup schedule / run now / restore -------------------------------------

def _backup_redirect():
    return redirect(url_for('settings.settings') + '#backup')


@settings_bp.route('/admin/backup/settings', methods=['POST'])
@login_required
@admin_required
def admin_backup_settings():
    """Save the daily backup time, how many backups to keep and the optional
    backup password (turns encryption on/off), then re-arm the scheduler."""
    password = request.form.get('backup_password', '')
    confirm = request.form.get('backup_password_confirm', '')
    clear_password = request.form.get('clear_backup_password') == 'on'

    try:
        if password and password != confirm:
            raise BackupError('The two backup passwords do not match.')
        backup_manager.save_backup_settings(
            time_str=request.form.get('backup_time', ''),
            keep=request.form.get('backup_keep', ''),
            new_password=password or None,
            clear_password=clear_password,
        )
    except BackupError as exc:
        flash(str(exc), 'danger')
        return _backup_redirect()

    try:
        from scheduler_service import payroll_scheduler
        payroll_scheduler.reschedule_backup()
    except Exception as exc:
        current_app.logger.error("Could not re-arm the backup schedule: %s", exc)
        flash('Settings saved, but the schedule could not be updated until the app restarts.', 'warning')
        return _backup_redirect()

    current_app.logger.info("Backup settings changed by admin %s (time=%s, keep=%s, encrypted=%s)",
                            session.get('admin_id'), backup_manager.schedule_time_str(),
                            backup_manager.backup_keep_count(),
                            backup_manager.get_backup_password() is not None)
    msg = (f"Backup settings saved. A backup will run every day at "
           f"{backup_manager.schedule_time_str()}.")
    if password:
        msg += " New backups are now encrypted - keep the password safe, it cannot be recovered."
    flash(msg, 'success')
    return _backup_redirect()


@settings_bp.route('/admin/backup/run-now', methods=['POST'])
@login_required
@admin_required
def admin_backup_run_now():
    """Run the same verified backup as the daily job, right now (saved in the
    backups folder - not downloaded)."""
    result = backup_manager.run_backup(now_stamp=now_ist().strftime('%Y%m%d_%H%M%S'))
    if result['success']:
        extra = ' (a copy was also saved to the external location)' if result['external_path'] else ''
        flash(f"Backup completed and verified: {os.path.basename(result['path'])}{extra}.", 'success')
        if result.get('external_error'):
            flash(f"The external copy failed: {result['external_error']}", 'warning')
    else:
        flash(f"Backup FAILED: {result['error']}", 'danger')
    return _backup_redirect()


@settings_bp.route('/admin/backup/restore', methods=['POST'])
@login_required
@admin_required
def admin_backup_restore():
    """Restore a backup that is in the backups folder. Requires typing RESTORE.
    The current data is saved as a pre_restore_* backup first."""
    if (request.form.get('confirm_text') or '').strip() != 'RESTORE':
        flash('Restore cancelled: type RESTORE in the confirmation box to continue.', 'warning')
        return _backup_redirect()
    try:
        path = backup_manager.resolve_backup(request.form.get('backup_ref', ''))
    except BackupError as exc:
        flash(str(exc), 'danger')
        return _backup_redirect()

    result = backup_manager.restore_backup(
        path,
        password=request.form.get('restore_password') or None,
        restore_env=request.form.get('restore_env') == 'on',
    )

    # The database file content changed underneath SQLAlchemy: drop pooled connections.
    try:
        db.session.remove()
        db.engine.dispose()
    except Exception:
        pass

    if not result['success']:
        flash(f"Restore failed: {result['error']}", 'danger')
        return _backup_redirect()

    current_app.logger.warning("Admin %s restored backup %s", session.get('admin_id'), os.path.basename(path))
    notes = [f"Restore complete from {os.path.basename(path)}."]
    if result['safety_backup']:
        notes.append(f"Your previous data was saved first as {os.path.basename(result['safety_backup'])}.")
    if result['env_restored']:
        notes.append("The .env file was restored (old copy kept as .env.before_restore).")
    if result['license_restored']:
        notes.append("license.lic was restored.")
    notes.append("Please CLOSE AND RESTART the application now, then run Train AI if face "
                 "recognition looks off. You may need to log in again.")
    flash(' '.join(notes), 'success')
    return _backup_redirect()


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
