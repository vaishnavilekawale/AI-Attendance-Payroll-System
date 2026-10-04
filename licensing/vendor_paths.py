"""
Where the VENDOR-side files live (customers.db, the private key, logs).

Vendor code runs on the vendor's own server, never inside a customer's
packaged application, so this module deliberately does NOT import
config.py (which would pull in the whole attendance app: logging set-up,
.env SECRET_KEY generation, instance/ folders...).

By default everything lives in the licensing/ folder itself. On a hosted
server, set VENDOR_DATA_DIR to a persistent disk, e.g.:

    VENDOR_DATA_DIR=/var/data/aiaps-vendor
"""
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))


def get_vendor_data_dir() -> str:
    """Directory for customers.db, the private key and the issue log."""
    configured = (os.environ.get("VENDOR_DATA_DIR") or "").strip()
    path = os.path.abspath(configured) if configured else _THIS_DIR
    os.makedirs(path, exist_ok=True)
    return path
