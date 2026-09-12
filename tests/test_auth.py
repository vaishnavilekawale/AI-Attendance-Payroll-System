"""
Authentication flow tests: login success/failure for both admin and
employee roles, and the admin force-password-change redirect that was
added as part of removing the auto-seeded admin/admin123 backdoor.
"""


def test_login_page_loads(client):
    response = client.get('/login')
    assert response.status_code == 200
    assert b'login' in response.data.lower()


def test_admin_login_success_redirects_to_dashboard(client, make_admin):
    make_admin(username='owner', password='correct-horse-battery-staple')

    response = client.post('/login', data={
        'username': 'owner',
        'password': 'correct-horse-battery-staple',
        'role': 'admin',
    }, follow_redirects=False)

    assert response.status_code == 302
    assert '/dashboard' in response.headers['Location']

    with client.session_transaction() as sess:
        assert sess.get('admin_id') is not None
        assert sess.get('user_role') == 'admin'


def test_admin_login_wrong_password_fails(client, make_admin):
    make_admin(username='owner', password='correct-horse-battery-staple')

    response = client.post('/login', data={
        'username': 'owner',
        'password': 'totally-wrong-password',
        'role': 'admin',
    })

    assert response.status_code == 200
    assert b'Invalid Password' in response.data

    with client.session_transaction() as sess:
        assert sess.get('admin_id') is None


def test_admin_login_unknown_username_fails(client, make_admin):
    make_admin(username='owner', password='correct-horse-battery-staple')

    response = client.post('/login', data={
        'username': 'someone-who-does-not-exist',
        'password': 'whatever',
        'role': 'admin',
    })

    assert response.status_code == 200
    assert b'Invalid Username' in response.data

    with client.session_transaction() as sess:
        assert sess.get('admin_id') is None


def test_admin_login_missing_fields_does_not_crash(client):
    response = client.post('/login', data={'username': '', 'password': ''})
    assert response.status_code == 200
    assert b'Please provide both username and password' in response.data


def test_admin_login_force_password_change_redirects_to_change_password(client, make_admin):
    """
    Covers the fix added alongside removing the auto-seeded admin/admin123
    account: an admin flagged with force_password_change=True (e.g. the
    legacy default account detected on upgrade, or an admin whose password
    was reset by another admin) must be sent to /change-password instead
    of /dashboard, even though their password check succeeds.
    """
    make_admin(username='legacy_admin', password='admin123', force_password_change=True)

    response = client.post('/login', data={
        'username': 'legacy_admin',
        'password': 'admin123',
        'role': 'admin',
    }, follow_redirects=False)

    assert response.status_code == 302
    assert '/change-password' in response.headers['Location']

    with client.session_transaction() as sess:
        # They ARE logged in (the password was correct) - just forced
        # onward to change it before reaching anywhere else.
        assert sess.get('admin_id') is not None


def test_admin_login_without_force_flag_goes_straight_to_dashboard(client, make_admin):
    """Sanity check for the previous test: force_password_change=False (the
    default) must NOT be redirected to change-password."""
    make_admin(username='owner', password='correct-horse-battery-staple', force_password_change=False)

    response = client.post('/login', data={
        'username': 'owner',
        'password': 'correct-horse-battery-staple',
        'role': 'admin',
    }, follow_redirects=False)

    assert response.status_code == 302
    assert '/change-password' not in response.headers['Location']
    assert '/dashboard' in response.headers['Location']


def test_employee_login_success_first_time_uses_phone_as_default_password(client, make_employee):
    """
    Employee portal login doesn't use Employee.password_hash at all - on
    first login, EmployeeLogin credentials are auto-provisioned with the
    employee's phone number as the default password (see app.py's login()
    admin-vs-employee branch), which is what actually gets checked here.
    """
    make_employee(employee_id='EMP0099', phone='9876543210')

    response = client.post('/login', data={
        'username': 'EMP0099',
        'password': '9876543210',
        'role': 'employee',
    }, follow_redirects=False)

    assert response.status_code == 302

    with client.session_transaction() as sess:
        assert sess.get('employee_id') is not None
        assert sess.get('user_role') == 'employee'


def test_employee_login_unknown_id_fails(client):
    response = client.post('/login', data={
        'username': 'EMP9999',
        'password': 'whatever',
        'role': 'employee',
    })
    assert response.status_code == 200
    assert b'Invalid Employee ID' in response.data
