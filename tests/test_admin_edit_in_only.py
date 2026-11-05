"""Admin edits only the IN time of TODAY (no OUT yet): the employee is still
working, so the record must stay 'present' (temporary), not become 'absent'."""
from datetime import date, datetime, timedelta, time


def _login_admin(client, admin):
    with client.session_transaction() as s:
        s['admin_id'] = admin.id
        s['admin_username'] = admin.username
        s['user_role'] = 'admin'


def _make_today_record(emp, in_hhmm='12:12'):
    from database import db
    from models import Attendance, AttendanceActivity
    d = date.today()
    h, m = map(int, in_hhmm.split(':'))
    att = Attendance(employee_id=emp.id, date=d, in_time=datetime.combine(d, time(h, m)),
                     status='present', late_entry=True)
    db.session.add(att)
    db.session.flush()
    db.session.add(AttendanceActivity(employee_id=emp.id, attendance_date=d,
                                      activity_time=time(h, m), action='IN'))
    db.session.commit()
    return att, d


def test_admin_edit_in_only_today_stays_present(client, app_context, make_admin, make_employee):
    from database import db
    from models import Attendance
    admin = make_admin()
    emp = make_employee(employee_id='EMP0001', email='e@x.com')
    att, d = _make_today_record(emp)

    _login_admin(client, admin)
    r = client.post(f'/admin/edit-attendance/{att.id}', data={'in_time': f'{d}T11:12', 'out_time': ''})
    assert r.status_code == 302

    db.session.expire_all()
    att = db.session.get(Attendance, att.id)
    assert att.in_time == datetime.combine(d, time(11, 12))
    assert att.out_time is None
    assert att.status == 'present'          # was 'absent' before the fix


def test_admin_edit_today_with_out_is_finalized(client, app_context, make_admin, make_employee):
    from database import db
    from models import Attendance
    admin = make_admin()
    emp = make_employee(employee_id='EMP0002', email='e2@x.com')
    att, d = _make_today_record(emp, '09:00')

    _login_admin(client, admin)
    client.post(f'/admin/edit-attendance/{att.id}',
                data={'in_time': f'{d}T09:00', 'out_time': f'{d}T09:30'})
    db.session.expire_all()
    att = db.session.get(Attendance, att.id)
    assert att.status == 'absent'           # 0.5h worked -> final status still applies


def test_admin_edit_past_day_without_out_is_still_finalized(client, app_context, make_admin, make_employee):
    from database import db
    from models import Attendance
    admin = make_admin()
    emp = make_employee(employee_id='EMP0003', email='e3@x.com')
    past = date.today() - timedelta(days=3)
    att = Attendance(employee_id=emp.id, date=past, in_time=datetime.combine(past, time(10, 0)),
                     status='present')
    db.session.add(att)
    db.session.commit()

    _login_admin(client, admin)
    client.post(f'/admin/edit-attendance/{att.id}', data={'in_time': f'{past}T10:00', 'out_time': ''})
    db.session.expire_all()
    att = db.session.get(Attendance, att.id)
    assert att.status == 'absent'           # unchanged behaviour for past days
