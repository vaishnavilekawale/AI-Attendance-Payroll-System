"""
First-run guided setup wizard.

Replaces the old bare /register form as the actual first-run onboarding
experience for a fresh install. Three steps:

  1. /setup/          - create the very first admin account, then log them in
  2. /setup/company   - basic company settings (name, office hours, etc.)
  3. /setup/done       - confirmation, links into the dashboard

This is also the first fully-migrated Blueprint in the project, and is
meant as the template other route groups in app.py (employees, attendance,
payroll, ...) get migrated into over time. Notice it imports `db`,
`admin_required`, `limiter`, and `create_admin_from_request_form` from
neutral modules (database.py / auth_decorators.py / extensions.py /
auth_helpers.py) rather than from app.py - that's what makes it possible
to register this blueprint on the `app` object without a circular import.
"""
from flask import Blueprint, render_template, request, redirect, url_for, session, flash

from database import db
from models import Admin, Settings
from auth_decorators import admin_required
from auth_helpers import create_admin_from_request_form
from extensions import limiter

setup_bp = Blueprint('setup', __name__, url_prefix='/setup')


@setup_bp.route('/', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def step1_admin():
    """Step 1: create the first admin account (only reachable pre-setup)."""
    if Admin.query.count() > 0:
        flash('Setup has already been completed. Please contact an existing administrator for access.', 'info')
        return redirect(url_for('login'))

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

        flash('Company settings saved.', 'success')
        return redirect(url_for('setup.step3_done'))

    return render_template('setup_wizard.html', step=2, settings=settings)


@setup_bp.route('/done')
@admin_required
def step3_done():
    """Step 3: confirmation screen, links into the real dashboard."""
    return render_template('setup_wizard.html', step=3)
