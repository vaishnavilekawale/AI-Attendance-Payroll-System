# AI Attendance & Payroll System

A Flask-based attendance and payroll platform that recognizes employees by
face (DeepFace + FaceNet512, with a password-verified manual fallback),
applies one shared rule engine to decide attendance status, and runs an
end-to-end payroll pipeline — configurable allowances/deductions,
AES-256-encrypted PDF payslips, and automated email delivery.

It's packaged as a **standalone Windows `.exe`** (via PyInstaller) so a
non-technical client can double-click one file and get a working local
app — no Python install, no `pip install`, no server setup.

This README walks through everything from a dev checkout to a signed-off,
shippable `.exe` on a client's machine.

---

## Contents

1. [Quick Start](#1-quick-start)
2. [Architecture & Features](#2-architecture--features)
3. [Prerequisites](#3-prerequisites)
4. [Running Locally](#4-running-locally)
5. [Environment Variables (`.env`)](#5-environment-variables-env)
6. [DeepFace Model Weights (Offline Machines)](#6-deepface-model-weights-offline-machines)
7. [Building the Windows `.exe`](#7-building-the-windows-exe)
8. [Deploying to a Client Machine](#8-deploying-to-a-client-machine)
9. [First Launch & Setup Wizard](#9-first-launch--setup-wizard)
10. [Troubleshooting](#10-troubleshooting)
11. [File Map](#11-file-map)

---

## 1. Quick Start

For someone who just wants to get the app running locally, right now:

```bash
git clone <https://github.com/vaishnavilekawale/AI-Attendance-Payroll-System> AI_APS
cd AI_APS

python -m venv venv
venv\Scripts\activate              # Windows
python -m pip install --upgrade pip
pip install -r requirements.txt

python app.py
```

Then open **http://127.0.0.1:5000/** in a browser. If no admin account
exists yet, you'll land in the Setup Wizard automatically.

Everything below explains this in more depth, plus how to configure,
package, and ship the app to a client.

---

## 2. Architecture & Features

### 2.1 How it fits together

```
┌─────────────────────────────────────────────────────────────────┐
│  Windows client machine                                         │
│                                                                   │
│   AttendancePayrollSystem.exe  (PyInstaller onedir build)        │
│        │                                                         │
│        ├─ launcher.py  → runs app.py's __main__ block            │
│        │                  (starts Flask, opens the browser)      │
│        │                                                         │
│        ├─ Flask app (Werkzeug dev server, 127.0.0.1:5000)        │
│        │     ├─ Admin / Manager / Employee routes & blueprints   │
│        │     ├─ Face recognition engine (DeepFace/OpenCV)        │
│        │     ├─ APScheduler background jobs (payroll, logout)    │
│        │     └─ PDF generation (ReportLab + pikepdf AES-256)     │
│        │                                                         │
│        ├─ SQLite database  → instance/attendance.db              │
│        ├─ Employee face photos (encrypted) → dataset/            │
│        ├─ Payslips / uploads → uploads/                          │
│        ├─ Face embeddings cache → trained_model/                 │
│        └─ .env (secrets, config)                                 │
│                                                                   │
│   Default OS browser opens automatically to http://127.0.0.1:5000│
└─────────────────────────────────────────────────────────────────┘
```

In plain terms: this is a normal Flask web app. PyInstaller freezes the
Python interpreter, all dependencies, and your source code into one
executable, and `launcher.py` opens the user's default browser pointed at
`localhost` — so it *feels* like a native desktop app, even though it's
really a local web server.

### 2.2 Roles

| Role | Access |
|---|---|
| **Admin** | Full system: employees, payroll, settings, reports, approvals |
| **Manager** | An employee flagged as a manager; approves manual-attendance/logout-regularization requests within their scope |
| **Employee** | Self-service dashboard: own attendance, payslips, profile, password |

### 2.3 Features at a glance

**Attendance**
- Public kiosk face-recognition scanning
- Strict cosine-distance matching with a confidence margin (won't guess on look-alikes)
- Frame-presence locking — one punch per continuous appearance
- Password-verified manual fallback, with mandatory manager/admin approval
- Full audit trail (`attendance_type`, `approval_status`, `submission_timestamp`)

**Rule engine**
- A single shared engine (`attendance.py::AttendanceManager`) computes
  Present / Late / Half-Day / Absent for both face and manual punches —
  status is never hardcoded per entry point

**Payroll**
- Configurable allowances (HRA, DA, Medical, Travel, Special, Other) and
  deductions (PF, ESIC, TDS, Professional Tax, LOP, Late, Transport)
- Automated monthly generation via APScheduler
- AES-256 password-protected PDF payslips
- Deliverability-conscious email delivery

**Reporting**
- Admin dashboard analytics and department-wise stats
- PDF export for both admin and employee reports
- One shared aggregation service, so numbers never disagree between screens

**Settings**
- Versioned attendance settings — past attendance is always evaluated
  against the rules that were in force *on that date*
- Payroll settings and company branding, all editable from the UI —
  no code changes needed

**Security**
- Encrypted-at-rest biometric photos (Fernet/AES)
- CSRF protection
- Rate-limited login
- Password hashing via Werkzeug

### 2.4 Tech stack

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

## 3. Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | **3.10.x** (3.10.11 recommended) | TensorFlow 2.15 / MediaPipe wheels on Windows are most reliable on 3.10. Avoid 3.12+. |
| pip | Latest | `python -m pip install --upgrade pip` |
| Git | Any recent | To clone/manage the repo |
| Windows | 10/11, 64-bit | Build and ship for 64-bit only |
| Webcam | Any USB/integrated | Required for face capture and recognition |
| MySQL Server | 8.x (optional) | Only needed if you use MySQL instead of the default SQLite |

> **Why Python 3.10 specifically?** DeepFace, TensorFlow 2.15, `tf-keras`,
> and `mediapipe==0.10.21` all publish official Windows wheels for
> 3.9–3.11, and 3.10 is the safest intersection. If you need a different
> version, confirm every package in `requirements.txt` has a matching
> wheel **before** investing time in a PyInstaller build.

---

## 4. Running Locally

```bash
git clone <https://github.com/vaishnavilekawale/AI-Attendance-Payroll-System> AI_APS
cd AI_APS

python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux (dev only — ship for Windows)

python -m pip install --upgrade pip
pip install -r requirements.txt
```

Installing `requirements.txt` pulls in TensorFlow, MediaPipe, and OpenCV —
expect a multi-GB download and several minutes on first install.

**Verify the install:**

```bash
python -c "import cv2, tensorflow, mediapipe, deepface; print('OK')"
```

If this fails, fix it here before touching PyInstaller — a plain import
error is far easier to diagnose than the same problem inside a packaged
build.

**Start the app:**

```bash
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

Open `http://127.0.0.1:5000/` — the public kiosk landing page. Admin and
Employee login are one click away from there.

On first run, the app automatically creates (next to `app.py`):
- `instance/attendance.db` (SQLite database)
- `dataset/`, `uploads/`, `trained_model/`

If no admin account exists yet, you'll be routed into the
[Setup Wizard](#9-first-launch--setup-wizard).

---

## 5. Environment Variables (`.env`)

Create a `.env` file **next to `app.py`** (dev) or **next to the `.exe`**
(packaged build). Never commit this file, and never ship the developer's
own `.env` to a customer — each install should get its own.

```ini
# ── Flask Core ──────────────────────────────────────────────────────
SECRET_KEY=<REQUIRED — see note below>
FLASK_ENV=production                       # production | development

# ── Database ─────────────────────────────────────────────────────────
# Leave unset to default to a local SQLite file at instance/attendance.db
# DATABASE_URL=mysql+pymysql://user:password@localhost/attendance_db

# ── Email (SMTP) — payslip delivery, password resets, notifications ──
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

### 5.1 Which keys actually matter

| Key | Required? | What happens if it's missing |
|---|---|---|
| `SECRET_KEY` | **Yes, effectively mandatory** | Falls back to a hardcoded default (`'your-secret-key-change-in-production'`). **Never ship a customer install without setting this explicitly** — an unset `SECRET_KEY` means every install shares the same, publicly-known key, which breaks session/CSRF-token integrity. Generate a unique one per install: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `MAIL_*` | Only if email is used | Payslip emails, password resets, and approval notifications silently fail/log errors without valid SMTP credentials. Everything else still runs fine. |
| `DATABASE_URL` | No | Defaults to a local SQLite file — the right choice for a single-site desktop install. Only set this for a MySQL deployment. |
| `FACE_DATA_ENCRYPTION_KEY` | No — auto-managed | Auto-generated and saved on first use by `crypto_utils.py`. **Back up `.env` once this key exists** — losing it permanently locks you out of previously-captured face photos. This is inherent to encryption, not a bug. |
| `COMPANY_*`, office timing, salary defaults | No | Sensible defaults live in `config.py`; these are just convenient overrides, and all are editable later from the admin **Settings** UI. |

---

## 6. DeepFace Model Weights (Offline Machines)

DeepFace downloads its model weight files (`facenet512_weights.h5`, plus
the RetinaFace detector weights) to `~/.deepface/weights` **the first time
it actually runs a face operation** — not at import time.

A customer's machine may have no internet access the moment they first
launch the app (many attendance-kiosk PCs are permanently offline). **If
you skip this step, the shipped `.exe` will fail — or hang trying to
reach the internet — the very first time someone tries to register a
face.**

### 6.1 Pre-download the weights on your build machine

```bash
python app.py
```

Then in the browser:
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
weight files — typically 100–300 MB in total.

### 6.2 Point the build at those weights

`attendance_app.spec` auto-detects `~/.deepface/weights` by default. If
your weights live somewhere else (a shared build server, a different user
profile), set an environment variable before building:

```bash
set DEEPFACE_WEIGHTS_DIR=C:\path\to\.deepface\weights
pyinstaller attendance_app.spec --clean
```

The spec prints one of two messages during the build, so you can confirm
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

**Do not ship a build that printed the warning** unless you've confirmed
the client site has internet access on first use.

---

## 7. Building the Windows `.exe`

### 7.1 Install build-only tools

These are kept out of `requirements.txt` on purpose — customers never
need them:

```bash
pip install -r requirements-packaging.txt
```

### 7.2 Pre-build checklist

Work through this **every time**, not just on the first build:

- [ ] `requirements.txt` is installed cleanly in a venv that has never
      had plain `opencv-python` installed alongside
      `opencv-contrib-python` — they conflict (see the comments in
      `requirements.txt`). If in doubt, rebuild the venv from scratch:
      ```bash
      deactivate
      rmdir /s /q venv
      python -m venv venv
      venv\Scripts\activate
      pip install --upgrade pip
      pip install -r requirements.txt
      pip install -r requirements-packaging.txt
      ```
- [ ] DeepFace weights are populated (Section 6) and either auto-detected
      or pointed to via `DEEPFACE_WEIGHTS_DIR`.
- [ ] You have an `.ico` file ready if you want a custom exe icon (point
      `icon=` in `attendance_app.spec` at it — optional).

### 7.3 Build

```bash
pyinstaller attendance_app.spec --clean
```

`--clean` clears PyInstaller's cache before building. Always use it after
changing `requirements.txt`, the spec file, or your Python version, to
avoid stale-cache packaging bugs.

The build produces:

```
dist/
└── AttendancePayrollSystem/          # onedir build — a folder, not a single file
    ├── AttendancePayrollSystem.exe
    ├── templates/
    ├── static/
    ├── dataset/                      # empty scaffold — see Section 8
    ├── uploads/
    ├── trained_model/
    └── ... (bundled Python runtime, DLLs, deepface_weights/ if bundled)
```

> The spec builds a **onedir** app (a folder containing the exe and its
> dependencies), not a single-file `--onefile` exe. Onedir starts noticeably
> faster — no self-extraction step on every launch — which is why it's the
> recommended mode for a TensorFlow/DeepFace-heavy app like this one. Ship
> the whole `AttendancePayrollSystem` folder, not just the `.exe`.

### 7.4 Verify the build away from your dev machine

Copy the entire `dist/AttendancePayrollSystem/` folder to:
- a **different folder** outside your project (e.g. `C:\Temp\test-install`), or
- ideally, a **clean VM or a second physical machine** with no Python installed.

Most "works on my machine" packaging bugs (a missing DLL, a missing data
file, a stale `sys.path` entry) only show up once you're away from your
own dev environment's installed Python.

Then work through the checklist at the bottom of `attendance_app.spec`
before shipping — it covers first-run DB creation, the setup wizard, the
scheduler, face capture, PDF/email, and AV false-positive checks, in the
order you should test them.

---

## 8. Deploying to a Client Machine

### 8.1 Where to install

Install to a location the exe can **write to without admin elevation**:

| | Location |
|---|---|
| ✅ | `C:\Users\<user>\AppData\Local\AttendancePayrollSystem\` |
| ✅ | `C:\AttendancePayrollSystem\` (if the account has write access) |
| ❌ | `C:\Program Files\AttendancePayrollSystem\` — write-protected by default; SQLite/photo/log writes will fail with permission errors |

### 8.2 Folder layout after first launch

```
AttendancePayrollSystem/
├── AttendancePayrollSystem.exe      # entry point — customer double-clicks this
├── .env                              # created by you before first launch (Section 5)
├── instance/
│   └── attendance.db                 # created automatically on first launch
├── dataset/
│   └── <employee_id>/                # encrypted face photos, created per employee
├── uploads/
│   └── payrolls/<year>/<month>/      # generated payslip PDFs
├── trained_model/
│   └── embeddings_cache.pkl          # face embedding cache, rebuilt as needed
├── templates/, static/               # bundled UI assets — don't edit on client machines
└── (bundled runtime: python3xx.dll, _internal/, deepface_weights/, etc.)
```

`instance/`, `dataset/`, `uploads/`, and `trained_model/` are all created
automatically the first time they're needed. You don't need to pre-create
them — but you **do** need write permission in the install folder for
this to succeed (see 8.1).

### 8.3 Backups

Back these four things up together, as a set, on whatever schedule you
back up the database:

| Item | Why it matters |
|---|---|
| `instance/attendance.db` | All records |
| `.env` | `SECRET_KEY`, `FACE_DATA_ENCRYPTION_KEY`, SMTP creds |
| `dataset/` | Encrypted face photos — unrecoverable without `.env`'s key |
| `uploads/` | Generated payslips |

Losing `.env` without a backup makes every previously-encrypted face
photo, and any custom payslip password, **permanently unrecoverable**.
This is inherent to encryption, not a bug to report.

---

## 9. First Launch & Setup Wizard

What the customer sees:

1. Double-click `AttendancePayrollSystem.exe`.
2. No console window appears (`console=False` in the spec) — the app
   starts silently in the background.
3. The default browser opens automatically to `http://127.0.0.1:5000/`.
4. If no admin account exists yet, the user is routed into the **Setup
   Wizard** (`setup_wizard.py` / `setup_wizard.html`) to:
   - Create the first Admin account (username/password)
   - Enter Company Settings (name, address, logo, contact info)
   - Set initial office timing / working-hours defaults (all editable
     later from Settings)
5. From here on, `/` is the public kiosk attendance screen; Admin/Employee
   login is one click away.

---

## 10. Troubleshooting

### Permission errors (database / uploads / dataset)

**Symptom:** `sqlite3.OperationalError: unable to open database file`, or
face photos / payslips silently fail to save.

**Cause:** the exe is installed somewhere Windows restricts write access
(`Program Files`, a read-only network share) without running as
Administrator.

**Fix:**
- Move the install to `%LOCALAPPDATA%\AttendancePayrollSystem\` (see 8.1), or
- Right-click the exe → Properties → Compatibility → confirm it's not
  forced into a virtualized/read-only mode, or
- As a last resort, "Run as Administrator" — not recommended as a
  permanent fix, since it changes file ownership in ways that can cause a
  *different* permission error for a non-admin user later.

### Camera / webcam access fails

**Symptom:** `Could not open webcam`, blank camera preview, or
`cv2.VideoCapture(0)` returns `isOpened() == False`.

1. **Windows Camera Privacy Settings** — Settings → Privacy & security →
   Camera → make sure "Let desktop apps access your camera" is **On**.
   This is the #1 cause on fresh Windows installs; a packaged exe has no
   camera-permission dialog of its own.
2. **Camera already in use** — close Zoom/Teams/Windows Camera app/any
   other program holding the camera; OpenCV can't share device access.
3. **Wrong device index** — on a machine with multiple cameras (e.g. a
   laptop webcam + a USB kiosk camera), `cv2.VideoCapture(0)` may grab
   the wrong one. Try `cv2.VideoCapture(1)` in a quick test script to
   find the right index, then adjust `FaceCapture.start_capture()` in
   `ai_engine.py` for that install.
4. **Driver issue** — confirm the camera works in the built-in Windows
   Camera app first. If it doesn't work there, it's a driver problem, not
   an application problem.

### Background scheduler doesn't seem to run

**Symptom:** monthly payroll never auto-generates; auto-logout
regularization requests never appear at 23:59.

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
   `importlib.metadata`, not plain imports. `pyinstaller-hooks-contrib`
   plus the explicit `copy_metadata('APScheduler')` in
   `attendance_app.spec` handles this — but if you ever hand-edit the
   spec and remove that line, the scheduler fails silently at init with
   no obvious import error to point at.
3. **The exe wasn't left running.** This app has no background service —
   the scheduler only runs while `AttendancePayrollSystem.exe` is open.
   Closing the browser tab does *not* stop it (see below); closing the
   exe process does. Check Task Manager to confirm the process is still
   running.
4. **The machine was asleep/off at the scheduled time.** APScheduler
   can't run a job while the machine is off or asleep — that's why
   `scheduler_service.py` includes a reconciliation pass on every startup
   that detects and backfills a missed payroll period. Confirm it ran by
   checking for `PAYROLL RECONCILIATION CHECK` in the logs after a restart.

### Antivirus / Windows Defender false positives

**Symptom:** Defender (or another AV) quarantines the exe, or
SmartScreen blocks it with "Windows protected your PC."

This is extremely common for PyInstaller-built executables — especially
ones bundling TensorFlow/OpenCV — and isn't unique to this project.

Mitigations, roughly in order of effectiveness:

1. **Code-sign the exe** with a purchased code-signing certificate —
   close to mandatory for anything you charge money for. An unsigned exe
   from an unknown publisher is exactly the SmartScreen/Defender
   heuristic trigger.
2. Rebuild with `upx=False` in `attendance_app.spec` — UPX-compressed
   executables are disproportionately flagged by heuristic AV engines,
   because malware also commonly uses UPX to evade signature detection.
3. Submit the exe to Microsoft for analysis
   (https://www.microsoft.com/en-us/wdsi/filesubmission) if Defender
   specifically flags it — legitimate PyInstaller apps are regularly
   reviewed and whitelisted this way, though it can take a few days.
4. As a stopgap for one specific customer site, an IT admin can add an
   exclusion for the install folder in Windows Security. Don't rely on
   this as your primary distribution strategy.

### "ModuleNotFoundError" or "DLL load failed" only in the built exe

**Symptom:** `python app.py` works fine, but the packaged exe crashes on
startup, or the first time a specific feature (face capture, PDF export)
is used.

This means a hidden import or data file wasn't bundled:

1. Confirm you built with `pip install pyinstaller-hooks-contrib` present
   — it ships the community hooks for TensorFlow/MediaPipe/PIL that
   vanilla PyInstaller doesn't know about.
2. Check whether the missing module belongs to a package already listed
   in `attendance_app.spec`'s `hiddenimports`/`collect_submodules` calls.
   If it's a new dependency you've since added to `requirements.txt`, it
   needs its own line in the spec.
3. Re-run with `pyinstaller attendance_app.spec --clean` — a stale build
   cache can mask a spec-file fix you already made.
4. Temporarily set `console=True` in the spec **on your own machine
   only** to see the actual traceback. Never ship a build with
   `console=True`.

### The exe process stays running after closing the browser tab

This is expected, not a bug: closing the browser tab does not close the
Flask server or the background scheduler — only closing the
`AttendancePayrollSystem.exe` process (via its window, if provided, or
Task Manager) does. Make sure end users understand this if they expect
"closing the window" to fully quit the app. Consider adding a system tray
icon with an explicit "Quit" action in a future iteration if this causes
confusion.

---

## 11. File Map

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

**Questions or issues during packaging?** Work through the build
verification checklist in Section 7.4 and the troubleshooting guide in
Section 10, in order — the overwhelming majority of PyInstaller packaging
issues for this stack are covered by one of those two sections.