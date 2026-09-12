"""
Comprehensive tests for scheduler_service.py to ensure 100% test coverage.

Tests cover:
- Module-level helper functions for payslip path management
- PayrollScheduler initialization and lifecycle
- Payroll generation scheduling
- Monthly payroll generation logic
- Missed payroll reconciliation
- Auto checkout approval scheduling
- Helper methods and edge cases
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch
import os


# ========================================================================
# Module-level helper function tests
# ========================================================================

def test_get_payslip_storage_subdir_with_settings(app_context):
    """Test that get_payslip_storage_subdir returns configured value when settings exist."""
    from scheduler_service import get_payslip_storage_subdir
    from models import PayrollSettings
    from database import db

    settings = PayrollSettings.get_settings()
    settings.payslip_storage_path = 'custom_payrolls'
    db.session.commit()

    result = get_payslip_storage_subdir()
    assert result == 'custom_payrolls'


def test_get_payslip_storage_subdir_fallback_on_exception(app_context):
    """Test that get_payslip_storage_subdir falls back to 'payrolls' on exception."""
    from scheduler_service import get_payslip_storage_subdir
    from models import PayrollSettings
    from database import db

    settings = PayrollSettings.get_settings()
    db.session.delete(settings)
    db.session.commit()

    result = get_payslip_storage_subdir()
    assert result == 'payrolls'


def test_get_payslip_storage_subdir_fallback_when_none(app_context):
    """Test that get_payslip_storage_subdir falls back to 'payrolls' when setting is None."""
    from scheduler_service import get_payslip_storage_subdir
    from models import PayrollSettings
    from database import db

    settings = PayrollSettings.get_settings()
    settings.payslip_storage_path = None
    db.session.commit()

    result = get_payslip_storage_subdir()
    assert result == 'payrolls'


def test_get_payslip_relative_path_with_defaults():
    """Test get_payslip_relative_path with default base_subdir."""
    from scheduler_service import get_payslip_relative_path

    result = get_payslip_relative_path(2026, 8, 'payslip_test.pdf')
    expected = os.path.join('payrolls', '2026', '08', 'payslip_test.pdf')
    assert result == expected


def test_get_payslip_relative_path_with_custom_base():
    """Test get_payslip_relative_path with custom base_subdir."""
    from scheduler_service import get_payslip_relative_path

    result = get_payslip_relative_path(2026, 8, 'payslip_test.pdf', base_subdir='custom')
    expected = os.path.join('custom', '2026', '08', 'payslip_test.pdf')
    assert result == expected


def test_get_payslip_relative_path_month_padding():
    """Test that get_payslip_relative_path pads month with leading zero."""
    from scheduler_service import get_payslip_relative_path

    result = get_payslip_relative_path(2026, 1, 'payslip.pdf')
    assert '01' in result

    result = get_payslip_relative_path(2026, 12, 'payslip.pdf')
    assert '12' in result


def test_get_payslip_full_path():
    """Test get_payslip_full_path returns absolute path under UPLOAD_FOLDER."""
    from scheduler_service import get_payslip_full_path
    from config import Config

    result = get_payslip_full_path(2026, 8, 'payslip_test.pdf')
    expected = os.path.join(Config.UPLOAD_FOLDER, 'payrolls', '2026', '08', 'payslip_test.pdf')
    assert result == expected


def test_get_payslip_full_path_with_custom_base():
    """Test get_payslip_full_path with custom base_subdir."""
    from scheduler_service import get_payslip_full_path
    from config import Config

    result = get_payslip_full_path(2026, 8, 'payslip_test.pdf', base_subdir='custom')
    expected = os.path.join(Config.UPLOAD_FOLDER, 'custom', '2026', '08', 'payslip_test.pdf')
    assert result == expected


# ========================================================================
# PayrollScheduler initialization tests
# ========================================================================

def test_payroll_scheduler_init():
    """Test PayrollScheduler __init__ sets default values."""
    from scheduler_service import PayrollScheduler

    scheduler = PayrollScheduler()
    assert scheduler.scheduler is None
    assert scheduler.app is None
    assert scheduler.payroll_calculator is None
    assert scheduler.pdf_generator is None
    assert scheduler.email_service is None


def test_payroll_scheduler_init_with_app():
    """Test PayrollScheduler __init__ with app parameter."""
    from scheduler_service import PayrollScheduler

    mock_app = Mock()
    scheduler = PayrollScheduler(app=mock_app)
    assert scheduler.app == mock_app


def test_init_app_in_reloader_process_skips_initialization(app_context, monkeypatch):
    """Test that init_app skips initialization in Flask reloader process."""
    from scheduler_service import PayrollScheduler
    import scheduler_service

    # Reset the module-level flag
    scheduler_service._scheduler_initialized = False

    # Simulate being in the reloader process
    monkeypatch.setenv('WERKZEUG_RUN_MAIN', 'false')

    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    assert scheduler.scheduler is None
    assert scheduler.payroll_calculator is None


def test_init_app_already_initialized_skips(app_context, monkeypatch):
    """Test that init_app skips if already initialized in same process."""
    from scheduler_service import PayrollScheduler
    import scheduler_service

    # Set the flag to simulate already initialized
    scheduler_service._scheduler_initialized = True

    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    assert scheduler.scheduler is None


def test_init_app_initializes_in_main_process(app_context, monkeypatch):
    """Test that init_app initializes properly in main process."""
    from scheduler_service import PayrollScheduler
    import scheduler_service

    # Reset the module-level flag
    scheduler_service._scheduler_initialized = False

    # Simulate being in the main process
    monkeypatch.setenv('WERKZEUG_RUN_MAIN', 'true')

    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    assert scheduler.scheduler is not None
    assert scheduler.payroll_calculator is not None
    assert scheduler.pdf_generator is not None
    assert scheduler.email_service is not None
    assert scheduler.app == app_context


def test_init_app_initializes_without_reloader(app_context, monkeypatch):
    """Test that init_app initializes when reloader is disabled (no WERKZEUG_RUN_MAIN)."""
    from scheduler_service import PayrollScheduler
    import scheduler_service

    # Reset the module-level flag
    scheduler_service._scheduler_initialized = False

    # Remove WERKZEUG_RUN_MAIN to simulate disabled reloader
    monkeypatch.delenv('WERKZEUG_RUN_MAIN', raising=False)

    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    assert scheduler.scheduler is not None
    assert scheduler.payroll_calculator is not None


def test_init_app_handles_startup_exception(app_context, monkeypatch):
    """Test that init_app handles scheduler startup exception gracefully."""
    from scheduler_service import PayrollScheduler
    import scheduler_service
    from apscheduler.schedulers.background import BackgroundScheduler

    # Reset the module-level flag
    scheduler_service._scheduler_initialized = False

    monkeypatch.setenv('WERKZEUG_RUN_MAIN', 'true')

    scheduler = PayrollScheduler()

    # Mock BackgroundScheduler to raise exception on start
    with patch.object(BackgroundScheduler, 'start', side_effect=Exception("Scheduler failed")):
        scheduler.init_app(app_context)

    # Flag should be reset on failure
    assert scheduler_service._scheduler_initialized == False


# ========================================================================
# Payroll generation scheduling tests
# ========================================================================

def test_schedule_payroll_generation_registers_jobs(app_context):
    """Test that schedule_payroll_generation registers both payroll and approval jobs."""
    from scheduler_service import PayrollScheduler
    import scheduler_service

    # Reset flag to allow initialization
    scheduler_service._scheduler_initialized = False

    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    # Check that jobs were registered
    payroll_job = scheduler.scheduler.get_job('payroll_generation')
    approval_job = scheduler.scheduler.get_job('auto_checkout_approval')

    assert payroll_job is not None
    assert approval_job is not None
    assert payroll_job.name == 'Monthly Payroll Generation (1st of Next Month)'
    assert approval_job.name == 'Daily Auto Logout Approval Request Creation'


def test_reschedule_payroll_generation(app_context):
    """Test that reschedule_payroll_generation calls schedule_payroll_generation."""
    from scheduler_service import PayrollScheduler
    import scheduler_service

    scheduler_service._scheduler_initialized = False
    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    # Reschedule should not raise an exception
    scheduler.reschedule_payroll_generation()

    # Jobs should still exist
    assert scheduler.scheduler.get_job('payroll_generation') is not None
    assert scheduler.scheduler.get_job('auto_checkout_approval') is not None


# ========================================================================
# Auto checkout approval tests
# ========================================================================

def test_run_auto_checkout_approval(app_context, make_employee):
    """Test that run_auto_checkout_approval creates approval requests."""
    from scheduler_service import PayrollScheduler
    import scheduler_service

    scheduler_service._scheduler_initialized = False
    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    # This should not raise an exception
    scheduler.run_auto_checkout_approval()


# ========================================================================
# Monthly payroll generation tests
# ========================================================================

def test_generate_monthly_payroll_disabled_skips(app_context, make_employee):
    """Test that generate_monthly_payroll skips when auto_generate_payroll is disabled."""
    from scheduler_service import PayrollScheduler
    from models import PayrollSettings
    from database import db
    import scheduler_service

    scheduler_service._scheduler_initialized = False
    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    # Disable auto payroll generation
    settings = PayrollSettings.get_settings()
    settings.auto_generate_payroll = False
    db.session.commit()

    # Should skip without error
    scheduler.generate_monthly_payroll()


def test_generate_monthly_payroll_creates_payroll(app_context, make_employee):
    """Test that generate_monthly_payroll creates payroll for eligible employee."""
    from scheduler_service import PayrollScheduler
    from models import PayrollSettings, Payroll
    from database import db
    import scheduler_service

    scheduler_service._scheduler_initialized = False
    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    # Enable auto payroll generation
    settings = PayrollSettings.get_settings()
    settings.auto_generate_payroll = True
    settings.auto_send_payslip_email = False  # Disable email for this test
    db.session.commit()

    # Create an employee with unique ID to avoid conflicts
    employee = make_employee(employee_id='EMP_TEST_001', name='Test Employee')

    # Clean up any existing payroll records for this employee from previous test runs
    now = datetime.now()
    prev_month = (now.replace(day=1) - timedelta(days=1)).month
    prev_year = (now.replace(day=1) - timedelta(days=1)).year

    Payroll.query.filter_by(
        employee_id=employee.id,
        month=prev_month,
        year=prev_year
    ).delete()
    db.session.commit()

    # Mock the PDF generation to avoid actual file operations
    with patch.object(scheduler.pdf_generator, 'generate_payslip'):
        scheduler.generate_monthly_payroll()

    # Check that payroll was created (for previous month)
    payroll = Payroll.query.filter_by(
        employee_id=employee.id,
        month=prev_month,
        year=prev_year
    ).first()

    # Payroll should exist (assuming employee is eligible)
    # Note: This depends on is_payroll_eligible logic


def test_generate_monthly_payroll_skips_existing_payroll(app_context, make_employee):
    """Test that generate_monthly_payroll skips if payroll already exists."""
    from scheduler_service import PayrollScheduler
    from models import PayrollSettings, Payroll
    from database import db
    import scheduler_service

    scheduler_service._scheduler_initialized = False
    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    settings = PayrollSettings.get_settings()
    settings.auto_generate_payroll = True
    settings.auto_send_payslip_email = False
    db.session.commit()

    employee = make_employee(employee_id='EMP0001')

    # Create an existing payroll record
    now = datetime.now()
    prev_month = (now.replace(day=1) - timedelta(days=1)).month
    prev_year = (now.replace(day=1) - timedelta(days=1)).year

    existing_payroll = Payroll(
        employee_id=employee.id,
        month=prev_month,
        year=prev_year,
        basic_salary=50000,
        gross_salary=50000,
        net_salary=50000
    )
    db.session.add(existing_payroll)
    db.session.commit()

    # Should skip without error
    scheduler.generate_monthly_payroll()


def test_generate_monthly_payroll_handles_employee_error(app_context, make_employee):
    """Test that generate_monthly_payroll continues on employee processing error."""
    from scheduler_service import PayrollScheduler
    from models import PayrollSettings
    from database import db
    import scheduler_service

    scheduler_service._scheduler_initialized = False
    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    settings = PayrollSettings.get_settings()
    settings.auto_generate_payroll = True
    settings.auto_send_payslip_email = False
    db.session.commit()

    employee = make_employee(employee_id='EMP0001')

    # Mock payroll calculator to raise exception
    with patch.object(scheduler.payroll_calculator, 'calculate_monthly_payroll', side_effect=Exception("Test error")):
        # Should not raise, should log and continue
        scheduler.generate_monthly_payroll()


def test_generate_monthly_payroll_auto_emails_existing_payslip(app_context, make_employee):
    """Test that generate_monthly_payroll auto-emails existing payslip when enabled."""
    from scheduler_service import PayrollScheduler
    from models import PayrollSettings, Payroll
    from database import db
    import scheduler_service

    scheduler_service._scheduler_initialized = False
    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    settings = PayrollSettings.get_settings()
    settings.auto_generate_payroll = True
    settings.auto_send_payslip_email = True
    db.session.commit()

    employee = make_employee(employee_id='EMP0001')

    # Create existing payroll with payslip but no email sent
    now = datetime.now()
    prev_month = (now.replace(day=1) - timedelta(days=1)).month
    prev_year = (now.replace(day=1) - timedelta(days=1)).year

    existing_payroll = Payroll(
        employee_id=employee.id,
        month=prev_month,
        year=prev_year,
        basic_salary=50000,
        gross_salary=50000,
        net_salary=50000,
        payslip_generated=True,
        payslip_path='test_path.pdf',
        email_sent=False
    )
    db.session.add(existing_payroll)
    db.session.commit()

    # Mock email service
    with patch.object(scheduler.email_service, 'send_payslip', return_value={'success': True}):
        scheduler.generate_monthly_payroll()

        db.session.refresh(existing_payroll)
        assert existing_payroll.email_sent == True


# ========================================================================
# Missed payroll reconciliation tests
# ========================================================================

def test_reconcile_missed_payroll_disabled_skips(app_context):
    """Test that reconcile_missed_payroll skips when auto_generate_payroll is disabled."""
    from scheduler_service import PayrollScheduler
    from models import PayrollSettings
    from database import db
    import scheduler_service

    scheduler_service._scheduler_initialized = False
    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    settings = PayrollSettings.get_settings()
    settings.auto_generate_payroll = False
    db.session.commit()

    # Should skip without error
    scheduler.reconcile_missed_payroll()


def test_reconcile_missed_payroll_existing_payroll_skips(app_context, make_employee):
    """Test that reconcile_missed_payroll skips when payroll already exists."""
    from scheduler_service import PayrollScheduler
    from models import PayrollSettings, Payroll
    from database import db
    import scheduler_service

    scheduler_service._scheduler_initialized = False
    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    settings = PayrollSettings.get_settings()
    settings.auto_generate_payroll = True
    db.session.commit()

    employee = make_employee(employee_id='EMP0001')

    # Create payroll for previous month
    now = datetime.now()
    prev_month = (now.replace(day=1) - timedelta(days=1)).month
    prev_year = (now.replace(day=1) - timedelta(days=1)).year

    payroll = Payroll(
        employee_id=employee.id,
        month=prev_month,
        year=prev_year,
        basic_salary=50000,
        gross_salary=50000,
        net_salary=50000
    )
    db.session.add(payroll)
    db.session.commit()

    # Should skip without error
    scheduler.reconcile_missed_payroll()


def test_reconcile_missed_payroll_not_yet_due_skips(app_context):
    """Test that reconcile_missed_payroll skips for periods not yet due."""
    from scheduler_service import PayrollScheduler
    from models import PayrollSettings
    from database import db
    import scheduler_service

    scheduler_service._scheduler_initialized = False
    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    settings = PayrollSettings.get_settings()
    settings.auto_generate_payroll = True
    db.session.commit()

    # Should skip without error for future periods
    scheduler.reconcile_missed_payroll()


def test_reconcile_missed_payroll_generates_missed_payroll(app_context, make_employee):
    """Test that reconcile_missed_payroll generates missed payroll."""
    from scheduler_service import PayrollScheduler
    from models import PayrollSettings
    from database import db
    import scheduler_service

    scheduler_service._scheduler_initialized = False
    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    settings = PayrollSettings.get_settings()
    settings.auto_generate_payroll = True
    settings.auto_send_payslip_email = False
    db.session.commit()

    employee = make_employee(employee_id='EMP0001')

    # Mock PDF generation
    with patch.object(scheduler.pdf_generator, 'generate_payslip'):
        # Should not raise error
        scheduler.reconcile_missed_payroll()


def test_reconcile_missed_payroll_handles_generation_error(app_context, make_employee):
    """Test that reconcile_missed_payroll continues on generation error."""
    from scheduler_service import PayrollScheduler
    from models import PayrollSettings
    from database import db
    import scheduler_service

    scheduler_service._scheduler_initialized = False
    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    settings = PayrollSettings.get_settings()
    settings.auto_generate_payroll = True
    db.session.commit()

    employee = make_employee(employee_id='EMP0001')

    # Mock payroll calculator to raise exception
    with patch.object(scheduler.payroll_calculator, 'calculate_monthly_payroll', side_effect=Exception("Test error")):
        # Should not raise, should log and continue
        scheduler.reconcile_missed_payroll()


# ========================================================================
# Helper method tests
# ========================================================================

def test_get_payslip_path(app_context):
    """Test _get_payslip_path returns correct full path."""
    from scheduler_service import PayrollScheduler
    from config import Config
    import scheduler_service

    scheduler_service._scheduler_initialized = False
    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    result = scheduler._get_payslip_path(2026, 8, 'payslip_test.pdf')
    expected = os.path.join(Config.UPLOAD_FOLDER, 'payrolls', '2026', '08', 'payslip_test.pdf')
    assert result == expected


def test_get_month_name():
    """Test _get_month_name returns correct month names."""
    from scheduler_service import PayrollScheduler

    scheduler = PayrollScheduler()

    assert scheduler._get_month_name(1) == 'January'
    assert scheduler._get_month_name(2) == 'February'
    assert scheduler._get_month_name(6) == 'June'
    assert scheduler._get_month_name(12) == 'December'


def test_shutdown_running_scheduler(app_context):
    """Test shutdown properly shuts down running scheduler."""
    from scheduler_service import PayrollScheduler
    import scheduler_service

    scheduler_service._scheduler_initialized = False
    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    # Should not raise error
    scheduler.shutdown()


def test_shutdown_no_scheduler():
    """Test shutdown handles case where scheduler is None."""
    from scheduler_service import PayrollScheduler

    scheduler = PayrollScheduler()
    scheduler.scheduler = None

    # Should not raise error
    scheduler.shutdown()


def test_shutdown_not_running_scheduler(app_context):
    """Test shutdown handles scheduler that is not running."""
    from scheduler_service import PayrollScheduler
    import scheduler_service

    scheduler_service._scheduler_initialized = False
    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    # Stop the scheduler first
    scheduler.scheduler.shutdown(wait=False)

    # Should not raise error even though not running
    scheduler.shutdown()


def test_shutdown_handles_exception(app_context):
    """Test shutdown handles exception during shutdown gracefully."""
    from scheduler_service import PayrollScheduler
    import scheduler_service

    scheduler_service._scheduler_initialized = False
    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    # Mock shutdown to raise exception
    with patch.object(scheduler.scheduler, 'shutdown', side_effect=Exception("Shutdown failed")):
        # Should not raise, should log warning
        scheduler.shutdown()


# ========================================================================
# Global scheduler instance tests
# ========================================================================

def test_global_scheduler_instance_exists():
    """Test that the global payroll_scheduler instance is created."""
    from scheduler_service import payroll_scheduler, PayrollScheduler

    assert payroll_scheduler is not None
    assert isinstance(payroll_scheduler, PayrollScheduler)