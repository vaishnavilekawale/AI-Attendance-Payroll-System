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
import time
from datetime import datetime, timedelta

from flask import Blueprint, Response, render_template, request, redirect, url_for, session, flash
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash

import login_security as ls
from database import db
from models import Admin, Employee, EmployeeLogin, LoginAudit, LoginLockout, now_ist
from auth_decorators import login_required, admin_required
from auth_helpers import create_admin_from_request_form
from email_service import EmailService
from extensions import limiter

auth_bp = Blueprint('auth', __name__)


# ==================== UNIFIED LOGIN / LOGOUT ====================

# One message for every kind of login failure (unknown user, wrong password,
# expired temporary password, no credentials provisioned). Separate messages
# would tell an attacker which usernames / Employee IDs actually exist.
GENERIC_LOGIN_ERRORS = {
    'admin': 'Invalid username or password.',
    'employee': 'Invalid Employee ID or password.',
}
GENERIC_LOGIN_ERROR = GENERIC_LOGIN_ERRORS['admin']

# Used to burn the same hashing time when the username does not exist, so a
# response-time difference cannot reveal valid usernames either.
_DUMMY_HASH = generate_password_hash('not-a-real-password')


def _locked_login_page(role, username):
    """
    Login page shown while this username+IP is locked. For admins it also
    offers "Unlock via email", but only when SMTP is configured. The button
    is shown for ANY locked admin username (existing or not), so it never
    reveals which usernames exist.
    """
    email_ok = False
    if role == 'admin':
        try:
            email_ok = EmailService().is_configured()
        except Exception:
            email_ok = False
    return render_template('login.html', selected_role=role,
                           unlock_username=username if email_ok else None)


def _login_failed(role, username):
    """Count the failure (may lock the username+IP) and show the right message."""
    locked_for = ls.record_failure(role, username)
    if locked_for:
        flash(f'Too many failed attempts. Try again in {locked_for} minutes.', 'danger')
        return _locked_login_page(role, username)
    flash(GENERIC_LOGIN_ERRORS.get(role, GENERIC_LOGIN_ERROR), 'danger')
    return render_template('login.html', selected_role=role)


def _begin_session(role, username):
    """
    Start a fresh login session. Clearing first prevents session fixation
    and stops an admin and an employee session from co-existing; marking it
    permanent makes PERMANENT_SESSION_LIFETIME (8 h) actually apply;
    last_active drives the inactivity timeout in app.py.
    """
    session.clear()
    session.permanent = True
    session['last_active'] = time.time()
    ls.record_success(role, username)


@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute, 50 per hour")
def login():
    """Unified login for both Admin and Employee"""
    # Keep the Admin/Employee tab the user was on after a failed attempt
    # (the page is re-rendered on POST, and the tab used to reset to Admin).
    role = request.values.get('role', 'admin')
    if role not in ('admin', 'employee'):
        role = 'admin'
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        role = request.form.get('role', 'admin')  # Default to admin if not specified

        if not username or not password:
            flash('Please provide both username and password', 'danger')
            return render_template('login.html', selected_role=role)

        if role not in ('admin', 'employee'):
            flash('Invalid role selected', 'danger')
            return render_template('login.html', selected_role=role)

        # Refuse to even check the password while this username+IP is locked.
        wait = ls.minutes_left(role, username)
        if wait:
            ls.log_event(role, username, 'blocked')
            flash(f'Too many failed attempts. Try again in {wait} minutes.', 'danger')
            return _locked_login_page(role, username)

        if role == 'admin':
            # Admin authentication
            admin = Admin.query.filter_by(username=username).first()

            if not admin:
                check_password_hash(_DUMMY_HASH, password)
                return _login_failed('admin', username)

            if admin.check_password(password):
                _begin_session('admin', username)
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
                # An expired temporary password is treated like any other
                # wrong password (same message, counts toward the lockout).
                if not admin.is_temporary_password_valid():
                    return _login_failed('admin', username)

                _begin_session('admin', username)
                session['admin_id'] = admin.id
                session['admin_username'] = admin.username
                session['user_role'] = 'admin'
                admin.last_login = now_ist()
                db.session.commit()

                # Redirect to change password
                flash('You must change your temporary password before continuing.', 'info')
                return redirect(url_for('auth.change_password'))
            else:
                return _login_failed('admin', username)

        # role == 'employee' - authentication using the EmployeeLogin table
        employee = Employee.query.filter_by(employee_id=username).first()
        login_creds = (
            EmployeeLogin.query.filter_by(employee_id=employee.id).first()
            if employee else None
        )

        # Unknown Employee ID, or no login provisioned yet: same generic
        # error as a wrong password. (This used to say "No login has been
        # set up for this Employee ID", which confirmed the ID exists.
        # Employee accounts are only ever created by the administrator with
        # a random temporary password - see employees.py.)
        if not employee or not login_creds:
            check_password_hash(_DUMMY_HASH, password)
            return _login_failed('employee', username)

        # Verify the password FIRST. The inactive-account message below is
        # only shown to someone who already proved they know the password,
        # so it cannot be used to probe which Employee IDs exist.
        used_temporary = False
        if not login_creds.check_password(password):
            if login_creds.check_temporary_password(password) \
                    and login_creds.is_temporary_password_valid():
                used_temporary = True
            else:
                return _login_failed('employee', username)

        if not login_creds.is_active:
            ls.log_event('employee', username, 'blocked')
            flash('Your account is inactive. Please contact administrator.', 'danger')
            return render_template('login.html', selected_role=role)

        _begin_session('employee', username)
        session['employee_id'] = employee.id
        session['employee_username'] = employee.employee_id
        session['user_role'] = 'employee'
        login_creds.previous_login = login_creds.last_login  # remember the login before this one
        login_creds.last_login = now_ist()
        employee.last_login = login_creds.last_login
        db.session.commit()

        if used_temporary:
            flash('You must change your temporary password before continuing.', 'info')
            return redirect(url_for('auth.employee_change_password'))

        # Check if first login or force password change - redirect to change password
        if login_creds.first_login or login_creds.force_password_change:
            flash('Please change your default password before continuing.', 'info')
            return redirect(url_for('auth.employee_change_password'))

        return redirect(url_for('attendance.employee_dashboard'))

    # Plain GET: no server override, so the page can use ?role= in the URL or
    # the tab last used in this browser tab (see login.html).
    return render_template('login.html', selected_role=request.args.get('role', ''))


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

        # Case-insensitive match (same as the employee flow): an admin who
        # registered "Name@Gmail.com" must still be found when typing
        # "name@gmail.com".
        admin = Admin.query.filter(func.lower(Admin.email) == email.lower()).first()

        if not admin:
            flash('No account found with this email address. '
                  'Please enter the email you registered with.', 'danger')
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

            # The success message travels in the URL (not the session flash),
            # so it is shown on the dashboard we land on and can never
            # reappear on some later page.
            return redirect(url_for('attendance.dashboard', pw_changed=1))

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
                login_creds.password_changed_at = now_ist()
                login_creds.clear_temporary_password()
                login_creds.first_login = False
                login_creds.force_password_change = False
                db.session.commit()
                return redirect(url_for('attendance.employee_dashboard', pw_changed=1))

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
                login_creds.password_changed_at = now_ist()
                login_creds.first_login = False
                login_creds.force_password_change = False
                login_creds.clear_temporary_password()
                db.session.commit()
                return redirect(url_for('attendance.employee_dashboard', pw_changed=1))
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
                login_creds.password_changed_at = now_ist()
                login_creds.force_password_change = False
                login_creds.clear_temporary_password()
                db.session.commit()
                return redirect(url_for('attendance.employee_dashboard', pw_changed=1))

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

# ==================== MANAGE ADMINS / UNLOCK ====================

@auth_bp.route('/admin/manage-admins')
@login_required
@admin_required
def manage_admins():
    """
    List admin accounts with their lock status. Another admin can unlock a
    locked admin from here (never themselves). With only one admin there is
    nobody who could unlock them, so a hint asks for a second admin.
    """
    admins = Admin.query.order_by(Admin.username).all()
    locks = ls.locked_until_by_username('admin')
    rows = [{'admin': a, 'locked_until': locks.get(a.username.strip().lower())} for a in admins]
    return render_template('manage_admins.html', rows=rows,
                           admin_count=len(admins),
                           current_admin_id=session.get('admin_id'))


@auth_bp.route('/admin/<int:admin_id>/unlock', methods=['POST'])
@login_required
@admin_required
def unlock_admin(admin_id):
    """Clear another admin's failed-login lock early (it also expires by itself)."""
    target = Admin.query.get_or_404(admin_id)
    if target.id == session.get('admin_id'):
        flash('You cannot unlock your own account. Ask another admin, or use '
              '"Unlock via email" on the login page.', 'warning')
        return redirect(url_for('auth.manage_admins'))
    ls.unlock('admin', target.username, by=session.get('admin_username') or 'admin')
    flash(f'Login unlocked for admin "{target.username}".', 'success')
    return redirect(url_for('auth.manage_admins'))


@auth_bp.route('/admin/unlock-request', methods=['POST'])
@limiter.limit("3 per minute, 10 per hour")
def admin_unlock_request():
    """
    "Unlock via email": send a one-time unlock link to the locked admin's
    registered email. The reply is always the same, whether or not the
    account exists, so it cannot be used to discover usernames.
    """
    username = request.form.get('username', '').strip()
    generic = ('If this admin account exists and has a registered email, an unlock link '
               f'has been sent. The link works once and expires in {ls.UNLOCK_TOKEN_MINUTES} minutes.')

    # Only for a username+IP that is really locked right now.
    if username and ls.minutes_left('admin', username):
        admin = Admin.query.filter(func.lower(Admin.username) == username.lower()).first()
        if admin and admin.email:
            raw = ls.create_unlock_token(admin)     # None => one was sent recently
            if raw:
                link = url_for('auth.admin_unlock_confirm', token=raw, _external=True)
                try:
                    result = EmailService().send_admin_unlock_link(
                        admin.email, admin.username, link, ls.UNLOCK_TOKEN_MINUTES)
                    sent = bool(result.get('success'))
                except Exception:
                    sent = False
                if not sent:
                    ls.discard_unlock_token(raw)
                    flash('Could not send the unlock email. Please wait for the lock '
                          'to expire or ask another admin to unlock you.', 'danger')
                    return redirect(url_for('auth.login'))
                ls.log_event('admin', f'{admin.username} (unlock email)', 'unlock_email')
    flash(generic, 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/admin/unlock/<token>', methods=['GET', 'POST'])
@limiter.limit("20 per minute")
def admin_unlock_confirm(token):
    """
    Landing page of the emailed link. GET only shows a confirm button (mail
    scanners often open links, which must not use up the one-time token);
    POST clears the lock. The password is never changed.
    """
    tok = ls.find_valid_unlock_token(token)
    if tok is None:
        flash('This unlock link is invalid, already used, or expired.', 'danger')
        return redirect(url_for('auth.login'))

    admin = db.session.get(Admin, tok.admin_id)
    if admin is None:
        flash('This unlock link is invalid, already used, or expired.', 'danger')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        ls.unlock('admin', admin.username, by='email link')
        ls.consume_unlock_token(tok)
        flash('Your login is unlocked. Please sign in with your password.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('admin_unlock.html', username=admin.username, token=token)


# ==================== LOGIN AUDIT (ADMIN) ====================

def _audit_filters():
    """Read the Date / Role / Event filters from the query string (shared by page + PDF)."""
    event = request.args.get('event', '').strip()
    role = request.args.get('role', '').strip()
    date_arg = request.args.get('date', '').strip()

    today = now_ist().date()
    selected = None            # None => all dates
    if date_arg != 'all':
        try:
            selected = datetime.strptime(date_arg, '%Y-%m-%d').date() if date_arg else today
        except ValueError:
            selected = today

    query = LoginAudit.query
    if selected is not None:
        day_start = datetime.combine(selected, datetime.min.time())
        query = query.filter(LoginAudit.created_at >= day_start,
                             LoginAudit.created_at < day_start + timedelta(days=1))
    if event in ('success', 'failed', 'locked', 'blocked', 'unlocked'):
        query = query.filter_by(event=event)
    else:
        event = ''
    if role in ('admin', 'employee'):
        query = query.filter_by(role=role)
    else:
        role = ''
    return query, event, role, selected, today


@auth_bp.route('/admin/login-audit')
@login_required
@admin_required
def login_audit():
    """
    Read-only view of login events and currently locked accounts.

    By default only TODAY's events are listed. Pick another day with
    ?date=YYYY-MM-DD, or show every record with ?date=all.
    The same filters can be downloaded as a PDF (see login_audit_pdf).
    """
    page = request.args.get('page', 1, type=int)
    query, event, role, selected, today = _audit_filters()

    entries = query.order_by(LoginAudit.created_at.desc(), LoginAudit.id.desc()).paginate(
        page=page, per_page=50, error_out=False
    )
    active_locks = LoginLockout.query.filter(
        LoginLockout.locked_until > now_ist()
    ).order_by(LoginLockout.locked_until.desc()).all()

    return render_template(
        'login_audit.html', entries=entries, active_locks=active_locks,
        event=event, role=role,
        date_value=selected.isoformat() if selected else '',
        date_param='all' if selected is None else selected.isoformat(),
        selected_date=selected, is_today=(selected == today),
    )


PDF_MAX_ROWS = 5000


@auth_bp.route('/admin/login-audit/pdf')
@login_required
@admin_required
def login_audit_pdf():
    """Download the login audit events matching the current filters as a PDF."""
    import io
    from services.app_services import get_services
    from models import Settings

    query, event, role, selected, _today = _audit_filters()
    total = query.count()
    rows = query.order_by(LoginAudit.created_at.desc(), LoginAudit.id.desc()).limit(PDF_MAX_ROWS).all()

    filters = {
        'Date': selected.strftime('%d %b %Y') if selected else 'All dates',
        'Role': role.capitalize() if role else 'All roles',
        'Event': event.capitalize() if event else 'All events',
    }
    # Same shared generator as every other report (company header, theme, page
    # numbers). Built in memory so audit data is never left on disk.
    _, _, _, pg = get_services()
    buf = io.BytesIO()
    pg.generate_login_audit_report(rows, filters, buf, company_settings=Settings.get_settings(),
                                   total=total, limit=PDF_MAX_ROWS,
                                   generated_by=session.get('admin_username', ''))
    pdf_bytes = buf.getvalue()
    filename = 'login_audit_%s.pdf' % (selected.isoformat() if selected else 'all')
    return Response(pdf_bytes, mimetype='application/pdf', headers={
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Cache-Control': 'no-store',
    })
