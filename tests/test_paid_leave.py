"""
Per-employee PAID leave (CL / SL / EL): quota handling, attendance pipeline,
payroll maths and the admin routes.

The most important guarantee tested here: an employee with NO leave gets
exactly the same numbers as before the feature existed.
"""
from datetime import date

import pytest

MON = date(2026, 9, 7)      # Monday, in the past
SAT = date(2026, 9, 12)
SUN = date(2026, 9, 13)


@pytest.fixture
def manager(app_context):
    from attendance import AttendanceManager
    return AttendanceManager()


@pytest.fixture
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture
def admin_client(client, make_admin):
    admin = make_admin()
    with client.session_transaction() as s:
        s['admin_id'] = admin.id
        s['admin_username'] = admin.username
    return client


def _set_default_quota(code, days):
    from database import db
    from models import LeaveQuota
    db.session.add(LeaveQuota(employee_id=None, leave_type=code, days_per_year=days))
    db.session.commit()


# ---------------------------------------------------------------- granting

def test_grant_skips_sundays_and_holidays(app_context, make_employee):
    from database import db
    from models import Holiday
    from leave_utils import grant_leave

    emp = make_employee(employee_id='L001')
    db.session.add(Holiday(date=date(2026, 9, 9), name='Test holiday'))
    db.session.commit()

    res = grant_leave(emp, MON, SUN, 'CL', 'trip')
    db.session.commit()

    # Mon-Sat is 6 days, minus the Wednesday holiday = 5; Sunday is an off day
    assert len(res['granted']) == 5
    assert date(2026, 9, 9) in res['off_days'] and SUN in res['off_days']
    assert res['over_quota'] == [] and res['already'] == []


def test_grant_is_not_duplicated(app_context, make_employee):
    from database import db
    from leave_utils import grant_leave

    emp = make_employee(employee_id='L002')
    grant_leave(emp, MON, MON, 'CL')
    db.session.commit()
    res = grant_leave(emp, MON, MON, 'SL')
    assert res['granted'] == [] and res['already'] == [MON]


def test_quota_exhausted_days_are_not_recorded(app_context, make_employee):
    from database import db
    from leave_utils import grant_leave, get_balance

    emp = make_employee(employee_id='L003')
    _set_default_quota('CL', 2)

    res = grant_leave(emp, MON, SAT, 'CL')      # 6 working days requested
    db.session.commit()

    assert len(res['granted']) == 2
    assert len(res['over_quota']) == 4          # these stay Absent / LOP
    bal = get_balance(emp.id, 2026)
    assert bal['CL']['used'] == 2 and bal['CL']['balance'] == 0


def test_employee_override_beats_default(app_context, make_employee):
    from database import db
    from models import LeaveQuota
    from leave_utils import grant_leave, get_quota

    emp = make_employee(employee_id='L004')
    _set_default_quota('SL', 1)
    db.session.add(LeaveQuota(employee_id=emp.id, leave_type='SL', days_per_year=3))
    db.session.commit()

    assert get_quota(emp.id, 'SL') == 3
    res = grant_leave(emp, MON, SAT, 'SL')
    assert len(res['granted']) == 3


def test_builtin_default_quota_used_when_nothing_saved(app_context, make_employee):
    from leave_utils import get_balance
    emp = make_employee(employee_id='L005')
    bal = get_balance(emp.id, 2026)
    assert bal['CL']['quota'] == 12 and bal['SL']['quota'] == 6 and bal['EL']['quota'] == 15


def test_before_joining_date_is_skipped(app_context, make_employee):
    from leave_utils import grant_leave
    emp = make_employee(employee_id='L006', joining_date=date(2026, 9, 9))
    res = grant_leave(emp, MON, date(2026, 9, 10), 'CL')
    assert res['before_joining'] == [MON, date(2026, 9, 8)]
    assert res['granted'] == [date(2026, 9, 9), date(2026, 9, 10)]


def test_invalid_input_rejected(app_context, make_employee):
    from leave_utils import grant_leave
    emp = make_employee(employee_id='L007')
    with pytest.raises(ValueError):
        grant_leave(emp, MON, MON, 'XX')
    with pytest.raises(ValueError):
        grant_leave(emp, SAT, MON, 'CL')


# ------------------------------------------------- attendance pipeline / stats

def _ids(records):
    return {r.employee.id for r in records}


def test_leave_employee_not_listed_as_absent(app_context, make_employee, manager):
    from database import db
    from leave_utils import grant_leave

    on_leave = make_employee(employee_id='L010', name='On Leave')
    other = make_employee(employee_id='L011', name='Other')

    before = manager.calculate_attendance_with_absent(MON)
    assert _ids(before) == {on_leave.id, other.id}       # both Absent (no punches)

    grant_leave(on_leave, MON, MON, 'CL')
    db.session.commit()

    after = manager.calculate_attendance_with_absent(MON)
    assert _ids(after) == {other.id}                     # other is untouched
    assert [r.status for r in after] == ['absent']


def test_real_punch_on_leave_day_still_counts(app_context, make_employee, manager):
    from datetime import datetime
    from database import db
    from models import Attendance
    from leave_utils import grant_leave

    emp = make_employee(employee_id='L012')
    grant_leave(emp, MON, MON, 'CL')
    db.session.add(Attendance(employee_id=emp.id, date=MON,
                              in_time=datetime(2026, 9, 7, 9, 0), out_time=datetime(2026, 9, 7, 18, 30),
                              total_hours=9.5, status='present'))
    db.session.commit()

    assert emp.id in _ids(manager.calculate_attendance_with_absent(MON))


def test_stats_count_paid_leave_and_keep_everything_else(app_context, make_employee, manager):
    from database import db
    from leave_utils import grant_leave
    from services.attendance_stats import calculate_employee_attendance_stats as stats_for

    emp = make_employee(employee_id='L013')

    base = stats_for(manager, emp.id, MON, SUN)          # Mon-Sat: 6 working days, no punches
    assert base['absent_days'] == 6 and base['paid_leave_days'] == 0
    assert base['present_days'] == 0 and base['half_days'] == 0

    grant_leave(emp, date(2026, 9, 8), date(2026, 9, 9), 'CL')
    db.session.commit()

    with_leave = stats_for(manager, emp.id, MON, SUN)
    assert with_leave['paid_leave_days'] == 2
    assert with_leave['absent_days'] == 4                # only the 2 leave days changed
    assert with_leave['present_days'] == 0 and with_leave['half_days'] == 0
    assert with_leave['present_days'] + with_leave['absent_days'] + with_leave['half_days'] \
        + with_leave['paid_leave_days'] == 6


def test_other_employee_stats_unchanged_by_someone_elses_leave(app_context, make_employee, manager):
    from database import db
    from leave_utils import grant_leave
    from services.attendance_stats import calculate_employee_attendance_stats as stats_for

    a = make_employee(employee_id='L014')
    b = make_employee(employee_id='L015')
    before = stats_for(manager, b.id, MON, SUN)
    grant_leave(a, MON, SAT, 'CL')
    db.session.commit()
    assert stats_for(manager, b.id, MON, SUN) == before


# -------------------------------------------------------------- payroll maths

def _payroll(emp, **kw):
    from payroll import compute_payroll_amounts
    args = dict(employee=emp, working_days=26, present_days=25, absent_days=1, half_days=0,
                late_days=0, total_hours_worked=200, overtime_hours=0, month=9, year=2026)
    args.update(kw)
    return compute_payroll_amounts(**args)


def test_default_call_is_identical_to_before(app_context, make_employee):
    emp = make_employee(employee_id='L020')
    old_style = _payroll(emp)
    explicit_zero = _payroll(emp, paid_leave_days=0)
    assert old_style == explicit_zero
    assert old_style['paid_leave_days'] == 0
    assert old_style['paid_days'] == 25 and old_style['lop_days'] == 1


def test_paid_leave_day_is_paid_not_lop(app_context, make_employee):
    emp = make_employee(employee_id='L021')
    absent = _payroll(emp)                                        # 25 present + 1 absent
    leave = _payroll(emp, absent_days=0, paid_leave_days=1)       # 25 present + 1 paid leave

    assert leave['paid_days'] == 26 and leave['lop_days'] == 0
    assert leave['proration_factor'] == 1.0
    assert absent['proration_factor'] < 1.0
    assert leave['basic_salary'] > absent['basic_salary']
    assert leave['basic_salary'] == emp.basic_salary             # full basic, nothing deducted
    assert leave['absent_deduction'] == 0 and leave['lop_deduction'] == 0


def test_paid_leave_never_exceeds_a_full_month(app_context, make_employee):
    emp = make_employee(employee_id='L022')
    res = _payroll(emp, present_days=26, absent_days=0, paid_leave_days=3)
    assert res['proration_factor'] == 1.0                        # capped at a full month


def test_payslip_row_only_shown_when_leave_taken(app_context, make_employee):
    from payroll import build_payslip_attendance_rows
    from types import SimpleNamespace

    base = dict(working_days=26, present_days=25, half_days=0, absent_days=1, late_days=0,
                paid_days=25, lop_days=1, per_day_salary=100)
    no_leave = [label for label, _ in build_payslip_attendance_rows(SimpleNamespace(**base, paid_leave_days=0))]
    with_leave = [label for label, _ in build_payslip_attendance_rows(SimpleNamespace(**base, paid_leave_days=2))]
    legacy = [label for label, _ in build_payslip_attendance_rows(SimpleNamespace(**base))]  # old record

    assert 'Paid Leave Days' not in no_leave and no_leave == legacy
    assert with_leave.index('Paid Leave Days') == with_leave.index('Late Days') - 1
    assert len(with_leave) == len(no_leave) + 1


def test_monthly_payroll_end_to_end(app_context, make_employee, manager):
    """Whole September 2026: 26 working days, no punches except the leave days."""
    from database import db
    from leave_utils import grant_leave
    from payroll import PayrollCalculator

    emp = make_employee(employee_id='L023')
    grant_leave(emp, date(2026, 9, 8), date(2026, 9, 9), 'CL')
    db.session.commit()

    records = PayrollCalculator().calculate_monthly_payroll(2026, 9, employee_id=emp.id)
    rec = [r for r in records if r.employee_id == emp.id][0]

    assert rec.paid_leave_days == 2
    assert rec.absent_days == 24                  # the 24 days with no punch and no leave
    assert rec.paid_days == 2                     # nothing else was worked
    assert rec.lop_days == 24


# ---------------------------------------------------------------------- routes

def test_leave_pages_and_actions(admin_client, make_employee):
    from models import EmployeeLeave, LeaveQuota, Payroll
    emp = make_employee(employee_id='L030', name='Leave Person')

    page = admin_client.get('/settings/leaves?year=2026')
    assert page.status_code == 200
    html = page.data.decode()
    assert 'Add Paid Leave' in html and 'Leave Person' in html and 'Balance 2026' in html

    r = admin_client.post('/settings/leaves/add', data={
        'employee_id': emp.id, 'leave_type': 'CL', 'from_date': '2026-09-07',
        'to_date': '2026-09-13', 'reason': 'Trip'}, follow_redirects=True)
    assert r.status_code == 200 and b'recorded for Leave Person' in r.data
    assert EmployeeLeave.query.filter_by(employee_id=emp.id).count() == 6   # Sunday skipped

    pdf = admin_client.get('/settings/leaves/pdf?year=2026')
    assert pdf.status_code == 200 and pdf.data[:4] == b'%PDF'
    pdf1 = admin_client.get(f'/settings/leaves/pdf?year=2026&employee_id={emp.id}')
    assert pdf1.status_code == 200 and pdf1.data[:4] == b'%PDF'

    # default + employee quota
    admin_client.post('/settings/leaves/quotas', data={'quota_CL': '3', 'quota_SL': '4', 'quota_EL': '5'})
    assert LeaveQuota.query.filter_by(employee_id=None, leave_type='CL').one().days_per_year == 3
    admin_client.post('/settings/leaves/quotas', data={'employee_id': emp.id, 'quota_CL': '20', 'quota_SL': '', 'quota_EL': ''})
    assert LeaveQuota.query.filter_by(employee_id=emp.id).count() == 1
    admin_client.post('/settings/leaves/quotas', data={'employee_id': emp.id, 'quota_CL': '', 'quota_SL': '', 'quota_EL': ''})
    assert LeaveQuota.query.filter_by(employee_id=emp.id).count() == 0     # back to default

    leave = EmployeeLeave.query.first()
    admin_client.post(f'/settings/leaves/{leave.id}/delete')
    assert EmployeeLeave.query.filter_by(employee_id=emp.id).count() == 5

    # settings page links to the leaves page
    assert b'Manage Leaves' in admin_client.get('/settings').data


def test_leave_routes_reject_bad_input(admin_client, make_employee):
    from models import EmployeeLeave
    emp = make_employee(employee_id='L031')
    for data in ({'leave_type': 'CL', 'from_date': '2026-09-07'},                       # no employee
                 {'employee_id': emp.id, 'leave_type': 'ZZ', 'from_date': '2026-09-07'},  # bad type
                 {'employee_id': emp.id, 'leave_type': 'CL', 'from_date': 'nope'},        # bad date
                 {'employee_id': emp.id, 'leave_type': 'CL', 'from_date': '2026-09-10',
                  'to_date': '2026-09-07'}):                                              # end before start
        assert admin_client.post('/settings/leaves/add', data=data).status_code == 302
    assert EmployeeLeave.query.count() == 0
    bad = admin_client.post('/settings/leaves/quotas', data={'quota_CL': '-1'})
    assert bad.status_code == 302


def test_leave_routes_need_admin(client):
    for path in ('/settings/leaves', '/settings/leaves/pdf'):
        assert client.get(path).status_code in (301, 302, 401, 403)
    assert client.post('/settings/leaves/add', data={}).status_code in (301, 302, 401, 403)


def test_reset_employee_quota_to_default(admin_client, make_employee):
    from models import LeaveQuota
    emp = make_employee(employee_id='L032')
    admin_client.post('/settings/leaves/quotas', data={'employee_id': emp.id, 'quota_CL': '20', 'quota_SL': '9', 'quota_EL': ''})
    admin_client.post('/settings/leaves/quotas', data={'quota_CL': '3', 'quota_SL': '4', 'quota_EL': '5'})  # default
    assert LeaveQuota.query.filter_by(employee_id=emp.id).count() == 2

    page = admin_client.get('/settings/leaves?year=2026').data.decode()
    assert 'Reset to default' in page and '(L032) *' in page            # marked as custom

    r = admin_client.post('/settings/leaves/quotas', data={'employee_id': emp.id, 'reset': '1'}, follow_redirects=True)
    assert b'uses the default quota again' in r.data
    assert LeaveQuota.query.filter_by(employee_id=emp.id).count() == 0
    # the company default is untouched
    assert LeaveQuota.query.filter_by(employee_id=None, leave_type='CL').one().days_per_year == 3
    from leave_utils import get_quota
    assert get_quota(emp.id, 'CL') == 3


# ================================================== phase 2: employee requests

from datetime import timedelta


def _future_monday(weeks=2):
    d = date.today() + timedelta(days=7 * weeks)
    return d - timedelta(days=d.weekday())          # a Monday


@pytest.fixture
def employee_client(flask_app):
    """Factory: test client logged in as the given employee."""
    def make(emp):
        c = flask_app.test_client()
        with c.session_transaction() as s:
            s['employee_id'] = emp.id
            s['employee_username'] = emp.username if hasattr(emp, 'username') else 'emp'
        return c
    return make


def _apply(c, mon, days=2, leave_type='CL', reason='trip'):
    return c.post('/my-leaves/apply', data={
        'leave_type': leave_type, 'from_date': mon.isoformat(),
        'to_date': (mon + timedelta(days=days - 1)).isoformat(), 'reason': reason}, follow_redirects=True)


def test_employee_applies_and_admin_approves(admin_client, employee_client, make_employee):
    from models import EmployeeLeave, LeaveRequest
    from leave_utils import get_balance
    emp = make_employee(employee_id='R001', name='Req Person')
    ec = employee_client(emp)
    mon = _future_monday()

    r = _apply(ec, mon, days=2)
    assert b'Leave request sent' in r.data
    req = LeaveRequest.query.one()
    assert req.status == 'pending' and req.employee_id == emp.id
    assert EmployeeLeave.query.count() == 0                       # nothing counts before approval
    assert get_balance(emp.id, mon.year)['CL']['used'] == 0

    assert b'Pending' in ec.get('/my-leaves').data
    page = admin_client.get('/settings/leaves').data
    assert b'Req Person' in page and b'Approve' in page

    r = admin_client.post(f'/settings/leaves/requests/{req.id}/approve', follow_redirects=True)
    assert b'Approved: 2 day(s)' in r.data
    assert LeaveRequest.query.one().status == 'approved'
    assert LeaveRequest.query.one().working_days == 2
    assert EmployeeLeave.query.filter_by(employee_id=emp.id).count() == 2
    assert get_balance(emp.id, mon.year)['CL']['used'] == 2

    mine = ec.get('/my-leaves').data.decode()
    assert 'Approved' in mine and 'Leave Taken in' in mine


def test_employee_sees_only_own_data(admin_client, employee_client, make_employee):
    from models import LeaveRequest
    a = make_employee(employee_id='R010', name='Alpha Person')
    b = make_employee(employee_id='R011', name='Bravo Person')
    _apply(employee_client(a), _future_monday(), days=1, reason='alpha secret reason')
    page = employee_client(b).get('/my-leaves').data.decode()
    assert 'alpha secret reason' not in page and 'Alpha Person' not in page

    # b cannot cancel a's request
    req = LeaveRequest.query.one()
    employee_client(b).post(f'/my-leaves/{req.id}/cancel')
    assert LeaveRequest.query.one().status == 'pending'


def test_cancel_own_pending_request(employee_client, make_employee):
    from models import LeaveRequest
    emp = make_employee(employee_id='R020')
    ec = employee_client(emp)
    _apply(ec, _future_monday(), days=1)
    req = LeaveRequest.query.one()
    assert b'cancelled' in ec.post(f'/my-leaves/{req.id}/cancel', follow_redirects=True).data
    assert LeaveRequest.query.one().status == 'cancelled'
    # an already decided request cannot be cancelled
    req.status = 'approved'
    from database import db
    db.session.commit()
    ec.post(f'/my-leaves/{req.id}/cancel')
    assert LeaveRequest.query.one().status == 'approved'


def test_reject_keeps_balance_and_shows_note(admin_client, employee_client, make_employee):
    from models import EmployeeLeave, LeaveRequest
    emp = make_employee(employee_id='R030')
    ec = employee_client(emp)
    _apply(ec, _future_monday(), days=2)
    req = LeaveRequest.query.one()
    admin_client.post(f'/settings/leaves/requests/{req.id}/reject', data={'note': 'Month end, please postpone'})
    assert LeaveRequest.query.one().status == 'rejected'
    assert EmployeeLeave.query.count() == 0
    assert b'Month end, please postpone' in ec.get('/my-leaves').data


def test_request_validation(employee_client, make_employee):
    from models import LeaveRequest
    emp = make_employee(employee_id='R040')
    ec = employee_client(emp)
    mon = _future_monday()
    sunday = mon + timedelta(days=6)

    assert b'no working day' in ec.post('/my-leaves/apply', data={'leave_type': 'CL', 'from_date': sunday.isoformat()}, follow_redirects=True).data
    for data in ({'leave_type': 'ZZ', 'from_date': mon.isoformat()},
                 {'leave_type': 'CL'},
                 {'leave_type': 'CL', 'from_date': mon.isoformat(), 'to_date': (mon - timedelta(days=1)).isoformat()},
                 {'leave_type': 'CL', 'from_date': (date.today() - timedelta(days=60)).isoformat()},
                 {'leave_type': 'CL', 'from_date': mon.isoformat(), 'to_date': (mon + timedelta(days=200)).isoformat()}):
        ec.post('/my-leaves/apply', data=data)
    assert LeaveRequest.query.count() == 0

    _apply(ec, mon, days=2)
    assert b'already have a pending request' in _apply(ec, mon, days=1).data   # overlap
    assert LeaveRequest.query.count() == 1


def test_apply_is_refused_when_balance_is_short(employee_client, make_employee):
    from models import LeaveRequest
    _set_default_quota('CL', 1)
    emp = make_employee(employee_id='R050')
    r = _apply(employee_client(emp), _future_monday(), days=3)
    assert b'only 1 left' in r.data
    assert LeaveRequest.query.count() == 0


def test_approve_partial_and_blocked_when_quota_is_gone(admin_client, employee_client, make_employee):
    from database import db
    from models import EmployeeLeave, LeaveQuota, LeaveRequest
    _set_default_quota('CL', 3)
    emp = make_employee(employee_id='R060')
    ec = employee_client(emp)
    mon = _future_monday()
    _apply(ec, mon, days=3)                                    # fits the quota of 3
    req = LeaveRequest.query.one()

    # the admin lowers the quota before approving -> only 1 day can still be paid
    LeaveQuota.query.filter_by(employee_id=None, leave_type='CL').one().days_per_year = 1
    db.session.commit()
    r = admin_client.post(f'/settings/leaves/requests/{req.id}/approve', follow_redirects=True)
    assert b'Approved: 1 day(s)' in r.data and b'stay Absent / LOP' in r.data
    assert 'Not paid' in LeaveRequest.query.one().admin_note
    assert EmployeeLeave.query.count() == 1

    # quota is now gone: another request cannot be approved at all and stays pending
    second = LeaveRequest(employee_id=emp.id, leave_type='CL', from_date=mon + timedelta(days=7),
                          to_date=mon + timedelta(days=8), working_days=2)
    db.session.add(second)
    db.session.commit()
    r = admin_client.post(f'/settings/leaves/requests/{second.id}/approve', follow_redirects=True)
    assert b'Could not approve' in r.data
    assert LeaveRequest.query.filter_by(status='pending').count() == 1
    assert EmployeeLeave.query.count() == 1


def test_decision_routes_are_admin_only(employee_client, make_employee, admin_client):
    from models import LeaveRequest
    emp = make_employee(employee_id='R070')
    ec = employee_client(emp)
    _apply(ec, _future_monday(), days=1)
    req = LeaveRequest.query.one()
    # the employee session must not be able to approve their own request
    assert ec.post(f'/settings/leaves/requests/{req.id}/approve').status_code in (301, 302, 401, 403)
    assert ec.post(f'/settings/leaves/requests/{req.id}/reject').status_code in (301, 302, 401, 403)
    assert LeaveRequest.query.one().status == 'pending'
    # an admin session cannot use the employee pages
    assert admin_client.get('/my-leaves').status_code in (301, 302, 401, 403)


def test_dashboard_and_navbar_show_leave(employee_client, make_employee, app_context):
    from database import db
    from leave_utils import grant_leave
    emp = make_employee(employee_id='R080')
    grant_leave(emp, date.today(), date.today(), 'CL')       # leave today (if not a Sunday/holiday)
    db.session.commit()
    ec = employee_client(emp)
    html = ec.get('/employee-dashboard').data.decode()
    assert 'My Leaves' in html and 'Leave balance' in html
    assert 'My Leaves' in ec.get('/employee-attendance').data.decode()
    assert ec.get('/my-leaves').status_code == 200
