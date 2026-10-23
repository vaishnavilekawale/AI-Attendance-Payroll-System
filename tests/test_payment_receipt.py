"""
Tests for the payment receipt: after a captured payment the customer gets the
license email WITH a PDF receipt (receipt no, application id, name, transaction
id, payment id, status, payment date, amount ...), the vendor can download the
same PDF, and a receipt problem never blocks the license.
"""
import base64
import hashlib
import hmac
import json
import re
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

FINGERPRINT = "ab" * 32
SECRET = "test_webhook_secret"
PAID_AT = int(datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc).timestamp())   # 02:30 PM IST


class FakeEmail:
    def __init__(self):
        self.sent = []

    def send_license_key_email(self, **kwargs):
        self.sent.append(kwargs)
        return True


@pytest.fixture
def vendor_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "vendor"
    data_dir.mkdir()
    key = Ed25519PrivateKey.generate()
    pem = data_dir / "vendor_private_key.pem"
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    monkeypatch.setenv("VENDOR_DATA_DIR", str(data_dir))
    monkeypatch.setenv("VENDOR_PRIVATE_KEY_PATH", str(pem))
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "api_secret")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("VENDOR_ADMIN_USER", "admin")
    monkeypatch.setenv("VENDOR_ADMIN_PASSWORD", "s3cret-pass")
    monkeypatch.setenv("COMPANY_NAME", "Acme Software")
    monkeypatch.delenv("VENDOR_DEV_MODE", raising=False)
    monkeypatch.delenv("VENDOR_PRIVATE_KEY_PEM", raising=False)
    return data_dir


@pytest.fixture
def fake_email(vendor_env):
    return FakeEmail()


@pytest.fixture
def client(vendor_env, fake_email, monkeypatch):
    from licensing import customer_service, vendor_app
    from licensing.customer_models import init_customer_db
    init_customer_db()
    monkeypatch.setattr(customer_service, "_customer_service",
                        customer_service.CustomerService(email_service=fake_email))
    app = vendor_app.create_vendor_app()
    app.config["TESTING"] = True
    return app.test_client()


def _make_order(order_id="order_TESTAAAA01", plan="yearly", name="Asha Verma"):
    from licensing.customer_models import Order, session_scope
    from licensing.plans import get_plan
    p = get_plan(plan)
    with session_scope() as s:
        s.add(Order(gateway_order_id=order_id, plan=p.code, amount=p.amount, currency=p.currency,
                    name=name, email="buyer@example.com", phone="+919876543210",
                    machine_fingerprint=FINGERPRINT, status="created"))
    return order_id


def _pay(client, order_id="order_TESTAAAA01", payment_id="pay_TEST0001", amount=999900, created_at=PAID_AT):
    event = {"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": payment_id, "order_id": order_id, "amount": amount, "currency": "INR",
        "status": "captured", "created_at": created_at}}}}
    body = json.dumps(event).encode()
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/webhook/razorpay", data=body, content_type="application/json",
                       headers={"X-Razorpay-Signature": sig})


def _auth():
    return {"Authorization": "Basic " + base64.b64encode(b"admin:s3cret-pass").decode()}


# ---------------------------------------------------------------------------
class TestReceiptAfterPayment:
    def test_license_email_carries_the_receipt_data(self, client, fake_email):
        _make_order()
        assert _pay(client).status_code == 200
        (mail,) = fake_email.sent
        r = mail["receipt"]
        assert r["customer_name"] == "Asha Verma" and r["customer_email"] == "buyer@example.com"
        assert r["order_id"] == "order_TESTAAAA01" and r["payment_id"] == "pay_TEST0001"
        assert r["amount"] == 999900 and r["currency"] == "INR" and r["status"] == "Paid"
        assert r["plan_name"] == "Yearly Plan"
        assert r["application_id"].startswith("APP-")
        assert re.fullmatch(r"RCPT-\d{4}-\d{5}", r["receipt_no"])
        assert r["paid_at"] == datetime.fromtimestamp(PAID_AT, tz=timezone.utc)   # the gateway's time
        assert r["valid_until"] is not None

    def test_lifetime_receipt_has_no_expiry(self, client, fake_email):
        _make_order(plan="lifetime")
        _pay(client, amount=2499900)
        assert fake_email.sent[0]["receipt"]["valid_until"] is None

    def test_receipt_number_and_payment_time_are_stored(self, client):
        from licensing.customer_models import Order, session_scope
        _make_order()
        _pay(client)
        with session_scope() as s:
            order = s.query(Order).one()
            assert order.receipt_no and order.paid_at == datetime(2026, 10, 23, 9, 0)

    def test_missing_gateway_time_falls_back_to_now(self, client, fake_email):
        _make_order()
        _pay(client, created_at=None)
        assert fake_email.sent[0]["receipt"]["paid_at"] is not None

    def test_duplicate_webhook_does_not_send_a_second_receipt(self, client, fake_email):
        _make_order()
        _pay(client)
        first = fake_email.sent[0]["receipt"]["receipt_no"]
        _pay(client)
        assert len(fake_email.sent) == 1
        from licensing.customer_models import Order, session_scope
        with session_scope() as s:
            assert s.query(Order).one().receipt_no == first

    def test_unsent_email_is_retried_with_the_same_receipt(self, client, fake_email):
        state = {"fail": True}

        def flaky(**kw):
            fake_email.sent.append(kw)
            return not state["fail"]
        fake_email.send_license_key_email = flaky
        _make_order()
        _pay(client)
        state["fail"] = False
        _pay(client)                                # gateway retry
        assert len(fake_email.sent) == 2
        assert fake_email.sent[0]["receipt"]["receipt_no"] == fake_email.sent[1]["receipt"]["receipt_no"]

    def test_resend_attaches_the_receipt(self, client, fake_email):
        from licensing import customer_service
        _make_order()
        _pay(client)
        customer_id = customer_service.get_customer_service().get_all_customers()[0].id
        customer_service.get_customer_service().resend_license_email(customer_id)
        assert fake_email.sent[-1]["receipt"]["payment_id"] == "pay_TEST0001"


class TestReceiptPdf:
    DATA = dict(receipt_no="RCPT-2026-00042", application_id="APP-00007", customer_name="Asha Verma",
                customer_email="a@x.com", customer_phone="+919876543210", plan_name="Yearly Plan",
                amount=999900, currency="INR", status="Paid", order_id="order_TESTAAAA01",
                payment_id="pay_TEST0001", paid_at=datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc),
                valid_until=datetime(2027, 10, 23, tzinfo=timezone.utc), machine_fingerprint=FINGERPRINT)

    def test_pdf_contains_every_field(self, vendor_env):
        from licensing.receipt_generator import build_receipt_pdf
        pdf = build_receipt_pdf(self.DATA)
        assert pdf.startswith(b"%PDF")
        for text in (b"RCPT-2026-00042", b"APP-00007", b"Asha Verma", b"a@x.com", b"Yearly Plan",
                     b"INR 9,999.00", b"Paid", b"order_TESTAAAA01", b"pay_TEST0001",
                     b"23 October 2026, 02:30 PM IST", b"23 October 2027", b"Acme Software"):
            assert text in pdf, text

    def test_company_details_and_logo_appear_in_the_header(self, vendor_env, monkeypatch, tmp_path):
        from PIL import Image
        from licensing.receipt_generator import build_receipt_pdf
        logo = tmp_path / "logo.png"
        Image.new("RGB", (40, 40), (200, 30, 30)).save(logo)
        monkeypatch.setenv("COMPANY_ADDRESS", "12 MG Road|Pune 411005")
        monkeypatch.setenv("COMPANY_PHONE", "+91 98765 43210")
        monkeypatch.setenv("COMPANY_GSTIN", "27ABCDE1234F1Z5")
        monkeypatch.setenv("SUPPORT_EMAIL", "help@acme.example")
        monkeypatch.setenv("COMPANY_LOGO_PATH", str(logo))
        with_logo = build_receipt_pdf(TestReceiptPdf.DATA)
        for text in (b"Acme Software", b"12 MG Road", b"Pune 411005", b"+91 98765 43210",
                     b"27ABCDE1234F1Z5", b"help@acme.example", b"PAYMENT RECEIPT", b"PAID"):
            assert text in with_logo, text
        assert b"/Subtype /Image" in with_logo
        monkeypatch.setenv("COMPANY_LOGO_PATH", str(tmp_path / "missing.png"))   # bad path: just no logo
        assert b"/Subtype /Image" not in build_receipt_pdf(TestReceiptPdf.DATA)

    def test_optional_company_lines_are_left_out_when_not_set(self, vendor_env, monkeypatch):
        from licensing.receipt_generator import build_receipt_pdf
        for name in ("COMPANY_ADDRESS", "COMPANY_PHONE", "COMPANY_GSTIN", "COMPANY_LOGO_PATH"):
            monkeypatch.delenv(name, raising=False)
        pdf = build_receipt_pdf(TestReceiptPdf.DATA)
        assert b"GSTIN" not in pdf and b"Phone:" not in pdf

    def test_hostile_text_cannot_break_the_pdf(self, vendor_env):
        from licensing.receipt_generator import build_receipt_pdf
        pdf = build_receipt_pdf({**self.DATA, "customer_name": "<b>Asha</b> & <unclosed " + "x" * 500})
        assert pdf.startswith(b"%PDF")

    def test_amount_and_ids_formatting(self):
        from licensing.receipt_generator import format_amount, application_id, receipt_number
        assert format_amount(123456, "inr") == "INR 1,234.56"
        assert application_id(12) == "APP-00012" and application_id(None) == "-"
        assert receipt_number(42, datetime(2026, 1, 1)) == "RCPT-2026-00042"


class TestEmailMessage:
    def _service(self, vendor_env):
        from licensing.license_email_service import LicenseEmailService
        return LicenseEmailService()

    def test_pdf_is_attached_and_details_are_in_the_body(self, vendor_env):
        svc = self._service(vendor_env)
        msg = svc._build_message("Asha", "a@x.com", "TOK.EN", FINGERPRINT, "Yearly Plan", None,
                                 receipt=TestReceiptPdf.DATA)
        assert "Receipt" in msg["Subject"]
        parts = [p for p in msg.walk() if p.get_filename()]
        names = sorted(p.get_filename() for p in parts)
        assert names == ["Receipt-RCPT-2026-00042.pdf", "license.lic"]
        pdf = next(p for p in parts if p.get_filename().endswith(".pdf")).get_payload(decode=True)
        assert pdf.startswith(b"%PDF")
        # check the decoded text parts (plain + html)
        texts = "".join(p.get_payload(decode=True).decode() for p in msg.walk()
                        if p.get_content_type() in ("text/plain", "text/html"))
        for text in ("pay_TEST0001", "order_TESTAAAA01", "APP-00007", "INR 9,999.00", "RCPT-2026-00042"):
            assert text in texts, text

    def test_without_a_receipt_the_email_is_as_before(self, vendor_env):
        svc = self._service(vendor_env)
        msg = svc._build_message("Asha", "a@x.com", "TOK.EN", FINGERPRINT, "Yearly Plan", None)
        assert msg["Subject"].startswith("Your License Key")
        assert [p.get_filename() for p in msg.walk() if p.get_filename()] == ["license.lic"]

    def test_a_broken_receipt_never_blocks_the_license(self, vendor_env, monkeypatch):
        from licensing import license_email_service as mod
        monkeypatch.setattr(mod, "build_receipt_pdf", lambda r: (_ for _ in ()).throw(RuntimeError("boom")))
        msg = self._service(vendor_env)._build_message("Asha", "a@x.com", "TOK.EN", FINGERPRINT,
                                                       "Yearly Plan", None, receipt=TestReceiptPdf.DATA)
        assert [p.get_filename() for p in msg.walk() if p.get_filename()] == ["license.lic"]


class TestAdminDownload:
    def test_download_needs_login_and_a_paid_order(self, client):
        _make_order()
        url = "/vendor/orders/order_TESTAAAA01/receipt.pdf"
        assert client.get(url).status_code == 401
        assert client.get(url, headers=_auth()).status_code == 404          # created, not paid yet
        assert client.get("/vendor/orders/bad id/receipt.pdf", headers=_auth()).status_code == 404

    def test_download_returns_the_pdf(self, client):
        _make_order()
        _pay(client)
        resp = client.get("/vendor/orders/order_TESTAAAA01/receipt.pdf", headers=_auth())
        assert resp.status_code == 200 and resp.mimetype == "application/pdf"
        assert resp.data.startswith(b"%PDF") and b"pay_TEST0001" in resp.data


class TestOldDatabaseUpgrade:
    def test_orders_table_gets_the_new_columns(self, tmp_path):
        from sqlalchemy import create_engine, inspect, text
        from licensing.customer_models import _upgrade_schema
        engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
        with engine.begin() as c:
            c.execute(text("CREATE TABLE orders (id INTEGER PRIMARY KEY, gateway_order_id VARCHAR(64))"))
        _upgrade_schema(engine)
        cols = {c["name"] for c in inspect(engine).get_columns("orders")}
        assert {"paid_at", "receipt_no"} <= cols