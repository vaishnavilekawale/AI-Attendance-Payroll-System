"""
Tests for: vendor-recorded trials (list, no re-trial on reinstall), the corrected
"days left" count, trial tokens not working as licenses, the setup wizard's trial
text, and signing-key rotation (keygen init-keys --rotate / check-key).

Everything is redirected into tmp_path - no real registry, .env, customers.db or key.
"""
import re
import shutil
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

FINGERPRINT = "ab" * 32


# ---------------------------------------------------------------------------
# Fixtures (self-contained, same idea as tests/test_licensing_flow.py)
# ---------------------------------------------------------------------------
@pytest.fixture
def vendor_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "vendor"
    data_dir.mkdir()
    key = Ed25519PrivateKey.generate()
    pem_path = data_dir / "vendor_private_key.pem"
    pem_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    public_hex = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    monkeypatch.setenv("VENDOR_DATA_DIR", str(data_dir))
    monkeypatch.setenv("VENDOR_PRIVATE_KEY_PATH", str(pem_path))
    monkeypatch.setenv("VENDOR_ADMIN_USER", "admin")
    monkeypatch.setenv("VENDOR_ADMIN_PASSWORD", "s3cret-pass")
    monkeypatch.delenv("VENDOR_DEV_MODE", raising=False)
    monkeypatch.delenv("VENDOR_PRIVATE_KEY_PEM", raising=False)
    return {"public_hex": public_hex, "dir": data_dir}


@pytest.fixture
def service(vendor_env):
    from licensing import customer_service
    from licensing.customer_models import init_customer_db
    init_customer_db()

    class _NoEmail:
        sent = []

        def send_license_key_email(self, **kwargs):
            self.sent.append(kwargs)
            return True
    return customer_service.CustomerService(email_service=_NoEmail())


@pytest.fixture
def vendor_client(vendor_env, service, monkeypatch):
    from licensing import customer_service, vendor_app
    monkeypatch.setattr(customer_service, "_customer_service", service)
    flask_vendor = vendor_app.create_vendor_app()
    flask_vendor.config["TESTING"] = True
    return flask_vendor.test_client()


@pytest.fixture
def lm(tmp_path, vendor_env, monkeypatch):
    from licensing import license_manager as lmod
    monkeypatch.setattr(lmod, "LICENSE_PUBLIC_KEY_HEX", vendor_env["public_hex"])
    monkeypatch.setattr(lmod, "_PUBLIC_KEY_IS_DEV_OVERRIDE", True)
    manager = lmod.LicenseManager()
    manager._machine_fingerprint = FINGERPRINT
    manager._legacy_fingerprint = "00" * 32
    monkeypatch.setattr(manager, "_secure_token_path", lambda: str(tmp_path / "app" / "secure_token.dat"))
    monkeypatch.setattr(manager, "_env_file_path", lambda: str(tmp_path / "app" / ".env"))
    monkeypatch.setattr(manager, "_license_file_path", lambda: str(tmp_path / "app" / "license.lic"))
    monkeypatch.setattr(manager, "_database_path", lambda: None)
    monkeypatch.setattr(manager, "_machine_guid", lambda: "test-machine-guid")
    monkeypatch.delenv("LICENSE_TOKEN", raising=False)
    (tmp_path / "app").mkdir()
    return manager


def _trial(service, name="Asha Verma", email="asha@example.com", company="Verma Traders",
           fingerprint=FINGERPRINT, claimed_start=None, now=None):
    return service.start_or_get_trial(name, email, company, fingerprint,
                                      claimed_start=claimed_start, now=now)


# ---------------------------------------------------------------------------
# 4. days-left count
# ---------------------------------------------------------------------------
class TestDaysLeft:
    def test_helper_counts_a_started_day_as_a_full_day(self):
        from licensing.plans import trial_days_remaining, days_until
        start = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
        assert trial_days_remaining(start, start) == 30
        assert trial_days_remaining(start, start + timedelta(hours=23)) == 30
        assert trial_days_remaining(start, start + timedelta(days=1, hours=1)) == 29
        assert trial_days_remaining(start, start + timedelta(days=29, hours=23)) == 1
        assert trial_days_remaining(start, start + timedelta(days=30)) == 0
        assert trial_days_remaining(start, start + timedelta(days=45)) == 0
        # the same rounding for licence expiry: 9 days 23 h left is "10", not "9"
        assert days_until(start + timedelta(days=10), start + timedelta(hours=1)) == 10

    def test_fresh_install_shows_30_not_29(self, lm):
        assert lm.start_trial() is True
        assert lm.get_trial_days_remaining() == 30

    def test_last_day_shows_1_and_day_31_is_over(self, lm):
        lm._secure_write({"trial_start": (datetime.now() - timedelta(days=29)).strftime("%Y-%m-%d")})
        assert lm.start_trial() is True
        assert lm.get_trial_days_remaining() == 1
        lm._secure_write({"trial_start": (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")})
        assert lm.start_trial() is False
        assert lm.get_trial_days_remaining() == 0


# ---------------------------------------------------------------------------
# 1 + 2. vendor sees trials; a reinstall does not get a new trial
# ---------------------------------------------------------------------------
class TestVendorTrials:
    def test_trial_is_recorded_and_listed(self, service):
        result = _trial(service)
        assert result["status"] == "started"
        assert result["days_remaining"] == 30 and result["expired"] is False

        (row,) = service.list_trials()
        assert row["admin_name"] == "Asha Verma"
        assert row["email"] == "asha@example.com"
        assert row["company"] == "Verma Traders"
        assert row["machine_fingerprint"] == FINGERPRINT
        assert row["days_remaining"] == 30 and row["status"] == "active"
        assert row["started_at"]

    def test_days_left_in_the_list_go_down(self, service):
        now = datetime.now(timezone.utc)
        _trial(service, claimed_start=(now - timedelta(days=10)).isoformat())
        (row,) = service.list_trials()
        assert row["days_remaining"] == 20

    def test_reinstall_gets_the_original_start_date_not_a_new_trial(self, service):
        now = datetime.now(timezone.utc)
        first = _trial(service, claimed_start=(now - timedelta(days=12)).isoformat())
        again = _trial(service, name="Someone Else", email="other@example.com",
                       claimed_start=now.isoformat())      # fresh reinstall claims "today"
        assert again["status"] == "existing"
        assert again["started_at"] == first["started_at"]
        assert again["days_remaining"] == first["days_remaining"] == 18
        rows = service.list_trials()
        assert len(rows) == 1 and rows[0]["checkins"] == 2

    def test_expired_trial_stays_expired_after_reinstall(self, service):
        now = datetime.now(timezone.utc)
        _trial(service, claimed_start=(now - timedelta(days=40)).isoformat())
        again = _trial(service)
        assert again["expired"] is True and again["days_remaining"] == 0
        assert service.list_trials()[0]["status"] == "expired"

    def test_unreasonable_claimed_dates_are_ignored(self, service):
        now = datetime.now(timezone.utc)
        for bad, fp in (((now + timedelta(days=3)).isoformat(), "11" * 32),
                        ("not a date", "22" * 32),
                        ((now - timedelta(days=900)).isoformat(), "33" * 32)):
            result = _trial(service, fingerprint=fp, claimed_start=bad)
            assert result["days_remaining"] == 30

    def test_paid_customer_is_marked_converted(self, service):
        from licensing.customer_models import Customer, session_scope
        _trial(service)
        with session_scope() as s:
            s.add(Customer(name="Asha", email="asha@example.com", machine_fingerprint=FINGERPRINT,
                           payment_yn="Y", plan="yearly"))
        assert service.list_trials()[0]["status"] == "converted"

    def test_validation(self, service):
        from licensing.customer_service import ValidationError
        with pytest.raises(ValidationError):
            _trial(service, email="nope")
        with pytest.raises(ValidationError):
            _trial(service, fingerprint="123")
        with pytest.raises(ValidationError):
            _trial(service, name="  ")


class TestVendorHttp:
    def test_trial_start_endpoint(self, vendor_client):
        body = {"admin_name": "Asha", "email": "a@example.com", "company": "Acme",
                "machine_fingerprint": FINGERPRINT}
        first = vendor_client.post("/api/trial/start", json=body)
        assert first.status_code == 200
        data = first.get_json()
        assert data["success"] and data["status"] == "started" and data["days_remaining"] == 30
        again = vendor_client.post("/api/trial/start", json=body).get_json()
        assert again["status"] == "existing" and again["started_at"] == data["started_at"]

    def test_trial_start_rejects_bad_input(self, vendor_client):
        assert vendor_client.post("/api/trial/start", json={"email": "x"}).status_code == 400
        assert vendor_client.post("/api/trial/start", data="junk").status_code == 400

    def test_vendor_trials_list_needs_login_and_shows_rows(self, vendor_client):
        vendor_client.post("/api/trial/start", json={
            "admin_name": "Asha", "email": "a@example.com", "company": "Acme",
            "machine_fingerprint": FINGERPRINT})
        assert vendor_client.get("/vendor/trials").status_code == 401
        import base64
        auth = {"Authorization": "Basic " + base64.b64encode(b"admin:s3cret-pass").decode()}
        resp = vendor_client.get("/vendor/trials", headers=auth)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["count"] == 1
        row = body["trials"][0]
        assert (row["admin_name"], row["company"], row["days_remaining"]) == ("Asha", "Acme", 30)
        assert vendor_client.get("/vendor/trials?status=expired", headers=auth).get_json()["count"] == 0


# ---------------------------------------------------------------------------
# 2. customer side: the app adopts the vendor's date
# ---------------------------------------------------------------------------
class TestClientAdoptsVendorTrial:
    def test_reinstall_with_wiped_markers_continues_the_old_trial(self, lm, service):
        now = datetime.now(timezone.utc)
        _trial(service, claimed_start=(now - timedelta(days=12)).isoformat())

        assert lm.start_trial() is True and lm.get_trial_days_remaining() == 30  # brand-new local trial
        token = service.start_or_get_trial("Asha", "a@example.com", "Acme", FINGERPRINT)["trial_token"]
        ok, message = lm.apply_vendor_trial(token)
        assert ok, message
        assert lm.get_trial_days_remaining() == 18        # the vendor's date wins
        assert lm.start_trial() is True

    def test_reinstall_after_the_trial_ended_is_locked(self, lm, service):
        now = datetime.now(timezone.utc)
        _trial(service, claimed_start=(now - timedelta(days=40)).isoformat())
        assert lm.start_trial() is True                    # local markers are fresh...
        token = service.start_or_get_trial("Asha", "a@example.com", "Acme", FINGERPRINT)["trial_token"]
        assert lm.apply_vendor_trial(token)[0] is True
        assert lm.start_trial() is False                   # ...until the vendor's record is applied
        assert lm.get_trial_days_remaining() == 0

    def test_vendor_date_never_extends_a_longer_running_local_trial(self, lm, service):
        lm._secure_write({"trial_start": (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")})
        token = _trial(service)["trial_token"]            # vendor says "started today"
        assert lm.apply_vendor_trial(token)[0] is True
        assert lm.get_trial_days_remaining() == 10        # earliest date wins

    def test_token_for_another_machine_or_a_forged_one_is_refused(self, lm, service):
        other = _trial(service, fingerprint="cd" * 32)["trial_token"]
        assert lm.apply_vendor_trial(other)[0] is False
        good = _trial(service)["trial_token"]
        head, sig = good.split(".")
        forged = head + "." + ("A" if sig[0] != "A" else "B") + sig[1:]
        assert lm.apply_vendor_trial(forged)[0] is False
        assert lm.apply_vendor_trial("garbage")[0] is False

    def test_trial_token_cannot_be_used_as_a_license(self, lm, service):
        token = _trial(service)["trial_token"]
        ok, message, _ = lm.verify_license_token(token)
        assert ok is False and "trial" in message.lower()
        assert lm.activate_license_from_string(token)[0] is False
        assert lm.is_license_valid()[0] is False

    def test_register_trial_end_to_end(self, lm, vendor_client, monkeypatch):
        """register_trial() -> HTTP -> vendor -> signed token -> markers."""
        from licensing import trial_client
        monkeypatch.setattr(trial_client, "EMBEDDED_LICENSE_SERVER_URL", "https://vendor.example")

        class _Resp:
            def __init__(self, r):
                self.status_code, self._r = r.status_code, r
            def json(self):
                return self._r.get_json()

        def fake_post(url, json=None, timeout=None):
            assert url == "https://vendor.example/api/trial/start"
            return _Resp(vendor_client.post("/api/trial/start", json=json))

        import requests
        monkeypatch.setattr(requests, "post", fake_post)

        lm.start_trial()
        result = trial_client.register_trial("Asha", "a@example.com", "Acme", lm=lm)
        assert result.status == "started" and result.days_remaining == 30
        assert lm.get_vendor_sync_time() is not None

    def test_offline_means_the_local_trial_keeps_running(self, lm, monkeypatch):
        from licensing import trial_client
        import requests
        monkeypatch.setattr(trial_client, "EMBEDDED_LICENSE_SERVER_URL", "https://vendor.example")
        def boom(*a, **k):
            raise requests.ConnectionError("no internet")
        monkeypatch.setattr(requests, "post", boom)
        assert lm.start_trial() is True
        result = trial_client.register_trial("Asha", "a@example.com", "Acme", lm=lm)
        assert result.status == "offline" and not result.ok
        assert lm.start_trial() is True and lm.get_trial_days_remaining() == 30

    def test_no_server_configured_is_a_quiet_no_op(self, lm, monkeypatch):
        from licensing import trial_client
        monkeypatch.setattr(trial_client, "EMBEDDED_LICENSE_SERVER_URL", "")
        monkeypatch.delenv("LICENSE_SERVER_URL", raising=False)
        assert trial_client.register_trial("A", "a@example.com", "C", lm=lm).status == "disabled"
        assert trial_client.sync_due(lm) is False

    def test_packaged_build_ignores_env_server_address(self, monkeypatch):
        import sys
        from licensing import trial_client
        monkeypatch.setattr(trial_client, "EMBEDDED_LICENSE_SERVER_URL", "https://real.example/")
        monkeypatch.setenv("LICENSE_SERVER_URL", "https://fake.example")
        assert trial_client.get_license_server_url() == "https://fake.example"      # dev checkout: env wins
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        assert trial_client.get_license_server_url() == "https://real.example"      # packaged: embedded only


# ---------------------------------------------------------------------------
# 3. wizard text: real days left + admin name
# ---------------------------------------------------------------------------
class TestWizardTrialText:
    def test_license_step_shows_admin_name_and_days_left(self, flask_app, lm, monkeypatch):
        from database import db
        from models import Admin
        import setup_wizard
        monkeypatch.setattr(setup_wizard, "get_license_manager", lambda: lm)
        lm._secure_write({"trial_start": (datetime.now() - timedelta(days=4)).strftime("%Y-%m-%d")})

        with flask_app.app_context():
            db.create_all()
            admin = Admin.query.filter_by(username="Priya Sharma").first()
            if admin is None:
                admin = Admin(username="Priya Sharma", email="priya.wizard@example.com")
                admin.set_password("pw-123456")
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id

        client = flask_app.test_client()
        with client.session_transaction() as sess:
            sess["admin_id"] = admin_id
        resp = client.get("/setup/license")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Priya Sharma" in html
        assert "26 days" in html                       # 30 - 4
        assert "You have a 30-day trial period" not in html
        # plans + machine count match the product
        assert "Monthly" in html and "Yearly" in html and "Lifetime" in html
        assert "Student" not in html and "Small Business" not in html and "2 machine" not in html
        assert "one computer" in html


    def _admin_client(self, flask_app):
        from database import db
        from models import Admin
        with flask_app.app_context():
            db.create_all()
            admin = Admin.query.filter_by(username="Wizard Admin").first()
            if admin is None:
                admin = Admin(username="Wizard Admin", email="wizard.admin@example.com")
                admin.set_password("pw-123456")
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        client = flask_app.test_client()
        with client.session_transaction() as sess:
            sess["admin_id"] = admin_id
        return client

    def test_company_step_registers_the_trial_with_the_vendor(self, flask_app, monkeypatch):
        import setup_wizard
        from licensing.trial_client import TrialSyncResult
        seen = {}

        def fake_register(name, email, company, lm=None):
            seen.update(name=name, email=email, company=company)
            return TrialSyncResult("started", "ok", 30)
        monkeypatch.setattr(setup_wizard, "register_trial", fake_register)
        resp = self._admin_client(flask_app).post("/setup/company", data={
            "company_name": "Verma Traders", "office_start_time": "09:00", "office_end_time": "18:00",
            "grace_period_minutes": "10", "working_hours_per_day": "8"})
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/setup/license")
        assert seen == {"name": "Wizard Admin", "email": "wizard.admin@example.com",
                        "company": "Verma Traders"}

    def test_company_step_locks_when_the_vendor_says_trial_already_used(self, flask_app, monkeypatch):
        import setup_wizard
        from licensing.trial_client import TrialSyncResult
        monkeypatch.setattr(setup_wizard, "register_trial",
                            lambda *a, **k: TrialSyncResult("existing", "ok", 0))
        resp = self._admin_client(flask_app).post("/setup/company", data={
            "company_name": "Verma Traders", "office_start_time": "09:00", "office_end_time": "18:00",
            "grace_period_minutes": "10", "working_hours_per_day": "8"})
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/license")


# ---------------------------------------------------------------------------
# 5. signing-key rotation
# ---------------------------------------------------------------------------
class TestKeyRotation:
    @pytest.fixture
    def sandbox(self, tmp_path, monkeypatch):
        """A copy of license_manager.py to patch, and an empty key directory."""
        import licensing
        from licensing import keygen
        src = tmp_path / "license_manager.py"
        shutil.copy(licensing.__path__[0] + "/license_manager.py", src)
        monkeypatch.setattr(keygen, "_APP_LICENSE_MANAGER", str(src))
        monkeypatch.setenv("VENDOR_DATA_DIR", str(tmp_path))
        monkeypatch.delenv("VENDOR_PRIVATE_KEY_PATH", raising=False)
        monkeypatch.delenv("VENDOR_PRIVATE_KEY_PEM", raising=False)
        return {"keygen": keygen, "app_file": src, "key": tmp_path / "vendor_private_key.pem",
                "dir": tmp_path}

    def test_init_keys_embeds_the_public_key_and_check_key_agrees(self, sandbox):
        keygen = sandbox["keygen"]
        before = keygen._embedded_public_hex(str(sandbox["app_file"]))
        keygen.init_keys()
        assert sandbox["key"].exists()
        after = keygen._embedded_public_hex(str(sandbox["app_file"]))
        assert after != before
        assert keygen.check_key() is True

    def test_embedded_constants_match_the_official_encoder(self, sandbox):
        """What init-keys writes must equal what `license_manager encode-key` would print."""
        from licensing import license_manager as lmod
        keygen = sandbox["keygen"]
        keygen.init_keys()
        public_hex = keygen._embedded_public_hex(str(sandbox["app_file"]))
        official = lmod.encode_public_key_for_embedding(public_hex)
        text = sandbox["app_file"].read_text(encoding="utf-8")
        assert f"_PUBKEY_ENC_PARTS = {official['parts']!r}" in text
        assert f'_PUBKEY_SHA256_PIN = "{official["pin"]}"' in text

    def test_second_init_without_rotate_refuses(self, sandbox):
        keygen = sandbox["keygen"]
        keygen.init_keys()
        with pytest.raises(SystemExit):
            keygen.init_keys()

    def test_rotate_keeps_the_old_key_and_old_licenses_stop_verifying(self, sandbox, monkeypatch):
        keygen = sandbox["keygen"]
        from licensing import license_manager as lmod
        keygen.init_keys()
        old_pem = sandbox["key"].read_bytes()
        old_public = keygen._embedded_public_hex(str(sandbox["app_file"]))
        old_token = keygen.issue_license_token(FINGERPRINT, "Old Customer")

        keygen.init_keys(rotate=True)
        revoked = [p for p in sandbox["dir"].iterdir() if ".revoked-" in p.name]
        assert len(revoked) == 1 and revoked[0].read_bytes() == old_pem   # kept, not deleted
        new_public = keygen._embedded_public_hex(str(sandbox["app_file"]))
        assert new_public != old_public and keygen.check_key() is True

        # An app built with the NEW public key rejects a token signed by the OLD key...
        monkeypatch.setattr(lmod, "LICENSE_PUBLIC_KEY_HEX", new_public)
        monkeypatch.setattr(lmod, "_PUBLIC_KEY_IS_DEV_OVERRIDE", True)
        manager = lmod.LicenseManager()
        manager._machine_fingerprint = FINGERPRINT
        manager._legacy_fingerprint = "00" * 32
        assert manager.verify_license_token(old_token)[0] is False
        # ...and accepts one signed by the new key.
        new_token = keygen.issue_license_token(FINGERPRINT, "New Customer")
        assert manager.verify_license_token(new_token)[0] is True

    def test_check_key_detects_a_mismatch(self, sandbox):
        keygen = sandbox["keygen"]
        keygen.init_keys()
        # swap in a different private key without touching the app
        other = Ed25519PrivateKey.generate()
        sandbox["key"].write_bytes(other.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
        assert keygen.check_key() is False

    def test_reissue_resigns_the_same_plan_and_expiry(self, vendor_env, service):
        from licensing.customer_models import Customer, session_scope
        from licensing.keygen import issue_license_for_plan
        token, expires = issue_license_for_plan(FINGERPRINT, "Asha", "yearly")
        with session_scope() as s:
            c = Customer(name="Asha", email="asha@example.com", machine_fingerprint=FINGERPRINT,
                         payment_yn="Y", plan="yearly", soft_key=token,
                         plan_expires_at=expires.replace(tzinfo=None))
            s.add(c)
            s.flush()
            cid = c.id
        assert service.reissue_license(cid) is True
        fresh = service.get_customer(cid)
        assert fresh.soft_key and fresh.soft_key != token          # signed again (new issued_at)
        assert fresh.plan == "yearly" and fresh.plan_expires_at == expires.replace(tzinfo=None)
        import base64, json
        payload = json.loads(base64.urlsafe_b64decode(fresh.soft_key.split(".")[0] + "=="))
        assert payload["expires_at"] == expires.isoformat() and payload["edition"] == "yearly"
        with pytest.raises(ValueError):
            service.reissue_license(99999)
