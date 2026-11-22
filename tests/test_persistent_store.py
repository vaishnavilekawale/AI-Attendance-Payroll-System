"""persistent_store: DB mirroring of files for ephemeral-disk hosts (no real DB needed)."""
import os
import persistent_store as ps


class MemBackend:
    def __init__(self):
        self.rows = {}
        self.fail = False

    def list_meta(self):
        if self.fail:
            raise RuntimeError("db down")
        return {p: sha for p, (_d, sha) in self.rows.items()}

    def get(self, path):
        return self.rows[path][0] if path in self.rows else None

    def put(self, path, data, sha):
        self.rows[path] = (data, sha)

    def delete(self, paths):
        for p in list(paths):
            self.rows.pop(p, None)


def _write(root, rel, data):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def test_push_then_restore_on_fresh_disk(tmp_path):
    db = MemBackend()
    a = tmp_path / "a"; b = tmp_path / "b"
    a.mkdir(); b.mkdir()
    s1 = ps.PersistentStore(str(a), db)
    s1.restore()
    _write(str(a), "dataset/7/img1.jpg", b"ENC-1")
    _write(str(a), "uploads/payslip.pdf", b"PDF")
    _write(str(a), "backups/x.zip.enc", b"BK")
    _write(str(a), "instance/attendance.db", b"sqlite-must-be-skipped")
    _write(str(a), "dataset/.gitkeep", b"")
    assert s1.sync() == {"pushed": 3, "deleted": 0}
    assert set(db.rows) == {"dataset/7/img1.jpg", "uploads/payslip.pdf", "backups/x.zip.enc"}

    # "new container": empty disk, same DB
    s2 = ps.PersistentStore(str(b), db)
    assert s2.restore() == 3
    assert (b / "dataset/7/img1.jpg").read_bytes() == b"ENC-1"
    assert (b / "uploads/payslip.pdf").read_bytes() == b"PDF"


def test_change_and_delete_are_synced(tmp_path):
    db = MemBackend()
    s = ps.PersistentStore(str(tmp_path), db)
    s.restore()
    p = _write(str(tmp_path), "dataset/1/a.jpg", b"one")
    s.sync()
    _write(str(tmp_path), "dataset/1/a.jpg", b"two-longer")
    assert s.sync()["pushed"] == 1
    assert db.rows["dataset/1/a.jpg"][0] == b"two-longer"
    _write(str(tmp_path), "dataset/1/keep.jpg", b"k")
    os.remove(p)
    r = s.sync()
    assert r["deleted"] == 1 and "dataset/1/a.jpg" not in db.rows
    assert s.sync() == {"pushed": 0, "deleted": 0}


def test_no_sync_or_delete_after_failed_restore(tmp_path):
    db = MemBackend()
    db.rows["dataset/1/a.jpg"] = (b"x", ps._sha256(b"x"))
    db.fail = True
    s = ps.PersistentStore(str(tmp_path), db)
    s.restore()
    db.fail = False
    assert s.sync() == {"pushed": 0, "deleted": 0}
    assert "dataset/1/a.jpg" in db.rows


def test_empty_local_never_wipes_db(tmp_path):
    db = MemBackend()
    db.rows["uploads/a.pdf"] = (b"x", ps._sha256(b"x"))
    s = ps.PersistentStore(str(tmp_path), db)
    s._restored_ok = True            # pretend restore ran but disk is somehow empty
    assert s.sync()["deleted"] == 0
    assert "uploads/a.pdf" in db.rows


def test_unsafe_remote_path_not_written(tmp_path):
    db = MemBackend()
    db.rows["../evil.txt"] = (b"x", ps._sha256(b"x"))
    s = ps.PersistentStore(str(tmp_path / "d"), db)
    os.makedirs(tmp_path / "d")
    s.restore()
    assert not (tmp_path / "evil.txt").exists()


def test_oversize_file_skipped(tmp_path):
    db = MemBackend()
    s = ps.PersistentStore(str(tmp_path), db, max_file_mb=0)
    s.restore()
    _write(str(tmp_path), "uploads/big.bin", b"x")
    assert s.sync()["pushed"] == 0
