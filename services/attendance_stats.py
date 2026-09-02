"""Shared attendance statistics calculation aligned with Employee Reports."""

from datetime import date, timedelta


def is_hidden_manual_attendance(attendance_type, approval_status):
    """
    Manual (Employee ID + Password) attendance requests must remain
    completely hidden from dashboards, reports, and payroll until they are
    finalized as 'approved'.

    - 'pending'  -> not yet decided, must stay hidden (Hidden-Until-Approved Rule)
    - 'rejected' -> permanently hidden, will never reflect anywhere (No
      Dashboard Reflection Rule), even though the employee may still be
      allowed to submit a fresh retry for the same day
    - 'approved' -> visible everywhere instantly (Instant Sync Rule)

    FACE_RECOGNITION attendance is always visible (approval_status defaults
    to 'approved' and is never set to anything else for that type).
    """
    return attendance_type == 'MANUAL_PASSWORD' and approval_status in ('pending', 'rejected')


def has_rejected_approval(attendance):
    """
    Return True when this attendance record has a REJECTED logout
    approval request. REJECTED approvals are treated strictly as ABSENT
    across reports, payroll, and dashboards.
    """
    attendance_id = getattr(attendance, 'id', None)
    if attendance_id is None:
        return False
    from models import LogoutApprovalRequest
    return (
        LogoutApprovalRequest.query.filter_by(
            attendance_id=attendance_id,
            status='rejected'
        ).first() is not None
    )


def normalize_attendance_status(status):
    """
    Normalize attendance status to canonical values for reporting and payroll.

    Handles case differences and common separators (spaces, underscores, hyphens).
    """
    if not status:
        return None

    normalized = str(status).strip().lower()
    normalized = normalized.replace('-', '_').replace(' ', '_')
    while '__' in normalized:
        normalized = normalized.replace('__', '_')

    if normalized in ('present', 'presented'):
        return 'present'
    if normalized in ('half_day', 'halfday'):
        return 'half_day'
    if normalized in ('absent', 'rejected'):
        return 'absent'
    if normalized in ('pending',):
        return 'pending'
    if normalized in ('late',):
        return 'late'

    return normalized


def classify_attendance_record(attendance, office_hours=9.0, half_day_threshold=4.5):
    """
    Classify a single attendance record using Employee Reports rules.

    Rules:
    - Present: total_hours >= office_hours OR status is present/late
    - Half day: total_hours >= half_day_threshold and < office_hours OR status is half_day
    - Absent: total_hours < half_day_threshold OR no IN time / no record
    - Late: late_entry is True OR status is late (independent counter)
    - Pending manual attendance: excluded from all counts until approved

    Returns:
        tuple: (category, is_late) where category is 'present', 'half_day', 'absent',
               or None when the day should not be counted (e.g. pending/future/manual pending)
    """
    raw_status = getattr(attendance, 'status', None)
    normalized = normalize_attendance_status(raw_status)
    total_hours = float(getattr(attendance, 'total_hours', None) or 0.0)
    has_in = bool(getattr(attendance, 'in_time', None))
    is_late = bool(getattr(attendance, 'late_entry', False)) or normalized == 'late'

    # Exclude pending/rejected manual attendance from all counts - hidden
    # until approved, and never counted at all if rejected.
    attendance_type = getattr(attendance, 'attendance_type', None)
    approval_status = getattr(attendance, 'approval_status', 'approved')
    if is_hidden_manual_attendance(attendance_type, approval_status):
        return None, is_late

    if normalized in ('pending',) or raw_status == '-':
        return None, is_late

    # REJECTED logout approvals are ALWAYS treated as ABSENT
    if normalized == 'absent':
        return 'absent', is_late

    if not has_in:
        return 'absent', is_late

    if total_hours >= office_hours or normalized in ('present', 'late'):
        return 'present', is_late

    if (half_day_threshold <= total_hours < office_hours) or normalized == 'half_day':
        return 'half_day', is_late

    return 'absent', is_late


def calculate_employee_attendance_stats(
    attendance_manager,
    employee_id,
    start_date,
    end_date,
    office_hours=None,
    half_day_threshold=None,
):
    """
    Calculate attendance day counts for an employee over a date range.

    Uses the same attendance pipeline as Employee Reports
    (`calculate_attendance_with_absent`) so payroll and reports stay aligned.
    """
    from models import Settings

    settings = Settings.get_settings()
    if office_hours is None:
        office_hours = settings.working_hours_per_day
    if half_day_threshold is None:
        half_day_threshold = (
            settings.half_day_hours
            if settings.half_day_hours
            else (office_hours / 2)
        )

    # Do not count today into cumulative historical stats - today is only
    # finalized after the day ends (evaluated starting midnight / next day).
    end_date = min(end_date, date.today() - timedelta(days=1))

    present_days = 0
    absent_days = 0
    half_days = 0
    late_days = 0
    total_hours_worked = 0.0
    overtime_hours = 0.0

    current_date = start_date
    while current_date <= end_date:
        # Sunday is a weekly holiday/off day - completely excluded from attendance
        if current_date.weekday() == 6:  # 6 = Sunday
            current_date += timedelta(days=1)
            continue

        daily_records = attendance_manager.calculate_attendance_with_absent(current_date)
        employee_records = [record for record in daily_records if record.employee.id == employee_id]

        for attendance in employee_records:
            category, is_late = classify_attendance_record(
                attendance,
                office_hours=office_hours,
                half_day_threshold=half_day_threshold,
            )

            if category == 'present':
                present_days += 1
            elif category == 'half_day':
                half_days += 1
            elif category == 'absent':
                absent_days += 1

            if is_late:
                late_days += 1

            if attendance.in_time and attendance.total_hours:
                total_hours_worked += attendance.total_hours
            if attendance.overtime_hours:
                overtime_hours += attendance.overtime_hours

        current_date += timedelta(days=1)

    return {
        'present_days': present_days,
        'absent_days': absent_days,
        'half_days': half_days,
        'late_days': late_days,
        'total_hours_worked': total_hours_worked,
        'overtime_hours': overtime_hours,
    }
