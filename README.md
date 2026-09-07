# AI Attendance & Payroll System

A Flask-based AI Attendance & Payroll System that recognizes employees by face
(DeepFace + FaceNet512, with a password-verified manual fallback), runs a
single shared rule engine for attendance status, and drives an end-to-end
payroll pipeline — configurable allowances/deductions, AES-256-encrypted PDF
payslips, and automated email delivery. It is designed to be packaged as a
**standalone Windows `.exe`** (via PyInstaller) and handed to a non-technical
client who double-clicks one file and gets a working local application —
no Python install, no `pip install`, no manual server setup.

This README is the single source of truth for taking the project from a
development checkout to a signed-off, shippable `.exe` on a client's machine.

---

## Table of Contents

1. [System Architecture & Features Overview](#1-system-architecture--features-overview)
2. [Prerequisites & Environment Setup](#2-prerequisites--environment-setup)
3. [Local Development Execution](#3-local-development-execution)
4. [Environment Variables (`.env`)](#4-environment-variables-env)
5. [DeepFace Model Weights — Offline Client Machines](#5-deepface-model-weights--offline-client-machines)
6. [Building the Windows `.exe` with PyInstaller](#6-building-the-windows-exe-with-pyinstaller)
7. [Production Deployment & `dist/` Folder Structure](#7-production-deployment--dist-folder-structure)
8. [First-Time Launch & Setup Wizard](#8-first-time-launch--setup-wizard)
9. [Troubleshooting Guide](#9-troubleshooting-guide)
10. [Appendix: File Map](#10-appendix-file-map)

---

## 1. System Architecture & Features Overview

### 1.1 High-level architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Windows client machine                                         │
│                                                                   │
│   AttendancePayrollSystem.exe  (PyInstaller onefile/onedir)     │
│        │                                                         │
│        ├─ launcher.py  → runs app.py's __main__ block           │
│        │                  (Flask app → browser)                 │
│        │                                                         │
│        ├─ Flask app (Werkzeug dev server, 127.0.0.1:5000)       │
│        │     ├─ Admin / Manager / Employee routes & blueprints  │
│        │     ├─ Face recognition engine (DeepFace/OpenCV)       │
│        │     ├─ APScheduler background jobs (payroll, logout)   │
│        │     └─ PDF generation (ReportLab + pikepdf AES-256)    │
│        │                                                         │
│        ├─ SQLite database  → instance/attendance.db             │
│        ├─ Employee face photos (encrypted) → dataset/           │
│        ├─ Payslips / uploads → uploads/                         │
│        ├─ Face embeddings cache → trained_model/                │
│        └─ .env (secrets, config)                                │
│                                                                   │
│   Default OS browser opens automatically to http://127.0.0.1:5000│
└─────────────────────────────────────────────────────────────────┘
```

The app is a normal Flask web app; PyInstaller just freezes the Python
interpreter + all dependencies + your source into one executable, and
`launcher.py` opens the user's default browser pointed at `localhost` so it
*feels* like a native desktop app even though it's a local web server.

### 1.2 Roles

| Role | Access |
|---|---|
| **Admin** | Full system: employees, payroll, settings, reports, approvals |
| **Manager** | An Employee flagged as a manager; approves manual-attendance/logout-regularization requests for their scope |
| **Employee** | Self-service dashboard: own attendance, payslips, profile, password |

### 1.3 Feature summary

- **Attendance:** public kiosk face-recognition scanning, strict-match
  cosine-distance matching with a confidence margin (no guessing on
  look-alikes), frame-presence locking (one punch per continuous
  appearance), password-verified manual fallback with mandatory
  manager/admin approval, full audit trail (`attendance_type`,
  `approval_status`, `submission_timestamp`).
- **Rule engine:** one shared engine (`attendance.py::AttendanceManager`)
  computes Present / Late / Half-Day / Absent for both face and manual
  punches — status is never hardcoded per entry point.
- **Payroll:** configurable allowances (HRA, DA, Medical, Travel, Special,
  Other) and deductions (PF, ESIC, TDS, Professional Tax, LOP, Late,
  Transport), automated monthly generation via APScheduler, AES-256
  password-protected PDF payslips (deterministic password rule), and
  deliverability-conscious email delivery.
- **Reporting:** admin dashboard analytics, department-wise stats, PDF
  export for both admin and employee reports, all backed by one shared
  aggregation service so numbers never disagree between screens.
- **Settings:** versioned attendance settings (past attendance is always
  evaluated against the rules in force *on that date*), payroll settings,
  company branding settings — all editable from the UI, no code changes.
- **Security:** encrypted-at-rest biometric photos (Fernet/AES), CSRF
  protection, rate-limited login, password hashing via Werkzeug.

### 1.4 Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.10+, Flask 3.x, SQLAlchemy 2.x |
| AI / CV | DeepFace (FaceNet512), OpenCV-Contrib, MediaPipe, TensorFlow 2.15 / tf-keras |
| Frontend | Bootstrap 5, vanilla JS (`fetch`), Chart.js |
| Database | SQLite (default) or MySQL (via PyMySQL) |
| PDF | ReportLab (layout), pikepdf (AES-256 password protection) |
| Scheduling | APScheduler (background jobs) |
| Packaging | PyInstaller 6.x + pyinstaller-hooks-contrib |

---

## 2. Prerequisites & Environment Setup

### 2.1 Required software

| Requirement | Version | Notes |
|---|---|---|
| Python | **3.10.x** (3.10.11 recommended) | TensorFlow 2.15 / mediapipe wheels on Windows are most reliable on 3.10. Do not use 3.12+. |
| pip | Latest | `python -m pip install --upgrade pip` |
| Git | Any recent | To clone/manage the repo |
| Windows | 10/11, 64-bit | Build and ship for 64-bit only |
| Webcam | Any USB/integrated | Required for face capture and recognition |
| (Optional) MySQL Server | 8.x | Only if you choose MySQL instead of the default SQLite |

> **Why 3.10, specifically?** DeepFace, TensorFlow 2.15, `tf-keras`, and
> `mediapipe==0.10.21` all publish official Windows wheels for 3.9–3.11.
> 3.10 is the safest intersection. If you must use a different version,
> confirm every package in `requirements.txt` has a matching wheel
> **before** you invest time in a PyInstaller build.

### 2.2 Clone and create a virtual environment

```bash
git clone <your-repo-url> AI_APS
cd AI_APS

python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux (dev only — ship for Windows)

python -m pip install --upgrade pip
pip install -r requirements.txt
```

Installing `requirements.txt` pulls in TensorFlow, MediaPipe, and OpenCV —
expect a multi-GB download and several minutes on first install.

### 2.3 Verify the install

```bash
python -c "import cv2, tensorflow, mediapipe, deepface; print('OK')"
```

If this fails, resolve it here before touching PyInstaller — packaging
problems are much harder to diagnose than a plain import error.

---

## 3. Local Development Execution

```bash
# From the project root, with venv activated
set FLASK_ENV=development        # Windows (cmd)
# $env:FLASK_ENV="development"   # Windows (PowerShell)
# export FLASK_ENV=development   # macOS/Linux

python app.py
```

You should see:

```
============================================================
🚀 AI Attendance & Payroll System
   Host: 127.0.0.1  |  Port: 5000
   Mode: DEVELOPMENT (debug=True)
============================================================
```

Open `http://127.0.0.1:5000/` — the public kiosk landing page. Admin/Employee
login is one click away from there.

**First run creates, next to `app.py`:**
- `instance/attendance.db` (SQLite database)
- `dataset/`, `uploads/`, `trained_model/` (created automatically if missing)

If no admin account exists yet, you'll be routed into the **Setup Wizard**
(see [Section 8](#8-first-time-launch--setup-wizard)).

---

## 4. Environment Variables (`.env`)

Create a `.env` file **next to `app.py`** (dev) or **next to the `.exe`**
(packaged build) — never commit this file, and never ship the developer's
own `.env` to a customer. Each install should get its own.

```ini
# ── Flask Core ──────────────────────────────────────────────────────
SECRET_KEY=<REQUIRED — see warning below>
FLASK_ENV=production                       # production | development

# ── Database ─────────────────────────────────────────────────────────
# Leave unset to default to a local SQLite file at instance/attendance.db
# DATABASE_URL=mysql+pymysql://user:password@localhost/attendance_db

# ── Email (SMTP) — for payslip delivery, password resets, notifications ──
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=your-company@gmail.com
MAIL_PASSWORD=<app password, NOT your normal Gmail password>
MAIL_DEFAULT_SENDER=your-company@gmail.com

# ── Company Branding ─────────────────────────────────────────────────
COMPANY_NAME=Your Company Pvt Ltd
COMPANY_LOGO=static/images/company_logo.png

# ── Office Timing ────────────────────────────────────────────────────
OFFICE_START_TIME=09:00
OFFICE_END_TIME=18:00
GRACE_PERIOD_MINUTES=15

# ── Working Hours & Salary Rules ─────────────────────────────────────
WORKING_HOURS_PER_DAY=9.0
LATE_DEDUCTION_ENABLED=false
LATE_DEDUCTION_PER_OCCURRENCE=0.0
OVERTIME_ENABLED=true
OVERTIME_RATE=1.5

# ── Face Recognition ─────────────────────────────────────────────────
FACE_RECOGNITION_TOLERANCE=0.6             # admin-configurable ceiling; see ai_engine.py STRICT_MAX_TOLERANCE
MIN_FACE_IMAGES_REQUIRED=20

# ── Rate Limiting ─────────────────────────────────────────────────────
RATELIMIT_STORAGE_URI=memory://            # fine for single-process desktop use

# ── Biometric Encryption Key ─────────────────────────────────────────
# DO NOT SET THIS MANUALLY on a fresh install. crypto_utils.py generates
# and appends it here automatically the first time it's needed, and logs
# a warning telling you to back up this file. If it's already present
# (e.g. you're restoring a previous install), leave it exactly as-is.
# FACE_DATA_ENCRYPTION_KEY=<auto-generated — back this up>
```

### 4.1 Mandatory vs. optional keys

| Key | Required? | Consequence if missing |
|---|---|---|
| `SECRET_KEY` | **Yes, effectively mandatory** | Falls back to a hardcoded default (`'your-secret-key-change-in-production'`) if unset. **Never ship a customer install without setting this explicitly** — an unset `SECRET_KEY` means every install shares the same, publicly-known key, which breaks session/CSRF-token integrity. Generate one per install: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `MAIL_*` | Only if email features are used | Payslip email delivery, password-reset emails, and approval notifications silently fail/log errors without valid SMTP credentials. The app still runs fine without them. |
| `DATABASE_URL` | No | Defaults to a local SQLite file — the right choice for a single-site desktop install. Only set this for a MySQL deployment. |
| `FACE_DATA_ENCRYPTION_KEY` | No — auto-managed | Auto-generated and persisted on first use by `crypto_utils.py`. **Back up `.env` once this key exists** — losing it permanently locks you out of previously-captured face photos (this is inherent to encryption, not a bug). |
| `COMPANY_*`, office timing, salary rule defaults | No | Sensible defaults exist in `config.py`; these are just convenient overrides. All are also editable later from the admin **Settings** UI. |

---

## 5. DeepFace Model Weights — Offline Client Machines

DeepFace downloads its model weight files (e.g. `facenet512_weights.h5`,
plus the RetinaFace detector weights) to `~/.deepface/weights` **the first
time it actually runs a face operation** — not at import time. A customer's
machine will very likely have no internet access at the moment they first
launch the app, or may be permanently offline (many attendance-kiosk PCs
are). **If you skip this step, the shipped `.exe` will fail (or hang trying
to reach the internet) the very first time someone tries to register a
face.**

### 5.1 Pre-download the weights on your *build* machine

```bash
# With your venv activated, from the project root
python app.py
```

Then, in the browser:
1. Log in as Admin (complete the Setup Wizard first if this is a fresh dev DB).
2. Add a test employee and go through **Face Registration** — capture at
   least one photo. This forces DeepFace to download and cache every
   weight file it needs (FaceNet512 + RetinaFace detector).
3. Stop the app (`Ctrl+C`).
4. Confirm the weights landed on disk:

```bash
dir %USERPROFILE%\.deepface\weights          # Windows
# ls ~/.deepface/weights                     # macOS/Linux
```

You should see files like `facenet512_weights.h5` and RetinaFace-related
weight files, typically totaling 100–300 MB.

### 5.2 Point the build at those weights

`attendance_app.spec` auto-detects `~/.deepface/weights` by default. If your
weights live somewhere else (a shared build server, a different user
profile), set an environment variable before building:

```bash
set DEEPFACE_WEIGHTS_DIR=C:\path\to\.deepface\weights
pyinstaller attendance_app.spec --clean
```

The spec prints one of two messages during the build so you can confirm
this worked *before* handing the exe to a customer:

```
[spec] Bundling DeepFace weights from: C:\Users\you\.deepface\weights
```
or, if not found:
```
[spec] WARNING: DeepFace weights folder not found or empty at '...'.
Building WITHOUT bundled weights - the shipped exe will try to download
them from the internet on the customer's machine...
```

**Do not ship a build that printed the WARNING** unless you've confirmed
the client site has internet access on first use.

---

## 6. Building the Windows `.exe` with PyInstaller

### 6.1 Install build-only tools

These are intentionally kept out of `requirements.txt` (customers never
need them):

```bash
pip install -r requirements-packaging.txt
```

### 6.2 Pre-build checklist

Run through this **every time**, not just the first build:

- [ ] `requirements.txt` installed cleanly in a venv that has never had
      plain `opencv-python` installed alongside `opencv-contrib-python`
      (they conflict — see `requirements.txt` comments). If in doubt,
      rebuild the venv from scratch:
      ```bash
      deactivate
      rmdir /s /q venv
      python -m venv venv
      venv\Scripts\activate
      pip install --upgrade pip
      pip install -r requirements.txt
      pip install -r requirements-packaging.txt
      ```
- [ ] DeepFace weights are populated (Section 5) and either auto-detected
      or pointed to via `DEEPFACE_WEIGHTS_DIR`.
- [ ] You have an `.ico` file ready if you want a custom exe icon (point
      `icon=` in `attendance_app.spec` at it — optional).

### 6.3 Build

```bash
pyinstaller attendance_app.spec --clean
```

`--clean` removes PyInstaller's cache before building — always use it after
changing `requirements.txt`, the spec file, or Python version, to avoid
stale-cache packaging bugs.

The build produces:
```
dist/
└── AttendancePayrollSystem/          # onedir build — folder, not a single file
    ├── AttendancePayrollSystem.exe
    ├── templates/
    ├── static/
    ├── dataset/                      # empty scaffold — see Section 7
    ├── uploads/
    ├── trained_model/
    └── ... (bundled Python runtime, DLLs, deepface_weights/ if bundled)
```

> The current spec builds a **onedir** app (a folder containing the exe and
> its dependencies), not a single-file `--onefile` exe. Onedir starts
> noticeably faster (no self-extraction step on every launch) and is the
> recommended mode for a TensorFlow/DeepFace-heavy app like this one. Ship
> the whole `AttendancePayrollSystem` folder, not just the `.exe` file.

### 6.4 Verify the build — do this away from your dev machine

Copy the entire `dist/AttendancePayrollSystem/` folder to:
- a **different folder** outside your project (e.g. `C:\Temp\test-install`), or
- ideally, a **clean VM or a second physical machine** with no Python installed.

Many "works on my machine" packaging bugs (missing DLL, missing data file,
stale `sys.path` entry) only surface once you're away from your own dev
environment's installed Python.

Then work through the checklist at the bottom of `attendance_app.spec`
before shipping — it covers first-run DB creation, the setup wizard, the
scheduler, face capture, PDF/email, and AV false-positive checks, in the
order you should test them.

---

## 7. Production Deployment & `dist/` Folder Structure

### 7.1 Where to install on the client machine

Install to a location the exe can **write to without admin elevation**:

```
✅  C:\Users\<user>\AppData\Local\AttendancePayrollSystem\
✅  C:\AttendancePayrollSystem\                (if the account has write access)
❌  C:\Program Files\AttendancePayrollSystem\  (write-protected by default —
                                                  SQLite/photo/log writes will
                                                  fail with permission errors)
```

### 7.2 Folder layout after first launch

```
AttendancePayrollSystem/
├── AttendancePayrollSystem.exe      # entry point — customer double-clicks this
├── .env                              # created by you before first launch (Section 4)
├── instance/
│   └── attendance.db                 # created automatically on first launch
├── dataset/
│   └── <employee_id>/                # encrypted face photos, created per employee
├── uploads/
│   └── payrolls/<year>/<month>/      # generated payslip PDFs
├── trained_model/
│   └── embeddings_cache.pkl          # face embedding cache, rebuilt as needed
├── templates/, static/               # bundled UI assets — do not edit on client machines
└── (bundled runtime: python3xx.dll, _internal/, deepface_weights/, etc.)
```

`instance/`, `dataset/`, `uploads/`, and `trained_model/` are all created
automatically the first time they're needed — you do not need to
pre-create them, but you **do** need write permission in the install
folder for this to succeed (see 7.1).

### 7.3 Backups

Back up these four things together, as a set, on whatever schedule you back
up the database:

```
instance/attendance.db      ← all records
.env                        ← SECRET_KEY, FACE_DATA_ENCRYPTION_KEY, SMTP creds
dataset/                    ← encrypted face photos (undecryptable without .env's key)
uploads/                    ← generated payslips
```

Losing `.env` without a backup makes every previously-encrypted face photo
and any custom payslip password **permanently unrecoverable** — this is
inherent to encryption, not a bug to report.

---

## 8. First-Time Launch & Setup Wizard

### 8.1 What the customer sees

1. Double-click `AttendancePayrollSystem.exe`.
2. No console window appears (see `console=False` in the spec) — the app
   starts silently in the background.
3. The default browser opens automatically to `http://127.0.0.1:5000/`.
4. If no admin account exists yet, the user is routed into the **Setup
   Wizard** (`setup_wizard.py` / `setup_wizard.html`):
   - Create the first Admin account (username/password).
   - Enter Company Settings (name, address, logo, contact info).
   - Set initial office timing / working-hours defaults (all editable
     later from Settings).
5. From here on, `/` is the public kiosk attendance screen; Admin/Employee
   login is one click away.

---

## 9. Troubleshooting Guide

### 9.1 Permission errors (database / uploads / dataset)

**Symptom:** `sqlite3.OperationalError: unable to open database file`, or
face photos / payslips silently fail to save.

**Cause:** the exe is installed somewhere Windows restricts write access
(`Program Files`, a read-only network share) without running as
Administrator.

**Fix:**
- Move the install to `%LOCALAPPDATA%\AttendancePayrollSystem\` (Section 7.1), or
- Right-click the exe → Properties → Compatibility → confirm it's not
  forced to run in a virtualized/read-only mode, or
- As a last resort, "Run as Administrator" — not recommended as a
  permanent fix, since it changes file ownership in ways that can cause
  *different* permission errors for a non-admin user later.

### 9.2 Camera / webcam access fails

**Symptom:** `Could not open webcam`, blank camera preview, or
`cv2.VideoCapture(0)` returns `isOpened() == False`.

**Checklist:**
1. **Windows Camera Privacy Settings**: Settings → Privacy & security →
   Camera → ensure "Let desktop apps access your camera" is **On**. This
   is the #1 cause on fresh Windows installs — a packaged exe has no
   camera permission dialog of its own to prompt you.
2. **Camera already in use**: close Zoom/Teams/Windows Camera app/any
   other program holding the camera — OpenCV cannot share device access.
3. **Wrong device index**: if the machine has multiple cameras (e.g. a
   laptop webcam + a USB kiosk camera), `cv2.VideoCapture(0)` may grab
   the wrong one. Try `cv2.VideoCapture(1)` in a quick test script to
   confirm the index, then adjust `FaceCapture.start_capture()` in
   `ai_engine.py` accordingly for that install.
4. **Driver issue**: confirm the camera works in the built-in Windows
   Camera app first — if it doesn't work there, it's a driver problem,
   not an application problem.

### 9.3 Background scheduler doesn't seem to run

**Symptom:** monthly payroll never auto-generates; auto-logout
regularization requests never appear at 23:59.

**Checklist:**
1. Check the log output around startup for:
   ```
   Payroll scheduler started
   DAILY APPROVAL SCHEDULER REGISTERED - 23:59
   Payroll Next Run: ...
   ```
   If these lines are missing, the scheduler failed to start — look for
   `Failed to start scheduler:` earlier in the log for the actual cause.
2. **Most common packaged-build cause: missing APScheduler entry-point
   metadata.** APScheduler discovers its jobstore/trigger plugins via
   `importlib.metadata`, not plain imports — `pyinstaller-hooks-contrib`
   plus the explicit `copy_metadata('APScheduler')` in
   `attendance_app.spec` handles this, but if you ever hand-edit the spec
   and remove that line, the scheduler will fail silently at init time
   with no obvious import error to point at.
3. **The exe was not left running.** Unlike a server, this app has no
   background service — the scheduler only runs while
   `AttendancePayrollSystem.exe` is open. If the customer closes the
   browser tab (but the exe process is still running in the background,
   which is normal — see 9.6) versus closing the exe process itself, only
   the latter stops the scheduler. Use Task Manager to confirm
   `AttendancePayrollSystem.exe` is actually still running.
4. **System was asleep/off at the scheduled time.** APScheduler cannot
   run a job while the machine is off or asleep. This is exactly why
   `scheduler_service.py` includes a reconciliation pass on every startup
   that detects and backfills a missed payroll period — confirm this ran
   by checking for `PAYROLL RECONCILIATION CHECK` in the logs after a
   restart.

### 9.4 Antivirus / Windows Defender false positives

**Symptom:** Defender (or another AV) quarantines the exe, or SmartScreen
blocks it with "Windows protected your PC."

This is extremely common for PyInstaller-built executables, especially
ones bundling TensorFlow/OpenCV, and is **not** unique to this project.

**Mitigations, roughly in order of effectiveness:**
1. **Code-sign the exe** with a purchased code-signing certificate. This
   is close to mandatory for anything you charge money for — an unsigned
   exe from an unknown publisher is exactly the SmartScreen/Defender
   heuristic trigger.
2. Rebuild with `upx=False` in `attendance_app.spec` — UPX-compressed
   executables are disproportionately flagged by heuristic AV engines
   even when clean, because malware also commonly uses UPX to evade
   signature detection.
3. Submit the exe to Microsoft for analysis
   (https://www.microsoft.com/en-us/wdsi/filesubmission) if Defender
   specifically flags it — false positives on legitimate PyInstaller apps
   are regularly reviewed and whitelisted this way, though it can take a
   few days.
4. As a stopgap for a specific customer site only, an IT admin can add an
   exclusion for the install folder in Windows Security settings — do not
   rely on this as your primary distribution strategy.

### 9.5 "ModuleNotFoundError" or "DLL load failed" only in the built exe

**Symptom:** `python app.py` works fine, but the packaged exe crashes on
startup or the first time a specific feature (face capture, PDF export)
is used.

**This means a hidden import or data file wasn't bundled.** Work through:
1. Confirm you built with `pip install pyinstaller-hooks-contrib` present
   — it ships the community hooks for TensorFlow/MediaPipe/PIL that
   vanilla PyInstaller doesn't know about.
2. Check whether the missing module belongs to a package already listed
   in `attendance_app.spec`'s `hiddenimports`/`collect_submodules` calls —
   if it's a new dependency you've since added to `requirements.txt`, it
   needs its own line added to the spec.
3. Re-run with `pyinstaller attendance_app.spec --clean` — a stale build
   cache can mask a spec-file fix you already made.
4. Temporarily set `console=True` in the spec **on your own machine only**
   to see the actual traceback (never ship a build with `console=True`).

### 9.6 The exe process stays running after closing the browser tab

This is expected behavior, not a bug: closing the browser tab does not
close the Flask server or the background scheduler — only closing the
`AttendancePayrollSystem.exe` process (via its window, if one is provided,
or Task Manager) does. Communicate this clearly to end users if they
expect "closing the window" to fully quit the app — consider adding a
system tray icon with an explicit "Quit" action in a future iteration if
this causes confusion.

---

## 10. Appendix: File Map

| File | Purpose |
|---|---|
| `app.py` | Main Flask app, routes, startup block |
| `launcher.py` | PyInstaller entry point — wraps `app.py` for frozen builds |
| `config.py` | Environment-aware configuration, frozen-safe `BASE_DIR` |
| `database.py` | SQLAlchemy/Flask-Migrate init, lightweight ad-hoc migrations |
| `models.py` | All ORM models |
| `ai_engine.py` | Face detection/recognition engine, embedding cache, presence tracker |
| `crypto_utils.py` | At-rest encryption for face photos & custom payslip passwords |
| `attendance.py` | Core attendance status rule engine |
| `payroll.py` | Payroll calculation engine |
| `pdf_generator.py` | Payslip/report PDF generation + AES-256 password protection |
| `email_service.py` | SMTP email delivery for payslips, resets, notifications |
| `scheduler_service.py` | APScheduler jobs: auto-logout, monthly payroll, reconciliation |
| `setup_wizard.py` | First-run admin/company setup flow |
| `attendance_app.spec` | PyInstaller build spec |
| `requirements.txt` | Runtime dependencies |
| `requirements-packaging.txt` | Build-only dependencies (PyInstaller, etc.) |

---

**Questions or issues during packaging?** Work through Section 6.4's build
verification checklist and Section 9's troubleshooting guide in order —
the overwhelming majority of PyInstaller packaging issues for this stack
are covered by one of those two sections.