"""
Payment gateway adapter (VENDOR side) - Razorpay.

Only the small slice of Razorpay this system needs is implemented, over its
plain REST API with `requests` (no extra SDK dependency):

  * create_order()                  POST /v1/orders
  * verify_checkout_signature()     the signature the browser receives after paying
  * verify_webhook_signature()      the X-Razorpay-Signature header on webhooks

Configuration (environment variables on the vendor server):
    RAZORPAY_KEY_ID          rzp_test_xxx / rzp_live_xxx   (public, sent to the browser)
    RAZORPAY_KEY_SECRET      API secret (server only)
    RAZORPAY_WEBHOOK_SECRET  the secret you typed when creating the webhook
                             in Razorpay Dashboard -> Settings -> Webhooks

The webhook secret is NOT the API secret; they are two different values.

Local testing without a Razorpay account: set VENDOR_DEV_MODE=true and leave
the keys empty. get_gateway() then returns DevGateway, which fabricates
order ids and lets /dev/simulate-payment/<order_id> play the part of the
webhook. DevGateway is refused unless VENDOR_DEV_MODE is explicitly true.

Switching to Stripe later only needs another class with the same four
methods; nothing else in the vendor code talks to the gateway directly.
"""
import hashlib
import hmac
import logging
import os
import secrets
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"
_HTTP_TIMEOUT_SECONDS = 20


class GatewayError(Exception):
    """The payment gateway rejected a request or could not be reached."""


def _hmac_sha256_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


class RazorpayGateway:
    name = "razorpay"

    def __init__(self, key_id: str, key_secret: str, webhook_secret: str):
        self.key_id = (key_id or "").strip()
        self.key_secret = (key_secret or "").strip()
        self.webhook_secret = (webhook_secret or "").strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.key_id and self.key_secret)

    @property
    def public_key(self) -> str:
        """Key id that is safe to expose to the browser's Checkout widget."""
        return self.key_id

    def create_order(self, amount: int, currency: str, receipt: str,
                     notes: Optional[Dict[str, str]] = None) -> str:
        """
        Create a gateway order and return its id.

        Args:
            amount: smallest currency unit (paise)
            currency: e.g. "INR"
            receipt: our own reference (<= 40 chars)
            notes: free-form key/values stored on the order (Razorpay allows 15)
        """
        if not self.is_configured:
            raise GatewayError("Razorpay is not configured (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET)")
        try:
            response = requests.post(
                f"{RAZORPAY_API_BASE}/orders",
                auth=(self.key_id, self.key_secret),
                json={
                    "amount": int(amount),
                    "currency": currency,
                    "receipt": receipt[:40],
                    "notes": notes or {},
                },
                timeout=_HTTP_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise GatewayError(f"Could not reach Razorpay: {e}") from e

        if response.status_code != 200:
            logger.error("Razorpay order creation failed: %s %s", response.status_code, response.text[:300])
            raise GatewayError("Razorpay rejected the order request")
        order_id = (response.json() or {}).get("id")
        if not order_id:
            raise GatewayError("Razorpay returned no order id")
        return order_id

    def verify_checkout_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
        """Verify the (order_id|payment_id) signature the browser gets after paying."""
        if not (self.key_secret and order_id and payment_id and signature):
            return False
        expected = _hmac_sha256_hex(self.key_secret, f"{order_id}|{payment_id}".encode("utf-8"))
        return hmac.compare_digest(expected, signature.strip())

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        """
        Verify a webhook delivery. `raw_body` MUST be the exact bytes received
        (request.get_data()), not re-serialised JSON - any change in spacing
        or key order would break the HMAC.
        """
        if not (self.webhook_secret and signature):
            return False
        expected = _hmac_sha256_hex(self.webhook_secret, raw_body)
        return hmac.compare_digest(expected, signature.strip())


class DevGateway(RazorpayGateway):
    """Offline stand-in for local development. See module docstring."""
    name = "dev"

    def __init__(self):
        super().__init__("rzp_test_dev", "dev_secret", "dev_webhook_secret")

    @property
    def is_configured(self) -> bool:
        return True

    def create_order(self, amount, currency, receipt, notes=None) -> str:
        return "order_dev_" + secrets.token_hex(8)


def get_gateway() -> RazorpayGateway:
    """Build the gateway from the current environment."""
    gateway = RazorpayGateway(
        os.environ.get("RAZORPAY_KEY_ID", ""),
        os.environ.get("RAZORPAY_KEY_SECRET", ""),
        os.environ.get("RAZORPAY_WEBHOOK_SECRET", ""),
    )
    if gateway.is_configured:
        return gateway
    if (os.environ.get("VENDOR_DEV_MODE") or "").strip().lower() == "true":
        logger.warning("Razorpay keys not set - using the offline DEV gateway (VENDOR_DEV_MODE=true).")
        return DevGateway()
    return gateway
