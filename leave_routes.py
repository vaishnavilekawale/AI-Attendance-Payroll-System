"""
Employee self-service for paid leave: see balance, apply, cancel a pending
request and see leaves taken. The ADMIN approves or rejects requests on
Settings -> Employee Leaves (settings_routes.py); only an approved request
creates leave days (leave_utils.grant_leave), so nothing about attendance or
payroll changes until then.
"""
from datetime import date, datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from auth_decorators import login_required, employee_required
from database import db
from models import Employee, EmployeeLeave, LeaveRequest, now_ist
from leave_utils import LEAVE_TYPES, LEAVE_TYPE_NAMES, get_balance, validate_leave_request

leave_bp = Blueprint('leave', __name__)


def _parse(raw):
    try:
        return datetime.strptime((raw or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def _year(raw):
    try:
        y = int(raw)
        if 2000 <= y <= 2100:
            return y
    except (TypeError, ValueError):
        pass
    return now_ist().year


@leave_bp.route('/my-leaves')
@login_required
@employee_required
def my_leaves():
    employee = db.session.get(Employee, session['employee_id'])
    year = _year(request.args.get('year'))
    balance = get_balance(employee.id, year)

    requests_ = (LeaveRequest.query.filter_by(employee_id=employee.id)
                 .order_by(LeaveRequest.created_at.desc(), LeaveRequest.id.desc()).limit(50).all())
    taken = (EmployeeLeave.query.filter(
                EmployeeLeave.employee_id == employee.id,
                EmployeeLeave.date >= date(year, 1, 1), EmployeeLeave.date <= date(year, 12, 31))
             .order_by(EmployeeLeave.date.desc()).all())
    years = sorted({year, now_ist().year} | {r.date.year for r in
                    EmployeeLeave.query.filter_by(employee_id=employee.id).all()}, reverse=True)
    return render_template('my_leaves.html', employee=employee, current_user=employee,
                           balance=balance, leave_types=LEAVE_TYPES, requests=requests_,
                           taken=taken, year=year, years=years,
                           today_iso=now_ist().date().isoformat())


@leave_bp.route('/my-leaves/apply', methods=['POST'])
@login_required
@employee_required
def apply_leave():
    employee = db.session.get(Employee, session['employee_id'])
    leave_type = (request.form.get('leave_type') or '').upper()
    start = _parse(request.form.get('from_date'))
    end = _parse(request.form.get('to_date')) or start
    reason = (request.form.get('reason') or '').strip()[:200]

    # One shared rule set (leave_utils.validate_leave_request): dates, limits,
    # Sundays/holidays skipped, no duplicate pending request, enough balance.
    error, days = validate_leave_request(employee, leave_type, start, end, now_ist().date())
    if error:
        flash(error, 'danger')
        return redirect(url_for('leave.my_leaves'))

    db.session.add(LeaveRequest(employee_id=employee.id, leave_type=leave_type,
                                from_date=start, to_date=end, working_days=len(days),
                                reason=reason or None))
    db.session.commit()
    flash(f"Leave request sent: {len(days)} working day(s) of {LEAVE_TYPE_NAMES[leave_type]}. "
          f"It counts only after your admin approves it.", 'success')
    return redirect(url_for('leave.my_leaves'))


@leave_bp.route('/my-leaves/<int:request_id>/cancel', methods=['POST'])
@login_required
@employee_required
def cancel_leave_request(request_id):
    req = db.session.get(LeaveRequest, request_id)
    if not req or req.employee_id != session['employee_id']:
        flash('Request not found.', 'danger')
    elif req.status != 'pending':
        flash('Only a pending request can be cancelled. Please ask your admin.', 'warning')
    else:
        req.status = 'cancelled'
        req.decided_at = now_ist()
        db.session.commit()
        flash('Leave request cancelled.', 'success')
    return redirect(url_for('leave.my_leaves'))
