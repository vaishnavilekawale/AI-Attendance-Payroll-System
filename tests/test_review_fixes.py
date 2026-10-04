"""Regression tests for two bugs found in the commercial-readiness review."""
import re


def _login(client, **ids):
    with client.session_transaction() as sess:
        sess.update(ids)


def test_attendance_mark_is_admin_only(flask_app):
    client = flask_app.test_client()
    _login(client, employee_id=1)               # an ordinary employee session
    resp = client.post('/attendance/mark', data={'employee_id': '2', 'confidence': '0.99'})
    assert resp.status_code == 302              # redirected to login, nothing marked


def test_setup_wizard_license_step_accepts_signed_token(flask_app, monkeypatch):
    from licensing import license_manager as lmod
    seen = {}

    class FakeLM:
        machine_fingerprint = 'ab' * 32

        def activate_license_from_string(self, token):
            seen['token'] = token
            return True, 'License activated successfully'

    import setup_wizard
    monkeypatch.setattr(setup_wizard, 'get_license_manager', lambda: FakeLM())
    client = flask_app.test_client()
    _login(client, admin_id=1)
    token = 'AbC.dEf-GHI_jkl' * 20
    resp = client.post('/setup/license', data={'license_key': token[:50] + '\n ' + token[50:]})
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/setup/done')
    assert seen['token'] == token               # whitespace removed, case preserved


def test_setup_wizard_license_step_template_has_no_16_char_limit(flask_app, monkeypatch):
    class FakeLM:
        machine_fingerprint = 'ab' * 32

    import setup_wizard
    monkeypatch.setattr(setup_wizard, 'get_license_manager', lambda: FakeLM())
    client = flask_app.test_client()
    _login(client, admin_id=1)
    html = client.get('/setup/license').get_data(as_text=True)
    assert 'maxlength="16"' not in html and not re.search(r'pattern="\[A-Z0-9\]\{16\}"', html)
