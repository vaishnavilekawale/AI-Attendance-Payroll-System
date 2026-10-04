# Automated licensing - setup (vendor side)

1. `python licensing/keygen.py init-keys` -> creates `vendor_private_key.pem` (back it up offline) and prints the public key.
   Then `python -m licensing.license_manager encode-key <public-hex>` and paste the two constants into `license_manager.py`
   BEFORE building a customer release. (Skip if you still have the PEM matching the key already embedded.)
2. Copy `licensing/.env.vendor.example` to `licensing/.env.vendor`; fill in Razorpay keys, webhook secret, SMTP, admin password.
3. Razorpay Dashboard -> Webhooks: URL `https://<domain>/webhook/razorpay`, events `payment.captured` + `order.paid`, same secret.
4. Run behind HTTPS: `waitress-serve --listen=0.0.0.0:8000 licensing.vendor_app:app`
5. Customer `.env`: `LICENSE_PURCHASE_URL=https://<domain>`.
6. Local test without Razorpay: `VENDOR_DEV_MODE=true python -m licensing.vendor_app`.
7. Tests: `python -m pytest tests/test_licensing_flow.py`
