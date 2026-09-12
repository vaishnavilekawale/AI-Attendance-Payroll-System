from datetime import datetime
from database import db
from werkzeug.security import generate_password_hash, check_password_hash

class Admin(db.Model):
    __tablename__ = 'admins'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    temporary_password_hash = db.Column(db.String(255))
    temporary_password_created_at = db.Column(db.DateTime)
    email = db.Column(db.String(120), unique=True, nullable=False)
    force_password_change = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def set_temporary_password(self, password):
        self.temporary_password_hash = generate_password_hash(password)
        self.temporary_password_created_at = datetime.utcnow()
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def check_temporary_password(self, password):
        if not self.temporary_password_hash:
            return False
        return check_password_hash(self.temporary_password_hash, password)
    
    def is_temporary_password_valid(self):
        if not self.temporary_password_hash or not self.temporary_password_created_at:
            return False
        from datetime import timedelta
        return datetime.utcnow() < self.temporary_password_created_at + timedelta(minutes=30)
    
    def clear_temporary_password(self):
        self.temporary_password_hash = None
        self.temporary_password_created_at = None

class Employee(db.Model):
    __tablename__ = 'employees'
    
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    department = db.Column(db.String(50), nullable=False)
    designation = db.Column(db.String(50), nullable=False)
    basic_salary = db.Column(db.Numeric(10, 2, asdecimal=False), nullable=False)
    joining_date = db.Column(db.Date, nullable=False)
    dob = db.Column(db.Date, nullable=True)  # Date of birth - used to build the payslip PDF password
    email = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    address = db.Column(db.Text, nullable=False)
    profile_photo = db.Column(db.String(255))
    # Optional custom payslip PDF-open password, stored encrypted at rest
    # (see crypto_utils.py) since a PDF-open password must be recoverable
    # in plaintext and can't simply be one-way hashed like a login
    # password. When NULL, pdf_generator.generate_payslip_password() falls
    # back to its default strengthened, deterministic formula.
    payslip_password_override = db.Column(db.String(255), nullable=True)
    face_images_count = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default='active')  # active, inactive
    office_location = db.Column(db.String(100))
    bank_name = db.Column(db.String(100))
    bank_account_number = db.Column(db.String(50))
    # Additional bank and tax details for professional payslip
    pan_number = db.Column(db.String(20))
    uan_number = db.Column(db.String(20))
    pf_number = db.Column(db.String(20))
    # Salary structure for professional payslip
    hra = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    da = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    medical_allowance = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    travel_allowance = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    special_allowance = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    other_allowances = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    # Deduction percentages and fixed monthly deductions
    employee_pf_percentage = db.Column(db.Float, default=12.0)
    employer_pf_percentage = db.Column(db.Float, default=12.0)
    esic_percentage = db.Column(db.Float, default=0.75)
    tds_percentage = db.Column(db.Float, default=0.0)
    bus_charges = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    other_deduction = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Authentication fields
    username = db.Column(db.String(80), unique=True, nullable=False)  # Same as employee_id
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='employee')  # admin, employee, manager
    must_change_password = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime)

    # ------------------------------------------------------------------
    # BIOMETRIC DATA CONSENT (DPDP Act, 2023 / privacy compliance)
    # ------------------------------------------------------------------
    # Face images captured for attendance recognition are "biometric data"
    # under India's Digital Personal Data Protection Act, 2023 (and treated
    # similarly under GDPR Art. 9 / most global privacy regimes) - a
    # sensitive/special category of personal data that requires clear,
    # informed, explicit, and separately recorded consent BEFORE
    # collection, not a blanket "I agree to the terms" checkbox buried in
    # onboarding paperwork.
    #
    # These columns hold the CURRENT consent state for quick checks (e.g.
    # "can we let this employee start face capture right now?"). They are
    # deliberately NOT the only record of consent - see BiometricConsentLog
    # below for the append-only audit trail a regulator or the employee
    # themself may ask to see later. Withdrawing consent must update these
    # columns (biometric_consent_given=False, timestamp of withdrawal) AND
    # append a new BiometricConsentLog row; it must never simply delete or
    # overwrite prior history.
    biometric_consent_given = db.Column(
        db.Boolean, nullable=False, default=False,
        doc="Current biometric (face data) collection consent status. "
            "Must be True before /api/upload-face-image or /capture-face "
            "will accept any image for this employee - enforced in "
            "app.py, not just in the UI, since a UI checkbox alone is not "
            "a real control.",
    )
    biometric_consent_timestamp = db.Column(
        db.DateTime, nullable=True,
        doc="UTC timestamp of the most recent consent decision (grant or "
            "withdrawal) reflected in biometric_consent_given. NULL means "
            "no consent decision has ever been recorded for this employee.",
    )
    biometric_consent_version = db.Column(
        db.String(20), nullable=True,
        doc="Version identifier of the privacy/consent notice the employee "
            "agreed to (e.g. 'v1.0'). Bump this whenever the notice text "
            "changes materially so previously-collected consent can be "
            "distinguished from consent to the current wording, and so "
            "affected employees can be prompted to re-consent.",
    )
    biometric_consent_ip_address = db.Column(
        db.String(45), nullable=True,
        doc="IP address (IPv4/IPv6) the consent decision was submitted "
            "from, for the audit trail. Best-effort only - this is a "
            "single desktop/LAN deployment behind Werkzeug, not a public "
            "internet-facing service, so treat this as supporting "
            "evidence rather than strong identity proof.",
    )

    # Relationships
    attendance_records = db.relationship('Attendance', backref='employee', lazy=True, cascade='all, delete-orphan')
    payroll_records = db.relationship('Payroll', backref='employee', lazy=True, cascade='all, delete-orphan')
    consent_logs = db.relationship('BiometricConsentLog', backref='employee', lazy=True, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def record_biometric_consent(self, granted, ip_address=None, policy_version='v1.0', notes=None):
        """
        Single entry point for changing biometric consent state. Always use
        this instead of setting biometric_consent_given directly, so the
        current-state columns and the append-only audit log can never drift
        out of sync with each other.

        `granted=True`  -> employee/admin has just given consent (checkbox
                            ticked on the consent modal before face capture).
        `granted=False` -> consent has been withdrawn (e.g. employee asked
                            for their biometric data collection to stop).
                            This method does NOT delete any already-captured
                            face images/embeddings - that is a separate,
                            explicit "erase biometric data" action, since
                            withdrawing consent for future collection and
                            requesting deletion of already-collected data
                            are two distinct data-subject rights under DPDP.
        """
        now = datetime.utcnow()
        self.biometric_consent_given = bool(granted)
        self.biometric_consent_timestamp = now
        self.biometric_consent_version = policy_version
        self.biometric_consent_ip_address = ip_address

        log_entry = BiometricConsentLog(
            employee_id=self.id,
            granted=bool(granted),
            policy_version=policy_version,
            ip_address=ip_address,
            notes=notes,
            created_at=now,
        )
        db.session.add(log_entry)
        return log_entry


class BiometricConsentLog(db.Model):
    """
    Append-only audit trail of every biometric consent decision (grant or
    withdrawal) made for an employee, across the employee's entire lifetime
    at the company.

    WHY THIS EXISTS SEPARATELY FROM Employee.biometric_consent_given:
    the columns on Employee only ever hold the CURRENT state - if an
    employee grants consent, later withdraws it, then grants it again, the
    Employee row shows only the most recent decision. Under DPDP (and most
    other privacy regimes), an organization must be able to demonstrate
    consent history on request - not just current status - so this table
    is intentionally never updated or deleted, only appended to. Rows are
    NOT cascaded-deleted independently; they cascade only if the parent
    Employee row itself is deleted (see Employee.consent_logs), matching
    how the rest of this codebase handles employee-owned child records.
    """
    __tablename__ = 'biometric_consent_log'

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    granted = db.Column(db.Boolean, nullable=False)
    policy_version = db.Column(db.String(20), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    notes = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class Attendance(db.Model):
    __tablename__ = 'attendance'
    
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    in_time = db.Column(db.DateTime)
    out_time = db.Column(db.DateTime)
    total_hours = db.Column(db.Float, default=0.0)
    late_entry = db.Column(db.Boolean, default=False)
    early_exit = db.Column(db.Boolean, default=False)
    overtime_hours = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default='absent')  # present, absent, half_day, late
    confidence = db.Column(db.Float)
    # How this specific attendance record was captured - e.g.
    # 'FACE_RECOGNITION' (default, camera auto-scan) or 'MANUAL_PASSWORD'
    # (Employee ID + password fallback, used when face recognition fails).
    # Nullable/defaulted so existing rows and other call sites that don't
    # set it explicitly are unaffected.
    attendance_type = db.Column(db.String(30), default='FACE_RECOGNITION')
    # Approval status for manual attendance: 'approved', 'pending', 'rejected'
    # Only applies to MANUAL_PASSWORD attendance type. FACE_RECOGNITION is always 'approved'.
    approval_status = db.Column(db.String(20), default='approved')
    # Remark typed by the Manager/Admin when a manual attendance (regularization)
    # request is rejected. Displayed on the Manager and Admin approval dashboards
    # so the employee/approver history shows WHY the request was rejected.
    rejection_remarks = db.Column(db.Text)
    # The exact timestamp when the employee clicked "Mark Attendance" (for manual attendance)
    # This serves as proof of check-in time and is included in email notifications
    submission_timestamp = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Ensure unique employee per day
    __table_args__ = (db.UniqueConstraint('employee_id', 'date', name='unique_employee_date'),)

class Payroll(db.Model):
    __tablename__ = 'payroll'
    
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    month = db.Column(db.Integer, nullable=False)
    year = db.Column(db.Integer, nullable=False)
    basic_salary = db.Column(db.Numeric(10, 2, asdecimal=False), nullable=False)
    working_days = db.Column(db.Integer, default=0)
    present_days = db.Column(db.Integer, default=0)
    absent_days = db.Column(db.Integer, default=0)
    half_days = db.Column(db.Integer, default=0)
    late_days = db.Column(db.Integer, default=0)
    paid_days = db.Column(db.Float, default=0.0)
    lop_days = db.Column(db.Float, default=0.0)
    total_hours_worked = db.Column(db.Float, default=0.0)
    overtime_hours = db.Column(db.Float, default=0.0)
    per_day_salary = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    absent_deduction = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    lop_deduction = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    half_day_deduction = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    late_deduction = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    overtime_bonus = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    # Individual allowances (persisted from employee salary structure)
    hra = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    da = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    medical_allowance = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    travel_allowance = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    special_allowance = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    other_allowances = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    # Pro-rata reference: the employee's ORIGINAL/full monthly figures at
    # the time this payroll was calculated, before the working-days
    # proration factor was applied. Persisted (rather than re-read from
    # Employee at display time) so a payslip stays accurate even if the
    # employee's salary structure changes in a later month.
    proration_factor = db.Column(db.Float, default=1.0)
    full_basic_salary = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    full_hra = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    full_da = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    full_medical_allowance = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    full_travel_allowance = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    full_special_allowance = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    full_other_allowances = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    # Professional tax and other deductions for professional payslip
    professional_tax = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    employee_pf = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    esic = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    tds = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    bus_charges = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    other_deduction = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    total_deductions = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    employer_pf = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    gross_salary = db.Column(db.Numeric(10, 2, asdecimal=False), nullable=False)
    net_salary = db.Column(db.Numeric(10, 2, asdecimal=False), nullable=False)
    net_ctc = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    payslip_generated = db.Column(db.Boolean, default=False)
    payslip_path = db.Column(db.String(255))
    email_sent = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Ensure unique employee per month
    __table_args__ = (db.UniqueConstraint('employee_id', 'month', 'year', name='unique_employee_month'),)

class Settings(db.Model):
    __tablename__ = 'settings'
    
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(100), default='AI Attendance System')
    company_logo = db.Column(db.String(255))
    office_start_time = db.Column(db.String(5), default='09:00')
    office_end_time = db.Column(db.String(5), default='18:00')
    grace_period_minutes = db.Column(db.Integer, default=15)
    working_hours_per_day = db.Column(db.Float, default=9.0)
    half_day_hours = db.Column(db.Float, default=4.5)  # Half of working hours by default
    late_deduction_enabled = db.Column(db.Boolean, default=False)
    late_deduction_per_occurrence = db.Column(db.Numeric(10, 2, asdecimal=False), default=0.0)
    half_day_deduction_enabled = db.Column(db.Boolean, default=True)
    half_day_deduction_per_occurrence = db.Column(db.Numeric(10, 2, asdecimal=False), default=200.0)
    absent_deduction_enabled = db.Column(db.Boolean, default=True)
    absent_deduction_per_occurrence = db.Column(db.Numeric(10, 2, asdecimal=False), default=500.0)
    overtime_enabled = db.Column(db.Boolean, default=True)
    overtime_rate = db.Column(db.Float, default=1.5)
    face_recognition_tolerance = db.Column(db.Float, default=0.6)
    min_face_images_required = db.Column(db.Integer, default=20)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    @classmethod
    def get_settings(cls):
        settings = cls.query.first()
        if not settings:
            settings = cls()
            db.session.add(settings)
            db.session.commit()
        return settings

class WorkingHours(db.Model):
    __tablename__ = 'working_hours'
    
    id = db.Column(db.Integer, primary_key=True)
    attendance_id = db.Column(db.Integer, db.ForeignKey('attendance.id'), nullable=False)
    in_time = db.Column(db.DateTime, nullable=False)
    out_time = db.Column(db.DateTime)
    total_hours = db.Column(db.Float, default=0.0)
    is_late = db.Column(db.Boolean, default=False)
    is_early_exit = db.Column(db.Boolean, default=False)
    overtime_hours = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class EmployeeLogin(db.Model):
    __tablename__ = 'employee_login'
    
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    temporary_password_hash = db.Column(db.String(255))
    temporary_password_created_at = db.Column(db.DateTime)
    first_login = db.Column(db.Boolean, default=True)
    force_password_change = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime)
    password_reset_token = db.Column(db.String(255))
    password_reset_expiry = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship
    employee = db.relationship('Employee', backref='login_credentials')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def set_temporary_password(self, password):
        self.temporary_password_hash = generate_password_hash(password)
        self.temporary_password_created_at = datetime.utcnow()
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def check_temporary_password(self, password):
        if not self.temporary_password_hash:
            return False
        return check_password_hash(self.temporary_password_hash, password)
    
    def is_temporary_password_valid(self):
        if not self.temporary_password_hash or not self.temporary_password_created_at:
            return False
        from datetime import timedelta
        return datetime.utcnow() < self.temporary_password_created_at + timedelta(minutes=30)
    
    def clear_temporary_password(self):
        self.temporary_password_hash = None
        self.temporary_password_created_at = None
    
    def generate_reset_token(self):
        import secrets
        self.password_reset_token = secrets.token_urlsafe(32)
        from datetime import timedelta
        self.password_reset_expiry = datetime.utcnow() + timedelta(hours=1)
        return self.password_reset_token
    
    def is_reset_token_valid(self):
        if not self.password_reset_token or not self.password_reset_expiry:
            return False
        return datetime.utcnow() < self.password_reset_expiry

class AttendanceActivity(db.Model):
    __tablename__ = 'attendance_activities'
    
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    attendance_date = db.Column(db.Date, nullable=False)
    activity_time = db.Column(db.Time, nullable=False)
    action = db.Column(db.String(10), nullable=False)  # IN or OUT
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationship
    employee = db.relationship('Employee', backref='attendance_activities')

class PayrollSettings(db.Model):
    """Payroll automation settings for automatic payroll generation"""
    __tablename__ = 'payroll_settings'
    
    id = db.Column(db.Integer, primary_key=True)
    # Payroll generation schedule
    payroll_generation_day = db.Column(db.Integer, default=31)  # Day of month (1-31)
    payroll_generation_time = db.Column(db.String(5), default='18:00')  # HH:MM format
    auto_generate_payroll = db.Column(db.Boolean, default=False)  # Enable/disable auto generation
    auto_send_payslip_email = db.Column(db.Boolean, default=False)  # Enable/disable auto email
    
    # Professional tax settings (per month in INR)
    professional_tax_jan = db.Column(db.Numeric(10, 2, asdecimal=False), default=200.0)
    professional_tax_feb = db.Column(db.Numeric(10, 2, asdecimal=False), default=300.0)
    professional_tax_mar = db.Column(db.Numeric(10, 2, asdecimal=False), default=200.0)
    professional_tax_apr = db.Column(db.Numeric(10, 2, asdecimal=False), default=200.0)
    professional_tax_may = db.Column(db.Numeric(10, 2, asdecimal=False), default=200.0)
    professional_tax_jun = db.Column(db.Numeric(10, 2, asdecimal=False), default=200.0)
    professional_tax_jul = db.Column(db.Numeric(10, 2, asdecimal=False), default=200.0)
    professional_tax_aug = db.Column(db.Numeric(10, 2, asdecimal=False), default=200.0)
    professional_tax_sep = db.Column(db.Numeric(10, 2, asdecimal=False), default=200.0)
    professional_tax_oct = db.Column(db.Numeric(10, 2, asdecimal=False), default=200.0)
    professional_tax_nov = db.Column(db.Numeric(10, 2, asdecimal=False), default=200.0)
    professional_tax_dec = db.Column(db.Numeric(10, 2, asdecimal=False), default=200.0)
    
    # PDF storage path
    payslip_storage_path = db.Column(db.String(255), default='payrolls')
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    @classmethod
    def get_settings(cls):
        settings = cls.query.first()
        if not settings:
            settings = cls()
            db.session.add(settings)
            db.session.commit()
        return settings
    
    def get_professional_tax(self, month):
        """Get professional tax amount for a specific month (1-12)"""
        tax_map = {
            1: self.professional_tax_jan,
            2: self.professional_tax_feb,
            3: self.professional_tax_mar,
            4: self.professional_tax_apr,
            5: self.professional_tax_may,
            6: self.professional_tax_jun,
            7: self.professional_tax_jul,
            8: self.professional_tax_aug,
            9: self.professional_tax_sep,
            10: self.professional_tax_oct,
            11: self.professional_tax_nov,
            12: self.professional_tax_dec
        }
        return tax_map.get(month, 200.0)

class LogoutApprovalRequest(db.Model):
    """Auto Logout Approval Requests for Manager approval workflow"""
    __tablename__ = 'logout_approval_requests'
    
    id = db.Column(db.Integer, primary_key=True)
    attendance_id = db.Column(db.Integer, db.ForeignKey('attendance.id'), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    manager_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    request_type = db.Column(db.String(50), default='auto_logout')  # auto_logout, time_edit
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected
    remarks = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approved_at = db.Column(db.DateTime)
    approved_by = db.Column(db.Integer, db.ForeignKey('employees.id'))
    
    # Email notification sent flags (for duplicate email prevention)
    employee_notification_sent = db.Column(db.Boolean, default=False)
    manager_notification_sent = db.Column(db.Boolean, default=False)
    
    # Relationships
    attendance = db.relationship('Attendance', backref='approval_requests')
    employee = db.relationship('Employee', foreign_keys=[employee_id], backref='logout_requests')
    manager = db.relationship('Employee', foreign_keys=[manager_id], backref='assigned_approvals')
    approver = db.relationship('Employee', foreign_keys=[approved_by])
    
    # Ensure exactly one request per attendance_id for auto_logout requests
    __table_args__ = (
        db.UniqueConstraint('attendance_id', 'request_type', name='unique_request_per_attendance'),
    )

class AttendanceSettingsHistory(db.Model):
    """Historical attendance settings with effective timestamps"""
    __tablename__ = 'attendance_settings_history'
    
    id = db.Column(db.Integer, primary_key=True)
    effective_from = db.Column(db.DateTime, nullable=False, unique=True)  # Timestamp from which these settings apply
    office_start_time = db.Column(db.String(10), nullable=False)  # Format: HH:MM
    office_end_time = db.Column(db.String(10), nullable=False)  # Format: HH:MM
    working_hours_per_day = db.Column(db.Float, nullable=False)
    half_day_hours = db.Column(db.Float, nullable=True)  # Optional, defaults to half of working_hours_per_day
    grace_period_minutes = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('admins.id'), nullable=True)
    
    # Relationship
    creator = db.relationship('Admin', foreign_keys=[created_by])
    
    @classmethod
    def get_settings_for_datetime(cls, target_datetime):
        """Get the settings that were effective at a specific timestamp"""
        settings = cls.query.filter(
            cls.effective_from <= target_datetime
        ).order_by(cls.effective_from.desc()).first()
        
        if settings:
            return settings
        
        # If no history exists, return None (caller should fall back to Settings)
        return None
    
    @classmethod
    def get_settings_for_date(cls, target_date):
        """Get the settings that were effective on a specific date (backward compatibility)
        
        This method is kept for backward compatibility with existing callers.
        It converts the date to datetime at midnight and calls get_settings_for_datetime.
        """
        # Convert date to datetime at midnight
        target_datetime = datetime.combine(target_date, datetime.min.time())
        return cls.get_settings_for_datetime(target_datetime)


class CompanySettings(db.Model):
    """Company details for professional payslip generation"""
    __tablename__ = 'company_settings'
    
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(100), nullable=False)
    company_address = db.Column(db.Text, nullable=False)
    company_phone = db.Column(db.String(20), nullable=False)
    company_email = db.Column(db.String(120), nullable=False)
    company_website = db.Column(db.String(255))
    company_logo = db.Column(db.String(255))  # Path to logo file
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    @classmethod
    def get_settings(cls):
        settings = cls.query.first()
        if not settings:
            settings = cls(
                company_name='AI Attendance System',
                company_address='123 Business Street, City, Country',
                company_phone='+1234567890',
                company_email='hr@company.com',
                company_website='www.company.com'
            )
            db.session.add(settings)
            db.session.commit()
        return settings
