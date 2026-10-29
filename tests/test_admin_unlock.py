"""
Admin unlock tests: another admin unlocks a locked admin (Manage Admins page)
and a locked admin unlocks themselves with a one-time emailed link.
"""
import re
from datetime import timedelta

import pytest

PW = 'correct-horse-battery-staple'


def _login(client, username, password):
    return client.post('/login', data={'username': username, 'password': password, 'role': 'admin'})


def _lock(client, username):
    r = None
    for _ in range(5):
        r = _login(client, username, 'wrong-password')
    return r


@pytest.fixture
def smtp_on(monkeypatch):
    """SMTP 'configured'; captures the unlock emails instead of sending them."""
    sent = []
    monkeypatch.setattr('email_service.EmailService.is_configured', lambda self: True)

    def fake_send(self, to_email, admin_username, link, minutes):
        sent.append({'to': to_email, 'user': admin_username, 'link': link})
        return {'success': True}
    monkeypatch.setattr('email_service.EmailService.send_admin_unlock_link', fake_send)
    return sent


def _token_from(link):
    return re.search(r'/admin/unlock/([^/?]+)', link).group(1)


# ------------------------------------------------- Option 1: another admin

def test_other_admin_can_unlock_a_locked_admin(client, make_admin):
    make_admin(username='alice', email='a@example.com', password=PW)
    bob = make_admin(username='bob', email='b@example.com', password=PW)

    _lock(client, 'bob')
    assert b'Too many failed attempts' in _login(client, 'bob', PW).data   # still locked

    assert _login(client, 'alice', PW).status_code == 302
    page = client.get('/admin/manage-admins')
    assert b'Locked' in page.data and b'Unlock' in page.data
    assert b'Only one admin account exists' not in page.data

    assert client.post(f'/admin/{bob.id}/unlock').status_code == 302
    client.get('/logout')
    assert _login(client, 'bob', PW).status_code == 302


def test_admin_cannot_unlock_themselves(client, make_admin):
    alice = make_admin(username='alice', email='a@example.com', password=PW)
    assert _login(client, 'alice', PW).status_code == 302
    r = client.post(f'/admin/{alice.id}/unlock', follow_redirects=True)
    assert b'cannot unlock your own account' in r.data


def test_unlock_button_not_shown_when_nobody_is_locked(client, make_admin):
    make_admin(username='alice', email='a@example.com', password=PW)
    make_admin(username='bob', email='b@example.com', password=PW)
    _login(client, 'alice', PW)
    page = client.get('/admin/manage-admins')
    assert b'Unlock</button>' not in page.data


def test_single_admin_sees_add_second_admin_hint(client, make_admin):
    make_admin(username='alice', email='a@example.com', password=PW)
    _login(client, 'alice', PW)
    page = client.get('/admin/manage-admins')
    assert b'Only one admin account exists' in page.data
    assert b'Unlock</button>' not in page.data


def test_unlock_admin_requires_admin_login(client, make_admin):
    bob = make_admin(username='bob', email='b@example.com', password=PW)
    r = client.post(f'/admin/{bob.id}/unlock')
    assert r.status_code == 302 and '/login' in r.headers['Location']


# ------------------------------------------------------ Option 2: email link

def test_unlock_via_email_button_only_when_smtp_configured(client, make_admin, monkeypatch):
    make_admin(username='alice', email='a@example.com', password=PW)
    monkeypatch.setattr('email_service.EmailService.is_configured', lambda self: False)
    assert b'Unlock via email' not in _lock(client, 'alice').data

    monkeypatch.setattr('email_service.EmailService.is_configured', lambda self: True)
    assert b'Unlock via email' in _login(client, 'alice', PW).data      # still locked


def test_single_admin_can_unlock_via_email_link(client, make_admin, smtp_on):
    make_admin(username='alice', email='a@example.com', password=PW)
    _lock(client, 'alice')
    assert b'Too many failed attempts' in _login(client, 'alice', PW).data

    r = client.post('/admin/unlock-request', data={'username': 'alice'}, follow_redirects=True)
    assert b'unlock link has been sent' in r.data
    assert len(smtp_on) == 1 and smtp_on[0]['to'] == 'a@example.com'

    token = _token_from(smtp_on[0]['link'])
    # GET only shows the confirm page - it must NOT use the token up.
    assert b'Unlock my login' in client.get(f'/admin/unlock/{token}').data
    assert b'Unlock my login' in client.get(f'/admin/unlock/{token}').data

    r = client.post(f'/admin/unlock/{token}', follow_redirects=True)
    assert b'Your login is unlocked' in r.data
    assert _login(client, 'alice', PW).status_code == 302          # lock gone, password unchanged


def test_unlock_link_is_one_time(client, make_admin, smtp_on):
    make_admin(username='alice', email='a@example.com', password=PW)
    _lock(client, 'alice')
    client.post('/admin/unlock-request', data={'username': 'alice'})
    token = _token_from(smtp_on[0]['link'])
    client.post(f'/admin/unlock/{token}')
    r = client.post(f'/admin/unlock/{token}', follow_redirects=True)
    assert b'invalid, already used, or expired' in r.data


def test_unlock_link_expires(client, make_admin, smtp_on, app_context):
    from database import db
    from models import AdminUnlockToken, now_ist
    make_admin(username='alice', email='a@example.com', password=PW)
    _lock(client, 'alice')
    client.post('/admin/unlock-request', data={'username': 'alice'})
    token = _token_from(smtp_on[0]['link'])
    AdminUnlockToken.query.first().expires_at = now_ist() - timedelta(seconds=1)
    db.session.commit()
    r = client.post(f'/admin/unlock/{token}', follow_redirects=True)
    assert b'invalid, already used, or expired' in r.data
    assert b'Too many failed attempts' in _login(client, 'alice', PW).data   # still locked


def test_garbage_token_is_rejected(client):
    r = client.post('/admin/unlock/not-a-real-token', follow_redirects=True)
    assert b'invalid, already used, or expired' in r.data


def test_unlock_request_has_same_reply_for_unknown_user_and_sends_nothing(client, make_admin, smtp_on):
    make_admin(username='alice', email='a@example.com', password=PW)
    for _ in range(5):
        _login(client, 'ghost', 'x')                                # ghost gets locked too
    r = client.post('/admin/unlock-request', data={'username': 'ghost'}, follow_redirects=True)
    assert b'unlock link has been sent' in r.data
    assert smtp_on == []


def test_unlock_request_needs_an_active_lock(client, make_admin, smtp_on):
    make_admin(username='alice', email='a@example.com', password=PW)
    r = client.post('/admin/unlock-request', data={'username': 'alice'}, follow_redirects=True)
    assert b'unlock link has been sent' in r.data     # same generic reply
    assert smtp_on == []                              # but nothing was emailed


def test_unlock_email_cooldown(client, make_admin, smtp_on):
    make_admin(username='alice', email='a@example.com', password=PW)
    _lock(client, 'alice')
    client.post('/admin/unlock-request', data={'username': 'alice'})
    client.post('/admin/unlock-request', data={'username': 'alice'})
    assert len(smtp_on) == 1


def test_email_failure_does_not_burn_the_cooldown(client, make_admin, monkeypatch):
    make_admin(username='alice', email='a@example.com', password=PW)
    monkeypatch.setattr('email_service.EmailService.is_configured', lambda self: True)
    monkeypatch.setattr('email_service.EmailService.send_admin_unlock_link',
                        lambda self, *a, **k: {'success': False})
    _lock(client, 'alice')
    r = client.post('/admin/unlock-request', data={'username': 'alice'}, follow_redirects=True)
    assert b'Could not send the unlock email' in r.data

    sent = []
    monkeypatch.setattr('email_service.EmailService.send_admin_unlock_link',
                        lambda self, to, u, link, m: sent.append(link) or {'success': True})
    client.post('/admin/unlock-request', data={'username': 'alice'})
    assert len(sent) == 1
