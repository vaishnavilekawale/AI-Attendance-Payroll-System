"""
Tests for services/attendance_calculator.py

Covers:
- AttendanceCalculator class
- calculate_working_hours
- recalculate_attendance
- process_auto_checkout
- calculate_attendance_with_absent
"""
import pytest
from datetime import datetime, time, timedelta, date
from unittest.mock import Mock, MagicMock, patch


@pytest.fixture
def calculator():
    from services.attendance_calculator import AttendanceCalculator
    return AttendanceCalculator()


def test_calculator_initialization():
    """Test that AttendanceCalculator initializes correctly."""
    from services.attendance_calculator import AttendanceCalculator

    calc = AttendanceCalculator()
    assert calc is not None


def test_calculate_working_hours_with_no_in_time(app_context, make_employee):
    """Test calculate_working_hours returns 0 when no IN time."""
    from services.attendance_calculator import AttendanceCalculator
    from models import Attendance
    from database import db

    employee = make_employee(employee_id='EMP001')
    attendance = Attendance(
        employee_id=employee.id,
        date=date.today(),
        in_time=None,
        out_time=None
    )

    calc = AttendanceCalculator()
    hours = calc.calculate_working_hours(attendance)

    assert hours == 0.0


def test_calculate_working_hours_with_in_out_times(app_context, make_employee):
    """Test calculate_working_hours with IN and OUT times."""
    from services.attendance_calculator import AttendanceCalculator
    from models import Attendance, AttendanceActivity
    from database import db

    employee = make_employee(employee_id='EMP001')
    attendance = Attendance(
        employee_id=employee.id,
        date=date.today(),
        in_time=datetime.combine(date.today(), time(9, 0)),
        out_time=datetime.combine(date.today(), time(17, 0))
    )

    calc = AttendanceCalculator()
    hours = calc.calculate_working_hours(attendance)

    assert hours == 8.0


def test_calculate_working_hours_with_activities(app_context, make_employee):
    """Test calculate_working_hours with multiple IN/OUT activities."""
    from services.attendance_calculator import AttendanceCalculator
    from models import Attendance, AttendanceActivity
    from database import db

    employee = make_employee(employee_id='EMP001')
    attendance = Attendance(
        employee_id=employee.id,
        date=date.today(),
        in_time=datetime.combine(date.today(), time(9, 0)),
        out_time=None
    )

    # Add activities
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
    activity3 = AttendanceActivity(
        employee_id=employee.id,
        attendance_date=date.today(),
        activity_time=time(13, 0),
        action='IN'
    )
    activity4 = AttendanceActivity(
        employee_id=employee.id,
        attendance_date=date.today(),
        activity_time=time(17, 0),
        action='OUT'
    )

    db.session.add_all([activity1, activity2, activity3, activity4])
    db.session.commit()

    calc = AttendanceCalculator()
    hours = calc.calculate_working_hours(attendance)

    # 3 hours morning + 4 hours afternoon = 7 hours
    assert hours == 7.0


def test_recalculate_attendance(app_context, make_employee):
    """Test recalculate_attendance updates attendance fields."""
    from services.attendance_calculator import AttendanceCalculator
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

    calc = AttendanceCalculator()
    calc.recalculate_attendance(attendance)

    db.session.refresh(attendance)

    assert attendance.total_hours is not None
    assert attendance.status is not None


def test_process_auto_checkout(app_context, make_employee):
    """Test process_auto_checkout sets OUT time to 23:59:59."""
    from services.attendance_calculator import AttendanceCalculator
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

    calc = AttendanceCalculator()
    calc.process_auto_checkout(attendance, commit_to_db=True)

    db.session.refresh(attendance)

    assert attendance.out_time is not None
    assert attendance.out_time.hour == 23
    assert attendance.out_time.minute == 59


def test_process_auto_checkout_calculates_hours(app_context, make_employee):
    """Test process_auto_checkout calculates working hours."""
    from services.attendance_calculator import AttendanceCalculator
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

    calc = AttendanceCalculator()
    calc.process_auto_checkout(attendance, commit_to_db=True)

    db.session.refresh(attendance)

    assert attendance.total_hours is not None
    # 9 AM to 11:59 PM = ~15 hours
    assert attendance.total_hours > 14


def test_calculate_attendance_with_absent(app_context, make_employee):
    """Test calculate_attendance_with_absent includes absent employees."""
    from attendance import AttendanceManager
    from models import Employee
    from database import db

    # Create active employees
    employee1 = make_employee(employee_id='EMP001', status='active')
    employee2 = make_employee(employee_id='EMP002', status='active')

    manager = AttendanceManager()
    records = manager.calculate_attendance_with_absent(date.today())

    # Should return records for all active employees
    assert len(records) >= 2


def test_calculate_attendance_with_absent_excludes_inactive(app_context, make_employee):
    """Test calculate_attendance_with_absent excludes inactive employees."""
    from attendance import AttendanceManager
    from models import Employee
    from database import db

    # Create active and inactive employees
    employee1 = make_employee(employee_id='EMP001', status='active')
    employee2 = make_employee(employee_id='EMP002', status='inactive')

    manager = AttendanceManager()
    records = manager.calculate_attendance_with_absent(date.today())

    # Should only include active employees
    employee_ids = [r.employee.id for r in records]
    assert employee1.id in employee_ids
    assert employee2.id not in employee_ids


def test_calculate_working_hours_cross_day(app_context, make_employee):
    """Test calculate_working_hours handles cross-day attendance."""
    from services.attendance_calculator import AttendanceCalculator
    from models import Attendance
    from database import db

    employee = make_employee(employee_id='EMP001')
    attendance = Attendance(
        employee_id=employee.id,
        date=date.today(),
        in_time=datetime.combine(date.today(), time(22, 0)),
        out_time=datetime.combine(date.today() + timedelta(days=1), time(6, 0))
    )

    calc = AttendanceCalculator()
    hours = calc.calculate_working_hours(attendance)

    # 10 PM to 6 AM next day = 8 hours
    assert hours == 8.0


def test_calculate_working_hours_rounding(app_context, make_employee):
    """Test calculate_working_hours rounds appropriately."""
    from services.attendance_calculator import AttendanceCalculator
    from models import Attendance
    from database import db

    employee = make_employee(employee_id='EMP001')
    attendance = Attendance(
        employee_id=employee.id,
        date=date.today(),
        in_time=datetime.combine(date.today(), time(9, 0)),
        out_time=datetime.combine(date.today(), time(17, 30))
    )

    calc = AttendanceCalculator()
    hours = calc.calculate_working_hours(attendance)

    # 9 AM to 5:30 PM = 8.5 hours
    assert hours == 8.5


def test_recalculate_attendance_with_late_entry(app_context, make_employee):
    """Test recalculate_attendance detects late entry."""
    from services.attendance_calculator import AttendanceCalculator
    from models import Attendance
    from database import db

    employee = make_employee(employee_id='EMP001')
    attendance = Attendance(
        employee_id=employee.id,
        date=date.today(),
        in_time=datetime.combine(date.today(), time(10, 0)),  # Late
        out_time=datetime.combine(date.today(), time(17, 0))
    )
    db.session.add(attendance)
    db.session.commit()

    calc = AttendanceCalculator()
    calc.recalculate_attendance(attendance)

    db.session.refresh(attendance)

    # Late entry should be detected based on settings
    assert attendance.late_entry is not None
