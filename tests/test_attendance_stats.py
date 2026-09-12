"""
Tests for services/attendance_stats.py

Covers:
- is_hidden_manual_attendance
- has_rejected_approval
- normalize_attendance_status
- classify_attendance_record
- calculate_employee_attendance_stats
"""
import pytest
from datetime import date, timedelta
from unittest.mock import Mock, MagicMock


def test_is_hidden_manual_attendance_pending():
    """Test that pending manual attendance is hidden."""
    from services.attendance_stats import is_hidden_manual_attendance

    result = is_hidden_manual_attendance('MANUAL_PASSWORD', 'pending')
    assert result == True


def test_is_hidden_manual_attendance_rejected():
    """Test that rejected manual attendance is hidden."""
    from services.attendance_stats import is_hidden_manual_attendance

    result = is_hidden_manual_attendance('MANUAL_PASSWORD', 'rejected')
    assert result == True


def test_is_hidden_manual_attendance_approved():
    """Test that approved manual attendance is visible."""
    from services.attendance_stats import is_hidden_manual_attendance

    result = is_hidden_manual_attendance('MANUAL_PASSWORD', 'approved')
    assert result == False


def test_is_hidden_manual_attendance_face_recognition():
    """Test that FACE_RECOGNITION attendance is always visible."""
    from services.attendance_stats import is_hidden_manual_attendance

    result = is_hidden_manual_attendance('FACE_RECOGNITION', 'pending')
    assert result == False


def test_has_rejected_approval_with_rejected(app_context, make_employee):
    """Test that has_rejected_approval returns True when rejected."""
    from services.attendance_stats import has_rejected_approval
    from models import Attendance, LogoutApprovalRequest
    from database import db

    employee = make_employee(employee_id='EMP001')
    attendance = Attendance(
        employee_id=employee.id,
        date=date.today(),
        in_time=None,
        out_time=None
    )
    db.session.add(attendance)
    db.session.commit()

    # Create rejected approval request with required fields
    approval = LogoutApprovalRequest(
        attendance_id=attendance.id,
        employee_id=employee.id,
        manager_id=employee.id,  # Required field
        status='rejected'
    )
    db.session.add(approval)
    db.session.commit()

    result = has_rejected_approval(attendance)
    assert result == True


def test_has_rejected_approval_without_rejected(app_context, make_employee):
    """Test that has_rejected_approval returns False when not rejected."""
    from services.attendance_stats import has_rejected_approval
    from models import Attendance
    from database import db

    employee = make_employee(employee_id='EMP001')
    attendance = Attendance(
        employee_id=employee.id,
        date=date.today(),
        in_time=None,
        out_time=None
    )
    db.session.add(attendance)
    db.session.commit()

    result = has_rejected_approval(attendance)
    assert result == False


def test_has_rejected_approval_no_id():
    """Test that has_rejected_approval returns False when attendance has no id."""
    from services.attendance_stats import has_rejected_approval

    attendance = Mock()
    attendance.id = None

    result = has_rejected_approval(attendance)
    assert result == False


def test_normalize_attendance_status_present():
    """Test normalization of present status."""
    from services.attendance_stats import normalize_attendance_status

    assert normalize_attendance_status('present') == 'present'
    assert normalize_attendance_status('Present') == 'present'
    assert normalize_attendance_status('PRESENT') == 'present'
    assert normalize_attendance_status('presented') == 'present'


def test_normalize_attendance_status_half_day():
    """Test normalization of half_day status."""
    from services.attendance_stats import normalize_attendance_status

    assert normalize_attendance_status('half_day') == 'half_day'
    assert normalize_attendance_status('halfday') == 'half_day'
    assert normalize_attendance_status('Half Day') == 'half_day'


def test_normalize_attendance_status_absent():
    """Test normalization of absent status."""
    from services.attendance_stats import normalize_attendance_status

    assert normalize_attendance_status('absent') == 'absent'
    assert normalize_attendance_status('rejected') == 'absent'


def test_normalize_attendance_status_late():
    """Test normalization of late status."""
    from services.attendance_stats import normalize_attendance_status

    assert normalize_attendance_status('late') == 'late'


def test_normalize_attendance_status_none():
    """Test normalization of None status."""
    from services.attendance_stats import normalize_attendance_status

    assert normalize_attendance_status(None) is None
    assert normalize_attendance_status('') is None


def test_normalize_attendance_status_separators():
    """Test normalization with various separators."""
    from services.attendance_stats import normalize_attendance_status

    assert normalize_attendance_status('half-day') == 'half_day'
    assert normalize_attendance_status('half day') == 'half_day'
    assert normalize_attendance_status('half__day') == 'half_day'


def test_classify_attendance_record_present():
    """Test classification of present attendance."""
    from services.attendance_stats import classify_attendance_record

    attendance = Mock()
    attendance.status = 'present'
    attendance.total_hours = 9.0
    attendance.in_time = Mock()
    attendance.late_entry = False
    attendance.attendance_type = 'FACE_RECOGNITION'
    attendance.approval_status = 'approved'

    category, is_late = classify_attendance_record(attendance)
    assert category == 'present'
    assert is_late == False


def test_classify_attendance_record_half_day():
    """Test classification of half_day attendance."""
    from services.attendance_stats import classify_attendance_record

    attendance = Mock()
    attendance.status = 'half_day'
    attendance.total_hours = 4.5
    attendance.in_time = Mock()
    attendance.late_entry = False
    attendance.attendance_type = 'FACE_RECOGNITION'
    attendance.approval_status = 'approved'

    category, is_late = classify_attendance_record(attendance)
    assert category == 'half_day'
    assert is_late == False


def test_classify_attendance_record_absent():
    """Test classification of absent attendance."""
    from services.attendance_stats import classify_attendance_record

    attendance = Mock()
    attendance.status = 'absent'
    attendance.total_hours = 0.0
    attendance.in_time = None
    attendance.late_entry = False
    attendance.attendance_type = 'FACE_RECOGNITION'
    attendance.approval_status = 'approved'

    category, is_late = classify_attendance_record(attendance)
    assert category == 'absent'
    assert is_late == False


def test_classify_attendance_record_late():
    """Test classification of late attendance."""
    from services.attendance_stats import classify_attendance_record

    attendance = Mock()
    attendance.status = 'late'
    attendance.total_hours = 9.0
    attendance.in_time = Mock()
    attendance.late_entry = True
    attendance.attendance_type = 'FACE_RECOGNITION'
    attendance.approval_status = 'approved'

    category, is_late = classify_attendance_record(attendance)
    assert category == 'present'
    assert is_late == True


def test_classify_attendance_record_hidden_manual_pending():
    """Test that pending manual attendance is hidden (returns None)."""
    from services.attendance_stats import classify_attendance_record

    attendance = Mock()
    attendance.status = 'present'
    attendance.total_hours = 9.0
    attendance.in_time = Mock()
    attendance.late_entry = False
    attendance.attendance_type = 'MANUAL_PASSWORD'
    attendance.approval_status = 'pending'

    category, is_late = classify_attendance_record(attendance)
    assert category is None


def test_classify_attendance_record_hidden_manual_rejected():
    """Test that rejected manual attendance is hidden (returns None)."""
    from services.attendance_stats import classify_attendance_record

    attendance = Mock()
    attendance.status = 'present'
    attendance.total_hours = 9.0
    attendance.in_time = Mock()
    attendance.late_entry = False
    attendance.attendance_type = 'MANUAL_PASSWORD'
    attendance.approval_status = 'rejected'

    category, is_late = classify_attendance_record(attendance)
    assert category is None


def test_classify_attendance_record_no_in_time():
    """Test classification when no IN time (absent)."""
    from services.attendance_stats import classify_attendance_record

    attendance = Mock()
    attendance.status = None
    attendance.total_hours = 0.0
    attendance.in_time = None
    attendance.late_entry = False
    attendance.attendance_type = 'FACE_RECOGNITION'
    attendance.approval_status = 'approved'

    category, is_late = classify_attendance_record(attendance)
    assert category == 'absent'


def test_classify_attendance_record_hours_based():
    """Test classification based on hours worked."""
    from services.attendance_stats import classify_attendance_record

    attendance = Mock()
    attendance.status = None
    attendance.total_hours = 8.0
    attendance.in_time = Mock()
    attendance.late_entry = False
    attendance.attendance_type = 'FACE_RECOGNITION'
    attendance.approval_status = 'approved'

    category, is_late = classify_attendance_record(attendance, office_hours=9.0)
    assert category == 'half_day'


def test_calculate_employee_attendance_stats(app_context, make_employee):
    """Test calculation of employee attendance statistics."""
    from services.attendance_stats import calculate_employee_attendance_stats
    from models import Attendance
    from database import db

    employee = make_employee(employee_id='EMP001')

    # Create attendance records
    attendance1 = Attendance(
        employee_id=employee.id,
        date=date.today() - timedelta(days=2),
        in_time=None,
        out_time=None,
        total_hours=9.0,
        status='present'
    )
    attendance2 = Attendance(
        employee_id=employee.id,
        date=date.today() - timedelta(days=3),
        in_time=None,
        out_time=None,
        total_hours=4.5,
        status='half_day'
    )
    db.session.add(attendance1)
    db.session.add(attendance2)
    db.session.commit()

    # Mock attendance manager
    attendance_manager = Mock()
    attendance_manager.calculate_attendance_with_absent = Mock(return_value=[attendance1, attendance2])

    start_date = date.today() - timedelta(days=5)
    end_date = date.today() - timedelta(days=1)

    stats = calculate_employee_attendance_stats(
        attendance_manager,
        employee.id,
        start_date,
        end_date
    )

    assert 'present_days' in stats
    assert 'absent_days' in stats
    assert 'half_days' in stats
    assert 'late_days' in stats
    assert 'total_hours_worked' in stats
    assert 'overtime_hours' in stats


def test_calculate_employee_attendance_stats_excludes_sundays(app_context, make_employee):
    """Test that Sundays are excluded from stats."""
    from services.attendance_stats import calculate_employee_attendance_stats

    employee = make_employee(employee_id='EMP001')

    # Mock attendance manager
    attendance_manager = Mock()
    attendance_manager.calculate_attendance_with_absent = Mock(return_value=[])

    # Use a date range that includes a Sunday
    start_date = date(2026, 9, 6)  # Sunday
    end_date = date(2026, 9, 8)    # Tuesday

    stats = calculate_employee_attendance_stats(
        attendance_manager,
        employee.id,
        start_date,
        end_date
    )

    # Sunday should be excluded
    assert stats['present_days'] == 0


def test_calculate_employee_attendance_stats_excludes_today(app_context, make_employee):
    """Test that today is excluded from historical stats."""
    from services.attendance_stats import calculate_employee_attendance_stats

    employee = make_employee(employee_id='EMP001')

    attendance_manager = Mock()
    attendance_manager.calculate_attendance_with_absent = Mock(return_value=[])

    start_date = date.today() - timedelta(days=1)
    end_date = date.today()

    stats = calculate_employee_attendance_stats(
        attendance_manager,
        employee.id,
        start_date,
        end_date
    )

    # Today should be excluded (end_date adjusted to yesterday)
    assert stats['present_days'] == 0
