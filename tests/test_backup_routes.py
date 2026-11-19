"""
Route-level tests for the backup settings / run-now / restore endpoints.

backup_manager is pointed at a temporary install folder and its heavy
functions are stubbed where needed, so no real data is read or written.
"""
import pytest

import backup_manager as bm


@pytest.fixture
def backup_sandbox(tmp_path, monkeypatch):
    base = tmp_path / "install"
    (base / "instance").mkdir(parents=True)
    monkeypatch.setattr(bm, "BASE_DIR", str(base))
    monkeypatch.setattr(bm.Config, "UPLOAD_FOLDER", str(base / "uploads"))
    monkeypatch.setattr(bm.Config, "DATASET_FOLDER", str(base / "dataset"))
    monkeypatch.delenv("BACKUP_DIR", raising=False)
    monkeypatch.setenv("BACKUP_AUTO_EXTERNAL", "false")
    return base


def test_backup_settings_requires_admin(logged_in_employee, backup_sandbox):
    client, _ = logged_in_employee
    response = client.post('/admin/backup/settings', data={'backup_time': '22:00', 'backup_keep': '7'})
    assert response.status_code in (302, 403)
    assert bm.schedule_time_str() == "02:00"


def test_admin_can_change_backup_time_keep_and_password(logged_in_admin, backup_sandbox):
    client, _ = logged_in_admin
    response = client.post('/admin/backup/settings', data={
        'backup_time': '22:15', 'backup_keep': '5',
        'backup_password': 'a-long-password', 'backup_password_confirm': 'a-long-password',
    })
    assert response.status_code == 302
    assert bm.schedule_time_str() == "22:15"
    assert bm.backup_keep_count() == 5
    assert bm.get_backup_password() == 'a-long-password'


def test_invalid_backup_time_is_rejected(logged_in_admin, backup_sandbox):
    client, _ = logged_in_admin
    client.post('/admin/backup/settings', data={'backup_time': '99:99', 'backup_keep': '7'})
    assert bm.schedule_time_str() == "02:00"


def test_mismatched_passwords_are_rejected(logged_in_admin, backup_sandbox):
    client, _ = logged_in_admin
    client.post('/admin/backup/settings', data={
        'backup_time': '03:00', 'backup_keep': '7',
        'backup_password': 'a-long-password', 'backup_password_confirm': 'different-one'})
    assert bm.get_backup_password() is None
    assert bm.schedule_time_str() == "02:00"


def test_run_now_creates_a_backup_file(logged_in_admin, backup_sandbox):
    client, _ = logged_in_admin
    response = client.post('/admin/backup/run-now')
    assert response.status_code == 302
    assert bm.list_backups(), "expected a backup file in the backups folder"


def test_restore_needs_the_confirmation_word(logged_in_admin, backup_sandbox, monkeypatch):
    client, _ = logged_in_admin
    monkeypatch.setattr(bm, "restore_backup",
                        lambda *a, **k: pytest.fail("restore must not run without confirmation"))
    response = client.post('/admin/backup/restore', data={'backup_ref': '0|x.zip', 'confirm_text': 'yes'})
    assert response.status_code == 302


def test_restore_rejects_path_tricks(logged_in_admin, backup_sandbox, monkeypatch):
    client, _ = logged_in_admin
    monkeypatch.setattr(bm, "restore_backup",
                        lambda *a, **k: pytest.fail("restore must not run for an invalid reference"))
    response = client.post('/admin/backup/restore',
                           data={'backup_ref': '0|../../evil.zip', 'confirm_text': 'RESTORE'})
    assert response.status_code == 302


def test_settings_page_shows_backup_card(logged_in_admin, backup_sandbox):
    client, _ = logged_in_admin
    response = client.get('/settings')
    assert response.status_code == 200
    assert b'Run backup now' in response.data
    assert b'Restore from a backup' in response.data
