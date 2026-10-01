# AI Attendance & Payroll System - User Manual

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
- What is the AI Attendance & Payroll System?
- Key features and capabilities
- Target users (Admins, Managers, Employees)

### 1.2 System Architecture
- Desktop application architecture
- Facial recognition technology
- Data storage (SQLite/MySQL)
- Offline capability

### 1.3 Security Features
- AES-256 encryption for payslips
- Fernet encryption for biometric data
- Password protection
- Role-based access control

---

## 2. System Requirements

### 2.1 Hardware Requirements
- Operating System: Windows 10/11 (64-bit)
- Processor: Intel i5 or equivalent (4 cores recommended)
- RAM: 8 GB minimum, 16 GB recommended
- Storage: 5 GB free space
- Webcam: Required for face recognition (HD recommended)
- Network: Optional (for email features)

### 2.2 Software Requirements
- No additional software required
- Microsoft .NET Framework (included with Windows)
- Web browser (Chrome, Edge, Firefox)

---

## 3. Installation Guide

### 3.1 Installing the Software
- Step-by-step installation via installer
- Installation directory selection
- Desktop shortcut creation
- Windows Service option (optional)

### 3.2 First Launch
- Auto-opening the web interface
- Browser compatibility
- Default port (5000)

### 3.3 License Activation
- Trial period (30 days)
- License key entry
- Machine fingerprint explanation
- Troubleshooting activation issues

---

## 4. First-Time Setup

### 4.1 Setup Wizard
- Creating the first admin account
- Setting company information
- Configuring working hours
- Setting attendance rules
- Configuring payroll settings

### 4.2 Company Settings
- Company name and logo
- Address and contact details
- Financial year configuration
- Holiday calendar setup

### 4.3 Initial Configuration
- Attendance policies
- Late arrival rules
- Overtime calculation
- Leave management

---

## 5. Getting Started

### 5.1 Dashboard Overview
- Navigation menu
- Quick stats cards
- Recent activity feed
- Pending approvals indicator

### 5.2 User Roles
- Admin (full access)
- Manager (department-level access)
- Employee (self-service access)

### 5.3 Basic Navigation
- Moving between sections
- Using search and filters
- Exporting data

---

## 6. Admin Dashboard

### 6.1 Dashboard Widgets
- Present today count
- Absent today count
- Late arrivals
- Pending approvals
- Monthly attendance summary
- Payroll status

### 6.2 Quick Actions
- Add new employee
- Mark manual attendance
- Generate payroll
- View reports

### 6.3 Date Range Filters
- Filtering by date range
- Today, this week, this month presets
- Custom date selection

---

## 7. Employee Management

### 7.1 Adding Employees
- Manual entry form
- Required fields
- Employee ID generation
- Department assignment

### 7.2 Employee Profile
- Personal information
- Contact details
- Joining date and designation
- Salary details
- Bank information

### 7.3 Face Registration
- Capturing face images
- Number of images required (5-10 recommended)
- Lighting and positioning guidelines
- Training the recognition model
- Retraining when needed

### 7.4 Editing Employees
- Updating employee details
- Changing department/role
- Salary adjustments
- Status changes (active/inactive)

### 7.5 Deleting Employees
- Soft delete vs. hard delete
- Data retention policies
- Payroll history preservation

---

## 8. Attendance Management

### 8.1 Face Recognition Attendance
- Kiosk mode setup
- Camera positioning
- Real-time recognition
- Automatic IN/OUT marking

### 8.2 Manual Attendance Entry
- Adding attendance for employees
- IN/OUT time entry
- Reason for manual entry
- Approval workflow

### 8.3 Attendance Regularization
- Requesting corrections
- Manager approval process
- Admin override capability

### 8.4 Attendance Rules
- Sunday as weekly holiday
- Late arrival thresholds
- Half-day calculation
- Overtime tracking

### 8.5 Attendance History
- Viewing past attendance
- Filtering by date/employee
- Exporting attendance records

---

## 9. Payroll Management

### 9.1 Payroll Configuration
- Salary components (Basic, HRA, DA, etc.)
- Allowances and deductions
- Statutory deductions (PF, ESIC, PT)
- Overtime rates

### 9.2 Generating Payroll
- Monthly payroll generation
- Selecting employees
- Calculation preview
- Finalizing payroll

### 9.3 Payroll Review
- Reviewing calculated amounts
- Manual adjustments
- Approval workflow
- Locking payroll

### 9.4 Payslip Generation
- PDF payslip creation
- Password protection
- Email delivery
- Download options

### 9.5 Payroll History
- Viewing past payroll
- Comparing months
- Exporting payroll data

---

## 10. Reports

### 10.1 Attendance Reports
- Daily attendance report
- Monthly attendance summary
- Late arrival report
- Absenteeism report
- Department-wise attendance

### 10.2 Payroll Reports
- Monthly payroll summary
- Salary register
- Deduction report
- Overtime report
- Cost center analysis

### 10.3 Employee Reports
- Employee profile report
- Attendance history
- Payroll history
- Leave balance

### 10.4 Export Options
- PDF export
- Excel export
- CSV export
- Custom date ranges

---

## 11. Settings

### 11.1 Company Settings
- Company information
- Logo upload
- Contact details
- Financial year

### 11.2 Attendance Settings
- Working hours
- Late arrival rules
- Half-day calculation
- Overtime settings
- Holiday calendar

### 11.3 Payroll Settings
- Salary structure
- Allowances configuration
- Deduction rules
- Statutory compliance
- Professional tax slabs

### 11.4 Email Settings
- SMTP configuration
- Email templates
- Payslip email settings
- Notification preferences

### 11.5 Security Settings
- Password policies
- Session timeout
- Audit logging
- Data encryption

---

## 12. Approvals Workflow

### 12.1 Manager Approvals
- Logout approval requests
- Manual attendance approval
- Regularization requests
- Department-level approvals

### 12.2 Admin Approvals
- Manager approval override
- Cross-department approvals
- System-level approvals
- Rejection handling

### 12.3 Approval Process
- Viewing pending requests
- Approving/rejecting requests
- Adding remarks
- Notification handling

---

## 13. Employee Portal

### 13.1 Employee Dashboard
- Personal attendance summary
- Today's status
- Pending requests
- Recent payslips

### 13.2 My Attendance
- Viewing personal attendance
- Checking IN/OUT times
- Requesting corrections
- Viewing attendance calendar

### 13.3 My Payroll
- Viewing payslips
- Downloading PDFs
- Understanding deductions
- Salary breakdown

### 13.4 My Profile
- Updating personal details
- Changing password
- Viewing payslip password
- Biometric consent management

### 13.5 My Reports
- Personal attendance report
- Personal payroll history
- Leave balance

---

## 14. Biometric Data Management

### 14.1 Biometric Consent
- Obtaining employee consent
- Consent logging
- Withdrawal process
- Data deletion on consent withdrawal

### 14.2 Face Data Security
- Encryption at rest
- Secure storage
- Access controls
- Audit trail

### 14.3 Face Data Management
- Updating face images
- Retraining recognition model
- Deleting face data
- Data backup considerations

---

## 15. Backup and Restore

### 15.1 Automated Backups
- Configuring backup schedule
- Backup location
- Backup encryption
- Retention policy

### 15.2 Manual Backup
- Creating on-demand backup
- Selecting data to backup
- Backup verification

### 15.3 Restore Process
- Restoring from backup
- Data integrity check
- Rollback procedures
- Emergency recovery

---

## 16. Troubleshooting

### 16.1 Installation Issues
- Installation fails
- Port already in use
- Firewall blocking
- Antivirus interference

### 16.2 License Issues
- License key not accepted
- Machine fingerprint mismatch
- Trial expired
- Activation count exceeded

### 16.3 Face Recognition Issues
- Camera not detected
- Poor recognition accuracy
- Lighting problems
- Multiple face detection

### 16.4 Database Issues
- Database corruption
- Connection errors
- Slow performance
- Data inconsistency

### 16.5 Payroll Calculation Issues
- Incorrect calculations
- Missing deductions
- Overtime not calculated
- Proration errors

### 16.6 Email Issues
- SMTP connection failed
- Emails not sending
- Attachment errors
- Spam filter issues

### 16.7 Performance Issues
- Slow application response
- High CPU usage
- Memory issues
- Database optimization

---

## 17. FAQ

### 17.1 General Questions
- What happens if I lose my license key?
- Can I install on multiple computers?
- How do I upgrade to a higher license tier?
- Is internet connection required?

### 17.2 Attendance Questions
- What if face recognition fails?
- Can I mark attendance manually?
- How are late arrivals calculated?
- What happens on Sundays?

### 17.3 Payroll Questions
- How is overtime calculated?
- Can I adjust payroll after generation?
- How are statutory deductions calculated?
- What is the payslip password?

### 17.4 Security Questions
- Is my data secure?
- What happens to biometric data?
- Can I encrypt my database?
- How do I backup my data?

---

## 18. Support

### 18.1 Contact Information
- Email support
- Phone support (for paid licenses)
- Support hours
- Response time SLA

### 18.2 Support Process
- How to report issues
- Information to provide
- Bug reporting
- Feature requests

### 18.3 Training Resources
- Video tutorials
- Webinar schedule
- On-site training (additional cost)
- Online documentation

### 18.4 Community
- User forum
- Knowledge base
- Best practices guide
- Tips and tricks

---

## Appendix

### A. Glossary
- Key terminology definitions

### B. Keyboard Shortcuts
- Quick navigation shortcuts
- Form shortcuts

### C. Error Codes
- Common error codes and solutions

### D. Regulatory Compliance
- GDPR compliance guide
- Biometric data laws
- Labor law considerations

### E. Version History
- Release notes
- New features
- Bug fixes
