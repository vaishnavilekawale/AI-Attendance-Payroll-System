"""'Approved/Rejected By' must show who really decided: the admin or the manager.

Bug: an admin's id was saved in approved_by (an EMPLOYEE foreign key), so admin id 1
showed up as employee #1.
"""
from datetime import date, datetime

import pytest


def _setup(make_admin, make_employee):
    from database import db
    from models import Attendance
    admin = make_admin()
    first = make_employee(employee_id='EMP0001', name='First Employee')   # same numeric id as the admin
    mgr = make_employee(employee_id='MGR1', name='Pradnya Manager', designation='Manager', email='m@x.com')
    emp = make_employee(employee_id='EMP0003', name='Onkar Employee', email='e@x.com')
    atts = []
    for day in (date(2026, 10, 8), date(2026, 10, 9)):
        att = Attendance(employee_id=emp.id, date=day, in_time=datetime(day.year, day.month, day.day, 11, 0),
                         status='present')
        db.session.add(att)
        atts.append(att)
    db.session.flush()
    return admin, first, mgr, emp, atts


def test_admin_decision_shows_admin_name(app_context, make_admin, make_employee):
    from database import db
    from models import LogoutApprovalRequest
    from services.approval_service import approval_service

    admin, first, mgr, emp, atts = _setup(make_admin, make_employee)
    assert admin.id == first.id                                    # the collision that caused the bug
    rejected = LogoutApprovalRequest(attendance_id=atts[0].id, employee_id=emp.id, manager_id=mgr.id)
    approved = LogoutApprovalRequest(attendance_id=atts[1].id, employee_id=emp.id, manager_id=mgr.id)
    db.session.add_all([rejected, approved])
    db.session.commit()

    assert approval_service.reject_logout_request_admin(rejected.id, admin.id, 'speak with admin')['success']
    assert approval_service.approve_logout_request_admin(approved.id, admin.id)['success']

    db.session.expire_all()
    for req in (db.session.get(LogoutApprovalRequest, rejected.id), db.session.get(LogoutApprovalRequest, approved.id)):
        assert req.approved_by is None and req.approved_by_admin_id == admin.id
        assert req.approver_name == f'{admin.username} (Admin)'
        assert 'First Employee' not in req.approver_name


def test_manager_decision_still_shows_manager(app_context, make_admin, make_employee):
    from database import db
    from models import LogoutApprovalRequest
    from services.approval_service import approval_service

    admin, first, mgr, emp, atts = _setup(make_admin, make_employee)
    req = LogoutApprovalRequest(attendance_id=atts[0].id, employee_id=emp.id, manager_id=mgr.id)
    db.session.add(req)
    db.session.commit()
    assert approval_service.reject_logout_request(req.id, mgr.id, 'no')['success']
    db.session.expire_all()
    req = db.session.get(LogoutApprovalRequest, req.id)
    assert req.approved_by == mgr.id and req.approved_by_admin_id is None
    assert req.approver_name == 'Pradnya Manager'


def test_old_rows_with_admin_id_in_approved_by_are_shown_correctly(app_context, make_admin, make_employee):
    from database import db
    from models import LogoutApprovalRequest

    admin, first, mgr, emp, atts = _setup(make_admin, make_employee)
    old = LogoutApprovalRequest(attendance_id=atts[0].id, employee_id=emp.id, manager_id=mgr.id,
                                status='rejected', approved_by=admin.id)      # how the old code saved it
    pending = LogoutApprovalRequest(attendance_id=atts[1].id, employee_id=emp.id, manager_id=mgr.id)
    db.session.add_all([old, pending])
    db.session.commit()
    assert old.approver_name == f'{admin.username} (Admin)'
    assert pending.approver_name == '-'


def test_migration_moves_old_admin_ids(app_context, make_admin, make_employee):
    """The one-time upgrade step: old admin decisions move to the new column, manager ones stay."""
    from database import db
    from models import LogoutApprovalRequest

    admin, first, mgr, emp, atts = _setup(make_admin, make_employee)
    by_admin = LogoutApprovalRequest(attendance_id=atts[0].id, employee_id=emp.id, manager_id=mgr.id,
                                     status='rejected', approved_by=admin.id)
    by_mgr = LogoutApprovalRequest(attendance_id=atts[1].id, employee_id=emp.id, manager_id=mgr.id,
                                   status='approved', approved_by=mgr.id)
    db.session.add_all([by_admin, by_mgr])
    db.session.commit()
    db.session.execute(db.text(
        "UPDATE logout_approval_requests SET approved_by_admin_id = approved_by, approved_by = NULL "
        "WHERE approved_by IS NOT NULL AND approved_by != manager_id"))
    db.session.commit()
    db.session.expire_all()
    a, m = db.session.get(LogoutApprovalRequest, by_admin.id), db.session.get(LogoutApprovalRequest, by_mgr.id)
    assert (a.approved_by, a.approved_by_admin_id) == (None, admin.id)
    assert (m.approved_by, m.approved_by_admin_id) == (mgr.id, None)


def test_admin_approvals_page_shows_admin_not_employee(flask_app, make_admin, make_employee, app_context):
    from database import db
    from models import LogoutApprovalRequest
    from services.approval_service import approval_service

    admin, first, mgr, emp, atts = _setup(make_admin, make_employee)
    req = LogoutApprovalRequest(attendance_id=atts[0].id, employee_id=emp.id, manager_id=mgr.id)
    db.session.add(req)
    db.session.commit()
    approval_service.reject_logout_request_admin(req.id, admin.id, 'speak with admin')

    c = flask_app.test_client()
    with c.session_transaction() as s:
        s['admin_id'] = admin.id
        s['admin_username'] = admin.username
        s['user_role'] = 'admin'
    html = c.get('/admin/approvals?date_from=2020-01-01&date_to=2030-12-31').data.decode()
    assert f'{admin.username} (Admin)' in html
    assert 'First Employee' not in html
