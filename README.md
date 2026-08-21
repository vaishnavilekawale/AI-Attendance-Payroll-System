# 🤖 AI Employee Attendance Monitoring and Payroll Management System

A complete AI-powered Employee Attendance Monitoring and Payroll Management System developed using Python, Flask, DeepFace (Facenet512), OpenCV, and SQLite. The system automatically recognizes employees using facial recognition, falls back to a password-secured manual entry when a face can't be confidently matched, records attendance with a full audit trail, calculates payroll dynamically from configurable allowances and deductions, generates password-protected PDF payslips, and delivers them through a deliverability-conscious email service.

## Project Overview

The AI Employee Attendance Monitoring and Payroll Management System is a comprehensive solution for modern HR management. It leverages AI face recognition to automate attendance tracking, enforces strict anti-proxy safeguards when face recognition can't be used, and ties every attendance record into a single, consistent rule engine that drives both reporting and payroll. The system features a responsive admin dashboard, an employee self-service portal, detailed audited reports, and end-to-end payroll automation — from allowance/deduction configuration to encrypted payslip delivery.

## Features

### Attendance & Recognition
- **AI Face Recognition**: Continuous, real-time camera scanning using DeepFace with the FaceNet512 embedding model — no manual "capture" click required once the camera starts.
- **Multi-Face, Strict-Match Detection**: Every face detected in a frame is analyzed independently, so multiple people can be recognized in a single scan. Matching uses a hard-capped cosine-distance tolerance plus a minimum confidence margin between the best and second-best candidate, so an ambiguous or borderline face is reported as "Unknown" rather than guessed.
- **Frame-Presence Locking (No Duplicate Punches)**: A server-side presence tracker ensures an employee is logged **exactly once** per continuous appearance in front of the camera — even if they stand there for hours — and only allows a new attendance attempt after they've genuinely left the frame (configurable absence timeout) and returned.
- **Secure Manual Fallback (Proxy-Attendance Prevention)**: If face recognition can't confidently identify someone, `/mark_manual_attendance` requires **both** the employee's Employee ID **and** their account password, verified live against the `EmployeeLogin` table (`EmployeeLogin.check_password()`). Employee-ID-only fallback has been removed entirely, so no one can punch in on a colleague's behalf just by knowing their ID. A password changed by the employee takes effect immediately, with zero cached credentials anywhere in the path.
- **Full Attendance Audit Trail**: Every `Attendance` row carries an `attendance_type` column — `FACE_RECOGNITION` (camera auto-scan) or `MANUAL_PASSWORD` (secure fallback) — so HR can always audit exactly how a given day's attendance was captured.
- **One Rule Engine, No Hardcoded Status**: Whether attendance is captured via camera or manual fallback, both paths call the same core engine (`attendance.py`'s `AttendanceManager.mark_attendance()`), which evaluates real office shift timing to compute the correct status — Present, Late, or Half-Day — rather than ever hardcoding a result.
- **Automatic Working Hours Calculation**: Computed in the background from IN/OUT timestamps as soon as a punch is recorded.
- **Automatic Late Entry Detection**: Configurable grace period after office start time.
- **Automatic Absent Detection**: Employees with no attendance record are marked absent after office end time, with no manual intervention.
- **Overtime Calculation**: Configurable overtime rate applied to hours worked beyond the standard workday.
- **Employee Self-Service Attendance**: A separate login-gated attendance stream for employees to mark their own IN/OUT via face recognition, with its own independent frame-presence lock isolated from the admin scanning flow.

### Employee & Payroll Management
- **Complete Employee Lifecycle Management**: Add/edit employees with department, designation, joining date, date of birth, bank details, and profile photo.
- **Statutory Details**: PAN Number, UAN Number, and PF Account Number captured per employee and displayed on the generated payslip, with a clean "N/A" fallback if any are missing.
- **Configurable Salary Allowances**: HRA, DA, Medical Allowance, Travel Allowance, Special Allowance, and Other Allowances — each stored per-employee and dynamically summed into gross salary.
- **Configurable Salary Deductions**: TDS (as a percentage of earned gross), Bus/Transport Charges, and Other Deductions, alongside the existing PF, ESIC, Professional Tax, LOP, and Late deductions — all dynamically summed into total deductions.
- **Automated Payroll Engine**: `Gross Salary = Basic + HRA + DA + Medical + Travel + Special + Other Allowances`, `Net Salary = Gross Salary − Total Deductions`, recalculated from live employee and attendance data every time payroll runs — never cached or hand-adjusted.
- **Safe Cascade Delete**: Deleting an employee cleanly removes every dependent record first — `LogoutApprovalRequest`, `AttendanceActivity`, `EmployeeLogin`, `Attendance`, and `Payroll` — in the correct order, preventing the SQLite NOT NULL / foreign-key integrity errors that occur when dependents are left behind.

### PDF Payslips & Email
- **Password-Protected PDF Payslips**: Clean, corporate-styled payslips built with ReportLab, then encrypted with `pikepdf` (AES-256) so only the individual employee can open their own file.
- **Deterministic Password Rule**: First 4 uppercase letters of the employee's name + date of birth (DDMM), with a safe fallback to Employee ID + DOB (or joining date, if DOB isn't on file) — computed on demand, never stored anywhere.
- **Deliverability-Conscious Email Service**: Proper nested MIME structure (`multipart/mixed` → `multipart/alternative` with plain-text + HTML, attachments as siblings rather than mixed into the alternative part), spam-safe subject lines, and correctly content-typed PDF attachments.
- **Password Never Sent By Email**: The payslip email explains the password *rule* with a fixed, generic worked example — the recipient's actual password is never included in the email body in any form.

### Reporting & Dashboard
- **Consistent Reporting Engine**: Admin Reports' summary cards, department analytics, rankings, and the per-employee summary table all share the exact same validity filtering and status-classification logic, so the numbers always agree with each other — no more mismatched Absent counts between sections.
- **Dashboard Analytics**: Real-time Present/Absent/Half-Day/Late cards and interactive charts.
- **Department-wise Statistics**: Attendance breakdown by department.
- **Admin Approval Workflows**: A dedicated queue (`admin_approvals.html`) for reviewing and approving/rejecting attendance regularization requests raised automatically when an employee misses a clock-out.

### Automation
- **Scheduled Auto-Logout Reconciliation**: A daily background job (APScheduler) closes out anyone still clocked in at end of day and raises a regularization request for admin review.
- **Scheduled Monthly Payroll**: Automatically generates payroll and distributes encrypted payslips via email at month-end.

### Platform
- **Login System**: Secure username/password authentication for admins, with a separate employee login backed by its own `EmployeeLogin` credentials table (including forced password change on first login and temporary-password support).
- **Mobile Responsive UI**: Bootstrap 5 responsive design across desktop, tablet, and mobile.

## Tech Stack

### Backend
- Python 3.10+
- Flask (Web Framework)
- SQLAlchemy (ORM)
- Flask-Login-style session-based authentication

### AI / Computer Vision
- DeepFace (Face Recognition)
- FaceNet512 (Embedding Model)
- OpenCV (Image Processing)
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
- APScheduler (background job scheduling)
- smtplib (SMTP email, standard library)
- Werkzeug (password hashing/security)

## Project Structure

```
attendance_ai/
│
├── app.py                      # Main Flask application & routes
├── config.py                   # Configuration settings
├── database.py                 # Database initialization
├── models.py                   # Database models (incl. attendance_type, dob, allowances, deductions, PAN/UAN/PF)
├── ai_engine.py                 # Face detection, matching, and frame-presence tracking
├── attendance.py                 # Core attendance rule engine (status/late/half-day logic)
├── payroll.py                     # Payroll calculation engine (allowances + deductions)
├── email_service.py                # SMTP email service (MIME-correct, spam-conscious)
├── pdf_generator.py                 # Payslip PDF layout + password protection helper
├── scheduler_service.py               # APScheduler jobs: auto-logout, monthly payroll
├── requirements.txt             # Python dependencies
├── README.md                    # This file
├── .env.                      # Environment variables template
├── attendance.db                # SQLite database (auto-generated, under instance/)
│
├── dataset/                    # Face images for training, organized by employee ID
├── trained_model/               # Cached face embeddings
├── payrolls/                    # Generated, password-encrypted PDF payslips
│
├── services/
│      approval_service.py        # Regularization / missed-clockout approval logic
│      attendance_calculator.py    # Present / Late / Half-Day timing rules
│      attendance_stats.py          # Aggregated attendance statistics helpers
│      admin_reports_service.py      # Admin Reports single-source-of-truth aggregation
│
├── static/
│      css/
│          style.css            # Custom styles
│      js/
│          main.js              # JavaScript functions
│      images/
│          company_logo.png     # Company logo
│
├── templates/
│      login.html               # Admin/Employee login page
│      register.html            # User registration page
│      forgot_password.html     # Password reset page
│      dashboard.html           # Admin dashboard
│      add_employee.html        # Add/Edit Employee modals (allowances, deductions, statutory details)
│      attendance.html          # Camera auto-scan + secure manual fallback
│      employee_attendance.html # Employee self-service attendance stream
│      admin_approvals.html     # Regularization / missed-clockout review queue
│      payroll.html             # Payroll management
│      reports.html             # Reports
│      settings.html            # System settings
│
└── uploads/                    # Uploaded files (photos, generated payslip copies)
```

## Installation Guide

### Prerequisites

- Python 3.10 or higher
- pip (Python package manager)
- Virtual environment (recommended)

### Step 1: Clone the Project

```bash
git clone https://github.com/<your-username>/attendance_ai.git
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

**Note:** Installing DeepFace, TensorFlow, and pikepdf may take some time as they pull in additional native dependencies.

### Step 5: Configure Environment Variables

Copy `.env.example` to `.env` and configure:

```bash
copy .env.example .env
```

Edit `.env` with your settings:

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

# Company Settings
COMPANY_NAME=AI Attendance System
COMPANY_LOGO=static/images/company_logo.png

# Office Timing
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

The application will start on `http://localhost:5000`

## AI Workflow

### Face Registration

1. Register the employee in the system with their basic details.
2. Navigate to employee management and open face capture.
3. Set the number of face images to capture (minimum 20 recommended).
4. Start capture — ensure good lighting and a clearly visible face.
5. Images are stored under `dataset/<employee_id>/`.

### AI Training

1. Navigate to Settings.
2. Trigger AI model training.
3. DeepFace processes all face images per employee and FaceNet512 generates 512-dimensional embeddings.
4. Embeddings are cached under `trained_model/` for fast comparison at recognition time.

### Face Recognition & Matching

1. The Attendance page starts the camera automatically — no manual "Start Camera" step and no Employee ID prompt.
2. Every ~1.5–2 seconds, a frame is sent to the recognition engine.
3. **Every face detected in that frame** is embedded and compared independently against all trained employees (multi-person aware).
4. A match is only accepted if the best candidate's distance is below a strict, hard-capped tolerance **and** clearly beats the second-best candidate by a minimum margin — otherwise the face is reported "Unknown" rather than guessed.
5. A server-side presence tracker records who is currently "in frame" so a matched employee is logged only once per continuous presence, regardless of how many frames they appear in.

## Attendance Workflow

### IN Punch

1. Employee's face is recognized (or they authenticate via the secure manual fallback).
2. Current time is recorded as IN Time; status is computed by the rule engine (Present or Late, based on office start time + grace period).
3. Duplicate IN punches for the same continuous presence are blocked by the presence tracker.

### OUT Punch

1. Employee is recognized again (or re-authenticates manually) later in the day.
2. Current time is recorded as OUT Time; working hours are calculated automatically (OUT − IN).
3. OUT can only be marked after a matching IN for the same day.

### Secure Manual Fallback (When Face Recognition Fails)

1. The "Face Not Recognized" card asks for **both** Employee ID and account password.
2. `/mark_manual_attendance` verifies the password live against `EmployeeLogin.check_password()` (the same mechanism used by the real employee login page, including temporary-password support) — never a hardcoded or cached comparison.
3. On success, the record is created/updated through the exact same `AttendanceManager.mark_attendance()` engine the camera flow uses — status is computed honestly (Present/Late/Half-Day), never force-set to "Present".
4. The resulting `Attendance` row is tagged `attendance_type = 'MANUAL_PASSWORD'` for audit purposes.
5. Any failure (wrong ID, wrong password, inactive account) returns the same generic message, so the endpoint can't be used to enumerate valid Employee IDs.

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

TDS                   = Earned Gross × TDS% (employee-configured)
Total Deductions      = Absent + Half-Day + Late Deduction
                        + Employee PF + ESIC + Professional Tax
                        + TDS + Bus/Transport Charges + Other Deductions

Net Salary            = Total Gross Earnings − Total Deductions
```

### Payslip Generation

1. Trigger PDF generation for an employee from the Payroll page.
2. ReportLab builds a professional payslip with:
   - Company header (name, address, phone, email, website)
   - Employee details: Employee ID, Name, Department, Designation, Location, Pay Period, **PAN Number, UAN Number, PF Account Number** (each falling back to "N/A" if not on file)
   - Attendance summary (working/present/absent/half-day/late/paid/LOP days)
   - Full earnings and deductions breakdown, with Net Pay in words
3. The finished PDF is immediately encrypted with `pikepdf` using a password derived from the employee's name + date of birth — no plain PDF is ever left on disk when protection is requested.
4. The payslip path is stored against the Payroll record.

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

- Latest attendance records with employee name, department, IN/OUT time, working hours, status, and (where relevant) which channel captured it — face recognition or manual password fallback.

## Reports Module

### Report Generation

1. Select a date range, and optionally filter by department, employee, designation, or status.
2. Generate the report.

### Consistency Guarantee

- Summary cards, Department Analytics, Rankings, and the per-Employee Summary table all use the **same** underlying validity filtering and status-classification logic. A known historical bug where malformed "no attendance record" placeholder objects were counted inconsistently between sections (causing Absent totals to disagree) has been fixed at the source — every section now reports identical numbers for the same filters.

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
- **Salary Deductions**: TDS %, Bus/Transport Charges, Other Deductions
- Profile photo

All numeric fields default safely to `0.0` if left blank, and the Edit form always pre-populates existing values so nothing is accidentally cleared on save.

### Face Registration

Same workflow as described in AI Workflow above — captured images feed directly into the recognition engine after training.

### Delete Employee (Safe Cascade)

Deleting an employee cleanly removes, in order: `LogoutApprovalRequest` (referencing this employee as employee/manager/approver, or referencing their attendance rows), `AttendanceActivity`, `EmployeeLogin`, `Attendance`, and `Payroll` — before the `Employee` row itself is removed, preventing SQLite foreign-key integrity errors.

## Login System

### Admin Login

- Username/password authentication with hashed credentials.
- Invalid Username vs. Invalid Password are distinguished for the admin, without exposing that distinction on security-sensitive employee-facing endpoints.

### Employee Login

- Backed by a dedicated `EmployeeLogin` table (separate from the `Employee` HR record), with forced password change on first login and support for temporary/reset passwords.
- Employees get their own self-service attendance stream, independently frame-presence-locked from the admin camera flow.

### Register / Forgot Password

- New admin registration with duplicate-username/email validation.
- Email-based password reset issuing a temporary password.

## Security Features

- **Password Hashing**: Werkzeug-based hashing everywhere credentials are stored (`Admin`, `Employee`, `EmployeeLogin`) — verification always reads the live hash, so a changed password takes effect immediately with no cached logic anywhere.
- **Anti-Proxy Attendance**: The manual fallback requires both Employee ID and password, with generic error messages that don't reveal whether an ID exists.
- **PDF Encryption**: Payslips are encrypted at rest with AES-256 (via `pikepdf`) using a deterministic, per-employee password never stored in the database or sent by email.
- **Strict Face-Match Thresholds**: A hard-capped cosine-distance tolerance plus a minimum confidence margin between top candidates, to minimize false-positive face matches.
- **Safe Cascade Deletes**: No orphaned foreign-key references left behind when an employee is removed.
- **SQL Injection Protection**: SQLAlchemy ORM throughout.
- **XSS Protection**: Jinja2 auto-escaping, plus explicit HTML-escaping in dynamic JavaScript-rendered attendance UI.
- **Session Management**: Server-side session-based authentication for both admin and employee roles.

## Automated Workflows

| Job | Schedule | Behavior |
|---|---|---|
| Auto-logout reconciliation | Daily | Closes out anyone still clocked in, raises a regularization request for admin review in `admin_approvals.html` |
| Payroll generation | Monthly | Calculates payroll and distributes encrypted payslips via email automatically |

## Mobile Responsive Design

Built with Bootstrap 5 across:
- **Login Page**: Centered, adaptive card layout.
- **Dashboard**: Grid layout that stacks on mobile.
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

### Issue: DeepFace / TensorFlow installation fails
**Solution:** Ensure a compatible Python version, install build tools, and retry inside a clean virtual environment.

### Issue: Webcam not accessible
**Solution:** Check browser permissions, ensure no other app is using the webcam, try Chrome.

### Issue: Face recognition not matching
**Solution:** Ensure good lighting, capture at least 20 images per employee, retrain the model, and review the tolerance setting — remember it's hard-capped for safety and can only be tightened, not loosened, below the system floor.

### Issue: Manual attendance fallback rejects a correct password
**Solution:** Confirm the employee is using their **current** `EmployeeLogin` password (the one used to log into the Employee Dashboard), not a payslip PDF password — these are unrelated credentials.

### Issue: Email not sending
**Solution:** Verify SMTP credentials in `.env`, use an app-specific password for Gmail, and check logs for the specific SMTP error.

### Issue: Payslip PDF won't open
**Solution:** Confirm the recipient is using first-4-letters-of-name (capitals) + DOB in DDMM format; if DOB isn't on file for that employee, the system falls back to their joining date instead.

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

### v2.0.0 (Current Release)

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
- Email: support@company.com

---

**Built with ❤️ using Python, Flask, and AI**