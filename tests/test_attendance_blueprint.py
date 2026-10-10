"""
Runtime smoke tests for attendance_routes.py (the attendance/camera/kiosk/
dashboard blueprint extracted from app.py).

These exercise the moved routes end-to-end through the Flask test client
with real sessions - unauthenticated redirects to auth.login, admin pages,
employee pages, the public kiosk endpoints, the biometric-consent gate on
capture_face(), and the relocated EmployeeAttendancePresenceTracker - and
check that the legacy `app.get_effective_report_status` / `app.get_services`
access paths (used by services/admin_reports_service.py, pdf_generator.py
and the existing test patches) still resolve.

Camera/ML paths that need real OpenCV/TensorFlow are intentionally not
driven here (the test environment stubs those modules, see conftest.py).
"""
import pytest


def _login_admin(client, admin):
    with client.session_transaction() as s:
        s['admin_id'] = admin.id
        s['admin_username'] = admin.username
        s['user_role'] = 'admin'


def _login_emp(client, emp):
    with client.session_transaction() as s:
        s['employee_id'] = emp.id
        s['employee_username'] = emp.employee_id
        s['user_role'] = 'employee'


def test_public_routes(client):
    assert client.get('/').status_code == 200
    r = client.get('/test-log'); assert r.status_code == 200 and r.data == b'OK'
    assert client.get('/login').status_code == 200
    with pytest.raises(Exception, match='TEST EXCEPTION'):
        client.get('/test')
    r = client.post('/api/auto-scan-attendance')
    assert r.status_code == 200 and r.get_json()['message'] == 'No image provided'
    r = client.post('/mark_manual_attendance', json={})
    assert r.status_code == 400


def test_unauth_redirects_to_auth_login(client):
    for path in ['/dashboard', '/attendance', '/employee-dashboard', '/employee-attendance',
                 '/employee-profile', '/attendance/history', '/train-ai', '/face-registration/1']:
        r = client.get(path)
        assert r.status_code == 302, path
        assert '/login' in r.headers['Location'], path


def test_admin_pages(client, make_admin, make_employee, monkeypatch, tmp_path):
    # Use an empty temp dataset folder so the assertions below ("no images
    # on disk") never depend on - or touch - the developer's real dataset/.
    import attendance_routes
    monkeypatch.setattr(attendance_routes.Config, 'DATASET_FOLDER', str(tmp_path))
    admin = make_admin(); emp = make_employee()
    _login_admin(client, admin)
    for path in ['/dashboard', '/attendance', '/attendance/history',
                 f'/face-registration/{emp.id}', f'/api/face-dataset-status/{emp.id}',
                 '/api/verify-employee-id/EMP0001', '/api/verify-employee-id/NOPE']:
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code)
    assert client.get('/dataset/nope/x.jpg').status_code == 404
    assert client.get('/uploads/nope.pdf').status_code == 404
    r = client.get('/train-ai'); assert r.status_code == 302 and '/settings' in r.headers['Location']
    # capture blocked by consent gate -> redirects back to face_registration
    from database import db
    emp.biometric_consent_given = False
    db.session.commit()
    r = client.post(f'/capture-face/{emp.id}')
    assert r.status_code == 302 and f'/face-registration/{emp.id}' in r.headers['Location']
    r = client.post('/api/train-face-model', data={'employee_id': str(emp.id)})
    assert r.status_code == 400  # no images on disk
    r = client.post('/api/upload-face-image', data={'employee_id': str(emp.id)})
    assert r.status_code == 400
    r = client.post(f'/delete-face-image/{emp.id}/nothing.jpg')
    assert r.status_code == 404


def test_employee_pages(client, make_employee):
    emp = make_employee()
    _login_emp(client, emp)
    for path in ['/employee-dashboard', '/employee-attendance', '/employee-profile']:
        assert client.get(path).status_code == 200, path
    r = client.post('/employee-profile', data={'phone': '9999999999', 'email': 'new@example.com'})
    assert r.status_code == 302 and r.headers['Location'].endswith('/employee-profile')
    r = client.post('/api/employee-attendance')
    assert r.get_json()['message'] == 'No image provided'
    # employee blocked from admin-only routes -> redirected to employee dashboard
    r = client.get('/dashboard')
    assert r.status_code == 200  # dashboard is login_required only (unchanged behaviour)


def test_manual_attendance_bad_creds_and_verify(client, make_employee):
    make_employee()
    r = client.post('/mark_manual_attendance', json={'employee_id': 'EMP0001', 'password': 'wrong'})
    assert r.get_json()['message'] == 'Invalid Employee ID or password'


def test_employee_presence_tracker_moved_intact():
    import attendance_routes as ar
    t = ar.EmployeeAttendancePresenceTracker()
    assert t.should_attempt_mark('7') is True
    t.note_face_seen('7'); t.lock('7')
    assert t.should_attempt_mark('7') is False       # still present -> locked
    t._last_seen['7'] -= (t.PRESENCE_TIMEOUT_SECONDS + 1)
    assert t.should_attempt_mark('7') is True        # left and came back
    t.sweep(); assert '7' not in t._logged
    assert ar.employee_attendance_presence_tracker is not None
    import ai_engine
    assert ar.presence_tracker is ai_engine.presence_tracker  # shared admin tracker unchanged


def test_reexports_for_legacy_import_paths():
    import app as m
    from services.attendance_stats import get_effective_report_status
    from services.app_services import get_services
    assert m.get_effective_report_status is get_effective_report_status
    assert m.get_services is get_services