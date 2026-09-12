<div align="center">

# AI Attendance & Payroll System

**A production-grade, face-recognition-driven attendance and payroll platform — packaged as a self-contained Windows desktop application.**

Built with Flask · DeepFace (FaceNet512) · SQLAlchemy · APScheduler · ReportLab

[![Python](https://img.shields.io/badge/Python-3.10.11-blue)]()
[![Flask](https://img.shields.io/badge/Flask-3.x-black)]()
[![Tests](https://img.shields.io/badge/tests-395%20passing-brightgreen)]()
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)]()
[![License](https://img.shields.io/badge/status-internal--production-orange)]()

</div>

---

## Overview

The **AI Attendance & Payroll System** is a full-stack Flask application that automates employee attendance tracking via facial recognition and drives an end-to-end payroll pipeline — from configurable allowances and deductions through AES‑256‑encrypted PDF payslips to automated email delivery.

It ships as a **standalone Windows `.exe`** (built with PyInstaller), so a non-technical end client can double-click a single file and get a fully working local application. There is no Python installation, no `pip install`, and no server configuration required on the client's machine.

This document covers the complete lifecycle of the project: local development, automated testing, environment configuration, building the executable, deploying it to a client, and troubleshooting the most common issues encountered in the field.

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [Architecture & Features](#2-architecture--features)
3. [Prerequisites](#3-prerequisites)
4. [Running the Project Locally](#4-running-the-project-locally)
5. [One-Click Setup with `setup.bat`](#5-one-click-setup-with-setupbat)
6. [Automated Testing](#6-automated-testing)
7. [Environment Variables (`.env`)](#7-environment-variables-env)
8. [DeepFace Model Weights (Offline Machines)](#8-deepface-model-weights-offline-machines)
9. [Building the Windows `.exe`](#9-building-the-windows-exe)
10. [Deploying to a Client Machine](#10-deploying-to-a-client-machine)
11. [First Launch & Setup Wizard](#11-first-launch--setup-wizard)
12. [Troubleshooting](#12-troubleshooting)
13. [Project File Map](#13-project-file-map)
14. [Contribution & Support Workflow](#14-contribution--support-workflow)

---

## 1. Quick Start

For a developer who wants the project running locally right now:

```bash
git clone https://github.com/vaishnavilekawale/AI-Attendance-Payroll-System AI_APS
cd AI_APS

python -m venv venv
venv\Scripts\activate              # Windows
python -m pip install --upgrade pip
pip install -r requirements.txt

python app.py
```

Then open **http://127.0.0.1:5000/** in a browser. If no admin account exists yet, the app automatically routes you into the **Setup Wizard**.

> Prefer a single command instead? See [Section 5](#5-one-click-setup-with-setupbat) — `setup.bat` automates every step above, including the executable build.

---

## 2. Architecture & Features

### 2.1 System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Windows Client Machine                                          │
│                                                                    │
│   AttendancePayrollSystem.exe  (PyInstaller onedir build)         │
│        │                                                          │
│        ├─ launcher.py  → runs app.py's __main__ block             │
│        │                  (starts Flask, opens the browser)       │
│        │                                                          │
│        ├─ Flask app (Werkzeug dev server, 127.0.0.1:5000)         │
│        │     ├─ Admin / Manager / Employee routes & blueprints    │
│        │     ├─ Face recognition engine (DeepFace / OpenCV)       │
│        │     ├─ APScheduler background jobs (payroll, logout)     │
│        │     └─ PDF generation (ReportLab + pikepdf AES-256)      │
│        │                                                          │
│        ├─ SQLite database   → instance/attendance.db              │
│        ├─ Employee face photos (encrypted) → dataset/             │
│        ├─ Payslips / uploads → uploads/                           │
│        ├─ Face embeddings cache → trained_model/                  │
│        └─ .env (secrets, configuration)                           │
│                                                                    │
│   Default OS browser opens automatically to http://127.0.0.1:5000 │
└─────────────────────────────────────────────────────────────────┘
```

In plain terms: this is a standard Flask web app. PyInstaller freezes the Python interpreter, every dependency, and the project source into a single distributable package, and `launcher.py` opens the user's default browser against `localhost` — so the experience *feels* like a native desktop app, even though under the hood it's a local web server.

### 2.2 Roles & Access Control

| Role | Access |
|---|---|
| **Admin** | Full system control — employees, payroll, settings, reports, approvals |
| **Manager** | An employee flagged as a manager; approves manual-attendance and logout-regularization requests within their scope |
| **Employee** | Self-service dashboard — own attendance, payslips, profile, and password management |

### 2.3 Feature Set

**Attendance Engine**
- Public kiosk face-recognition scanning
- Strict cosine-distance matching with a configurable confidence margin (does not guess on look‑alikes)
- Frame-presence locking — a single punch per continuous appearance
- Password-verified manual fallback, gated behind mandatory manager/admin approval
- Full audit trail (`attendance_type`, `approval_status`, `submission_timestamp`)

**Unified Rule Engine**
- One shared engine (`attendance.py::AttendanceManager`) computes Present / Late / Half-Day / Absent for both face-based and manual punches — status logic is never duplicated or hardcoded per entry point

**Payroll Engine**
- Configurable allowances (HRA, DA, Medical, Travel, Special, Other) and deductions (PF, ESIC, TDS, Professional Tax, LOP, Late, Transport)
- Automated monthly generation via APScheduler
- AES‑256 password-protected PDF payslips
- Deliverability-conscious automated email dispatch

**Reporting & Analytics**
- Admin dashboard analytics with department-wise statistics
- PDF export for both admin and employee reports
- A single shared aggregation service, ensuring figures never disagree across screens

**Configuration & Settings**
- Versioned attendance settings — historical attendance is always evaluated against the rules that were in force *on that date*
- Payroll settings and company branding, fully editable from the UI — zero code changes required

**Security**
- Encrypted-at-rest biometric photos (Fernet/AES)
- CSRF protection on all state-changing routes
- Rate-limited login to mitigate brute-force attempts
- Password hashing via Werkzeug's secure primitives

### 2.4 Technology Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.10+, Flask 3.x, SQLAlchemy 2.x |
| AI / Computer Vision | DeepFace (FaceNet512), OpenCV-Contrib, MediaPipe, TensorFlow 2.15 / tf-keras |
| Frontend | Bootstrap 5, vanilla JavaScript (`fetch`), Chart.js |
| Database | SQLite (default) or MySQL (via PyMySQL) |
| PDF Generation | ReportLab (layout), pikepdf (AES-256 password protection) |
| Scheduling | APScheduler (background jobs) |
| Testing | pytest, with a fully stubbed ML/CV layer for fast, deterministic runs |
| Packaging | PyInstaller 6.x + pyinstaller-hooks-contrib |

---

## 3. Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | **3.10.x** (3.10.11 recommended) | TensorFlow 2.15 / MediaPipe wheels on Windows are most reliable on 3.10. Avoid 3.12+. |
| pip | Latest | `python -m pip install --upgrade pip` |
| Git | Any recent version | To clone and manage the repository |
| Windows | 10/11, 64-bit | Build and ship for 64-bit only |
| Webcam | Any USB/integrated | Required for face capture and recognition |
| MySQL Server | 8.x (optional) | Only needed if using MySQL instead of the default SQLite |

> **Why Python 3.10 specifically?** DeepFace, TensorFlow 2.15, `tf-keras`, and `mediapipe==0.10.21` all publish official Windows wheels for 3.9–3.11, and 3.10 is the safest intersection. If a different version is required, confirm every package in `requirements.txt` has a matching wheel **before** investing time in a PyInstaller build.

---

## 4. Running the Project Locally

```bash
git clone https://github.com/vaishnavilekawale/AI-Attendance-Payroll-System AI_APS
cd AI_APS

python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux (development only — ship for Windows)

python -m pip install --upgrade pip
pip install -r requirements.txt
```

Installing `requirements.txt` pulls in TensorFlow, MediaPipe, and OpenCV — expect a multi-gigabyte download and several minutes on first install.

**Verify the install:**

```bash
python -c "import cv2, tensorflow, mediapipe, deepface; print('OK')"
```

If this fails, resolve it here before touching PyInstaller — a plain import error is far easier to diagnose than the same failure inside a packaged build.

**Start the app:**

```bash
set FLASK_ENV=development        # Windows (cmd)
# $env:FLASK_ENV="development"   # Windows (PowerShell)
# export FLASK_ENV=development   # macOS/Linux

python app.py
```

Expected console output:

```
============================================================
🚀 AI Attendance & Payroll System
   Host: 127.0.0.1  |  Port: 5000
   Mode: DEVELOPMENT (debug=True)
============================================================
```

Open `http://127.0.0.1:5000/` — the public kiosk landing page. Admin and Employee login are one click away from there.

On first run, the app automatically creates (next to `app.py`):
- `instance/attendance.db` (SQLite database)
- `dataset/`, `uploads/`, `trained_model/`

If no admin account exists yet, the app routes to the [Setup Wizard](#11-first-launch--setup-wizard).

---

## 5. One-Click Setup with `setup.bat`

For a faster local bootstrap on Windows, `setup.bat` automates the entire environment setup **and** produces a build-ready executable in a single run. It is intended for developers and build engineers, not for end clients (client-side deployment is covered in [Section 10](#10-deploying-to-a-client-machine)).

### 5.1 What it does, step by step

| Step | Action |
|---|---|
| 1 | Prints the active Python version, so a version mismatch is visible immediately |
| 2 | Creates a fresh virtual environment (`venv`) |
| 3 | Activates the virtual environment for the remainder of the script |
| 4 | Upgrades `pip` to the latest version |
| 5 | Installs all runtime dependencies from `requirements.txt` |
| 6 | Installs build-only dependencies from `requirements-packaging.txt` (PyInstaller and its hook contrib package), if present |
| 7 | Copies `.env.example` to `.env` if no `.env` already exists, and warns that its values need to be reviewed |
| 8 | Ensures the required runtime folders exist — `dataset/`, `instance/`, `uploads/`, `trained_model/` — since Git does not track empty directories |
| 9 | Runs `pyinstaller attendance_app.spec --clean` to produce the packaged `.exe` |
| 10 | Confirms completion and points to the output in `dist/` |

### 5.2 Usage

```bat
setup.bat
```

Run it from the project root, in a standard Windows Command Prompt (not PowerShell, unless PowerShell is configured to run `.bat` scripts). No arguments are required.

### 5.3 When to use it vs. manual steps

| Scenario | Recommended approach |
|---|---|
| First-time setup on a fresh clone | `setup.bat` — fastest path from zero to a built `.exe` |
| Iterating on code, running the dev server repeatedly | Manual steps in [Section 4](#4-running-the-project-locally) — skips the PyInstaller build on every run |
| CI or automated testing | Manual `pip install` + `pytest`, not `setup.bat` (it always attempts a full executable build) |
| Rebuilding after a dependency change | `setup.bat`, or `pyinstaller attendance_app.spec --clean` alone if the venv is already correct |

### 5.4 Notes and caveats

- `setup.bat` will happily run every time it's invoked — it doesn't skip steps if they were already completed, so re-running it after a small code change still triggers a full dependency install and PyInstaller build. For quick iteration, prefer running `python app.py` directly against an already-set-up venv.
- The `.env` copy step only fires if `.env.example` exists in the project root; if you're relying on it, make sure `.env.example` is committed and kept up to date.
- Because the script ends with a full PyInstaller build, first-time DeepFace weight downloads should be handled **before** running `setup.bat` for a client-facing build — see [Section 8](#8-deepface-model-weights-offline-machines).

---

## 6. Automated Testing

The project ships with a substantial **pytest** suite covering authentication, attendance rules, payroll math, PDF generation, email delivery, scheduling, rate limiting, and the Flask routes themselves.

### 6.1 Test suite at a glance

| Test module | Focus area |
|---|---|
| `test_app_routes.py` | Flask route behavior and view-level integration |
| `test_admin_reports_service.py` | Admin reporting/aggregation service |
| `test_attendance.py` / `test_attendance_calculator.py` / `test_attendance_stats.py` | Core attendance rule engine and statistics |
| `test_ai_engine_face_matching.py` / `test_ai_engine_dataset_mapping.py` | Face-matching logic and dataset/embedding mapping (ML layer stubbed — see below) |
| `test_approval_service.py` | Manager/admin approval workflow |
| `test_auth.py` / `test_auth_decorators.py` / `test_auth_helpers.py` | Login, session handling, and access-control decorators |
| `test_config_and_setup.py` | Configuration loading and the first-run Setup Wizard |
| `test_crypto_utils.py` | At-rest encryption for biometric data and payslip passwords |
| `test_email_service.py` | SMTP email delivery paths |
| `test_employees.py` | Employee CRUD and validation |
| `test_file_helpers.py` | File-handling utilities |
| `test_models.py` | ORM model definitions and relationships |
| `test_payroll_calculations.py` | Payroll math — allowances, deductions, net pay |
| `test_pdf_generator.py` | Payslip/report PDF generation and AES-256 protection |
| `test_rate_limiting.py` | Login rate-limiting behavior |
| `test_scheduler_service.py` | APScheduler jobs — auto-logout, monthly payroll, reconciliation |

Across these modules the suite currently exercises **roughly 395 individual test cases**, giving meaningful coverage of every core service layer in the application.

### 6.2 Why tests run fast without a GPU or TensorFlow install

`conftest.py` pre-registers lightweight stub modules for the heavy ML/CV stack (`cv2`, `mediapipe`, `deepface`, TensorFlow) in `sys.modules` **before** anything in the app is imported. This means:

- The test suite never needs the multi-gigabyte TensorFlow/DeepFace/OpenCV/MediaPipe stack installed to validate auth, payroll math, rate limiting, or any other logic that doesn't touch face recognition directly.
- Tests are fast and fully deterministic — no camera, no GPU, no model downloads required.
- This is distinct from the app's own `try/except ImportError` guards around these libraries (which only handle a *missing* library gracefully); the test stubs force the fast path unconditionally, regardless of what is actually installed in the environment running the tests.

### 6.3 Running the test suite

```bash
# From the project root, with the venv activated
pip install pytest

pytest
```

Useful variations:

```bash
pytest -v                              # verbose per-test output
pytest tests/test_payroll_calculations.py    # run a single module
pytest -k "attendance"                 # run tests matching a keyword
pytest --maxfail=1 -x                  # stop on first failure
```

### 6.4 Adding coverage reporting (optional)

```bash
pip install pytest-cov
pytest --cov=. --cov-report=term-missing
```

### 6.5 When to run the suite

- **Before every commit** that touches `attendance.py`, `payroll.py`, `ai_engine.py`, `pdf_generator.py`, `scheduler_service.py`, or any file under `services/`.
- **Before every PyInstaller build** intended for a client — a green test suite is not a substitute for the manual verification checklist in [Section 9.4](#94-verify-the-build-away-from-your-dev-machine), but it catches regressions far earlier and far more cheaply.
- **After upgrading any dependency** in `requirements.txt` (Flask, SQLAlchemy, DeepFace, TensorFlow, etc.) — version bumps in this stack have historically been a common source of subtle breakage.

---

## 7. Environment Variables (`.env`)

Create a `.env` file **next to `app.py`** (development) or **next to the `.exe`** (packaged build). Never commit this file, and never ship a developer's own `.env` to a customer — each install should have its own.

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
# (e.g. restoring a previous install), leave it exactly as-is.
# FACE_DATA_ENCRYPTION_KEY=<auto-generated — back this up>
```

### 7.1 Which keys actually matter

| Key | Required? | What happens if it's missing |
|---|---|---|
| `SECRET_KEY` | **Yes, effectively mandatory** | Falls back to a hardcoded default (`'your-secret-key-change-in-production'`). **Never ship a customer install without setting this explicitly** — an unset `SECRET_KEY` means every install shares the same, publicly known key, which breaks session/CSRF-token integrity. Generate a unique one per install: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `MAIL_*` | Only if email is used | Payslip emails, password resets, and approval notifications silently fail/log errors without valid SMTP credentials. Everything else still runs fine. |
| `DATABASE_URL` | No | Defaults to a local SQLite file — the right choice for a single-site desktop install. Only set this for a MySQL deployment. |
| `FACE_DATA_ENCRYPTION_KEY` | No — auto-managed | Auto-generated and saved on first use by `crypto_utils.py`. **Back up `.env` once this key exists** — losing it permanently locks you out of previously-captured face photos. This is inherent to encryption, not a bug. |
| `COMPANY_*`, office timing, salary defaults | No | Sensible defaults live in `config.py`; these are just convenient overrides, and all are editable later from the admin **Settings** UI. |

---

## 8. DeepFace Model Weights (Offline Machines)

DeepFace downloads its model weight files (`facenet512_weights.h5`, plus the RetinaFace detector weights) to `~/.deepface/weights` **the first time it actually runs a face operation** — not at import time.

A customer's machine may have no internet access the moment they first launch the app (many attendance-kiosk PCs are permanently offline). **Skipping this step means the shipped `.exe` will fail — or hang trying to reach the internet — the very first time someone tries to register a face.**

### 8.1 Pre-download the weights on your build machine

```bash
python app.py
```

Then in the browser:
1. Log in as Admin (complete the Setup Wizard first if this is a fresh dev database).
2. Add a test employee and go through **Face Registration** — capture at least one photo. This forces DeepFace to download and cache every weight file it needs (FaceNet512 + RetinaFace detector).
3. Stop the app (`Ctrl+C`).
4. Confirm the weights landed on disk:

```bash
dir %USERPROFILE%\.deepface\weights          # Windows
# ls ~/.deepface/weights                     # macOS/Linux
```

Expect to see files like `facenet512_weights.h5` and RetinaFace-related weight files — typically 100–300 MB in total.

### 8.2 Point the build at those weights

`attendance_app.spec` auto-detects `~/.deepface/weights` by default. If the weights live elsewhere (a shared build server, a different user profile), set an environment variable before building:

```bash
set DEEPFACE_WEIGHTS_DIR=C:\path\to\.deepface\weights
pyinstaller attendance_app.spec --clean
```

The spec prints one of two messages during the build, confirming this worked *before* the exe reaches a customer:

```
[spec] Bundling DeepFace weights from: C:\Users\you\.deepface\weights
```
or, if not found:
```
[spec] WARNING: DeepFace weights folder not found or empty at '...'.
Building WITHOUT bundled weights - the shipped exe will try to download
them from the internet on the customer's machine...
```

**Do not ship a build that prints the warning** unless the client site is confirmed to have internet access on first use.

---

## 9. Building the Windows `.exe`

### 9.1 Install build-only tools

Kept out of `requirements.txt` intentionally — customers never need them:

```bash
pip install -r requirements-packaging.txt
```

### 9.2 Pre-build checklist

Work through this **every time**, not only on the first build:

- [ ] `requirements.txt` is installed cleanly in a venv that has never had plain `opencv-python` installed alongside `opencv-contrib-python` — they conflict (see the comments in `requirements.txt`). If in doubt, rebuild the venv from scratch:
      ```bash
      deactivate
      rmdir /s /q venv
      python -m venv venv
      venv\Scripts\activate
      pip install --upgrade pip
      pip install -r requirements.txt
      pip install -r requirements-packaging.txt
      ```
- [ ] DeepFace weights are populated ([Section 8](#8-deepface-model-weights-offline-machines)) and either auto-detected or pointed to via `DEEPFACE_WEIGHTS_DIR`.
- [ ] The full pytest suite passes ([Section 6](#6-automated-testing)).
- [ ] An `.ico` file is ready if a custom exe icon is wanted (point `icon=` in `attendance_app.spec` at it — optional).

### 9.3 Build

```bash
pyinstaller attendance_app.spec --clean
```

`--clean` clears PyInstaller's cache before building. Always use it after changing `requirements.txt`, the spec file, or the Python version, to avoid stale-cache packaging bugs.

The build produces:

```
dist/
└── AttendancePayrollSystem/          # onedir build — a folder, not a single file
    ├── AttendancePayrollSystem.exe
    ├── templates/
    ├── static/
    ├── dataset/                      # empty scaffold — see Section 10
    ├── uploads/
    ├── trained_model/
    └── ... (bundled Python runtime, DLLs, deepface_weights/ if bundled)
```

> The spec builds a **onedir** app (a folder containing the exe and its dependencies), not a single-file `--onefile` exe. Onedir starts noticeably faster — no self-extraction step on every launch — which is why it's the recommended mode for a TensorFlow/DeepFace-heavy app like this one. Ship the whole `AttendancePayrollSystem` folder, not just the `.exe`.

### 9.4 Verify the build away from your dev machine

Copy the entire `dist/AttendancePayrollSystem/` folder to:
- a **different folder** outside the project (e.g. `C:\Temp\test-install`), or
- ideally, a **clean VM or a second physical machine** with no Python installed.

Most "works on my machine" packaging bugs (a missing DLL, a missing data file, a stale `sys.path` entry) only surface once you're away from your own dev environment's installed Python.

Then work through the checklist at the bottom of `attendance_app.spec` before shipping — it covers first-run database creation, the Setup Wizard, the scheduler, face capture, PDF/email, and antivirus false-positive checks, in the order they should be tested.

---

## 10. Deploying to a Client Machine

### 10.1 Where to install

Install to a location the exe can **write to without admin elevation**:

| | Location |
|---|---|
| ✅ | `C:\Users\<user>\AppData\Local\AttendancePayrollSystem\` |
| ✅ | `C:\AttendancePayrollSystem\` (if the account has write access) |
| ❌ | `C:\Program Files\AttendancePayrollSystem\` — write-protected by default; SQLite/photo/log writes will fail with permission errors |

### 10.2 Folder layout after first launch

```
AttendancePayrollSystem/
├── AttendancePayrollSystem.exe      # entry point — customer double-clicks this
├── .env                              # created before first launch (Section 7)
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

`instance/`, `dataset/`, `uploads/`, and `trained_model/` are all created automatically the first time they're needed — pre-creating them is not required, but write permission in the install folder **is** required (see 10.1).

### 10.3 Backups

Back these four items up together, as a set, on whatever schedule the database is backed up:

| Item | Why it matters |
|---|---|
| `instance/attendance.db` | All records |
| `.env` | `SECRET_KEY`, `FACE_DATA_ENCRYPTION_KEY`, SMTP credentials |
| `dataset/` | Encrypted face photos — unrecoverable without `.env`'s key |
| `uploads/` | Generated payslips |

Losing `.env` without a backup makes every previously encrypted face photo, and any custom payslip password, **permanently unrecoverable**. This is inherent to encryption, not a bug to report.

---

## 11. First Launch & Setup Wizard

What the customer sees:

1. Double-click `AttendancePayrollSystem.exe`.
2. No console window appears (`console=False` in the spec) — the app starts silently in the background.
3. The default browser opens automatically to `http://127.0.0.1:5000/`.
4. If no admin account exists yet, the user is routed into the **Setup Wizard** (`setup_wizard.py` / `setup_wizard.html`) to:
   - Create the first Admin account (username/password)
   - Enter Company Settings (name, address, logo, contact info)
   - Set initial office timing / working-hours defaults (all editable later from Settings)
5. From here on, `/` is the public kiosk attendance screen; Admin/Employee login is one click away.

---

## 12. Troubleshooting

### Permission errors (database / uploads / dataset)

**Symptom:** `sqlite3.OperationalError: unable to open database file`, or face photos / payslips silently fail to save.

**Cause:** the exe is installed somewhere Windows restricts write access (`Program Files`, a read-only network share) without running as Administrator.

**Fix:**
- Move the install to `%LOCALAPPDATA%\AttendancePayrollSystem\` (see 10.1), or
- Right-click the exe → Properties → Compatibility → confirm it's not forced into a virtualized/read-only mode, or
- As a last resort, "Run as Administrator" — not recommended as a permanent fix, since it changes file ownership in ways that can cause a *different* permission error for a non-admin user later.

### Camera / webcam access fails

**Symptom:** `Could not open webcam`, blank camera preview, or `cv2.VideoCapture(0)` returns `isOpened() == False`.

1. **Windows Camera Privacy Settings** — Settings → Privacy & security → Camera → confirm "Let desktop apps access your camera" is **On**. This is the #1 cause on fresh Windows installs; a packaged exe has no camera-permission dialog of its own.
2. **Camera already in use** — close Zoom/Teams/Windows Camera app/any other program holding the camera; OpenCV cannot share device access.
3. **Wrong device index** — on a machine with multiple cameras (e.g. a laptop webcam + a USB kiosk camera), `cv2.VideoCapture(0)` may grab the wrong one. Try `cv2.VideoCapture(1)` in a quick test script to find the right index, then adjust `FaceCapture.start_capture()` in `ai_engine.py` for that install.
4. **Driver issue** — confirm the camera works in the built-in Windows Camera app first. If it doesn't work there, it's a driver problem, not an application problem.

### Background scheduler doesn't seem to run

**Symptom:** monthly payroll never auto-generates; auto-logout regularization requests never appear at 23:59.

1. Check the log output around startup for:
   ```
   Payroll scheduler started
   DAILY APPROVAL SCHEDULER REGISTERED - 23:59
   Payroll Next Run: ...
   ```
   If these lines are missing, the scheduler failed to start — look for `Failed to start scheduler:` earlier in the log for the actual cause.
2. **Most common packaged-build cause: missing APScheduler entry-point metadata.** APScheduler discovers its jobstore/trigger plugins via `importlib.metadata`, not plain imports. `pyinstaller-hooks-contrib` plus the explicit `copy_metadata('APScheduler')` in `attendance_app.spec` handles this — but hand-editing the spec and removing that line makes the scheduler fail silently at init with no obvious import error to point at.
3. **The exe wasn't left running.** This app has no background service — the scheduler only runs while `AttendancePayrollSystem.exe` is open. Closing the browser tab does *not* stop it (see below); closing the exe process does. Check Task Manager to confirm the process is still running.
4. **The machine was asleep/off at the scheduled time.** APScheduler can't run a job while the machine is off or asleep — that's why `scheduler_service.py` includes a reconciliation pass on every startup that detects and backfills a missed payroll period. Confirm it ran by checking for `PAYROLL RECONCILIATION CHECK` in the logs after a restart.

### Antivirus / Windows Defender false positives

**Symptom:** Defender (or another AV) quarantines the exe, or SmartScreen blocks it with "Windows protected your PC."

This is extremely common for PyInstaller-built executables — especially ones bundling TensorFlow/OpenCV — and isn't unique to this project.

Mitigations, roughly in order of effectiveness:

1. **Code-sign the exe** with a purchased code-signing certificate — close to mandatory for anything charged for commercially. An unsigned exe from an unknown publisher is exactly the SmartScreen/Defender heuristic trigger.
2. Rebuild with `upx=False` in `attendance_app.spec` — UPX-compressed executables are disproportionately flagged by heuristic AV engines, because malware also commonly uses UPX to evade signature detection.
3. Submit the exe to Microsoft for analysis (https://www.microsoft.com/en-us/wdsi/filesubmission) if Defender specifically flags it — legitimate PyInstaller apps are regularly reviewed and whitelisted this way, though it can take a few days.
4. As a stopgap for one specific customer site, an IT admin can add an exclusion for the install folder in Windows Security. Don't rely on this as the primary distribution strategy.

### "ModuleNotFoundError" or "DLL load failed" only in the built exe

**Symptom:** `python app.py` works fine, but the packaged exe crashes on startup, or the first time a specific feature (face capture, PDF export) is used.

This means a hidden import or data file wasn't bundled:

1. Confirm the build was done with `pip install pyinstaller-hooks-contrib` present — it ships the community hooks for TensorFlow/MediaPipe/PIL that vanilla PyInstaller doesn't know about.
2. Check whether the missing module belongs to a package already listed in `attendance_app.spec`'s `hiddenimports`/`collect_submodules` calls. If it's a new dependency added since to `requirements.txt`, it needs its own line in the spec.
3. Re-run with `pyinstaller attendance_app.spec --clean` — a stale build cache can mask a spec-file fix already made.
4. Temporarily set `console=True` in the spec **on your own machine only** to see the actual traceback. Never ship a build with `console=True`.

### The exe process stays running after closing the browser tab

This is expected, not a bug: closing the browser tab does not close the Flask server or the background scheduler — only closing the `AttendancePayrollSystem.exe` process (via its window, if provided, or Task Manager) does. Make sure end users understand this if they expect "closing the window" to fully quit the app. Consider adding a system tray icon with an explicit "Quit" action in a future iteration if this causes confusion.

---

## 13. Project File Map

| File / Folder | Purpose |
|---|---|
| `app.py` | Main Flask app, routes, startup block |
| `launcher.py` | PyInstaller entry point — wraps `app.py` for frozen builds |
| `config.py` | Environment-aware configuration, frozen-safe `BASE_DIR` |
| `database.py` | SQLAlchemy/Flask-Migrate init, lightweight ad-hoc migrations |
| `models.py` | All ORM models |
| `ai_engine.py` | Face detection/recognition engine, embedding cache, presence tracker |
| `face_recognition_singleton.py` | Shared singleton access to the face recognition engine |
| `crypto_utils.py` | At-rest encryption for face photos & custom payslip passwords |
| `attendance.py` | Core attendance status rule engine |
| `payroll.py` | Payroll calculation engine |
| `pdf_generator.py` | Payslip/report PDF generation + AES-256 password protection |
| `email_service.py` | SMTP email delivery for payslips, resets, notifications |
| `scheduler_service.py` | APScheduler jobs: auto-logout, monthly payroll, reconciliation |
| `setup_wizard.py` | First-run admin/company setup flow |
| `auth_decorators.py` / `auth_helpers.py` | Access-control decorators and authentication helpers |
| `employees.py` | Employee management logic |
| `file_helpers.py` | Shared file-handling utilities |
| `extensions.py` | Flask extension initialization |
| `services/` | Domain services — admin reporting, approvals, attendance calculation & stats |
| `templates/` | All Jinja2 HTML views (login, dashboards, payroll, reports, approvals, settings, etc.) |
| `static/` | CSS, JavaScript, and image assets |
| `tests/` | Full pytest suite (~395 tests) covering every service layer — see [Section 6](#6-automated-testing) |
| `conftest.py` | Shared pytest fixtures and ML/CV stubbing for fast, deterministic tests |
| `dataset/` | Encrypted employee face photos (runtime-generated) |
| `uploads/` | Generated payslips and other uploaded artifacts (runtime-generated) |
| `trained_model/` | Face embedding cache (runtime-generated) |
| `instance/` | SQLite database (runtime-generated) |
| `attendance_app.spec` | PyInstaller build specification |
| `setup.bat` | One-click environment setup + executable build — see [Section 5](#5-one-click-setup-with-setupbat) |
| `setup_wizard.py` / `setup_wizard.html` | First-run configuration flow |
| `requirements.txt` | Runtime dependencies |
| `requirements-packaging.txt` | Build-only dependencies (PyInstaller, hooks) |

---

## 14. Contribution & Support Workflow

1. **Before writing code:** confirm `pytest` passes on a clean checkout, so any pre-existing failures aren't mistaken for regressions introduced later.
2. **While developing:** run the relevant test module(s) frequently (`pytest tests/test_<area>.py -v`) rather than waiting until the end.
3. **Before opening a PR / handing off work:** run the full suite (`pytest`), and if the change touches packaging, walk the [pre-build checklist](#92-pre-build-checklist) and produce a test build per [Section 9](#9-building-the-windows-exe).
4. **Reporting an issue:** include the exact console output, whether it reproduces in `python app.py` (dev) or only in the packaged `.exe`, and the relevant section of this README already checked, so troubleshooting doesn't retread the same ground.
5. **Packaging or shipping a customer build:** work through the checklist in [Section 9.4](#94-verify-the-build-away-from-your-dev-machine) and the troubleshooting guide in [Section 12](#12-troubleshooting), in order — the overwhelming majority of PyInstaller packaging issues for this stack are covered by one of those two sections.

---

<div align="center">

**AI Attendance & Payroll System** — internal engineering documentation.

</div>