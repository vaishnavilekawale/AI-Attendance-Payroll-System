"""
End-to-end tests for the automated licensing / trial / payment system.

Covers, without any network access:
  * plan pricing + expiry maths
  * keygen (Ed25519) tokens verified by the customer-side LicenseManager
  * the Razorpay webhook: signature check, customer row (payment_yn='Y'),
    plan + expiry, token generation, email dispatch, idempotency, races
  * the customer-side gate: trial -> expired -> lock screen -> activate
  * trial tamper / clock-rollback protection

Nothing here touches the real registry, the real %LOCALAPPDATA% marker, the
real .env or the real customers.db - everything is redirected into tmp_path.
"""
import hashlib
import hmac
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from licensing import plans
from licensing.plans import get_plan, compute_expiry, add_months

WEBHOOK_SECRET = "test_webhook_secret"
FINGERPRINT = "ab" * 32  # 64 hex chars


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def vendor_env(tmp_path, monkeypatch):
    """A throw-away vendor data dir + signing key + gateway secrets."""
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
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "api_secret")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("VENDOR_ADMIN_USER", "admin")
    monkeypatch.setenv("VENDOR_ADMIN_PASSWORD", "s3cret-pass")
    monkeypatch.delenv("VENDOR_DEV_MODE", raising=False)
    monkeypatch.delenv("VENDOR_PRIVATE_KEY_PEM", raising=False)
    for name in ("PLAN_PRICE_MONTHLY", "PLAN_PRICE_YEARLY", "PLAN_PRICE_LIFETIME", "PLAN_CURRENCY"):
        monkeypatch.delenv(name, raising=False)
    return {"public_hex": public_hex, "dir": data_dir}


class FakeEmail:
    """Stands in for LicenseEmailService."""

    def __init__(self, succeed=True):
        self.succeed = succeed
        self.sent = []
        self._lock = threading.Lock()

    def send_license_key_email(self, **kwargs):
        with self._lock:
            self.sent.append(kwargs)
        return self.succeed


@pytest.fixture
def fake_email(vendor_env):
    return FakeEmail()


@pytest.fixture
def vendor_client(vendor_env, fake_email, monkeypatch):
    from licensing import customer_service, vendor_app
    from licensing.customer_models import init_customer_db

    init_customer_db()
    service = customer_service.CustomerService(email_service=fake_email)
    monkeypatch.setattr(customer_service, "_customer_service", service)

    flask_vendor = vendor_app.create_vendor_app()
    flask_vendor.config["TESTING"] = True
    return flask_vendor.test_client()


def _sign(body: bytes, secret=WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _event(order_id, payment_id="pay_TEST0001", amount=999900, currency="INR",
           event="payment.captured", status="captured"):
    return {"event": event, "payload": {"payment": {"entity": {
        "id": payment_id, "order_id": order_id, "amount": amount,
        "currency": currency, "status": status}}}}


def _post_webhook(client, event, signature=None, secret=WEBHOOK_SECRET):
    body = json.dumps(event).encode()
    return client.post("/webhook/razorpay", data=body, content_type="application/json",
                       headers={"X-Razorpay-Signature": signature or _sign(body, secret)})


def _make_order(service, plan="yearly", email="Buyer@Example.com", fingerprint=FINGERPRINT,
                order_id="order_TESTAAAA01"):
    """Insert an order exactly as create_pending_order would, without a gateway call."""
    from licensing.customer_models import Order, session_scope
    p = get_plan(plan)
    with session_scope() as s:
        s.add(Order(gateway_order_id=order_id, plan=p.code, amount=p.amount, currency=p.currency,
                    name="Asha Verma", email=email.lower(), phone="+919876543210",
                    machine_fingerprint=fingerprint, status="created"))
    return order_id


def _customers():
    from licensing.customer_models import Customer, session_scope
    with session_scope() as s:
        rows = s.query(Customer).all()
        s.expunge_all()
        return rows


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------
class TestPlans:
    def test_three_plans_exist(self, vendor_env):
        assert {"monthly", "yearly", "lifetime"} == set(plans.get_plans())

    def test_server_side_prices_and_env_override(self, vendor_env, monkeypatch):
        assert get_plan("yearly").amount == 999900
        monkeypatch.setenv("PLAN_PRICE_YEARLY", "123400")
        assert get_plan("yearly").amount == 123400

    def test_unknown_plan(self, vendor_env):
        assert get_plan("platinum") is None
        assert get_plan("") is None

    def test_monthly_yearly_lifetime_expiry(self, vendor_env):
        now = datetime(2026, 1, 31, 10, 0, tzinfo=timezone.utc)
        assert compute_expiry(get_plan("monthly"), now=now) == datetime(2026, 2, 28, 10, 0, tzinfo=timezone.utc)
        assert compute_expiry(get_plan("yearly"), now=now) == datetime(2027, 1, 31, 10, 0, tzinfo=timezone.utc)
        assert compute_expiry(get_plan("lifetime"), now=now) is None

    def test_leap_year_clamp(self):
        assert add_months(datetime(2027, 1, 31), 1) == datetime(2027, 2, 28)
        assert add_months(datetime(2028, 1, 31), 1) == datetime(2028, 2, 29)
        assert add_months(datetime(2026, 12, 15), 2) == datetime(2027, 2, 15)

    def test_renewal_stacks_on_future_expiry_only(self, vendor_env):
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        future = datetime(2026, 6, 20, tzinfo=timezone.utc)
        past = datetime(2026, 5, 1, tzinfo=timezone.utc)
        assert compute_expiry(get_plan("monthly"), now=now, current_expiry=future) == \
            datetime(2026, 7, 20, tzinfo=timezone.utc)
        assert compute_expiry(get_plan("monthly"), now=now, current_expiry=past) == \
            datetime(2026, 7, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# keygen <-> LicenseManager (Ed25519)
# ---------------------------------------------------------------------------
@pytest.fixture
def lm(tmp_path, vendor_env, monkeypatch):
    """A LicenseManager whose every marker lives in tmp_path and which trusts the test key."""
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


class TestKeygenAndVerification:
    def test_token_verifies_for_its_machine(self, lm):
        from licensing.keygen import issue_license_for_plan
        token, expires = issue_license_for_plan(FINGERPRINT, "Asha", "yearly")
        ok, msg, payload = lm.verify_license_token(token)
        assert ok, msg
        assert payload["edition"] == "yearly"
        assert expires is not None
        assert payload["machine_fingerprint"] == FINGERPRINT

    def test_lifetime_token_has_no_expiry(self, lm):
        from licensing.keygen import issue_license_for_plan
        token, expires = issue_license_for_plan(FINGERPRINT, "Asha", "lifetime")
        ok, _, payload = lm.verify_license_token(token)
        assert ok and expires is None and payload["expires_at"] is None

    def test_other_machine_rejected(self, lm):
        from licensing.keygen import issue_license_for_plan
        token, _ = issue_license_for_plan("cd" * 32, "Asha", "lifetime")
        ok, msg, _ = lm.verify_license_token(token)
        assert not ok and "different machine" in msg

    def test_expired_token_rejected(self, lm):
        from licensing.keygen import issue_license_token
        token = issue_license_token(FINGERPRINT, "Asha",
                                    expires_at=datetime.now(timezone.utc) - timedelta(days=1))
        ok, msg, _ = lm.verify_license_token(token)
        assert not ok and "expired" in msg.lower()

    def test_tampered_payload_rejected(self, lm):
        import base64
        from licensing.keygen import issue_license_token
        token = issue_license_token(FINGERPRINT, "Asha", expires_at=datetime.now(timezone.utc) + timedelta(days=5))
        payload_b64, sig = token.split(".")
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
        payload["expires_at"] = None  # try to make it perpetual
        forged = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).decode().rstrip("=")
        ok, msg, _ = lm.verify_license_token(f"{forged}.{sig}")
        assert not ok and "invalid" in msg.lower()

    def test_token_from_a_different_vendor_key_rejected(self, lm, tmp_path, monkeypatch):
        from licensing.keygen import issue_license_token
        other = Ed25519PrivateKey.generate()
        other_pem = tmp_path / "other.pem"
        other_pem.write_bytes(other.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        token = issue_license_token(FINGERPRINT, "Mallory", private_key_path=str(other_pem))
        ok, _, _ = lm.verify_license_token(token)
        assert not ok

    def test_bad_fingerprint_refused_by_keygen(self, vendor_env):
        from licensing.keygen import issue_license_token
        with pytest.raises(ValueError):
            issue_license_token("not-a-fingerprint", "Asha")


# ---------------------------------------------------------------------------
# Webhook -> customer -> license -> email
# ---------------------------------------------------------------------------
class TestWebhookFlow:
    def test_paid_webhook_creates_customer_license_and_email(self, vendor_client, fake_email, lm):
        from licensing import customer_service
        _make_order(customer_service.get_customer_service(), plan="yearly")

        resp = _post_webhook(vendor_client, _event("order_TESTAAAA01", amount=999900))
        assert resp.status_code == 200, resp.get_json()

        (customer,) = _customers()
        assert customer.payment_yn == "Y"
        assert customer.plan == "yearly"
        assert customer.email == "buyer@example.com"
        assert customer.machine_fingerprint == FINGERPRINT
        assert customer.last_payment_id == "pay_TEST0001"
        assert customer.license_email_sent_yn == "Y"
        # yearly => ~12 months from now
        delta = customer.plan_expires_at - datetime.utcnow()
        assert timedelta(days=364) < delta < timedelta(days=367)

        # the stored token verifies on the buyer's machine
        ok, msg, payload = lm.verify_license_token(customer.soft_key)
        assert ok, msg
        assert payload["customer_name"] == "Asha Verma"

        # and exactly that token was emailed to the buyer
        (mail,) = fake_email.sent
        assert mail["license_key"] == customer.soft_key
        assert mail["customer_email"] == "buyer@example.com"
        assert mail["plan_name"] == "Yearly Plan"
        assert mail["expires_at"] is not None

    @pytest.mark.parametrize("plan,amount,expect_days", [
        ("monthly", 99900, (27, 32)), ("yearly", 999900, (364, 367)), ("lifetime", 2499900, None)])
    def test_each_plan_gets_the_right_expiry(self, vendor_client, fake_email, plan, amount, expect_days):
        from licensing import customer_service
        _make_order(customer_service.get_customer_service(), plan=plan)
        resp = _post_webhook(vendor_client, _event("order_TESTAAAA01", amount=amount))
        assert resp.status_code == 200
        (customer,) = _customers()
        assert customer.plan == plan
        if expect_days is None:
            assert customer.plan_expires_at is None
        else:
            days = (customer.plan_expires_at - datetime.utcnow()).days
            assert expect_days[0] <= days <= expect_days[1]

    def test_bad_signature_rejected_and_nothing_created(self, vendor_client, fake_email):
        from licensing import customer_service
        _make_order(customer_service.get_customer_service())
        resp = _post_webhook(vendor_client, _event("order_TESTAAAA01"), signature="deadbeef")
        assert resp.status_code == 400
        resp = _post_webhook(vendor_client, _event("order_TESTAAAA01"), secret="wrong-secret")
        assert resp.status_code == 400
        assert _customers() == [] and fake_email.sent == []

    def test_missing_signature_header_rejected(self, vendor_client):
        resp = vendor_client.post("/webhook/razorpay", data=b"{}", content_type="application/json")
        assert resp.status_code == 400

    def test_duplicate_delivery_is_idempotent(self, vendor_client, fake_email):
        from licensing import customer_service
        _make_order(customer_service.get_customer_service())
        for _ in range(3):
            assert _post_webhook(vendor_client, _event("order_TESTAAAA01")).status_code == 200
        assert len(_customers()) == 1
        assert len(fake_email.sent) == 1

    def test_both_event_types_for_one_payment_issue_one_license(self, vendor_client, fake_email):
        from licensing import customer_service
        _make_order(customer_service.get_customer_service())
        _post_webhook(vendor_client, _event("order_TESTAAAA01", event="payment.captured"))
        _post_webhook(vendor_client, _event("order_TESTAAAA01", event="order.paid"))
        assert len(fake_email.sent) == 1

    def test_simultaneous_deliveries_issue_exactly_one_license(self, vendor_client, fake_email):
        from licensing import customer_service
        _make_order(customer_service.get_customer_service())
        results = []

        def deliver():
            results.append(_post_webhook(vendor_client, _event("order_TESTAAAA01")).status_code)

        threads = [threading.Thread(target=deliver) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert all(code == 200 for code in results), results
        assert len(fake_email.sent) == 1
        assert len(_customers()) == 1

    def test_wrong_amount_is_rejected_no_license(self, vendor_client, fake_email):
        from licensing import customer_service
        _make_order(customer_service.get_customer_service(), plan="lifetime")
        # attacker/bug: only a monthly amount was actually paid for a lifetime order
        resp = _post_webhook(vendor_client, _event("order_TESTAAAA01", amount=99900))
        assert resp.status_code == 200
        assert _customers() == [] and fake_email.sent == []

    def test_wrong_currency_is_rejected(self, vendor_client, fake_email):
        from licensing import customer_service
        _make_order(customer_service.get_customer_service(), plan="yearly")
        _post_webhook(vendor_client, _event("order_TESTAAAA01", amount=999900, currency="USD"))
        assert _customers() == [] and fake_email.sent == []

    def test_unknown_order_and_ignored_events_are_acknowledged(self, vendor_client, fake_email):
        assert _post_webhook(vendor_client, _event("order_DOESNOTEXIST")).status_code == 200
        assert _post_webhook(vendor_client, {"event": "payment.failed", "payload": {}}).status_code == 200
        assert _post_webhook(vendor_client, _event("order_TESTAAAA01", status="authorized")).status_code == 200
        assert _customers() == [] and fake_email.sent == []

    def test_email_failure_still_stores_license_and_retry_delivers(self, vendor_client, fake_email):
        from licensing import customer_service
        _make_order(customer_service.get_customer_service())
        fake_email.succeed = False
        assert _post_webhook(vendor_client, _event("order_TESTAAAA01")).status_code == 200
        (customer,) = _customers()
        assert customer.payment_yn == "Y" and customer.soft_key
        assert customer.license_email_sent_yn == "N"

        fake_email.succeed = True  # SMTP fixed; gateway retries the delivery
        assert _post_webhook(vendor_client, _event("order_TESTAAAA01")).status_code == 200
        (customer,) = _customers()
        assert customer.license_email_sent_yn == "Y"
        assert len(fake_email.sent) == 2
        assert fake_email.sent[0]["license_key"] == fake_email.sent[1]["license_key"]

    def test_signing_failure_returns_5xx_and_allows_retry(self, vendor_client, fake_email, monkeypatch, vendor_env):
        from licensing import customer_service
        _make_order(customer_service.get_customer_service())
        good_key = vendor_env["dir"] / "vendor_private_key.pem"
        moved = vendor_env["dir"] / "moved.pem"
        good_key.rename(moved)  # private key temporarily unavailable
        assert _post_webhook(vendor_client, _event("order_TESTAAAA01")).status_code == 500
        assert _customers() == []

        moved.rename(good_key)  # fixed; Razorpay retries
        assert _post_webhook(vendor_client, _event("order_TESTAAAA01")).status_code == 200
        assert len(_customers()) == 1 and len(fake_email.sent) == 1

    def test_renewal_extends_existing_expiry(self, vendor_client, fake_email):
        from licensing import customer_service
        svc = customer_service.get_customer_service()
        _make_order(svc, plan="monthly", order_id="order_TESTAAAA01")
        _post_webhook(vendor_client, _event("order_TESTAAAA01", payment_id="pay_ONE", amount=99900))
        (first,) = _customers()

        _make_order(svc, plan="monthly", order_id="order_TESTAAAA02")
        _post_webhook(vendor_client, _event("order_TESTAAAA02", payment_id="pay_TWO", amount=99900))
        (second,) = _customers()  # same email + machine => same customer row

        assert second.id == first.id
        assert second.plan_expires_at > first.plan_expires_at + timedelta(days=27)
        assert second.last_payment_id == "pay_TWO"

    def test_lifetime_customer_is_never_downgraded(self, vendor_client, fake_email):
        from licensing import customer_service
        svc = customer_service.get_customer_service()
        _make_order(svc, plan="lifetime", order_id="order_TESTAAAA01")
        _post_webhook(vendor_client, _event("order_TESTAAAA01", amount=2499900))
        _make_order(svc, plan="monthly", order_id="order_TESTAAAA02")
        _post_webhook(vendor_client, _event("order_TESTAAAA02", payment_id="pay_TWO", amount=99900))
        (customer,) = _customers()
        assert customer.plan == "lifetime" and customer.plan_expires_at is None


class TestCheckoutApi:
    class StubGateway:
        name = "stub"
        public_key = "rzp_test_abc"
        is_configured = True
        created = []

        def create_order(self, amount, currency, receipt, notes=None):
            self.created.append((amount, currency, notes))
            return "order_STUB000001"

        def verify_webhook_signature(self, raw_body, signature):
            return hmac.compare_digest(_sign(raw_body), signature or "")

    def _form(self, **over):
        form = {"plan": "yearly", "name": "Asha Verma", "email": "asha@example.com",
                "phone": "+91 98765 43210", "machine_fingerprint": FINGERPRINT}
        form.update(over)
        return form

    @pytest.fixture
    def client(self, vendor_client, monkeypatch):
        from licensing import vendor_app
        self.StubGateway.created = []
        monkeypatch.setattr(vendor_app, "get_gateway", lambda: self.StubGateway())
        return vendor_client

    def test_create_order_prices_on_the_server(self, client):
        resp = client.post("/api/create-order", json=self._form(amount=1, price=1))
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["order"]["amount"] == 999900
        assert self.StubGateway.created[0][0] == 999900

    @pytest.mark.parametrize("override", [
        {"machine_fingerprint": "short"}, {"machine_fingerprint": "zz" * 32},
        {"email": "not-an-email"}, {"name": ""}, {"phone": "abc"}, {"plan": "free-forever"}])
    def test_validation_errors(self, client, override):
        resp = client.post("/api/create-order", json=self._form(**override))
        assert resp.status_code == 400
        assert resp.get_json()["success"] is False

    def test_fingerprint_with_spaces_and_uppercase_is_normalised(self, client):
        spaced = " ".join([FINGERPRINT.upper()[i:i + 8] for i in range(0, 64, 8)])
        assert client.post("/api/create-order", json=self._form(machine_fingerprint=spaced)).status_code == 200

    def test_order_status_never_leaks_token_or_full_email(self, client, fake_email):
        client.post("/api/create-order", json=self._form())
        _post_webhook(client, _event("order_STUB000001"))
        body = client.get("/api/order-status/order_STUB000001").get_json()
        assert body["status"] == "paid" and body["email_sent"] is True
        assert body["email"] == "a***@example.com"
        assert "soft_key" not in body and "license" not in json.dumps(body).lower()

    def test_pages_render(self, client):
        page = client.get("/")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        for needle in ("Monthly Plan", "Yearly Plan", "Lifetime Plan", 'name="name"', 'name="email"',
                       'name="phone"', 'name="machine_fingerprint"'):
            assert needle in html
        assert client.get("/thank-you?order_id=order_STUB000001").status_code == 200
        assert client.get("/thank-you?order_id=<script>").status_code == 404


class TestAdminApi:
    def test_requires_authentication(self, vendor_client):
        assert vendor_client.get("/vendor/customers").status_code == 401
        assert vendor_client.get("/vendor/stats").status_code == 401

    def test_wrong_password_rejected(self, vendor_client):
        import base64
        bad = base64.b64encode(b"admin:wrong").decode()
        assert vendor_client.get("/vendor/customers", headers={"Authorization": f"Basic {bad}"}).status_code == 401

    def test_disabled_when_no_password_configured(self, vendor_client, monkeypatch):
        monkeypatch.delenv("VENDOR_ADMIN_PASSWORD")
        assert vendor_client.get("/vendor/customers").status_code == 503

    def test_authenticated_list_and_resend(self, vendor_client, fake_email):
        import base64
        from licensing import customer_service
        auth = {"Authorization": "Basic " + base64.b64encode(b"admin:s3cret-pass").decode()}
        _make_order(customer_service.get_customer_service())
        _post_webhook(vendor_client, _event("order_TESTAAAA01"))

        listing = vendor_client.get("/vendor/customers", headers=auth).get_json()
        assert listing["count"] == 1 and listing["customers"][0]["payment_yn"] == "Y"

        cid = listing["customers"][0]["id"]
        assert vendor_client.post(f"/vendor/customers/{cid}/resend", headers=auth).status_code == 200
        assert len(fake_email.sent) == 2

    def test_mass_assignment_blocked(self, vendor_client, fake_email):
        import base64
        from licensing import customer_service
        auth = {"Authorization": "Basic " + base64.b64encode(b"admin:s3cret-pass").decode()}
        _make_order(customer_service.get_customer_service())
        _post_webhook(vendor_client, _event("order_TESTAAAA01"))
        resp = vendor_client.put("/vendor/customers/1", json={"id": 99}, headers=auth)
        assert resp.status_code == 400

    def test_activation_report_marks_installed(self, vendor_client, fake_email):
        from licensing import customer_service
        _make_order(customer_service.get_customer_service())
        _post_webhook(vendor_client, _event("order_TESTAAAA01"))
        (customer,) = _customers()
        ok = vendor_client.post("/vendor/activate", json={
            "license_key": customer.soft_key, "machine_fingerprint": FINGERPRINT})
        assert ok.status_code == 200
        assert _customers()[0].software_installed_yn == "Y"
        bad = vendor_client.post("/vendor/activate", json={
            "license_key": customer.soft_key, "machine_fingerprint": "cd" * 32})
        assert bad.status_code == 404


class TestEmailTemplate:
    def test_customer_name_is_escaped_and_headers_are_safe(self, vendor_env):
        from licensing.license_email_service import LicenseEmailService
        svc = LicenseEmailService()
        svc.smtp_username, svc.smtp_password = "u", "p"
        evil = '<script>alert(1)</script>\r\nBcc: attacker@evil.com'
        msg = svc._build_message(evil, "buyer@example.com", "aaa.bbb", FINGERPRINT, "Yearly Plan", None)
        raw = msg.as_string()
        # No injected header: the newline was collapsed, so "Bcc:" is only text inside the To display name.
        assert msg["Bcc"] is None
        assert "\nBcc:" not in raw
        # The HTML body must carry the name escaped, never as live markup.
        html_body = msg.get_payload()[0].get_payload()[1].get_payload(decode=True).decode()
        assert "<script>alert(1)" not in html_body
        assert "&lt;script&gt;alert(1)" in html_body
        assert 'filename="license.lic"' in raw

    def test_email_contains_token_plan_and_instructions(self, vendor_env):
        from licensing.license_email_service import LicenseEmailService
        svc = LicenseEmailService()
        expires = datetime(2027, 3, 4, tzinfo=timezone.utc)
        html = svc._create_email_template("Asha", "TOKEN.PART", FINGERPRINT, "Yearly Plan", expires)
        assert "TOKEN.PART" in html and "Yearly Plan" in html and "04 March 2027" in html
        assert "Activate" in html
        assert "Never" in svc._create_email_template("A", "T.P", FINGERPRINT, "Lifetime Plan", None)


# ---------------------------------------------------------------------------
# Customer side: trial protection + lock screen
# ---------------------------------------------------------------------------
class TestTrialProtection:
    def test_new_install_starts_trial_in_all_markers(self, lm):
        assert lm.start_trial() is True
        today = datetime.now().strftime("%Y-%m-%d")
        state, status = lm._secure_read()
        assert status == "ok" and state["trial_start"] == today
        assert lm._env_read("TRIAL_START") == today

    def test_deleting_one_marker_does_not_restart_trial(self, lm, tmp_path):
        start = (datetime.now() - timedelta(days=12)).strftime("%Y-%m-%d")
        lm._secure_write({"trial_start": start})
        lm._env_write("TRIAL_START", start)
        assert lm.start_trial() is True and lm.get_trial_days_remaining() <= 18

        (tmp_path / "app" / ".env").unlink()          # user deletes the .env marker
        lm2 = lm
        assert lm2.start_trial() is True
        assert lm2.get_trial_days_remaining() <= 18   # re-seeded from the surviving marker

    def test_trial_expires_after_30_days(self, lm):
        old = (datetime.now() - timedelta(days=31)).strftime("%Y-%m-%d")
        lm._secure_write({"trial_start": old})
        assert lm.start_trial() is False
        assert "expired" in (lm._trial_block_reason or "").lower()

    def test_wiping_the_secure_file_cannot_extend_an_expired_trial(self, lm, tmp_path):
        old = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
        lm._secure_write({"trial_start": old})
        lm._env_write("TRIAL_START", old)
        (tmp_path / "app" / "secure_token.dat").unlink()
        assert lm.start_trial() is False              # .env still holds the original date

    def test_editing_the_encrypted_file_ends_the_trial(self, lm, tmp_path):
        lm.start_trial()
        (tmp_path / "app" / "secure_token.dat").write_bytes(b"hello i edited this")
        assert lm.start_trial() is False
        assert lm._is_trial_revoked()

    def test_clock_rollback_revokes_trial(self, lm):
        lm.start_trial()
        future = datetime.now(timezone.utc) + timedelta(days=10)
        state, _ = lm._secure_read()
        state["last_seen"] = future.isoformat()        # app was last used "10 days from now"
        lm._secure_write(state)
        assert lm.start_trial() is False               # real clock is now 10 days "behind"
        assert "clock" in (lm._trial_block_reason or "").lower()
        assert lm.start_trial() is False               # and stays revoked

    def test_future_trial_start_date_is_rejected(self, lm):
        lm._secure_write({"trial_start": (datetime.now() + timedelta(days=40)).strftime("%Y-%m-%d")})
        assert lm.start_trial() is False


@pytest.fixture
def gated_app(flask_app, lm, monkeypatch):
    """The REAL app with the license gate switched on and pointed at the isolated LicenseManager."""
    from licensing import client_security
    monkeypatch.setattr(client_security, "get_license_manager", lambda: lm)
    flask_app.config["LICENSE_GATE_IN_TESTS"] = True
    client_security.invalidate_access_state()
    yield flask_app
    flask_app.config["LICENSE_GATE_IN_TESTS"] = False
    client_security.invalidate_access_state()


def _expire_trial(lm):
    old = (datetime.now() - timedelta(days=31)).strftime("%Y-%m-%d")
    lm._secure_write({"trial_start": old})
    lm._env_write("TRIAL_START", old)


class TestLockScreen:
    def test_trial_active_app_is_not_blocked(self, gated_app):
        client = gated_app.test_client()
        resp = client.get("/")
        assert resp.status_code != 403
        assert "/license" not in (resp.headers.get("Location") or "")

    def test_trial_shows_days_left_pill(self, gated_app):
        from licensing import client_security
        state = client_security.get_access_state(force=True)
        assert state.mode == "trial" and state.days_remaining >= 29
        assert "Trial:" in client_security._reminder_pill(state)

    def test_expired_trial_blocks_everything_and_shows_lock_screen(self, gated_app, lm):
        from licensing import client_security
        _expire_trial(lm)
        client_security.invalidate_access_state()
        client = gated_app.test_client()

        for path in ("/", "/dashboard", "/login", "/employees", "/does-not-exist"):
            resp = client.get(path)
            assert resp.status_code == 302 and resp.headers["Location"].endswith("/license"), path

        api = client.get("/api/anything", headers={"Accept": "application/json"})
        assert api.status_code == 403 and api.get_json()["error"] == "license_required"

        page = client.get("/license")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert FINGERPRINT in html                       # fingerprint displayed...
        assert 'id="copy-btn"' in html                   # ...with a copy button
        assert 'name="license_key"' in html              # key input
        assert "Activate" in html                        # activate button
        assert "Your 30-day trial has ended" in html

    def test_static_files_still_reachable_while_locked(self, gated_app, lm):
        from licensing import client_security
        _expire_trial(lm)
        client_security.invalidate_access_state()
        resp = gated_app.test_client().get("/static/css/style.css")
        assert resp.status_code in (200, 304)

    def test_activate_valid_token_unlocks_app(self, gated_app, lm, tmp_path):
        from licensing import client_security
        from licensing.keygen import issue_license_for_plan
        _expire_trial(lm)
        client_security.invalidate_access_state()
        client = gated_app.test_client()
        assert client.get("/").status_code == 302

        token, _ = issue_license_for_plan(FINGERPRINT, "Asha Verma", "yearly")
        # emails wrap long lines and people paste quotes: the endpoint must cope
        pasted = '"' + token[:40] + "\n  " + token[40:] + '"\n'
        resp = client.post("/license/activate", json={"license_key": pasted})
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["success"] is True

        assert (tmp_path / "app" / "license.lic").read_text() == token
        after = client.get("/")
        assert after.status_code != 403 and "/license" not in (after.headers.get("Location") or "")
        assert client_security.get_access_state().mode == "licensed"

    def test_activate_lifetime_token(self, gated_app, lm):
        from licensing import client_security
        from licensing.keygen import issue_license_for_plan
        _expire_trial(lm)
        client_security.invalidate_access_state()
        token, _ = issue_license_for_plan(FINGERPRINT, "Asha", "lifetime")
        resp = gated_app.test_client().post("/license/activate", json={"license_key": token})
        assert resp.status_code == 200 and "Lifetime" in resp.get_json()["message"]

    def test_activate_rejects_garbage_wrong_machine_expired_and_tampered(self, gated_app, lm, tmp_path):
        from licensing import client_security
        from licensing.keygen import issue_license_for_plan, issue_license_token
        _expire_trial(lm)
        client_security.invalidate_access_state()
        client = gated_app.test_client()

        assert client.post("/license/activate", json={"license_key": ""}).status_code == 400
        assert client.post("/license/activate", json={"license_key": "hello world"}).status_code == 400

        other, _ = issue_license_for_plan("cd" * 32, "Someone Else", "lifetime")
        r = client.post("/license/activate", json={"license_key": other})
        assert r.status_code == 400 and "different machine" in r.get_json()["message"]

        expired = issue_license_token(FINGERPRINT, "Asha",
                                      expires_at=datetime.now(timezone.utc) - timedelta(days=2))
        r = client.post("/license/activate", json={"license_key": expired})
        assert r.status_code == 400 and "expired" in r.get_json()["message"].lower()

        good, _ = issue_license_for_plan(FINGERPRINT, "Asha", "monthly")
        payload, sig = good.split(".")
        r = client.post("/license/activate", json={"license_key": payload + "." + sig[:-4] + "AAAA"})
        assert r.status_code == 400

        assert not (tmp_path / "app" / "license.lic").exists()
        assert client.get("/").status_code == 302      # still locked

    def test_form_post_with_csrf_token_works(self, gated_app, lm):
        """The lock screen's own form posts the CSRF token it renders; make sure that path works with CSRF ON."""
        from licensing import client_security
        from licensing.keygen import issue_license_for_plan
        _expire_trial(lm)
        client_security.invalidate_access_state()
        gated_app.config["WTF_CSRF_ENABLED"] = True
        try:
            client = gated_app.test_client()
            html = client.get("/license").get_data(as_text=True)
            import re
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
            token, _ = issue_license_for_plan(FINGERPRINT, "Asha", "lifetime")
            no_csrf = client.post("/license/activate", json={"license_key": token})
            assert no_csrf.status_code == 400            # blocked by CSRFProtect
            ok = client.post("/license/activate", json={"license_key": token},
                             headers={"X-CSRFToken": csrf})
            assert ok.status_code == 200
        finally:
            gated_app.config["WTF_CSRF_ENABLED"] = False

    def test_expired_license_locks_again_with_renew_message(self, gated_app, lm, tmp_path):
        from licensing import client_security
        from licensing.keygen import issue_license_token
        _expire_trial(lm)
        token = issue_license_token(FINGERPRINT, "Asha",
                                    expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
        (tmp_path / "app" / "license.lic").write_text(token)
        client_security.invalidate_access_state()
        resp = gated_app.test_client().get("/license")
        assert "Your license has expired" in resp.get_data(as_text=True)

    def test_gate_fails_closed_when_check_crashes(self, gated_app, monkeypatch):
        from licensing import client_security

        def boom():
            raise RuntimeError("registry exploded")
        monkeypatch.setattr(client_security, "get_license_manager", boom)
        client_security.invalidate_access_state()
        assert gated_app.test_client().get("/").status_code == 302

    def test_gate_never_active_in_normal_tests(self, flask_app):
        """Sanity: with TESTING and no opt-in the existing suite is unaffected."""
        resp = flask_app.test_client().get("/")
        assert "/license" not in (resp.headers.get("Location") or "")
