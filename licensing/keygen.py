#!/usr/bin/env python3
"""
AI Attendance & Payroll System - VENDOR-ONLY License Issuing Tool (Ed25519)

This tool creates (once) and holds the Ed25519 PRIVATE signing key, and is
the only place a license token can be produced. It must NEVER be shipped
to a customer, bundled into the packaged .exe, or committed to a public
repository - only licensing/license_manager.py's PUBLIC key travels with
the product; this script and the private key file it manages stay on the
vendor's own machine.

One-time setup:
    python keygen.py init-keys
This generates licensing/vendor_private_key.pem and prints the matching
public-key hex. Paste that hex into license_manager.py's
LICENSE_PUBLIC_KEY_HEX (or set it via the LICENSE_PUBLIC_KEY_HEX
environment variable in your build) before shipping any customer build.

Issuing a license (once you have the customer's machine fingerprint - the
app displays it when no valid license is found):
    python keygen.py issue <machine_fingerprint> "<customer name>" \\
        [--days 365] [--edition pro]

Send the printed token to the customer. They save it as 'license.lic' in
the application's folder (next to the .exe), or paste it into a license
activation screen that calls LicenseManager.activate_license_from_string().

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
import base64
import argparse
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PRIVATE_KEY_PATH = os.path.join(_THIS_DIR, "vendor_private_key.pem")
LICENSE_LOG_PATH = os.path.join(_THIS_DIR, "license_keys_log.txt")


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def init_keys(private_key_path: str = DEFAULT_PRIVATE_KEY_PATH):
    """Generate a new Ed25519 vendor keypair. Refuses to overwrite an
    existing private key - see module docstring for why."""
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
    print("Public key (paste into license_manager.py's LICENSE_PUBLIC_KEY_HEX,")
    print("or set it as the LICENSE_PUBLIC_KEY_HEX environment variable in your build):")
    print()
    print(f"    {public_bytes.hex()}")
    print("=" * 70)


def _load_private_key(private_key_path: str) -> Ed25519PrivateKey:
    if not os.path.isfile(private_key_path):
        raise FileNotFoundError(
            f"No private key found at {private_key_path}. Run 'python keygen.py init-keys' first."
        )
    with open(private_key_path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def issue_license_token(machine_fingerprint: str, customer_name: str,
                         expires_at: datetime = None, edition: str = None,
                         private_key_path: str = DEFAULT_PRIVATE_KEY_PATH) -> str:
    """
    Build and sign a license token for `machine_fingerprint`.

    Args:
        machine_fingerprint: 64-char hex fingerprint from the customer's machine.
        customer_name: Name embedded in the license (display/audit only).
        expires_at: timezone-aware UTC datetime the license stops validating,
            or None for a perpetual license.
        edition: optional free-form edition/feature tag.
        private_key_path: path to the vendor's Ed25519 private key PEM.

    Returns:
        str: the signed license token, "<payload>.<signature>".
    """
    if not machine_fingerprint or len(machine_fingerprint) != 64:
        raise ValueError("machine_fingerprint must be a 64-character hex string")

    private_key = _load_private_key(private_key_path)

    payload = {
        "customer_name": customer_name,
        "machine_fingerprint": machine_fingerprint.lower(),
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at.isoformat() if expires_at else None,
        "edition": edition,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = private_key.sign(payload_bytes)

    token = f"{_b64u(payload_bytes)}.{_b64u(signature)}"

    try:
        with open(LICENSE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(
                f"{datetime.now(timezone.utc).isoformat()}|{machine_fingerprint}|"
                f"{customer_name}|{payload['expires_at']}\n"
            )
    except OSError:
        pass  # record-keeping only - never block issuing a license over a log write failure

    return token


def main():
    parser = argparse.ArgumentParser(description="AI Attendance & Payroll System - vendor license tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-keys", help="Generate a new Ed25519 vendor keypair (run once)")

    issue_parser = subparsers.add_parser("issue", help="Issue a signed license token for a customer machine")
    issue_parser.add_argument("machine_fingerprint", help="64-character hex fingerprint from the customer's machine")
    issue_parser.add_argument("customer_name", help="Customer name to embed in the license")
    issue_parser.add_argument("--days", type=int, default=None,
                               help="License validity in days from now (omit for a perpetual license)")
    issue_parser.add_argument("--edition", default=None, help="Optional edition/feature tag")
    issue_parser.add_argument("--key", default=DEFAULT_PRIVATE_KEY_PATH, help="Path to the vendor private key PEM")

    args = parser.parse_args()

    if args.command == "init-keys":
        init_keys()
        return

    if args.command == "issue":
        expires_at = None
        if args.days is not None:
            expires_at = datetime.now(timezone.utc) + timedelta(days=args.days)

        try:
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
        print()
        print(token)
        print()
        print("Send this to the customer. They should save it as 'license.lic' in the")
        print("application's folder (next to the .exe), or paste it into the license")
        print("activation screen if the app provides one.")
        print("=" * 70)


if __name__ == "__main__":
    main()