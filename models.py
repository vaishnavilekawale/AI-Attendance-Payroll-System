from datetime import datetime
from zoneinfo import ZoneInfo
from database import db
from werkzeug.security import generate_password_hash, check_password_hash

IST = ZoneInfo('Asia/Kolkata')


def now_ist():
    """
    Return the current time in India Standard Time, as a NAIVE datetime
    (no tzinfo attached).

    This replaces the old `now_ist()` (deprecated in Python 3.12+)
    everywhere in this codebase - both as the value used for timestamp
    fields and as the default/onupdate callable on DateTime columns below.

    Deliberately naive rather than fully timezone-aware: SQLite (this
    app's only supported database - see config.py) has no native
    timezone-aware datetime type. SQLAlchemy's sqlite DATETIME column
    strips tzinfo when it writes a value to disk, so a timezone-aware
    datetime written on INSERT comes back naive on the very next SELECT.
    If this function returned an aware datetime, every comparison between
    a freshly-created aware value and a value just reloaded from the
    database (e.g. Admin.is_temporary_password_valid(), or
    EmployeeLogin's equivalent) would raise:
        TypeError: can't compare offset-naive and offset-aware datetimes

    Returning an already-naive value sidesteps that entirely: every
    datetime in the app - freshly generated or reloaded from the database
    - stays naive and directly comparable, while still recording the
    correct IST wall-clock instant (computed via ZoneInfo, then stripped
    of tzinfo rather than left in UTC and converted at display time).

    NOTE: this changes what these columns actually store, from naive UTC
    to naive IST. See app.py's `matches_ist_date` / `matches_ist_date_range`
    helpers, which used to shift stored naive-UTC values by +5:30 for
    display/filtering - now that the stored value already IS IST, adding
    that offset again would double-shift it. Those helpers were updated
    to match.
    """
    return datetime.now(IST).replace(tzinfo=None)

class Admin(db.Model):
    __tablename__ = 'admins'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    temporary_password_hash = db.Column(db.String(255))
    temporary_password_created_at = db.Column(db.DateTime)
    email = db.Column(db.String(120), unique=True, nullable=False)
    force_password_change = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=now_ist)
    last_login = db.Column(db.DateTime)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def set_temporary_password(self, password):
        self.temporary_password_hash = generate_password_hash(password)
        self.temporary_password_created_at = now_ist()
    
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
        return now_ist() < self.temporary_password_created_at + timedelta(minutes=30)
    
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
    # Whether Professional Tax (from Payroll Settings) is deducted for this
    # employee. Defaults to True so existing employees are unaffected.
    pt_applicable = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=now_ist)
    updated_at = db.Column(db.DateTime, default=now_ist, onupdate=now_ist)
    
    # Authentication fields
    username = db.Column(db.String(80), unique=True, nullable=False)  # Same as employee_id
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='employee')  # admin, employee, manager
    must_change_password = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime)
    # Whether the employee has given consent to have their face captured and
    # stored for biometric attendance. This is a STRICT OPT-IN: it defaults
    # to False, so no face image can be captured or uploaded for a newly
    # created employee until consent has been explicitly recorded via
    # record_biometric_consent(). See BiometricConsentLog for the audit trail.
    # (The python-side default only applies to newly inserted rows; it does
    # not change the value stored for employees that already exist.)
    biometric_consent_given = db.Column(db.Boolean, default=False, nullable=False)
    # Snapshot of the most recent consent decision, kept on the Employee row
    # itself for fast reads (e.g. the face-capture gate) without a join.
    # The full history of every decision lives in BiometricConsentLog.
    biometric_consent_timestamp = db.Column(db.DateTime, nullable=True)
    biometric_consent_version = db.Column(db.String(20), nullable=True)
    biometric_consent_ip_address = db.Column(db.String(45), nullable=True)
    
    # Relationships
    attendance_records = db.relationship('Attendance', backref='employee', lazy=True, cascade='all, delete-orphan')
    payroll_records = db.relationship('Payroll', backref='employee', lazy=True, cascade='all, delete-orphan')
    consent_logs = db.relationship('BiometricConsentLog', backref='employee', lazy=True, cascade='all, delete-orphan')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def record_biometric_consent(self, granted, ip_address=None, policy_version=None, notes=None):
        """Record a biometric consent decision (grant or withdrawal).

        Updates the fast-read snapshot fields on this Employee row and
        appends an immutable BiometricConsentLog entry so the full consent
        history is always auditable, even after the current status changes
        again later. Returns the created log entry.
        """
        self.biometric_consent_given = granted
        self.biometric_consent_timestamp = now_ist()
        self.biometric_consent_ip_address = ip_address
        if policy_version is not None:
            self.biometric_consent_version = policy_version

        log = BiometricConsentLog(
            employee_id=self.id,
            granted=granted,
            policy_version=policy_version,
            ip_address=ip_address,
            notes=notes,
        )
        db.session.add(log)
        return log

class BiometricConsentLog(db.Model):
    """Audit trail of biometric (face data) consent decisions for an employee.

    A new row is written every time consent is granted, withdrawn, or
    re-confirmed, rather than overwriting a single flag - this gives a
    full history of who consented, when, under which policy version, and
    from where, which is what an audit or a data-protection request
    actually needs. `Employee.biometric_consent_given` remains the fast
    "current status" flag checked by the face-capture flow; this table is
    the record of how that flag arrived at its current value.
    """
    __tablename__ = 'biometric_consent_log'

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    granted = db.Column(db.Boolean, nullable=False)
    policy_version = db.Column(db.String(20), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)  # IPv6-safe length
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=now_ist)

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
    created_at = db.Column(db.DateTime, default=now_ist)
    updated_at = db.Column(db.DateTime, default=now_ist, onupdate=now_ist)
    
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
    created_at = db.Column(db.DateTime, default=now_ist)
    updated_at = db.Column(db.DateTime, default=now_ist, onupdate=now_ist)
    
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
    updated_at = db.Column(db.DateTime, default=now_ist, onupdate=now_ist)
    
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
    created_at = db.Column(db.DateTime, default=now_ist)
    updated_at = db.Column(db.DateTime, default=now_ist, onupdate=now_ist)

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
    created_at = db.Column(db.DateTime, default=now_ist)
    updated_at = db.Column(db.DateTime, default=now_ist, onupdate=now_ist)
    
    # Relationship
    employee = db.relationship('Employee', backref='login_credentials')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def set_temporary_password(self, password):
        self.temporary_password_hash = generate_password_hash(password)
        self.temporary_password_created_at = now_ist()
    
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
        return now_ist() < self.temporary_password_created_at + timedelta(minutes=30)
    
    def clear_temporary_password(self):
        self.temporary_password_hash = None
        self.temporary_password_created_at = None
    
    def generate_reset_token(self):
        import secrets
        self.password_reset_token = secrets.token_urlsafe(32)
        from datetime import timedelta
        self.password_reset_expiry = now_ist() + timedelta(hours=1)
        return self.password_reset_token
    
    def is_reset_token_valid(self):
        if not self.password_reset_token or not self.password_reset_expiry:
            return False
        return now_ist() < self.password_reset_expiry

class AttendanceActivity(db.Model):
    __tablename__ = 'attendance_activities'
    
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    attendance_date = db.Column(db.Date, nullable=False)
    activity_time = db.Column(db.Time, nullable=False)
    action = db.Column(db.String(10), nullable=False)  # IN or OUT
    created_at = db.Column(db.DateTime, default=now_ist)

    # Admin-edit bookkeeping (nothing is ever deleted by an admin edit):
    #   original_time   - the real punch time, set when an admin moved this punch
    #   hidden_by_admin - punch falls outside the admin-edited IN..OUT window;
    #                     kept in DB but not shown / not counted
    #   admin_added     - punch created by an admin edit (removed on next edit)
    original_time = db.Column(db.Time, nullable=True)
    hidden_by_admin = db.Column(db.Boolean, default=False)
    admin_added = db.Column(db.Boolean, default=False)
    
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
    
    created_at = db.Column(db.DateTime, default=now_ist)
    updated_at = db.Column(db.DateTime, default=now_ist, onupdate=now_ist)
    
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
    created_at = db.Column(db.DateTime, default=now_ist)
    approved_at = db.Column(db.DateTime)
    approved_by = db.Column(db.Integer, db.ForeignKey('employees.id'))

    # Set when an Admin manually edits the attendance after this request was
    # rejected. The request stays 'rejected' (so approval history is preserved),
    # but it no longer forces the attendance to ABSENT.
    admin_overridden = db.Column(db.Boolean, default=False)
    admin_overridden_at = db.Column(db.DateTime)

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
    created_at = db.Column(db.DateTime, default=now_ist)
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
    
    created_at = db.Column(db.DateTime, default=now_ist)
    updated_at = db.Column(db.DateTime, default=now_ist, onupdate=now_ist)
    
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
