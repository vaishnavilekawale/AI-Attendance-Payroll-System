"""
Tests for backup_manager.py: daily schedule + catch-up logic, 7-backup
retention, encryption, verification, failure reporting and restore.

Every test runs against a throw-away BASE_DIR (pytest tmp_path), so no real
data, .env or backups folder is ever touched.
"""
import os
import sqlite3
import zipfile
from datetime import datetime, timedelta

import pytest

import backup_manager as bm


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A fake install folder with a small database, one upload, one face image, .env and license."""
    base = tmp_path / "install"
    (base / "instance").mkdir(parents=True)
    (base / "uploads").mkdir()
    (base / "dataset").mkdir()

    db = base / "instance" / "attendance.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE employee (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO employee (name) VALUES ('Asha')")
    conn.commit()
    conn.close()

    (base / "uploads" / "photo.jpg").write_bytes(b"photo-bytes")
    (base / "dataset" / "face1.enc").write_bytes(b"face-bytes")
    (base / ".env").write_text("FACE_DATA_ENCRYPTION_KEY=abc\n")
    (base / "license.lic").write_text("LICENSE-TOKEN")

    monkeypatch.setattr(bm, "BASE_DIR", str(base))
    monkeypatch.setattr(bm.Config, "UPLOAD_FOLDER", str(base / "uploads"))
    monkeypatch.setattr(bm.Config, "DATASET_FOLDER", str(base / "dataset"))
    monkeypatch.delenv("BACKUP_DIR", raising=False)
    monkeypatch.delenv("BACKUP_TIME", raising=False)
    monkeypatch.delenv("BACKUP_KEEP", raising=False)
    monkeypatch.setenv("BACKUP_AUTO_EXTERNAL", "false")
    return base


def _names(directory):
    return sorted(os.listdir(directory))


def _employee_names(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute("SELECT name FROM employee ORDER BY id")]
    finally:
        conn.close()


# ---------------------------------------------------------------- settings
def test_defaults_are_2am_and_seven_backups(sandbox):
    assert bm.schedule_time_str() == "02:00"
    assert bm.backup_keep_count() == 7


def test_save_time_and_keep(sandbox):
    bm.save_backup_settings(time_str="22:30", keep=10)
    assert bm.schedule_time_str() == "22:30"
    assert bm.schedule_hm() == (22, 30)
    assert bm.backup_keep_count() == 10


@pytest.mark.parametrize("bad", ["25:00", "12:60", "abc", "", "7", "12:3x"])
def test_invalid_time_rejected(sandbox, bad):
    with pytest.raises(bm.BackupError):
        bm.save_backup_settings(time_str=bad)


@pytest.mark.parametrize("bad", [0, 61, "x", -1])
def test_invalid_keep_rejected(sandbox, bad):
    with pytest.raises(bm.BackupError):
        bm.save_backup_settings(keep=bad)


def test_short_password_rejected_and_clear_works(sandbox):
    with pytest.raises(bm.BackupError):
        bm.save_backup_settings(new_password="short")
    bm.save_backup_settings(new_password="long-enough-pw")
    assert bm.get_backup_password() == "long-enough-pw"
    bm.save_backup_settings(clear_password=True)
    assert bm.get_backup_password() is None


# ------------------------------------------------------------- encryption
def test_encrypt_roundtrip_multiple_chunks(sandbox, tmp_path, monkeypatch):
    monkeypatch.setattr(bm, "ENC_CHUNK", 1000)  # force several chunks
    data = os.urandom(5500)
    src, enc, out = tmp_path / "a.bin", tmp_path / "a.enc", tmp_path / "a.out"
    src.write_bytes(data)
    bm.encrypt_file(str(src), str(enc), "pw-12345678")
    assert bm.is_encrypted_file(str(enc))
    assert data[:50] not in enc.read_bytes()
    bm.decrypt_file(str(enc), str(out), "pw-12345678")
    assert out.read_bytes() == data


def test_encrypt_empty_file_roundtrip(sandbox, tmp_path):
    src, enc, out = tmp_path / "e.bin", tmp_path / "e.enc", tmp_path / "e.out"
    src.write_bytes(b"")
    bm.encrypt_file(str(src), str(enc), "pw-12345678")
    bm.decrypt_file(str(enc), str(out), "pw-12345678")
    assert out.read_bytes() == b""


def test_wrong_password_detected(sandbox, tmp_path):
    src, enc = tmp_path / "a.bin", tmp_path / "a.enc"
    src.write_bytes(b"secret" * 100)
    bm.encrypt_file(str(src), str(enc), "right-password")
    with pytest.raises(bm.BackupPasswordError):
        bm.verify_encrypted_file(str(enc), "wrong-password")


def test_truncated_encrypted_file_detected(sandbox, tmp_path, monkeypatch):
    monkeypatch.setattr(bm, "ENC_CHUNK", 1000)
    src, enc = tmp_path / "a.bin", tmp_path / "a.enc"
    src.write_bytes(os.urandom(4000))
    bm.encrypt_file(str(src), str(enc), "right-password")
    raw = enc.read_bytes()
    # Cut exactly after the first complete chunk: must not look like a valid file.
    first_chunk_end = len(bm.ENC_MAGIC) + 20 + 4 + int.from_bytes(raw[len(bm.ENC_MAGIC) + 20:len(bm.ENC_MAGIC) + 24], "big")
    enc.write_bytes(raw[:first_chunk_end])
    with pytest.raises(bm.BackupError):
        bm.verify_encrypted_file(str(enc), "right-password")


# -------------------------------------------------------------- run_backup
def test_run_backup_contents_and_verification(sandbox):
    result = bm.run_backup(now_stamp="20260101_020000")
    assert result["success"], result
    assert result["path"].endswith("automated_backup_20260101_020000.zip")
    assert not result["encrypted"]
    with zipfile.ZipFile(result["path"]) as zf:
        names = set(zf.namelist())
    assert {"instance/attendance.db", "uploads/photo.jpg", "dataset/face1.enc",
            ".env", "license.lic"} <= names
    assert not any(n.startswith("backups") for n in names)  # never nested
    assert not [f for f in _names(sandbox / "backups") if f.endswith(".tmp")]
    assert bm.backup_health() is None


def test_run_backup_encrypted_when_password_set(sandbox):
    bm.save_backup_settings(new_password="my-backup-pass")
    result = bm.run_backup(now_stamp="20260101_020000")
    assert result["success"] and result["encrypted"]
    assert result["path"].endswith(".zip.enc")
    files = _names(sandbox / "backups")
    assert files == ["automated_backup_20260101_020000.zip.enc"]  # no plain zip / tmp left
    assert b"FACE_DATA_ENCRYPTION_KEY" not in open(result["path"], "rb").read()


def test_retention_keeps_newest_seven(sandbox):
    for day in range(1, 10):  # 9 daily backups
        assert bm.run_backup(now_stamp=f"202601{day:02d}_020000")["success"]
    files = _names(sandbox / "backups")
    assert len(files) == 7
    assert files[0] == "automated_backup_20260103_020000.zip"
    assert files[-1] == "automated_backup_20260109_020000.zip"


def test_retention_follows_keep_setting(sandbox):
    bm.save_backup_settings(keep=3)
    for day in range(1, 7):
        bm.run_backup(now_stamp=f"202601{day:02d}_020000")
    assert len(_names(sandbox / "backups")) == 3


def test_external_copy_made(sandbox, tmp_path, monkeypatch):
    ext = tmp_path / "external"
    monkeypatch.setenv("BACKUP_DIR", str(ext))
    result = bm.run_backup(now_stamp="20260101_020000")
    assert result["success"]
    assert result["external_path"] == str(ext / "automated_backup_20260101_020000.zip")
    assert os.path.exists(result["external_path"])


def test_corrupt_database_fails_and_is_reported(sandbox):
    (sandbox / "instance" / "attendance.db").write_bytes(b"this is not a sqlite database" * 50)
    result = bm.run_backup(now_stamp="20260101_020000")
    # The snapshot step falls back to a raw copy, the integrity check must then reject it.
    assert not result["success"]
    assert result["error"]
    assert _names(sandbox / "backups") == []  # nothing half-valid left behind
    health = bm.backup_health()
    assert health and health["level"] == "danger"
    assert bm.backup_status()["last_error"]


def test_success_after_failure_clears_error(sandbox):
    bm._update_state(last_error="boom", last_error_utc=datetime.now().astimezone().isoformat())
    assert bm.run_backup(now_stamp="20260101_020000")["success"]
    assert bm.backup_status()["last_error"] is None
    assert bm.backup_health() is None


# ---------------------------------------------------- schedule / catch-up
def test_most_recent_slot(sandbox):
    bm.save_backup_settings(time_str="22:00")
    assert bm.most_recent_slot(datetime(2026, 1, 10, 23, 0)) == datetime(2026, 1, 10, 22, 0)
    assert bm.most_recent_slot(datetime(2026, 1, 10, 9, 0)) == datetime(2026, 1, 9, 22, 0)


def test_overdue_when_no_backup_exists(sandbox):
    assert bm.is_backup_overdue()


def test_not_overdue_right_after_backup(sandbox):
    bm.run_backup(now_stamp="20260101_020000")
    assert not bm.is_backup_overdue()


def test_overdue_after_missed_slot(sandbox):
    """Server was off at the scheduled time -> next startup must catch up."""
    bm.run_backup(now_stamp="20260101_020000")
    # Pretend it is now 3 days later, after the 02:00 slot.
    future = datetime.now() + timedelta(days=3)
    assert bm.is_backup_overdue(future)


def test_not_overdue_before_todays_slot_if_yesterdays_done(sandbox):
    bm.save_backup_settings(time_str="23:00")
    bm.run_backup(now_stamp="20260101_020000")
    # Backup just made; "now" is a minute later - next slot (23:00) is hours away at worst.
    assert not bm.is_backup_overdue(datetime.now() + timedelta(minutes=1))


def test_catchup_then_seven_most_recent_kept(sandbox):
    """A week-plus of missed days produces ONE catch-up backup; the newest 7 stay."""
    for day in range(1, 8):
        bm.run_backup(now_stamp=f"202601{day:02d}_020000")
    assert bm.is_backup_overdue(datetime.now() + timedelta(days=10))
    bm.run_backup(now_stamp="20260120_020000")
    files = _names(sandbox / "backups")
    assert len(files) == 7 and files[-1] == "automated_backup_20260120_020000.zip"


def test_health_warns_when_never_backed_up(sandbox):
    health = bm.backup_health()
    assert health and health["level"] == "warning"


# ----------------------------------------------------------------- restore
def _wreck_data(sandbox):
    conn = sqlite3.connect(sandbox / "instance" / "attendance.db")
    conn.execute("DELETE FROM employee")
    conn.execute("INSERT INTO employee (name) VALUES ('WRONG')")
    conn.commit()
    conn.close()
    (sandbox / "uploads" / "photo.jpg").write_bytes(b"changed")


@pytest.mark.parametrize("replace_file", [False, True])
def test_restore_plain_backup(sandbox, replace_file):
    backup = bm.run_backup(now_stamp="20260101_020000")["path"]
    _wreck_data(sandbox)

    result = bm.restore_backup(backup, restore_env=True, replace_db_file=replace_file)

    assert result["success"], result
    assert _employee_names(sandbox / "instance" / "attendance.db") == ["Asha"]
    assert (sandbox / "uploads" / "photo.jpg").read_bytes() == b"photo-bytes"
    assert result["env_restored"] and result["restart_required"]
    assert (sandbox / ".env.before_restore").exists()
    # A safety copy of the pre-restore state exists and is itself restorable.
    assert os.path.basename(result["safety_backup"]).startswith("pre_restore_")
    assert os.path.exists(result["safety_backup"])


def test_restore_encrypted_backup_needs_correct_password(sandbox):
    bm.save_backup_settings(new_password="my-backup-pass")
    backup = bm.run_backup(now_stamp="20260101_020000")["path"]
    _wreck_data(sandbox)

    no_pw = bm.restore_backup(backup)
    assert not no_pw["success"] and "password" in no_pw["error"].lower()

    bad_pw = bm.restore_backup(backup, password="not-the-password")
    assert not bad_pw["success"]
    assert _employee_names(sandbox / "instance" / "attendance.db") == ["WRONG"]  # untouched

    ok = bm.restore_backup(backup, password="my-backup-pass")
    assert ok["success"], ok
    assert _employee_names(sandbox / "instance" / "attendance.db") == ["Asha"]


def test_restore_can_skip_env_and_only_restores_missing_license(sandbox):
    backup = bm.run_backup(now_stamp="20260101_020000")["path"]
    (sandbox / ".env").write_text("FACE_DATA_ENCRYPTION_KEY=NEW\n")
    (sandbox / "license.lic").write_text("NEWER-LICENSE")

    result = bm.restore_backup(backup, restore_env=False)

    assert result["success"]
    assert not result["env_restored"] and not result["license_restored"]
    assert "NEW" in (sandbox / ".env").read_text()
    assert (sandbox / "license.lic").read_text() == "NEWER-LICENSE"

    os.remove(sandbox / "license.lic")
    again = bm.restore_backup(backup, restore_env=False)
    assert again["license_restored"]
    assert (sandbox / "license.lic").read_text() == "LICENSE-TOKEN"


def test_restore_rejects_zip_slip(sandbox, tmp_path):
    evil = tmp_path / "evil.zip"
    conn_path = sandbox / "instance" / "attendance.db"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.write(conn_path, "instance/attendance.db")
        zf.writestr("uploads/../../outside.txt", "pwned")
    result = bm.restore_backup(str(evil))
    assert not result["success"] and "unsafe" in result["error"].lower()
    assert not (tmp_path / "outside.txt").exists()


def test_restore_rejects_non_backup_and_missing_db(sandbox, tmp_path):
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip")
    assert not bm.restore_backup(str(junk))["success"]

    no_db = tmp_path / "nodb.zip"
    with zipfile.ZipFile(no_db, "w") as zf:
        zf.writestr("uploads/a.txt", "x")
    result = bm.restore_backup(str(no_db))
    assert not result["success"] and "database" in result["error"].lower()


def test_restore_corrupt_db_in_backup_leaves_current_data_alone(sandbox, tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("instance/attendance.db", b"garbage" * 100)
    before = _employee_names(sandbox / "instance" / "attendance.db")
    result = bm.restore_backup(str(bad))
    assert not result["success"]
    assert _employee_names(sandbox / "instance" / "attendance.db") == before


# -------------------------------------------------------- listing / refs
def test_list_and_resolve_backups(sandbox):
    bm.run_backup(now_stamp="20260101_020000")
    items = bm.list_backups()
    assert len(items) == 1 and items[0]["name"] == "automated_backup_20260101_020000.zip"
    assert os.path.isfile(bm.resolve_backup(items[0]["ref"]))


@pytest.mark.parametrize("ref", ["", "nonsense", "0|../../etc/passwd.zip", "9|x.zip",
                                 "0|missing.zip", "0|notabackup.txt", "-1|a.zip"])
def test_resolve_backup_refuses_bad_refs(sandbox, ref):
    bm.run_backup(now_stamp="20260101_020000")
    with pytest.raises(bm.BackupError):
        bm.resolve_backup(ref)
