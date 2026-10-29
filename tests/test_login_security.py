"""
Login security tests: lockout with escalating auto-expiring locks, generic
error messages, audit log, admin unlock, session lifetime / inactivity
timeout, and the shared lockout on the public kiosk manual-attendance
endpoint.
"""
import time
from datetime import timedelta

import pytest

PW = 'correct-horse-battery-staple'


def _login(client, username, password, role='admin', ip=None):
    kwargs = {}
    if ip:
        kwargs['environ_overrides'] = {'REMOTE_ADDR': ip}
    return client.post('/login', data={
        'username': username, 'password': password, 'role': role,
    }, **kwargs)


@pytest.fixture
def make_emp_login(make_employee):
    """Employee + EmployeeLogin (first_login already done) factory."""
    def _make(employee_id='EMP0001', password=PW, **cred_overrides):
        from database import db
        from models import EmployeeLogin
        emp = make_employee(employee_id=employee_id)
        creds = EmployeeLogin(employee_id=emp.id, username=employee_id,
                              password_hash='placeholder', first_login=False,
                              **cred_overrides)
        creds.set_password(password)
        db.session.add(creds)
        db.session.commit()
        return emp
    return _make


# ---------------------------------------------------------------- login page

def test_login_page_has_no_default_credentials_or_remember_me(client):
    html = client.get('/login').data
    assert b'admin123' not in html
    assert b'Default:' not in html
    assert b'Remember me' not in html


# ------------------------------------------------------------ generic errors

def test_admin_errors_are_identical_for_unknown_user_and_wrong_password(client, make_admin):
    make_admin(username='owner', password=PW)
    a = _login(client, 'nobody', 'whatever')
    b = _login(client, 'owner', 'wrong-password')
    for r in (a, b):
        assert b'Invalid username or password' in r.data
        assert b'Invalid Username' not in r.data
        assert b'Invalid Password' not in r.data


def test_employee_errors_are_generic(client, make_emp_login):
    make_emp_login('EMP0001')
    unknown = _login(client, 'EMP9999', 'x', role='employee')
    wrong = _login(client, 'EMP0001', 'nope-nope', role='employee')
    assert b'Invalid Employee ID or password' in unknown.data
    assert b'Invalid Employee ID or password' in wrong.data
    # The page is re-rendered on the Employee tab, not reset to Admin.
    assert b'const serverRole = "employee"' in unknown.data
    assert b'const serverRole = "employee"' in wrong.data


def test_admin_failure_keeps_admin_tab(client, make_admin):
    make_admin(username='owner', password=PW)
    r = _login(client, 'owner', 'wrong-password')
    assert b'const serverRole = "admin"' in r.data


def test_inactive_message_only_after_correct_password(client, make_emp_login):
    make_emp_login('EMP0001', is_active=False)
    wrong = _login(client, 'EMP0001', 'wrong-password', role='employee')
    assert b'inactive' not in wrong.data
    right = _login(client, 'EMP0001', PW, role='employee')
    assert b'Your account is inactive' in right.data
    with client.session_transaction() as sess:
        assert sess.get('employee_id') is None


def test_admin_forgot_password_unknown_email_shows_not_registered(client, make_admin):
    make_admin(username='owner', password=PW)
    r = client.post('/forgot-password', data={'email': 'nobody@example.com'})
    assert b'No account found with this email address' in r.data


def test_admin_forgot_password_email_match_is_case_insensitive(client, make_admin, monkeypatch):
    make_admin(username='owner', email='Owner@Example.com', password=PW)
    sent = {}

    def fake_send(self, to_email, admin_username, temp_password):
        sent['to'] = to_email
        return {'success': True}

    monkeypatch.setattr('email_service.EmailService.send_admin_temp_password', fake_send)
    r = client.post('/forgot-password', data={'email': ' owner@example.com '})
    assert b'temporary password has been sent' in r.data
    assert sent['to'] == 'Owner@Example.com'


# ------------------------------------------------------------------ lockout

def test_five_failures_lock_the_login_for_15_minutes(client, make_admin):
    make_admin(username='owner', password=PW)
    for _ in range(4):
        r = _login(client, 'owner', 'wrong-password')
        assert b'Too many failed attempts' not in r.data
    r = _login(client, 'owner', 'wrong-password')
    assert b'Too many failed attempts. Try again in 15 minutes.' in r.data

    # Even the CORRECT password is refused while locked.
    r = _login(client, 'owner', PW)
    assert b'Too many failed attempts' in r.data
    with client.session_transaction() as sess:
        assert sess.get('admin_id') is None


def test_unknown_usernames_are_locked_too(client, make_admin):
    """Lockout must not reveal which usernames exist."""
    make_admin(username='owner', password=PW)
    for _ in range(5):
        r = _login(client, 'ghost-user', 'x')
    assert b'Too many failed attempts' in r.data


def test_lock_is_per_ip_so_real_admin_can_still_log_in_elsewhere(client, make_admin):
    make_admin(username='owner', password=PW)
    for _ in range(5):
        _login(client, 'owner', 'wrong-password', ip='10.0.0.99')   # attacker
    blocked = _login(client, 'owner', PW, ip='10.0.0.99')
    assert b'Too many failed attempts' in blocked.data

    ok = _login(client, 'owner', PW, ip='10.0.0.5')                 # real admin
    assert ok.status_code == 302
    assert '/dashboard' in ok.headers['Location']


def test_successful_login_resets_the_failure_counter(client, make_admin):
    make_admin(username='owner', password=PW)
    for _ in range(3):
        _login(client, 'owner', 'wrong-password')
    assert _login(client, 'owner', PW).status_code == 302
    client.get('/logout')
    for _ in range(4):
        r = _login(client, 'owner', 'wrong-password')
    assert b'Too many failed attempts' not in r.data


def test_lock_escalates_15_30_60_and_expires_by_itself(app_context):
    import login_security as ls
    from database import db
    from models import LoginLockout, now_ist

    with app_context.test_request_context('/login', environ_base={'REMOTE_ADDR': '1.2.3.4'}):
        expected = [15, 30, 60, 60]
        for want in expected:
            got = 0
            for _ in range(ls.MAX_ATTEMPTS):
                got = ls.record_failure('admin', 'owner')
            assert got == want
            assert ls.minutes_left('admin', 'owner') in (want, want + 1)
            # simulate time passing: the lock has expired on its own
            rec = LoginLockout.query.first()
            rec.locked_until = now_ist() - timedelta(seconds=1)
            db.session.commit()
            assert ls.minutes_left('admin', 'owner') == 0


def test_admin_can_unlock_an_employee(client, make_admin, make_emp_login):
    emp = make_emp_login('EMP0001')
    make_admin(username='owner', password=PW)

    for _ in range(5):
        _login(client, 'EMP0001', 'wrong-password', role='employee')
    assert b'Too many failed attempts' in _login(client, 'EMP0001', PW, role='employee').data

    assert _login(client, 'owner', PW).status_code == 302          # admin logs in
    r = client.post(f'/employees/{emp.id}/unlock')
    assert r.status_code == 302
    client.get('/logout')

    ok = _login(client, 'EMP0001', PW, role='employee')
    assert ok.status_code == 302


def test_unlock_requires_admin(client, make_emp_login):
    emp = make_emp_login('EMP0001')
    r = client.post(f'/employees/{emp.id}/unlock')
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


# -------------------------------------------------------------- audit trail

def test_audit_log_records_failed_locked_and_success(client, make_admin):
    from models import LoginAudit
    make_admin(username='owner', password=PW)
    for _ in range(5):
        _login(client, 'owner', 'wrong-password')
    events = [e.event for e in LoginAudit.query.order_by(LoginAudit.id).all()]
    assert events == ['failed'] * 4 + ['locked']

    _login(client, 'owner', PW, ip='10.0.0.5')
    last = LoginAudit.query.order_by(LoginAudit.id.desc()).first()
    assert (last.event, last.username, last.ip, last.role) == ('success', 'owner', '10.0.0.5', 'admin')


def test_audit_page_is_admin_only_and_renders(client, make_admin):
    assert client.get('/admin/login-audit').status_code == 302
    make_admin(username='owner', password=PW)
    _login(client, 'owner', PW)
    r = client.get('/admin/login-audit')
    assert r.status_code == 200
    assert b'Login Audit Log' in r.data


def test_audit_pdf_download_respects_filters_and_is_admin_only(client, make_admin):
    assert client.get('/admin/login-audit/pdf').status_code == 302      # not logged in
    make_admin(username='owner', password=PW)
    _login(client, 'owner', 'wrong-password')                           # a 'failed' row
    _login(client, 'owner', PW)                                         # a 'success' row

    r = client.get('/admin/login-audit/pdf?event=failed&role=admin')
    assert r.status_code == 200
    assert r.mimetype == 'application/pdf'
    assert r.data.startswith(b'%PDF')
    assert 'attachment' in r.headers['Content-Disposition']
    assert '.pdf' in r.headers['Content-Disposition']

    # 'all dates' + no matches still yields a valid PDF
    empty = client.get('/admin/login-audit/pdf?date=all&event=unlocked')
    assert empty.status_code == 200 and empty.data.startswith(b'%PDF')


def test_audit_page_has_pdf_button_with_current_filters(client, make_admin):
    make_admin(username='owner', password=PW)
    _login(client, 'owner', PW)
    html = client.get('/admin/login-audit?event=failed&role=admin').data
    assert b'/admin/login-audit/pdf' in html and b'Download PDF' in html


# ------------------------------------------------------------------ session

def test_session_is_permanent_and_tracks_last_active(client, make_admin):
    make_admin(username='owner', password=PW)
    _login(client, 'owner', PW)
    with client.session_transaction() as sess:
        assert sess.permanent is True
        assert 'last_active' in sess


def test_idle_timeout_logs_the_user_out(client, make_admin):
    make_admin(username='owner', password=PW)
    _login(client, 'owner', PW)

    with client.session_transaction() as sess:
        sess['last_active'] = time.time() - 5 * 60            # recently active
    assert client.get('/dashboard').status_code == 200

    with client.session_transaction() as sess:
        sess['last_active'] = time.time() - 31 * 60           # idle too long
    r = client.get('/dashboard')
    assert r.status_code == 302
    assert '/login' in r.headers['Location']
    with client.session_transaction() as sess:
        assert sess.get('admin_id') is None


def test_login_replaces_any_previous_session(client, make_admin, make_emp_login):
    make_emp_login('EMP0001')
    make_admin(username='owner', password=PW)
    _login(client, 'EMP0001', PW, role='employee')
    _login(client, 'owner', PW)
    with client.session_transaction() as sess:
        assert sess.get('admin_id') is not None
        assert sess.get('employee_id') is None


# -------------------------------------------------------- kiosk (public) API

def test_kiosk_manual_attendance_shares_the_lockout(client, make_emp_login):
    make_emp_login('EMP0001')
    for _ in range(4):
        r = client.post('/mark_manual_attendance', json={'employee_id': 'EMP0001', 'password': 'wrong'})
        assert r.get_json()['message'] == 'Invalid Employee ID or password'
    r = client.post('/mark_manual_attendance', json={'employee_id': 'EMP0001', 'password': 'wrong'})
    assert 'Too many failed attempts' in r.get_json()['message']

    # Correct password is refused while locked, and /login is locked too (shared counter).
    r = client.post('/mark_manual_attendance', json={'employee_id': 'EMP0001', 'password': PW})
    assert 'Too many failed attempts' in r.get_json()['message']
    assert b'Too many failed attempts' in _login(client, 'EMP0001', PW, role='employee').data


# ------------------------------------------- flash messages after password change

def test_password_changed_message_shows_on_dashboard_not_on_next_page(client, make_admin):
    """The success flash must be shown right after the redirect (on the
    dashboard), not left over until the next page that happens to render
    flashes (e.g. /employees)."""
    make_admin(username='owner', password='temp-pass-1', force_password_change=True)
    _login(client, 'owner', 'temp-pass-1')

    r = client.post('/change-password', data={
        'current_password': 'temp-pass-1',
        'new_password': 'brand-new-pass-9',
        'confirm_password': 'brand-new-pass-9',
    }, follow_redirects=True)
    assert b'Password changed successfully' in r.data

    assert b'Password changed successfully' not in client.get('/employees').data


def test_password_changed_message_shows_on_employee_dashboard(client, make_emp_login):
    make_emp_login('EMP0001', password='temp-pass-1', force_password_change=True)
    _login(client, 'EMP0001', 'temp-pass-1', role='employee')
    r = client.post('/employee-change-password', data={
        'current_password': 'temp-pass-1',
        'new_password': 'brand-new-pass-9',
        'confirm_password': 'brand-new-pass-9',
    }, follow_redirects=True)
    assert b'Password changed successfully' in r.data


def test_plain_requests_do_not_rewrite_the_session_cookie(client, make_admin):
    """
    Background requests must not re-send the session cookie, otherwise a
    slow parallel response can resurrect an already-shown flash message
    (e.g. 'Password changed successfully.' showing again on the next page).
    """
    make_admin(username='owner', password=PW)
    _login(client, 'owner', PW)
    # The first page that renders a form legitimately stores its CSRF token
    # in the session; after that, plain requests must leave the cookie alone.
    assert client.get('/employees').status_code == 200
    for _ in range(3):
        r = client.get('/employees')
        assert r.status_code == 200
        assert r.headers.getlist('Set-Cookie') == []


def test_password_change_message_shows_on_dashboard_only(client, make_admin):
    admin = make_admin(username='owner', password='old-password-1')
    from database import db
    admin.set_temporary_password('Temp#12345')
    admin.force_password_change = True
    db.session.commit()

    _login(client, 'owner', 'Temp#12345')
    r = client.post('/change-password', data={
        'current_password': 'Temp#12345',
        'new_password': 'brand-new-pw',
        'confirm_password': 'brand-new-pw',
    })
    assert r.status_code == 302
    assert r.headers['Location'].endswith('/dashboard?pw_changed=1')

    landed = client.get(r.headers['Location'])
    assert b'Password changed successfully' in landed.data          # shown on the dashboard
    assert b'Password changed successfully' not in client.get('/employees').data
    assert b'Password changed successfully' not in client.get('/dashboard').data   # not on a plain visit


def test_employee_password_change_message_shows_on_employee_dashboard_only(client, make_emp_login):
    emp = make_emp_login('EMP0001', force_password_change=True)
    _login(client, 'EMP0001', PW, role='employee')
    r = client.post('/employee-change-password', data={
        'current_password': PW,
        'new_password': 'brand-new-pw',
        'confirm_password': 'brand-new-pw',
    })
    assert r.status_code == 302
    assert r.headers['Location'].endswith('/employee-dashboard?pw_changed=1')
    assert b'Password changed successfully' in client.get(r.headers['Location']).data
    assert b'Password changed successfully' not in client.get('/employee-dashboard').data


def test_background_requests_never_touch_the_session_but_page_views_do(client, make_admin):
    """
    Regression test for the flash-message-reappears bug: only HTML page
    navigations may refresh last_active (and so rewrite the cookie).
    fetch()/API-style requests must leave the session cookie alone even when
    last_active is old, otherwise parallel background requests can overwrite
    the cookie with a stale copy that still holds a shown flash message.
    """
    import time
    make_admin(username='owner', password=PW)
    _login(client, 'owner', PW)
    client.get('/employees', headers={'Accept': 'text/html'})   # primes the CSRF token

    old = time.time() - 120
    with client.session_transaction() as sess:
        sess['last_active'] = old

    for headers in ({'Accept': '*/*'},
                    {'Accept': 'application/json'},
                    {'Accept': 'text/html', 'X-Requested-With': 'XMLHttpRequest'}):
        r = client.get('/employees', headers=headers)
        assert r.headers.getlist('Set-Cookie') == [], headers
        with client.session_transaction() as sess:
            assert sess['last_active'] == old

    r = client.get('/employees', headers={'Accept': 'text/html'})
    assert r.headers.getlist('Set-Cookie') != []
    with client.session_transaction() as sess:
        assert sess['last_active'] > old


def test_audit_page_defaults_to_today_with_date_filter(client, make_admin):
    from datetime import timedelta
    from database import db
    from models import LoginAudit, now_ist

    make_admin(username='owner', password=PW)
    old = now_ist() - timedelta(days=3)
    db.session.add(LoginAudit(role='employee', username='OLD-USER', ip='9.9.9.9',
                              event='failed', created_at=old))
    db.session.commit()
    _login(client, 'owner', PW)          # creates a 'success' row for today

    default = client.get('/admin/login-audit').data
    assert b'owner' in default                      # today's event is listed
    assert b'OLD-USER' not in default               # older record is hidden by default
    assert b'Today' in default

    day = client.get('/admin/login-audit?date=' + old.date().isoformat()).data
    assert b'OLD-USER' in day and b'owner' not in day.split(b'Recent login events')[1]

    everything = client.get('/admin/login-audit?date=all').data
    assert b'OLD-USER' in everything and b'owner' in everything
    assert b'All dates' in everything

    # garbage in the date param falls back to today instead of erroring
    bad = client.get('/admin/login-audit?date=not-a-date')
    assert bad.status_code == 200 and b'OLD-USER' not in bad.data


def test_login_form_url_and_tab_memory(client):
    html = client.get('/login?role=employee').data
    assert b'loginForm.action = loginUrl' in html          # role kept in form URL
    assert b"sessionStorage.setItem('loginRole'" in html   # tab remembered in the browser tab
    assert b'const serverRole = ""' in html                # GET: no server override
