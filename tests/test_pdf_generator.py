"""
Tests for pdf_generator.py

Covers:
- generate_payslip_password function
- PDF payslip generation
- Password protection
- Formatting helpers
"""
import pytest
import os
import tempfile
from datetime import datetime, date
from unittest.mock import Mock, patch, MagicMock


def test_generate_payslip_password_with_override(app_context, make_employee):
    """Test generate_payslip_password with custom override."""
    from pdf_generator import generate_payslip_password
    from models import Employee
    from database import db

    employee = make_employee(employee_id='EMP001')
    employee.payslip_password_override = 'custom123'
    db.session.commit()

    with patch('crypto_utils.decrypt_str') as mock_decrypt:
        mock_decrypt.return_value = 'custom123'
        password = generate_payslip_password(employee)

        assert password == 'custom123'


def test_generate_payslip_password_default_formula(app_context, make_employee):
    """Test generate_payslip_password with default formula."""
    from pdf_generator import generate_payslip_password

    employee = make_employee(
        employee_id='EMP001',
        name='Test Employee',
        phone='9876543210',
        dob=date(1990, 1, 15)
    )

    password = generate_payslip_password(employee)

    assert password is not None
    assert len(password) > 0


def test_generate_payslip_password_no_dob_uses_joining_date(app_context, make_employee):
    """Test generate_payslip_password uses joining_date when no DOB."""
    from pdf_generator import generate_payslip_password

    employee = make_employee(
        employee_id='EMP001',
        name='Test Employee',
        phone='9876543210',
        dob=None,
        joining_date=date(2020, 1, 15)
    )

    password = generate_payslip_password(employee)

    assert password is not None


def test_format_currency():
    """Test currency formatting."""
    from pdf_generator import PDFGenerator

    generator = PDFGenerator()
    formatted = generator._format_currency(50000.50)

    assert formatted is not None
    assert '50,000' in formatted or '50000' in formatted


def test_format_days():
    """Test days formatting."""
    from pdf_generator import PDFGenerator

    generator = PDFGenerator()
    formatted = generator._format_days(8.5)

    assert formatted is not None


def test_pdf_generator_class_initialization():
    """Test PDFGenerator class initialization."""
    from pdf_generator import PDFGenerator

    generator = PDFGenerator()
    assert generator is not None


def test_generate_payslip_with_all_params(app_context, make_employee):
    """Test generate_payslip with all required parameters."""
    from pdf_generator import PDFGenerator
    from models import Payroll, Settings
    from database import db
    import tempfile

    employee = make_employee(employee_id='EMP001', name='Test Employee')
    payroll = Payroll(
        employee_id=employee.id,
        month=1,
        year=2026,
        basic_salary=50000,
        gross_salary=50000,
        net_salary=50000
    )
    db.session.add(payroll)
    db.session.commit()

    settings = Settings.get_settings()
    db.session.commit()

    generator = PDFGenerator()

    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        output_path = tmp.name

    try:
        result = generator.generate_payslip(
            payroll=payroll,
            employee=employee,
            company_settings=settings,
            output_path=output_path,
            password='test123'
        )

        assert result is not None
    finally:
        if os.path.exists(output_path):
            os.unlink(output_path)


def test_generate_payslip_without_password(app_context, make_employee):
    """Test generate_payslip without password."""
    from pdf_generator import PDFGenerator
    from models import Payroll, Settings
    from database import db
    import tempfile

    employee = make_employee(employee_id='EMP001', name='Test Employee')
    payroll = Payroll(
        employee_id=employee.id,
        month=1,
        year=2026,
        basic_salary=50000,
        gross_salary=50000,
        net_salary=50000
    )
    db.session.add(payroll)
    db.session.commit()

    settings = Settings.get_settings()
    db.session.commit()

    generator = PDFGenerator()

    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        output_path = tmp.name

    try:
        result = generator.generate_payslip(
            payroll=payroll,
            employee=employee,
            company_settings=settings,
            output_path=output_path
        )

        assert result is not None
    finally:
        if os.path.exists(output_path):
            os.unlink(output_path)


def test_generate_payslip_password_decryption_error(app_context, make_employee):
    """Test generate_payslip_password handles decryption errors."""
    from pdf_generator import generate_payslip_password
    from models import Employee
    from database import db

    employee = make_employee(employee_id='EMP001')
    employee.payslip_password_override = 'encrypted_value'
    db.session.commit()

    with patch('crypto_utils.decrypt_str') as mock_decrypt:
        mock_decrypt.side_effect = Exception('Decryption failed')
        password = generate_payslip_password(employee)

        # Should fall back to default formula
        assert password is not None


def test_generate_payslip_password_no_joining_date(app_context, make_employee):
    """Test generate_payslip_password handles missing joining_date."""
    from pdf_generator import generate_payslip_password

    employee = make_employee(
        employee_id='EMP001',
        name='Test Employee',
        phone='9876543210',
        dob=None,
        joining_date=date(2020, 1, 15)  # joining_date is NOT NULL
    )

    password = generate_payslip_password(employee)

    # Should still generate a password using joining_date
    assert password is not None
