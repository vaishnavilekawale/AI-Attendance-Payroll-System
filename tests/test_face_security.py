"""
Tests for two security / compliance guarantees around face data:

1. Biometric consent is a strict opt-in: Employee.biometric_consent_given
   defaults to False, and /api/upload-face-image refuses (403) to accept
   any image for an employee whose consent has not been recorded - creating
   no file and no folder.

2. Face images uploaded via /api/upload-face-image are encrypted at rest
   (Fernet via crypto_utils) before touching disk, using the same
   <timestamp>.jpg naming as the live-capture path, and remain readable
   through the existing decrypting readers (serve_dataset).

A throwaway Fernet key and a temporary dataset folder are used, so these
tests never touch the real .env or the real dataset/ directory.
"""
import os
import pytest
from cryptography.fernet import Fernet

# Minimal but genuine JPEG/PNG signatures - the route validates the magic
# bytes, it does not need a decodable picture.
FAKE_JPEG = b'\xff\xd8\xff\xe0' + b'\x00\x10JFIF' + os.urandom(64)
FAKE_PNG = b'\x89PNG\r\n\x1a\n' + os.urandom(64)


@pytest.fixture
def dataset_dir(tmp_path, monkeypatch):
    """Point the dataset folder at a temp dir and use a throwaway key.

    IMPORTANT: patch the `Config` class object that attendance_routes.py
    actually holds, not just `config.Config`. Other tests
    (test_config_and_setup.py) importlib.reload(config), which rebinds
    `config.Config` to a NEW class while modules that imported it earlier
    keep the old one - patching only `config.Config` would then leave the
    routes writing into the REAL dataset/ folder. Both are patched here so
    the test is safe regardless of test order.
    """
    import config
    import crypto_utils
    import attendance_routes
    monkeypatch.setattr(attendance_routes.Config, 'DATASET_FOLDER', str(tmp_path))
    monkeypatch.setattr(config.Config, 'DATASET_FOLDER', str(tmp_path))
    monkeypatch.setattr(crypto_utils, '_fernet', Fernet(Fernet.generate_key()))
    return tmp_path


def _login_admin(client, admin):
    with client.session_transaction() as s:
        s['admin_id'] = admin.id
        s['admin_username'] = admin.username
        s['user_role'] = 'admin'


def _post_image(client, employee_id, data=FAKE_JPEG, filename='face.jpg'):
    import io
    return client.post(
        '/api/upload-face-image',
        data={'employee_id': str(employee_id), 'image': (io.BytesIO(data), filename)},
        content_type='multipart/form-data',
    )


def _consent(employee, granted=True):
    from database import db
    employee.record_biometric_consent(granted=granted, ip_address='127.0.0.1', policy_version='v1.0')
    db.session.commit()


# ---------------------------------------------------------------- consent

def test_new_employee_has_no_biometric_consent_by_default(make_employee):
    from database import db
    from models import Employee
    emp = make_employee()
    db.session.expire_all()
    fresh = db.session.get(Employee, emp.id)
    assert fresh.biometric_consent_given is False
    assert fresh.biometric_consent_timestamp is None


def test_recording_consent_flips_flag_and_writes_audit_log(make_employee):
    from models import BiometricConsentLog
    emp = make_employee()
    _consent(emp, True)
    assert emp.biometric_consent_given is True
    assert emp.biometric_consent_timestamp is not None
    assert BiometricConsentLog.query.filter_by(employee_id=emp.id, granted=True).count() == 1


def test_upload_refused_without_consent_and_writes_nothing(client, make_admin, make_employee, dataset_dir):
    admin = make_admin()
    emp = make_employee()
    _login_admin(client, admin)

    r = _post_image(client, emp.id)

    assert r.status_code == 403
    body = r.get_json()
    assert body['success'] is False and body['consent_required'] is True
    assert 'consent' in body['message'].lower()
    # Nothing created: not even the employee's dataset folder.
    assert os.listdir(dataset_dir) == []


def test_upload_refused_after_consent_withdrawn(client, make_admin, make_employee, dataset_dir):
    admin = make_admin()
    emp = make_employee()
    _consent(emp, True)
    _consent(emp, False)
    _login_admin(client, admin)

    assert _post_image(client, emp.id).status_code == 403
    assert os.listdir(dataset_dir) == []


def test_capture_face_still_blocked_without_consent(client, make_admin, make_employee):
    admin = make_admin()
    emp = make_employee()
    _login_admin(client, admin)
    r = client.post(f'/capture-face/{emp.id}')
    assert r.status_code == 302 and f'/face-registration/{emp.id}' in r.headers['Location']


# ------------------------------------------------------------- encryption

def test_uploaded_image_is_encrypted_on_disk(client, make_admin, make_employee, dataset_dir):
    import crypto_utils
    admin = make_admin()
    emp = make_employee()
    _consent(emp, True)
    _login_admin(client, admin)

    r = _post_image(client, emp.id, FAKE_JPEG)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['count'] == 1

    emp_dir = dataset_dir / str(emp.id)
    files = os.listdir(emp_dir)
    assert len(files) == 1 and files[0].endswith('.jpg')      # same naming as FaceCapture
    assert not any(f.endswith('.tmp') for f in files)          # atomic write left no temp file

    on_disk = (emp_dir / files[0]).read_bytes()
    assert on_disk != FAKE_JPEG                                # not plaintext
    assert FAKE_JPEG[:16] not in on_disk                       # no plaintext leakage
    assert crypto_utils.is_encrypted(on_disk)                  # valid Fernet token
    assert crypto_utils.decrypt_bytes(on_disk) == FAKE_JPEG    # round-trips exactly


def test_png_upload_is_also_encrypted(client, make_admin, make_employee, dataset_dir):
    import crypto_utils
    admin = make_admin()
    emp = make_employee()
    _consent(emp, True)
    _login_admin(client, admin)

    assert _post_image(client, emp.id, FAKE_PNG, 'face.png').status_code == 200
    emp_dir = dataset_dir / str(emp.id)
    on_disk = (emp_dir / os.listdir(emp_dir)[0]).read_bytes()
    assert crypto_utils.decrypt_bytes(on_disk) == FAKE_PNG


def test_serve_dataset_decrypts_uploaded_image(client, make_admin, make_employee, dataset_dir):
    admin = make_admin()
    emp = make_employee()
    _consent(emp, True)
    _login_admin(client, admin)
    _post_image(client, emp.id, FAKE_JPEG)

    name = os.listdir(dataset_dir / str(emp.id))[0]
    r = client.get(f'/dataset/{emp.id}/{name}')
    assert r.status_code == 200
    assert r.data == FAKE_JPEG                                 # existing reader decrypts transparently


@pytest.mark.parametrize('payload', [b'', b'not an image at all', b'GIF89a' + b'\x00' * 32])
def test_empty_or_non_image_upload_rejected_and_not_stored(client, make_admin, make_employee, dataset_dir, payload):
    admin = make_admin()
    emp = make_employee()
    _consent(emp, True)
    _login_admin(client, admin)

    r = _post_image(client, emp.id, payload)
    assert r.status_code == 400 and r.get_json()['success'] is False
    emp_dir = dataset_dir / str(emp.id)
    assert (not emp_dir.exists()) or os.listdir(emp_dir) == []


def test_failed_write_leaves_no_partial_file(client, make_admin, make_employee, dataset_dir, monkeypatch):
    import crypto_utils
    admin = make_admin()
    emp = make_employee()
    _consent(emp, True)
    _login_admin(client, admin)

    def boom(_):
        raise RuntimeError('encryption failure')
    monkeypatch.setattr(crypto_utils, 'encrypt_bytes', boom)

    r = _post_image(client, emp.id)
    assert r.status_code == 500 and 'encryption failure' in r.get_json()['message']
    emp_dir = dataset_dir / str(emp.id)
    assert os.listdir(emp_dir) == []      # no plaintext fallback, no partial/tmp file