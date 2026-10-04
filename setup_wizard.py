"""
First-run guided setup wizard.

Replaces the old bare /register form as the actual first-run onboarding
experience for a fresh install. Four steps:

  1. /setup/          - create the very first admin account, then log them in
  2. /setup/company   - basic company settings (name, office hours, etc.)
  3. /setup/license   - license activation (optional, can skip for trial)
  4. /setup/done       - confirmation, links into the dashboard

This is also the first fully-migrated Blueprint in the project, and is
meant as the template other route groups in app.py (employees, attendance,
payroll, ...) get migrated into over time. Notice it imports `db`,
`admin_required`, `limiter`, and `create_admin_from_request_form` from
neutral modules (database.py / auth_decorators.py / extensions.py /
auth_helpers.py) rather than from app.py - that's what makes it possible
to register this blueprint on the `app` object without a circular import.
"""
import os
import secrets
import logging
from flask import Blueprint, render_template, request, redirect, url_for, session, flash

from database import db
from models import Admin, Settings
from auth_decorators import admin_required
from auth_helpers import create_admin_from_request_form
from extensions import limiter
from licensing.license_manager import get_license_manager
from config import BASE_DIR

logger = logging.getLogger(__name__)
setup_bp = Blueprint('setup', __name__, url_prefix='/setup')


def ensure_secret_key():
    """
    Ensure SECRET_KEY exists in .env file.
    
    This function checks if a .env file exists and whether it contains a SECRET_KEY.
    If the .env file doesn't exist or doesn't have SECRET_KEY, it generates a secure
    random 32-byte hex key using the secrets module and appends it to the .env file.
    It also creates a setup_complete.flag file to track that this initialization has occurred.
    
    This should be called during the first installation to ensure each installation has
    a unique SECRET_KEY, which is critical for session security and CSRF protection.
    
    Returns:
        bool: True if SECRET_KEY was newly generated, False if it already existed.
    """
    env_path = os.path.join(BASE_DIR, '.env')
    flag_path = os.path.join(BASE_DIR, 'setup_complete.flag')
    
    # Check if setup has already been completed
    if os.path.exists(flag_path):
        logger.info("Setup already completed (flag file exists)")
        return False
    
    # Check if .env file exists
    env_exists = os.path.exists(env_path)
    
    # Check if SECRET_KEY already exists in .env
    secret_key_exists = False
    if env_exists:
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('SECRET_KEY='):
                    secret_key_exists = True
                    break
    
    # If SECRET_KEY doesn't exist, generate and append it
    if not secret_key_exists:
        # Generate secure 32-byte hex key (64 hex characters)
        secret_key = secrets.token_hex(32)
        
        # Ensure .env file exists
        if not env_exists:
            with open(env_path, 'w') as f:
                f.write(f'# Auto-generated SECRET_KEY for this installation\n')
                f.write(f'# DO NOT share this key or change it after setup\n')
                f.write(f'SECRET_KEY={secret_key}\n')
            logger.info(f"Created new .env file with generated SECRET_KEY")
        else:
            # Append to existing .env file
            with open(env_path, 'a') as f:
                f.write(f'\n# Auto-generated SECRET_KEY for this installation\n')
                f.write(f'# DO NOT share this key or change it after setup\n')
                f.write(f'SECRET_KEY={secret_key}\n')
            logger.info(f"Appended generated SECRET_KEY to existing .env file")
        
        # Create setup_complete.flag to prevent re-running
        with open(flag_path, 'w') as f:
            f.write(f'Setup completed on: {secrets.token_hex(8)}\n')
        logger.info(f"Created setup_complete.flag")
        
        return True
    else:
        logger.info("SECRET_KEY alredy exists in .env file")
        # Create flag file anyway to mark setup as complete
        if not os.path.exists(flag_path):
            with open(flag_path, 'w') as f:
                f.write(f'Setup completed on: {secrets.token_hex(8)}\n')
        return False


@setup_bp.route('/', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def step1_admin():
    """Step 1: create the first admin account (only reachable pre-setup)."""
    if Admin.query.count() > 0:
        flash('Setup has already been completed. Please contact an existing administrator for access.', 'info')
        return redirect(url_for('auth.login'))

    # Ensure SECRET_KEY exists in .env file for this installation
    ensure_secret_key()

    if request.method == 'POST':
        admin = create_admin_from_request_form()
        if admin:
            # Log the freshly created admin straight in, matching the
            # session convention used by the normal /login route, so they
            # can move on to step 2 without having to log in separately.
            session['admin_id'] = admin.id
            flash(f'Welcome, {admin.username}! Your admin account is ready.', 'success')
            return redirect(url_for('setup.step2_company'))

    return render_template('setup_wizard.html', step=1, admin_count=0)


@setup_bp.route('/company', methods=['GET', 'POST'])
@admin_required
def step2_company():
    """Step 2: basic company settings, pre-filled with sensible defaults."""
    settings = Settings.get_settings()

    if request.method == 'POST':
        company_name = (request.form.get('company_name') or '').strip()
        office_start_time = request.form.get('office_start_time') or settings.office_start_time
        office_end_time = request.form.get('office_end_time') or settings.office_end_time

        if not company_name:
            flash('Company name is required.', 'danger')
            return render_template('setup_wizard.html', step=2, settings=settings)

        try:
            grace_period_minutes = int(request.form.get('grace_period_minutes', settings.grace_period_minutes))
            working_hours_per_day = float(request.form.get('working_hours_per_day', settings.working_hours_per_day))
        except ValueError:
            flash('Grace period and working hours must be numbers.', 'danger')
            return render_template('setup_wizard.html', step=2, settings=settings)

        settings.company_name = company_name
        settings.office_start_time = office_start_time
        settings.office_end_time = office_end_time
        settings.grace_period_minutes = grace_period_minutes
        settings.working_hours_per_day = working_hours_per_day
        settings.half_day_hours = working_hours_per_day / 2
        db.session.commit()
        
        # Handle SMTP configuration if provided
        smtp_server = request.form.get('smtp_server', '').strip()
        smtp_port = request.form.get('smtp_port', '').strip()
        smtp_use_tls = request.form.get('smtp_use_tls', 'true').strip()
        smtp_username = request.form.get('smtp_username', '').strip()
        smtp_password = request.form.get('smtp_password', '').strip()
        
        if smtp_server and smtp_username:
            # Update .env file with SMTP settings
            env_path = os.path.join(BASE_DIR, '.env')
            
            # Read existing .env file
            env_vars = {}
            if os.path.exists(env_path):
                with open(env_path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            env_vars[key.strip()] = value.strip()
            
            # Update SMTP settings
            env_vars['SMTP_SERVER'] = smtp_server
            if smtp_port:
                env_vars['SMTP_PORT'] = smtp_port
            env_vars['SMTP_USE_TLS'] = smtp_use_tls
            env_vars['SMTP_USERNAME'] = smtp_username
            if smtp_password:
                env_vars['SMTP_PASSWORD'] = smtp_password
            
            # Write back to .env file
            with open(env_path, 'w') as f:
                for key, value in env_vars.items():
                    f.write(f'{key}={value}\n')
            
            flash('SMTP configuration saved.', 'success')

        flash('Company settings saved.', 'success')
        return redirect(url_for('setup.step3_license'))

    return render_template('setup_wizard.html', step=2, settings=settings)


@setup_bp.route('/license', methods=['GET', 'POST'])
@admin_required
def step3_license():
    """Step 3: license activation (optional, can skip for trial)."""
    license_manager = get_license_manager()
    machine_fingerprint = license_manager.machine_fingerprint
    
    if request.method == 'POST':
        # Signed Ed25519 license token (long, case-sensitive) - whitespace from
        # an email copy/paste is stripped, the case is NOT touched.
        license_key = ''.join(request.form.get('license_key', '').split()).strip('"\'')

        if not license_key:
            # Skip license activation, continue with trial
            flash('Continuing with 30-day trial period.', 'info')
            return redirect(url_for('setup.step4_done'))

        is_valid, message = license_manager.activate_license_from_string(license_key)

        if is_valid:
            from licensing.client_security import invalidate_access_state
            invalidate_access_state()
            flash('License activated successfully!', 'success')
            return redirect(url_for('setup.step4_done'))
        else:
            flash(f'License validation failed: {message}', 'danger')
            return render_template('setup_wizard.html', step=3, machine_fingerprint=machine_fingerprint)

    return render_template('setup_wizard.html', step=3, machine_fingerprint=machine_fingerprint)


@setup_bp.route('/done')
@admin_required
def step4_done():
    """Step 4: confirmation screen, links into the real dashboard."""
    return render_template('setup_wizard.html', step=4)
