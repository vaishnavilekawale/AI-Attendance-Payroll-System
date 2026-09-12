"""
Unit tests for SQLAlchemy models in models.py and database initialization logic in database.py.

Tests cover:
- Model creation and field validation
- Field constraints (unique, nullable, defaults)
- Relationships between Employee and Attendance/Payroll
- Database initialization and rollback behavior
"""

import pytest
from datetime import datetime, date, timedelta
from sqlalchemy.exc import IntegrityError
from models import (
    Admin, Employee, Attendance, Payroll, Settings,
    BiometricConsentLog, WorkingHours, EmployeeLogin,
    AttendanceActivity, PayrollSettings, LogoutApprovalRequest,
    AttendanceSettingsHistory, CompanySettings
)
from database import db, init_db


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def make_admin(app_context):
    """Factory fixture to create Admin records."""
    def _make_admin(username='test_admin', email='admin@test.com', password='password123'):
        admin = Admin(username=username, email=email)
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        return admin
    yield _make_admin
    # Cleanup
    Admin.query.delete()
    db.session.commit()


@pytest.fixture
def make_employee(app_context):
    """Factory fixture to create Employee records."""
    def _make_employee(
        employee_id='EMP001',
        name='Test Employee',
        department='Engineering',
        designation='Developer',
        basic_salary=50000.00,
        joining_date=date(2024, 1, 1),
        email='employee@test.com',
        phone='1234567890',
        address='123 Test Street'
    ):
        employee = Employee(
            employee_id=employee_id,
            name=name,
            department=department,
            designation=designation,
            basic_salary=basic_salary,
            joining_date=joining_date,
            email=email,
            phone=phone,
            address=address,
            username=employee_id
        )
        employee.set_password('default_password')
        db.session.add(employee)
        db.session.commit()
        return employee
    yield _make_employee
    # Cleanup
    Employee.query.delete()
    db.session.commit()


@pytest.fixture
def make_attendance(app_context, make_employee):
    """Factory fixture to create Attendance records."""
    def _make_attendance(employee_id, attendance_date, status='present', **kwargs):
        employee = Employee.query.filter_by(employee_id=employee_id).first()
        if not employee:
            employee = make_employee(employee_id=employee_id)
        
        attendance = Attendance(
            employee_id=employee.id,
            date=attendance_date,
            status=status,
            **kwargs
        )
        db.session.add(attendance)
        db.session.commit()
        return attendance
    yield _make_attendance
    # Cleanup
    Attendance.query.delete()
    db.session.commit()


@pytest.fixture
def make_payroll(app_context, make_employee):
    """Factory fixture to create Payroll records."""
    def _make_payroll(employee_id, month=1, year=2024, basic_salary=50000.00, **kwargs):
        employee = Employee.query.filter_by(employee_id=employee_id).first()
        if not employee:
            employee = make_employee(employee_id=employee_id, basic_salary=basic_salary)
        
        payroll = Payroll(
            employee_id=employee.id,
            month=month,
            year=year,
            basic_salary=basic_salary,
            gross_salary=basic_salary,
            net_salary=basic_salary,
            **kwargs
        )
        db.session.add(payroll)
        db.session.commit()
        return payroll
    yield _make_payroll
    # Cleanup
    Payroll.query.delete()
    db.session.commit()


# ============================================================================
# Admin Model Tests
# ============================================================================

def test_admin_creation(app_context, make_admin):
    """Test creating an Admin record."""
    admin = make_admin(username='admin1', email='admin1@test.com')
    
    assert admin.id is not None
    assert admin.username == 'admin1'
    assert admin.email == 'admin1@test.com'
    assert admin.password_hash is not None
    assert admin.force_password_change is False
    assert admin.created_at is not None


def test_admin_password_hashing(app_context):
    """Test Admin password hashing and checking."""
    admin = Admin(username='admin', email='admin@test.com')
    admin.set_password('test_password')
    
    assert admin.check_password('test_password') is True
    assert admin.check_password('wrong_password') is False


def test_admin_temporary_password(app_context):
    """Test Admin temporary password functionality."""
    admin = Admin(username='admin', email='admin@test.com')
    admin.set_temporary_password('temp_pass')
    
    assert admin.check_temporary_password('temp_pass') is True
    assert admin.is_temporary_password_valid() is True
    
    admin.clear_temporary_password()
    assert admin.check_temporary_password('temp_pass') is False
    assert admin.is_temporary_password_valid() is False


def test_admin_unique_username(app_context, make_admin):
    """Test that Admin username must be unique."""
    make_admin(username='admin1', email='admin1@test.com')
    
    try:
        with pytest.raises(IntegrityError):
            admin2 = Admin(username='admin1', email='admin2@test.com')
            admin2.set_password('password')
            db.session.add(admin2)
            db.session.commit()
    finally:
        db.session.rollback()


def test_admin_unique_email(app_context, make_admin):
    """Test that Admin email must be unique."""
    make_admin(username='admin1', email='admin@test.com')
    
    try:
        with pytest.raises(IntegrityError):
            admin2 = Admin(username='admin2', email='admin@test.com')
            admin2.set_password('password')
            db.session.add(admin2)
            db.session.commit()
    finally:
        db.session.rollback()


def test_admin_nullable_fields(app_context):
    """Test that required Admin fields cannot be null."""
    with pytest.raises(IntegrityError):
        admin = Admin(username=None, email=None)
        db.session.add(admin)
        db.session.commit()


# ============================================================================
# Employee Model Tests
# ============================================================================

def test_employee_creation(app_context, make_employee):
    """Test creating an Employee record."""
    employee = make_employee(
        employee_id='EMP001',
        name='John Doe',
        department='Engineering',
        designation='Developer'
    )
    
    assert employee.id is not None
    assert employee.employee_id == 'EMP001'
    assert employee.name == 'John Doe'
    assert employee.department == 'Engineering'
    assert employee.designation == 'Developer'
    assert employee.basic_salary == 50000.00
    assert employee.status == 'active'
    assert employee.face_images_count == 0
    assert employee.created_at is not None


def test_employee_password_hashing(app_context):
    """Test Employee password hashing and checking."""
    employee = Employee(
        employee_id='EMP001',
        name='Test',
        department='IT',
        designation='Dev',
        basic_salary=50000,
        joining_date=date.today(),
        email='test@test.com',
        phone='1234567890',
        address='Test',
        username='EMP001'
    )
    employee.set_password('test_password')
    db.session.add(employee)
    db.session.commit()
    
    assert employee.check_password('test_password') is True
    assert employee.check_password('wrong_password') is False


def test_employee_unique_employee_id(app_context, make_employee):
    """Test that Employee employee_id must be unique."""
    make_employee(employee_id='EMP001')
    
    try:
        with pytest.raises(IntegrityError):
            employee2 = Employee(
                employee_id='EMP001',
                name='Another',
                department='IT',
                designation='Dev',
                basic_salary=40000,
                joining_date=date.today(),
                email='another@test.com',
                phone='9876543210',
                address='Another',
                username='EMP001'
            )
            employee2.set_password('password')
            db.session.add(employee2)
            db.session.commit()
    finally:
        db.session.rollback()


def test_employee_unique_username(app_context, make_employee):
    """Test that Employee username must be unique."""
    employee1 = Employee(
        employee_id='EMP001',
        name='Test1',
        department='IT',
        designation='Dev',
        basic_salary=40000,
        joining_date=date.today(),
        email='test1@test.com',
        phone='1234567890',
        address='Test1',
        username='user1'
    )
    employee1.set_password('password')
    db.session.add(employee1)
    db.session.commit()
    
    try:
        with pytest.raises(IntegrityError):
            employee2 = Employee(
                employee_id='EMP002',
                name='Another',
                department='IT',
                designation='Dev',
                basic_salary=40000,
                joining_date=date.today(),
                email='another@test.com',
                phone='9876543210',
                address='Another',
                username='user1'
            )
            employee2.set_password('password')
            db.session.add(employee2)
            db.session.commit()
    finally:
        db.session.rollback()


def test_employee_nullable_fields(app_context):
    """Test that required Employee fields cannot be null."""
    with pytest.raises(IntegrityError):
        employee = Employee(
            employee_id=None,
            name=None,
            department=None,
            designation=None,
            basic_salary=None,
            joining_date=None,
            email=None,
            phone=None,
            address=None
        )
        db.session.add(employee)
        db.session.commit()


def test_employee_optional_fields_nullable(app_context, make_employee):
    """Test that optional Employee fields can be null."""
    employee = make_employee()
    employee.dob = None
    employee.profile_photo = None
    employee.payslip_password_override = None
    employee.office_location = None
    employee.bank_name = None
    employee.bank_account_number = None
    employee.pan_number = None
    employee.uan_number = None
    employee.pf_number = None
    db.session.commit()
    
    assert employee.dob is None
    assert employee.profile_photo is None
    assert employee.payslip_password_override is None


def test_employee_biometric_consent(app_context, make_employee):
    """Test Employee biometric consent recording."""
    employee = make_employee()
    
    # Record consent
    log = employee.record_biometric_consent(
        granted=True,
        ip_address='127.0.0.1',
        policy_version='v1.0',
        notes='Initial consent'
    )
    db.session.commit()
    
    assert employee.biometric_consent_given is True
    assert employee.biometric_consent_timestamp is not None
    assert employee.biometric_consent_version == 'v1.0'
    assert employee.biometric_consent_ip_address == '127.0.0.1'
    assert log.id is not None
    assert log.granted is True


def test_employee_biometric_consent_withdrawal(app_context, make_employee):
    """Test Employee biometric consent withdrawal."""
    employee = make_employee()
    employee.record_biometric_consent(granted=True, ip_address='127.0.0.1')
    db.session.commit()
    
    # Withdraw consent
    employee.record_biometric_consent(
        granted=False,
        ip_address='127.0.0.1',
        notes='User requested withdrawal'
    )
    db.session.commit()
    
    assert employee.biometric_consent_given is False
    
    # Check that both consent events are logged
    logs = BiometricConsentLog.query.filter_by(employee_id=employee.id).all()
    assert len(logs) == 2
    assert logs[0].granted is True
    assert logs[1].granted is False


# ============================================================================
# Attendance Model Tests
# ============================================================================

def test_attendance_creation(app_context, make_attendance):
    """Test creating an Attendance record."""
    attendance = make_attendance(
        employee_id='EMP001',
        attendance_date=date.today(),
        status='present'
    )
    
    assert attendance.id is not None
    assert attendance.date == date.today()
    assert attendance.status == 'present'
    assert attendance.total_hours == 0.0
    assert attendance.late_entry is False
    assert attendance.early_exit is False
    assert attendance.attendance_type == 'FACE_RECOGNITION'
    assert attendance.approval_status == 'approved'


def test_attendance_unique_employee_date(app_context, make_attendance):
    """Test that Attendance has unique constraint on employee_id + date."""
    make_attendance(employee_id='EMP001', attendance_date=date.today())
    
    try:
        with pytest.raises(IntegrityError):
            make_attendance(employee_id='EMP001', attendance_date=date.today())
    finally:
        db.session.rollback()


def test_attendance_status_values(app_context, make_attendance):
    """Test various Attendance status values."""
    for status in ['present', 'absent', 'half_day', 'late']:
        attendance = make_attendance(
            employee_id=f'EMP{status}',
            attendance_date=date.today() + timedelta(days=int(hash(status)) % 10),
            status=status
        )
        assert attendance.status == status


def test_attendance_nullable_employee_id(app_context):
    """Test that Attendance employee_id cannot be null."""
    with pytest.raises(IntegrityError):
        attendance = Attendance(
            employee_id=None,
            date=date.today(),
            status='present'
        )
        db.session.add(attendance)
        db.session.commit()


# ============================================================================
# Payroll Model Tests
# ============================================================================

def test_payroll_creation(app_context, make_payroll):
    """Test creating a Payroll record."""
    payroll = make_payroll(
        employee_id='EMP001',
        month=1,
        year=2024,
        basic_salary=50000.00
    )
    
    assert payroll.id is not None
    assert payroll.month == 1
    assert payroll.year == 2024
    assert payroll.basic_salary == 50000.00
    assert payroll.working_days == 0
    assert payroll.present_days == 0
    assert payroll.absent_days == 0
    assert payroll.payslip_generated is False
    assert payroll.email_sent is False


def test_payroll_unique_employee_month(app_context, make_payroll):
    """Test that Payroll has unique constraint on employee_id + month + year."""
    make_payroll(employee_id='EMP001', month=1, year=2024)
    
    try:
        with pytest.raises(IntegrityError):
            make_payroll(employee_id='EMP001', month=1, year=2024)
    finally:
        db.session.rollback()


def test_payroll_nullable_fields(app_context):
    """Test that required Payroll fields cannot be null."""
    with pytest.raises(IntegrityError):
        payroll = Payroll(
            employee_id=None,
            month=None,
            year=None,
            basic_salary=None,
            gross_salary=None,
            net_salary=None
        )
        db.session.add(payroll)
        db.session.commit()


# ============================================================================
# Settings Model Tests
# ============================================================================

def test_settings_creation(app_context):
    """Test creating a Settings record."""
    settings = Settings(
        company_name='Test Company',
        office_start_time='09:00',
        office_end_time='18:00'
    )
    db.session.add(settings)
    db.session.commit()
    
    assert settings.id is not None
    assert settings.company_name == 'Test Company'
    assert settings.office_start_time == '09:00'
    assert settings.office_end_time == '18:00'
    assert settings.grace_period_minutes == 15
    assert settings.working_hours_per_day == 9.0


def test_settings_get_settings(app_context):
    """Test Settings.get_settings() class method."""
    # First call creates default
    settings = Settings.get_settings()
    assert settings is not None
    
    # Second call returns existing
    settings2 = Settings.get_settings()
    assert settings.id == settings2.id


def test_settings_defaults(app_context):
    """Test Settings default values."""
    settings = Settings()
    db.session.add(settings)
    db.session.commit()
    
    assert settings.company_name == 'AI Attendance System'
    assert settings.office_start_time == '09:00'
    assert settings.office_end_time == '18:00'
    assert settings.grace_period_minutes == 15
    assert settings.working_hours_per_day == 9.0
    assert settings.half_day_hours == 4.5
    assert settings.late_deduction_enabled is False
    assert settings.half_day_deduction_enabled is True
    assert settings.absent_deduction_enabled is True
    assert settings.overtime_enabled is True
    assert settings.overtime_rate == 1.5
    assert settings.face_recognition_tolerance == 0.6
    assert settings.min_face_images_required == 20


# ============================================================================
# Employee-Attendance Relationship Tests
# ============================================================================

def test_employee_attendance_relationship(app_context, make_employee, make_attendance):
    """Test relationship between Employee and Attendance."""
    employee = make_employee(employee_id='EMP001')
    
    attendance1 = make_attendance(employee_id='EMP001', attendance_date=date.today())
    attendance2 = make_attendance(employee_id='EMP001', attendance_date=date.today() - timedelta(days=1))
    
    # Test backref from attendance to employee
    assert attendance1.employee.id == employee.id
    assert attendance2.employee.id == employee.id
    
    # Test relationship from employee to attendances
    assert len(employee.attendance_records) == 2
    assert attendance1 in employee.attendance_records
    assert attendance2 in employee.attendance_records


def test_employee_attendance_cascade_delete(app_context, make_employee, make_attendance):
    """Test that deleting Employee cascades to Attendance records."""
    employee = make_employee(employee_id='EMP001')
    make_attendance(employee_id='EMP001', attendance_date=date.today())
    
    assert len(Attendance.query.all()) == 1
    
    db.session.delete(employee)
    db.session.commit()
    
    assert len(Attendance.query.all()) == 0


# ============================================================================
# Employee-Payroll Relationship Tests
# ============================================================================

def test_employee_payroll_relationship(app_context, make_employee, make_payroll):
    """Test relationship between Employee and Payroll."""
    employee = make_employee(employee_id='EMP001', basic_salary=50000)
    
    payroll1 = make_payroll(employee_id='EMP001', month=1, year=2024)
    payroll2 = make_payroll(employee_id='EMP001', month=2, year=2024)
    
    # Test backref from payroll to employee
    assert payroll1.employee.id == employee.id
    assert payroll2.employee.id == employee.id
    
    # Test relationship from employee to payrolls
    assert len(employee.payroll_records) == 2
    assert payroll1 in employee.payroll_records
    assert payroll2 in employee.payroll_records


def test_employee_payroll_cascade_delete(app_context, make_employee, make_payroll):
    """Test that deleting Employee cascades to Payroll records."""
    employee = make_employee(employee_id='EMP001', basic_salary=50000)
    make_payroll(employee_id='EMP001', month=1, year=2024)
    
    assert len(Payroll.query.all()) == 1
    
    db.session.delete(employee)
    db.session.commit()
    
    assert len(Payroll.query.all()) == 0


# ============================================================================
# BiometricConsentLog Model Tests
# ============================================================================

def test_biometric_consent_log_creation(app_context, make_employee):
    """Test creating a BiometricConsentLog record."""
    employee = make_employee()
    
    log = BiometricConsentLog(
        employee_id=employee.id,
        granted=True,
        policy_version='v1.0',
        ip_address='127.0.0.1',
        notes='Initial consent'
    )
    db.session.add(log)
    db.session.commit()
    
    assert log.id is not None
    assert log.employee_id == employee.id
    assert log.granted is True
    assert log.policy_version == 'v1.0'
    assert log.ip_address == '127.0.0.1'
    assert log.notes == 'Initial consent'
    assert log.created_at is not None


def test_biometric_consent_log_relationship(app_context, make_employee):
    """Test relationship between Employee and BiometricConsentLog."""
    employee = make_employee()
    
    log1 = BiometricConsentLog(employee_id=employee.id, granted=True)
    log2 = BiometricConsentLog(employee_id=employee.id, granted=False)
    db.session.add_all([log1, log2])
    db.session.commit()
    
    assert len(employee.consent_logs) == 2
    assert log1 in employee.consent_logs
    assert log2 in employee.consent_logs


def test_biometric_consent_log_cascade_delete(app_context, make_employee):
    """Test that deleting Employee cascades to BiometricConsentLog records."""
    employee = make_employee()
    log = BiometricConsentLog(employee_id=employee.id, granted=True)
    db.session.add(log)
    db.session.commit()
    
    assert len(BiometricConsentLog.query.all()) == 1
    
    db.session.delete(employee)
    db.session.commit()
    
    assert len(BiometricConsentLog.query.all()) == 0


# ============================================================================
# EmployeeLogin Model Tests
# ============================================================================

def test_employee_login_creation(app_context, make_employee):
    """Test creating an EmployeeLogin record."""
    employee = make_employee()
    
    login = EmployeeLogin(
        employee_id=employee.id,
        username=employee.employee_id,
        force_password_change=True
    )
    login.set_password('password123')
    db.session.add(login)
    db.session.commit()
    
    assert login.id is not None
    assert login.employee_id == employee.id
    assert login.username == employee.employee_id
    assert login.first_login is True
    assert login.force_password_change is True
    assert login.is_active is True


def test_employee_login_password_reset_token(app_context, make_employee):
    """Test EmployeeLogin password reset token functionality."""
    employee = make_employee()
    
    login = EmployeeLogin(employee_id=employee.id, username=employee.employee_id)
    login.set_password('password')
    db.session.add(login)
    db.session.commit()
    
    # Generate reset token
    token = login.generate_reset_token()
    assert token is not None
    assert login.password_reset_token == token
    assert login.password_reset_expiry is not None
    assert login.is_reset_token_valid() is True


# ============================================================================
# PayrollSettings Model Tests
# ============================================================================

def test_payroll_settings_creation(app_context):
    """Test creating a PayrollSettings record."""
    settings = PayrollSettings(
        payroll_generation_day=31,
        payroll_generation_time='18:00',
        auto_generate_payroll=True
    )
    db.session.add(settings)
    db.session.commit()
    
    assert settings.id is not None
    assert settings.payroll_generation_day == 31
    assert settings.payroll_generation_time == '18:00'
    assert settings.auto_generate_payroll is True


def test_payroll_settings_get_settings(app_context):
    """Test PayrollSettings.get_settings() class method."""
    settings = PayrollSettings.get_settings()
    assert settings is not None
    
    settings2 = PayrollSettings.get_settings()
    assert settings.id == settings2.id


def test_payroll_settings_professional_tax(app_context):
    """Test PayrollSettings.get_professional_tax() method."""
    settings = PayrollSettings()
    settings.professional_tax_jan = 200.0
    settings.professional_tax_feb = 300.0
    db.session.add(settings)
    db.session.commit()
    
    assert settings.get_professional_tax(1) == 200.0
    assert settings.get_professional_tax(2) == 300.0
    assert settings.get_professional_tax(3) == 200.0  # Default


# ============================================================================
# CompanySettings Model Tests
# ============================================================================

def test_company_settings_creation(app_context):
    """Test creating a CompanySettings record."""
    settings = CompanySettings(
        company_name='Test Company',
        company_address='123 Test St',
        company_phone='1234567890',
        company_email='test@company.com'
    )
    db.session.add(settings)
    db.session.commit()
    
    assert settings.id is not None
    assert settings.company_name == 'Test Company'
    assert settings.company_address == '123 Test St'
    assert settings.company_phone == '1234567890'
    assert settings.company_email == 'test@company.com'


def test_company_settings_get_settings(app_context):
    """Test CompanySettings.get_settings() class method."""
    settings = CompanySettings.get_settings()
    assert settings is not None
    
    settings2 = CompanySettings.get_settings()
    assert settings.id == settings2.id


def test_company_settings_defaults(app_context):
    """Test CompanySettings default values when created."""
    # Clear any existing settings
    CompanySettings.query.delete()
    db.session.commit()
    
    settings = CompanySettings.get_settings()
    assert settings.company_name == 'AI Attendance System'
    assert settings.company_address == '123 Business Street, City, Country'
    assert settings.company_phone == '+1234567890'
    assert settings.company_email == 'hr@company.com'
    assert settings.company_website == 'www.company.com'


# ============================================================================
# LogoutApprovalRequest Model Tests
# ============================================================================

def test_logout_approval_request_creation(app_context, make_employee):
    """Test creating a LogoutApprovalRequest record."""
    employee = make_employee(employee_id='EMP001')
    manager = make_employee(employee_id='EMP002')
    
    attendance = Attendance(
        employee_id=employee.id,
        date=date.today(),
        status='present'
    )
    db.session.add(attendance)
    db.session.commit()
    
    request = LogoutApprovalRequest(
        attendance_id=attendance.id,
        employee_id=employee.id,
        manager_id=manager.id,
        request_type='auto_logout',
        status='pending'
    )
    db.session.add(request)
    db.session.commit()
    
    assert request.id is not None
    assert request.attendance_id == attendance.id
    assert request.employee_id == employee.id
    assert request.manager_id == manager.id
    assert request.status == 'pending'


def test_logout_approval_request_unique_constraint(app_context, make_employee):
    """Test unique constraint on attendance_id + request_type."""
    employee = make_employee(employee_id='EMP001')
    manager = make_employee(employee_id='EMP002')
    
    attendance = Attendance(
        employee_id=employee.id,
        date=date.today(),
        status='present'
    )
    db.session.add(attendance)
    db.session.commit()
    
    request1 = LogoutApprovalRequest(
        attendance_id=attendance.id,
        employee_id=employee.id,
        manager_id=manager.id,
        request_type='auto_logout'
    )
    db.session.add(request1)
    db.session.commit()
    
    try:
        with pytest.raises(IntegrityError):
            request2 = LogoutApprovalRequest(
                attendance_id=attendance.id,
                employee_id=employee.id,
                manager_id=manager.id,
                request_type='auto_logout'
            )
            db.session.add(request2)
            db.session.commit()
    finally:
        db.session.rollback()


# ============================================================================
# AttendanceSettingsHistory Model Tests
# ============================================================================

def test_attendance_settings_history_creation(app_context, make_admin):
    """Test creating an AttendanceSettingsHistory record."""
    admin = make_admin()
    
    history = AttendanceSettingsHistory(
        effective_from=datetime.utcnow(),
        office_start_time='09:00',
        office_end_time='18:00',
        working_hours_per_day=9.0,
        grace_period_minutes=15,
        created_by=admin.id
    )
    db.session.add(history)
    db.session.commit()
    
    assert history.id is not None
    assert history.office_start_time == '09:00'
    assert history.office_end_time == '18:00'
    assert history.working_hours_per_day == 9.0


def test_attendance_settings_history_get_for_datetime(app_context):
    """Test AttendanceSettingsHistory.get_settings_for_datetime() class method."""
    now = datetime.utcnow()
    
    history1 = AttendanceSettingsHistory(
        effective_from=now - timedelta(days=2),
        office_start_time='08:00',
        office_end_time='17:00',
        working_hours_per_day=8.0,
        grace_period_minutes=10
    )
    history2 = AttendanceSettingsHistory(
        effective_from=now - timedelta(days=1),
        office_start_time='09:00',
        office_end_time='18:00',
        working_hours_per_day=9.0,
        grace_period_minutes=15
    )
    db.session.add_all([history1, history2])
    db.session.commit()
    
    # Should return the most recent settings before the target time
    settings = AttendanceSettingsHistory.get_settings_for_datetime(now - timedelta(hours=12))
    assert settings.id == history2.id
    assert settings.office_start_time == '09:00'


# ============================================================================
# Database Initialization Tests
# ============================================================================

def test_db_init_creates_tables(app_context):
    """Test that init_db creates all tables."""
    from sqlalchemy import inspect
    
    inspector = inspect(db.engine)
    table_names = inspector.get_table_names()
    
    # Check that key tables exist
    assert 'admins' in table_names
    assert 'employees' in table_names
    assert 'attendance' in table_names
    assert 'payroll' in table_names
    assert 'settings' in table_names
    assert 'biometric_consent_log' in table_names
    assert 'employee_login' in table_names


def test_db_session_rollback(app_context, make_employee):
    """Test that db.session.rollback() undoes uncommitted changes."""
    initial_count = Employee.query.count()
    
    employee = Employee(
        employee_id='TEMP001',
        name='Temp',
        department='IT',
        designation='Dev',
        basic_salary=40000,
        joining_date=date.today(),
        email='temp@test.com',
        phone='1234567890',
        address='Temp',
        username='TEMP001'
    )
    employee.set_password('password')
    db.session.add(employee)
    
    # Don't commit, just rollback
    db.session.rollback()
    
    assert Employee.query.count() == initial_count


def test_db_session_commit_persists(app_context, make_employee):
    """Test that db.session.commit() persists changes."""
    initial_count = Employee.query.count()
    
    employee = Employee(
        employee_id='TEMP001',
        name='Temp',
        department='IT',
        designation='Dev',
        basic_salary=40000,
        joining_date=date.today(),
        email='temp@test.com',
        phone='1234567890',
        address='Temp',
        username='TEMP001'
    )
    employee.set_password('password')
    db.session.add(employee)
    db.session.commit()
    
    assert Employee.query.count() == initial_count + 1
    
    # Cleanup
    db.session.delete(employee)
    db.session.commit()


def test_db_transaction_isolation(app_context, make_employee):
    """Test that transactions are isolated between test cases."""
    # Each test should start with a clean state
    initial_count = Employee.query.count()
    
    # Create an employee
    employee = make_employee(employee_id='ISOLATED001')
    
    assert Employee.query.count() == initial_count + 1
    
    # Delete it
    db.session.delete(employee)
    db.session.commit()
    
    assert Employee.query.count() == initial_count


# ============================================================================
# WorkingHours Model Tests
# ============================================================================

def test_working_hours_creation(app_context, make_attendance):
    """Test creating a WorkingHours record."""
    attendance = make_attendance(employee_id='EMP001', attendance_date=date.today())
    
    working_hours = WorkingHours(
        attendance_id=attendance.id,
        in_time=datetime(2024, 1, 1, 9, 0),
        out_time=datetime(2024, 1, 1, 18, 0),
        total_hours=9.0
    )
    db.session.add(working_hours)
    db.session.commit()
    
    assert working_hours.id is not None
    assert working_hours.attendance_id == attendance.id
    assert working_hours.total_hours == 9.0
    assert working_hours.is_late is False
    assert working_hours.is_early_exit is False


# ============================================================================
# AttendanceActivity Model Tests
# ============================================================================

def test_attendance_activity_creation(app_context, make_employee):
    """Test creating an AttendanceActivity record."""
    employee = make_employee()
    
    activity = AttendanceActivity(
        employee_id=employee.id,
        attendance_date=date.today(),
        activity_time=datetime.now().time(),
        action='IN'
    )
    db.session.add(activity)
    db.session.commit()
    
    assert activity.id is not None
    assert activity.employee_id == employee.id
    assert activity.attendance_date == date.today()
    assert activity.action == 'IN'


def test_attendance_activity_relationship(app_context, make_employee):
    """Test relationship between Employee and AttendanceActivity."""
    employee = make_employee()
    
    activity1 = AttendanceActivity(
        employee_id=employee.id,
        attendance_date=date.today(),
        activity_time=datetime.now().time(),
        action='IN'
    )
    activity2 = AttendanceActivity(
        employee_id=employee.id,
        attendance_date=date.today(),
        activity_time=(datetime.now() + timedelta(hours=9)).time(),
        action='OUT'
    )
    db.session.add_all([activity1, activity2])
    db.session.commit()
    
    assert len(employee.attendance_activities) == 2
    assert activity1 in employee.attendance_activities
    assert activity2 in employee.attendance_activities
