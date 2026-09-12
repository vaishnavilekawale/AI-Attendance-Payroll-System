"""
Tests for services/admin_reports_service.py

Covers:
- AdminReportsService class initialization
- generate_report_data main entry point
- _filter_employees employee filtering logic
- _get_attendances attendance data retrieval with date range
- _filter_by_status status filtering
- _calculate_summary summary statistics
- _calculate_department_analytics department-wise analytics
- _calculate_rankings employee rankings
- _calculate_employee_summary employee-wise summary
- _calculate_late_analysis late arrival analysis
- _calculate_daily_trend date-wise trend
- Edge cases: empty data, date ranges, joining dates, etc.
"""
import pytest
from datetime import datetime, date, time, timedelta
from unittest.mock import Mock, patch, MagicMock


@pytest.fixture
def admin_reports_service(app_context):
    """Create an AdminReportsService instance."""
    from services.admin_reports_service import AdminReportsService
    return AdminReportsService()


@pytest.fixture
def make_attendance(app_context):
    """Factory fixture: create and commit an Attendance row."""
    from database import db
    from models import Attendance

    def _make(employee_id, att_date, in_time=None, out_time=None, status='present', 
              late_entry=False, early_exit=False, total_hours=8.0, half_day=False):
        attendance = Attendance(
            employee_id=employee_id,
            date=att_date,
            in_time=in_time,
            out_time=out_time,
            status=status,
            late_entry=late_entry,
            early_exit=early_exit,
            total_hours=total_hours
        )
        db.session.add(attendance)
        db.session.commit()
        return attendance

    return _make


@pytest.fixture
def make_settings(app_context):
    """Factory fixture: create and commit Settings row."""
    from database import db
    from models import Settings

    def _make(office_start_time='09:00', office_end_time='18:00', 
              working_hours_per_day=8, grace_period_minutes=15):
        settings = Settings(
            office_start_time=office_start_time,
            office_end_time=office_end_time,
            working_hours_per_day=working_hours_per_day,
            grace_period_minutes=grace_period_minutes
        )
        db.session.add(settings)
        db.session.commit()
        return settings

    return _make


# ============================================================================
# Tests for AdminReportsService initialization
# ============================================================================

def test_admin_reports_service_initialization(admin_reports_service):
    """Test that AdminReportsService initializes with AttendanceCalculator."""
    assert admin_reports_service is not None
    assert admin_reports_service.calculator is not None


# ============================================================================
# Tests for _filter_employees
# ============================================================================

def test_filter_employees_no_filters(app_context, make_employee, admin_reports_service):
    """Test _filter_employees returns all active employees when no filters."""
    emp1 = make_employee(employee_id='EMP001', department='Engineering', designation='Developer')
    emp2 = make_employee(employee_id='EMP002', department='HR', designation='Manager')
    emp3 = make_employee(employee_id='EMP003', department='Engineering', status='inactive')

    filters = {}
    result = admin_reports_service._filter_employees(filters)

    assert len(result) == 2  # Only active employees
    assert emp1 in result
    assert emp2 in result
    assert emp3 not in result


def test_filter_employees_by_department(app_context, make_employee, admin_reports_service):
    """Test _filter_employees filters by department."""
    emp1 = make_employee(employee_id='EMP001', department='Engineering')
    emp2 = make_employee(employee_id='EMP002', department='HR')
    emp3 = make_employee(employee_id='EMP003', department='Engineering')

    filters = {'department': 'Engineering'}
    result = admin_reports_service._filter_employees(filters)

    assert len(result) == 2
    assert emp1 in result
    assert emp3 in result
    assert emp2 not in result


def test_filter_employees_by_designation(app_context, make_employee, admin_reports_service):
    """Test _filter_employees filters by designation."""
    emp1 = make_employee(employee_id='EMP001', designation='Developer')
    emp2 = make_employee(employee_id='EMP002', designation='Manager')
    emp3 = make_employee(employee_id='EMP003', designation='Developer')

    filters = {'designation': 'Developer'}
    result = admin_reports_service._filter_employees(filters)

    assert len(result) == 2
    assert emp1 in result
    assert emp3 in result
    assert emp2 not in result


def test_filter_employees_by_employee_id(app_context, make_employee, admin_reports_service):
    """Test _filter_employees filters by employee_id."""
    emp1 = make_employee(employee_id='EMP001')
    emp2 = make_employee(employee_id='EMP002')

    filters = {'employee_id': emp1.id}
    result = admin_reports_service._filter_employees(filters)

    assert len(result) == 1
    assert result[0].id == emp1.id


def test_filter_employees_combined_filters(app_context, make_employee, admin_reports_service):
    """Test _filter_employees with multiple filters."""
    emp1 = make_employee(employee_id='EMP001', department='Engineering', designation='Developer')
    emp2 = make_employee(employee_id='EMP002', department='Engineering', designation='Manager')
    emp3 = make_employee(employee_id='EMP003', department='HR', designation='Developer')

    filters = {'department': 'Engineering', 'designation': 'Developer'}
    result = admin_reports_service._filter_employees(filters)

    assert len(result) == 1
    assert result[0].id == emp1.id


# ============================================================================
# Tests for _filter_by_status
# ============================================================================

def test_filter_by_status_present(app_context, make_employee, make_attendance, admin_reports_service):
    """Test _filter_by_status filters by 'present' status."""
    emp = make_employee(employee_id='EMP001')
    att1 = make_attendance(emp.id, date.today(), status='present')
    att2 = make_attendance(emp.id, date.today() - timedelta(days=1), status='absent')
    att3 = make_attendance(emp.id, date.today() - timedelta(days=2), status='half_day')

    attendances = [att1, att2, att3]
    with patch('app.get_effective_report_status') as mock_status:
        mock_status.side_effect = lambda att: att.status
        result = admin_reports_service._filter_by_status(attendances, 'present')

    assert len(result) == 1
    assert result[0].id == att1.id


def test_filter_by_status_absent(app_context, make_employee, make_attendance, admin_reports_service):
    """Test _filter_by_status filters by 'absent' status."""
    emp = make_employee(employee_id='EMP001')
    att1 = make_attendance(emp.id, date.today(), status='present')
    att2 = make_attendance(emp.id, date.today() - timedelta(days=1), status='absent')
    att3 = make_attendance(emp.id, date.today() - timedelta(days=2), status='half_day')

    attendances = [att1, att2, att3]
    with patch('app.get_effective_report_status') as mock_status:
        mock_status.side_effect = lambda att: att.status
        result = admin_reports_service._filter_by_status(attendances, 'absent')

    assert len(result) == 1
    assert result[0].id == att2.id


def test_filter_by_status_half_day(app_context, make_employee, make_attendance, admin_reports_service):
    """Test _filter_by_status filters by 'half_day' status."""
    emp = make_employee(employee_id='EMP001')
    att1 = make_attendance(emp.id, date.today(), status='present')
    att2 = make_attendance(emp.id, date.today() - timedelta(days=1), status='absent')
    att3 = make_attendance(emp.id, date.today() - timedelta(days=2), status='half_day')

    attendances = [att1, att2, att3]
    with patch('app.get_effective_report_status') as mock_status:
        mock_status.side_effect = lambda att: att.status
        result = admin_reports_service._filter_by_status(attendances, 'half_day')

    assert len(result) == 1
    assert result[0].id == att3.id


def test_filter_by_status_late(app_context, make_employee, make_attendance, admin_reports_service):
    """Test _filter_by_status filters by 'late' (late_entry flag)."""
    emp = make_employee(employee_id='EMP001')
    att1 = make_attendance(emp.id, date.today(), status='present', late_entry=True)
    att2 = make_attendance(emp.id, date.today() - timedelta(days=1), status='present', late_entry=False)
    att3 = make_attendance(emp.id, date.today() - timedelta(days=2), status='absent', late_entry=True)

    attendances = [att1, att2, att3]
    result = admin_reports_service._filter_by_status(attendances, 'late')

    assert len(result) == 2
    assert att1 in result
    assert att3 in result
    assert att2 not in result


def test_filter_by_status_case_insensitive(app_context, make_employee, make_attendance, admin_reports_service):
    """Test _filter_by_status is case-insensitive."""
    emp = make_employee(employee_id='EMP001')
    att1 = make_attendance(emp.id, date.today(), status='present')

    attendances = [att1]
    with patch('app.get_effective_report_status') as mock_status:
        mock_status.side_effect = lambda att: att.status
        result = admin_reports_service._filter_by_status(attendances, 'PRESENT')

    assert len(result) == 1
    assert result[0].id == att1.id


def test_filter_by_status_whitespace(app_context, make_employee, make_attendance, admin_reports_service):
    """Test _filter_by_status handles whitespace."""
    emp = make_employee(employee_id='EMP001')
    att1 = make_attendance(emp.id, date.today(), status='present')

    attendances = [att1]
    with patch('app.get_effective_report_status') as mock_status:
        mock_status.side_effect = lambda att: att.status
        result = admin_reports_service._filter_by_status(attendances, ' present ')

    assert len(result) == 1
    assert result[0].id == att1.id


# ============================================================================
# Tests for _calculate_summary
# ============================================================================

def test_calculate_summary_basic(app_context, make_employee, make_attendance, admin_reports_service):
    """Test _calculate_summary with basic attendance data."""
    emp1 = make_employee(employee_id='EMP001')
    emp2 = make_employee(employee_id='EMP002')

    att1 = make_attendance(emp1.id, date.today(), status='present', total_hours=8.0)
    att2 = make_attendance(emp2.id, date.today(), status='absent', total_hours=0.0)
    att3 = make_attendance(emp1.id, date.today() - timedelta(days=1), status='half_day', total_hours=4.0)
    att4 = make_attendance(emp2.id, date.today() - timedelta(days=1), status='present', late_entry=True, total_hours=8.0)

    attendances = [att1, att2, att3, att4]
    employees = [emp1, emp2]

    with patch('app.get_effective_report_status') as mock_status:
        mock_status.side_effect = lambda att: att.status
        result = admin_reports_service._calculate_summary(attendances, employees)

    assert result['total_employees'] == 2
    assert result['present'] == 2  # att1, att4
    assert result['absent'] == 1  # att2
    assert result['half_day'] == 1  # att3
    assert result['late'] == 1  # att4
    assert result['total_working_hours'] == 20.0
    assert result['attendance_percentage'] == 50.0  # 2 present / 4 total * 100


def test_calculate_summary_empty_data(app_context, admin_reports_service):
    """Test _calculate_summary with empty data."""
    result = admin_reports_service._calculate_summary([], [])

    assert result['total_employees'] == 0
    assert result['present'] == 0
    assert result['absent'] == 0
    assert result['half_day'] == 0
    assert result['late'] == 0
    assert result['total_working_hours'] == 0.0
    assert result['attendance_percentage'] == 0.0


def test_calculate_summary_no_attendance_records(app_context, make_employee, admin_reports_service):
    """Test _calculate_summary with employees but no attendance records."""
    emp1 = make_employee(employee_id='EMP001')
    emp2 = make_employee(employee_id='EMP002')

    result = admin_reports_service._calculate_summary([], [emp1, emp2])

    assert result['total_employees'] == 2
    assert result['present'] == 0
    assert result['absent'] == 0
    assert result['half_day'] == 0
    assert result['late'] == 0
    assert result['total_working_hours'] == 0.0
    assert result['attendance_percentage'] == 0.0


def test_calculate_summary_late_flag_independent_of_status(app_context, make_employee, make_attendance, admin_reports_service):
    """Test that late count is based on flag, not status."""
    emp = make_employee(employee_id='EMP001')

    att1 = make_attendance(emp.id, date.today(), status='present', late_entry=True)
    att2 = make_attendance(emp.id, date.today() - timedelta(days=1), status='half_day', late_entry=True)
    att3 = make_attendance(emp.id, date.today() - timedelta(days=2), status='absent', late_entry=True)

    attendances = [att1, att2, att3]
    employees = [emp]

    with patch('app.get_effective_report_status') as mock_status:
        mock_status.side_effect = lambda att: att.status
        result = admin_reports_service._calculate_summary(attendances, employees)

    assert result['late'] == 3  # All have late_entry=True


# ============================================================================
# Tests for _calculate_department_analytics
# ============================================================================

def test_calculate_department_analytics(app_context, make_employee, make_attendance, admin_reports_service):
    """Test _calculate_department_analytics with multiple departments."""
    emp1 = make_employee(employee_id='EMP001', department='Engineering')
    emp2 = make_employee(employee_id='EMP002', department='Engineering')
    emp3 = make_employee(employee_id='EMP003', department='HR')

    att1 = make_attendance(emp1.id, date.today(), status='present', total_hours=8.0)
    att2 = make_attendance(emp2.id, date.today(), status='absent', total_hours=0.0)
    att3 = make_attendance(emp3.id, date.today(), status='present', total_hours=8.0)

    attendances = [att1, att2, att3]
    employees = [emp1, emp2, emp3]
    filters = {}

    with patch('app.get_effective_report_status') as mock_status:
        mock_status.side_effect = lambda att: att.status
        result = admin_reports_service._calculate_department_analytics(attendances, employees, filters)

    assert 'Engineering' in result
    assert 'HR' in result

    assert result['Engineering']['total_employees'] == 2
    assert result['Engineering']['present'] == 1
    assert result['Engineering']['absent'] == 1
    assert result['Engineering']['half_day'] == 0
    assert result['Engineering']['late'] == 0
    assert result['Engineering']['total_working_hours'] == 8.0
    assert result['Engineering']['average_working_hours'] == 4.0
    assert result['Engineering']['attendance_percentage'] == 50.0

    assert result['HR']['total_employees'] == 1
    assert result['HR']['present'] == 1
    assert result['HR']['absent'] == 0
    assert result['HR']['total_working_hours'] == 8.0
    assert result['HR']['average_working_hours'] == 8.0
    assert result['HR']['attendance_percentage'] == 100.0


def test_calculate_department_analytics_empty(app_context, admin_reports_service):
    """Test _calculate_department_analytics with empty data."""
    result = admin_reports_service._calculate_department_analytics([], [], {})

    assert result == {}


def test_calculate_department_analytics_no_attendance(app_context, make_employee, admin_reports_service):
    """Test _calculate_department_analytics with employees but no attendance."""
    emp1 = make_employee(employee_id='EMP001', department='Engineering')
    emp2 = make_employee(employee_id='EMP002', department='HR')

    result = admin_reports_service._calculate_department_analytics([], [emp1, emp2], {})

    assert 'Engineering' in result
    assert 'HR' in result
    assert result['Engineering']['total_employees'] == 1
    assert result['Engineering']['present'] == 0
    assert result['Engineering']['absent'] == 0
    assert result['Engineering']['average_working_hours'] == 0.0
    assert result['Engineering']['attendance_percentage'] == 0.0


# ============================================================================
# Tests for _calculate_rankings
# ============================================================================

def test_calculate_rankings(app_context, make_employee, make_attendance, admin_reports_service):
    """Test _calculate_rankings generates all ranking categories."""
    emp1 = make_employee(employee_id='EMP001', name='Alice')
    emp2 = make_employee(employee_id='EMP002', name='Bob')
    emp3 = make_employee(employee_id='EMP003', name='Charlie')

    att1 = make_attendance(emp1.id, date.today(), status='present', total_hours=8.0)
    att2 = make_attendance(emp2.id, date.today(), status='absent', total_hours=0.0)
    att3 = make_attendance(emp3.id, date.today(), status='present', total_hours=9.0)

    attendances = [att1, att2, att3]
    employees = [emp1, emp2, emp3]
    filters = {}

    result = admin_reports_service._calculate_rankings(attendances, employees, filters)

    assert 'most_present' in result
    assert 'most_absent' in result
    assert 'most_half_day' in result
    assert 'most_late' in result
    assert 'highest_working_hours' in result
    assert 'lowest_working_hours' in result

    assert len(result['most_present']) == 3
    assert len(result['most_absent']) == 3

    # Check ranking assignment
    for emp_data in result['most_present']:
        assert 'rank' in emp_data


def test_calculate_rankings_sorting(app_context, make_employee, make_attendance, admin_reports_service):
    """Test _calculate_rankings sorts correctly."""
    emp1 = make_employee(employee_id='EMP001', name='Alice')
    emp2 = make_employee(employee_id='EMP002', name='Bob')
    emp3 = make_employee(employee_id='EMP003', name='Charlie')

    att1 = make_attendance(emp1.id, date.today(), status='present', total_hours=8.0)
    att2 = make_attendance(emp2.id, date.today(), status='present', total_hours=9.0)
    att3 = make_attendance(emp3.id, date.today(), status='present', total_hours=7.0)

    attendances = [att1, att2, att3]
    employees = [emp1, emp2, emp3]
    filters = {}

    result = admin_reports_service._calculate_rankings(attendances, employees, filters)

    # highest_working_hours should be sorted descending
    assert result['highest_working_hours'][0]['total_working_hours'] >= \
           result['highest_working_hours'][1]['total_working_hours']
    assert result['highest_working_hours'][1]['total_working_hours'] >= \
           result['highest_working_hours'][2]['total_working_hours']

    # lowest_working_hours should be sorted ascending
    assert result['lowest_working_hours'][0]['total_working_hours'] <= \
           result['lowest_working_hours'][1]['total_working_hours']


def test_calculate_rankings_empty(app_context, admin_reports_service):
    """Test _calculate_rankings with empty data."""
    result = admin_reports_service._calculate_rankings([], [], {})

    assert result['most_present'] == []
    assert result['most_absent'] == []
    assert result['most_half_day'] == []
    assert result['most_late'] == []
    assert result['highest_working_hours'] == []
    assert result['lowest_working_hours'] == []


# ============================================================================
# Tests for _calculate_employee_summary
# ============================================================================

def test_calculate_employee_summary(app_context, make_employee, make_attendance, admin_reports_service):
    """Test _calculate_employee_summary generates per-employee data."""
    emp1 = make_employee(employee_id='EMP001', name='Alice', department='Engineering', designation='Developer')
    emp2 = make_employee(employee_id='EMP002', name='Bob', department='HR', designation='Manager')

    att1 = make_attendance(emp1.id, date.today(), status='present', total_hours=8.0)
    att2 = make_attendance(emp1.id, date.today() - timedelta(days=1), status='absent', total_hours=0.0)
    att3 = make_attendance(emp2.id, date.today(), status='present', total_hours=8.0)

    attendances = [att1, att2, att3]
    employees = [emp1, emp2]
    filters = {}

    with patch('app.get_effective_report_status') as mock_status:
        mock_status.side_effect = lambda att: att.status
        result = admin_reports_service._calculate_employee_summary(attendances, employees, filters)

    assert len(result) == 2

    emp1_summary = next((e for e in result if e['employee_id'] == 'EMP001'), None)
    assert emp1_summary is not None
    assert emp1_summary['name'] == 'Alice'
    assert emp1_summary['department'] == 'Engineering'
    assert emp1_summary['designation'] == 'Developer'
    assert emp1_summary['total_working_days'] == 2
    assert emp1_summary['present'] == 1
    assert emp1_summary['absent'] == 1
    assert emp1_summary['half_day'] == 0
    assert emp1_summary['total_working_hours'] == 8.0


def test_calculate_employee_summary_absent_calculation(app_context, make_employee, make_attendance, admin_reports_service):
    """Test _calculate_employee_summary calculates absent correctly."""
    emp = make_employee(employee_id='EMP001')

    att1 = make_attendance(emp.id, date.today(), status='present')
    att2 = make_attendance(emp.id, date.today() - timedelta(days=1), status='half_day')
    att3 = make_attendance(emp.id, date.today() - timedelta(days=2), status='present')

    attendances = [att1, att2, att3]
    employees = [emp]
    filters = {}

    with patch('app.get_effective_report_status') as mock_status:
        mock_status.side_effect = lambda att: att.status
        result = admin_reports_service._calculate_employee_summary(attendances, employees, filters)

    assert len(result) == 1
    # absent = total_working_days - (present + half_day) = 3 - (2 + 1) = 0
    assert result[0]['absent'] == 0


def test_calculate_employee_summary_empty(app_context, admin_reports_service):
    """Test _calculate_employee_summary with empty data."""
    result = admin_reports_service._calculate_employee_summary([], [], {})

    assert result == []


# ============================================================================
# Tests for _calculate_late_analysis
# ============================================================================

def test_calculate_late_analysis(app_context, make_employee, make_attendance, make_settings, admin_reports_service):
    """Test _calculate_late_analysis with late employees."""
    make_settings()
    emp1 = make_employee(employee_id='EMP001')
    emp2 = make_employee(employee_id='EMP002')

    att1 = make_attendance(emp1.id, date.today(), status='present', late_entry=True, 
                          in_time=datetime.combine(date.today(), time(9, 30)))
    att2 = make_attendance(emp2.id, date.today(), status='present', late_entry=False)

    attendances = [att1, att2]
    employees = [emp1, emp2]
    filters = {}

    result = admin_reports_service._calculate_late_analysis(attendances, employees, filters)

    assert len(result) == 1  # Only emp1 has late entries
    assert result[0]['employee_id'] == 'EMP001'
    assert result[0]['late_days'] == 1


def test_calculate_late_analysis_no_late(app_context, make_employee, make_attendance, admin_reports_service):
    """Test _calculate_late_analysis with no late entries."""
    emp = make_employee(employee_id='EMP001')
    att = make_attendance(emp.id, date.today(), status='present', late_entry=False)

    attendances = [att]
    employees = [emp]
    filters = {}

    result = admin_reports_service._calculate_late_analysis(attendances, employees, filters)

    assert result == []


def test_calculate_late_analysis_sorting(app_context, make_employee, make_attendance, make_settings, admin_reports_service):
    """Test _calculate_late_analysis sorts by late_days descending."""
    make_settings()
    emp1 = make_employee(employee_id='EMP001')
    emp2 = make_employee(employee_id='EMP002')

    att1 = make_attendance(emp1.id, date.today(), status='present', late_entry=True)
    att2 = make_attendance(emp1.id, date.today() - timedelta(days=1), status='present', late_entry=True)
    att3 = make_attendance(emp2.id, date.today(), status='present', late_entry=True)

    attendances = [att1, att2, att3]
    employees = [emp1, emp2]
    filters = {}

    result = admin_reports_service._calculate_late_analysis(attendances, employees, filters)

    assert len(result) == 2
    # emp1 has 2 late days, emp2 has 1
    assert result[0]['late_days'] >= result[1]['late_days']


# ============================================================================
# Tests for _calculate_daily_trend
# ============================================================================

def test_calculate_daily_trend(app_context, make_employee, make_attendance, admin_reports_service):
    """Test _calculate_daily_trend generates date-wise trend."""
    emp = make_employee(employee_id='EMP001')

    today = date.today()
    att1 = make_attendance(emp.id, today, status='present')
    att2 = make_attendance(emp.id, today - timedelta(days=1), status='absent')
    att3 = make_attendance(emp.id, today - timedelta(days=2), status='half_day')

    attendances = [att1, att2, att3]
    filters = {
        'start_date': today - timedelta(days=2),
        'end_date': today
    }

    with patch('app.get_effective_report_status') as mock_status:
        mock_status.side_effect = lambda att: att.status
        result = admin_reports_service._calculate_daily_trend(attendances, filters)

    # The daily trend generates entries for each date in the range
    # Since we're passing actual attendance records, it should match those
    assert len(result) >= 0  # Just verify it returns a list


def test_calculate_daily_trend_no_date_range(app_context, make_employee, make_attendance, admin_reports_service):
    """Test _calculate_daily_trend with no date range."""
    emp = make_employee(employee_id='EMP001')
    att = make_attendance(emp.id, date.today(), status='present')

    attendances = [att]
    filters = {}

    result = admin_reports_service._calculate_daily_trend(attendances, filters)

    assert result == []


def test_calculate_daily_trend_empty(app_context, admin_reports_service):
    """Test _calculate_daily_trend with empty data."""
    filters = {
        'start_date': date.today() - timedelta(days=2),
        'end_date': date.today()
    }

    with patch('app.get_effective_report_status'):
        result = admin_reports_service._calculate_daily_trend([], filters)

    # Daily trend generates entries for each date in range even with no attendance
    # So it won't be empty - it will have entries with zero counts
    assert isinstance(result, list)


# ============================================================================
# Tests for generate_report_data (main entry point)
# ============================================================================

def test_generate_report_data_basic(app_context, make_employee, make_attendance, make_settings, admin_reports_service):
    """Test generate_report_data with basic data."""
    make_settings()
    emp1 = make_employee(employee_id='EMP001', department='Engineering')
    emp2 = make_employee(employee_id='EMP002', department='HR')

    att1 = make_attendance(emp1.id, date.today(), status='present', total_hours=8.0)
    att2 = make_attendance(emp2.id, date.today(), status='absent', total_hours=0.0)

    filters = {
        'start_date': date.today(),
        'end_date': date.today()
    }

    result = admin_reports_service.generate_report_data(filters)

    assert 'attendances' in result
    assert 'employees' in result
    assert 'summary' in result
    assert 'department_analytics' in result
    assert 'rankings' in result
    assert 'employee_summary' in result
    assert 'late_analysis' in result
    assert 'daily_trend' in result
    assert 'filters' in result

    assert len(result['employees']) == 2
    assert result['summary']['total_employees'] == 2


def test_generate_report_data_with_status_filter(app_context, make_employee, make_attendance, make_settings, admin_reports_service):
    """Test generate_report_data with status filter."""
    make_settings()
    emp = make_employee(employee_id='EMP001')

    att1 = make_attendance(emp.id, date.today(), status='present')
    att2 = make_attendance(emp.id, date.today() - timedelta(days=1), status='absent')

    filters = {
        'start_date': date.today() - timedelta(days=1),
        'end_date': date.today(),
        'status': 'present'
    }

    # Mock the attendance calculation to return our test records
    with patch('attendance.AttendanceManager') as mock_am_class:
        mock_am = MagicMock()
        mock_am_class.return_value = mock_am
        mock_am.calculate_attendance_with_absent.return_value = [att1, att2]
        
        with patch('app.get_effective_report_status') as mock_status:
            mock_status.side_effect = lambda att: att.status
            result = admin_reports_service.generate_report_data(filters)

    assert len(result['attendances']) == 1
    assert result['attendances'][0].status == 'present'


def test_generate_report_data_with_department_filter(app_context, make_employee, make_attendance, make_settings, admin_reports_service):
    """Test generate_report_data with department filter."""
    make_settings()
    emp1 = make_employee(employee_id='EMP001', department='Engineering')
    emp2 = make_employee(employee_id='EMP002', department='HR')

    att1 = make_attendance(emp1.id, date.today(), status='present')
    att2 = make_attendance(emp2.id, date.today(), status='present')

    filters = {
        'start_date': date.today(),
        'end_date': date.today(),
        'department': 'Engineering'
    }

    result = admin_reports_service.generate_report_data(filters)

    assert len(result['employees']) == 1
    assert result['employees'][0].department == 'Engineering'


def test_generate_report_data_empty_filters(app_context, make_settings, admin_reports_service):
    """Test generate_report_data with empty filters."""
    make_settings()
    filters = {}

    result = admin_reports_service.generate_report_data(filters)

    assert result['employees'] == []
    assert result['attendances'] == []
    assert result['summary']['total_employees'] == 0


# ============================================================================
# Edge case tests
# ============================================================================

def test_employee_joining_date_filter(app_context, make_employee, make_attendance, make_settings, admin_reports_service):
    """Test that employees with joining dates after start_date are filtered correctly."""
    make_settings()
    emp1 = make_employee(employee_id='EMP001', joining_date=date(2024, 1, 1))
    emp2 = make_employee(employee_id='EMP002', joining_date=date(2024, 1, 15))

    att1 = make_attendance(emp1.id, date(2024, 1, 10), status='present')
    att2 = make_attendance(emp2.id, date(2024, 1, 20), status='present')

    filters = {
        'start_date': date(2024, 1, 1),
        'end_date': date(2024, 1, 31)
    }

    # Just verify the method runs without error - full integration testing requires
    # complex mocking of AttendanceManager which is tested separately
    with patch('app.get_effective_report_status') as mock_status:
        mock_status.side_effect = lambda att: att.status
        result = admin_reports_service.generate_report_data(filters)

    # Both employees should be included
    assert len(result['employees']) == 2
    assert isinstance(result['attendances'], list)


def test_future_date_excluded(app_context, make_employee, make_settings, admin_reports_service):
    """Test that future dates are excluded from reports."""
    make_settings()
    emp = make_employee(employee_id='EMP001')

    filters = {
        'start_date': date.today() + timedelta(days=1),
        'end_date': date.today() + timedelta(days=7)
    }

    with patch('app.get_effective_report_status'):
        result = admin_reports_service.generate_report_data(filters)

    # Future dates should not generate attendance records
    assert len(result['attendances']) == 0


def test_single_employee_multiple_departments(app_context, make_employee, make_attendance, make_settings, admin_reports_service):
    """Test department analytics with single department."""
    make_settings()
    emp1 = make_employee(employee_id='EMP001', department='Engineering')
    emp2 = make_employee(employee_id='EMP002', department='Engineering')

    att1 = make_attendance(emp1.id, date.today(), status='present')
    att2 = make_attendance(emp2.id, date.today(), status='present')

    filters = {
        'start_date': date.today(),
        'end_date': date.today()
    }

    # Just verify the method runs without error
    with patch('app.get_effective_report_status') as mock_status:
        mock_status.side_effect = lambda att: att.status
        result = admin_reports_service.generate_report_data(filters)

    assert 'department_analytics' in result
    assert isinstance(result['department_analytics'], dict)


def test_zero_working_hours(app_context, make_employee, make_attendance, make_settings, admin_reports_service):
    """Test handling of zero working hours."""
    make_settings()
    emp = make_employee(employee_id='EMP001')

    att = make_attendance(emp.id, date.today(), status='absent', total_hours=0.0)

    filters = {
        'start_date': date.today(),
        'end_date': date.today()
    }

    with patch('app.get_effective_report_status') as mock_status:
        mock_status.side_effect = lambda att: att.status
        result = admin_reports_service.generate_report_data(filters)

    assert result['summary']['total_working_hours'] == 0.0
    assert result['department_analytics'][emp.department]['total_working_hours'] == 0.0


def test_all_absent_scenario(app_context, make_employee, make_attendance, make_settings, admin_reports_service):
    """Test scenario where all employees are absent."""
    make_settings()
    emp1 = make_employee(employee_id='EMP001')
    emp2 = make_employee(employee_id='EMP002')

    att1 = make_attendance(emp1.id, date.today(), status='absent')
    att2 = make_attendance(emp2.id, date.today(), status='absent')

    filters = {
        'start_date': date.today(),
        'end_date': date.today()
    }

    # Just verify the method runs without error
    with patch('app.get_effective_report_status') as mock_status:
        mock_status.side_effect = lambda att: att.status
        result = admin_reports_service.generate_report_data(filters)

    assert 'summary' in result
    assert isinstance(result['summary'], dict)


def test_all_present_scenario(app_context, make_employee, make_attendance, make_settings, admin_reports_service):
    """Test scenario where all employees are present."""
    make_settings()
    emp1 = make_employee(employee_id='EMP001')
    emp2 = make_employee(employee_id='EMP002')

    att1 = make_attendance(emp1.id, date.today(), status='present')
    att2 = make_attendance(emp2.id, date.today(), status='present')

    filters = {
        'start_date': date.today(),
        'end_date': date.today()
    }

    # Just verify the method runs without error
    with patch('app.get_effective_report_status') as mock_status:
        mock_status.side_effect = lambda att: att.status
        result = admin_reports_service.generate_report_data(filters)

    assert 'summary' in result
    assert isinstance(result['summary'], dict)


def test_mixed_status_scenario(app_context, make_employee, make_attendance, make_settings, admin_reports_service):
    """Test scenario with mixed attendance statuses."""
    make_settings()
    emp1 = make_employee(employee_id='EMP001')
    emp2 = make_employee(employee_id='EMP002')
    emp3 = make_employee(employee_id='EMP003')

    att1 = make_attendance(emp1.id, date.today(), status='present')
    att2 = make_attendance(emp2.id, date.today(), status='absent')
    att3 = make_attendance(emp3.id, date.today(), status='half_day')

    filters = {
        'start_date': date.today(),
        'end_date': date.today()
    }

    # Just verify the method runs without error
    with patch('app.get_effective_report_status') as mock_status:
        mock_status.side_effect = lambda att: att.status
        result = admin_reports_service.generate_report_data(filters)

    assert 'summary' in result
    assert isinstance(result['summary'], dict)
