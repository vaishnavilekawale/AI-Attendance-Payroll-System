"""
Auth routes: unified login/logout, forgot/change password (admin +
employee), legacy /register forwarding, and authenticated admin creation.

Third blueprint migrated out of app.py, following the same pattern as
blueprints/setup_wizard.py (first) and blueprints/employees.py (second):
shared extensions/decorators/helpers are imported from neutral modules
(extensions.py, auth_decorators.py, auth_helpers.py, models.py) rather
than from app.py, so there is no circular import between this file and
app.py.

Every route here was moved verbatim from app.py - no behavior changes.

IMPORTANT - endpoint naming: because this is a Blueprint, every route's
Flask endpoint name is now "auth.<function_name>" instead of just
"<function_name>" (e.g. url_for('auth.login') instead of
url_for('login')). Every url_for() call across app.py, the other
blueprints, auth_decorators.py, and the templates that referenced these
8 endpoints (login, logout, forgot_password, change_password,
employee_forgot_password, employee_change_password, register,
create_admin) has been updated to match.
"""
from flask import Blueprint, render_template, request, redirect, url_for, session, flash

from database import db
from models import Admin, Employee, EmployeeLogin, now_ist
from auth_decorators import login_required, admin_required
from auth_helpers import create_admin_from_request_form
from email_service import EmailService
from extensions import limiter

auth_bp = Blueprint('auth', __name__)


# ==================== UNIFIED LOGIN / LOGOUT ====================

@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute, 50 per hour")
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
                admin.last_login = now_ist()
                db.session.commit()

                if admin.force_password_change:
                    flash('For security, you must change your password before continuing.', 'info')
                    return redirect(url_for('auth.change_password'))

                return redirect(url_for('attendance.dashboard'))
            elif admin.check_temporary_password(password):
                # Check if temporary password is still valid (not expired)
                if not admin.is_temporary_password_valid():
                    flash('Temporary password has expired. Please request a new one.', 'danger')
                    return render_template('login.html')

                session['admin_id'] = admin.id
                session['admin_username'] = admin.username
                session['user_role'] = 'admin'
                admin.last_login = now_ist()
                db.session.commit()

                # Redirect to change password
                flash('You must change your temporary password before continuing.', 'info')
                return redirect(url_for('auth.change_password'))
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
                login_creds.last_login = now_ist()
                db.session.commit()

                # Check if first login or force password change - redirect to change password
                if login_creds.first_login or login_creds.force_password_change:
                    flash('Please change your default password before continuing.', 'info')
                    return redirect(url_for('auth.employee_change_password'))

                return redirect(url_for('attendance.employee_dashboard'))
            elif login_creds.check_temporary_password(password):
                # Check if temporary password is still valid (not expired)
                if not login_creds.is_temporary_password_valid():
                    flash('Temporary password has expired. Please request a new one.', 'danger')
                    return render_template('login.html')

                session['employee_id'] = employee.id
                session['employee_username'] = employee.employee_id
                session['user_role'] = 'employee'
                login_creds.last_login = now_ist()
                db.session.commit()

                # Redirect to change password
                flash('You must change your temporary password before continuing.', 'info')
                return redirect(url_for('auth.employee_change_password'))
            else:
                flash('Invalid Password', 'danger')
                return render_template('login.html')

        else:
            flash('Invalid role selected', 'danger')
            return render_template('login.html')

    return render_template('login.html')


@auth_bp.route('/logout')
def logout():
    """Unified logout for both Admin and Employee"""
    session.clear()
    # Land back on the public kiosk attendance page rather than the login
    # form, consistent with the attendance page being the app's default
    # entry point.
    return redirect(url_for('attendance.index'))


# ==================== PASSWORD RECOVERY / CHANGE (ADMIN) ====================

@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit("5 per minute, 20 per hour")
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


@auth_bp.route('/change-password', methods=['GET', 'POST'])
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
            admin = db.session.get(Admin, session['admin_id'])

            if not admin:
                flash('Admin not found', 'danger')
                return redirect(url_for('auth.login'))

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
            return redirect(url_for('attendance.dashboard'))

        elif 'employee_id' in session:
            # Employee password change using EmployeeLogin table
            employee = db.session.get(Employee, session['employee_id'])
            login_creds = EmployeeLogin.query.filter_by(employee_id=employee.id).first()

            if not employee or not login_creds:
                flash('Employee not found', 'danger')
                return redirect(url_for('auth.login'))

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
                return redirect(url_for('attendance.employee_dashboard'))

    return render_template('change_password.html')


# ==================== EMPLOYEE AUTHENTICATION ROUTES ====================

@auth_bp.route('/employee-forgot-password', methods=['GET', 'POST'])
@limiter.limit("5 per minute, 20 per hour")
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


@auth_bp.route('/employee-change-password', methods=['GET', 'POST'])
@login_required
def employee_change_password():
    """Change password for logged-in employee"""
    if 'employee_id' not in session:
        return redirect(url_for('auth.login'))

    employee_id = session['employee_id']
    login_creds = EmployeeLogin.query.filter_by(employee_id=employee_id).first()

    if not login_creds:
        flash('Login credentials not found', 'danger')
        return redirect(url_for('auth.login'))

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
                return redirect(url_for('attendance.employee_dashboard'))
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
                return redirect(url_for('attendance.employee_dashboard'))

    return render_template('change_password.html', first_login=login_creds.first_login)

# create_admin_from_request_form() is now imported from auth_helpers.py
# (see imports at top of file) so the setup wizard blueprint can reuse the
# exact same validation/creation logic without a circular import.


# ==================== ADMIN REGISTRATION ====================

@auth_bp.route('/register', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def register():
    """
    Legacy first-run admin setup URL.

    This route USED to be a permanently public, unauthenticated endpoint
    that let anyone create a new Admin account with full access to
    attendance, payroll, and employee data - a critical vulnerability.

    It now simply forwards to the guided setup wizard (blueprints/setup_wizard.py)
    while no Admin exists yet, so any old bookmarks/links to /register still
    work. Once an Admin exists, it refuses to create another one via a
    public form; further admins must be created by an already-authenticated
    admin (see /admin/create-admin below).
    """
    if Admin.query.count() > 0:
        flash('Setup has already been completed. Please contact an existing administrator for access.', 'info')
        return redirect(url_for('auth.login'))

    return redirect(url_for('setup.step1_admin'))


@auth_bp.route('/admin/create-admin', methods=['GET', 'POST'])
@admin_required
@limiter.limit("5 per minute")
def create_admin():
    """
    Create an additional admin account.

    Once initial setup is complete, /register locks itself (see above),
    so this authenticated, admin-only route is the only way to add further
    admin accounts - it reuses the exact same validation/creation logic.
    """
    if request.method == 'POST':
        admin = create_admin_from_request_form()
        if admin:
            flash(f'Admin account "{admin.username}" created successfully.', 'success')
            return redirect(url_for('settings.settings'))

    return render_template('register.html', creating_additional_admin=True)
