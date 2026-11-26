"""
Tests for storage_sync.py - the database-backed file mirror used on hosts with
an ephemeral disk (Render free + Neon). They use an in-memory fake store, so no
database is needed; the SQL layer (SqlStore) is only a thin wrapper.
"""
import os
import time

import pytest

import storage_sync as ss


class FakeStore:
    def __init__(self):
        self.rows = {}          # path -> dict(data, sha, size, mtime)
        self.fail_get = set()

    def ensure_table(self):
        pass

    def list_rows(self):
        return {p: (r["sha"], r["size"]) for p, r in self.rows.items()}

    def get(self, path):
        if path in self.fail_get or path not in self.rows:
            return None
        r = self.rows[path]
        return r["data"], r["mtime"]

    def put(self, path, data, sha, size, mtime):
        self.rows[path] = {"data": data, "sha": sha, "size": size, "mtime": mtime}

    def delete(self, path):
        self.rows.pop(path, None)


OLD = time.time() - 3600   # "old enough" mtime so files are not deferred


def _write(base, rel, data=b"x", mtime=OLD):
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "BASE_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_BACKEND", "db")
    monkeypatch.delenv("STORAGE_LIMIT_MB", raising=False)
    store = FakeStore()
    monkeypatch.setattr(ss._state, "store", store)
    monkeypatch.setattr(ss._state, "app", None)
    monkeypatch.setattr(ss._state, "restored", False)
    monkeypatch.setattr(ss._state, "deletes_allowed", False)
    monkeypatch.setattr(ss._state, "last_error", None)
    monkeypatch.setattr(ss._state, "last_sync_utc", None)
    ss._manifest.clear()
    return tmp_path, store


def _ready():
    ss._state.restored = True
    ss._state.deletes_allowed = True


# --------------------------------------------------------------- enable flag
def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    assert ss.is_enabled() is False
    assert ss.sync_now()["skipped"] == "disabled"


def test_init_app_does_nothing_when_disabled(monkeypatch):
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    assert ss.init_app(object()) is False


def test_sync_waits_for_restore(env):
    base, store = env
    _write(base, "dataset/1/a.jpg")
    assert ss.sync_now()["skipped"] == "restore not finished"
    assert store.rows == {}


# ------------------------------------------------------------------ what is mirrored
def test_only_expected_files_are_uploaded(env):
    base, store = env
    _write(base, "dataset/1/a.jpg", b"photo")
    _write(base, "uploads/payrolls/2026/01/p.pdf", b"pdf")
    _write(base, "trained_model/face_data.pkl", b"pkl")
    _write(base, "trained_model/embeddings_cache.pkl", b"emb")
    _write(base, "license.lic", b"lic")
    # must NOT be stored:
    _write(base, "trained_model/temp_detect.jpg", b"tmp")
    _write(base, "uploads/backups/automated_backup_1.zip", b"zip")
    _write(base, "uploads/x.tmp", b"tmp")
    _write(base, ".env", b"SECRET=1")
    _write(base, "instance/attendance.db", b"db")
    _write(base, "dataset/.gitkeep", b"")
    _ready()
    res = ss.sync_now()
    assert res["errors"] == 0
    assert sorted(store.rows) == sorted([
        "dataset/1/a.jpg", "uploads/payrolls/2026/01/p.pdf",
        "trained_model/face_data.pkl", "trained_model/embeddings_cache.pkl", "license.lic"])
    assert store.rows["dataset/1/a.jpg"]["data"] == b"photo"


def test_second_sync_uploads_nothing(env):
    base, store = env
    _write(base, "dataset/1/a.jpg", b"photo")
    _ready()
    assert ss.sync_now()["uploaded"] == 1
    again = ss.sync_now()
    assert again["uploaded"] == 0 and again["unchanged"] == 1


def test_changed_file_is_reuploaded_and_deleted_file_removed(env):
    base, store = env
    p = _write(base, "dataset/1/a.jpg", b"v1")
    _write(base, "dataset/1/b.jpg", b"keep")
    _ready()
    ss.sync_now()
    _write(base, "dataset/1/a.jpg", b"version-2")
    (base / "dataset/1/b.jpg").unlink()
    res = ss.sync_now()
    assert res["uploaded"] == 1 and res["deleted"] == 1
    assert store.rows["dataset/1/a.jpg"]["data"] == b"version-2"
    assert "dataset/1/b.jpg" not in store.rows


def test_recently_written_file_is_deferred(env):
    base, store = env
    _write(base, "dataset/1/a.jpg", b"new", mtime=time.time())
    _ready()
    res = ss.sync_now()
    assert res["deferred"] == 1 and store.rows == {}


# ------------------------------------------------------------------ restore
def test_restore_recreates_files_with_same_bytes_and_mtime(env, tmp_path):
    base, store = env
    _write(base, "dataset/7/a.jpg", b"enc-photo", mtime=OLD)
    _write(base, "trained_model/face_data.pkl", b"model")
    _ready()
    ss.sync_now()

    # simulate the platform wiping the disk
    for f in list(base.rglob("*")):
        if f.is_file():
            f.unlink()
    ss._manifest.clear()
    ss._state.restored = False

    stats = ss.restore_all()
    assert stats["restored"] == 2 and stats["errors"] == 0
    assert (base / "dataset/7/a.jpg").read_bytes() == b"enc-photo"
    assert int((base / "dataset/7/a.jpg").stat().st_mtime) == int(OLD)
    assert ss._state.restored and ss._state.deletes_allowed
    # nothing to upload after a clean restore
    assert ss.sync_now()["uploaded"] == 0


def test_restore_skips_files_already_correct(env):
    base, store = env
    _write(base, "dataset/1/a.jpg", b"same")
    _ready()
    ss.sync_now()
    ss._state.restored = False
    stats = ss.restore_all()
    assert stats["unchanged"] == 1 and stats["restored"] == 0


def test_restore_ignores_unsafe_paths(env):
    base, store = env
    store.put("../evil.txt", b"x", ss._sha256(b"x"), 1, 0)
    store.put("dataset/../../evil.txt", b"x", ss._sha256(b"x"), 1, 0)
    store.put("random/file.txt", b"x", ss._sha256(b"x"), 1, 0)
    stats = ss.restore_all()
    assert stats["restored"] == 0
    assert not (base.parent / "evil.txt").exists()


def test_corrupt_row_is_not_written_and_blocks_deletes(env):
    base, store = env
    good, bad = b"good-data", b"bad-data"
    store.put("dataset/1/good.jpg", good, ss._sha256(good), len(good), OLD)
    store.put("dataset/1/bad.jpg", bad, "0" * 64, len(bad), OLD)   # wrong checksum
    stats = ss.restore_all()
    assert stats["errors"] == 1 and stats["restored"] == 1
    assert not (base / "dataset/1/bad.jpg").exists()
    assert ss._state.restored is True and ss._state.deletes_allowed is False
    # the unrestorable row must survive a sync even though the file is not on disk
    ss.sync_now()
    assert "dataset/1/bad.jpg" in store.rows


# ------------------------------------------------------------------ safety rules
def test_empty_disk_never_wipes_the_database(env):
    base, store = env
    store.put("dataset/1/a.jpg", b"x", ss._sha256(b"x"), 1, OLD)
    _ready()                       # (restore claimed success but disk is empty)
    res = ss.sync_now()
    assert res["deleted"] == 0 and "dataset/1/a.jpg" in store.rows


def test_missing_top_level_folder_is_not_treated_as_delete(env):
    base, store = env
    store.put("dataset/1/a.jpg", b"x", ss._sha256(b"x"), 1, OLD)
    _write(base, "uploads/p.pdf", b"pdf")     # disk not empty, but dataset/ is gone
    _ready()
    ss.sync_now()
    assert "dataset/1/a.jpg" in store.rows


def test_storage_limit_stops_uploads_but_deletes_nothing(env, monkeypatch):
    base, store = env
    monkeypatch.setenv("STORAGE_LIMIT_MB", "0.0001")      # ~104 bytes
    _write(base, "dataset/1/small.jpg", b"a" * 50)
    _write(base, "dataset/1/big.jpg", b"b" * 5000)
    _ready()
    res = ss.sync_now()
    assert res["uploaded"] == 1 and res["over_limit"] == 1
    assert "dataset/1/small.jpg" in store.rows and "dataset/1/big.jpg" not in store.rows
    assert "limit" in ss._state.last_error.lower()
    status = ss.storage_status()
    assert status["enabled"] and status["files"] == 1


def test_upload_error_is_reported_not_raised(env, monkeypatch):
    base, store = env
    _write(base, "dataset/1/a.jpg", b"x")
    _ready()

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(store, "put", boom)
    res = ss.sync_now()
    assert res["errors"] == 1
    assert "db down" in ss._state.last_error


def test_rel_ok_rules():
    assert ss._rel_ok("dataset/1/a.jpg")
    assert ss._rel_ok("license.lic")
    assert not ss._rel_ok("../x")
    assert not ss._rel_ok("dataset")
    assert not ss._rel_ok("uploads/backups/a.zip")
    assert not ss._rel_ok("trained_model/temp_detect.jpg")
    assert not ss._rel_ok("instance/attendance.db")
    assert not ss._rel_ok(".env")


# ------------------------------------------------------------------ backup_manager integration
def test_cloud_mode_backup_syncs_instead_of_making_a_zip(env, monkeypatch):
    import backup_manager as bm
    base, store = env
    monkeypatch.setattr(bm, "BASE_DIR", str(base))
    _write(base, "dataset/1/a.jpg", b"photo")
    _ready()

    result = bm.run_backup()

    assert result["success"] is True
    assert "dataset/1/a.jpg" in store.rows
    assert not list(base.rglob("automated_backup_*"))      # no zip written to the ephemeral disk
    assert bm.last_backup_time() is not None


def test_cloud_mode_backup_reports_failure_when_storage_is_full(env, monkeypatch):
    import backup_manager as bm
    base, store = env
    monkeypatch.setattr(bm, "BASE_DIR", str(base))
    monkeypatch.setenv("STORAGE_LIMIT_MB", "0.0001")
    _write(base, "dataset/1/big.jpg", b"b" * 5000)
    _ready()

    result = bm.run_backup()

    assert result["success"] is False
    assert "limit" in result["error"].lower()
