#!/usr/bin/env python3
"""
AI Attendance & Payroll System - VENDOR-ONLY License Issuing Tool (Ed25519)

This tool creates (once) and holds the Ed25519 PRIVATE signing key, and is
the only place a license token can be produced. It must NEVER be shipped
to a customer, bundled into the packaged .exe, or committed to a public
repository - only licensing/license_manager.py's PUBLIC key travels with
the product; this script and the private key file it manages stay on the
vendor's own machine / server.

One-time setup:
    python keygen.py init-keys
This generates vendor_private_key.pem and prints the matching public-key
hex. Embed it in the customer app with:
    python -m licensing.license_manager encode-key <public-key-hex>
and paste the two printed constants into licensing/license_manager.py
BEFORE building any customer release.

Issuing a license by hand (the app shows the fingerprint on its lock screen):
    python keygen.py issue <machine_fingerprint> "<customer name>" --plan yearly
    python keygen.py issue <machine_fingerprint> "<customer name>" --days 365
    python keygen.py issue <machine_fingerprint> "<customer name>"          (perpetual)

The payment webhook calls issue_license_for_plan() below automatically; the
CLI is for manual / support issuing only.

Where the private key is read from (first match wins):
    1. the explicit private_key_path argument / --key option
    2. VENDOR_PRIVATE_KEY_PEM  - the PEM text itself (handy for hosts that
       only offer environment variables / secrets, not files)
    3. VENDOR_PRIVATE_KEY_PATH - path to the PEM file
    4. <VENDOR_DATA_DIR or licensing/>/vendor_private_key.pem

Security notes:
  - vendor_private_key.pem is written with restrictive permissions (0600
    on POSIX) and this tool refuses to silently overwrite an existing one
    (regenerating it invalidates every license already issued against the
    old public key). Back it up somewhere safe and offline.
  - Keep this file, vendor_private_key.pem, and license_keys_log.txt out
    of any repository that could be exposed to a customer - see
    .gitignore.
"""
import os
import sys
import json
import re
import base64
import argparse
from datetime import datetime, timedelta, timezone
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

try:  # imported as part of the licensing package
    from .vendor_paths import get_vendor_data_dir
    from .plans import get_plan, compute_expiry
except ImportError:  # run directly: python keygen.py ...
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from vendor_paths import get_vendor_data_dir  # type: ignore
    from plans import get_plan, compute_expiry  # type: ignore

_FINGERPRINT_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _default_private_key_path() -> str:
    explicit = (os.environ.get("VENDOR_PRIVATE_KEY_PATH") or "").strip()
    if explicit:
        return explicit
    return os.path.join(get_vendor_data_dir(), "vendor_private_key.pem")


def _license_log_path() -> str:
    return os.path.join(get_vendor_data_dir(), "license_keys_log.txt")


# Kept as module constants for backwards compatibility with older callers.
DEFAULT_PRIVATE_KEY_PATH = _default_private_key_path()
LICENSE_LOG_PATH = _license_log_path()


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def is_valid_fingerprint(value: Optional[str]) -> bool:
    """True for a 64-character hex machine fingerprint."""
    return bool(value) and bool(_FINGERPRINT_RE.match(value.strip()))


def init_keys(private_key_path: Optional[str] = None):
    """Generate a new Ed25519 vendor keypair. Refuses to overwrite an
    existing private key - see module docstring for why."""
    private_key_path = private_key_path or _default_private_key_path()
    if os.path.exists(private_key_path):
        print(f"A private key already exists at {private_key_path}.")
        print("Refusing to overwrite it - delete it manually first if you really want a new")
        print("keypair (doing so invalidates every license you've already issued).")
        sys.exit(1)

    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with open(private_key_path, "wb") as f:
        f.write(pem)
    try:
        os.chmod(private_key_path, 0o600)
    except OSError:
        pass  # best-effort; not all filesystems support POSIX permission bits

    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    print("=" * 70)
    print("Ed25519 keypair generated.")
    print(f"Private key saved to: {private_key_path}")
    print("  -> BACK THIS UP OFFLINE. Never commit it or ship it to a customer.")
    print()
    print("Public key (64 hex chars):")
    print()
    print(f"    {public_bytes.hex()}")
    print()
    print("Embed it in the customer app with:")
    print(f"    python -m licensing.license_manager encode-key {public_bytes.hex()}")
    print("and paste the two printed constants into licensing/license_manager.py.")
    print("=" * 70)


def _load_private_key(private_key_path: Optional[str] = None) -> Ed25519PrivateKey:
    if not private_key_path:
        pem_text = (os.environ.get("VENDOR_PRIVATE_KEY_PEM") or "").strip()
        if pem_text:
            # Hosts often store multi-line secrets with literal "\n" sequences.
            pem_text = pem_text.replace("\\n", "\n")
            return serialization.load_pem_private_key(pem_text.encode("utf-8"), password=None)
        private_key_path = _default_private_key_path()

    if not os.path.isfile(private_key_path):
        raise FileNotFoundError(
            f"No private key found at {private_key_path}. Run 'python keygen.py init-keys' first."
        )
    with open(private_key_path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def issue_license_token(machine_fingerprint: str, customer_name: str,
                         expires_at: Optional[datetime] = None, edition: Optional[str] = None,
                         private_key_path: Optional[str] = None) -> str:
    """
    Build and sign a license token for `machine_fingerprint`.

    Args:
        machine_fingerprint: 64-char hex fingerprint from the customer's machine.
        customer_name: Name embedded in the license (display/audit only).
        expires_at: timezone-aware UTC datetime the license stops validating,
            or None for a perpetual license.
        edition: optional free-form edition/feature tag (the plan code is
            stored here by issue_license_for_plan).
        private_key_path: path to the vendor's Ed25519 private key PEM
            (see the module docstring for the default lookup order).

    Returns:
        str: the signed license token, "<payload>.<signature>".
    """
    if not is_valid_fingerprint(machine_fingerprint):
        raise ValueError("machine_fingerprint must be a 64-character hex string")
    machine_fingerprint = machine_fingerprint.strip().lower()

    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    private_key = _load_private_key(private_key_path)

    payload = {
        "customer_name": customer_name,
        "machine_fingerprint": machine_fingerprint,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at.astimezone(timezone.utc).isoformat() if expires_at else None,
        "edition": edition,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = private_key.sign(payload_bytes)

    token = f"{_b64u(payload_bytes)}.{_b64u(signature)}"

    try:
        with open(_license_log_path(), "a", encoding="utf-8") as f:
            f.write(
                f"{datetime.now(timezone.utc).isoformat()}|{machine_fingerprint}|"
                f"{customer_name}|{payload['expires_at']}|{edition}\n"
            )
    except OSError:
        pass  # record-keeping only - never block issuing a license over a log write failure

    return token


def issue_license_for_plan(machine_fingerprint: str, customer_name: str, plan_code: str,
                            current_expiry: Optional[datetime] = None,
                            now: Optional[datetime] = None,
                            private_key_path: Optional[str] = None):
    """
    Issue a token for a purchased plan ("monthly" / "yearly" / "lifetime").

    This is what the payment webhook calls. The expiry is calculated here
    from the plan, so the caller never has to do date maths.

    Returns:
        (token, expires_at) - expires_at is a UTC datetime or None (lifetime).
    """
    plan = get_plan(plan_code)
    if plan is None:
        raise ValueError(f"Unknown plan '{plan_code}'")
    expires_at = compute_expiry(plan, now=now, current_expiry=current_expiry)
    token = issue_license_token(
        machine_fingerprint, customer_name,
        expires_at=expires_at, edition=plan.code,
        private_key_path=private_key_path,
    )
    return token, expires_at


def main():
    parser = argparse.ArgumentParser(description="AI Attendance & Payroll System - vendor license tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-keys", help="Generate a new Ed25519 vendor keypair (run once)")

    issue_parser = subparsers.add_parser("issue", help="Issue a signed license token for a customer machine")
    issue_parser.add_argument("machine_fingerprint", help="64-character hex fingerprint from the customer's machine")
    issue_parser.add_argument("customer_name", help="Customer name to embed in the license")
    group = issue_parser.add_mutually_exclusive_group()
    group.add_argument("--plan", choices=["monthly", "yearly", "lifetime"],
                       help="Issue for a standard plan (sets the expiry automatically)")
    group.add_argument("--days", type=int, default=None,
                       help="License validity in days from now (omit for a perpetual license)")
    issue_parser.add_argument("--edition", default=None, help="Optional edition/feature tag")
    issue_parser.add_argument("--key", default=None, help="Path to the vendor private key PEM")

    args = parser.parse_args()

    if args.command == "init-keys":
        init_keys()
        return

    if args.command == "issue":
        try:
            if args.plan:
                token, expires_at = issue_license_for_plan(
                    args.machine_fingerprint, args.customer_name, args.plan,
                    private_key_path=args.key,
                )
            else:
                expires_at = None
                if args.days is not None:
                    expires_at = datetime.now(timezone.utc) + timedelta(days=args.days)
                token = issue_license_token(
                    args.machine_fingerprint, args.customer_name,
                    expires_at=expires_at, edition=args.edition,
                    private_key_path=args.key,
                )
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)

        print("=" * 70)
        print(f"LICENSE TOKEN for {args.customer_name}:")
        print(f"Expires: {expires_at.isoformat() if expires_at else 'never (perpetual)'}")
        print()
        print(token)
        print()
        print("Send this to the customer. They paste it into the lock screen's")
        print("'License key' box, or save it as 'license.lic' next to the application.")
        print("=" * 70)


if __name__ == "__main__":
    main()
