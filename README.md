# 🧠 AI Attendance & Payroll System

**Face-recognition attendance, automated payroll, encrypted payslips and signed licensing — shipped as a one-click Windows desktop app.**

![Python](https://img.shields.io/badge/Python-3.10.x-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-000000?logo=flask&logoColor=white)
![TensorFlow](https://img.shields.io/badge/TensorFlow-2.15-FF6F00?logo=tensorflow&logoColor=white)
![DeepFace](https://img.shields.io/badge/DeepFace-FaceNet512-8A2BE2)
![Security](https://img.shields.io/badge/Security-AES--256%20%7C%20Ed25519-success)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11%20(64--bit)-0078D6?logo=windows&logoColor=white)
![Tests](https://img.shields.io/badge/tests-pytest-brightgreen?logo=pytest&logoColor=white)
![Version](https://img.shields.io/badge/version-1.0.0-informational)
![Status](https://img.shields.io/badge/status-internal--production-orange)

[Features](#2--features) • [Quick Start](#6--quick-start) • [Installation](#7--installation--setup-a-to-z) • [Build the .exe](#11--building--packaging-for-developers) • [Licensing](#12--license--backup-management) • [Troubleshooting](#14--troubleshooting--faqs)

> 👋 **New here?** Read [section 5](#5--prerequisites) (what you need), then jump to [section 7, Option A](#option-a-recommended-one-click-with-setupbat). Follow the steps **in order** and you will have the app running.

---

## 📖 Table of Contents

1. [Project Overview](#1--project-overview)
2. [Features](#2--features)
3. [Tech Stack](#3--tech-stack)
4. [Architecture](#4--architecture)
5. [Prerequisites](#5--prerequisites)
6. [Quick Start](#6--quick-start)
7. [Installation & Setup (A to Z)](#7--installation--setup-a-to-z)
8. [Running the Application](#8--running-the-application)
9. [Check That Everything Works](#9--check-that-everything-works)
10. [Automated Testing](#10--automated-testing)
11. [Building & Packaging (For Developers)](#11--building--packaging-for-developers)
12. [License & Backup Management](#12--license--backup-management)
13. [Deploying to a Client Machine](#13--deploying-to-a-client-machine)
14. [Troubleshooting & FAQs](#14--troubleshooting--faqs)
15. [Project Structure](#15--project-structure)
16. [Contributing & Support](#16--contributing--support)

---

## 1. 🎯 Project Overview

The **AI Attendance & Payroll System** is a full-stack Flask application that:

- ✅ marks employee attendance automatically using **face recognition** — at a public kiosk screen *and* from each employee's own login,
- 💰 runs a complete **payroll pipeline** (allowances, deductions, overtime, net pay),
- 📄 generates **AES-256 password-protected PDF payslips** and emails them automatically,
- 🔑 protects the product with **Ed25519-signed, machine-bound licenses** (30-day trial, then a license lock screen), and
- 💾 protects customer data with **automated weekly backups** (plus a catch-up backup if the PC was off).

It ships as a **Windows installer / `.exe`** built with PyInstaller and Inno Setup, so a non-technical client just installs and double-clicks — **no Python, no `pip`, no server setup** on their machine.

> 💡 Under the hood it is a local Flask web app served on `127.0.0.1:5000`. The launcher opens the user's default browser automatically, so it *feels* like a native desktop app.

**Two ways people use this project:**

| You are… | You do… |
| --- | --- |
| 👩‍💻 A **developer / vendor** | Follow sections 5 to 12: set up Python, run from source, test, build the `.exe` and issue licenses. |
| 🏢 A **customer / client** | Follow section 13: install the `Setup.exe` you were given and open the app from the Start Menu. |

---

## 2. ✨ Features

### 🎥 Attendance

| Feature | Details |
| --- | --- |
| **Face-recognition kiosk** | Public scanning page (`/`) that scans continuously (about every 2 seconds). DeepFace (FaceNet512, RetinaFace detector) with strict cosine-distance matching |
| **Employee self-service attendance** | After logging in, an employee marks their *own* attendance with their face (**My Attendance** page). The face must match the logged-in employee, otherwise it is rejected |
| **No guessing on look-alikes** | Hard distance ceiling of `0.30` plus a minimum margin of `0.05` over the second-best candidate — ambiguous faces are reported *Unknown* instead of being guessed |
| **Single punch per appearance** | Frame-presence lock (8-second timeout) prevents duplicate punches while someone stands in front of the camera |
| **Multiple IN/OUT pairs per day** | Every IN→OUT pair is stored as an activity; working hours are the exact sum of all pairs |
| **Auto-logout approval** | At **23:59** every day, anyone who never punched out gets a *logout approval request* that a manager/admin approves or rejects. A startup pass also backfills requests missed while the PC was off |
| **Manual fallback with approval** | Password-verified manual attendance, gated behind manager/admin approval |
| **Admin / manager attendance edit** | Managers and admins can correct IN/OUT times; calculations are re-run from the edited times |
| **Full audit trail** | `attendance_type`, `approval_status`, `submission_timestamp` on every record |
| **One shared rule engine** | Present / Late / Half-Day / Absent / Overtime computed in one place (`services/attendance_calculator.py`) for face *and* manual punches |
| **Versioned settings** | Past attendance is always evaluated against the rules in force *on that date* (`AttendanceSettingsHistory`) |

### 💰 Payroll & Reports

- Configurable **allowances** (HRA, DA, Medical, Travel, Special, Other) and **deductions** (PF, ESIC, TDS, Professional Tax, LOP, Late, Transport)
- **Overtime** and **late-entry deduction** rules (configurable in Settings / `.env`)
- Payroll eligibility follows each employee's **joining date**
- **Automated monthly payroll** via APScheduler (1st of every month, 12:01, for the month just completed), with a startup **reconciliation pass** that backfills any period missed while the PC was off
- **AES-256 password-protected PDF payslips** (ReportLab + pikepdf) with automated email delivery. Admins or employees can set a **custom payslip password**; otherwise a default formula derived from the employee's own details is used
- Admin dashboard analytics, department-wise stats, and PDF export for admin and employee reports
- A single shared aggregation service, so numbers never disagree between screens

### 🔐 Security

- **Face photos encrypted at rest** (Fernet / AES) — with an **opt-in biometric consent log** per employee
- **AES-256** password-protected payslips; custom payslip passwords are stored encrypted
- **Ed25519 licensing** — customers only hold the *public* key, so they cannot forge a license. In packaged builds the public key is embedded and cannot be overridden from `.env`
- **Tamper-resistant 30-day trial** (stored in several places; winding the clock back revokes the trial)
- **CSRF protection** on all state-changing routes
- **Rate-limited login and password flows** (login: 10/minute and 50/hour; password/reset/setup routes: 5/minute)
- Werkzeug password hashing

### 💾 Operations

- **Automated weekly backups** every Sunday at 02:00 — saved to a dedicated `backups\` folder **and** copied to an external location when one is available (see [Backups](#-backups)); last 4 kept per folder
- **Startup catch-up backup** if the newest backup is older than 7 days
- One-click **manual backup download** from Admin → Settings
- **Role-based access:** Admin, Manager, Employee
- **First-run Setup Wizard** — admin account, company details, license (optional) — no manual database or admin seeding
- **License lock screen** when the trial has ended and no valid license exists
- **Fully offline-capable UI** — Bootstrap, icons, Chart.js and fonts are bundled locally

### 👥 Roles

| Role | Access |
| --- | --- |
| **Admin** | Full control — employees, face registration, payroll, settings, licensing, backups, reports, approvals |
| **Manager** | An employee whose designation is **Manager**; approves manual-attendance and logout requests for their department |
| **Employee** | Self-service portal — Dashboard, **My Attendance** (face scan), **My Payroll** (payslips), **My Reports**, **My Profile**, change password |

---

## 3. 🛠 Tech Stack

| Layer | Technology |
| --- | --- |
| **Backend** | Python 3.10, Flask 3.0, SQLAlchemy 2.0, Flask-Migrate / Alembic, Flask-WTF, Flask-Limiter |
| **AI / Computer Vision** | DeepFace 0.0.93 (FaceNet512 + RetinaFace), TensorFlow 2.15 + tf-keras, OpenCV-Contrib 4.8.1.78, MediaPipe 0.10.21, NumPy 1.26.4 |
| **Frontend** | Bootstrap 5, Bootstrap Icons, vanilla JavaScript (`fetch`), Chart.js |
| **Database** | SQLite (default) or MySQL via PyMySQL |
| **PDF & Security** | ReportLab, pikepdf (AES-256), `cryptography` (Fernet, Ed25519) |
| **Scheduling** | APScheduler (payroll, auto-logout approvals, backups) |
| **Licensing / payments (vendor side)** | Ed25519 signing, Razorpay purchase portal (`licensing/vendor_app.py`) |
| **Production server** | Waitress (used automatically outside development mode) |
| **Testing / CI** | pytest (600+ tests, stubbed ML/CV layer), GitHub Actions workflow |
| **Packaging** | PyInstaller 6.x + Inno Setup 6 |

---

## 4. 🏗 Architecture

```
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
│     ├─ License gate: runs before every request; shows the      │
│     │               lock screen when trial/license has ended   │
│     ├─ Flask blueprints: setup · auth · employees · attendance │
│     │                    payroll · reports · approvals · settings
│     ├─ APScheduler: monthly payroll · 23:59 auto-logout        │
│     │               approvals · Sunday 02:00 backup            │
│     │                                                          │
│     ├─ instance/attendance.db      (SQLite)                    │
│     ├─ dataset/                    (encrypted face photos)     │
│     ├─ uploads/                    (payslips, profile photos)  │
│     ├─ backups/                    (automated backup zips)     │
│     ├─ trained_model/              (face-embedding cache)      │
│     ├─ logs/                       (rotating app logs)         │
│     ├─ license.lic                 (signed license token)      │
│     └─ .env                        (secrets & configuration)   │
└───────────────────────────────────────────────────────────────┘
```

**In plain words:** when you start the app it (1) checks the license, (2) loads the face-recognition model, (3) opens your browser, and (4) keeps running in the background and serves the web pages. Everything is stored in plain folders next to the app.

### Main pages

| Area | URLs |
| --- | --- |
| **Kiosk** | `/` (public, auto-scan), `POST /api/auto-scan-attendance` |
| **Auth** | `/login` (admin and employee tabs), `/logout`, `/forgot-password`, `/employee-forgot-password`, `/change-password`, `/employee-change-password` |
| **Setup & license** | `/setup/` (admin → company → license → done), `/license` (lock screen), `/license/activate` |
| **Admin** | `/dashboard`, `/employees`, `/face-registration/<id>`, `/attendance`, `/payroll`, `/payroll-settings`, `/reports`, `/settings`, `/admin/approvals`, `/admin/pending-manual-attendance` |
| **Manager** | `/manager/approvals`, `/manager/pending-manual-attendance`, `/manager/edit-attendance/<id>` |
| **Employee** | `/employee-dashboard`, `/employee-attendance`, `/employee-payroll`, `/employee-reports`, `/employee-profile` |

---

## 5. 📋 Prerequisites

| Requirement | Version | Notes |
| --- | --- | --- |
| 🐍 **Python** | **3.10.x** (3.10.11 recommended) | TensorFlow 2.15 / MediaPipe wheels are most reliable on 3.10. **Avoid 3.11, 3.12 and newer.** |
| 🔧 **Git** | Any recent version | To clone the repository |
| 📷 **Webcam** | Any USB / built-in | Needed for face registration and recognition |
| 🪟 **Windows** | 10 / 11, 64-bit | Build and ship for 64-bit only |
| 💽 **Disk / RAM** | ~5 GB free, 8 GB RAM recommended | TensorFlow + DeepFace are large |
| 🌐 **Internet** | Needed during setup | To install packages, to download the UI files, and to download face-model weights on first use |
| 🗄 **MySQL 8.x** | *Optional* | Only if you don't want the default SQLite |

**Only for building the installer** (developers / build machine):

| Tool | Purpose |
| --- | --- |
| **PyInstaller** (installed via `requirements-packaging.txt`) | Freezes the app into an `.exe` |
| **[Inno Setup 6](https://jrsoftware.org/isinfo.php)** | Compiles the final `Setup.exe` installer |

> ⚠️ **Why Python 3.10?** DeepFace, TensorFlow 2.15, `tf-keras` and `mediapipe==0.10.21` all publish official Windows wheels for 3.9–3.11, and 3.10 is the safest overlap. Also, `numpy` is pinned to `1.26.4` (below 2.0) on purpose — NumPy 2.x breaks TensorFlow/MediaPipe.

### How to install Python 3.10 correctly

1. Download **Python 3.10.11** from [python.org](https://www.python.org/downloads/release/python-31011/).
2. Run the installer. On the **first screen tick "Add Python to PATH"** (this is the most common thing people forget).
3. Open **Command Prompt** and check:

```
python --version     # must print Python 3.10.x
git --version
```

If `python --version` prints a different version, another Python comes first in your PATH. You can still check that 3.10 is installed with `py -3.10 --version`. `setup.bat` finds Python 3.10 by itself using this `py -3.10` launcher.

---

## 6. ⚡ Quick Start

Fastest path for people who already have Python 3.10 and Git:

```
git clone https://github.com/vaishnavilekawale/AI-Attendance-Payroll-System AI_APS
cd AI_APS
setup.bat
```

`setup.bat` installs everything, checks it, downloads the UI files and then shows a menu. Choose **1** (run the app). Open **<http://127.0.0.1:5000>** — on first run you'll be taken to the **Setup Wizard**. 🎉

Prefer typing the commands yourself?

```
python -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt

pip uninstall -y opencv-python opencv-python-headless
pip install --force-reinstall --no-deps opencv-contrib-python==4.8.1.78

python scripts\download_vendor_assets.py
copy .env.example .env
set FLASK_ENV=development
python app.py
```

> ⏱ First install downloads several GB (TensorFlow, OpenCV, MediaPipe) — expect several minutes (10 to 30 on a slow connection).

---

## 7. 🧭 Installation & Setup (A to Z)

Read [section 5](#5--prerequisites) first. Then choose **one** option:

- **Option A:** run `setup.bat` (easiest, recommended)
- **Option B:** type the commands yourself (Steps 1 to 8)

### Option A (recommended): one click with `setup.bat`

1. Install **Python 3.10.11** and **Git** (see section 5).
2. Download the project:

```
git clone https://github.com/vaishnavilekawale/AI-Attendance-Payroll-System AI_APS
cd AI_APS
```

   (Or click **Code → Download ZIP** on GitHub and extract it.)

3. **Double-click `setup.bat`** inside the project folder.

It does these steps for you and **stops at the first error** (it never prints a fake "success"):

| Step | What happens |
| --- | --- |
| 1 | Finds **Python 3.10** (stops with a clear message if it is missing) |
| 2 | Creates the `venv` folder |
| 3 | Upgrades `pip`, removes the conflicting plain `opencv-python`, installs everything in `requirements.txt` and checks that `cv2`, TensorFlow, MediaPipe and DeepFace import correctly |
| 4 | Creates `.env` from `.env.example`, creates `licensing\.env.vendor` from its example (vendor use only) and creates the folders the app needs |
| 5 | Downloads the UI files (Bootstrap, icons, Chart.js, fonts) into `static\vendor` |

Then a menu appears:

```
1 - Run the app now (development mode)
2 - Build the .exe (PyInstaller, slow)
3 - Exit
```

**Recommended:** press **1** (run the app) and follow [section 9](#9--check-that-everything-works). Press **2** only when you really want to build the `.exe` (see section 11).

> 📝 During install you will see a list of **about 112 packages**. That is normal. Your `requirements.txt` has about 35 direct packages, but TensorFlow, DeepFace and MediaPipe bring many more of their own.

> ⚠️ **OpenCV conflict:** DeepFace asks for plain `opencv-python`, which clashes with `opencv-contrib-python` (both provide `cv2`). If you see `cv2` errors after setup, follow the repair steps in [Troubleshooting](#-cv2-errors-module-cv2-has-no-attribute-imread--attendance-not-marked).

### Option B: manual setup, step by step

#### Step 1️⃣ — Install the prerequisites

1. Install **Python 3.10.11** from [python.org](https://www.python.org/downloads/release/python-31011/).
   ✔ Tick **"Add Python to PATH"** in the installer.
2. Install **Git** from [git-scm.com](https://git-scm.com/downloads).
3. Confirm both work — open **Command Prompt** and run:

```
python --version    # should print Python 3.10.x
git --version
```

#### Step 2️⃣ — Clone the repository

```
git clone https://github.com/vaishnavilekawale/AI-Attendance-Payroll-System AI_APS
cd AI_APS
```

#### Step 3️⃣ — Create and activate a virtual environment

```
python -m venv venv
venv\Scripts\activate
```

Your prompt should now start with `(venv)`. A *virtual environment* is a private folder for this project's packages, so it does not mix with other Python projects.

> macOS/Linux is fine for development only: `source venv/bin/activate`. Production builds are Windows-only.

#### Step 4️⃣ — Install dependencies

```
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Now fix the OpenCV conflict (DeepFace installs plain `opencv-python`):

```
pip uninstall -y opencv-python opencv-python-headless
pip install --force-reinstall --no-deps opencv-contrib-python==4.8.1.78
```

Verify the heavy ML stack imported correctly **before** doing anything else:

```
python -c "import cv2, tensorflow, mediapipe, deepface; print('OK')"
pip check
```

You want to see `OK`. `pip check` may still print `deepface ... requires opencv-python, which is not installed` — that message is **harmless** (see [Troubleshooting](#-pip-check-says-deepface-requires-opencv-python)).

> ⚠️ Use a **clean** venv. Never have plain `opencv-python` installed next to `opencv-contrib-python` — both provide `cv2` and will conflict. Always install packages with the **pinned versions** from `requirements.txt`, and never interrupt a `pip install` halfway.

#### Step 5️⃣ — Configure the environment file (`.env`)

```
copy .env.example .env
```

The `.env` file must sit **next to `app.py`** (development) or **next to the `.exe`** (packaged build). Open it and review:

```
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
COMPANY_LOGO=static/images/company_logo.png
OFFICE_START_TIME=09:00
OFFICE_END_TIME=18:00
GRACE_PERIOD_MINUTES=15

# ── Working hours & salary rules ───────────────────────────
WORKING_HOURS_PER_DAY=9.0
LATE_DEDUCTION_ENABLED=false
LATE_DEDUCTION_PER_OCCURRENCE=0.0
OVERTIME_ENABLED=true
OVERTIME_RATE=1.5

# ── Face recognition ───────────────────────────────────────
FACE_RECOGNITION_TOLERANCE=0.6
MIN_FACE_IMAGES_REQUIRED=20

# ── Biometric encryption key ───────────────────────────────
# Do NOT set on a fresh install — generated automatically and written here.
# BACK UP .env once it exists. Losing this key = face photos unrecoverable.
# FACE_DATA_ENCRYPTION_KEY=

# ── Licensing (customer app) ───────────────────────────────
# LICENSE_PURCHASE_URL=https://buy.yourcompany.com
SUPPORT_EMAIL=your@gmail.com
# LICENSE_SERVER_URL=https://buy.yourcompany.com
```

| Key | Required? | What to know |
| --- | --- | --- |
| `SECRET_KEY` | Auto | Blank → a secure random key is generated and saved on first run. When **restoring** an install, reuse its original value. |
| `FLASK_ENV` | Yes | `development` enables Flask's debugger (unsafe for customers). `production` uses Waitress. |
| `MAIL_*` | Only for email | Without valid SMTP credentials payslip/reset emails fail and log an error; everything else still works. For Gmail use an **App Password**. |
| `DATABASE_URL` | No | Default is SQLite — right for a single-site install. |
| `WORKING_HOURS_PER_DAY`, `OVERTIME_*`, `LATE_DEDUCTION_*` | No | Default salary rules; also editable later in Settings. |
| `FACE_RECOGNITION_TOLERANCE` | No | Admin-tunable, but can only make matching **stricter** than the built-in `0.30` ceiling, never looser. |
| `FACE_DATA_ENCRYPTION_KEY` | Auto | Managed by `crypto_utils.py`. **Back it up.** |
| `LICENSE_PURCHASE_URL` | No | Address of your vendor portal; powers the **Buy a license** button on the lock screen (the machine fingerprint is appended automatically). |
| `SUPPORT_EMAIL` | No | Shown on the license lock screen. |
| `LICENSE_SERVER_URL` | No | If set, successful activations are reported to your vendor portal (best effort, never blocks activation). |
| `LICENSE_TOKEN` / `LICENSE_PUBLIC_KEY_HEX` | No | Optional overrides for development (see [License Management](#12--license--backup-management)). The public-key override is **ignored in packaged builds**. |
| `BACKUP_DIR`, `BACKUP_AUTO_EXTERNAL`, `BACKUP_KEEP`, `BACKUP_CATCHUP_DAYS` | No | Backup behaviour (see [Backups](#-backups)). |
| `DISABLE_CATCHUP_BACKUP` | No | `true` disables the startup catch-up backup. |
| `SKIP_BROWSER_AUTOLAUNCH` | No | `true` stops the app from opening the browser on start. |
| `RATELIMIT_STORAGE_URI` | No | Rate-limit storage; default `memory://`. |

> 🔒 **Never commit `.env`** and never copy your own `.env` into a customer deliverable. Every install gets its own.

#### Step 6️⃣ — Download the offline UI assets (one time)

```
pip install requests
python scripts\download_vendor_assets.py
```

This saves Bootstrap, Bootstrap Icons, Chart.js and fonts into `static\vendor\` so the UI renders without internet. Required before building the installer; **also required for development**, because these files are **not stored in GitHub** (they are git-ignored). If you skip this step the pages open **without any styling**.

#### Step 7️⃣ — Download the face-model weights (one time)

DeepFace downloads FaceNet512 + RetinaFace weights to `%USERPROFILE%\.deepface\weights` the **first time it runs a face operation** — not at install time. Trigger it once on your machine:

1. Run `python app.py` (see section 8) and finish the Setup Wizard.
2. Add a test employee and capture **at least one photo** in Face Registration.
3. Stop the app (`Ctrl+C`) and confirm the files exist:

```
dir %USERPROFILE%\.deepface\weights
```

You should see `facenet512_weights.h5` and RetinaFace weights (~100–300 MB total). The PyInstaller build bundles these so client PCs work **offline**. (`scripts\prepare_deepface_weights.py` can also prepare them.)

#### Step 8️⃣ — Create the runtime folders (only if they are missing)

The app creates `instance/`, `dataset/`, `uploads/`, `trained_model/` and `logs/` by itself on first run (and `backups/` on the first backup). `setup.bat` also creates them. If you ever need to do it by hand:

```
mkdir dataset instance uploads trained_model logs
```

---

## 8. ▶ Running the Application

### 🧪 Development mode

```
# Windows (cmd)
set FLASK_ENV=development
python app.py

# Windows (PowerShell)
$env:FLASK_ENV="development"
python app.py
```

Always run it from inside the activated venv (`venv\Scripts\activate`). Or just choose **1** in the `setup.bat` menu.

Expected console output (abridged):

```
==================================================
[LICENSE] Checking license status...
==================================================
✅ License check passed: License is valid
🔍 Loading face recognition model and employee face data...
✅ Face recognition loaded: 4 employees registered
✅ Face embeddings ready: 80 image(s) ...
==================================================
🚀 Attendance & Payroll System
   Running at: http://127.0.0.1:5000/
   Mode: DEVELOPMENT (debug=True)
==================================================
```

On a fresh install the license line reads `Trial mode: 30 day(s) remaining` and the face line reads `0 employees registered`.

Stop the app any time with **Ctrl + C**.

### 🌐 Open in the browser

**➡ <http://127.0.0.1:5000/>** (opens automatically; set `SKIP_BROWSER_AUTOLAUNCH=true` to disable)

| URL | Purpose |
| --- | --- |
| `/` | Public kiosk attendance screen |
| `/login?role=admin` · `/login?role=employee` | Admin / Employee login (one click away from the kiosk page) |
| `/setup/` | Setup Wizard — shown automatically until the first Admin exists |
| `/license` | License lock screen — shown automatically once trial/license has ended |

### 🧙 First-run Setup Wizard

1. Create the first **Admin** account (remember this password — write it down).
2. Enter **Company** details (name, office hours, etc.).
3. **License** — activate a license now, or skip to start the 30-day trial.
4. **Done** — confirmation and a link into the dashboard.

> ℹ️ Outside development mode (`FLASK_ENV=production`, or any packaged build) the app is served by **Waitress** with 8 threads, so several kiosk check-ins can be handled at once.

On first run the app creates `instance/attendance.db`, `dataset/`, `uploads/` and `trained_model/` automatically. Logs go to `logs/` (rotating).

### 👩‍💼 Typical daily workflow

1. **Admin** adds employees and registers their faces (at least `MIN_FACE_IMAGES_REQUIRED` photos each), then trains/refreshes the face model.
2. **Employees** are marked present by the kiosk screen, or log in and use **My Attendance**.
3. At **23:59** anyone who never punched out gets a logout approval request → **manager/admin** approves or rejects it.
4. On the **1st of every month** payroll for the previous month is generated, payslip PDFs are created and emailed.

---

## 9. ✅ Check That Everything Works

Do this once after setup (and again on a clean PC before delivering to a client). If a step fails, see [section 14](#14--troubleshooting--faqs).

- [ ] The console shows **License check passed** and **Face recognition loaded**
- [ ] The page opens **with styling** (colours, buttons) — if it looks like plain text, run `python scripts\download_vendor_assets.py`
- [ ] The Setup Wizard finishes and you can **log in as Admin**
- [ ] You can **add an employee**
- [ ] **Face registration** works: the camera opens and photos are captured (the first time it downloads the model files, so keep the internet on)
- [ ] On the kiosk page (`/`) the registered face **marks attendance**
- [ ] The employee can **log in and mark attendance** on the **My Attendance** page (no "Attendance Not Marked" error)
- [ ] Manual attendance goes to a manager/admin for **approval**
- [ ] You can run **payroll** for a month
- [ ] You can **download a payslip PDF** (it asks for a password)
- [ ] You can download an **admin or employee report PDF**
- [ ] A **manual backup** downloads from Admin → Settings → Data Backup

> 💡 Automated tests (section 10) use fake camera and AI parts. They do **not** prove that your real camera and real face recognition work. Only the checklist above does.

---

## 10. 🧪 Automated Testing

```
pip install pytest
pytest                    # full suite
pytest -v                 # verbose
pytest tests/test_payroll_calculations.py
pytest -k "attendance"    # keyword filter
pytest -x --maxfail=1     # stop on first failure
```

The suite (600+ tests) covers authentication, attendance rules, the attendance calculator, approvals, payroll math, PDF generation, email, scheduling, backups, rate limiting, face matching and wrong-person protection, face-data encryption/consent (`test_face_security.py`), licensing, configuration/setup and the Flask routes.

`conftest.py` stubs `cv2`, `mediapipe`, `deepface` and TensorFlow, so tests run **fast, offline, with no GPU or camera**.

**How to read the result:** at the end pytest prints a line such as `626 passed`. If you see `failed`, scroll up to read which test failed and why, and send that text when asking for help.

Optional coverage:

```
pip install pytest-cov
pytest --cov=. --cov-report=term-missing
```

**Run the suite** before every commit touching core modules, before every client build, and after upgrading any dependency.

**CI:** `.github/workflows/ci.yml` runs the app import check and `pytest tests/` on every push and pull request to `main`/`master` (Python 3.10).

---

## 11. 📦 Building & Packaging (For Developers)

Two stages: **PyInstaller** makes the app folder → **Inno Setup** wraps it into a single installer.

### 📋 Pre-build checklist

- [ ] Clean venv with `requirements.txt` installed (no plain `opencv-python`; `python -c "import cv2; print(cv2.imread)"` works)
- [ ] `python scripts/download_vendor_assets.py` has been run
- [ ] DeepFace weights exist in `%USERPROFILE%\.deepface\weights` (Step 7)
- [ ] `pytest` passes
- [ ] Your **own** Ed25519 public key is embedded in `licensing/license_manager.py` — see [License Management](#12--license--backup-management)
- [ ] `console=False` in `attendance_app.spec` for client builds (`True` is only for debugging a build on your own machine)
- [ ] `upx=False` stays as is (UPX triggers antivirus false-positives)
- [ ] `installer\app_icon.ico` exists and is a **multi-size** icon (see below)
- [ ] The six `installer\wizard_*.bmp` images are present (see below)

### 🎨 Application icon & installer images

**Application icon — `installer\app_icon.ico`.** Used by PyInstaller (the `.exe` icon) and by Inno Setup (`SetupIconFile`). It **must contain several sizes**: 16, 24, 32, 48, 64, 128 **and 256 px**. An icon with only one small size looks tiny and blurry in Explorer's *Large* and *Extra large icons* views, because Windows has to scale it up. Create it from a PNG of at least 256×256 (512×512 is better, with a transparent background):

```
python -c "from PIL import Image; img = Image.open('logo.png').convert('RGBA'); img.save('installer/app_icon.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
python scripts\verify_icon.py
```

Rebuild the `.exe` afterwards (the icon is embedded at build time). If Explorer still shows the old icon, rename the `.exe` once or restart the PC — Windows caches icons.

**Installer wizard images — `installer\wizard_*.bmp`.** The Inno Setup script (`WizardImageFile` / `WizardSmallImageFile`) uses them for the setup wizard. **Do not delete them** — the installer will not compile without them.

| Files | Used for | Sizes (px) |
| --- | --- | --- |
| `wizard_large_100.bmp`, `_150.bmp`, `_200.bmp` | Tall banner on the Welcome / Finished pages | 164×314 · 246×459 · 328×604 |
| `wizard_small_100.bmp`, `_150.bmp`, `_200.bmp` | Small logo in the top-right of the other pages | 55×58 · 83×80 · 110×106 |

The three variants are for 100 %, 150 % and 200 % display scaling.

### 1️⃣ Install build tools

```
pip install -r requirements-packaging.txt      # PyInstaller + hooks-contrib
```

### 2️⃣ Build the `.exe` with PyInstaller

```
pyinstaller attendance_app.spec --clean
```

(Or choose **2** in the `setup.bat` menu — it installs the build tools, tries to pre-download the face weights and builds.)

Look for this line in the build output:

```
[spec] Bundling DeepFace weights from: C:\Users\you\.deepface\weights
```

If you see `WARNING: DeepFace weights folder not found`, **stop** — the build will try to download weights on the client PC. If weights live elsewhere:

```
set DEEPFACE_WEIGHTS_DIR=C:\path\to\.deepface\weights
pyinstaller attendance_app.spec --clean
```

Also check for `[spec] Using application icon: ...installer\app_icon.ico` — if you see `WARNING: app icon not found`, the exe gets PyInstaller's generic icon.

Output (a **onedir** build — ship the whole folder, not just the exe):

```
dist/
└── AttendancePayrollSystem/
    ├── AttendancePayrollSystem.exe
    ├── templates/  static/  dataset/  uploads/  trained_model/
    └── deepface_weights/ + bundled Python runtime & DLLs
```

> ℹ️ The spec file itself states that it has not yet been verified end-to-end on a real build. Treat the checklist at the bottom of `attendance_app.spec` as mandatory, and test the result on another PC (next step).

### 3️⃣ Verify the build away from your dev PC

Copy `dist\AttendancePayrollSystem\` to a different folder or (ideally) a clean VM with **no Python**, then run through the checklist at the bottom of `attendance_app.spec` and [section 9](#9--check-that-everything-works): first-run DB creation, Setup Wizard, scheduler, face capture, PDF/email, antivirus behaviour.

**Also confirm your private licensing key was not bundled:**

```
dir /s /b dist\*.pem
```

This must print nothing.

### 4️⃣ Compile the installer with Inno Setup

1. Install **[Inno Setup 6](https://jrsoftware.org/isdl.php)**.
2. Make sure `dist\AttendancePayrollSystem\` exists (Inno only packages what's already on disk).
3. Compile — any one of these:
   - **`setup.bat`:** choose **2** (PyInstaller, then the installer, in one go). If Inno Setup 6 is not installed, the script says so, keeps your `dist` folder and offers to open the download page — install it, press any key in the same window and the installer step continues (no rebuild).
   - **GUI:** open `installer\AttendancePayrollSystem.iss` → **Build ▸ Compile**, or
   - **Command line** (run from the project root):

```
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\AttendancePayrollSystem.iss
```

> 💡 In **PowerShell** put `&` in front: `& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\AttendancePayrollSystem.iss`. In **CMD** use it exactly as written above.

4. Your installer appears at:

```
installer\Output\AttendancePayrollSystem-Setup-1.0.0.exe
```

**What the installer does:**

- Installs per-user to `%LOCALAPPDATA%\AttendancePayrollSystem` — **no admin rights / UAC prompt**, and the app can write its database and photos there.
- Adds a Start Menu group, an optional Desktop shortcut, and an "Add or Remove Programs" entry.
- **Uninstall keeps customer data** (database, uploads, dataset, `.env`, logs) on purpose, so a reinstall never wipes payroll history.

> ❌ Do **not** switch the install path to `Program Files` without also changing where the app stores data (`BASE_DIR` in `config.py`) — first launch would fail with permission errors. The `.iss` header explains how.

To bump the version, edit `MyAppVersion` (currently `1.0.0`) in the `.iss` file.

---

## 12. 🔑 License & Backup Management

### 🔐 How licensing works

Licenses use **Ed25519 public-key signatures**. The vendor keeps a **private key** (signs licenses); the app ships only the **public key** (verifies them). A customer can read every line of source and still cannot create a valid license.

Each license token is bound to the customer's **machine fingerprint** (a SHA-256 hash of hardware identifiers) and can be perpetual or time-limited.

**On every start, and before every request, the app decides in this order:**

```
Valid signed license found?  ──yes──►  Start normally
        │ no
        ▼
30-day trial still active?   ──yes──►  Start in Trial mode
        │ no
        ▼
Block the app: browsers are redirected to the License lock screen (/license),
API calls get a 403 "license_required" answer
```

The license token is read from the `LICENSE_TOKEN` environment variable, or from a `license.lic` file next to the application. The check **fails closed**: if it ever crashes, the app stays locked.

> ℹ️ The trial start date is stored in several places, and winding the system clock back revokes the trial. As with any offline licensing, a determined reverse-engineer of a packaged exe can always patch it out — it stops every non-technical route.

### 🧑‍💼 Vendor workflow (you)

**One-time setup — create your keypair:**

```
python licensing/keygen.py init-keys
```

Then create the vendor portal settings file (`setup.bat` already does this for you; manually it is):

```
copy licensing\.env.vendor.example licensing\.env.vendor
```

Fill in your Razorpay keys, email settings and `VENDOR_ADMIN_PASSWORD` in `licensing\.env.vendor` **only on your vendor server**. This file is git-ignored and is never bundled into customer builds. For local testing without Razorpay set `VENDOR_DEV_MODE=true` and run `python -m licensing.vendor_app`.

Back to the keys: the command above writes `licensing/vendor_private_key.pem` and prints a **public-key hex**. Embed that key in `licensing/license_manager.py` **before building any customer release**. (During development you can instead set `LICENSE_PUBLIC_KEY_HEX` in `.env`; packaged builds ignore that variable and always use the embedded key.)

> 🚨 **Never ship, email or commit `vendor_private_key.pem`.** Anyone holding it can issue unlimited licenses. Keep an offline backup. Regenerating it invalidates every license already issued. It is already listed in `.gitignore`, together with `license.lic` and `license_keys_log.txt`.

**Issuing a license manually for a customer:**

```
python licensing/keygen.py issue <MACHINE_FINGERPRINT> "Customer Name" --days 365 --edition pro
```

`--days` sets the validity period — **omit it for a perpetual license**. `--edition` is an optional tag. The command prints a token — send it to the customer.

**Selling licenses online (vendor portal).** `licensing/vendor_app.py` is a small separate Flask app with a purchase page, **Razorpay** checkout, a webhook, customer database and automatic license emails. Plans are **monthly**, **yearly** and **lifetime**; the price is always looked up on the server from `licensing/plans.py` (override with `PLAN_PRICE_MONTHLY`, `PLAN_PRICE_YEARLY`, `PLAN_PRICE_LIFETIME`, `PLAN_CURRENCY` in `.env.vendor`). Renewing a still-running plan extends from its current expiry. See `licensing/VENDOR_PORTAL_SETUP.md` and `licensing/VENDOR_SYSTEM_GUIDE.md` for deployment.

### 🙋 Customer activation

There are three equivalent ways:

1. **License lock screen** (`/license`) — shown automatically when trial/license has ended. It displays the **machine fingerprint** (with a Copy button), a **Buy a license** button (if `LICENSE_PURCHASE_URL` is set) and a box to paste the token, then **Activate**. No restart needed.
2. **Setup Wizard** — step 3 of the first-run wizard.
3. **Admin → Settings → 🔑 License Activation** card. It shows the current state: **License Valid** (customer, expiry or *Perpetual*), **Trial Mode** (days remaining) or **No License**. *(The card only displays the first 16 characters of the fingerprint as a preview; the full value is on the lock screen and in the admin-only `/settings/license-info` endpoint.)*

Alternative (no UI): save the token as **`license.lic`** next to the `.exe`, or set the `LICENSE_TOKEN` environment variable.

> ℹ️ A license is tied to one machine. If the customer changes hardware, the fingerprint changes and a new license must be issued.

The `licensing/` folder also contains the vendor-side customer database, email dispatch, and guides — see `licensing/VENDOR_SYSTEM_GUIDE.md`, `licensing/USER_MANUAL.md` and `licensing/EULA.txt`.

### 💾 Backups

All backup logic lives in `backup_manager.py` and is shared by the weekly job, the startup catch-up and the admin **Backup Now** button.

| Type | When | Where |
| --- | --- | --- |
| ⏰ **Automated weekly backup** | Every **Sunday at 02:00** while the app is running | `backups\automated_backup_<YYYYMMDD_HHMMSS>.zip` in the install folder **plus** a second copy in an external location when available |
| 🔁 **Startup catch-up backup** | About 60 seconds after startup, if the newest successful backup is older than `BACKUP_CATCHUP_DAYS` (default 7) or none exists | Same as above |
| 🖱 **Manual backup** | Any time: **Admin → Settings → Data Backup → Backup Now** | Downloaded through the browser as `attendance_backup_<timestamp>.zip` |

- **External copy:** the folder in `BACKUP_DIR` if set; otherwise (unless `BACKUP_AUTO_EXTERNAL=false`) the first usable non-system **fixed** drive such as `D:\`, in an `AttendancePayrollSystem_Backups` folder. Removable USB drives are **never** chosen automatically. A failed external copy only logs a warning — the local copy already succeeded.
- Only the **last 4** backups are kept per folder (`BACKUP_KEEP`).
- Each zip contains: `instance/attendance.db`, `uploads/`, `dataset/` (encrypted face photos) and `.env` (secrets + encryption key). Backups never contain other backups.
- Older versions saved backups in `uploads/backups/`; those files are moved to `backups\` the first time a backup runs.
- Look for `AUTOMATED BACKUP SCHEDULER REGISTERED - Every Sunday at 2:00 AM` and `Catch-up backup not needed - last backup: ...` in the logs.

> ⚠️ A same-disk copy does not protect against disk failure or theft. Point `BACKUP_DIR` at an external drive or synced cloud folder, or copy the zips away regularly. Treat backup zips like passwords: they contain `.env` and the face-encryption key.

**Restoring:** stop the app, extract the zip over the install folder (keeping the same `.env`), and start the app again.

---

## 13. 🚚 Deploying to a Client Machine

1. Give the client `AttendancePayrollSystem-Setup-<version>.exe` (built above).
2. They run it (no admin rights needed) and launch the app from the Start Menu.
3. Their browser opens to `http://127.0.0.1:5000/` and the **Setup Wizard** runs.
4. The app runs in **30-day trial** until you issue a license (see above).
5. Configure SMTP in `.env` (next to the exe) if payslip emails are needed.

### Where things live on the client

```
%LOCALAPPDATA%\AttendancePayrollSystem\
├── AttendancePayrollSystem.exe
├── .env                     ← secrets, SMTP, encryption key  (back up!)
├── license.lic              ← after activation
├── instance\attendance.db   ← all records                    (back up!)
├── dataset\                 ← encrypted face photos          (back up!)
├── uploads\                 ← payslips, profile photos       (back up!)
├── backups\                 ← automated backup zips
├── trained_model\           ← face-embedding cache (rebuilt automatically)
└── logs\                    ← rotating app logs
```

> 🔥 Losing `.env` makes every previously encrypted face photo **permanently unrecoverable**. That is inherent to encryption, not a bug.

---

## 14. 🩺 Troubleshooting & FAQs

### Setup problems

**🐍 `setup.bat` says "Python 3.10 was not found"**

Install Python 3.10.11 from python.org and tick **"Add Python to PATH"**. Python 3.11, 3.12 and 3.13 will **not** work with TensorFlow 2.15. Check with `py -3.10 --version`.

**📦 `pip install -r requirements.txt` fails (especially on tensorflow)**

You are probably not on Python 3.10 or not on 64-bit Windows. Delete the `venv` folder and run `setup.bat` again. Also make sure you have enough free disk space (about 5 GB) and a stable internet connection.

**🚫 `python -c "import cv2, tensorflow, ..."` fails**

Check `python --version` is 3.10.x, use a fresh venv, and make sure `numpy` is 1.26.4 (not 2.x) and only `opencv-contrib-python` is installed:

```
pip uninstall -y opencv-python opencv-python-headless
pip install --force-reinstall --no-deps opencv-contrib-python==4.8.1.78
```

Fix this before attempting any PyInstaller build.

**📝 The install shows "112 packages" but my `requirements.txt` is much shorter**

Normal. TensorFlow, DeepFace and MediaPipe depend on many other packages, and pip lists all of them.

**🎨 The pages look plain / unstyled**

The UI files in `static\vendor\` are not stored in GitHub. Run `python scripts\download_vendor_assets.py` (it needs internet). `setup.bat` does this for you.

### 🧩 `cv2` errors: `module 'cv2' has no attribute 'imread'` / "Attendance Not Marked"

**Symptom:** it worked yesterday, but today the **My Attendance** page shows *"Attendance Not Marked — Reason: Error: module 'cv2' has no attribute 'imread'"* and the kiosk (`/`) marks nothing. The log shows `POST /api/auto-scan-attendance 200` repeatedly but nobody is recorded.

**Cause:** your code is fine — the OpenCV install inside the venv is damaged. `import cv2` finds an *empty* `cv2` folder (or a stray `cv2` folder / `cv2.py` in the project), so every `cv2.*` call fails. It usually happens after two OpenCV packages (`opencv-python`, `opencv-python-headless`, `opencv-contrib-python`) were installed together and one was uninstalled, or after an interrupted `pip install`.

**Fix — no need to delete the venv.** With the venv active:

```
pip uninstall -y opencv-python opencv-python-headless opencv-contrib-python opencv-contrib-python-headless
Remove-Item -Recurse -Force .\venv\Lib\site-packages\cv2 -ErrorAction SilentlyContinue
pip install --no-cache-dir opencv-contrib-python==4.8.1.78
pip install numpy==1.26.4
python -c "import cv2; print(cv2.__version__, cv2.imread)"
```

The last command must print `4.8.1 <built-in function imread>`. Then restart `python app.py`. ("Skipping … as it is not installed" warnings in the first command are normal.)

If it still fails, check for something shadowing OpenCV:

```
python -c "import cv2; print(cv2.__file__)"
dir cv2*
```

If `cv2.__file__` is `None`, or `dir cv2*` lists a `cv2` folder / `cv2.py` in the project folder, delete or rename it. As a last resort, rename `venv` to `venv_old`, create a new one and run `pip install -r requirements.txt` again.

### 🧹 `WARNING: Ignoring invalid distribution -andas` (or `~umpy`, `~il`, …)

**Cause:** an interrupted `pip install` or uninstall left half-removed folders whose names start with `~` inside `venv\Lib\site-packages` (for example `~andas`, `~umpy`, `~il`).

**Fix:**

1. List them:

   ```
   Get-ChildItem .\venv\Lib\site-packages -Directory -Filter "~*"
   ```

2. Check that the real packages still import:

   ```
   python -c "import numpy, PIL, sqlalchemy, markupsafe, pikepdf, pandas, cv2; print('ALL OK')"
   ```

3. Only if that prints `ALL OK`, delete the leftovers (only names starting with `~` are matched; real packages are untouched):

   ```
   Remove-Item -Recurse -Force ".\venv\Lib\site-packages\~*"
   ```

4. If a package was damaged, reinstall it at its **pinned** version, e.g. `pip install --force-reinstall --no-deps pandas==2.2.3`, then run `pip check`.

> Prevention: never interrupt `pip` (Ctrl+C / closing the window) while it is installing, and install with the pinned versions from `requirements.txt`.

### ℹ️ `pip check` says `deepface requires opencv-python`

`deepface` and `retina-face` list plain `opencv-python` as a dependency, but this project deliberately uses `opencv-contrib-python`, which provides the same `cv2` module. The message is **harmless**. Do **not** "fix" it by installing `opencv-python` — that brings the two-OpenCV conflict back.

### 🖼 The `.exe` icon looks small or blurry in Explorer

`installer\app_icon.ico` contains only one small size. Rebuild it as a multi-size icon (16–256 px) as described in [Application icon & installer images](#-application-icon--installer-images), then rebuild the exe. Windows caches icons, so rename the exe or restart the PC if the old one still shows.

### Licensing

**🔑 The app shows the License lock screen / closes with "LICENSE VALIDATION FAILED"**

The trial has ended (or the license is invalid/expired) and no valid license was found. Copy the **machine fingerprint** from the lock screen (or console message), send it to the vendor, and paste the returned token into the box on the lock screen — or place it in `license.lic` next to the exe (or set `LICENSE_TOKEN`). A token issued for a different PC, or signed by a different key than the one built into the app, will not verify.

**🔑 "License activation failed" when pasting a token**

- Paste the **entire** token (`xxxx.yyyy`, no spaces or line breaks).
- The token must be issued for **this machine's** fingerprint.
- The app's public key must match the vendor private key used to sign it.
- Check whether the license has expired.

### Files, camera and face recognition

**🔒 `unable to open database file` / photos or payslips not saving**

The app is installed in a write-protected folder (e.g. `Program Files`). Reinstall to `%LOCALAPPDATA%\AttendancePayrollSystem\` (the installer's default). Running as Administrator is a last resort only.

**📷 Camera doesn't open / black preview**

1. **Settings ▸ Privacy & security ▸ Camera** → enable **"Let desktop apps access your camera"** (and allow the browser to use the camera for the kiosk / My Attendance pages).
2. Close Zoom / Teams / the Windows Camera app — only one program can use the camera.
3. On PCs with multiple cameras, the wrong device index may be used (`cv2.VideoCapture(0)` vs `1`); adjust `FaceCapture.start_capture()` in `ai_engine.py`.
4. If the Windows Camera app also fails, it's a driver problem.

**🐌 First face registration is slow, hangs or fails**

On a normal PC it is downloading the model files (about 100 to 300 MB). Keep the internet on and wait. On an **offline PC** the build didn't include DeepFace weights: rebuild after populating `%USERPROFILE%\.deepface\weights` (Step 7) and confirm the `[spec] Bundling DeepFace weights` line appears.

**🙅 The face is not recognised / shows "Unknown"**

Matching is intentionally strict (distance ≤ 0.30 and a 0.05 margin over the next-best employee). Make sure the employee has at least `MIN_FACE_IMAGES_REQUIRED` good, well-lit photos from slightly different angles, that the face model was **trained/refreshed** after registration, and that the camera image is not too dark. Look-alikes may be rejected on purpose.

**⛔ "Face does not match your profile" on My Attendance**

The employee-login page only accepts the face of the logged-in employee. Log in with the right account, or re-register that employee's photos.

### Scheduler and operations

**⏰ Payroll / auto-logout / backups didn't run**

1. Check the logs for `Payroll scheduler started`, `DAILY APPROVAL SCHEDULER REGISTERED - 23:59`, and `AUTOMATED BACKUP SCHEDULER REGISTERED`. If missing, look for `Failed to start scheduler:`.
2. The scheduler only runs **while the exe is running** — there is no background service. Closing the browser tab does *not* stop the app; closing the exe does.
3. If the PC was **off or asleep** at the scheduled time the job is skipped, but it is backfilled on the next start: payroll (`PAYROLL RECONCILIATION CHECK`), logout approval requests (`AUTO LOGOUT RECONCILIATION`) and backups (`Startup Catch-up Backup Check`, if the newest backup is older than 7 days). You can also use **Backup Now** any time.
4. In a custom PyInstaller spec, keep `copy_metadata('APScheduler')` — removing it makes the scheduler fail silently.

**✉️ Payslip emails are not sent**

Fill in the `MAIL_*` settings in `.env`. For Gmail use an **App Password**, not your normal password. Everything else works without email.

**❓ The exe keeps running after I close the browser tab**

Expected — the Flask server and scheduler run inside the exe. End the `AttendancePayrollSystem.exe` process (Task Manager) to fully quit.

### Packaging

**🛡 Windows Defender / SmartScreen blocks the exe**

Very common for PyInstaller apps bundling TensorFlow/OpenCV. In order of effectiveness: **code-sign** the exe/installer; keep `upx=False`; submit the file to [Microsoft for analysis](https://www.microsoft.com/en-us/wdsi/filesubmission); as a stopgap, have IT add an exclusion for the install folder.

**💥 `ModuleNotFoundError` / `DLL load failed` only in the built exe**

A hidden import or data file wasn't bundled. Ensure `pyinstaller-hooks-contrib` is installed, add the missing module to `hiddenimports` in `attendance_app.spec`, rebuild with `--clean`, and temporarily set `console=True` **on your own machine** to see the traceback.

**🧱 Inno Setup says a `wizard_*.bmp` file is missing**

Keep all six images in `installer\` (see [Application icon & installer images](#-application-icon--installer-images)). Don't delete them.

### Other questions

**❓ Can I use MySQL instead of SQLite?**

Yes. Set `DATABASE_URL=mysql+pymysql://user:password@host/dbname` in `.env`. Note that the built-in backup zips only include the SQLite file, so back up a MySQL database with your own tooling.

**❓ Can I change the port (5000)?**

The host/port are set in the `__main__` block of `app.py` (`SERVER_HOST`, `SERVER_PORT`).

**❓ What is the payslip PDF password?**

If the admin (or the employee, from the portal) set a custom payslip password, that is used. Otherwise a default is derived from the employee's own details: first 2 letters of the name (uppercase) + last 4 digits of the phone number + date of birth as `DDMM` + last 2 characters of the employee ID (e.g. `VA3210150301`). If there is no date of birth on file, the joining date is used.

---

## 15. 🗂 Project Structure

| Path | Purpose |
| --- | --- |
| `app.py` | Flask app creation/config, extensions, blueprint registration, error handlers, startup block (license check → face model → server) |
| `launcher.py` | PyInstaller entry point that runs `app.py` |
| `config.py` | Environment-aware config, frozen-safe `BASE_DIR`, logging |
| `database.py` · `models.py` · `extensions.py` | DB init/migrations, ORM models (Admin, Employee, Attendance, AttendanceActivity, Payroll, Settings, LogoutApprovalRequest, BiometricConsentLog, …), Flask extensions |
| `auth_routes.py` · `attendance_routes.py` · `payroll_routes.py` · `reports_routes.py` · `approvals_routes.py` · `settings_routes.py` · `employees.py` | Blueprints (every route lives in one of them) |
| `auth_decorators.py` · `auth_helpers.py` · `file_helpers.py` | Login/role decorators, admin-creation helpers, safe file handling |
| `setup_wizard.py` | First-run admin / company / license setup |
| `attendance.py` · `payroll.py` | Attendance-rule and payroll engines |
| `ai_engine.py` · `face_recognition_singleton.py` | Face detection/recognition, strict matching, presence tracker, embedding cache |
| `crypto_utils.py` | Encryption for face photos and stored payslip passwords |
| `pdf_generator.py` · `email_service.py` | PDF payslips/reports (AES-256), SMTP delivery |
| `scheduler_service.py` | APScheduler: monthly payroll, 23:59 auto-logout approvals, weekly backup, startup reconciliation and catch-up |
| `backup_manager.py` | Backup creation, external copy, pruning and catch-up detection |
| `services/` | `admin_reports_service`, `approval_service`, `attendance_calculator`, `attendance_stats`, `app_services` |
| `licensing/` | Customer side: `license_manager.py` (Ed25519, trial, fingerprint), `client_security.py` (license gate + lock screen). Vendor side: `keygen.py`, `vendor_app.py` (Razorpay portal), `plans.py`, `payment_gateway.py`, `customer_*`, `license_email_service.py`, guides and `EULA.txt` |
| `installer/` | Inno Setup script (`.iss`), `app_icon.ico`, six `wizard_*.bmp` images and icon instructions |
| `scripts/` | `download_vendor_assets.py`, `prepare_deepface_weights.py`, `verify_icon.py`, `fix_cdn_links.py`, `update_contact_details.py` |
| `attendance_app.spec` | PyInstaller build specification |
| `setup.bat` | One-click environment setup with a menu: run the app or build the `.exe` |
| `templates/` · `static/` | Jinja2 views and CSS/JS/images (`static/vendor/` is filled by the download script) |
| `tests/` · `conftest.py` | pytest suite (600+ tests) with stubbed ML layer |
| `.github/workflows/ci.yml` | GitHub Actions: import check + pytest on every push/PR |
| `instance/` · `dataset/` · `uploads/` · `backups/` · `trained_model/` · `logs/` | Runtime data (git-ignored) |
| `requirements.txt` · `requirements-packaging.txt` | Runtime vs build-only dependencies |
| `.env.example` | Template for your `.env` settings file |

---

## 16. 🤝 Contributing & Support

1. ✅ Confirm `pytest` passes on a clean checkout before you start.
2. 🔁 Run the relevant test module often while developing (`pytest tests/test_<area>.py -v`).
3. 🧪 Run the full suite before opening a PR; if you touched packaging, walk the [pre-build checklist](#-pre-build-checklist) and make a test build.
4. 🐞 When reporting an issue include: exact console output/log lines, whether it happens in `python app.py` or **only** in the packaged exe, and what you already tried from [Troubleshooting](#14--troubleshooting--faqs).

**Author:** Vaishnavi Maruti Lekawale · **Repository:** [AI-Attendance-Payroll-System](https://github.com/vaishnavilekawale/AI-Attendance-Payroll-System)

---

**AI Attendance & Payroll System** — built with ❤️ using Flask, DeepFace and Python.