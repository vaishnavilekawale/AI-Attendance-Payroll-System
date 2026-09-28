"""
Payroll routes: admin payroll list/calculate/payslip generation/email,
payroll automation settings, and the employee-self-service payroll page
+ custom payslip password.

Fourth blueprint migrated out of app.py. Uses services/app_services.py's
get_services() (itself extracted from app.py) for the shared
AttendanceManager/PayrollCalculator/EmailService/PDFGenerator singletons,
rather than importing anything from app.py directly - this is what keeps
this file free of a circular import with app.py.

Every route here was moved verbatim from app.py - no behavior changes.

IMPORTANT - endpoint naming: because this is a Blueprint, every route's
Flask endpoint name is now "payroll.<function_name>" instead of just
"<function_name>" (e.g. url_for('payroll.payroll') instead of
url_for('payroll'), url_for('payroll.calculate_payroll') instead of
url_for('calculate_payroll'), etc.). Every url_for() call across app.py
and the templates that referenced these 7 endpoints (payroll,
calculate_payroll, generate_payslip, send_payslip_email,
payroll_settings, employee_payroll, set_payslip_password) has been
updated to match.
"""
import os
from datetime import datetime

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    send_file, make_response, session,
)
from werkzeug.utils import secure_filename

import crypto_utils
from database import db
from models import Payroll, Employee, PayrollSettings, CompanySettings
from auth_decorators import login_required, admin_required, employee_required
from file_helpers import allowed_file
from pdf_generator import generate_payslip_password
from scheduler_service import payroll_scheduler, get_payslip_full_path
from services.app_services import get_services
from extensions import limiter

payroll_bp = Blueprint('payroll', __name__)


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


# ==================== ADMIN PAYROLL ====================

@payroll_bp.route('/payroll')
@login_required
@admin_required
def payroll():
    month = request.args.get('month', datetime.now().month, type=int)
    year = request.args.get('year', datetime.now().year, type=int)

    _, pc, _, _ = get_services()
    payrolls = Payroll.query.filter_by(month=month, year=year).order_by(Payroll.net_salary.desc()).all()
    summary = pc.get_payroll_summary(year, month)

    return render_template('payroll.html', payrolls=payrolls, summary=summary, month=month, year=year)


@payroll_bp.route('/payroll/calculate', methods=['POST'])
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
            employee = db.session.get(Employee, int(employee_id))
            if employee and not is_payroll_eligible(employee, month, year):
                flash(f'Employee {employee.name} is not eligible for payroll for {month}/{year} (joined on {employee.joining_date}).', 'warning')
                return redirect(url_for('payroll.payroll', month=month, year=year))

        payroll_records = pc.calculate_monthly_payroll(
            year,
            month,
            int(employee_id) if employee_id and employee_id.strip() else None,
        )
        flash(f'Payroll calculated for {len(payroll_records)} employee(s) for {month}/{year}', 'success')
    except Exception as e:
        flash(f'Error calculating payroll: {str(e)}', 'danger')

    return redirect(url_for('payroll.payroll', month=month, year=year))


@payroll_bp.route('/payroll/payslip/<int:id>')
@login_required
def generate_payslip(id):
    payroll = Payroll.query.get_or_404(id)
    employee = Employee.query.get_or_404(payroll.employee_id)

    # Re-fetch from DB so PDF numbers match the latest saved payroll record.
    db.session.refresh(payroll)

    filename = f"payslip_{employee.employee_id}_{payroll.month}_{payroll.year}.pdf"

    # BUG FIX: build the path using the SAME shared helper scheduler_service
    # uses (get_payslip_full_path), so a manually-generated payslip always
    # lands in exactly the same place an auto-generated one would - always
    # anchored under Config.UPLOAD_FOLDER. See scheduler_service.py's
    # get_payslip_full_path()/_get_payslip_path() docstrings for the full
    # history of the bug this fixes.
    output_path = get_payslip_full_path(payroll.year, payroll.month, filename)

    # Create directory if it doesn't exist
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

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

    # BUG FIX: store the exact, full, absolute path the file was just
    # written to (output_path, from get_payslip_full_path() above), not a
    # hand-built relative string. This is exactly what scheduler_service.py
    # stores for auto-generated payrolls, and it's what
    # send_payslip_email() below (and EmailService) now read directly -
    # eliminating the "generated here, looked for there" mismatch.
    payroll.payslip_path = output_path

    db.session.commit()

    # 2. Browser cache disable karanyasathi he use kara
    response = make_response(send_file(output_path, as_attachment=True, download_name=filename))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@payroll_bp.route('/payroll/send-email/<int:id>')
@login_required
@admin_required
def send_payslip_email(id):
    payroll = Payroll.query.get_or_404(id)
    employee = Employee.query.get_or_404(payroll.employee_id)

    if not payroll.payslip_path:
        flash('Please generate payslip first', 'warning')
        return redirect(url_for('payroll.payroll'))

    # BUG FIX: pass the stored path straight through. It's now always the
    # full, correct path under Config.UPLOAD_FOLDER (see generate_payslip()
    # above and scheduler_service.py). Previously this stripped the
    # year/month sub-folders via os.path.basename() and re-joined with
    # UPLOAD_FOLDER directly, which pointed at a location the file was
    # never actually written to. EmailService also independently falls
    # back to resolving legacy/pre-fix path formats
    # (see EmailService._resolve_attachment_path), so payslips generated
    # before this fix can still be emailed successfully.
    payslip_path = payroll.payslip_path

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

    return redirect(url_for('payroll.payroll'))


# ==================== PAYROLL AUTOMATION / COMPANY SETTINGS ====================

@payroll_bp.route('/payroll-settings', methods=['GET', 'POST'])
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
        return redirect(url_for('payroll.payroll_settings'))

    return render_template('payroll_settings.html',
                         payroll_settings=payroll_settings,
                         company_settings=company_settings)


# ==================== EMPLOYEE-SELF-SERVICE PAYROLL ====================

@payroll_bp.route('/employee-payroll')
@login_required
@employee_required
def employee_payroll():
    """Employee payroll page showing only their own payroll"""
    employee_id = session['employee_id']
    employee = db.session.get(Employee, employee_id)

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


@payroll_bp.route('/employee/set-payslip-password', methods=['POST'])
@login_required
@employee_required
@limiter.limit("5 per minute")
def set_payslip_password():
    """
    Let an employee set (or clear) a custom payslip PDF-open password,
    overriding the default auto-generated one. Stored encrypted at rest
    (see crypto_utils.py / Employee.payslip_password_override) since the
    PDF-open password must remain recoverable in plaintext.
    """
    employee_id = session['employee_id']
    employee = db.session.get(Employee, employee_id)
    if not employee:
        flash('Employee not found', 'danger')
        return redirect(url_for('auth.login'))

    if request.form.get('clear_override') == '1':
        employee.payslip_password_override = None
        db.session.commit()
        flash('Custom payslip password removed - the default auto-generated password will be used again.', 'success')
        return redirect(url_for('attendance.employee_profile'))

    new_password = (request.form.get('payslip_password') or '').strip()
    if len(new_password) < 8:
        flash('Payslip password must be at least 8 characters.', 'danger')
        return redirect(url_for('attendance.employee_profile'))

    employee.payslip_password_override = crypto_utils.encrypt_str(new_password)
    db.session.commit()
    flash('Custom payslip password saved. Use it to open your future payslip PDFs.', 'success')
    return redirect(url_for('attendance.employee_profile'))
