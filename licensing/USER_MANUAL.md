# AI Attendance & Payroll System - User Manual

**Version 1.0.0**

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [System Requirements](#2-system-requirements)
3. [Installation Guide](#3-installation-guide)
4. [First-Time Setup](#4-first-time-setup)
5. [Getting Started](#5-getting-started)
6. [Admin Dashboard](#6-admin-dashboard)
7. [Employee Management](#7-employee-management)
8. [Attendance Management](#8-attendance-management)
9. [Payroll Management](#9-payroll-management)
10. [Reports](#10-reports)
11. [Settings](#11-settings)
12. [Approvals Workflow](#12-approvals-workflow)
13. [Employee Portal](#13-employee-portal)
14. [Biometric Data Management](#14-biometric-data-management)
15. [Backup and Restore](#15-backup-and-restore)
16. [Troubleshooting](#16-troubleshooting)
17. [FAQ](#17-faq)
18. [Support](#18-support)

---

## 1. Introduction

### 1.1 Overview

The **AI Attendance & Payroll System** is a comprehensive desktop application that automates employee attendance tracking using facial recognition technology and provides end-to-end payroll management. Designed for small to medium businesses, this system eliminates manual attendance marking, reduces errors, and streamlines payroll processing.

**Key Features:**
- **Face Recognition Attendance**: Automated IN/OUT marking using AI-powered facial recognition
- **Payroll Automation**: Configurable salary structure with automated calculations
- **Encrypted Payslips**: AES-256 encrypted PDF payslips with email delivery
- **Role-Based Access**: Admin, Manager, and Employee roles with appropriate permissions
- **Offline Capability**: Works without internet connection (except for email features)
- **Data Security**: Fernet encryption for biometric data, AES-256 for payslips

**Target Users:**
- **Admin**: Full system access, employee management, payroll generation
- **Manager**: Department-level access, attendance approval, team oversight
- **Employee**: Self-service portal for attendance, payslips, and profile management

### 1.2 System Architecture

The system is built as a desktop application that runs locally on your computer:

```
┌─────────────────────────────────────┐
│  Your Computer (Windows 10/11)      │
│                                     │
│  AttendancePayrollSystem.exe        │
│       │                             │
│       ├─ Flask Web Server (localhost:5000)
│       │   ├─ Attendance Module      │
│       │   ├─ Payroll Module         │
│       │   ├─ Face Recognition       │
│       │   └─ Reporting              │
│       │                             │
│       ├─ SQLite Database            │
│       ├─ Face Data (Encrypted)      │
│       └─ Payslips (Encrypted PDF)   │
└─────────────────────────────────────┘
```

The application opens in your default web browser, providing a user-friendly interface while all data remains stored locally on your computer.

### 1.3 Security Features

Your data security is paramount:

- **AES-256 Encryption**: All payslip PDFs are password-protected with AES-256 encryption
- **Fernet Encryption**: Biometric face data is encrypted at rest using Fernet (AES-128-CBC + HMAC)
- **Password Hashing**: User passwords are hashed using industry-standard algorithms
- **Role-Based Access Control**: Each user role has precisely defined permissions
- **CSRF Protection**: All state-changing requests are protected against cross-site request forgery
- **Rate Limiting**: Login attempts are rate-limited to prevent brute-force attacks

---

## 2. System Requirements

### 2.1 Hardware Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| **Operating System** | Windows 10 (64-bit) | Windows 10/11 (64-bit) |
| **Processor** | Intel i3 or equivalent | Intel i5 or equivalent (4 cores) |
| **RAM** | 8 GB | 16 GB |
| **Storage** | 5 GB free space | 10 GB free space |
| **Webcam** | Required (720p) | HD Webcam (1080p) |
| **Network** | Optional | Required for email features |

### 2.2 Software Requirements

- **No additional software required** - The application is self-contained
- **Microsoft .NET Framework**: Included with Windows 10/11
- **Web Browser**: Chrome, Edge, Firefox, or Safari (latest version)

---

## 3. Installation Guide

### 3.1 Installing the Software

**Step 1: Download the Installer**
- Download `AttendancePayrollSystem-Setup-1.0.0.exe` from the provided link
- Save the file to your Downloads folder or desktop

**Step 2: Run the Installer**
- Double-click the installer file
- If prompted by Windows SmartScreen, click "More info" then "Run anyway"
- Follow the installation wizard prompts

**Step 3: Choose Installation Location**
- Default location: `%LOCALAPPDATA%\AttendancePayrollSystem`
- Recommended to use default location for proper permissions
- Click "Next" to continue

**Step 4: Create Shortcuts**
- **Start Menu**: Automatically created (recommended)
- **Desktop Shortcut**: Optional (uncheck if not needed)
- Click "Next" to continue

**Step 5: Complete Installation**
- Click "Install" to begin
- Wait for installation to complete (typically 1-2 minutes)
- Click "Finish" to exit the installer

**Step 6: Launch the Application**
- The application will launch automatically after installation
- Or double-click the desktop shortcut or Start Menu entry

### 3.2 First Launch

On first launch, the application will:
1. Start the Flask web server on `http://127.0.0.1:5000`
2. Automatically open your default web browser
3. Display the Setup Wizard (if no admin account exists)

**Browser Compatibility:**
- Google Chrome (recommended)
- Microsoft Edge
- Mozilla Firefox
- Safari

**Default Port:** 5000 (automatically configured)

### 3.3 License Activation

#### Trial Period
- **Duration**: 30 days from first launch
- **Features**: Full access to all features
- **Limitation**: No technical support

#### Purchasing a License
To purchase a license:
1. Visit: https://yourcompany.com
2. Select your license tier (Student, Small Business, Medium Business)
3. Complete payment
4. Receive license key via email

#### Activating Your License

**Step 1: Find Your Machine Fingerprint**
- Go to Settings → License
- Copy the 64-character machine fingerprint displayed
- This fingerprint is unique to your computer

**Step 2: Provide Fingerprint to Vendor**
- Email your machine fingerprint to: support@yourcompany.com
- Or enter it during purchase on the website

**Step 3: Receive License Key**
- Vendor will generate a 16-character license key
- License key will be sent to your registered email

**Step 4: Enter License Key**
- Go to Settings → License
- Enter the 16-character license key
- Click "Activate"
- System will validate and activate the license

#### Troubleshooting Activation Issues

**Issue: License key not accepted**
- Verify you entered all 16 characters correctly
- Check that the license key matches your machine fingerprint
- Contact support with your machine fingerprint and license key

**Issue: Machine fingerprint mismatch**
- Ensure you provided the correct machine fingerprint
- Machine fingerprint is case-sensitive
- Re-check the fingerprint displayed in Settings → License

**Issue: Trial expired**
- Purchase a license to continue using the system
- Trial data is preserved after license activation

**Issue: Activation count exceeded**
- Each license allows up to 2 machine activations
- Contact support if you need additional activations

---

## 4. First-Time Setup

### 4.1 Setup Wizard

The Setup Wizard guides you through initial configuration when you first launch the application.

#### Step 1: Create Admin Account

**Required Fields:**
- **Username**: Unique identifier for admin login
- **Password**: Minimum 8 characters, recommended mix of letters, numbers, symbols
- **Confirm Password**: Must match password
- **Email**: For password recovery and notifications
- **Full Name**: Your display name

**Best Practices:**
- Use a strong password
- Remember your password (no password recovery without email)
- Use a professional email address

#### Step 2: Company Information

**Required Fields:**
- **Company Name**: Your business name
- **Address**: Business address
- **Phone**: Contact phone number
- **Email**: Business email

**Optional Fields:**
- **Website**: Company website URL
- **Logo**: Upload company logo (recommended size: 200x100 pixels)

#### Step 3: Working Hours Configuration

**Default Settings:**
- **Working Hours**: 9:00 AM to 6:00 PM
- **Working Days**: Monday to Saturday
- **Weekly Holiday**: Sunday

**Customization:**
- Adjust start and end times as needed
- Select working days
- Configure weekly holiday

#### Step 4: Attendance Rules

**Late Arrival Threshold:**
- Default: 15 minutes
- Employees arriving after threshold are marked "Late"

**Half-Day Calculation:**
- Default: 4 hours minimum for full day
- Less than 4 hours = half-day
- Less than 2 hours = absent

**Overtime Calculation:**
- Default: Enabled
- Overtime starts after working hours
- Configurable overtime rate multiplier

#### Step 5: Payroll Settings

**Financial Year:**
- Default: April to March
- Can be customized to your fiscal year

**Salary Components:**
- Basic Salary
- House Rent Allowance (HRA)
- Dearness Allowance (DA)
- Other Allowances (customizable)

**Statutory Deductions:**
- Provident Fund (PF)
- Employee State Insurance (ESIC)
- Professional Tax (PT)

**Click "Complete Setup"** to finish the wizard and proceed to the dashboard.

### 4.2 Company Settings

After completing the Setup Wizard, you can further customize company settings:

**Access:** Settings → Company Settings

**Editable Fields:**
- Company name and logo
- Address and contact details
- Financial year configuration
- Holiday calendar

**Holiday Calendar:**
- Add company-specific holidays
- Set holiday dates and descriptions
- Holidays are automatically excluded from attendance calculations

### 4.3 Initial Configuration

**Access:** Settings → Attendance Settings

**Attendance Policies:**
- Configure late arrival rules
- Set half-day calculation thresholds
- Enable/disable overtime tracking
- Configure leave management rules

**Leave Management:**
- Set leave types (Casual, Sick, Earned)
- Configure leave accrual rules
- Set maximum leave balance

---

## 5. Getting Started

### 5.1 Dashboard Overview

The dashboard provides a quick overview of your organization's status:

**Top Navigation Bar:**
- **Dashboard**: Main overview page
- **Attendance**: Attendance management
- **Employees**: Employee management
- **Payroll**: Payroll processing
- **Reports**: Reports and analytics
- **Approvals**: Approval requests
- **Settings**: System configuration
- **Logout**: Sign out

**Dashboard Widgets:**
- **Present Today**: Number of employees currently present
- **Absent Today**: Number of employees absent today
- **Late Arrivals**: Number of employees who arrived late
- **Pending Approvals**: Number of approval requests awaiting action
- **Monthly Attendance Summary**: Graph showing attendance trends
- **Payroll Status**: Current month payroll status

**Date Range Filter:**
- Select date range for dashboard data
- Presets: Today, This Week, This Month, Custom
- Dashboard widgets update based on selected range

### 5.2 User Roles

#### Admin
- **Full Access**: All system features and settings
- **Employee Management**: Add, edit, delete employees
- **Payroll Generation**: Generate and manage payroll
- **System Configuration**: Configure all settings
- **Approvals**: Override manager approvals

#### Manager
- **Department Access**: View and manage own department
- **Attendance Approval**: Approve department attendance requests
- **Team Oversight**: View team attendance and payroll
- **Limited Settings**: Configure department-specific settings

#### Employee
- **Self-Service**: View own attendance and payslips
- **Profile Management**: Update personal details
- **Attendance Requests**: Request attendance corrections
- **Payslip Access**: Download own payslips

### 5.3 Basic Navigation

**Moving Between Sections:**
- Click navigation menu items to switch sections
- Use browser back/forward buttons
- Dashboard is always accessible from navigation

**Using Search and Filters:**
- Most pages have search boxes
- Filter by date range, department, status
- Use preset date ranges for quick filtering

**Exporting Data:**
- Look for "Export" buttons on pages
- Export formats: PDF, Excel, CSV
- Select date range before exporting

---

## 6. Admin Dashboard

### 6.1 Dashboard Widgets

**Present Today Widget:**
- Shows count of employees marked present
- Click to view present employees list
- Real-time updates as employees check in

**Absent Today Widget:**
- Shows count of employees marked absent
- Click to view absent employees list
- Shows reason for absence if available

**Late Arrivals Widget:**
- Shows count of employees who arrived late
- Click to view late arrival details
- Shows arrival time and delay duration

**Pending Approvals Widget:**
- Shows count of approval requests
- Click to go to Approvals section
- Color-coded by urgency

**Monthly Attendance Summary:**
- Bar chart showing daily attendance
- Filter by month
- Hover over bars for details

**Payroll Status Widget:**
- Shows current month payroll status
- Status: Not Generated, In Progress, Completed
- Click to go to Payroll section

### 6.2 Quick Actions

**Add New Employee:**
- Click "Add Employee" button
- Opens employee registration form
- Fill required fields and save

**Mark Manual Attendance:**
- Click "Mark Attendance" button
- Select employee and date
- Enter IN/OUT times and reason
- Submit for approval

**Generate Payroll:**
- Click "Generate Payroll" button
- Select month and employees
- Review calculations
- Finalize payroll

**View Reports:**
- Click "Reports" button
- Select report type
- Configure parameters
- Generate and export

### 6.3 Date Range Filters

**Filtering by Date Range:**
- Use date range picker on dashboard
- Select start and end dates
- Dashboard widgets update automatically

**Presets:**
- **Today**: Current day only
- **This Week**: Current week (Monday to Sunday)
- **This Month**: Current month
- **Last Month**: Previous month
- **Custom**: Select custom date range

**Custom Date Selection:**
- Click date range picker
- Select start date
- Select end date
- Click "Apply"

---

## 7. Employee Management

### 7.1 Adding Employees

**Access:** Employees → Add Employee

**Required Fields:**
- **Employee ID**: Unique identifier (auto-generated or manual)
- **First Name**: Employee's first name
- **Last Name**: Employee's last name
- **Email**: Employee email address
- **Phone**: Contact phone number
- **Joining Date**: Date of joining
- **Department**: Department assignment
- **Designation**: Job title/role
- **Basic Salary**: Monthly basic salary

**Optional Fields:**
- **Address**: Residential address
- **Bank Name**: Bank for salary transfer
- **Account Number**: Bank account number
- **IFSC Code**: Bank IFSC code
- **PAN Number**: PAN for tax purposes

**Department Assignment:**
- Select from existing departments
- Or create new department
- Department determines manager access

**Click "Save Employee"** to add the employee.

### 7.2 Employee Profile

**Access:** Employees → View Employees → Click employee name

**Profile Sections:**

**Personal Information:**
- Name, employee ID, email, phone
- Address, joining date, designation
- Department and reporting manager

**Contact Details:**
- Email address
- Phone number
- Emergency contact (if configured)

**Salary Details:**
- Basic salary
- HRA, DA, other allowances
- Deductions (PF, ESIC, PT)
- Net salary

**Bank Information:**
- Bank name
- Account number
- IFSC code
- PAN number

**Face Registration Status:**
- Shows if face images are registered
- Number of face images captured
- Recognition model training status

### 7.3 Face Registration

**Access:** Employees → View Employees → Click "Register Face"

**Importance of Face Registration:**
- Required for automated attendance
- Enables face recognition check-in/check-out
- Improves accuracy and reduces manual work

**Capturing Face Images:**

**Step 1: Position Camera**
- Ensure good lighting (natural or bright artificial light)
- Position camera at eye level
- Maintain 2-3 feet distance from camera
- Ensure face is clearly visible

**Step 2: Capture Images**
- Click "Capture" to take photo
- Capture 5-10 images for better recognition
- Vary angle slightly between captures (front, slight left, slight right)
- Ensure neutral expression in all photos

**Step 3: Review Images**
- Review captured images
- Delete blurry or poor-quality images
- Retake if necessary

**Step 4: Train Model**
- Click "Train Recognition Model"
- Wait for training to complete (typically 1-2 minutes)
- System will confirm when training is complete

**Lighting and Positioning Guidelines:**
- **Lighting**: Bright, even lighting. Avoid backlighting.
- **Positioning**: Face centered, looking directly at camera
- **Background**: Plain, uncluttered background
- **Expression**: Neutral expression, mouth closed
- **Accessories**: Remove hats, sunglasses, masks

**Retraining When Needed:**
- Retrain if recognition accuracy decreases
- Retrain after significant appearance changes (haircut, beard, glasses)
- Retrain if adding new face images
- Access: Employees → View Employees → Click "Retrain Model"

### 7.4 Editing Employees

**Access:** Employees → View Employees → Click "Edit"

**Editable Fields:**
- Personal information (name, email, phone, address)
- Department and designation
- Salary details
- Bank information

**Changing Department/Role:**
- Select new department from dropdown
- Update designation
- Save changes
- Manager access updates automatically

**Salary Adjustments:**
- Update basic salary
- Adjust allowances
- Modify deductions
- Effective date for changes
- Save changes

**Status Changes:**
- **Active**: Employee is currently working
- **Inactive**: Employee has left or is on long leave
- Inactive employees cannot mark attendance
- Payroll can still be generated for inactive employees

**Click "Update Employee"** to save changes.

### 7.5 Deleting Employees

**Access:** Employees → View Employees → Click "Delete"

**Soft Delete vs. Hard Delete:**

**Soft Delete (Recommended):**
- Marks employee as inactive
- Preserves all historical data
- Payroll history retained
- Attendance history retained
- Employee can be reactivated later

**Hard Delete:**
- Permanently removes employee
- Deletes all associated data
- Cannot be undone
- **Warning**: Use with extreme caution

**Data Retention Policies:**
- Attendance records: Retained indefinitely
- Payroll records: Retained indefinitely (legal requirement)
- Face data: Deleted when employee is deleted (GDPR compliance)
- Backup recommendations: Keep backups for 7 years

**Payroll History Preservation:**
- Payroll history is always preserved
- Even if employee is deleted
- Required for tax and legal compliance

---

## 8. Attendance Management

### 8.1 Face Recognition Attendance

**Access:** Attendance → Face Recognition

**Kiosk Mode Setup:**
- Place computer/tablet at entrance
- Position camera at appropriate height
- Ensure good lighting
- Open Face Recognition page in full-screen mode

**Camera Positioning:**
- Mount camera at 4-5 feet height
- Angle slightly downward (15-20 degrees)
- Ensure 3-5 feet distance from user
- Test positioning before deployment

**Real-Time Recognition:**
- Employee stands in front of camera
- System detects face automatically
- Recognition takes 1-2 seconds
- System displays employee name and photo
- Attendance marked automatically (IN or OUT based on time)

**Automatic IN/OUT Marking:**
- **First check-in of day**: Marks IN
- **Subsequent check-ins**: Marks OUT if last was IN, IN if last was OUT
- System tracks last action for each employee
- Manual override available if needed

**Best Practices:**
- Ensure consistent lighting throughout day
- Position camera away from direct sunlight
- Keep camera lens clean
- Test recognition with different employees

### 8.2 Manual Attendance Entry

**Access:** Attendance → Mark Attendance

**When to Use Manual Entry:**
- Face recognition system unavailable
- Employee forgot to check in/out
- Camera malfunction
- Remote employee attendance
- System downtime

**Adding Attendance for Employees:**

**Step 1: Select Employee**
- Search or select employee from list
- Employee must be registered in system

**Step 2: Select Date**
- Choose date for attendance
- Cannot mark future dates

**Step 3: Enter IN/OUT Times**
- Enter check-in time
- Enter check-out time
- Times must be in HH:MM format (24-hour)

**Step 4: Provide Reason**
- Select reason from dropdown:
  - Forgot to check-in
  - Camera malfunction
  - System downtime
  - Remote work
  - Other (specify)
- Add additional remarks if needed

**Step 5: Submit for Approval**
- Click "Submit"
- Attendance goes to manager for approval
- Admin can override manager approval

**Approval Workflow:**
- Manager reviews manual attendance requests
- Manager can approve or reject
- Rejected requests require resubmission
- Admin can approve directly

### 8.3 Attendance Regularization

**Access:** Employees → My Attendance → Request Correction (for employees)
**Access:** Approvals → Manual Attendance (for managers/admins)

**Requesting Corrections:**

**Step 1: View Attendance**
- Go to My Attendance
- Select date with incorrect attendance
- Click "Request Correction"

**Step 2: Provide Correct Details**
- Enter correct IN/OUT times
- Select reason for correction
- Add remarks explaining the correction

**Step 3: Submit Request**
- Click "Submit Request"
- Request goes to manager for approval

**Manager Approval Process:**
- Manager receives notification
- Manager reviews request
- Manager can approve or reject
- Approved corrections update attendance record

**Admin Override Capability:**
- Admin can approve any request
- Admin can override manager rejection
- Use for exceptional cases

### 8.4 Attendance Rules

**Sunday as Weekly Holiday:**
- Sundays are automatically marked as holiday
- No attendance required on Sundays
- Can be configured in Settings

**Late Arrival Thresholds:**
- Default: 15 minutes after start time
- Configurable in Settings → Attendance Settings
- Employees arriving after threshold marked "Late"
- Late arrivals affect attendance percentage

**Half-Day Calculation:**
- Default: Less than 4 hours = half-day
- Less than 2 hours = absent
- Configurable in Settings
- Affects payroll calculations

**Overtime Tracking:**
- Enabled by default
- Overtime calculated after working hours
- Configurable overtime rate multiplier
- Overtime included in payroll if enabled

### 8.5 Attendance History

**Access:** Attendance → Attendance History

**Viewing Past Attendance:**
- Select date range
- View all attendance records
- Filter by employee or department
- Export to PDF/Excel/CSV

**Filtering by Date/Employee:**
- Use date range picker
- Search by employee name or ID
- Filter by department
- Filter by attendance status (Present, Absent, Late)

**Exporting Attendance Records:**
- Click "Export" button
- Select format (PDF, Excel, CSV)
- Select date range
- Download file

---

## 9. Payroll Management

### 9.1 Payroll Configuration

**Access:** Settings → Payroll Settings

**Salary Components:**

**Basic Salary:**
- Base salary component
- Typically 40-50% of total salary
- Configured per employee

**House Rent Allowance (HRA):**
- Percentage of basic salary
- Typically 40-50% of basic
- Configurable percentage

**Dearness Allowance (DA):**
- Cost of living adjustment
- Percentage of basic salary
- Configurable percentage

**Other Allowances:**
- Custom allowances (travel, medical, etc.)
- Fixed amount or percentage
- Configurable per employee

**Allowances and Deductions:**

**Statutory Deductions:**
- **Provident Fund (PF)**: 12% of basic salary (employee share)
- **Employee State Insurance (ESIC)**: 0.75% of gross salary
- **Professional Tax (PT)**: Fixed amount based on state

**Other Deductions:**
- Loan repayments
- Advance recoveries
- Other deductions (configurable)

**Overtime Rates:**
- Default: 1.5x hourly rate
- Configurable multiplier
- Calculated based on basic salary

### 9.2 Generating Payroll

**Access:** Payroll → Generate Payroll

**Monthly Payroll Generation:**

**Step 1: Select Month**
- Select month and year
- Cannot generate future payroll
- Previous months can be regenerated

**Step 2: Select Employees**
- Select all employees or specific employees
- Filter by department
- Filter by employment status

**Step 3: Review Calculations**
- System calculates based on attendance
- Review calculated amounts
- Check for errors or anomalies

**Calculation Preview:**
- Working days in month
- Present days
- Absent days
- Late days
- Overtime hours
- Gross salary
- Deductions
- Net salary

**Step 4: Finalize Payroll**
- Click "Finalize Payroll"
- Payroll is locked for the month
- Payslips are generated
- Email dispatch initiated (if configured)

### 9.3 Payroll Review

**Access:** Payroll → Review Payroll

**Reviewing Calculated Amounts:**
- View detailed breakdown per employee
- Check attendance-based calculations
- Verify deductions
- Review overtime calculations

**Manual Adjustments:**
- Add manual adjustments
- Provide reason for adjustment
- Adjustments are logged for audit

**Approval Workflow:**
- Manager reviews department payroll
- Manager can approve or request changes
- Admin reviews final payroll
- Admin approves for finalization

**Locking Payroll:**
- Once approved, payroll is locked
- Cannot be modified without admin override
- Payslips are generated after locking

### 9.4 Payslip Generation

**Access:** Payroll → Payslips

**PDF Payslip Creation:**
- Automatic after payroll finalization
- Includes all salary components
- Shows attendance summary
- Shows deductions breakdown

**Password Protection:**
- Default password: Employee ID or PAN
- Configurable in Settings
- AES-256 encryption
- Required for security

**Email Delivery:**
- Automatic if SMTP configured
- Sent to employee email
- Password provided in separate email
- Configurable in Settings → Email Settings

**Download Options:**
- Admin can download any payslip
- Employee can download own payslip
- PDF format
- Password required to open

### 9.5 Payroll History

**Access:** Payroll → Payroll History

**Viewing Past Payroll:**
- Select month and year
- View all payroll records
- Filter by employee or department

**Comparing Months:**
- Compare salary across months
- Identify trends
- Spot anomalies

**Exporting Payroll Data:**
- Export to Excel for analysis
- Export to PDF for records
- Export to CSV for import to other systems

---

## 10. Reports

### 10.1 Attendance Reports

**Access:** Reports → Attendance Reports

**Daily Attendance Report:**
- Shows attendance for specific date
- Lists all employees
- Shows IN/OUT times
- Shows attendance status

**Monthly Attendance Summary:**
- Summary for selected month
- Total present, absent, late days
- Attendance percentage
- Department-wise breakdown

**Late Arrival Report:**
- Lists all late arrivals in period
- Shows arrival time and delay
- Filter by employee or department

**Absenteeism Report:**
- Lists all absences in period
- Shows reason if available
- Pattern analysis

**Department-Wise Attendance:**
- Attendance by department
- Comparison across departments
- Identify underperforming departments

### 10.2 Payroll Reports

**Access:** Reports → Payroll Reports

**Monthly Payroll Summary:**
- Total payroll for month
- Breakdown by component
- Department-wise summary

**Salary Register:**
- Detailed salary register
- All employees
- All components
- Statutory compliance format

**Deduction Report:**
- All deductions
- PF, ESIC, PT breakdown
- Employee-wise summary

**Overtime Report:**
- Overtime hours by employee
- Overtime cost
- Department-wise overtime

**Cost Center Analysis:**
- Cost by department
- Cost by designation
- Trend analysis

### 10.3 Employee Reports

**Access:** Reports → Employee Reports

**Employee Profile Report:**
- Complete employee profile
- All personal and professional details
- Salary information

**Attendance History:**
- Complete attendance history
- Monthly summaries
- Yearly summaries

**Payroll History:**
- Complete payroll history
- Monthly payslips
- Yearly totals

**Leave Balance:**
- Leave balance by type
- Leave taken
- Leave remaining

### 10.4 Export Options

**PDF Export:**
- Professional formatting
- Suitable for printing
- Password protection available

**Excel Export:**
- Spreadsheet format
- Suitable for analysis
- Compatible with Excel, Google Sheets

**CSV Export:**
- Plain text format
- Suitable for data import
- Compatible with most systems

**Custom Date Ranges:**
- Select any date range
- Apply to all reports
- Flexible reporting

---

## 11. Settings

### 11.1 Company Settings

**Access:** Settings → Company Settings

**Company Information:**
- Company name
- Logo upload
- Address
- Contact details
- Website

**Financial Year:**
- Start month
- End month
- Configurable to match fiscal year

**Holiday Calendar:**
- Add company holidays
- Set holiday dates
- Holiday descriptions

### 11.2 Attendance Settings

**Access:** Settings → Attendance Settings

**Working Hours:**
- Start time
- End time
- Working days

**Late Arrival Rules:**
- Threshold (minutes)
- Half-day threshold (hours)

**Half-Day Calculation:**
- Minimum hours for full day
- Minimum hours for half-day

**Overtime Settings:**
- Enable/disable overtime
- Overtime rate multiplier
- Overtime calculation method

**Holiday Calendar:**
- Manage company holidays
- Add/remove holidays

### 11.3 Payroll Settings

**Access:** Settings → Payroll Settings

**Salary Structure:**
- Basic salary percentage
- HRA percentage
- DA percentage
- Other allowances

**Allowances Configuration:**
- Add custom allowances
- Set allowance types (fixed/percentage)
- Configure per employee

**Deduction Rules:**
- PF percentage
- ESIC percentage
- PT amount
- Other deductions

**Statutory Compliance:**
- Configure statutory components
- Set compliance parameters

**Professional Tax Slabs:**
- Configure PT slabs by state
- Set PT amounts

### 11.4 Email Settings

**Access:** Settings → Email Settings

**SMTP Configuration:**
- SMTP server (e.g., smtp.gmail.com)
- SMTP port (e.g., 587 for TLS)
- SMTP username
- SMTP password
- Use TLS (Yes/No)

**Email Templates:**
- Payslip email template
- Notification templates
- Customizable subject and body

**Payslip Email Settings:**
- Enable/disable automatic payslip email
- Email subject
- Email body template
- Password delivery method

**Notification Preferences:**
- Attendance notifications
- Payroll notifications
- Approval notifications

### 11.5 Security Settings

**Access:** Settings → Security Settings

**Password Policies:**
- Minimum password length
- Require special characters
- Require numbers
- Password expiration (optional)

**Session Timeout:**
- Auto-logout after inactivity
- Configurable timeout (minutes)

**Audit Logging:**
- Enable/disable audit logs
- Log retention period
- Log export

**Data Encryption:**
- Verify encryption status
- Encryption key management
- Backup encryption

---

## 12. Approvals Workflow

### 12.1 Manager Approvals

**Access:** Approvals (for managers)

**Logout Approval Requests:**
- Employees request logout approval
- Manager reviews request
- Manager approves or rejects
- Reason for rejection required

**Manual Attendance Approval:**
- Employees submit manual attendance
- Manager reviews and approves
- Manager can reject with reason

**Regularization Requests:**
- Employees request attendance correction
- Manager reviews and approves
- Manager can reject with reason

**Department-Level Approvals:**
- Manager only sees department requests
- Cannot approve other departments
- Clear separation of responsibilities

### 12.2 Admin Approvals

**Access:** Approvals → Admin Approvals (for admins)

**Manager Approval Override:**
- Admin can override manager rejection
- Admin can approve any request
- Use for exceptional cases

**Cross-Department Approvals:**
- Admin can approve any department
- Full visibility across organization
- Used for manager absence

**System-Level Approvals:**
- High-level approvals
- Policy exceptions
- Special cases

**Rejection Handling:**
- Admin can reject any request
- Rejection reason required
- Employee can resubmit

### 12.3 Approval Process

**Viewing Pending Requests:**
- Dashboard shows pending count
- Approvals section lists all requests
- Filter by type and status

**Approving/Rejecting Requests:**
- Click request to view details
- Click "Approve" or "Reject"
- Add remarks for rejection
- Submit decision

**Adding Remarks:**
- Required for rejections
- Optional for approvals
- Helps employees understand decision

**Notification Handling:**
- Employees receive notification
- Email notification if configured
- In-app notification

---

## 13. Employee Portal

### 13.1 Employee Dashboard

**Access:** Employee login → Dashboard

**Personal Attendance Summary:**
- Today's status (Present/Absent/Late)
- Current month attendance
- Attendance percentage

**Today's Status:**
- Check-in time (if checked in)
- Check-out time (if checked out)
- Working hours today

**Pending Requests:**
- Attendance correction requests
- Leave requests
- Other pending requests

**Recent Payslips:**
- Latest 3 payslips
- Quick download links
- Payslip password reminder

### 13.2 My Attendance

**Access:** Employee Portal → My Attendance

**Viewing Personal Attendance:**
- Monthly calendar view
- Daily attendance details
- IN/OUT times
- Attendance status

**Checking IN/OUT Times:**
- View check-in time for each day
- View check-out time for each day
- Total working hours per day
- Late arrival indication

**Requesting Corrections:**
- Click date with incorrect attendance
- Click "Request Correction"
- Enter correct details
- Submit for manager approval

**Requesting Leave:**
- Select leave type
- Select date range
- Provide reason
- Submit for approval

### 13.3 My Payroll

**Access:** Employee Portal → My Payroll

**Viewing Payslips:**
- List of all payslips
- Month-wise
- Download PDF

**Downloading PDFs:**
- Click download button
- Enter payslip password
- PDF opens in browser
- Save to local computer

**Understanding Deductions:**
- View deduction breakdown
- PF, ESIC, PT details
- Other deductions

**Salary Breakdown:**
- Basic salary
- Allowances (HRA, DA, etc.)
- Gross salary
- Deductions
- Net salary

### 13.4 My Profile

**Access:** Employee Portal → My Profile

**Updating Personal Details:**
- Name, email, phone
- Address
- Emergency contact
- Save changes

**Changing Password:**
- Enter current password
- Enter new password
- Confirm new password
- Save changes

**Viewing Payslip Password:**
- Default password shown
- Can be changed by admin
- Keep secure

**Biometric Consent Management:**
- View consent status
- Withdraw consent (deletes face data)
- Re-register face after withdrawal

### 13.5 My Reports

**Access:** Employee Portal → My Reports

**Personal Attendance Report:**
- Complete attendance history
- Monthly summaries
- Export to PDF/Excel

**Personal Payroll History:**
- All payslips
- Yearly totals
- Tax summary

**Leave Balance:**
- Leave balance by type
- Leave taken this year
- Leave remaining

---

## 14. Biometric Data Management

### 14.1 Biometric Consent

**Obtaining Employee Consent:**
- Consent obtained during face registration
- Employee must agree before face capture
- Consent logged in system
- Consent can be withdrawn anytime

**Consent Logging:**
- Date and time of consent
- Purpose of data collection
- Data retention period
- Employee signature (digital)

**Withdrawal Process:**
- Employee requests withdrawal
- Admin processes request
- Face data deleted immediately
- Attendance marking reverts to manual

**Data Deletion on Consent Withdrawal:**
- All face images deleted
- Recognition model retrained without employee
- Historical attendance preserved
- Payroll history preserved

### 14.2 Face Data Security

**Encryption at Rest:**
- Face images encrypted using Fernet
- Encryption key stored securely
- Decryption only for recognition
- No plain-text storage

**Secure Storage:**
- Encrypted files in dataset folder
- Access restricted to application
- No direct file access
- Regular backups

**Access Controls:**
- Only admins can view face data
- Managers cannot access face data
- Employees cannot access others' face data
- Audit trail for all access

**Audit Trail:**
- All face data access logged
- Who accessed, when, why
- Regular audit reviews
- Compliance monitoring

### 14.3 Face Data Management

**Updating Face Images:**
- Employee can request update
- Admin captures new images
- Old images deleted
- Model retrained

**Retraining Recognition Model:**
- Required after adding new employees
- Required after updating face images
- Required after deleting employees
- Takes 1-2 minutes

**Deleting Face Data:**
- Admin can delete face data
- Employee consent withdrawal
- Data deletion confirmation
- Cannot be undone

**Data Backup Considerations:**
- Face data included in backups
- Encrypted in backups
- Backup retention policy
- Secure backup storage

---

## 15. Backup and Restore

### 15.1 Automated Backups

**Configuring Backup Schedule:**
- Access: Settings → Backup Settings
- Set backup frequency (daily, weekly)
- Set backup time
- Set backup location

**Backup Location:**
- Local folder (default)
- Network drive
- Cloud storage (if configured)
- External drive

**Backup Encryption:**
- Backups encrypted by default
- AES-256 encryption
- Backup password required
- Secure key management

**Retention Policy:**
- Set number of backups to retain
- Old backups automatically deleted
- Configurable retention period
- Compliance requirements

### 15.2 Manual Backup

**Creating On-Demand Backup:**
- Access: Settings → Backup
- Click "Create Backup"
- Select backup location
- Enter backup password
- Wait for backup to complete

**Selecting Data to Backup:**
- Database (required)
- Face data (optional)
- Payslips (optional)
- Uploads (optional)

**Backup Verification:**
- Verify backup file exists
- Check backup file_SIZE
- Test restore (optional)
- Log backup completion

### 15.3 Restore Process

**Restoring from Backup:**
- Access: Settings → Restore
- Select backup file
- Enter backup password
- Select restore options
- Click "Restore"

**Data Integrity Check:**
- Automatic integrity check
- Verify database consistency
- Check file completeness
- Report any errors

**Rollback Procedures:**
- Select backup to restore
- Current data backed up before restore
- Restore process logged
- Confirm restore completion

**Emergency Recovery:**
- Use latest backup
- Contact support if needed
- Provide backup file
- Support can assist with recovery

---

## 16. Troubleshooting

### 16.1 Installation Issues

**Installation Fails:**
- Ensure you have admin rights
- Check available disk space (5 GB minimum)
- Disable antivirus temporarily
- Run installer as administrator
- Contact support if issue persists

**Port Already in Use:**
- Another application using port 5000
- Close other applications
- Or configure different port in .env file
- Restart application

**Firewall Blocking:**
- Add exception for application in Windows Firewall
- Allow Python/Flask through firewall
- Check antivirus firewall settings
- Contact IT administrator if needed

**Antivirus Interference:**
- Add application to antivirus exclusions
- Exclude installation directory from scanning
- Temporarily disable antivirus during installation
- Re-enable after installation

### 16.2 License Issues

**License Key Not Accepted:**
- Verify 16-character key entered correctly
- Check key matches machine fingerprint
- Ensure key is for correct license tier
- Contact support with key and fingerprint

**Machine Fingerprint Mismatch:**
- Re-check fingerprint in Settings → License
- Ensure correct fingerprint provided to vendor
- Fingerprint is case-sensitive
- Contact support if issue persists

**Trial Expired:**
- Purchase license to continue
- Trial data is preserved
- Activate license in Settings → License
- Contact support for purchase assistance

**Activation Count Exceeded:**
- Each license allows 2 activations
- Contact support for additional activations
- Provide license key and machine fingerprints
- May require additional fee

### 16.3 Face Recognition Issues

**Camera Not Detected:**
- Check camera is connected
- Ensure camera drivers installed
- Test camera in Windows Camera app
- Restart application
- Try different USB port

**Poor Recognition Accuracy:**
- Ensure good lighting
- Retrain recognition model
- Capture more face images
- Check camera positioning
- Clean camera lens

**Lighting Problems:**
- Use bright, even lighting
- Avoid backlighting
- Use artificial light if needed
- Position away from windows
- Test at different times of day

**Multiple Face Detection:**
- Ensure only one person in frame
- Position camera to avoid background faces
- Use background screen if needed
- Train model with varied angles

### 16.4 Database Issues

**Database Corruption:**
- Restore from backup
- Contact support for assistance
- Provide error message
- Provide database file if requested

**Connection Errors:**
- Restart application
- Check database file permissions
- Ensure database file not locked
- Check disk space

**Slow Performance:**
- Optimize database (Settings → Database → Optimize)
- Archive old records
- Increase system RAM
- Close other applications

**Data Inconsistency:**
- Run data integrity check
- Restore from backup if needed
- Contact support for assistance
- Provide specific error details

### 16.5 Payroll Calculation Issues

**Incorrect Calculations:**
- Verify attendance data
- Check payroll settings
- Verify salary components
- Review manual adjustments
- Recalculate payroll

**Missing Deductions:**
- Check deduction settings
- Verify employee eligibility
- Check salary thresholds
- Review statutory compliance

**Overtime Not Calculated:**
- Verify overtime enabled in settings
- Check attendance times
- Verify overtime threshold
- Review overtime rate

**Proration Errors:**
- Check joining date
- Verify leaving date
- Review proration method
- Manually adjust if needed

### 16.6 Email Issues

**SMTP Connection Failed:**
- Verify SMTP settings
- Check internet connection
- Verify SMTP credentials
- Check firewall settings
- Test SMTP with email client

**Emails Not Sending:**
- Check SMTP configuration
- Verify recipient email addresses
- Check spam folder
- Review email logs
- Contact SMTP provider

**Attachment Errors:**
- Check file permissions
- Verify file exists
- Check file size limits
- Review SMTP provider limits

**Spam Filter Issues:**
- Add sender to whitelist
- Check spam folder
- Use reputable SMTP provider
- Configure SPF/DKIM records

### 16.7 Performance Issues

**Slow Application Response:**
- Close other applications
- Increase system RAM
- Optimize database
- Archive old records
- Restart application

**High CPU Usage:**
- Close other applications
- Check for background processes
- Restart computer
- Update system drivers

**Memory Issues:**
- Increase system RAM
- Close other applications
- Restart application
- Check for memory leaks

**Database Optimization:**
- Run database optimization
- Archive old records
- Rebuild indexes
- Compact database

---

## 17. FAQ

### 17.1 General Questions

**Q: What happens if I lose my license key?**
A: Contact support with your machine fingerprint and purchase details. Support can resend your license key.

**Q: Can I install on multiple computers?**
A: Each license allows up to 2 machine activations. Additional activations require additional licenses or support approval.

**Q: How do I upgrade to a higher license tier?**
A: Contact support for upgrade options. You'll receive a prorated invoice and new license key.

**Q: Is internet connection required?**
A: No, the system works offline. Internet is only required for email features and license activation.

### 17.2 Attendance Questions

**Q: What if face recognition fails?**
A: Use manual attendance entry. Contact admin to mark attendance manually. Consider retraining recognition model.

**Q: Can I mark attendance manually?**
A: Yes, admins can mark manual attendance. Employees can request manual attendance with manager approval.

**Q: How are late arrivals calculated?**
A: Late arrivals are calculated based on the threshold set in Settings (default: 15 minutes after start time).

**Q: What happens on Sundays?**
A: Sundays are automatically marked as weekly holidays. No attendance is required or expected.

### 17.3 Payroll Questions

**Q: How is overtime calculated?**
A: Overtime is calculated for hours worked beyond the configured working hours. Rate multiplier is configurable (default: 1.5x).

**Q: Can I adjust payroll after generation?**
A: Yes, admins can make manual adjustments before finalization. After finalization, admin override is required.

**Q: How are statutory deductions calculated?**
A: Statutory deductions (PF, ESIC, PT) are calculated based on government rules. Configurable in Settings.

**Q: What is the payslip password?**
A: Default password is employee ID or PAN number. Can be changed by admin in Settings.

### 17.4 Security Questions

**Q: Is my data secure?**
A: Yes, all data is encrypted at rest. Biometric data uses Fernet encryption, payslips use AES-256 encryption.

**Q: What happens to biometric data?**
A: Biometric data is encrypted and stored securely. Employees can withdraw consent anytime, which deletes their face data.

**Q: Can I encrypt my database?**
A: The database uses SQLite with file-level encryption. Additional encryption can be configured in Settings.

**Q: How do I backup my data?**
A: Use Settings → Backup to create manual backups. Configure automated backups in Settings → Backup Settings.

---

## 18. Support

### 18.1 Contact Information

**Email Support:**
- support@yourcompany.com
- Response time: 24-48 hours
- Include license key and machine fingerprint in all emails

**Phone Support:**
- Available for paid licenses (Small Business and above)
- Response time: During business hours
- Call: +91 XXXXX XXXXX

**Support Hours:**
- Monday to Friday: 9:00 AM to 6:00 PM IST
- Saturday: 9:00 AM to 1:00 PM IST
- Sunday: Closed

**Response Time SLA:**
- Critical issues: 4 hours
- High priority: 24 hours
- Medium priority: 48 hours
- Low priority: 72 hours

### 18.2 Support Process

**How to Report Issues:**
1. Check this manual for troubleshooting
2. Check error message details
3. Gather system information (OS version, error logs)
4. Email support with details
5. Include license key and machine fingerprint

**Information to Provide:**
- License key
- Machine fingerprint
- Error message (exact text)
- Steps to reproduce issue
- System information (OS, RAM, etc.)
- Screenshots (if applicable)

**Bug Reporting:**
- Email support with bug details
- Include steps to reproduce
- Provide expected vs actual behavior
- Include screenshots if applicable

**Feature Requests:**
- Email support with feature description
- Explain use case
- Provide business justification
- Priority assessment by support team

### 18.3 Training Resources

**Video Tutorials:**
- Available on YouTube channel
- Cover all major features
- Step-by-step guidance
- Updated regularly

**Webinar Schedule:**
- Monthly webinars for new features
- Q&A sessions
- Advanced training
- Registration via website

**On-Site Training:**
- Available for Enterprise licenses
- Additional cost applies
- Customized to your needs
- Contact support for pricing

**Online Documentation:**
- This user manual
- Knowledge base articles
- FAQ section
- Video tutorials

### 18.4 Community

**User Forum:**
- Community support
- Best practices sharing
- Tips and tricks
- Peer assistance

**Knowledge Base:**
- Detailed articles
- Common issues and solutions
- Feature guides
- Configuration examples

**Best Practices Guide:**
- Recommended configurations
- Security best practices
- Optimization tips
- Industry standards

**Tips and Tricks:**
- Productivity tips
- Shortcuts
- Advanced features
- Hidden capabilities

---

## Appendix

### A. Glossary

**AES-256**: Advanced Encryption Standard with 256-bit key strength

**API**: Application Programming Interface

**CSV**: Comma-Separated Values (file format)

**Fernet**: Symmetric encryption algorithm (AES-128-CBC + HMAC)

**GUI**: Graphical User Interface

**HRA**: House Rent Allowance

**DA**: Dearness Allowance

**PF**: Provident Fund

**ESIC**: Employee State Insurance Corporation

**PT**: Professional Tax

**SMTP**: Simple Mail Transfer Protocol

**SQL**: Structured Query Language

**UI**: User Interface

### B. Keyboard Shortcuts

**Navigation:**
- Alt + D: Go to Dashboard
- Alt + A: Go to Attendance
- Alt + E: Go to Employees
- Alt + P: Go to Payroll
- Alt + R: Go to Reports
- Alt + S: Go to Settings

**Forms:**
- Tab: Move to next field
- Shift + Tab: Move to previous field
- Enter: Submit form
- Escape: Cancel form

**General:**
- Ctrl + S: Save (where applicable)
- Ctrl + F: Search
- Ctrl + P: Print
- F5: Refresh page

### C. Error Codes

**ERR-001**: Database connection failed
- Solution: Restart application, check database file permissions

**ERR-002**: Camera not detected
- Solution: Check camera connection, install drivers

**ERR-003**: Face recognition model not trained
- Solution: Train recognition model in employee profile

**ERR-004**: License validation failed
- Solution: Check license key, contact support

**ERR-005**: SMTP connection failed
- Solution: Verify SMTP settings, check internet connection

**ERR-006**: Payroll calculation error
- Solution: Verify attendance data, check payroll settings

**ERR-007**: File write permission denied
- Solution: Check folder permissions, run as administrator

**ERR-008**: Encryption key not found
- Solution: Check .env file, regenerate encryption key

### D. Regulatory Compliance

**GDPR Compliance:**
- Biometric consent required
- Right to data deletion
- Data portability
- Privacy by design

**Biometric Data Laws:**
- Consent before collection
- Secure storage
- Limited retention
- Purpose limitation

**Labor Law Considerations:**
- Attendance records retention (7 years)
- Payroll records retention (7 years)
- Statutory compliance (PF, ESIC, PT)
- Working hours regulations

### E. Version History

**Version 1.0.0** (Current Release)
- Initial commercial release
- Face recognition attendance
- Automated payroll
- Encrypted payslips
- Role-based access control
- Offline capability

**Planned Features (Future Releases):**
- Mobile app for employees
- Cloud sync option
- Advanced analytics
- Integration with accounting software
- Multi-language support

---

**End of User Manual**

For additional support, contact: support@yourcompany.com
Website: https://yourcompany.com
