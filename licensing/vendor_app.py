"""
Vendor Web Portal (Flask)  -  run this on YOUR server, never inside a customer install.

What it does
------------
  GET  /                          pricing page + purchase form (Monthly / Yearly / Lifetime)
  POST /api/create-order          validates the form, creates the Razorpay order
  GET  /thank-you                 post-payment page (polls the order until the license is emailed)
  GET  /api/order-status/<id>     order status for that page (never returns the token)
  POST /webhook/razorpay          Razorpay -> us: payment succeeded  => license issued + emailed
  /vendor/*                       password-protected admin API (customer_routes.py)
  GET  /healthz                   liveness probe

The license is issued ONLY by the webhook - never because the browser says the
payment worked - and the webhook is only trusted after its HMAC signature has
been verified against RAZORPAY_WEBHOOK_SECRET.

Run (development):
    set VENDOR_DEV_MODE=true            (no Razorpay account needed - see payment_gateway.py)
    python -m licensing.vendor_app

Run (production, behind HTTPS):
    waitress-serve --listen=0.0.0.0:8000 licensing.vendor_app:app
    (or gunicorn "licensing.vendor_app:app")

Razorpay dashboard set-up:
    Settings -> Webhooks -> Add: URL https://<your-domain>/webhook/razorpay
    Events: payment.captured and order.paid.  Secret: RAZORPAY_WEBHOOK_SECRET.

Configuration comes from environment variables, optionally loaded from the
file named by VENDOR_ENV_FILE (default: <VENDOR_DATA_DIR>/.env.vendor):

    RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET
    PLAN_PRICE_MONTHLY / PLAN_PRICE_YEARLY / PLAN_PRICE_LIFETIME / PLAN_CURRENCY
    VENDOR_PRIVATE_KEY_PEM or VENDOR_PRIVATE_KEY_PATH   (see keygen.py)
    MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD, MAIL_DEFAULT_SENDER ...
    VENDOR_ADMIN_USER, VENDOR_ADMIN_PASSWORD
    VENDOR_SECRET_KEY, VENDOR_TRUST_PROXY=true (if behind nginx/Cloudflare)
    VENDOR_DEV_MODE=true  (local testing only)
"""
import json
import logging
import os
import re
import secrets
import sys

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, abort
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Allow `python licensing/vendor_app.py` as well as `python -m licensing.vendor_app`.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "licensing"

from .vendor_paths import get_vendor_data_dir
from .plans import get_plans

_env_file = os.environ.get("VENDOR_ENV_FILE") or os.path.join(get_vendor_data_dir(), ".env.vendor")
load_dotenv(_env_file, override=True)  # the file wins over stale OS-level variables
_env_mtime = os.path.getmtime(_env_file) if os.path.isfile(_env_file) else None


def _reload_env_if_changed():
    """Re-read .env.vendor when it is saved, so price / text / SMTP edits apply
    without restarting the portal."""
    global _env_mtime
    try:
        mtime = os.path.getmtime(_env_file) if os.path.isfile(_env_file) else None
    except OSError:
        return
    if mtime != _env_mtime:
        _env_mtime = mtime
        if mtime is not None:
            load_dotenv(_env_file, override=True)
            logging.getLogger("vendor_app").info("Reloaded %s (file changed)", _env_file)

from .customer_models import init_customer_db  # noqa: E402
from .customer_routes import vendor_bp  # noqa: E402  (after load_dotenv: reads env at call time anyway)
from .customer_service import (  # noqa: E402
    get_customer_service, ValidationError,
)
from .payment_gateway import get_gateway, GatewayError  # noqa: E402

logger = logging.getLogger("vendor_app")

_ORDER_ID_RE = re.compile(r"^order_[A-Za-z0-9_]{6,40}$")
_HANDLED_EVENTS = {"payment.captured", "order.paid"}


def _dev_mode() -> bool:
    return (os.environ.get("VENDOR_DEV_MODE") or "").strip().lower() == "true"


# ---------------------------------------------------------------------------
# Webhook processing (kept separate from the Flask view so tests and the dev
# "simulate payment" button exercise exactly the same code)
# ---------------------------------------------------------------------------
def process_gateway_event(event: dict):
    """
    Act on a VERIFIED Razorpay event payload.

    Returns (http_status, message). 5xx makes Razorpay retry the delivery.
    """
    event_name = event.get("event", "")
    if event_name not in _HANDLED_EVENTS:
        return 200, f"ignored event '{event_name}'"

    payload = event.get("payload") or {}
    payment = ((payload.get("payment") or {}).get("entity")) or {}
    order = ((payload.get("order") or {}).get("entity")) or {}

    order_id = payment.get("order_id") or order.get("id")
    payment_id = payment.get("id")
    if not order_id or not payment_id:
        logger.error("Webhook %s without order/payment id", event_name)
        return 400, "missing order or payment id"

    # Only a CAPTURED payment is money in the bank.
    if payment.get("status") != "captured":
        return 200, f"payment status '{payment.get('status')}' - waiting for capture"

    try:
        result = get_customer_service().fulfill_paid_order(
            gateway_order_id=order_id,
            gateway_payment_id=payment_id,
            paid_amount=payment.get("amount"),
            paid_currency=payment.get("currency"),
        )
    except Exception:
        logger.exception("Fulfilment failed for order %s - asking the gateway to retry", order_id)
        return 500, "fulfilment failed, please retry"

    logger.info("Webhook %s order=%s -> %s (%s)", event_name, order_id, result.status, result.detail)
    if result.status == "rejected":
        # Amount mismatch etc. Retrying will never help; a human must look.
        return 200, "order rejected"
    return 200, result.status


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_vendor_app() -> Flask:
    app = Flask(__name__, template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                       "vendor_templates"))
    app.config["SECRET_KEY"] = os.environ.get("VENDOR_SECRET_KEY") or secrets.token_hex(32)
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024
    app.config["JSON_SORT_KEYS"] = False

    if (os.environ.get("VENDOR_TRUST_PROXY") or "").strip().lower() == "true":
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    limiter = Limiter(get_remote_address, app=app, default_limits=[],
                      storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"))

    init_customer_db()
    app.register_blueprint(vendor_bp)

    @app.before_request
    def _live_reload_settings():
        _reload_env_if_changed()

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    # ----- public pages -------------------------------------------------
    @app.route("/")
    def index():
        gateway = get_gateway()
        return render_template(
            "index.html",
            plans=list(get_plans().values()),
            gateway_ready=gateway.is_configured,
            dev_mode=_dev_mode() and gateway.name == "dev",
            company_name=os.environ.get("COMPANY_NAME", "Your Company Name"),
            support_email=os.environ.get("SUPPORT_EMAIL", "support@yourcompany.com"),
            preset_fingerprint=re.sub(r"[^0-9a-fA-F]", "", request.args.get("fp", ""))[:64],
        )

    @app.route("/thank-you")
    def thank_you():
        order_id = request.args.get("order_id", "")
        if not _ORDER_ID_RE.match(order_id):
            abort(404)
        return render_template(
            "thank_you.html", order_id=order_id,
            company_name=os.environ.get("COMPANY_NAME", "Your Company Name"),
            support_email=os.environ.get("SUPPORT_EMAIL", "support@yourcompany.com"))

    @app.route("/healthz")
    def healthz():
        return jsonify({"ok": True})

    # ----- checkout API -------------------------------------------------
    @app.route("/api/create-order", methods=["POST"])
    @limiter.limit("10 per minute; 60 per hour")
    def create_order():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"success": False, "error": "Invalid request."}), 400

        gateway = get_gateway()
        if not gateway.is_configured:
            logger.error("create-order called but no payment gateway is configured")
            return jsonify({"success": False,
                            "error": "Online payment is not available right now. Please contact support."}), 503
        try:
            order = get_customer_service().create_pending_order(data, gateway)
        except ValidationError as e:
            return jsonify({"success": False, "error": str(e)}), 400
        except GatewayError as e:
            logger.error("Gateway error creating order: %s", e)
            return jsonify({"success": False,
                            "error": "We could not start the payment. Please try again in a moment."}), 502
        except Exception:
            logger.exception("Unexpected error creating order")
            return jsonify({"success": False, "error": "Something went wrong. Please try again."}), 500

        order["dev_mode"] = gateway.name == "dev"
        return jsonify({"success": True, "order": order})

    @app.route("/api/order-status/<order_id>")
    @limiter.limit("60 per minute")
    def order_status(order_id):
        if not _ORDER_ID_RE.match(order_id):
            return jsonify({"success": False, "error": "Unknown order"}), 404
        status = get_customer_service().get_order_status(order_id)
        if status is None:
            return jsonify({"success": False, "error": "Unknown order"}), 404
        return jsonify({"success": True, **status})

    # ----- the payment webhook -------------------------------------------
    @app.route("/webhook/razorpay", methods=["POST"])
    @limiter.exempt
    def razorpay_webhook():
        raw_body = request.get_data()  # exact bytes: required for the HMAC
        signature = request.headers.get("X-Razorpay-Signature", "")

        if not get_gateway().verify_webhook_signature(raw_body, signature):
            logger.warning("Webhook rejected: bad or missing signature (from %s)", request.remote_addr)
            return jsonify({"ok": False, "error": "invalid signature"}), 400

        try:
            event = json.loads(raw_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return jsonify({"ok": False, "error": "invalid JSON"}), 400
        if not isinstance(event, dict):
            return jsonify({"ok": False, "error": "invalid payload"}), 400

        status, message = process_gateway_event(event)
        return jsonify({"ok": status < 400, "message": message}), status

    # ----- local testing only ---------------------------------------------
    if _dev_mode():
        @app.route("/dev/simulate-payment/<order_id>", methods=["POST"])
        def dev_simulate_payment(order_id):
            """Plays the part of Razorpay's webhook. Only exists when VENDOR_DEV_MODE=true."""
            if not _ORDER_ID_RE.match(order_id):
                abort(404)
            from .customer_models import session_scope, Order
            with session_scope() as session:
                order = session.query(Order).filter_by(gateway_order_id=order_id).first()
                if not order:
                    abort(404)
                amount, currency = order.amount, order.currency
            status, message = process_gateway_event({
                "event": "payment.captured",
                "payload": {"payment": {"entity": {
                    "id": "pay_dev_" + secrets.token_hex(6), "order_id": order_id,
                    "amount": amount, "currency": currency, "status": "captured"}}},
            })
            return jsonify({"ok": status < 400, "message": message}), status

        logger.warning("VENDOR_DEV_MODE is ON: /dev/simulate-payment is enabled. Never use in production.")

    @app.errorhandler(404)
    def _not_found(_error):
        return jsonify({"success": False, "error": "Not found"}), 404

    @app.errorhandler(413)
    def _too_large(_error):
        return jsonify({"success": False, "error": "Request too large"}), 413

    return app


_app = None


def get_app() -> Flask:
    """The shared application instance (created on first use)."""
    global _app
    if _app is None:
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        _app = create_vendor_app()
    return _app


def __getattr__(name):
    # `waitress-serve licensing.vendor_app:app` / `gunicorn licensing.vendor_app:app`
    # look up the attribute `app`; creating it lazily means merely IMPORTING this
    # module (tests, tooling) never touches customers.db.
    if name == "app":
        return get_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")
    application = get_app()
    if _dev_mode():
        application.run(host=host, port=port, debug=False)
    else:
        from waitress import serve
        serve(application, host=host, port=port, threads=8)