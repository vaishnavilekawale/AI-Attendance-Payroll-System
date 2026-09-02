# 🤖 AI Employee Attendance Monitoring and Payroll Management System

A complete AI-powered Employee Attendance Monitoring and Payroll Management System built with Python, Flask, DeepFace (FaceNet512), OpenCV/MediaPipe, and SQLite. The system runs as a walk-up kiosk that recognizes employees by face, falls back to a password-verified manual entry with a full manager/admin approval workflow when a face can't be confidently matched, computes attendance status from one shared rule engine, and drives an end-to-end payroll pipeline — from configurable allowances/deductions through AES-256-encrypted PDF payslips to deliverability-conscious email delivery.

## Project Overview

The AI Employee Attendance Monitoring and Payroll Management System is a three-role platform (**Admin**, **Manager**, **Employee**) for modern HR management. It leverages AI face recognition to automate attendance tracking at a public kiosk, enforces strict anti-proxy safeguards — including a hidden-until-approved review cycle — when face recognition can't be used, and ties every attendance record into a single, consistent rule engine that drives dashboards, reports, and payroll alike. The system features a responsive admin dashboard, a manager approval console, a full employee self-service portal, versioned/auditable settings, detailed reports, and automated payroll with encrypted payslip delivery.

## Features

### Attendance & Recognition
- **Public Kiosk Landing Page**: The root URL (`/`) is a public, no-login kiosk screen (`home_attendance.html`) with continuous, real-time camera scanning — anyone can walk up and mark attendance immediately. Admin and Employee login remain one click away via an unobtrusive link, rather than gating the home screen behind a session check.
- **AI Face Recognition**: Continuous camera scanning using DeepFace with the FaceNet512 embedding model, with MediaPipe available as a secondary face-detection backend — no manual "capture" click required once the camera starts.
- **Multi-Face, Strict-Match Detection**: Every face detected in a frame is analyzed independently, so multiple people can be recognized in a single scan. Matching uses a hard-capped cosine-distance tolerance plus a minimum confidence margin between the best and second-best candidate, so an ambiguous or borderline face is reported as "Unknown" rather than guessed.
- **Frame-Presence Locking (No Duplicate Punches)**: A server-side presence tracker (`AttendancePresenceTracker`) ensures an employee is logged **exactly once** per continuous appearance in front of the camera — even if they stand there for hours — and only allows a new attendance attempt after they've genuinely left the frame (configurable absence timeout) and returned. The admin kiosk and the employee self-service stream each keep their own, independently isolated presence lock.
- **Secure Manual Fallback (Proxy-Attendance Prevention)**: If face recognition can't confidently identify someone, `/mark_manual_attendance` requires **both** the employee's Employee ID **and** their account password, verified live against the `EmployeeLogin` table (`EmployeeLogin.check_password()`, with valid-temporary-password support). Employee-ID-only fallback does not exist, so no one can punch in on a colleague's behalf just by knowing their ID. Invalid ID, wrong password, and inactive account all return the exact same generic error, so the endpoint can't be used to enumerate valid Employee IDs.
- **Hidden-Until-Approved Manual Attendance Workflow**: Every manual (password) punch starts life as `approval_status = 'pending'` and is invisible to normal reporting until a Manager or Admin approves or rejects it. While pending, the employee cannot mark OUT or submit another request for the day. A rejected request re-opens a same-day retry window (up to office end time) so the employee can correct and resubmit; once approved, later IN/OUT actions for that day proceed normally without resetting back to "pending."
- **Full Attendance Audit Trail**: Every `Attendance` row carries `attendance_type` (`FACE_RECOGNITION` or `MANUAL_PASSWORD`) and, for manual entries, `approval_status` (`pending` / `approved` / `rejected`) plus a `submission_timestamp` recording the exact moment the employee clicked "Mark Attendance" — so HR can always audit exactly how and when a given day's attendance was captured.
- **One Rule Engine, No Hardcoded Status**: Whether attendance is captured via camera or the manual fallback, both paths call the same core engine (`attendance.py`'s `AttendanceManager.mark_attendance()`), which evaluates real office shift timing to compute the correct status — Present, Late, or Half-Day — rather than ever hardcoding a result. A manual punch made after hours is correctly shown as "Late," never force-set to "Present."
- **Automatic Working Hours Calculation**: Computed in the background from IN/OUT timestamps as soon as a punch is recorded.
- **Automatic Late Entry Detection**: Configurable grace period after office start time; an employee can be "Present" and "Late" simultaneously.
- **Automatic Absent Detection**: Employees with no attendance record are marked absent after office end time, with no manual intervention, consistently across dashboard, recent-attendance table, and reports.
- **Overtime Calculation**: Configurable overtime rate applied to hours worked beyond the standard workday.
- **Auto-Logout Regularization**: A daily job closes out anyone still clocked-in at day's end and raises a manager-review request rather than silently guessing an OUT time; a rejected regularization request blocks further attendance marking for that employee until resolved.
- **Employee Self-Service Attendance**: A separate login-gated attendance stream (`employee_attendance.html`) for employees to mark their own IN/OUT via face recognition, with its own independent frame-presence lock.

### Roles & Approval Workflows
- **Three Roles, One App**: `Admin`, `Manager` (an `Employee` whose `designation` is "Manager"), and `Employee`, each with their own dashboard and permission scope.
- **Manager Approval Console** (`manager_approvals.html`): Managers review and approve/reject two independent queues — auto-logout regularization requests and pending manual (password-fallback) attendance requests for their scope — with pending/approved/rejected tabs and IST-adjusted timestamps.
- **Admin Approval Console** (`admin_approvals.html`): A parallel, org-wide view of the same logout-regularization and pending-manual-attendance queues for administrators.
- **Editable Attendance with Trail**: Dedicated admin (`admin_edit_attendance.html`) and manager (`manager_edit_attendance.html`) screens for correcting an attendance record after the fact, alongside the approval flows.
- **Email Notifications at Every Step**: Manual-attendance submission notifies the employee (and the Admin, if the submitter is a Manager); logout-regularization requests notify both the employee and their manager, with duplicate-send protection built into the data model.

### Employee & Payroll Management
- **Complete Employee Lifecycle Management**: Add/edit employees with department, designation, joining date, date of birth, bank details, and profile photo. All numeric fields default safely to `0.0` if left blank, and the Edit form always pre-populates existing values so nothing is accidentally cleared on save.
- **Statutory Details**: PAN Number, UAN Number, and PF Account Number captured per employee and displayed on the generated payslip, with a clean "N/A" fallback if any are missing.
- **Configurable Salary Allowances**: HRA, DA, Medical Allowance, Travel Allowance, Special Allowance, and Other Allowances — each stored per-employee and dynamically summed into gross salary.
- **Configurable Salary Deductions**: Employee/Employer PF %, ESIC %, TDS % (of earned gross), Bus/Transport Charges, Other Deductions, plus Professional Tax, LOP, and Late deductions — all dynamically summed into total deductions.
- **Automated Payroll Engine**: `Gross Salary = Basic + HRA + DA + Medical + Travel + Special + Other Allowances`, `Net Salary = Gross Salary − Total Deductions`, recalculated from live employee and attendance data every time payroll runs — never cached or hand-adjusted.
- **Safe Cascade Delete**: Deleting an employee cleanly removes every dependent record first — `LogoutApprovalRequest`, `AttendanceActivity`, `EmployeeLogin`, `Attendance`, and `Payroll` — in the correct order, preventing SQLite NOT NULL / foreign-key integrity errors.
- **Employee Self-Service Portal**: Employees get their own login and dashboard (`employee_dashboard.html`), profile page (`employee_profile.html`), payroll/payslip view (`employee_payroll.html`), and personal attendance reports with export (`employee_reports.html`), plus a dedicated employee-side forgot/change-password flow.

### PDF Payslips & Email
- **Password-Protected PDF Payslips**: Clean, corporate-styled payslips built with ReportLab, then encrypted with `pikepdf` (AES-256) so only the individual employee can open their own file.
- **Deterministic Password Rule**: First 4 uppercase letters of the employee's name + date of birth (DDMM), with a safe fallback to Employee ID + DOB (or joining date, if DOB isn't on file) — computed on demand, never stored anywhere.
- **Deliverability-Conscious Email Service**: Proper nested MIME structure (`multipart/mixed` → `multipart/alternative` with plain-text + HTML, attachments as siblings rather than mixed into the alternative part), spam-safe subject lines, and correctly content-typed PDF attachments.
- **Password Never Sent By Email**: The payslip email explains the password *rule* with a fixed, generic worked example — the recipient's actual password is never included in the email body in any form.
- **Full Notification Suite**: Payslip delivery, attendance report delivery, admin/employee password resets and welcome emails, manual-attendance submission notices, and logout-regularization notices to both employee and manager, all via one deliverability-conscious `EmailService`.

### Reporting & Dashboard
- **Consistent Reporting Engine**: Admin Reports' summary cards, department analytics, rankings, and the per-employee summary table all share the exact same validity filtering and status-classification logic (`services/admin_reports_service.py`), so the numbers always agree with each other — no more mismatched Absent counts between sections.
- **Dashboard Analytics**: Real-time Present/Absent/Half-Day/Late cards and interactive charts, computed via the same shared aggregation logic used by Reports.
- **Department-wise Statistics**: Attendance breakdown by department.
- **PDF Export**: Both admin reports and individual employee attendance reports can be exported as branded PDFs (`generate_admin_reports_pdf`, `generate_attendance_report`).

### Configurable, Versioned Settings
- **Attendance Settings with History**: Office start/end time, grace period, working hours per day, and half-day threshold are editable from the admin **Settings** page and stored with an `effective_from` timestamp (`AttendanceSettingsHistory`) — past attendance is always evaluated against the rules that were actually in force on that date, not today's rules.
- **Payroll Settings**: A dedicated **Payroll Settings** page (`payroll_settings.html`) controls the automatic monthly payroll generation day/time, whether payslip emails auto-send, and month-by-month Professional Tax slabs (`PayrollSettings`).
- **Company Settings**: Company name, address, phone, email, website, and logo used across the UI and generated PDFs (`CompanySettings`), editable without touching code.

### Automation
- **Scheduled Auto-Logout Regularization**: A daily background job (APScheduler, 23:59) closes out anyone still clocked in and raises a regularization request for manager/admin review.
- **Scheduled Monthly Payroll**: Runs automatically on the last day of every month at a configurable time, generating payroll and distributing encrypted payslips via email when auto-send is enabled; missed runs are reconciled on next startup.

### Platform
- **Login System**: Secure username/password authentication for admins, with a separate employee login backed by its own `EmployeeLogin` credentials table (including forced password change on first login and temporary-password support with expiry).
- **Mobile Responsive UI**: Bootstrap 5 responsive design across desktop, tablet, and mobile.

## Tech Stack

### Backend
- Python 3.10+
- Flask (Web Framework)
- SQLAlchemy (ORM)
- Session-based authentication (Admin / Employee / Manager roles)

### AI / Computer Vision
- DeepFace (Face Recognition)
- FaceNet512 (Embedding Model)
- OpenCV + OpenCV-Contrib (Image Processing)
- MediaPipe (secondary face-detection backend)
- TensorFlow / tf-keras (DeepFace backend)
- NumPy (Numerical Computing)

### Frontend
- HTML5, CSS3
- Bootstrap 5 (UI Framework) + Bootstrap Icons
- Vanilla JavaScript (`fetch()`-based AJAX, no page reloads for attendance actions)
- Chart.js (Data Visualization)

### Database
- SQLite (Default)
- MySQL (Optional, via PyMySQL)

### Other Libraries
- ReportLab (PDF layout/generation)
- pikepdf (AES-256 PDF password protection)
- qrcode (payslip/report QR support)
- APScheduler (background job scheduling)
- smtplib (SMTP email, standard library)
- Werkzeug (password hashing/security)
- Flask-WTF / WTForms (forms, CSRF)

## Project Structure

```
attendance_ai/
│
├── app.py                        # Main Flask application & routes (admin, manager, employee, APIs)
├── config.py                     # Base configuration / environment defaults
├── database.py                   # Database initialization
├── models.py                     # DB models (Employee, Attendance, Payroll, Settings, PayrollSettings,
│                                  #   AttendanceSettingsHistory, CompanySettings, LogoutApprovalRequest, ...)
├── ai_engine.py                   # Face detection (DeepFace/MediaPipe), matching, frame-presence tracking
├── attendance.py                   # Core attendance rule engine (status/late/half-day/auto-checkout logic)
├── payroll.py                       # Payroll calculation engine (allowances + deductions)
├── email_service.py                  # SMTP email service (MIME-correct, spam-conscious, full notification suite)
├── pdf_generator.py                   # Payslip/report PDF layout + AES-256 password protection helper
├── scheduler_service.py                 # APScheduler jobs: auto-logout, monthly payroll, reconciliation
├── requirements.txt               # Python dependencies
├── README.md                      # This file
├── .env                          # Environment variables (SMTP, DB, office timing, etc.)
├── instance/attendance.db          # SQLite database (auto-generated)
│
├── dataset/                      # Face images for training, organized by employee ID
├── trained_model/                 # Cached face embeddings
├── payrolls/<year>/<month>/         # Generated, password-encrypted PDF payslips
├── uploads/                      # Uploaded files (photos, generated payslip/report copies)
│
├── services/
│      approval_service.py          # Regularization / manual-attendance approval logic
│      attendance_calculator.py      # Present / Late / Half-Day timing rules
│      attendance_stats.py            # Aggregated attendance statistics helpers
│      admin_reports_service.py        # Admin Reports single-source-of-truth aggregation
│
├── static/
│      css/style.css              # Custom styles
│      js/main.js                  # JavaScript functions
│      images/company_logo_MD.jpg    # Company logo
│
└── templates/
       home_attendance.html        # Public kiosk landing page (default entry point)
       login.html                  # Admin/Employee login page
       register.html               # Admin registration page
       forgot_password.html        # Admin password reset
       change_password.html        # Admin change password
       employee_forgot_password.html  # Employee password reset
       dashboard.html              # Admin dashboard
       employee_dashboard.html     # Employee self-service dashboard
       add_employee.html           # Add/Edit Employee modals (allowances, deductions, statutory details)
       attendance.html             # Admin camera scan + secure manual fallback
       employee_attendance.html    # Employee self-service attendance stream
       employee_profile.html       # Employee self-service profile
       employee_payroll.html       # Employee self-service payslips
       employee_reports.html       # Employee self-service attendance reports
       admin_approvals.html        # Admin regularization / manual-attendance review queue
       manager_approvals.html      # Manager regularization / manual-attendance review queue
       admin_edit_attendance.html  # Admin manual attendance correction
       manager_edit_attendance.html  # Manager manual attendance correction
       payroll.html                # Payroll management
       payroll_settings.html       # Payroll automation & professional-tax settings
       reports.html                # Admin reports
       settings.html               # Attendance / company settings
```

## Installation Guide

### Prerequisites

- Python 3.10 or higher
- pip (Python package manager)
- Virtual environment (recommended)

### Step 1: Clone the Project

```bash
git clone https://github.com/vaishnavilekawale/AI-Attendance-Payroll-System
cd attendance_ai
```

### Step 2: Create Virtual Environment

```bash
python -m venv venv
```

### Step 3: Activate Virtual Environment

**Windows:**
```bash
venv\Scripts\activate
```

**Linux/Mac:**
```bash
source venv/bin/activate
```

### Step 4: Install Dependencies

```bash
pip install -r requirements.txt
```

**Note:** Installing DeepFace, TensorFlow, MediaPipe, and pikepdf may take some time as they pull in additional native dependencies.

### Step 5: Configure Environment Variables

Create a `.env` file in the project root and configure:

```env
SECRET_KEY=your-secret-key-change-in-production
FLASK_ENV=development

# Database Configuration
DATABASE_URL=sqlite:///attendance.db

# Email Configuration (SMTP)
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=your-email@gmail.com
MAIL_PASSWORD=your-app-password
MAIL_DEFAULT_SENDER=your-email@gmail.com

# Company Settings (also editable later from the Settings page)
COMPANY_NAME=AI Attendance System
COMPANY_LOGO=static/images/company_logo.png

# Office Timing (also editable later from the Settings page, versioned per change)
OFFICE_START_TIME=09:00
OFFICE_END_TIME=18:00
GRACE_PERIOD_MINUTES=15

# Working Hours
WORKING_HOURS_PER_DAY=9.0

# Salary Calculation
LATE_DEDUCTION_ENABLED=false
LATE_DEDUCTION_PER_OCCURRENCE=0.0

# Overtime Calculation
OVERTIME_ENABLED=true
OVERTIME_RATE=1.5

# Face Recognition Settings
FACE_RECOGNITION_TOLERANCE=0.6
MIN_FACE_IMAGES_REQUIRED=20
```

### Step 6: Database Setup

The database is created automatically on first run:

```bash
python -c "from app import app, db; app.app_context().push(); db.create_all()"
```

Default admin credentials:
- Username: `admin`
- Password: `admin123`

**Important:** Change the default password after first login!

### Step 7: Run the Application

```bash
python app.py
```

The application will start on `http://localhost:5000`, opening directly on the **public kiosk landing page**. Admin and Employee login links are available from there.

## AI Workflow

### Face Registration

1. Register the employee in the system with their basic details.
2. Navigate to employee management and open face capture.
3. Set the number of face images to capture (minimum 20 recommended).
4. Start capture — ensure good lighting and a clearly visible face.
5. Images are stored under `dataset/<employee_id>/`.

### AI Training

1. Navigate to Settings (or trigger `/train-ai` / `/api/train-face-model`).
2. DeepFace processes all face images per employee and FaceNet512 generates 512-dimensional embeddings.
3. Embeddings are cached under `trained_model/` for fast comparison at recognition time, with an in-memory cache layer for repeated lookups.

### Face Recognition & Matching

1. The kiosk landing page starts the camera automatically — no manual "Start Camera" step and no Employee ID prompt.
2. Every ~1.5–2 seconds, a frame is sent to the recognition engine.
3. **Every face detected in that frame** is embedded and compared independently against all trained employees (multi-person aware).
4. A match is only accepted if the best candidate's distance is below a strict, hard-capped tolerance **and** clearly beats the second-best candidate by a minimum margin — otherwise the face is reported "Unknown" rather than guessed.
5. A server-side presence tracker (`AttendancePresenceTracker`) records who is currently "in frame" so a matched employee is logged only once per continuous presence, regardless of how many frames they appear in.

## Attendance Workflow

### IN Punch

1. Employee's face is recognized (or they authenticate via the secure manual fallback).
2. Current time is recorded as IN Time; status is computed by the rule engine (Present or Late, based on office start time + grace period).
3. Duplicate IN punches for the same continuous presence are blocked by the presence tracker.

### OUT Punch

1. Employee is recognized again (or re-authenticates manually) later in the day.
2. Current time is recorded as OUT Time; working hours are calculated automatically (OUT − IN).
3. OUT can only be marked after a matching IN for the same day, and is blocked entirely while a manual IN for that day is still pending approval.

### Secure Manual Fallback (When Face Recognition Fails)

1. The "Face Not Recognized" card asks for **both** Employee ID and account password.
2. `/mark_manual_attendance` verifies the password live against `EmployeeLogin.check_password()` (the same mechanism used by the real employee login page, including temporary-password support) — never a hardcoded or cached comparison.
3. On success, the record is created/updated through the same `AttendanceManager.mark_attendance()` engine the camera flow uses — status is computed honestly (Present/Late/Half-Day), never force-set to "Present."
4. The resulting `Attendance` row is tagged `attendance_type = 'MANUAL_PASSWORD'` and starts as `approval_status = 'pending'` for audit and review purposes.
5. The submission is **hidden from normal counts until a Manager or Admin approves it**. Until then, the employee cannot mark OUT or submit a second request that day.
6. If rejected, the employee can correct and resubmit for the same day up until office end time; past that, the retry window closes and they must contact a manager or admin.
7. Any authentication failure (wrong ID, wrong password, inactive account) returns the same generic message, so the endpoint can't be used to enumerate valid Employee IDs.

### Manager / Admin Approval

1. Pending manual-attendance punches and auto-logout regularization requests both surface in the Manager Approvals and Admin Approvals consoles.
2. Approving a manual punch makes it count normally everywhere (dashboard, reports, payroll); rejecting it opens the same-day retry window described above.
3. A rejected logout-regularization request marks that day Absent and blocks further attendance attempts for the employee that day.

### Working Hours Calculation

- Calculated automatically from IN/OUT timestamps, stored in decimal hours (e.g., 8.5).

### Late Entry Detection

- Configurable grace period (default 15 minutes) after office start time.
- Late entry is tracked as its own flag alongside status — an employee can be "Present" and "Late" simultaneously.

### Automatic Absent Detection

- Before office end time: employees without attendance are not yet marked absent.
- After office end time: any employee with no attendance record for the day is automatically marked absent — no manual step needed. Applies consistently across the dashboard, recent-attendance table, and reports.

### Overtime Calculation

- Applied when working hours exceed the configured working hours per day, at a configurable overtime rate (default 1.5×).

## Payroll Workflow

### Monthly Calculation

1. Navigate to Payroll, select month and year, and trigger calculation.
2. The system processes all active employees for the selected period, pulling live attendance and employee data — no cached figures.

### Attendance Analysis

- Present Days, Absent Days, Half Days, Late Days, Total Hours Worked, and Overtime Hours are all derived from the same attendance records used everywhere else in the system (dashboard, reports, payroll) — kept consistent by a shared aggregation approach.

### Salary Calculation

```
Per-Day Salary        = Basic Salary / Working Days in Month
Absent Deduction      = Absent Days × Per-Day Salary
Half-Day Deduction    = Half Days × (Per-Day Salary / 2)
Late Deduction        = Late Days × Late Deduction Amount (if enabled)
Overtime Bonus        = Overtime Hours × Hourly Rate × Overtime Rate

Base Gross Salary     = Basic + HRA + DA + Medical + Travel + Special + Other Allowances
Total Gross Earnings  = Base Gross Salary + Overtime Bonus

Employee PF           = Basic × Employee PF% (employee-configured)
Employer PF           = Basic × Employer PF% (employee-configured, reported for CTC only)
ESIC                  = Earned Gross × ESIC% (employee-configured)
TDS                   = Earned Gross × TDS% (employee-configured)
Professional Tax      = Month-wise slab from Payroll Settings

Total Deductions      = Absent + Half-Day + Late Deduction
                        + Employee PF + ESIC + Professional Tax
                        + TDS + Bus/Transport Charges + Other Deductions

Net Salary            = Total Gross Earnings − Total Deductions
```

### Payslip Generation

1. Trigger PDF generation for an employee from the Payroll page.
2. ReportLab builds a professional payslip with:
   - Company header (name, address, phone, email, website — pulled from Company Settings)
   - Employee details: Employee ID, Name, Department, Designation, Location, Pay Period, **PAN Number, UAN Number, PF Account Number** (each falling back to "N/A" if not on file)
   - Attendance summary (working/present/absent/half-day/late/paid/LOP days)
   - Full earnings and deductions breakdown, with Net Pay in words
3. The finished PDF is immediately encrypted with `pikepdf` using a password derived from the employee's name + date of birth — no plain PDF is ever left on disk when protection is requested.
4. The payslip path is stored against the Payroll record, under `payrolls/<year>/<month>/`.

### Email Delivery

1. Trigger email delivery for an employee from the Payroll page.
2. The email is built with a proper `multipart/mixed` → `multipart/alternative` (plain-text + HTML) structure, with the PDF attached as a correctly content-typed sibling part — good practice for inbox placement.
3. The subject line follows a clean, non-spammy format: `Payslip for {Month} {Year} - {Company Name}`.
4. The email explains **how** to unlock the password-protected PDF (name + DOB rule, with a fixed generic example) — it never contains the recipient's actual password.
5. Delivery status is recorded against the Payroll record.

## Dashboard Module

### Overview Cards

- **Present Today**, **Half Day Today**, **Absent Today** (including automatic absences), **Late Today**, **Total Employees** — all computed via the same shared aggregation logic used by Reports, so these numbers never disagree with the detailed reports.

### Charts

- **Today's Attendance Overview**: Doughnut chart (Present / Half-Day / Absent / Late).
- **Department-wise Attendance**: Bar chart by department.

### Recent Attendance Table

- Latest attendance records with employee name, department, IN/OUT time, working hours, status, and which channel captured it — face recognition or manual password fallback (with its approval state where applicable).

## Reports Module

### Report Generation

1. Select a date range, and optionally filter by department, employee, designation, or status.
2. Generate the report — available from both the Admin Reports page and each employee's own self-service Reports page.

### Consistency Guarantee

- Summary cards, Department Analytics, Rankings, and the per-Employee Summary table all use the **same** underlying validity filtering and status-classification logic (`services/admin_reports_service.py`). A known historical bug where malformed "no attendance record" placeholder objects were counted inconsistently between sections (causing Absent totals to disagree) has been fixed at the source — every section now reports identical numbers for the same filters.

### PDF Export

- Generates a matching PDF version of the on-screen report, with company branding, the selected filters, and full summary statistics.

## Employee Management

### Add / Edit Employee

Captured fields include:
- Employee ID, Name, Department, Designation, Basic Salary, Joining Date, **Date of Birth**
- Email, Phone, Address, Office Location
- Bank Name, Bank Account Number
- **Statutory Details**: PAN Number, UAN Number, PF Account Number
- **Salary Allowances**: HRA, DA, Medical Allowance, Travel Allowance, Special Allowance, Other Allowances
- **Salary Deductions**: Employee/Employer PF %, ESIC %, TDS %, Bus/Transport Charges, Other Deductions
- Profile photo

All numeric fields default safely to `0.0` if left blank, and the Edit form always pre-populates existing values so nothing is accidentally cleared on save. Setting an employee's **Designation** to "Manager" grants them access to the Manager Approval console for their scope.

### Face Registration

Same workflow as described in AI Workflow above — captured images feed directly into the recognition engine after training.

### Delete Employee (Safe Cascade)

Deleting an employee cleanly removes, in order: `LogoutApprovalRequest` (referencing this employee as employee/manager/approver, or referencing their attendance rows), `AttendanceActivity`, `EmployeeLogin`, `Attendance`, and `Payroll` — before the `Employee` row itself is removed, preventing SQLite foreign-key integrity errors.

## Login System

### Admin Login

- Username/password authentication with hashed credentials.
- Invalid Username vs. Invalid Password are distinguished for the admin, without exposing that distinction on security-sensitive employee-facing endpoints.

### Employee Login

- Backed by a dedicated `EmployeeLogin` table (separate from the `Employee` HR record), with forced password change on first login and support for temporary/reset passwords with expiry.
- Employees get their own self-service portal — dashboard, profile, payroll, and reports — independently frame-presence-locked from the admin camera flow.
- An `Employee` whose designation is "Manager" additionally sees the Manager Approvals console.

### Register / Forgot Password

- New admin registration with duplicate-username/email validation.
- Separate, email-based password reset flows for admins and for employees, each issuing a temporary password.

## Security Features

- **Password Hashing**: Werkzeug-based hashing everywhere credentials are stored (`Admin`, `Employee`, `EmployeeLogin`) — verification always reads the live hash, so a changed password takes effect immediately with no cached logic anywhere.
- **Anti-Proxy Attendance**: The manual fallback requires both Employee ID and password, with generic error messages that don't reveal whether an ID exists, plus a hidden-until-approved review cycle for every manual punch.
- **PDF Encryption**: Payslips are encrypted at rest with AES-256 (via `pikepdf`) using a deterministic, per-employee password never stored in the database or sent by email.
- **Strict Face-Match Thresholds**: A hard-capped cosine-distance tolerance plus a minimum confidence margin between top candidates, to minimize false-positive face matches.
- **Safe Cascade Deletes**: No orphaned foreign-key references left behind when an employee is removed.
- **SQL Injection Protection**: SQLAlchemy ORM throughout.
- **XSS Protection**: Jinja2 auto-escaping, plus explicit HTML-escaping in dynamic JavaScript-rendered attendance UI.
- **CSRF Protection**: Flask-WTF / WTForms on form submissions.
- **Session Management**: Server-side session-based authentication for admin, manager, and employee roles.

## Automated Workflows

| Job | Schedule | Behavior |
|---|---|---|
| Auto-logout regularization | Daily, 23:59 | Closes out anyone still clocked in, raises a regularization request for manager/admin review |
| Payroll generation | Monthly, last day of month (time configurable) | Calculates payroll and, if auto-send is enabled, distributes encrypted payslips via email; missed runs are reconciled on next startup |

## Mobile Responsive Design

Built with Bootstrap 5 across:
- **Kiosk / Login Pages**: Centered, adaptive card layout.
- **Dashboards**: Grid layout that stacks on mobile, for both admin and employee views.
- **Tables**: Horizontal scroll on small screens.
- **Forms**: Full-width inputs on mobile, including the Add/Edit Employee allowance/deduction sections.
- **Navigation**: Collapsible sidebar.
- **Charts**: Auto-resizing.

Optimized for Desktop (1200px+), Tablet (768–1199px), and Mobile (< 768px).

## Database Configuration

### SQLite (Default)

No additional configuration needed. Database file lives under `instance/attendance.db`.

### MySQL

1. Install MySQL server and create a database:
```sql
CREATE DATABASE attendance_db;
```
2. Update `.env`:
```env
DATABASE_URL=mysql+pymysql://username:password@localhost/attendance_db
```
3. PyMySQL is already included in `requirements.txt`.

## Troubleshooting

### Issue: DeepFace / TensorFlow / MediaPipe installation fails
**Solution:** Ensure a compatible Python version, install build tools, and retry inside a clean virtual environment.

### Issue: Webcam not accessible
**Solution:** Check browser permissions, ensure no other app is using the webcam, try Chrome.

### Issue: Face recognition not matching
**Solution:** Ensure good lighting, capture at least 20 images per employee, retrain the model, and review the tolerance setting — remember it's hard-capped for safety and can only be tightened, not loosened, below the system floor.

### Issue: Manual attendance fallback rejects a correct password
**Solution:** Confirm the employee is using their **current** `EmployeeLogin` password (the one used to log into the Employee Dashboard), not a payslip PDF password — these are unrelated credentials.

### Issue: Manual attendance was marked but isn't showing up in reports/payroll
**Solution:** Manual (password-fallback) punches start as `pending` and stay hidden from normal counts until a Manager or Admin approves them from the Approvals console — check there first.

### Issue: Can't mark OUT after a manual IN
**Solution:** If the manual IN is still `pending` approval, OUT and any further punches are blocked by design until a manager/admin approves or rejects it.

### Issue: Email not sending
**Solution:** Verify SMTP credentials in `.env`, use an app-specific password for Gmail, and check logs for the specific SMTP error.

### Issue: Payslip PDF won't open
**Solution:** Confirm the recipient is using first-4-letters-of-name (capitals) + DOB in DDMM format; if DOB isn't on file for that employee, the system falls back to Employee ID + DOB, or to their joining date if DOB is missing entirely.

### Issue: Database locked error
**Solution:** Close all other connections to the `.db` file and ensure only one application instance is running.

### Issue: Port 5000 already in use
**Solution:**
```python
app.run(debug=True, host='0.0.0.0', port=5001)
```

## Deployment

### Production Deployment

1. **Environment Variables:**
```env
FLASK_ENV=production
SECRET_KEY=your-secure-secret-key
SESSION_COOKIE_SECURE=true
```
2. **Production WSGI Server:**
```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```
3. **Reverse Proxy:** Nginx/Apache with SSL/HTTPS, static file serving.
4. **Database:** MySQL/PostgreSQL for production, with regular backups.
5. **Monitoring:** Application monitoring, error logging, resource alerts.

## Version History

### v3.0.0 (Current Release)

- Root URL is now a public, no-login **kiosk landing page** with continuous face-scan attendance; admin/employee login is one click away instead of gating the home screen.
- Introduced a **Manager** role (an `Employee` with designation "Manager") with its own Approvals console, separate from Admin.
- Manual (password-fallback) attendance now runs a **hidden-until-approved** review cycle: every manual punch starts `pending`, blocks further punches for the day until resolved, supports a same-day retry after rejection, and only counts in reports/payroll once approved.
- Added dedicated **Admin** and **Manager** edit-attendance screens for after-the-fact corrections.
- Added a full **Employee Self-Service Portal**: dashboard, profile, payslip viewing, and personal attendance reports with export, plus an employee-specific forgot/change-password flow.
- Added **versioned attendance settings** (`AttendanceSettingsHistory`) so historical attendance is always evaluated against the rules in force on that date, not today's settings.
- Added a dedicated **Payroll Settings** page for auto-generation schedule, auto-email toggle, and month-by-month Professional Tax slabs.
- Added **Company Settings** (name, address, contact, logo) editable from the UI and used across generated PDFs.
- Added MediaPipe as a secondary face-detection backend alongside DeepFace.
- Reconciliation for missed scheduled payroll runs on application startup.

### v2.0.0

- Removed the insecure Employee-ID-only manual attendance fallback; added password-verified `/mark_manual_attendance`.
- Added `attendance_type` audit column (`FACE_RECOGNITION` / `MANUAL_PASSWORD`).
- Manual attendance now routes through the same rule engine as camera attendance — no hardcoded status.
- Multi-face, margin-based strict matching with server-side frame-presence locking (admin and employee streams independently isolated).
- Configurable salary allowances (HRA, DA, Medical, Travel, Special, Other) and deductions (TDS%, Bus Charges, Other Deduction) captured end-to-end from form → database → payroll → payslip.
- PAN/UAN/PF statutory details captured and displayed on payslips with clean fallbacks.
- Password-protected PDF payslips (pikepdf, AES-256) with a deterministic, never-stored password rule.
- Deliverability-conscious email service (proper MIME nesting, spam-safe subjects, no plain-text passwords ever emailed).
- Fixed Admin Reports inconsistency between summary cards and per-employee table (shared aggregation/validity logic).
- Safe cascade delete covering `LogoutApprovalRequest` and `AttendanceActivity`, eliminating FK integrity errors on employee deletion.

### v1.1.0

- DeepFace (FaceNet512) integration.
- Automatic absent logic after office end time.
- Responsive dashboard with improved charts.
- Register User / Forgot Password authentication flows.

### v1.0.0

- Initial release: AI face recognition, attendance tracking, payroll management, PDF payslips, email integration, reports and analytics.

## Future Enhancements

- Biometric authentication (fingerprint/iris) as an additional fallback layer
- Native mobile app (iOS/Android)
- Geo-fencing for location-based attendance verification
- Integrated leave management
- Multi-shift support with rotation
- Predictive analytics for attendance patterns
- REST APIs for third-party integrations
- Multi-language support (i18n)
- Custom, schedulable report builder
- Comprehensive system-wide audit logs beyond attendance

## License

This project is proprietary software. All rights reserved.

## Contact

For support and inquiries:
- Email: lekawalevaishnavi@gmail.com

---

**Built with ❤️ using Python, Flask, and AI**
