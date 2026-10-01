<div align="center">

# 🧠 AI Attendance & Payroll System

**Face-recognition attendance, automated payroll, encrypted payslips and signed licensing — shipped as a one-click Windows desktop app.**

[![Python](https://img.shields.io/badge/Python-3.10.x-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/release/python-31011/)
[![Flask](https://img.shields.io/badge/Flask-3.0-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![TensorFlow](https://img.shields.io/badge/TensorFlow-2.15-FF6F00?logo=tensorflow&logoColor=white)](https://www.tensorflow.org/)
[![DeepFace](https://img.shields.io/badge/DeepFace-FaceNet512-8A2BE2)](https://github.com/serengil/deepface)
[![Security](https://img.shields.io/badge/Security-AES--256%20%7C%20Ed25519-success)]()
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11%20(64--bit)-0078D6?logo=windows&logoColor=white)]()
[![Tests](https://img.shields.io/badge/tests-pytest-brightgreen?logo=pytest&logoColor=white)]()
[![Status](https://img.shields.io/badge/status-internal--production-orange)]()

[Features](#-features) •
[Quick Start](#-quick-start) •
[Installation](#-installation--setup-a-to-z) •
[Build the .exe](#-building--packaging-for-developers) •
[Licensing](#-license--backup-management) •
[Troubleshooting](#-troubleshooting--faqs)

</div>

---

## 📖 Table of Contents

1. [Project Overview](#-project-overview)
2. [Features](#-features)
3. [Tech Stack](#-tech-stack)
4. [Architecture](#-architecture)
5. [Prerequisites](#-prerequisites)
6. [Quick Start](#-quick-start)
7. [Installation & Setup (A to Z)](#-installation--setup-a-to-z)
8. [Running the Application](#-running-the-application)
9. [Automated Testing](#-automated-testing)
10. [Building & Packaging (For Developers)](#-building--packaging-for-developers)
11. [License & Backup Management](#-license--backup-management)
12. [Deploying to a Client Machine](#-deploying-to-a-client-machine)
13. [Troubleshooting & FAQs](#-troubleshooting--faqs)
14. [Project Structure](#-project-structure)
15. [Contributing & Support](#-contributing--support)

---

## 🎯 Project Overview

The **AI Attendance & Payroll System** is a full-stack Flask application that:

- ✅ marks employee attendance automatically using **face recognition** at a public kiosk,
- 💰 runs a complete **payroll pipeline** (allowances, deductions, net pay),
- 📄 generates **AES-256 password-protected PDF payslips** and emails them automatically,
- 🔑 protects the product with **Ed25519-signed, machine-bound licenses**, and
- 💾 protects customer data with **automated weekly backups**.

It ships as a **Windows installer / `.exe`** built with PyInstaller and Inno Setup, so a non-technical client just installs and double-clicks — **no Python, no `pip`, no server setup** on their machine.

> 💡 Under the hood it is a local Flask web app served on `127.0.0.1:5000`. The launcher opens the user's default browser automatically, so it *feels* like a native desktop app.

---

## ✨ Features

### 🎥 Attendance
| Feature | Details |
|---|---|
| **Face-recognition kiosk** | Public scanning page, DeepFace (FaceNet512) with strict cosine-distance matching |
| **No guessing on look-alikes** | Configurable confidence margin — ambiguous faces are rejected |
| **Single punch per appearance** | Frame-presence locking prevents duplicate punches |
| **Manual fallback with approval** | Password-verified manual attendance, gated behind manager/admin approval |
| **Full audit trail** | `attendance_type`, `approval_status`, `submission_timestamp` on every record |
| **One shared rule engine** | Present / Late / Half-Day / Absent computed in one place for face *and* manual punches |
| **Versioned settings** | Past attendance is always evaluated against the rules in force *on that date* |

### 💰 Payroll & Reports
- Configurable **allowances** (HRA, DA, Medical, Travel, Special, Other) and **deductions** (PF, ESIC, TDS, Professional Tax, LOP, Late, Transport)
- **Automated monthly payroll** via APScheduler, with a startup **reconciliation pass** that backfills any period missed while the PC was off
- **AES-256 password-protected PDF payslips** (ReportLab + pikepdf) with automated email delivery
- Admin dashboard analytics, department-wise stats, and PDF export for admin and employee reports
- A single shared aggregation service, so numbers never disagree between screens

### 🔐 Security
- **Face photos encrypted at rest** (Fernet / AES) — opt-in biometric consent
- **AES-256** password-protected payslips
- **Ed25519 licensing** — customers only hold the *public* key, so they cannot forge a license
- **CSRF protection** on all state-changing routes
- **Rate-limited login** against brute-force attempts
- Werkzeug password hashing

### 💾 Operations
- **Automated weekly backups** every Sunday at 02:00 (last 4 kept)
- One-click **manual backup download** from Admin → Settings
- **Role-based access:** Admin, Manager, Employee
- **First-run Setup Wizard** — no manual database or admin seeding
- **Fully offline-capable UI** — Bootstrap, icons, Chart.js and fonts are bundled locally

### 👥 Roles

| Role | Access |
|---|---|
| **Admin** | Full control — employees, payroll, settings, licensing, backups, reports, approvals |
| **Manager** | An employee flagged as manager; approves manual-attendance and logout-regularization requests in their scope |
| **Employee** | Self-service — own attendance, payslips, profile, password |

---

## 🛠 Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.10, Flask 3.0, SQLAlchemy 2.0, Flask-Migrate / Alembic, Flask-WTF, Flask-Limiter |
| **AI / Computer Vision** | DeepFace (FaceNet512), TensorFlow 2.15 + tf-keras, OpenCV-Contrib, MediaPipe |
| **Frontend** | Bootstrap 5, Bootstrap Icons, vanilla JavaScript (`fetch`), Chart.js |
| **Database** | SQLite (default) or MySQL via PyMySQL |
| **PDF & Security** | ReportLab, pikepdf (AES-256), `cryptography` (Fernet, Ed25519) |
| **Scheduling** | APScheduler (payroll, auto-logout, backups) |
| **Production server** | Waitress (used automatically outside development mode) |
| **Testing** | pytest, with a fully stubbed ML/CV layer |
| **Packaging** | PyInstaller 6.x + Inno Setup 6 |

---

## 🏗 Architecture

```text
┌───────────────────────────────────────────────────────────────┐
│  Windows Client Machine                                        │
│                                                                │
│  AttendancePayrollSystem.exe   (PyInstaller "onedir" build)    │
│     │                                                          │
│     ├─ launcher.py ──► runs app.py's __main__ block            │
│     │                    ├─ 1. License check (Ed25519 / trial) │
│     │                    ├─ 2. Load face model + embeddings    │
│     │                    ├─ 3. Open browser automatically      │
│     │                    └─ 4. Serve on 127.0.0.1:5000         │
│     │                                                          │
│     ├─ Flask blueprints: setup · auth · employees · attendance │
│     │                    payroll · reports · approvals · settings
│     ├─ APScheduler: monthly payroll · 23:59 auto-logout ·      │
│     │               Sunday 02:00 backup                        │
│     │                                                          │
│     ├─ instance/attendance.db      (SQLite)                    │
│     ├─ dataset/                    (encrypted face photos)     │
│     ├─ uploads/                    (payslips, backups/)        │
│     ├─ trained_model/              (face-embedding cache)      │
│     ├─ license.lic                 (signed license token)      │
│     └─ .env                        (secrets & configuration)   │
└───────────────────────────────────────────────────────────────┘
```

---

## 📋 Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| 🐍 **Python** | **3.10.x** (3.10.11 recommended) | TensorFlow 2.15 / MediaPipe wheels are most reliable on 3.10. **Avoid 3.12+.** |
| 🔧 **Git** | Any recent version | To clone the repository |
| 📷 **Webcam** | Any USB / built-in | Needed for face registration and recognition |
| 🪟 **Windows** | 10 / 11, 64-bit | Build and ship for 64-bit only |
| 💽 **Disk / RAM** | ~5 GB free, 8 GB RAM recommended | TensorFlow + DeepFace are large |
| 🌐 **Internet** | Needed once | To install packages and download face-model weights |
| 🗄 **MySQL 8.x** | *Optional* | Only if you don't want the default SQLite |

**Only for building the installer** (developers/build machine):

| Tool | Purpose |
|---|---|
| **PyInstaller** (installed via `requirements-packaging.txt`) | Freezes the app into an `.exe` |
| **[Inno Setup 6](https://jrsoftware.org/isinfo.php)** | Compiles the final `Setup.exe` installer |

> ⚠️ **Why Python 3.10?** DeepFace, TensorFlow 2.15, `tf-keras` and `mediapipe==0.10.21` all publish official Windows wheels for 3.9–3.11, and 3.10 is the safest overlap. Also, `numpy` is pinned to `1.26.4` (below 2.0) on purpose — NumPy 2.x breaks TensorFlow/MediaPipe.

---

## ⚡ Quick Start

```bash
git clone https://github.com/vaishnavilekawale/AI-Attendance-Payroll-System AI_APS
cd AI_APS

python -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt

copy .env.example .env
set FLASK_ENV=development
python app.py
```

Open **http://127.0.0.1:5000** — on first run you'll be taken to the **Setup Wizard**. 🎉

> ⏱ First install downloads several GB (TensorFlow, OpenCV, MediaPipe) — expect several minutes.

---

## 🧭 Installation & Setup (A to Z)

### Step 1️⃣ — Install the prerequisites

1. Install **Python 3.10.11** from [python.org](https://www.python.org/downloads/release/python-31011/).
   ✔ Tick **"Add Python to PATH"** in the installer.
2. Install **Git** from [git-scm.com](https://git-scm.com/downloads).
3. Confirm both work — open **Command Prompt** and run:

```bash
python --version    # should print Python 3.10.x
git --version
```

### Step 2️⃣ — Clone the repository

```bash
git clone https://github.com/vaishnavilekawale/AI-Attendance-Payroll-System AI_APS
cd AI_APS
```

### Step 3️⃣ — Create and activate a virtual environment

```bash
python -m venv venv
venv\Scripts\activate
```

Your prompt should now start with `(venv)`.

> macOS/Linux is fine for development only: `source venv/bin/activate`. Production builds are Windows-only.

### Step 4️⃣ — Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Verify the heavy ML stack imported correctly **before** doing anything else:

```bash
python -c "import cv2, tensorflow, mediapipe, deepface; print('OK')"
```

> ⚠️ Use a **clean** venv. Never have plain `opencv-python` installed next to `opencv-contrib-python` — both provide `cv2` and will conflict. If in doubt:
> `pip uninstall -y opencv-python opencv-contrib-python` then `pip install -r requirements.txt`.

### Step 5️⃣ — Configure the environment file (`.env`)

```bash
copy .env.example .env
```

The `.env` file must sit **next to `app.py`** (development) or **next to the `.exe`** (packaged build). Open it and review:

```ini
# ── Flask core ─────────────────────────────────────────────
SECRET_KEY=                   # leave blank on a NEW install — auto-generated on first run
FLASK_ENV=production          # use "development" only on your own dev machine

# ── Database ───────────────────────────────────────────────
# Leave commented for SQLite (instance/attendance.db)
# DATABASE_URL=mysql+pymysql://user:password@localhost/attendance_db

# ── Email (payslips, password resets, notifications) ───────
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=your-company@gmail.com
MAIL_PASSWORD=                # SMTP APP PASSWORD, not your normal password
MAIL_DEFAULT_SENDER=your-company@gmail.com

# ── Company & office defaults (also editable later in Settings) ──
COMPANY_NAME=Your Company Pvt Ltd
OFFICE_START_TIME=09:00
OFFICE_END_TIME=18:00
GRACE_PERIOD_MINUTES=15

# ── Face recognition ───────────────────────────────────────
FACE_RECOGNITION_TOLERANCE=0.6
MIN_FACE_IMAGES_REQUIRED=20

# ── Biometric encryption key ───────────────────────────────
# Do NOT set on a fresh install — generated automatically and written here.
# BACK UP .env once it exists. Losing this key = face photos unrecoverable.
# FACE_DATA_ENCRYPTION_KEY=
```

| Key | Required? | What to know |
|---|---|---|
| `SECRET_KEY` | Auto | Blank → a secure random key is generated and saved on first run. When **restoring** an install, reuse its original value. |
| `FLASK_ENV` | Yes | `development` enables Flask's debugger (unsafe for customers). `production` uses Waitress. |
| `MAIL_*` | Only for email | Without valid SMTP credentials payslip/reset emails fail and log an error; everything else still works. |
| `DATABASE_URL` | No | Default is SQLite — right for a single-site install. |
| `FACE_DATA_ENCRYPTION_KEY` | Auto | Managed by `crypto_utils.py`. **Back it up.** |
| `LICENSE_TOKEN` / `LICENSE_PUBLIC_KEY_HEX` | No | Optional overrides for the licensing system (see [License Management](#-license--backup-management)). |

> 🔒 **Never commit `.env`** and never copy your own `.env` into a customer deliverable. Every install gets its own.

### Step 6️⃣ — Download the offline UI assets (one time)

```bash
pip install requests
python scripts/download_vendor_assets.py
```

This saves Bootstrap, Bootstrap Icons, Chart.js and fonts into `static/vendor/` so the UI renders without internet. Required before building the installer; recommended for development too.

### Step 7️⃣ — Download the face-model weights (one time)

DeepFace downloads FaceNet512 + RetinaFace weights to `%USERPROFILE%\.deepface\weights` the **first time it runs a face operation** — not at install time. Trigger it once on your machine:

1. Run `python app.py` (see below) and finish the Setup Wizard.
2. Add a test employee and capture **at least one photo** in Face Registration.
3. Stop the app (`Ctrl+C`) and confirm the files exist:

```bash
dir %USERPROFILE%\.deepface\weights
```

You should see `facenet512_weights.h5` and RetinaFace weights (~100–300 MB total). The PyInstaller build bundles these so client PCs work **offline**.

### Step 8️⃣ — (Optional) Windows one-click helper

`setup.bat` automates steps 3–5 **and** runs a full PyInstaller build:

```bat
setup.bat
```

It creates the venv, installs runtime + packaging dependencies, copies `.env.example` → `.env`, creates the runtime folders, and runs `pyinstaller attendance_app.spec --clean`. Because it always builds the `.exe`, use the manual steps above when you just want to iterate on code.

---

## ▶ Running the Application

### 🧪 Development mode

```bash
# Windows (cmd)
set FLASK_ENV=development
python app.py

# Windows (PowerShell)
$env:FLASK_ENV="development"
python app.py
```

Expected console output:

```text
==================================================
[LICENSE] Checking license status...
==================================================
✅ License valid: Trial mode: 30 day(s) remaining
🔍 Loading face recognition model and employee face data...
✅ Face recognition loaded: 0 employees registered
==================================================
🚀 Attendance & Payroll System
   Running at: http://127.0.0.1:5000/
   Mode: DEVELOPMENT (debug=True)
==================================================
```

### 🌐 Open in the browser

**➡ http://127.0.0.1:5000/** (opens automatically; set `SKIP_BROWSER_AUTOLAUNCH=true` to disable)

| URL | Purpose |
|---|---|
| `/` | Public kiosk attendance screen |
| Admin / Employee login | One click away from the kiosk page |
| Setup Wizard | Shown automatically until the first Admin exists |

### 🧙 First-run Setup Wizard
1. Create the first **Admin** account.
2. Enter **Company** details (name, address, logo, contact).
3. Set default **office timing / working hours** (editable later in Settings).

> ℹ️ Outside development mode (`FLASK_ENV=production`, or any packaged build) the app is served by **Waitress** with 8 threads, so several kiosk check-ins can be handled at once.

On first run the app creates `instance/attendance.db`, `dataset/`, `uploads/` and `trained_model/` automatically. Logs go to `logs/` (rotating).

---

## 🧪 Automated Testing

```bash
pip install pytest
pytest                    # full suite
pytest -v                 # verbose
pytest tests/test_payroll_calculations.py
pytest -k "attendance"    # keyword filter
pytest -x --maxfail=1     # stop on first failure
```

The suite (470+ tests) covers authentication, attendance rules, payroll math, PDF generation, email, scheduling, rate limiting, face-data encryption/consent (`test_face_security.py`) and the Flask routes.

`conftest.py` stubs `cv2`, `mediapipe`, `deepface` and TensorFlow, so tests run **fast, offline, with no GPU or camera**.

Optional coverage:

```bash
pip install pytest-cov
pytest --cov=. --cov-report=term-missing
```

**Run the suite** before every commit touching core modules, before every client build, and after upgrading any dependency.

---

## 📦 Building & Packaging (For Developers)

Two stages: **PyInstaller** makes the app folder → **Inno Setup** wraps it into a single installer.

### 📋 Pre-build checklist

- [ ] Clean venv with `requirements.txt` installed (no plain `opencv-python`)
- [ ] `python scripts/download_vendor_assets.py` has been run
- [ ] DeepFace weights exist in `%USERPROFILE%\.deepface\weights` (Step 7)
- [ ] `pytest` passes
- [ ] Your **own** Ed25519 public key is set in `licensing/license_manager.py` (or via `LICENSE_PUBLIC_KEY_HEX`) — see [License Management](#-license--backup-management)
- [ ] `console=False` in `attendance_app.spec` for client builds (`True` is only for debugging a build on your own machine)
- [ ] `upx=False` stays as is (UPX triggers antivirus false-positives)
- [ ] `installer\app_icon.ico` exists (or comment out the `SetupIconFile` line in the `.iss`)

### 1️⃣ Install build tools

```bash
pip install -r requirements-packaging.txt      # PyInstaller + hooks-contrib
```

### 2️⃣ Build the `.exe` with PyInstaller

```bash
pyinstaller attendance_app.spec --clean
```

Look for this line in the build output:

```text
[spec] Bundling DeepFace weights from: C:\Users\you\.deepface\weights
```

If you see `WARNING: DeepFace weights folder not found`, **stop** — the build will try to download weights on the client PC. If weights live elsewhere:

```bash
set DEEPFACE_WEIGHTS_DIR=C:\path\to\.deepface\weights
pyinstaller attendance_app.spec --clean
```

Output (a **onedir** build — ship the whole folder, not just the exe):

```text
dist/
└── AttendancePayrollSystem/
    ├── AttendancePayrollSystem.exe
    ├── templates/  static/  dataset/  uploads/  trained_model/
    └── deepface_weights/ + bundled Python runtime & DLLs
```

### 3️⃣ Verify the build away from your dev PC

Copy `dist\AttendancePayrollSystem\` to a different folder or (ideally) a clean VM with **no Python**, then run through the checklist at the bottom of `attendance_app.spec`: first-run DB creation, Setup Wizard, scheduler, face capture, PDF/email, antivirus behaviour.

**Also confirm your private licensing key was not bundled:**

```bash
dir /s /b dist\*.pem
```

This must print nothing.

### 4️⃣ Compile the installer with Inno Setup

1. Install **[Inno Setup 6](https://jrsoftware.org/isdl.php)**.
2. Make sure `dist\AttendancePayrollSystem\` exists (Inno only packages what's already on disk).
3. Compile — either:
   - **GUI:** open `installer\AttendancePayrollSystem.iss` → **Build ▸ Compile**, or
   - **Command line:**
     ```bat
     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\AttendancePayrollSystem.iss
     ```
4. Your installer appears at:

```text
installer\Output\AttendancePayrollSystem-Setup-1.0.0.exe
```

**What the installer does:**
- Installs per-user to `%LOCALAPPDATA%\AttendancePayrollSystem` — **no admin rights / UAC prompt**, and the app can write its database and photos there.
- Adds a Start Menu group, an optional Desktop shortcut, and an "Add or Remove Programs" entry.
- **Uninstall keeps customer data** (database, uploads, dataset, `.env`, logs) on purpose, so a reinstall never wipes payroll history.

> ❌ Do **not** switch the install path to `Program Files` without also changing where the app stores data (`BASE_DIR` in `config.py`) — first launch would fail with permission errors. The `.iss` header explains how.

To bump the version, edit `MyAppVersion` in the `.iss` file.

---

## 🔑 License & Backup Management

### 🔐 How licensing works

Licenses use **Ed25519 public-key signatures**. The vendor keeps a **private key** (signs licenses); the app ships only the **public key** (verifies them). A customer can read every line of source and still cannot create a valid license.

Each license token is bound to the customer's **machine fingerprint** (a SHA-256 hash of hardware identifiers) and can be perpetual or time-limited.

**On every start, the app decides in this order:**

```text
Valid signed license found?  ──yes──►  Start normally
        │ no
        ▼
30-day trial still active?   ──yes──►  Start in Trial mode
        │ no
        ▼
Show the machine fingerprint and exit
```

The license token is read from the `LICENSE_TOKEN` environment variable, or from a `license.lic` file next to the application.

### 🧑‍💼 Vendor workflow (you)

**One-time setup — create your keypair:**

```bash
python licensing/keygen.py init-keys
```

This writes `licensing/vendor_private_key.pem` and prints a **public-key hex**. Paste that hex into `LICENSE_PUBLIC_KEY_HEX` in `licensing/license_manager.py` (or set it as an environment variable) **before building any customer release**.

> 🚨 **Never ship, email or commit `vendor_private_key.pem`.** Anyone holding it can issue unlimited licenses. Keep an offline backup. Regenerating it invalidates every license already issued. It is already listed in `.gitignore`, together with `license.lic` and `license_keys_log.txt`.

**Issuing a license for a customer:**

```bash
python licensing/keygen.py issue <MACHINE_FINGERPRINT> "Customer Name" --days 365 --edition pro
```

`--days` sets the validity period — **omit it for a perpetual license**. `--edition` is an optional tag. The command prints a token — send it to the customer.

### 🙋 Customer activation (License Activation UI)

1. Log in as **Admin** → **Settings** → **🔑 License Activation** card.
2. The card shows the current state: **License Valid** (customer, expiry or *Perpetual*), **Trial Mode** (days remaining) or **No License**.
3. To get the **machine fingerprint** for the vendor: it is printed in the console message when startup is blocked, and returned in full by the admin-only `/settings/license-info` endpoint. *(The Settings card only displays the first 16 characters as a preview.)*
4. Paste the token received from the vendor into the text box and click **Activate License**.

Alternative (no UI): save the token as **`license.lic`** next to the `.exe`, or set the `LICENSE_TOKEN` environment variable.

> ℹ️ A license is tied to one machine. If the customer changes hardware, the fingerprint changes and a new license must be issued.

The `licensing/` folder also contains a vendor-side customer database, email dispatch, and guides — see `licensing/VENDOR_SYSTEM_GUIDE.md`, `licensing/USER_MANUAL.md` and `licensing/EULA.txt`.

### 💾 Backups

| Type | When | Where |
|---|---|---|
| ⏰ **Automated weekly backup** | Every **Sunday at 02:00** while the app is running | `uploads/backups/automated_backup_<YYYYMMDD_HHMMSS>.zip` |
| 🖱 **Manual backup** | Any time: **Admin → Settings → Data Backup → Backup Now** | Downloaded through the browser as `attendance_backup_<timestamp>.zip` |

- The automated job keeps only the **last 4** backups and deletes older ones.
- Each zip contains: `instance/attendance.db`, `uploads/`, `dataset/` (encrypted face photos) and `.env` (secrets + encryption key).
- Look for `AUTOMATED BACKUP SCHEDULER REGISTERED - Every Sunday at 2:00 AM` in the logs to confirm it is scheduled.

> ⚠️ Backups live on the **same PC** as the data. **Copy them to an external drive or cloud storage regularly** — they don't protect against disk failure or theft. Treat backup zips like passwords: they contain `.env` and the face-encryption key.

**Restoring:** stop the app, extract the zip over the install folder (keeping the same `.env`), and start the app again.

---

## 🚚 Deploying to a Client Machine

1. Give the client `AttendancePayrollSystem-Setup-<version>.exe` (built above).
2. They run it (no admin rights needed) and launch the app from the Start Menu.
3. Their browser opens to `http://127.0.0.1:5000/` and the **Setup Wizard** runs.
4. The app runs in **30-day trial** until you issue a license (see above).
5. Configure SMTP in `.env` (next to the exe) if payslip emails are needed.

### Where things live on the client

```text
%LOCALAPPDATA%\AttendancePayrollSystem\
├── AttendancePayrollSystem.exe
├── .env                     ← secrets, SMTP, encryption key  (back up!)
├── license.lic              ← after activation
├── instance\attendance.db   ← all records                    (back up!)
├── dataset\                 ← encrypted face photos          (back up!)
├── uploads\                 ← payslips + backups\            (back up!)
├── trained_model\           ← face-embedding cache (rebuilt automatically)
└── logs\                    ← rotating app logs
```

> 🔥 Losing `.env` makes every previously encrypted face photo **permanently unrecoverable**. That is inherent to encryption, not a bug.

---

## 🩺 Troubleshooting & FAQs

<details>
<summary><b>🔑 The app closes immediately with "LICENSE VALIDATION FAILED"</b></summary>

The trial has ended (or the license is invalid/expired) and no valid license was found. Note the **machine fingerprint** printed in the message, send it to the vendor, and place the returned token in `license.lic` next to the exe (or set `LICENSE_TOKEN`). A token issued for a different PC, or signed by a different key than the one built into the app, will not verify.
</details>

<details>
<summary><b>🔑 "License activation failed" when pasting a token</b></summary>

- Paste the **entire** token (`xxxx.yyyy`, no spaces or line breaks).
- The token must be issued for **this machine's** fingerprint.
- The app's public key must match the vendor private key used to sign it (`LICENSE_PUBLIC_KEY_HEX`).
- Check whether the license has expired.
</details>

<details>
<summary><b>🔒 <code>unable to open database file</code> / photos or payslips not saving</b></summary>

The app is installed in a write-protected folder (e.g. `Program Files`). Reinstall to `%LOCALAPPDATA%\AttendancePayrollSystem\` (the installer's default). Running as Administrator is a last resort only.
</details>

<details>
<summary><b>📷 Camera doesn't open / black preview</b></summary>

1. **Settings ▸ Privacy & security ▸ Camera** → enable **"Let desktop apps access your camera"**.
2. Close Zoom / Teams / the Windows Camera app — only one program can use the camera.
3. On PCs with multiple cameras, the wrong device index may be used (`cv2.VideoCapture(0)` vs `1`); adjust `FaceCapture.start_capture()` in `ai_engine.py`.
4. If the Windows Camera app also fails, it's a driver problem.
</details>

<details>
<summary><b>🐌 First face registration hangs or fails on an offline PC</b></summary>

The build didn't include DeepFace weights. Rebuild after populating `%USERPROFILE%\.deepface\weights` (Step 7) and confirm the `[spec] Bundling DeepFace weights` line appears.
</details>

<details>
<summary><b>⏰ Payroll / auto-logout / backups didn't run</b></summary>

1. Check the logs for `Payroll scheduler started`, `DAILY APPROVAL SCHEDULER REGISTERED - 23:59`, and `AUTOMATED BACKUP SCHEDULER REGISTERED`. If missing, look for `Failed to start scheduler:`.
2. The scheduler only runs **while the exe is running** — there is no background service. Closing the browser tab does *not* stop the app; closing the exe does.
3. If the PC was **off or asleep** at the scheduled time the job is skipped. Payroll and approvals are backfilled on next start (`PAYROLL RECONCILIATION CHECK` in the log); **backups are not backfilled**, so use **Backup Now** if a Sunday was missed.
4. In a custom PyInstaller spec, keep `copy_metadata('APScheduler')` — removing it makes the scheduler fail silently.
</details>

<details>
<summary><b>🛡 Windows Defender / SmartScreen blocks the exe</b></summary>

Very common for PyInstaller apps bundling TensorFlow/OpenCV. In order of effectiveness: **code-sign** the exe/installer; keep `upx=False`; submit the file to [Microsoft for analysis](https://www.microsoft.com/en-us/wdsi/filesubmission); as a stopgap, have IT add an exclusion for the install folder.
</details>

<details>
<summary><b>💥 <code>ModuleNotFoundError</code> / <code>DLL load failed</code> only in the built exe</b></summary>

A hidden import or data file wasn't bundled. Ensure `pyinstaller-hooks-contrib` is installed, add the missing module to `hiddenimports` in `attendance_app.spec`, rebuild with `--clean`, and temporarily set `console=True` **on your own machine** to see the traceback.
</details>

<details>
<summary><b>🚫 <code>python -c "import cv2, tensorflow, ..."</code> fails</b></summary>

Check `python --version` is 3.10.x, use a fresh venv, and make sure `numpy` is 1.26.4 (not 2.x) and only `opencv-contrib-python` is installed. Fix this before attempting any PyInstaller build.
</details>

<details>
<summary><b>❓ The exe keeps running after I close the browser tab</b></summary>

Expected — the Flask server and scheduler run inside the exe. End the `AttendancePayrollSystem.exe` process (Task Manager) to fully quit.
</details>

<details>
<summary><b>❓ Can I use MySQL instead of SQLite?</b></summary>

Yes. Set `DATABASE_URL=mysql+pymysql://user:password@host/dbname` in `.env`. Note that the built-in backup zips only include the SQLite file, so back up a MySQL database with your own tooling.
</details>

<details>
<summary><b>❓ Can I change the port (5000)?</b></summary>

The host/port are set in the `__main__` block of `app.py` (`SERVER_HOST`, `SERVER_PORT`).
</details>

---

## 🗂 Project Structure

| Path | Purpose |
|---|---|
| `app.py` | Flask app factory, blueprint registration, startup block (license check → face model → server) |
| `launcher.py` | PyInstaller entry point that runs `app.py` |
| `config.py` | Environment-aware config, frozen-safe `BASE_DIR`, logging |
| `database.py` · `models.py` · `extensions.py` | DB init/migrations, ORM models, Flask extensions |
| `auth_routes.py` · `attendance_routes.py` · `payroll_routes.py` · `reports_routes.py` · `approvals_routes.py` · `settings_routes.py` | Blueprints |
| `setup_wizard.py` | First-run admin/company setup |
| `employees.py` · `attendance.py` · `payroll.py` | Employee, attendance-rule and payroll engines |
| `ai_engine.py` · `face_recognition_singleton.py` | Face detection/recognition, embedding cache |
| `crypto_utils.py` | Encryption for face photos and payslip passwords |
| `pdf_generator.py` · `email_service.py` | PDF payslips/reports (AES-256), SMTP delivery |
| `scheduler_service.py` | APScheduler: payroll, auto-logout, weekly backup, reconciliation |
| `services/` | Admin reports, approvals, attendance calculator & stats |
| `licensing/` | Ed25519 license verification, **vendor-only** `keygen.py`, customer DB and guides |
| `installer/` | Inno Setup script (`.iss`), icon and instructions |
| `scripts/download_vendor_assets.py` | Downloads offline UI assets |
| `attendance_app.spec` | PyInstaller build specification |
| `setup.bat` | One-click environment setup + `.exe` build |
| `templates/` · `static/` | Jinja2 views and CSS/JS/images |
| `tests/` · `conftest.py` | pytest suite with stubbed ML layer |
| `instance/` · `dataset/` · `uploads/` · `trained_model/` · `logs/` | Runtime data (git-ignored) |
| `requirements.txt` · `requirements-packaging.txt` | Runtime vs build-only dependencies |

---

## 🤝 Contributing & Support

1. ✅ Confirm `pytest` passes on a clean checkout before you start.
2. 🔁 Run the relevant test module often while developing (`pytest tests/test_<area>.py -v`).
3. 🧪 Run the full suite before opening a PR; if you touched packaging, walk the [pre-build checklist](#-pre-build-checklist) and make a test build.
4. 🐞 When reporting an issue include: exact console output/log lines, whether it happens in `python app.py` or **only** in the packaged exe, and what you already tried from [Troubleshooting](#-troubleshooting--faqs).

**Author:** Vaishnavi Maruti Lekawale · **Repository:** [AI-Attendance-Payroll-System](https://github.com/vaishnavilekawale/AI-Attendance-Payroll-System)

---

<div align="center">

**AI Attendance & Payroll System** — built with ❤️ using Flask, DeepFace and Python.

</div>