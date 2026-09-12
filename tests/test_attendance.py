"""
Tests for attendance.py

Covers:
- auto_checkout_pending_attendance
- AttendanceManager class
- mark_attendance
- mark_in
- mark_out
- mark_additional_activity
- recalculate_all_attendance
"""
import pytest
from datetime import datetime, time, date, timedelta
from unittest.mock import Mock, patch, MagicMock


def test_auto_checkout_pending_attendance_no_records(app_context):
    """Test auto_checkout_pending_attendance when no pending records."""
    from attendance import auto_checkout_pending_attendance

    # Should not raise error when no records
    auto_checkout_pending_attendance()


def test_auto_checkout_pending_attendance_with_records(app_context, make_employee):
    """Test auto_checkout_pending_attendance processes pending records."""
    from attendance import auto_checkout_pending_attendance
    from models import Attendance, AttendanceActivity
    from database import db

    employee = make_employee(employee_id='EMP001')
    attendance = Attendance(
        employee_id=employee.id,
        date=date.today(),
        in_time=datetime.combine(date.today(), time(9, 0)),
        out_time=None
    )
    db.session.add(attendance)
    db.session.commit()

    # Add IN activity
    activity = AttendanceActivity(
        employee_id=employee.id,
        attendance_date=date.today(),
        activity_time=time(9, 0),
        action='IN'
    )
    db.session.add(activity)
    db.session.commit()

    # Process auto checkout
    auto_checkout_pending_attendance()

    db.session.refresh(attendance)
    assert attendance.out_time is not None


def test_auto_checkout_pending_attendance_skips_future_dates(app_context, make_employee):
    """Test auto_checkout_pending_attendance skips future dates."""
    from attendance import auto_checkout_pending_attendance
    from models import Attendance
    from database import db

    employee = make_employee(employee_id='EMP001')
    future_date = date.today() + timedelta(days=1)
    attendance = Attendance(
        employee_id=employee.id,
        date=future_date,
        in_time=datetime.combine(future_date, time(9, 0)),
        out_time=None
    )
    db.session.add(attendance)
    db.session.commit()

    # Process auto checkout
    auto_checkout_pending_attendance()

    db.session.refresh(attendance)
    # Future dates should be skipped
    assert attendance.out_time is None


def test_auto_checkout_pending_attendance_skips_checked_out(app_context, make_employee):
    """Test auto_checkout_pending_attendance skips already checked out."""
    from attendance import auto_checkout_pending_attendance
    from models import Attendance, AttendanceActivity
    from database import db

    employee = make_employee(employee_id='EMP001')
    attendance = Attendance(
        employee_id=employee.id,
        date=date.today(),
        in_time=datetime.combine(date.today(), time(9, 0)),
        out_time=datetime.combine(date.today(), time(17, 0))
    )
    db.session.add(attendance)
    db.session.commit()

    # Add OUT activity
    activity = AttendanceActivity(
        employee_id=employee.id,
        attendance_date=date.today(),
        activity_time=time(17, 0),
        action='OUT'
    )
    db.session.add(activity)
    db.session.commit()

    # Process auto checkout
    auto_checkout_pending_attendance()

    db.session.refresh(attendance)
    # Already checked out, should not change
    assert attendance.out_time.hour == 17


def test_attendance_manager_initialization():
    """Test AttendanceManager initialization."""
    from attendance import AttendanceManager

    manager = AttendanceManager()
    assert manager is not None
    assert manager.calculator is not None


def test_mark_attendance_first_in(app_context, make_employee):
    """Test mark_attendance for first IN of the day."""
    from attendance import AttendanceManager
    from models import Attendance
    from database import db

    employee = make_employee(employee_id='EMP001')

    manager = AttendanceManager()
    result = manager.mark_attendance(employee.id, confidence=0.95)

    assert result['success'] == True

    attendance = Attendance.query.filter_by(
        employee_id=employee.id,
        date=date.today()
    ).first()
    assert attendance is not None
    assert attendance.in_time is not None


def test_mark_attendance_out_after_in(app_context, make_employee):
    """Test mark_attendance for OUT after IN."""
    from attendance import AttendanceManager
    from models import Attendance
    from database import db

    employee = make_employee(employee_id='EMP001')

    # First mark IN
    manager = AttendanceManager()
    manager.mark_attendance(employee.id, confidence=0.95)

    # Then mark OUT
    result = manager.mark_attendance(employee.id, confidence=0.95)

    assert result['success'] == True

    attendance = Attendance.query.filter_by(
        employee_id=employee.id,
        date=date.today()
    ).first()
    assert attendance.out_time is not None


def test_mark_attendance_employee_not_found(app_context):
    """Test mark_attendance when employee not found."""
    from attendance import AttendanceManager

    manager = AttendanceManager()
    result = manager.mark_attendance(99999, confidence=0.95)

    assert result['success'] == False
    assert 'not found' in result['message'].lower()


def test_mark_in_success(app_context, make_employee):
    """Test mark_in creates attendance record."""
    from attendance import AttendanceManager
    from models import Attendance
    from database import db

    employee = make_employee(employee_id='EMP001')

    manager = AttendanceManager()
    result = manager.mark_in(employee, confidence=0.95)

    assert result['success'] == True

    attendance = Attendance.query.filter_by(
        employee_id=employee.id,
        date=date.today()
    ).first()
    assert attendance is not None


def test_mark_out_success(app_context, make_employee):
    """Test mark_out updates attendance record."""
    from attendance import AttendanceManager
    from models import Attendance, AttendanceActivity
    from database import db

    employee = make_employee(employee_id='EMP001')

    # First mark IN
    attendance = Attendance(
        employee_id=employee.id,
        date=date.today(),
        in_time=datetime.combine(date.today(), time(9, 0)),
        out_time=None
    )
    db.session.add(attendance)
    db.session.commit()

    # Add IN activity
    activity = AttendanceActivity(
        employee_id=employee.id,
        attendance_date=date.today(),
        activity_time=time(9, 0),
        action='IN'
    )
    db.session.add(activity)
    db.session.commit()

    # Mark OUT
    manager = AttendanceManager()
    result = manager.mark_out(attendance, confidence=0.95)

    assert result['success'] == True

    db.session.refresh(attendance)
    assert attendance.out_time is not None


def test_mark_additional_activity(app_context, make_employee):
    """Test mark_additional_activity for unlimited IN/OUT."""
    from attendance import AttendanceManager
    from models import Attendance, AttendanceActivity
    from database import db

    employee = make_employee(employee_id='EMP001')

    # Create completed first cycle
    attendance = Attendance(
        employee_id=employee.id,
        date=date.today(),
        in_time=datetime.combine(date.today(), time(9, 0)),
        out_time=datetime.combine(date.today(), time(12, 0))
    )
    db.session.add(attendance)
    db.session.commit()

    # Add activities for first cycle
    activity1 = AttendanceActivity(
        employee_id=employee.id,
        attendance_date=date.today(),
        activity_time=time(9, 0),
        action='IN'
    )
    activity2 = AttendanceActivity(
        employee_id=employee.id,
        attendance_date=date.today(),
        activity_time=time(12, 0),
        action='OUT'
    )
    db.session.add_all([activity1, activity2])
    db.session.commit()

    # Mark additional IN
    manager = AttendanceManager()
    result = manager.mark_additional_activity(attendance, confidence=0.95)

    assert result['success'] == True

    # Should have additional activity
    activities = AttendanceActivity.query.filter_by(
        employee_id=employee.id,
        attendance_date=date.today()
    ).all()
    assert len(activities) >= 3


def test_recalculate_all_attendance(app_context, make_employee):
    """Test recalculate_all_attendance updates all records."""
    from attendance import AttendanceManager
    from models import Attendance
    from database import db

    employee = make_employee(employee_id='EMP001')

    # Create attendance records
    attendance1 = Attendance(
        employee_id=employee.id,
        date=date.today() - timedelta(days=1),
        in_time=datetime.combine(date.today() - timedelta(days=1), time(9, 0)),
        out_time=datetime.combine(date.today() - timedelta(days=1), time(17, 0))
    )
    attendance2 = Attendance(
        employee_id=employee.id,
        date=date.today() - timedelta(days=2),
        in_time=datetime.combine(date.today() - timedelta(days=2), time(9, 0)),
        out_time=datetime.combine(date.today() - timedelta(days=2), time(17, 0))
    )
    db.session.add_all([attendance1, attendance2])
    db.session.commit()

    # Recalculate
    manager = AttendanceManager()
    manager.recalculate_all_attendance()

    # Verify calculations were updated
    db.session.refresh(attendance1)
    db.session.refresh(attendance2)
    assert attendance1.total_hours is not None
    assert attendance2.total_hours is not None


def test_recalculate_all_attendance_skips_no_in_time(app_context, make_employee):
    """Test recalculate_all_attendance skips records without IN time."""
    from attendance import AttendanceManager
    from models import Attendance
    from database import db

    employee = make_employee(employee_id='EMP001')

    # Create attendance without IN time
    attendance = Attendance(
        employee_id=employee.id,
        date=date.today(),
        in_time=None,
        out_time=None
    )
    db.session.add(attendance)
    db.session.commit()

    # Recalculate - should not raise error
    manager = AttendanceManager()
    manager.recalculate_all_attendance()


def test_apply_auto_checkout(app_context, make_employee):
    """Test apply_auto_checkout delegates to calculator."""
    from attendance import AttendanceManager
    from models import Attendance
    from database import db

    employee = make_employee(employee_id='EMP001')
    attendance = Attendance(
        employee_id=employee.id,
        date=date.today(),
        in_time=datetime.combine(date.today(), time(9, 0)),
        out_time=None
    )
    db.session.add(attendance)
    db.session.commit()

    manager = AttendanceManager()
    result = manager.apply_auto_checkout(attendance, commit_to_db=True)

    db.session.refresh(attendance)
    assert attendance.out_time is not None


def test_mark_attendance_invalid_state(app_context, make_employee):
    """Test mark_attendance handles invalid attendance state."""
    from attendance import AttendanceManager
    from models import Attendance
    from database import db

    employee = make_employee(employee_id='EMP001')

    # Create attendance with only OUT time (invalid state)
    attendance = Attendance(
        employee_id=employee.id,
        date=date.today(),
        in_time=None,
        out_time=datetime.combine(date.today(), time(17, 0))
    )
    db.session.add(attendance)
    db.session.commit()

    manager = AttendanceManager()
    result = manager.mark_attendance(employee.id, confidence=0.95)

    assert result['success'] == False
