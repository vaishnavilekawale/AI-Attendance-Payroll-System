"""A PAST day with IN but no OUT must be Absent EVERYWHERE (admin reports and
attendance page included), also after the admin edited / cleared the OUT of a
record whose logout request had been rejected."""
from datetime import date, datetime, time, timedelta


def _login(client, admin):
    with client.session_transaction() as s:
        s['admin_id'] = admin.id
        s['admin_username'] = admin.username
        s['user_role'] = 'admin'


def _past_workday():
    d = date.today() - timedelta(days=1)
    while d.weekday() == 6:          # Sunday is a holiday
        d -= timedelta(days=1)
    return d


def _status_in_admin_report(emp, d):
    from attendance import AttendanceManager
    rows = AttendanceManager().calculate_attendance_with_absent(d)
    return next(r.status for r in rows if r.employee.id == emp.id)


def test_reject_then_admin_sets_and_clears_out_is_consistent(client, app_context, make_admin, make_employee):
    from database import db
    from models import Attendance, AttendanceActivity, LogoutApprovalRequest
    from services.attendance_stats import get_effective_report_status

    admin = make_admin()
    mgr = make_employee(employee_id='MGR1', designation='Manager', email='m@x.com')
    emp = make_employee(employee_id='EMP0001', email='e@x.com')
    d = _past_workday()
    att = Attendance(employee_id=emp.id, date=d, in_time=datetime.combine(d, time(11, 12)), status='Absent')
    db.session.add(att)
    db.session.flush()
    db.session.add(AttendanceActivity(employee_id=emp.id, attendance_date=d,
                                      activity_time=time(11, 12), action='IN'))
    db.session.add(LogoutApprovalRequest(attendance_id=att.id, employee_id=emp.id,
                                         manager_id=mgr.id, status='rejected'))
    db.session.commit()
    _login(client, admin)

    # 1) manager rejected -> absent everywhere
    assert _status_in_admin_report(emp, d) == 'absent'

    # 2) admin sets OUT -> present/half day (not absent) everywhere
    client.post(f'/admin/edit-attendance/{att.id}',
                data={'in_time': f'{d}T11:12', 'out_time': f'{d}T21:30'})
    db.session.expire_all()
    s2 = _status_in_admin_report(emp, d)
    assert s2 in ('present', 'half_day')
    assert get_effective_report_status(db.session.get(Attendance, att.id)) == s2

    # 3) admin CLEARS OUT on that past day -> absent in the admin report too
    #    (this used to stay 'present' there)
    client.post(f'/admin/edit-attendance/{att.id}', data={'in_time': f'{d}T11:12', 'out_time': ''})
    db.session.expire_all()
    assert _status_in_admin_report(emp, d) == 'absent'
    assert get_effective_report_status(db.session.get(Attendance, att.id)) == 'absent'


def test_today_in_only_stays_temporary_present(client, app_context, make_employee):
    from database import db
    from models import Attendance, AttendanceActivity
    emp = make_employee(employee_id='EMP0002', email='e2@x.com')
    d = date.today()
    if d.weekday() == 6:
        return                       # Sunday: nothing is shown
    att = Attendance(employee_id=emp.id, date=d, in_time=datetime.combine(d, time(9, 0)), status='present')
    db.session.add(att)
    db.session.flush()
    db.session.add(AttendanceActivity(employee_id=emp.id, attendance_date=d,
                                      activity_time=time(9, 0), action='IN'))
    db.session.commit()
    assert _status_in_admin_report(emp, d) == 'present'
