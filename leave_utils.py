"""
Per-employee PAID leave (CL / SL / EL) helpers.

How it fits with the rest of the system
---------------------------------------
* A leave day is stored as one row in ``employee_leaves``.
* For THAT employee the day behaves like a Sunday/holiday: the attendance
  pipeline skips them (no Absent mark, no LOP) and payroll counts the day as a
  PAID day, so the salary is not reduced. Every other employee is untouched.
* Each leave type has a yearly quota (company default, optionally overridden
  per employee). When the quota is used up, extra days are NOT recorded as
  leave, so they stay Absent / LOP automatically.
* Sundays and company holidays are never consumed from the balance.

Like holiday_utils, the read helpers never raise: outside an app context or
before the table exists they just return "no leaves", so behaviour is exactly
what it was before this feature.
"""
from datetime import date, timedelta

from sqlalchemy.exc import SQLAlchemyError

LEAVE_TYPES = (
    ('CL', 'Casual Leave'),
    ('SL', 'Sick Leave'),
    ('EL', 'Earned Leave'),
)
LEAVE_TYPE_NAMES = dict(LEAVE_TYPES)

# Used until the admin saves a different default in Settings -> Employee Leaves.
DEFAULT_QUOTAS = {'CL': 12.0, 'SL': 6.0, 'EL': 15.0}

MAX_RANGE_DAYS = 366


# ----------------------------------------------------------------------
# Read helpers (never raise)
# ----------------------------------------------------------------------

def get_leave_dates(employee_id, start, end):
    """{date: leave_type} of one employee's leave days between start and end."""
    if end < start:
        return {}
    try:
        from database import db
        from models import EmployeeLeave
        try:
            rows = EmployeeLeave.query.filter(
                EmployeeLeave.employee_id == employee_id,
                EmployeeLeave.date >= start,
                EmployeeLeave.date <= end,
            ).all()
        except SQLAlchemyError:
            db.session.rollback()
            return {}
        return {r.date: r.leave_type for r in rows}
    except RuntimeError:  # no Flask app context
        return {}


def get_employees_on_leave(d):
    """Set of employee ids that have a paid leave on date d."""
    try:
        from database import db
        from models import EmployeeLeave
        try:
            rows = EmployeeLeave.query.filter(EmployeeLeave.date == d).all()
        except SQLAlchemyError:
            db.session.rollback()
            return set()
        return {r.employee_id for r in rows}
    except RuntimeError:
        return set()


# ----------------------------------------------------------------------
# Quota / balance
# ----------------------------------------------------------------------

def get_default_quotas():
    """{type: days_per_year} company defaults (saved values, else built-ins)."""
    from models import LeaveQuota
    quotas = dict(DEFAULT_QUOTAS)
    for row in LeaveQuota.query.filter(LeaveQuota.employee_id.is_(None)).all():
        if row.leave_type in quotas:
            quotas[row.leave_type] = float(row.days_per_year or 0.0)
    return quotas


def get_employee_overrides(employee_id):
    """{type: days_per_year} overrides saved for one employee (may be empty)."""
    from models import LeaveQuota
    return {
        r.leave_type: float(r.days_per_year or 0.0)
        for r in LeaveQuota.query.filter(LeaveQuota.employee_id == employee_id).all()
        if r.leave_type in LEAVE_TYPE_NAMES
    }


def get_quota(employee_id, leave_type, defaults=None, overrides=None):
    overrides = get_employee_overrides(employee_id) if overrides is None else overrides
    if leave_type in overrides:
        return overrides[leave_type]
    defaults = get_default_quotas() if defaults is None else defaults
    return defaults.get(leave_type, 0.0)


def get_used_days(employee_id, year):
    """{type: days} of leave already recorded for the calendar year."""
    from models import EmployeeLeave
    used = {code: 0.0 for code, _ in LEAVE_TYPES}
    rows = EmployeeLeave.query.filter(
        EmployeeLeave.employee_id == employee_id,
        EmployeeLeave.date >= date(year, 1, 1),
        EmployeeLeave.date <= date(year, 12, 31),
    ).all()
    for r in rows:
        if r.leave_type in used:
            used[r.leave_type] += 1.0
    return used


def get_balance(employee_id, year, defaults=None):
    """{type: {'quota', 'used', 'balance'}} for one employee and year."""
    defaults = get_default_quotas() if defaults is None else defaults
    overrides = get_employee_overrides(employee_id)
    used = get_used_days(employee_id, year)
    out = {}
    for code, _ in LEAVE_TYPES:
        quota = get_quota(employee_id, code, defaults, overrides)
        out[code] = {
            'quota': quota,
            'used': used[code],
            'balance': max(0.0, quota - used[code]),
            'has_override': code in overrides,
        }
    return out


# ----------------------------------------------------------------------
# Granting leave
# ----------------------------------------------------------------------

def grant_leave(employee, start, end, leave_type, reason='', admin_id=None):
    """Record paid leave for ``employee`` on every working day in start..end.

    Returns a dict describing what happened (nothing is committed - the caller
    commits):
        granted        dates saved as paid leave
        off_days       Sundays / holidays skipped (they are already paid off days)
        already        dates that already had a leave
        before_joining dates before the employee joined
        over_quota     dates NOT saved because the yearly quota of this type is
                       used up (they stay Absent / LOP)
    """
    from database import db
    from models import EmployeeLeave
    from holiday_utils import get_holidays, is_off_day

    result = {'granted': [], 'off_days': [], 'already': [],
              'before_joining': [], 'over_quota': []}

    if leave_type not in LEAVE_TYPE_NAMES:
        raise ValueError('Invalid leave type')
    if end < start:
        raise ValueError('End date is before start date')
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise ValueError(f'A single entry can cover at most {MAX_RANGE_DAYS} days')

    holidays = get_holidays(start, end)
    existing = get_leave_dates(employee.id, start, end)
    defaults = get_default_quotas()
    overrides = get_employee_overrides(employee.id)
    used_by_year = {}

    d = start
    while d <= end:
        if employee.joining_date and d < employee.joining_date:
            result['before_joining'].append(d)
        elif is_off_day(d, holidays):
            result['off_days'].append(d)
        elif d in existing:
            result['already'].append(d)
        else:
            if d.year not in used_by_year:
                used_by_year[d.year] = get_used_days(employee.id, d.year)
            quota = get_quota(employee.id, leave_type, defaults, overrides)
            if used_by_year[d.year][leave_type] + 1 > quota + 1e-9:
                result['over_quota'].append(d)
            else:
                db.session.add(EmployeeLeave(
                    employee_id=employee.id, date=d, leave_type=leave_type,
                    reason=(reason or '').strip()[:200] or None, created_by=admin_id))
                used_by_year[d.year][leave_type] += 1
                result['granted'].append(d)
        d += timedelta(days=1)
    return result


# ----------------------------------------------------------------------
# Leave requests (employee applies, admin decides)
# ----------------------------------------------------------------------

REQUEST_MAX_DAYS = 60          # one request can span at most this many days
REQUEST_MAX_PAST_DAYS = 30     # an employee may apply for dates up to this far back
REQUEST_MAX_FUTURE_DAYS = 365


def leave_working_days(employee, start, end):
    """Dates in start..end that a leave would really use: not a Sunday/holiday,
    not before joining and not already on leave."""
    from holiday_utils import get_holidays, is_off_day
    if end < start:
        return []
    holidays = get_holidays(start, end)
    existing = get_leave_dates(employee.id, start, end)
    days = []
    d = start
    while d <= end:
        if (not (employee.joining_date and d < employee.joining_date)
                and not is_off_day(d, holidays) and d not in existing):
            days.append(d)
        d += timedelta(days=1)
    return days


def validate_leave_request(employee, leave_type, start, end, today):
    """Checks for an employee's application. Returns (error_message, working_days)."""
    from models import LeaveRequest
    if leave_type not in LEAVE_TYPE_NAMES:
        return 'Please select a leave type.', []
    if start is None:
        return 'Please select the leave date.', []
    end = end or start
    if end < start:
        return 'The "To" date cannot be before the "From" date.', []
    if (end - start).days + 1 > REQUEST_MAX_DAYS:
        return f'One request can cover at most {REQUEST_MAX_DAYS} days.', []
    if start < today - timedelta(days=REQUEST_MAX_PAST_DAYS):
        return (f'You can apply for dates up to {REQUEST_MAX_PAST_DAYS} days back. '
                'For older dates please ask the admin.'), []
    if end > today + timedelta(days=REQUEST_MAX_FUTURE_DAYS):
        return 'That date is too far in the future.', []

    days = leave_working_days(employee, start, end)
    if not days:
        return ('There is no working day in that range (Sundays, holidays and days that '
                'already have leave are skipped).'), []

    clash = LeaveRequest.query.filter(
        LeaveRequest.employee_id == employee.id, LeaveRequest.status == 'pending',
        LeaveRequest.from_date <= end, LeaveRequest.to_date >= start).first()
    if clash:
        return 'You already have a pending request for these dates.', []

    # Balance check per calendar year (a request may cross new year).
    balances = {}
    for d in days:
        balances.setdefault(d.year, 0)
        balances[d.year] += 1
    for year, needed in balances.items():
        left = get_balance(employee.id, year)[leave_type]['balance']
        if needed > left + 1e-9:
            return (f'You need {needed} day(s) of {LEAVE_TYPE_NAMES[leave_type]} but only '
                    f'{left:g} left for {year}. Choose another leave type or fewer days.'), []
    return None, days
