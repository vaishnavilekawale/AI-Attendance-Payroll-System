"""Admin edit after a manager REJECT: history stays 'rejected', attendance is
no longer forced absent, and the activity log reflects the edited times."""
from datetime import date, datetime


def _login_admin(client, admin):
    with client.session_transaction() as s:
        s['admin_id'] = admin.id
        s['admin_username'] = admin.username
        s['user_role'] = 'admin'


def test_admin_edit_keeps_rejected_history_and_syncs_activities(
        client, app_context, make_admin, make_employee):
    from database import db
    from models import Attendance, AttendanceActivity, LogoutApprovalRequest
    from services.attendance_stats import has_rejected_approval

    admin = make_admin()
    mgr = make_employee(employee_id='MGR1', designation='Manager', email='m@x.com')
    emp = make_employee(employee_id='EMP0001', email='e@x.com')

    d = date(2026, 10, 9)  # Friday
    att = Attendance(employee_id=emp.id, date=d,
                     in_time=datetime(2026, 10, 9, 11, 45),
                     out_time=datetime(2026, 10, 9, 12, 3), status='absent')
    db.session.add(att)
    db.session.flush()
    for t, a in ((datetime(2026, 10, 9, 11, 45), 'IN'), (datetime(2026, 10, 9, 11, 50), 'OUT'),
                 (datetime(2026, 10, 9, 12, 3), 'IN')):
        db.session.add(AttendanceActivity(employee_id=emp.id, attendance_date=d,
                                          activity_time=t.time(), action=a))
    req = LogoutApprovalRequest(attendance_id=att.id, employee_id=emp.id,
                                manager_id=mgr.id, status='rejected')
    db.session.add(req)
    db.session.commit()
    assert has_rejected_approval(att)

    _login_admin(client, admin)
    r = client.post(f'/admin/edit-attendance/{att.id}',
                    data={'in_time': '2026-10-09T11:45', 'out_time': '2026-10-09T23:00'})
    assert r.status_code == 302

    db.session.expire_all()
    req = db.session.get(LogoutApprovalRequest, req.id)
    assert req is not None and req.status == 'rejected'      # history preserved
    assert req.admin_overridden is True
    att = db.session.get(Attendance, att.id)
    assert not has_rejected_approval(att)
    assert att.status == 'present'                            # not forced absent
    assert att.total_hours == 11.03   # IN/OUT pairs: 11:45-11:50 + 12:03-23:00 (break excluded)

    acts = AttendanceActivity.query.filter_by(
        employee_id=emp.id, attendance_date=d).order_by(AttendanceActivity.id).all()
    # middle punches are kept; only the last (dangling) IN stays and the
    # edited OUT 23:00 is appended after it
    assert [(a.action, a.activity_time.strftime('%H:%M')) for a in acts] == \
        [('IN', '11:45'), ('OUT', '11:50'), ('IN', '12:03'), ('OUT', '23:00')]


def test_admin_edit_back_and_forth_never_loses_punches(client, app_context, make_admin, make_employee):
    from database import db
    from models import Attendance, AttendanceActivity

    admin = make_admin()
    emp = make_employee(employee_id='EMP0002', email='e2@x.com')
    d = date(2026, 9, 28)  # Monday
    att = Attendance(employee_id=emp.id, date=d, in_time=datetime(2026, 9, 28, 13, 0),
                     out_time=datetime(2026, 9, 28, 21, 0), status='half_day')
    db.session.add(att)
    db.session.flush()
    for h, m, a in ((13, 0, 'IN'), (13, 30, 'OUT'), (13, 45, 'IN'), (21, 0, 'OUT')):
        db.session.add(AttendanceActivity(employee_id=emp.id, attendance_date=d,
                                          activity_time=datetime(2026, 9, 28, h, m).time(), action=a))
    db.session.commit()
    _login_admin(client, admin)

    def visible():
        acts = AttendanceActivity.query.filter_by(employee_id=emp.id, attendance_date=d) \
            .filter(AttendanceActivity.hidden_by_admin.isnot(True)) \
            .order_by(AttendanceActivity.activity_time).all()
        return [(a.action, a.activity_time.strftime('%H:%M')) for a in acts]

    def edit(i, o):
        client.post(f'/admin/edit-attendance/{att.id}',
                    data={'in_time': f'2026-09-28T{i}', 'out_time': f'2026-09-28T{o}'})
        db.session.expire_all()

    edit('14:00', '20:00')
    assert visible() == [('IN', '14:00'), ('OUT', '20:00')]
    assert AttendanceActivity.query.filter_by(employee_id=emp.id).count() == 4  # nothing deleted
    edit('13:00', '21:00')
    assert visible() == [('IN', '13:00'), ('OUT', '13:30'), ('IN', '13:45'), ('OUT', '21:00')]


def test_admin_edit_hours_use_in_out_pairs(client, app_context, make_admin, make_employee):
    from database import db
    from models import Attendance, AttendanceActivity

    admin = make_admin()
    emp = make_employee(employee_id='EMP0003', email='e3@x.com')
    d = date(2026, 9, 28)
    att = Attendance(employee_id=emp.id, date=d, in_time=datetime(2026, 9, 28, 13, 0),
                     out_time=datetime(2026, 9, 28, 21, 0), status='half_day')
    db.session.add(att)
    db.session.flush()
    for h, m, a in ((13, 0, 'IN'), (14, 0, 'OUT'), (15, 0, 'IN'), (21, 0, 'OUT')):
        db.session.add(AttendanceActivity(employee_id=emp.id, attendance_date=d,
                                          activity_time=datetime(2026, 9, 28, h, m).time(), action=a))
    db.session.commit()
    _login_admin(client, admin)

    # same times as original -> hours must equal the pair sum (1h + 6h = 7h), not 8h
    client.post(f'/admin/edit-attendance/{att.id}',
                data={'in_time': '2026-09-28T13:00', 'out_time': '2026-09-28T21:00'})
    db.session.expire_all()
    assert db.session.get(Attendance, att.id).total_hours == 7.0
