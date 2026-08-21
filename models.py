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
    basic_salary = db.Column(db.Float, nullable=False)
    joining_date = db.Column(db.Date, nullable=False)
    dob = db.Column(db.Date, nullable=True)  # Date of birth - used to build the payslip PDF password
    email = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    address = db.Column(db.Text, nullable=False)
    profile_photo = db.Column(db.String(255))
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
    hra = db.Column(db.Float, default=0.0)
    da = db.Column(db.Float, default=0.0)
    medical_allowance = db.Column(db.Float, default=0.0)
    travel_allowance = db.Column(db.Float, default=0.0)
    special_allowance = db.Column(db.Float, default=0.0)
    other_allowances = db.Column(db.Float, default=0.0)
    # Deduction percentages and fixed monthly deductions
    employee_pf_percentage = db.Column(db.Float, default=12.0)
    employer_pf_percentage = db.Column(db.Float, default=12.0)
    esic_percentage = db.Column(db.Float, default=0.75)
    tds_percentage = db.Column(db.Float, default=0.0)
    bus_charges = db.Column(db.Float, default=0.0)
    other_deduction = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Authentication fields
    username = db.Column(db.String(80), unique=True, nullable=False)  # Same as employee_id
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='employee')  # admin, employee, manager
    must_change_password = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime)
    
    # Relationships
    attendance_records = db.relationship('Attendance', backref='employee', lazy=True, cascade='all, delete-orphan')
    payroll_records = db.relationship('Payroll', backref='employee', lazy=True, cascade='all, delete-orphan')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

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
    basic_salary = db.Column(db.Float, nullable=False)
    working_days = db.Column(db.Integer, default=0)
    present_days = db.Column(db.Integer, default=0)
    absent_days = db.Column(db.Integer, default=0)
    half_days = db.Column(db.Integer, default=0)
    late_days = db.Column(db.Integer, default=0)
    paid_days = db.Column(db.Float, default=0.0)
    lop_days = db.Column(db.Float, default=0.0)
    total_hours_worked = db.Column(db.Float, default=0.0)
    overtime_hours = db.Column(db.Float, default=0.0)
    per_day_salary = db.Column(db.Float, default=0.0)
    absent_deduction = db.Column(db.Float, default=0.0)
    lop_deduction = db.Column(db.Float, default=0.0)
    half_day_deduction = db.Column(db.Float, default=0.0)
    late_deduction = db.Column(db.Float, default=0.0)
    overtime_bonus = db.Column(db.Float, default=0.0)
    # Individual allowances (persisted from employee salary structure)
    hra = db.Column(db.Float, default=0.0)
    da = db.Column(db.Float, default=0.0)
    medical_allowance = db.Column(db.Float, default=0.0)
    travel_allowance = db.Column(db.Float, default=0.0)
    special_allowance = db.Column(db.Float, default=0.0)
    other_allowances = db.Column(db.Float, default=0.0)
    # Professional tax and other deductions for professional payslip
    professional_tax = db.Column(db.Float, default=0.0)
    employee_pf = db.Column(db.Float, default=0.0)
    esic = db.Column(db.Float, default=0.0)
    tds = db.Column(db.Float, default=0.0)
    bus_charges = db.Column(db.Float, default=0.0)
    other_deduction = db.Column(db.Float, default=0.0)
    total_deductions = db.Column(db.Float, default=0.0)
    employer_pf = db.Column(db.Float, default=0.0)
    gross_salary = db.Column(db.Float, nullable=False)
    net_salary = db.Column(db.Float, nullable=False)
    net_ctc = db.Column(db.Float, default=0.0)
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
    late_deduction_per_occurrence = db.Column(db.Float, default=0.0)
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
    professional_tax_jan = db.Column(db.Float, default=200.0)
    professional_tax_feb = db.Column(db.Float, default=300.0)
    professional_tax_mar = db.Column(db.Float, default=200.0)
    professional_tax_apr = db.Column(db.Float, default=200.0)
    professional_tax_may = db.Column(db.Float, default=200.0)
    professional_tax_jun = db.Column(db.Float, default=200.0)
    professional_tax_jul = db.Column(db.Float, default=200.0)
    professional_tax_aug = db.Column(db.Float, default=200.0)
    professional_tax_sep = db.Column(db.Float, default=200.0)
    professional_tax_oct = db.Column(db.Float, default=200.0)
    professional_tax_nov = db.Column(db.Float, default=200.0)
    professional_tax_dec = db.Column(db.Float, default=200.0)
    
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
