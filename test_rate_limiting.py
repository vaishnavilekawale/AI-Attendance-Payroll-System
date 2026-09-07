"""
Rate limiting tests for the three route groups most exposed to
brute-force/credential-stuffing/enumeration abuse: /login, /register, and
the two forgot-password routes.

Flask-Limiter keys requests by remote address (see extensions.py -
get_remote_address); Flask's test client consistently uses '127.0.0.1',
so repeated calls from the same `client` fixture accumulate against the
same rate-limit bucket, which is exactly what these tests rely on.
"""
import pytest


def test_login_rate_limit_triggers_after_10_per_minute(client, make_admin):
    make_admin(username='owner', password='correct-horse-battery-staple')

    bad_login = {'username': 'owner', 'password': 'wrong', 'role': 'admin'}

    # The configured limit is "10 per minute, 50 per hour" - the first 10
    # requests in the window should all be processed normally (200, since
    # a bad password just re-renders the login page rather than a
    # redirect); it's request #11 that should be rejected outright.
    for i in range(10):
        response = client.post('/login', data=bad_login)
        assert response.status_code == 200, f"request {i+1} unexpectedly rate-limited"

    response = client.post('/login', data=bad_login)
    assert response.status_code == 429


def test_login_rate_limit_does_not_trigger_under_the_limit(client, make_admin):
    make_admin(username='owner', password='correct-horse-battery-staple')

    for _ in range(5):
        response = client.post('/login', data={
            'username': 'owner', 'password': 'wrong', 'role': 'admin',
        })
        assert response.status_code == 200


def test_register_rate_limit_triggers_after_5_per_minute(client):
    # No admin exists yet in this test's fresh DB, so /register redirects
    # to the setup wizard each time (302) - what matters is that it isn't
    # rate-limited until the 6th request.
    for i in range(5):
        response = client.get('/register')
        assert response.status_code == 302, f"request {i+1} unexpectedly rate-limited"

    response = client.get('/register')
    assert response.status_code == 429


def test_forgot_password_rate_limit_triggers_after_5_per_minute(client):
    for i in range(5):
        response = client.post('/forgot-password', data={'username': 'nobody'})
        assert response.status_code in (200, 302), f"request {i+1} unexpectedly rate-limited"

    response = client.post('/forgot-password', data={'username': 'nobody'})
    assert response.status_code == 429


def test_employee_forgot_password_rate_limit_triggers_after_5_per_minute(client):
    for i in range(5):
        response = client.post('/employee-forgot-password', data={'employee_id': 'EMP9999'})
        assert response.status_code in (200, 302), f"request {i+1} unexpectedly rate-limited"

    response = client.post('/employee-forgot-password', data={'employee_id': 'EMP9999'})
    assert response.status_code == 429


def test_rate_limits_are_independent_per_route(client, make_admin):
    """
    Exhausting /login's limit should not affect /forgot-password's separate
    limit, and vice versa - each @limiter.limit(...) decorator tracks its
    own bucket per route, not one shared global counter.
    """
    make_admin(username='owner', password='correct-horse-battery-staple')

    for _ in range(5):
        client.post('/forgot-password', data={'username': 'nobody'})
    # /forgot-password should now be exhausted...
    assert client.post('/forgot-password', data={'username': 'nobody'}).status_code == 429

    # ...but /login should still work normally, since it's a different
    # route with its own independent 10/minute bucket.
    response = client.post('/login', data={
        'username': 'owner', 'password': 'correct-horse-battery-staple', 'role': 'admin',
    })
    assert response.status_code == 302
