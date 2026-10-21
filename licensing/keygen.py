#!/usr/bin/env python3
"""
AI Attendance & Payroll System - VENDOR-ONLY License Issuing Tool (Ed25519)

This tool creates (once) and holds the Ed25519 PRIVATE signing key, and is
the only place a license token can be produced. It must NEVER be shipped
to a customer, bundled into the packaged .exe, or committed to a public
repository - only licensing/license_manager.py's PUBLIC key travels with
the product; this script and the private key file it manages stay on the
vendor's own machine / server.

One-time setup (or whenever you rotate the key):
    python keygen.py init-keys              first key  (refuses if one exists)
    python keygen.py init-keys --rotate     NEW key: the old PEM is renamed to
                                            vendor_private_key.pem.revoked-<time>
                                            (never deleted) and a fresh one is made
Both commands also PATCH licensing/license_manager.py for you (the two
_PUBKEY_* constants), so there is nothing to copy by hand. Pass --no-embed to
skip that and print the encode-key command instead. Afterwards:
    python keygen.py check-key              confirms the private key and the key
                                            embedded in the app really match
Then BUILD A NEW RELEASE: an installed app only trusts the public key it was
built with. Licenses signed by the OLD key stop verifying, so re-issue them
(Vendor admin API: POST /vendor/customers/<id>/reissue, or `issue` below).

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
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

try:  # imported as part of the licensing package
    from .vendor_paths import get_vendor_data_dir
    from .plans import get_plan, compute_expiry, TRIAL_DAYS
except ImportError:  # run directly: python keygen.py ...
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from vendor_paths import get_vendor_data_dir  # type: ignore
    from plans import get_plan, compute_expiry, TRIAL_DAYS  # type: ignore

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


_APP_LICENSE_MANAGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "license_manager.py")
_PARTS_RE = re.compile(r"^_PUBKEY_ENC_PARTS\s*=\s*\(.*?\)\s*$", re.MULTILINE)
_PIN_RE = re.compile(r"^_PUBKEY_SHA256_PIN\s*=\s*\".*?\"\s*$", re.MULTILINE)
_SEED_RE = re.compile(r"^_PUBKEY_MASK_SEED\s*=\s*b\"(.*?)\"\s*$", re.MULTILINE)


def _public_hex_of(private_key: Ed25519PrivateKey) -> str:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()


def _mask_for(source: str) -> bytes:
    match = _SEED_RE.search(source)
    if not match:
        raise RuntimeError("Could not find _PUBKEY_MASK_SEED in license_manager.py")
    return hashlib.sha256(match.group(1).encode("utf-8")).digest()


def embed_public_key_in_app(public_key_hex: str, app_file: Optional[str] = None) -> bool:
    """
    Write the encoded public key into licensing/license_manager.py (the same
    thing `python -m licensing.license_manager encode-key` prints for you to
    paste). Returns True when the file was updated.
    """
    app_file = app_file or _APP_LICENSE_MANAGER
    if not os.path.isfile(app_file):
        print(f"  (could not find {app_file} - embed the key by hand, see below)")
        return False
    with open(app_file, "r", encoding="utf-8") as f:
        source = f.read()

    raw = bytes.fromhex(public_key_hex.strip())
    if len(raw) != 32:
        raise ValueError("Public key must be exactly 32 bytes (64 hex characters)")
    mask = _mask_for(source)
    b64 = base64.b64encode(bytes(a ^ b for a, b in zip(raw, mask))).decode("ascii")
    half = len(b64) // 2
    parts = (b64[half:], b64[:half])  # stored reversed, same as encode_public_key_for_embedding()
    pin = hashlib.sha256(raw).hexdigest()

    if not (_PARTS_RE.search(source) and _PIN_RE.search(source)):
        print("  (the _PUBKEY_* constants were not found in license_manager.py - embed by hand)")
        return False
    source = _PARTS_RE.sub(lambda _m: f"_PUBKEY_ENC_PARTS = {parts!r}", source, count=1)
    source = _PIN_RE.sub(lambda _m: f'_PUBKEY_SHA256_PIN = "{pin}"', source, count=1)

    tmp = app_file + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(source)
    os.replace(tmp, app_file)
    return True


def _embedded_public_hex(app_file: Optional[str] = None) -> str:
    """Decode the public key currently embedded in license_manager.py (no import needed)."""
    app_file = app_file or _APP_LICENSE_MANAGER
    with open(app_file, "r", encoding="utf-8") as f:
        source = f.read()
    parts_match = re.search(r"^_PUBKEY_ENC_PARTS\s*=\s*\(\s*[\"'](.*?)[\"']\s*,\s*[\"'](.*?)[\"']\s*\)",
                            source, re.MULTILINE)
    pin_match = re.search(r"^_PUBKEY_SHA256_PIN\s*=\s*\"(.*?)\"", source, re.MULTILINE)
    if not parts_match or not pin_match:
        raise RuntimeError("Could not read the embedded key constants from license_manager.py")
    masked = base64.b64decode(parts_match.group(2) + parts_match.group(1))
    mask = _mask_for(source)
    raw = bytes(a ^ b for a, b in zip(masked, mask))
    if hashlib.sha256(raw).hexdigest() != pin_match.group(1):
        raise RuntimeError("The embedded key does not match its own SHA-256 pin (file edited by hand?)")
    return raw.hex()


def check_key(private_key_path: Optional[str] = None) -> bool:
    """True if the private key on this machine matches the public key built into the app."""
    mine = _public_hex_of(_load_private_key(private_key_path))
    embedded = _embedded_public_hex()
    if mine == embedded:
        print("OK - the private key and the key embedded in license_manager.py MATCH.")
        return True
    print("MISMATCH - licenses signed with this private key will NOT verify in the app.")
    print(f"  private key's public key : {mine}")
    print(f"  embedded in the app      : {embedded}")
    print("Run 'python keygen.py init-keys --rotate' (new pair) or restore the right PEM.")
    return False


def init_keys(private_key_path: Optional[str] = None, rotate: bool = False, embed: bool = True):
    """Generate a new Ed25519 vendor keypair.

    Without rotate=True this refuses to overwrite an existing private key (see
    module docstring). With rotate=True the old key is kept under a
    '.revoked-<timestamp>' name - never deleted - and a fresh pair is made."""
    private_key_path = private_key_path or _default_private_key_path()
    if os.path.exists(private_key_path):
        if not rotate:
            print(f"A private key already exists at {private_key_path}.")
            print("Refusing to overwrite it. To replace it with a NEW key run:")
            print("    python keygen.py init-keys --rotate")
            print("(doing so invalidates every license already issued with the old key).")
            sys.exit(1)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        retired = f"{private_key_path}.revoked-{stamp}"
        os.replace(private_key_path, retired)
        print(f"Old private key kept as: {retired}")
        print("  -> it can still SIGN licenses. Move it offline or destroy it once you are sure.")

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

    public_hex = _public_hex_of(private_key)

    print("=" * 70)
    print("Ed25519 keypair generated.")
    print(f"Private key saved to: {private_key_path}")
    print("  -> BACK THIS UP OFFLINE. Never commit it or ship it to a customer.")
    print()
    print("Public key (64 hex chars):")
    print()
    print(f"    {public_hex}")
    print()
    embedded = embed_public_key_in_app(public_hex) if embed else False
    if embedded:
        print("licensing/license_manager.py UPDATED with the new public key.")
        print("Now: build a new release, and re-issue licenses signed by the old key.")
    else:
        print("Embed it in the customer app with:")
        print(f"    python -m licensing.license_manager encode-key {public_hex}")
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
                         private_key_path: Optional[str] = None,
                         extra: Optional[dict] = None, record: bool = True) -> str:
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
        extra: additional fields to sign into the payload (used by the trial
            token to carry "trial_started_at"). Cannot override the core fields.
        record: append a line to license_keys_log.txt (trial tokens turn this off).

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
    for key, value in (extra or {}).items():
        payload.setdefault(key, value)
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = private_key.sign(payload_bytes)

    token = f"{_b64u(payload_bytes)}.{_b64u(signature)}"

    if record:
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


def issue_trial_token(machine_fingerprint: str, customer_name: str, started_at: datetime,
                      private_key_path: Optional[str] = None) -> str:
    """
    Signed proof of the vendor's trial record for one machine.

    It is NOT a license: edition is "trial" and the app refuses to treat it
    as one (see LicenseManager.verify_license_token). It tells the app the
    one date it must believe - when the trial began - so reinstalling cannot
    give a fresh 30 days. expires_at is the end of the trial.
    """
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    started_at = started_at.astimezone(timezone.utc)
    return issue_license_token(
        machine_fingerprint, customer_name,
        expires_at=started_at + timedelta(days=TRIAL_DAYS), edition="trial",
        private_key_path=private_key_path,
        extra={"trial_started_at": started_at.isoformat()}, record=False,
    )


def main():
    parser = argparse.ArgumentParser(description="AI Attendance & Payroll System - vendor license tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-keys", help="Generate a new Ed25519 vendor keypair")
    init_parser.add_argument("--rotate", action="store_true",
                             help="Replace an existing key (the old PEM is kept as .revoked-<time>)")
    init_parser.add_argument("--no-embed", action="store_true",
                             help="Do not patch licensing/license_manager.py; just print the constants command")

    check_parser = subparsers.add_parser("check-key", help="Check the private key matches the key embedded in the app")
    check_parser.add_argument("--key", default=None, help="Path to the vendor private key PEM")

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
        init_keys(rotate=args.rotate, embed=not args.no_embed)
        return

    if args.command == "check-key":
        try:
            sys.exit(0 if check_key(args.key) else 1)
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)

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