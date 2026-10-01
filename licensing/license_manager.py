"""
Ed25519 Signed Licensing System

This replaces the previous SHA-256-derived "license key" scheme. That
scheme was insecure by construction: the exact algorithm used to VALIDATE
a key (a plain SHA-256 hash of fingerprint + status flags, see the old
generate_license_key()) shipped inside the customer-facing application
itself, so anyone who read license_manager.py - which is everyone, since
it's part of the product they bought - could compute their own valid
license key for their own machine fingerprint without paying. There was
no secret anywhere in that scheme; a hash is not a signature.

This module now verifies a LICENSE TOKEN using Ed25519 public-key
signatures instead:

  - The vendor holds an Ed25519 PRIVATE key (see licensing/keygen.py +
    licensing/vendor_private_key.pem), kept only on the vendor's own
    machine and NEVER shipped to a customer.
  - This module ships only the matching PUBLIC key (embedded below in an
    encoded form - see "Public key handling"). A public key can verify a
    signature but cannot be used to forge one.
  - A license token is a signed JSON payload binding a customer + this
    machine's hardware fingerprint + an optional expiry. Only a token
    signed by the vendor's private key will verify against the public
    key embedded here, so a customer cannot self-issue a valid license no
    matter how closely they read this source file.

Token format (a lightweight, self-contained analogue of a JWT):

    "<base64url(payload_json)>.<base64url(ed25519_signature)>"

payload_json fields:
    customer_name        str
    machine_fingerprint  str  (64-char hex, SHA-256 of hardware ids)
    issued_at            str  (ISO-8601 UTC)
    expires_at           str or null (ISO-8601 UTC; null = perpetual)
    edition              str or null (optional feature/tier tag)

The license token is read from (in order of precedence):
    1. the LICENSE_TOKEN environment variable, or
    2. a 'license.lic' file next to the application (BASE_DIR).

--------------------------------------------------------------------------
HARDENING NOTES (what changed in this revision)
--------------------------------------------------------------------------

1. Public key handling
   Previously LICENSE_PUBLIC_KEY_HEX was read straight from the
   environment. config.py calls load_dotenv() on the .env file that sits
   next to the .exe, so any customer could generate their own Ed25519
   keypair, put THEIR public key in .env, and sign their own licenses.

   Now:
     - The vendor public key is embedded in this file as an XOR-masked,
       split, base64-encoded constant plus a SHA-256 pin of the decoded
       key. It is decoded at runtime and checked against the pin, so a
       plain-text edit of one constant is detected.
     - In a FROZEN (PyInstaller) build the LICENSE_PUBLIC_KEY_HEX
       environment variable / .env entry is IGNORED completely.
     - In a development checkout (not frozen) the environment variable is
       still honoured so you can test with a throw-away keypair.
   To embed a new key (e.g. after rotating your keypair) run:
       python -m licensing.license_manager encode-key <64-char-public-key-hex>
   and paste the printed constants over _PUBKEY_ENC_PARTS and
   _PUBKEY_SHA256_PIN below.

   HONEST LIMIT: this is obfuscation plus an integrity pin, not
   tamper-proofing. Python code inside a PyInstaller bundle can always be
   patched by a determined person. This raises the bar from "edit .env"
   to "reverse-engineer and repack the executable". For more, compile
   this module with Nuitka/Cython for release builds.

2. Trial reset prevention
   The trial start date is now stored in up to THREE places, and the
   EARLIEST date found wins, so deleting one marker does not restart the
   trial:
     - HKCU\\SOFTWARE\\AIAttendanceSystem\\TrialStartDate (Windows)
     - an encrypted marker file:
         %LOCALAPPDATA%\\AttendancePayrollSystem\\secure_token.dat
     - TRIAL_START in the .env file next to the application
   Missing markers are silently re-seeded from the ones that survive.
   The encrypted marker file is a Fernet token keyed from this machine's
   Windows MachineGuid: a copy taken from another PC will not decrypt,
   and an edited file fails its integrity check - both are treated as
   tampering and end the trial.

   System-clock rollback detection: the app records a "last seen" UTC
   timestamp (in the marker file and the registry) and also reads the
   newest activity timestamp out of the SQLite database (attendance /
   payroll rows). If the current clock is more than 24 hours BEHIND the
   newest of these, the clock was set back: the trial is revoked, and
   licence-expiry checks use max(clock, last activity) rather than the
   raw clock, so winding the clock back does not un-expire a licence.

   HONEST LIMIT: a fully offline trial can always be defeated by someone
   who wipes every marker AND the database on a fresh install, or who
   reinstalls Windows. Only online activation can close that completely.

3. Stable machine fingerprint (v2)
   The fingerprint is now built from stable identifiers only:
     - CPU id       (SMBIOS processor ID; registry model string fallback)
     - MachineGuid  (HKLM\\SOFTWARE\\Microsoft\\Cryptography)
     - a hardware "anchor", first usable of: motherboard serial ->
       SMBIOS system UUID -> system serial -> system-disk serial
   The MAC address and the working-directory volume serial (both
   volatile: VPNs, USB adapters, cwd changes) are NO LONGER used, and the
   baseboard *model name* (identical across a batch of OEM PCs) is gone.
   Placeholder values such as "To be filled by O.E.M." are discarded.
   Identical PCs cloned from one disk image share CPU id and MachineGuid
   but differ in board/UUID/disk serial, which keeps fingerprints unique.

   BACKWARD COMPATIBILITY: licences already issued against the OLD
   fingerprint keep working - verification accepts a token bound to
   either the new (v2) or the legacy fingerprint (ACCEPT_LEGACY_FINGERPRINT).
   Issue NEW licences against the v2 fingerprint shown in the app.
"""
import os
import re
import sys
import json
import base64
import hashlib
import logging
import platform
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, Dict, List

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

try:
    import winreg
    WINDOWS_AVAILABLE = True
except ImportError:
    WINDOWS_AVAILABLE = False

from config import BASE_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Vendor PUBLIC key - embedded, encoded form.
#
# This is the only key that ships inside the customer-facing application.
# It can verify a license signature but can never be used to create one.
#
# It is stored as: base64( raw_key XOR mask ), split in two halves that are
# stored in reverse order, plus a SHA-256 pin of the raw key. See the
# "Public key handling" note in the module docstring, and regenerate the
# two constants below with:
#     python -m licensing.license_manager encode-key <64-char-hex-public-key>
#
# IMPORTANT: the key currently embedded here is the one that matched the
# vendor_private_key.pem that was present in the project archive. If that
# PEM has ever left your control, generate a new keypair
# (python licensing/keygen.py init-keys), run encode-key on the new public
# key, and paste the result here BEFORE building any customer release.
# ---------------------------------------------------------------------------
_PUBKEY_MASK_SEED = b"AIAPS::license-pubkey-mask::v1"
_PUBKEY_ENC_PARTS = ("Tt0qsSgZPt+G4LaaKMoQc=", "5wjCGO1bpre2/X3AflzHao")
_PUBKEY_SHA256_PIN = "7e8ab717c9cbee5525b5dd88335dbc40b4ed1bb70baf589bd79a001b7cf99a96"


def _is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def _pubkey_mask() -> bytes:
    return hashlib.sha256(_PUBKEY_MASK_SEED).digest()


def encode_public_key_for_embedding(public_key_hex: str) -> Dict[str, object]:
    """
    Produce the constants to paste into _PUBKEY_ENC_PARTS / _PUBKEY_SHA256_PIN
    for a given 64-char hex Ed25519 public key.
    """
    raw = bytes.fromhex(public_key_hex.strip())
    if len(raw) != 32:
        raise ValueError("Public key must be exactly 32 bytes (64 hex characters)")
    mask = _pubkey_mask()
    masked = bytes(a ^ b for a, b in zip(raw, mask))
    b64 = base64.b64encode(masked).decode("ascii")
    half = len(b64) // 2
    parts = (b64[half:], b64[:half])  # stored reversed
    return {
        "parts": parts,
        "pin": hashlib.sha256(raw).hexdigest(),
    }


def _decode_embedded_public_key_hex() -> str:
    """Decode the embedded public key and verify it against its pin."""
    b64 = "".join(reversed(_PUBKEY_ENC_PARTS))
    masked = base64.b64decode(b64)
    mask = _pubkey_mask()
    raw = bytes(a ^ b for a, b in zip(masked, mask))
    if hashlib.sha256(raw).hexdigest() != _PUBKEY_SHA256_PIN:
        raise LicenseError("Embedded vendor public key failed its integrity check")
    return raw.hex()


def _resolve_public_key_hex() -> Tuple[str, bool]:
    """
    Decide which public key to use.

    Returns (public_key_hex, is_dev_override).
      - Frozen build: ALWAYS the embedded key; environment/.env ignored.
      - Development checkout: LICENSE_PUBLIC_KEY_HEX env var if set, else
        the embedded key.
    """
    env_value = (os.environ.get("LICENSE_PUBLIC_KEY_HEX") or "").strip().lower()
    try:
        embedded = _decode_embedded_public_key_hex()
    except Exception as exc:  # pragma: no cover - only on a corrupted build
        logger.error("Could not decode embedded vendor public key: %s", exc)
        embedded = ""

    if env_value:
        if _is_frozen():
            if env_value != embedded:
                logger.warning(
                    "LICENSE_PUBLIC_KEY_HEX is set but is IGNORED in packaged builds."
                )
            return embedded, False
        if env_value != embedded:
            logger.warning(
                "Using LICENSE_PUBLIC_KEY_HEX from the environment (development override)."
            )
            return env_value, True
    return embedded, False


class LicenseError(Exception):
    """Raised when the configured vendor public key itself is unusable."""


LICENSE_PUBLIC_KEY_HEX, _PUBLIC_KEY_IS_DEV_OVERRIDE = _resolve_public_key_hex()

LICENSE_FILE_NAME = "license.lic"
LICENSE_REGISTRY_PATH = r"SOFTWARE\AIAttendanceSystem"
LICENSE_REGISTRY_TRIAL_START = "TrialStartDate"
LICENSE_REGISTRY_LAST_SEEN = "LastSeenUtc"
LICENSE_REGISTRY_TRIAL_REVOKED = "TrialRevoked"

SECURE_TOKEN_DIR_NAME = "AttendancePayrollSystem"
SECURE_TOKEN_FILE_NAME = "secure_token.dat"

ENV_TRIAL_START_KEY = "TRIAL_START"
ENV_TRIAL_REVOKED_KEY = "TRIAL_REVOKED"

# Trial period is a convenience for evaluation. The Ed25519 check is what
# actually gates a paid deployment; the trial protections below simply make
# casual resets (delete one registry key / one .env line / roll the clock
# back) stop working.
TRIAL_DAYS = 30

# How far BEHIND the newest recorded activity the system clock may be
# before it is treated as a clock rollback. Generous on purpose: covers
# timezone mistakes and a user correcting a fast clock.
CLOCK_ROLLBACK_TOLERANCE = timedelta(hours=24)

# Database timestamps in this app are NAIVE India Standard Time
# (models.now_ist()), i.e. UTC+05:30.
_IST_OFFSET = timedelta(hours=5, minutes=30)

# (table, column) pairs that hold "the app was really used at time X".
_DB_ACTIVITY_COLUMNS = (
    ("attendance", "created_at"),
    ("attendance", "updated_at"),
    ("payroll", "created_at"),
    ("payroll", "updated_at"),
    ("attendance_activities", "created_at"),
)

# Keep accepting licences that were issued against the pre-v2 fingerprint
# so existing customers are not locked out by this upgrade. Set to False
# once every customer has been re-issued a v2 licence.
ACCEPT_LEGACY_FINGERPRINT = True

FINGERPRINT_VERSION_TAG = "AIAPS-FP-v2"


def _b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64u_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


# ---------------------------------------------------------------------------
# Hardware identifier helpers (fingerprint v2)
# ---------------------------------------------------------------------------
_JUNK_SERIALS = {
    "TOBEFILLEDBYOEM", "DEFAULTSTRING", "NOTAPPLICABLE", "NOTSPECIFIED",
    "SYSTEMSERIALNUMBER", "BASEBOARDSERIALNUMBER", "SERIALNUMBER", "NONE",
    "UNKNOWN", "DEFAULT", "NULL", "123456789", "1234567890", "0123456789",
    "OEM", "TYPE2BOARDSERIALNUMBER", "CHASSISSERIALNUMBER", "NOSERIAL",
    "SYSTEMPRODUCTNAME", "TOBEFILLEDBYOEMTOBEFILLEDBYOEM",
}


def _clean_serial(value) -> str:
    """Normalise a hardware serial and return '' if it is a placeholder."""
    if value is None:
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()
    if len(cleaned) < 4:
        return ""
    if len(set(cleaned)) == 1:  # 0000000, FFFFFFF, ...
        return ""
    if cleaned in _JUNK_SERIALS:
        return ""
    return cleaned


def _parse_smbios_table(raw: bytes) -> Dict[str, str]:
    """
    Parse a Windows RawSMBIOSData blob (as returned by
    GetSystemFirmwareTable('RSMB')) and return the identifiers we use:
    cpu_id, board_serial, system_uuid, system_serial. Missing / placeholder
    values are returned as ''. Pure function - unit-testable anywhere.
    """
    out = {"cpu_id": "", "board_serial": "", "system_uuid": "", "system_serial": ""}
    if not raw or len(raw) < 8:
        return out

    length = int.from_bytes(raw[4:8], "little")
    table = raw[8:8 + length] if length else raw[8:]
    n = len(table)
    i = 0
    seen = set()

    while i + 4 <= n:
        stype = table[i]
        slen = table[i + 1]
        if slen < 4 or i + slen > n:
            break

        # String-set that follows the formatted area (ends with 00 00).
        j = i + slen
        strings: List[str] = []
        if j + 1 < n + 1 and table[j:j + 2] == b"\x00\x00":
            end = j + 2
        else:
            k = j
            while k + 1 < n and not (table[k] == 0 and table[k + 1] == 0):
                k += 1
            strings = [s.decode("ascii", "ignore") for s in table[j:k].split(b"\x00")]
            end = k + 2

        def _s(idx: int) -> str:
            return strings[idx - 1] if 1 <= idx <= len(strings) else ""

        area = table[i:i + slen]

        if stype not in seen:
            if stype == 1 and slen >= 0x19:  # System Information
                out["system_serial"] = _clean_serial(_s(area[7]))
                uuid_bytes = bytes(area[8:24])
                if uuid_bytes not in (b"\x00" * 16, b"\xff" * 16):
                    out["system_uuid"] = uuid_bytes.hex().upper()
                seen.add(1)
            elif stype == 2 and slen >= 8:  # Baseboard
                out["board_serial"] = _clean_serial(_s(area[7]))
                seen.add(2)
            elif stype == 4 and slen >= 16:  # Processor
                proc_id = bytes(area[8:16])
                if proc_id != b"\x00" * 8:
                    out["cpu_id"] = proc_id.hex().upper()
                seen.add(4)
            elif stype == 127:  # End of table
                break

        i = end

    return out


def _read_smbios_windows() -> Dict[str, str]:
    """Read SMBIOS identifiers via GetSystemFirmwareTable (no subprocess)."""
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.GetSystemFirmwareTable.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
        ]
        k32.GetSystemFirmwareTable.restype = ctypes.c_uint32
        signature = int.from_bytes(b"RSMB", "big")
        size = k32.GetSystemFirmwareTable(signature, 0, None, 0)
        if not size:
            return {}
        buf = ctypes.create_string_buffer(size)
        got = k32.GetSystemFirmwareTable(signature, 0, buf, size)
        if not got:
            return {}
        return _parse_smbios_table(buf.raw[:got])
    except Exception as exc:
        logger.warning("Could not read SMBIOS data: %s", exc)
        return {}


def _windows_machine_guid() -> str:
    """HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid."""
    if not WINDOWS_AVAILABLE:
        return ""
    try:
        flags = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography", 0, flags) as key:
            value = winreg.QueryValueEx(key, "MachineGuid")[0]
            return str(value).strip().lower()
    except Exception as exc:
        logger.warning("Could not read MachineGuid: %s", exc)
        return ""


def _windows_cpu_registry_id() -> str:
    """Fallback CPU descriptor from the registry (model-level, no serial)."""
    if not WINDOWS_AVAILABLE:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            ident = str(winreg.QueryValueEx(key, "Identifier")[0])
            try:
                name = str(winreg.QueryValueEx(key, "ProcessorNameString")[0])
            except FileNotFoundError:
                name = ""
            return re.sub(r"\s+", " ", f"{ident}|{name}").strip().upper()
    except Exception as exc:
        logger.warning("Could not read CPU registry id: %s", exc)
        return ""


def _windows_system_disk_serial() -> str:
    """
    Serial of the physical disk holding the system drive. Last-resort
    anchor only (a disk swap changes it), so it is used solely when no
    board serial / SMBIOS UUID / system serial is available. Runs a single
    hidden PowerShell call.
    """
    if not WINDOWS_AVAILABLE:
        return ""
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$d=$env:SystemDrive.TrimEnd(':');"
        "$s=(Get-Partition -DriveLetter $d | Get-Disk | Select-Object -First 1).SerialNumber;"
        "if(-not $s){$s=(Get-CimInstance Win32_DiskDrive | "
        "Where-Object {$_.InterfaceType -ne 'USB'} | Sort-Object Index | "
        "Select-Object -First 1).SerialNumber};"
        "Write-Output $s"
    )
    try:
        ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        result = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=25, creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        return _clean_serial(result.stdout.decode("utf-8", "ignore").strip())
    except Exception as exc:
        logger.warning("Could not read system disk serial: %s", exc)
        return ""


def _read_text_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    except OSError:
        return ""


class LicenseManager:
    """
    Generates this machine's hardware fingerprint and verifies Ed25519
    signed license tokens bound to it. Also owns the tamper-resistant
    trial bookkeeping.
    """

    def __init__(self):
        self._machine_fingerprint = None
        self._legacy_fingerprint = None
        self._fingerprint_sources: List[str] = []
        self._public_key = None
        self._machine_guid_cache: Optional[str] = None
        self._trial_block_reason: Optional[str] = None
        self._rollback_detected = False

    # ------------------------------------------------------------------
    # Machine fingerprinting
    # ------------------------------------------------------------------
    @property
    def machine_fingerprint(self) -> str:
        """Get or generate the (v2) machine fingerprint."""
        if self._machine_fingerprint is None:
            self._machine_fingerprint = self._generate_machine_fingerprint()
        return self._machine_fingerprint

    @property
    def legacy_machine_fingerprint(self) -> str:
        """The pre-v2 fingerprint, kept only so old licences still verify."""
        if self._legacy_fingerprint is None:
            self._legacy_fingerprint = self._generate_legacy_machine_fingerprint()
        return self._legacy_fingerprint

    @property
    def fingerprint_sources(self) -> List[str]:
        """Names of the identifiers that fed the v2 fingerprint."""
        _ = self.machine_fingerprint
        return list(self._fingerprint_sources)

    def _collect_hardware_ids(self) -> Dict[str, str]:
        """Gather cpu / guid / anchor identifiers for this platform."""
        cpu = ""
        guid = ""
        anchors: List[Tuple[str, str]] = []

        if WINDOWS_AVAILABLE:
            smbios = _read_smbios_windows()
            cpu = smbios.get("cpu_id", "") or _windows_cpu_registry_id()
            guid = _windows_machine_guid()
            anchors = [
                ("board_serial", smbios.get("board_serial", "")),
                ("system_uuid", smbios.get("system_uuid", "")),
                ("system_serial", smbios.get("system_serial", "")),
            ]
            if not any(v for _, v in anchors):
                anchors.append(("disk_serial", _windows_system_disk_serial()))
        else:
            # Development / non-Windows: best-effort equivalents.
            cpu = (platform.processor() or platform.machine() or "").strip().upper()
            guid = (_read_text_file("/etc/machine-id")
                    or _read_text_file("/var/lib/dbus/machine-id")).lower()
            anchors = [
                ("board_serial", _clean_serial(_read_text_file("/sys/class/dmi/id/board_serial"))),
                ("system_uuid", _clean_serial(_read_text_file("/sys/class/dmi/id/product_uuid"))),
            ]
            if not any(v for _, v in anchors):
                anchors.append(("hostname", platform.node().upper()))

        anchor_name, anchor_value = "", ""
        for name, value in anchors:
            if value:
                anchor_name, anchor_value = name, value
                break

        return {
            "cpu": cpu,
            "guid": guid,
            "anchor_name": anchor_name,
            "anchor": anchor_value,
        }

    def _generate_machine_fingerprint(self) -> str:
        """
        v2 fingerprint: SHA-256 over a labelled, canonical string of
        cpu id + MachineGuid + one stable hardware anchor. This is an
        IDENTIFIER, not a credential - it needs to be stable and
        collision-resistant, not secret.
        """
        try:
            ids = self._collect_hardware_ids()
            sources = [k for k in ("cpu", "guid") if ids.get(k)]
            if ids.get("anchor"):
                sources.append(ids["anchor_name"])
            self._fingerprint_sources = sources

            if len(sources) < 3:
                logger.warning(
                    "Machine fingerprint built from incomplete identifiers: %s. "
                    "License binding may be less unique on this machine.", sources
                )

            material = "|".join([
                FINGERPRINT_VERSION_TAG,
                f"cpu={ids.get('cpu', '')}",
                f"guid={ids.get('guid', '')}",
                f"{ids.get('anchor_name') or 'anchor'}={ids.get('anchor', '')}",
            ])
            fingerprint = hashlib.sha256(material.encode("utf-8")).hexdigest()
            logger.info("Generated machine fingerprint (%s): %s...",
                        "+".join(sources) or "none", fingerprint[:16])
            return fingerprint

        except Exception as e:
            logger.error(f"Error generating machine fingerprint: {e}")
            fallback = hashlib.sha256(
                (FINGERPRINT_VERSION_TAG + "|fallback|" + platform.node()).encode("utf-8")
            ).hexdigest()
            self._fingerprint_sources = ["hostname"]
            logger.warning(f"Using fallback fingerprint: {fallback[:16]}...")
            return fallback

    def _generate_legacy_machine_fingerprint(self) -> str:
        """
        The ORIGINAL fingerprint algorithm, unchanged, used only to keep
        pre-existing licences valid (see ACCEPT_LEGACY_FINGERPRINT). Do
        not issue new licences against this value.
        """
        try:
            components = []

            try:
                cpu_info = platform.processor()
                components.append(cpu_info.strip() if cpu_info else "UNKNOWN_CPU")
            except Exception as e:
                logger.warning(f"Could not get CPU info: {e}")
                components.append("UNKNOWN_CPU")

            if WINDOWS_AVAILABLE:
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                         r"HARDWARE\DESCRIPTION\System\BIOS") as key:
                        motherboard_serial = winreg.QueryValueEx(key, "BaseBoardProduct")[0]
                        components.append(str(motherboard_serial) if motherboard_serial else platform.node())
                except Exception as e:
                    logger.warning(f"Could not get motherboard serial: {e}")
                    components.append(platform.node())
            else:
                components.append(platform.node())

            try:
                import uuid
                components.append(str(uuid.getnode()))
            except Exception as e:
                logger.warning(f"Could not get MAC address: {e}")
                components.append("UNKNOWN_MAC")

            if WINDOWS_AVAILABLE:
                try:
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    volume_serial = kernel32.GetVolumeSerialNumberW(None)
                    components.append(str(volume_serial) if volume_serial else "UNKNOWN_DISK")
                except Exception as e:
                    logger.warning(f"Could not get volume serial: {e}")
                    components.append("UNKNOWN_DISK")
            else:
                try:
                    import shutil
                    disk_usage = shutil.disk_usage(BASE_DIR)
                    components.append(str(disk_usage.total))
                except Exception as e:
                    logger.warning(f"Could not get disk size: {e}")
                    components.append("UNKNOWN_DISK")

            if not any(components):
                components = [platform.node(), platform.machine(), str(os.getpid())]

            fingerprint_input = "|".join(components).encode("utf-8")
            return hashlib.sha256(fingerprint_input).hexdigest()

        except Exception as e:
            logger.error(f"Error generating legacy machine fingerprint: {e}")
            return hashlib.sha256(platform.node().encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Ed25519 verification
    # ------------------------------------------------------------------
    def _get_public_key(self) -> Ed25519PublicKey:
        if self._public_key is None:
            try:
                raw = bytes.fromhex(LICENSE_PUBLIC_KEY_HEX)
                if len(raw) != 32:
                    raise ValueError("Vendor public key must decode to exactly 32 bytes")
                # Integrity pin: in normal (non-dev-override) operation the key
                # in use must be the exact key this build was pinned to.
                if not _PUBLIC_KEY_IS_DEV_OVERRIDE:
                    if hashlib.sha256(raw).hexdigest() != _PUBKEY_SHA256_PIN:
                        raise ValueError("Vendor public key does not match the pinned value")
                self._public_key = Ed25519PublicKey.from_public_bytes(raw)
            except Exception as e:
                raise LicenseError(f"Invalid vendor public key configured: {e}")
        return self._public_key

    def verify_license_token(self, token: str) -> Tuple[bool, str, Optional[Dict]]:
        """
        Verify a license token string against the vendor public key and
        this machine's fingerprint.

        Returns:
            (is_valid, message, payload_dict_or_None)
        """
        if not token or "." not in token:
            return False, "Malformed license token", None

        try:
            payload_b64, sig_b64 = token.strip().split(".", 1)
            payload_bytes = _b64u_decode(payload_b64)
            signature = _b64u_decode(sig_b64)
        except Exception as e:
            return False, f"Malformed license token: {e}", None

        try:
            public_key = self._get_public_key()
        except LicenseError as e:
            logger.error(str(e))
            return False, str(e), None

        try:
            public_key.verify(signature, payload_bytes)
        except InvalidSignature:
            return False, "License signature is invalid (tampered, or not issued for this product)", None
        except Exception as e:
            return False, f"Signature verification error: {e}", None

        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except Exception as e:
            return False, f"License payload is not valid JSON: {e}", None

        token_fingerprint = str(payload.get("machine_fingerprint", "")).lower()
        accepted = {self.machine_fingerprint.lower()}
        if ACCEPT_LEGACY_FINGERPRINT:
            accepted.add(self.legacy_machine_fingerprint.lower())
        if token_fingerprint not in accepted:
            return False, "This license was issued for a different machine", payload
        if token_fingerprint != self.machine_fingerprint.lower():
            logger.info("License matched the legacy machine fingerprint; "
                        "consider re-issuing it against the v2 fingerprint.")

        expires_at = payload.get("expires_at")
        if expires_at:
            try:
                expiry = datetime.fromisoformat(expires_at)
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                # Use the later of the wall clock and the newest recorded
                # activity, so winding the clock back cannot un-expire a licence.
                if self._effective_now_utc() > expiry:
                    return False, f"License expired on {expires_at}", payload
            except ValueError:
                return False, "License payload has an invalid expiry date", payload

        return True, "License is valid", payload

    # ------------------------------------------------------------------
    # License file storage
    # ------------------------------------------------------------------
    def _license_file_path(self) -> str:
        # BASE_DIR is frozen-aware (see config.py) - resolves next to the
        # .exe when packaged, or the project folder in development.
        return os.path.join(BASE_DIR, LICENSE_FILE_NAME)

    def load_license_token(self) -> Optional[str]:
        """Load the raw license token string, preferring the LICENSE_TOKEN
        environment variable, then falling back to the license.lic file."""
        env_token = os.environ.get("LICENSE_TOKEN")
        if env_token:
            return env_token.strip()

        path = self._license_file_path()
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except OSError as e:
                logger.error(f"Could not read license file {path}: {e}")
        return None

    def activate_license_from_string(self, token: str) -> Tuple[bool, str]:
        """
        Validate a license token and, if valid, persist it as this
        machine's active license (writes license.lic next to the app).
        Intended for use by a "paste your license key" activation
        screen/CLI once the customer receives a token from the vendor.
        """
        is_valid, message, _ = self.verify_license_token(token)
        if not is_valid:
            return False, message

        try:
            with open(self._license_file_path(), "w", encoding="utf-8") as f:
                f.write(token.strip())
            logger.info(f"License activated and saved to {self._license_file_path()}")
            return True, "License activated successfully"
        except OSError as e:
            logger.error(f"Could not save license file: {e}")
            return False, f"Could not save license file: {e}"

    def is_license_valid(self) -> Tuple[bool, str]:
        """Check whether a currently-installed license token is valid."""
        token = self.load_license_token()
        if not token:
            return False, "No license file found"
        is_valid, message, _ = self.verify_license_token(token)
        return is_valid, message

    def get_license_payload(self) -> Optional[Dict]:
        """Return the verified license payload dict, or None if no
        currently-installed license verifies successfully."""
        token = self.load_license_token()
        if not token:
            return None
        is_valid, _, payload = self.verify_license_token(token)
        return payload if is_valid else None

    # ------------------------------------------------------------------
    # Secure local marker (encrypted secure_token.dat in AppData)
    # ------------------------------------------------------------------
    def _machine_guid(self) -> str:
        if self._machine_guid_cache is None:
            guid = _windows_machine_guid() if WINDOWS_AVAILABLE else ""
            if not guid:
                guid = (_read_text_file("/etc/machine-id")
                        or _read_text_file("/var/lib/dbus/machine-id")
                        or platform.node())
            self._machine_guid_cache = guid
        return self._machine_guid_cache

    def _secure_token_path(self) -> str:
        if WINDOWS_AVAILABLE:
            base = os.environ.get("LOCALAPPDATA") or os.path.join(
                os.path.expanduser("~"), "AppData", "Local")
        else:
            base = os.path.join(os.path.expanduser("~"), ".local", "share")
        return os.path.join(base, SECURE_TOKEN_DIR_NAME, SECURE_TOKEN_FILE_NAME)

    def _secure_fernet(self) -> Fernet:
        material = ("AIAPS-secure-token-v1|" + self._machine_guid()).encode("utf-8")
        key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
        return Fernet(key)

    def _secure_read(self) -> Tuple[Optional[Dict], str]:
        """
        Returns (state, status) where status is 'ok', 'missing' or 'corrupt'.
        'corrupt' = file exists but cannot be decrypted / parsed (edited,
        damaged, or copied from another machine).
        """
        path = self._secure_token_path()
        if not os.path.isfile(path):
            return None, "missing"
        try:
            with open(path, "rb") as f:
                blob = f.read()
            state = json.loads(self._secure_fernet().decrypt(blob).decode("utf-8"))
            if not isinstance(state, dict):
                return None, "corrupt"
            return state, "ok"
        except (InvalidToken, ValueError, OSError, UnicodeDecodeError):
            return None, "corrupt"

    def _secure_write(self, state: Dict) -> bool:
        path = self._secure_token_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            blob = self._secure_fernet().encrypt(json.dumps(state).encode("utf-8"))
            tmp_path = path + ".tmp"
            with open(tmp_path, "wb") as f:
                f.write(blob)
            os.replace(tmp_path, path)
            return True
        except OSError as e:
            logger.warning(f"Could not write secure token file: {e}")
            return False

    # ------------------------------------------------------------------
    # Registry / .env markers
    # ------------------------------------------------------------------
    def _reg_read(self, name: str) -> Optional[str]:
        if not WINDOWS_AVAILABLE:
            return None
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, LICENSE_REGISTRY_PATH) as key:
                return str(winreg.QueryValueEx(key, name)[0])
        except (FileNotFoundError, OSError):
            return None

    def _reg_write(self, name: str, value: str) -> bool:
        if not WINDOWS_AVAILABLE:
            return False
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, LICENSE_REGISTRY_PATH) as key:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
            return True
        except OSError as e:
            logger.warning(f"Could not write registry value {name}: {e}")
            return False

    def _env_file_path(self) -> str:
        return os.path.join(BASE_DIR, ".env")

    def _env_read(self, key: str) -> Optional[str]:
        path = self._env_file_path()
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith(key + "="):
                        return stripped.split("=", 1)[1].strip()
        except OSError:
            return None
        return None

    def _env_write(self, key: str, value: str) -> bool:
        if self._env_read(key) is not None:
            return True
        try:
            with open(self._env_file_path(), "a", encoding="utf-8") as f:
                f.write(f"\n{key}={value}\n")
            return True
        except OSError as e:
            logger.warning(f"Could not write {key} to .env: {e}")
            return False

    # ------------------------------------------------------------------
    # Clock rollback detection
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_iso_utc(value) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _database_path(self) -> Optional[str]:
        try:
            from config import Config
            uri = getattr(Config, "SQLALCHEMY_DATABASE_URI", "") or ""
        except Exception:
            uri = ""
        if uri.startswith("sqlite:///"):
            return uri[len("sqlite:///"):]
        if uri and not uri.startswith("sqlite"):
            return None  # MySQL etc. - cannot read directly
        return os.path.join(BASE_DIR, "instance", "attendance.db")

    def _db_last_activity_utc(self) -> Optional[datetime]:
        """Newest activity timestamp in the SQLite database, as UTC."""
        db_path = self._database_path()
        if not db_path or not os.path.isfile(db_path):
            return None
        newest: Optional[datetime] = None
        conn = None
        try:
            uri = "file:" + db_path.replace("\\", "/").replace("?", "%3f") + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=2)
            for table, column in _DB_ACTIVITY_COLUMNS:
                try:
                    row = conn.execute(f"SELECT MAX({column}) FROM {table}").fetchone()
                except sqlite3.Error:
                    continue  # table/column not present yet
                if not row or not row[0]:
                    continue
                try:
                    naive = datetime.fromisoformat(str(row[0]).replace(" ", "T"))
                except ValueError:
                    continue
                as_utc = (naive - _IST_OFFSET).replace(tzinfo=timezone.utc) \
                    if naive.tzinfo is None else naive.astimezone(timezone.utc)
                if newest is None or as_utc > newest:
                    newest = as_utc
        except sqlite3.Error as e:
            logger.debug(f"Could not read database activity timestamps: {e}")
        finally:
            if conn is not None:
                conn.close()
        return newest

    def _last_activity_utc(self) -> Optional[datetime]:
        """Newest of: secure-marker last_seen, registry last_seen, DB activity."""
        candidates: List[datetime] = []
        state, _ = self._secure_read()
        if state:
            parsed = self._parse_iso_utc(state.get("last_seen"))
            if parsed:
                candidates.append(parsed)
        parsed = self._parse_iso_utc(self._reg_read(LICENSE_REGISTRY_LAST_SEEN))
        if parsed:
            candidates.append(parsed)
        db_latest = self._db_last_activity_utc()
        if db_latest:
            candidates.append(db_latest)
        return max(candidates) if candidates else None

    def _effective_now_utc(self) -> datetime:
        """max(system clock, newest recorded activity)."""
        now = datetime.now(timezone.utc)
        last = self._last_activity_utc()
        return max(now, last) if last else now

    def is_clock_rolled_back(self) -> bool:
        """True if the system clock is well behind the newest recorded activity."""
        now = datetime.now(timezone.utc)
        last = self._last_activity_utc()
        rolled_back = bool(last and now < last - CLOCK_ROLLBACK_TOLERANCE)
        if rolled_back:
            logger.warning("System clock appears to have been set back "
                           "(now=%s, newest recorded activity=%s)", now.isoformat(), last.isoformat())
        self._rollback_detected = rolled_back
        return rolled_back

    def touch_last_seen(self) -> None:
        """Record 'now' as the newest known time (never moves backwards)."""
        try:
            now = datetime.now(timezone.utc)
            state, status = self._secure_read()
            state = dict(state) if state else {}
            prior = self._parse_iso_utc(state.get("last_seen"))
            if prior is None or now > prior:
                state["last_seen"] = now.isoformat()
                if status != "corrupt":
                    self._secure_write(state)
            prior_reg = self._parse_iso_utc(self._reg_read(LICENSE_REGISTRY_LAST_SEEN))
            if prior_reg is None or now > prior_reg:
                self._reg_write(LICENSE_REGISTRY_LAST_SEEN, now.isoformat())
        except Exception as e:
            logger.debug(f"touch_last_seen failed: {e}")

    # ------------------------------------------------------------------
    # Trial period (tamper-resistant)
    # ------------------------------------------------------------------
    def _is_trial_revoked(self) -> bool:
        state, status = self._secure_read()
        if state and state.get("trial_revoked"):
            return True
        if self._reg_read(LICENSE_REGISTRY_TRIAL_REVOKED) == "1":
            return True
        if self._env_read(ENV_TRIAL_REVOKED_KEY) == "1":
            return True
        return False

    def _revoke_trial(self, reason: str) -> None:
        logger.warning(f"Trial revoked: {reason}")
        self._trial_block_reason = reason
        state, _ = self._secure_read()
        state = dict(state) if state else {}
        state["trial_revoked"] = True
        self._secure_write(state)
        self._reg_write(LICENSE_REGISTRY_TRIAL_REVOKED, "1")
        self._env_write(ENV_TRIAL_REVOKED_KEY, "1")

    @staticmethod
    def _parse_trial_date(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d")
        except ValueError:
            return None

    def _trial_start_markers(self) -> Tuple[Optional[datetime], bool, str]:
        """
        Returns (earliest_start, any_marker_invalid, secure_status).
        Looks at registry, secure token file and .env.
        """
        raw_values = []
        state, status = self._secure_read()
        if state:
            raw_values.append(state.get("trial_start"))
        raw_values.append(self._reg_read(LICENSE_REGISTRY_TRIAL_START))
        raw_values.append(self._env_read(ENV_TRIAL_START_KEY))

        parsed: List[datetime] = []
        invalid = False
        for raw in raw_values:
            if raw is None or raw == "":
                continue
            date = self._parse_trial_date(raw)
            if date is None:
                invalid = True
            else:
                parsed.append(date)
        return (min(parsed) if parsed else None), invalid, status

    def _seed_trial_markers(self, start: datetime) -> None:
        """Write the trial start to every marker location that lacks it."""
        start_str = start.strftime("%Y-%m-%d")
        state, status = self._secure_read()
        state = dict(state) if state else {}
        if status != "corrupt" and state.get("trial_start") != start_str:
            state["trial_start"] = start_str
            self._secure_write(state)
        if self._reg_read(LICENSE_REGISTRY_TRIAL_START) != start_str:
            self._reg_write(LICENSE_REGISTRY_TRIAL_START, start_str)
        if self._env_read(ENV_TRIAL_START_KEY) is None:
            self._env_write(ENV_TRIAL_START_KEY, start_str)

    def start_trial(self) -> bool:
        """
        Start (or continue) the evaluation trial. Returns True while the
        trial is active. The reason for a False return is available in
        self._trial_block_reason.
        """
        self._trial_block_reason = None
        try:
            if self._is_trial_revoked():
                self._trial_block_reason = "Evaluation period ended (trial was revoked)."
                return False

            earliest, invalid, status = self._trial_start_markers()

            if status == "corrupt":
                self._revoke_trial("secure trial marker was modified or copied from another machine")
                self._trial_block_reason = ("Evaluation marker integrity check failed; "
                                            "the trial cannot continue on this machine.")
                return False
            if invalid:
                self._revoke_trial("a trial marker held an unreadable value")
                self._trial_block_reason = "Evaluation marker was modified; the trial cannot continue."
                return False

            today = datetime.now()
            if earliest is None:
                earliest = datetime.strptime(today.strftime("%Y-%m-%d"), "%Y-%m-%d")
                logger.info(f"Trial started: {earliest.strftime('%Y-%m-%d')}")

            # Clock rollback checks (against recorded activity and the start date).
            if self.is_clock_rolled_back():
                self._revoke_trial("system clock was set back")
                self._trial_block_reason = ("The system clock appears to have been set back. "
                                            "The trial cannot continue.")
                return False
            if earliest > today + timedelta(days=1):
                self._revoke_trial("trial start date is in the future")
                self._trial_block_reason = ("The trial start date is in the future (clock changed). "
                                            "The trial cannot continue.")
                return False

            self._seed_trial_markers(earliest)

            end_date = earliest + timedelta(days=TRIAL_DAYS)
            if today > end_date:
                logger.warning("Trial period has expired")
                self.touch_last_seen()
                self._trial_block_reason = "The evaluation period has expired."
                return False

            self.touch_last_seen()
            logger.info(f"Trial active: {(end_date - today).days} days remaining")
            return True
        except Exception as e:
            logger.error(f"Error managing trial: {e}")
            self._trial_block_reason = "The evaluation status could not be determined."
            return False

    def get_trial_days_remaining(self) -> int:
        """Days left in the trial; 0 if none, expired or revoked. No side effects."""
        try:
            if self._is_trial_revoked():
                return 0
            earliest, invalid, status = self._trial_start_markers()
            if invalid or status == "corrupt" or earliest is None:
                return 0
            remaining = (earliest + timedelta(days=TRIAL_DAYS) - datetime.now()).days
            return max(0, remaining)
        except Exception as e:
            logger.error(f"Error getting trial days remaining: {e}")
            return 0

    def get_license_info(self) -> Dict:
        """Comprehensive license information, e.g. for an admin settings page."""
        try:
            is_valid, validation_message = self.is_license_valid()
            payload = self.get_license_payload() if is_valid else None

            info = {
                "machine_fingerprint": self.machine_fingerprint,
                "fingerprint_sources": self.fingerprint_sources,
                "license_valid": is_valid,
                "validation_message": validation_message,
                "trial_active": False if is_valid else self.start_trial(),
                "trial_days_remaining": self.get_trial_days_remaining(),
                "trial_total_days": TRIAL_DAYS,
                "clock_rollback_detected": self._rollback_detected,
            }
            if payload:
                info["customer_name"] = payload.get("customer_name")
                info["issued_at"] = payload.get("issued_at")
                info["expires_at"] = payload.get("expires_at")
                info["edition"] = payload.get("edition")
            return info
        except Exception as e:
            logger.error(f"Error getting license info: {e}")
            return {"error": str(e)}


# Singleton instance
_license_manager = None


def get_license_manager() -> LicenseManager:
    """Get the singleton LicenseManager instance."""
    global _license_manager
    if _license_manager is None:
        _license_manager = LicenseManager()
    return _license_manager


def check_license_on_startup() -> Tuple[bool, str]:
    """
    Check license status on application startup. Called from app.py
    before the application starts.

    Unlike the previous implementation, an internal error while checking
    the license does NOT fail open into "allow startup anyway" - a
    license check that cannot be completed blocks startup, the same as
    an explicitly invalid one, since the whole point of this module is
    that startup must not proceed without a verifiable license.

    Returns:
        Tuple[bool, str]: (should_proceed, message)
    """
    lm = get_license_manager()

    is_valid, message = lm.is_license_valid()
    if is_valid:
        if lm.is_clock_rolled_back():
            logger.warning("License is valid but the system clock looks wrong; "
                           "expiry checks use the latest recorded activity time.")
        lm.touch_last_seen()
        logger.info(f"License validation passed: {message}")
        return True, message

    if lm.start_trial():
        days_remaining = lm.get_trial_days_remaining()
        logger.info(f"Trial mode: {days_remaining} day(s) remaining")
        return True, f"Trial mode: {days_remaining} day(s) remaining"

    logger.error(f"License validation failed: {message}")
    trial_note = f" {lm._trial_block_reason}" if lm._trial_block_reason else ""
    return False, (
        f"{message}. No valid license found and no active trial.{trial_note}\n"
        f"Machine fingerprint: {lm.machine_fingerprint}\n"
        f"Please contact support to purchase a license, or place a valid "
        f"'{LICENSE_FILE_NAME}' file next to the application (or set the "
        f"LICENSE_TOKEN environment variable)."
    )


if __name__ == "__main__":
    # Developer helper:
    #   python -m licensing.license_manager encode-key <64-char-public-key-hex>
    #   python -m licensing.license_manager fingerprint
    if len(sys.argv) >= 3 and sys.argv[1] == "encode-key":
        result = encode_public_key_for_embedding(sys.argv[2])
        print("Paste these two constants into licensing/license_manager.py:\n")
        print(f"_PUBKEY_ENC_PARTS = {result['parts']!r}")
        print(f"_PUBKEY_SHA256_PIN = {result['pin']!r}")
    elif len(sys.argv) >= 2 and sys.argv[1] == "fingerprint":
        mgr = LicenseManager()
        print(mgr.machine_fingerprint)
        print("sources:", ", ".join(mgr.fingerprint_sources))
    else:
        print("Usage:\n"
              "  python -m licensing.license_manager encode-key <public-key-hex>\n"
              "  python -m licensing.license_manager fingerprint")