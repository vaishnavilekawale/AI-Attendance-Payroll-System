"""
Scheduler Service for Automatic Payroll Generation
Uses APScheduler to handle scheduled payroll generation tasks
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
import logging
from models import PayrollSettings, CompanySettings, Payroll, Employee
from payroll import PayrollCalculator, is_payroll_eligible
from pdf_generator import PDFGenerator
from email_service import EmailService
from database import db
import os

logger = logging.getLogger(__name__)

# Module-level flag to prevent duplicate scheduler initialization
# This persists across Flask debug reloader restarts
_scheduler_initialized = False

class PayrollScheduler:
    def __init__(self, app=None):
        self.scheduler = None
        self.app = app
        self.payroll_calculator = None
        self.pdf_generator = None
        self.email_service = None

    def run_auto_checkout_approval(self):
        """
        Run auto logout approval request creation at 11:59 PM
        Instead of directly checking out employees, creates approval requests for managers
        """
        logger.info("run_auto_checkout_approval started")

        with self.app.app_context():
            logger.info("Application context created")
            from services.approval_service import approval_service
            result = approval_service.create_daily_approval_requests()
            logger.info(f"Approval request creation completed: {result}")

        logger.info("run_auto_checkout_approval finished")
    
    def init_app(self, app):
        """Initialize scheduler with Flask app
        
        IMPORTANT: This checks if we're in the Flask debug reloader process.
        The scheduler should only start ONCE in the main process, not in the reloader process.
        This prevents duplicate scheduler initialization and Thread-1 exceptions.
        
        Flask's debug reloader runs the script twice:
        1. Once in the main process (WERKZEUG_RUN_MAIN=true)
        2. Once in the reloader process (no WERKZEUG_RUN_MAIN)
        
        We only initialize in the main process to avoid duplicate schedulers.
        
        When use_reloader=False, WERKZEUG_RUN_MAIN is not set, so we should still initialize.
        """
        # Check if we're in the Flask debug reloader process
        # WERKZEUG_RUN_MAIN is set only in the main process, not in the reloader
        werkzeug_main = os.environ.get('WERKZEUG_RUN_MAIN')
        
        # Use module-level flag to prevent duplicate initialization within same process
        global _scheduler_initialized
        if _scheduler_initialized:
            logger.info("Scheduler already initialized in this process, skipping")
            return
        
        # Initialize if we're in the main process OR if reloader is disabled (no WERKZEUG_RUN_MAIN set)
        if werkzeug_main == 'true' or werkzeug_main is None:
            # Only initialize scheduler in the main process or when reloader is disabled
            self.app = app
            self.payroll_calculator = PayrollCalculator()
            self.pdf_generator = PDFGenerator()
            self.email_service = EmailService()
            
            # Create scheduler with daemon=False to prevent Thread-1 exceptions on reload
            # daemon=False allows the scheduler to be properly shut down
            # self.scheduler = BackgroundScheduler(daemon=False)
            self.scheduler = BackgroundScheduler()
            
            # Start scheduler
            try:
                self.scheduler.start()
                logger.info("Payroll scheduler started")
                
                # Mark as initialized
                _scheduler_initialized = True
                
                # Schedule payroll generation job
                self.schedule_payroll_generation()
            except Exception as e:
                logger.error(f"Failed to start scheduler: {e}")
                # Reset flag on failure so we can retry
                _scheduler_initialized = False
        else:
            logger.info("Skipping scheduler initialization in reloader process (WERKZEUG_RUN_MAIN=%s)", werkzeug_main)
    
    def schedule_payroll_generation(self):
        """Schedule payroll generation based on settings"""
        with self.app.app_context():
            settings = PayrollSettings.get_settings()
            
            # Schedule daily auto logout approval request creation at 23:59
            # This runs independently of payroll auto-generation setting
            self.scheduler.add_job(
                func=self.run_auto_checkout_approval,
                trigger=CronTrigger(hour=23, minute=59),
                id="auto_checkout_approval",
                name="Daily Auto Logout Approval Request Creation",
                replace_existing=True
            )
            
            auto_job = self.scheduler.get_job("auto_checkout_approval")
            if auto_job:
                logger.info("DAILY APPROVAL SCHEDULER REGISTERED - 23:59")
                logger.info(f"Auto Logout Approval Job: {auto_job}")
                logger.info(f"Auto Logout Approval Next Run: {auto_job.next_run_time}")
            
            # Parse time (HH:MM format)
            hour, minute = map(int, settings.payroll_generation_time.split(':'))
            
            # Create cron trigger - runs on the LAST DAY of every month
            # APScheduler supports day='last' for the last day of the month
            # This ensures payroll runs at the end of every month automatically
            trigger = CronTrigger(
                day='last',
                hour=hour,
                minute=minute
            )
            
            # Add job - ALWAYS scheduled regardless of auto_generate_payroll setting
            # The generate_monthly_payroll method checks the setting internally
            self.scheduler.add_job(
                func=self.generate_monthly_payroll,
                trigger=trigger,
                id='payroll_generation',
                name='Monthly Payroll Generation (End of Month)',
                replace_existing=True
            )

            payroll_job = self.scheduler.get_job("payroll_generation")
            if payroll_job:
                logger.info(f"Payroll Job: {payroll_job}")
                logger.info(f"Payroll Next Run: {payroll_job.next_run_time}")
            
            logger.info(f"Payroll generation scheduled for last day of month at {settings.payroll_generation_time}")
    
    def reschedule_payroll_generation(self):
        """Reschedule payroll generation when settings change"""
        self.schedule_payroll_generation()
    
    def generate_monthly_payroll(self):
        """Generate payroll for all active employees
        
        Runs automatically at the end of every month (last day of month).
        Auto-calculates payroll, generates PDF payslips, and sends payslip
        emails to all employees.
        """
        logger.info("Starting automatic payroll generation...")
        
        with self.app.app_context():
            try:
                # Get settings
                payroll_settings = PayrollSettings.get_settings()
                
                # Check if auto payroll generation is enabled
                if not payroll_settings.auto_generate_payroll:
                    logger.info("Auto payroll generation is disabled - skipping")
                    return
                
                # Get current date
                now = datetime.now()
                current_month = now.month
                current_year = now.year
                
                # If we're running on the last day of month, generate for current month
                # Otherwise, generate for previous month
                if now.day >= 28:
                    month = current_month
                    year = current_year
                else:
                    # Generate for previous month
                    from datetime import timedelta
                    prev_month = now.replace(day=1) - timedelta(days=1)
                    month = prev_month.month
                    year = prev_month.year
                
                logger.info(f"Generating payroll for {month}/{year}")
                
                # Get settings
                company_settings = CompanySettings.get_settings()
                
                # Get all active employees
                employees = Employee.query.filter_by(status='active').all()
                
                for employee in employees:
                    try:
                        # Check if employee is eligible for payroll for this month
                        if not is_payroll_eligible(employee, month, year):
                            logger.info(f"Skipping payroll for employee {employee.id} ({employee.name}) - not eligible for {month}/{year}")
                            continue
                        
                        # Check if payroll already exists for this employee and month
                        existing_payroll = Payroll.query.filter_by(
                            employee_id=employee.id,
                            month=month,
                            year=year
                        ).first()

                        if existing_payroll:
                            logger.info(f"Payroll already exists for {employee.name} for {month}/{year}")

                            # Auto email existing payslip if enabled
                            if (
                                    payroll_settings.auto_send_payslip_email
                                    and existing_payroll.payslip_generated
                                    and existing_payroll.payslip_path
                                    and not existing_payroll.email_sent
                                ):
                            # if (
                            #     payroll_settings.auto_send_payslip_email
                            #     and existing_payroll.payslip_generated
                            #     and not existing_payroll.email_sent
                            # ):
                                month_name = self._get_month_name(month)

                                logger.info(f"[AUTO EMAIL] Sending payslip to {employee.email}")

                                result = self.email_service.send_payslip(
                                    employee_email=employee.email,
                                    employee_name=employee.name,
                                    payslip_path=existing_payroll.payslip_path,
                                    month=month_name,
                                    year=year
                                )

                                if result["success"]:
                                    existing_payroll.email_sent = True
                                    db.session.commit()
                                    logger.info(f"[AUTO EMAIL] Email sent successfully to {employee.email}")
                                else:
                                    logger.error(f"[AUTO EMAIL] Failed to send email to {employee.email}")

                            continue
                        # existing_payroll = Payroll.query.filter_by(
                        #     employee_id=employee.id,
                        #     month=month,
                        #     year=year
                        # ).first()
                        
                        # if existing_payroll:
                        #     logger.info(f"Payroll already exists for {employee.name} for {month}/{year}, skipping")
                        #     continue
                        
                        # Calculate payroll
                        payrolls = self.payroll_calculator.calculate_monthly_payroll(
                            year=year,
                            month=month,
                            employee_id=employee.id
                        )
                        
                        if not payrolls:
                            logger.warning(f"No payroll generated for {employee.name}")
                            continue
                        
                        payroll = payrolls[0]
                        
                        # Generate payslip PDF
                        payslip_filename = f"payslip_{employee.employee_id}_{year}_{month}.pdf"
                        payslip_path = self._get_payslip_path(year, month, payslip_filename)
                        
                        # Ensure directory exists
                        os.makedirs(os.path.dirname(payslip_path), exist_ok=True)
                        
                        # Generate PDF with new signature
                        self.pdf_generator.generate_payslip(
                            payroll=payroll,
                            employee=employee,
                            company_settings=company_settings,
                            output_path=payslip_path
                        )
                        
                        # Update payroll record
                        payroll.payslip_generated = True
                        payroll.payslip_path = payslip_path
                        db.session.commit()
                        
                        logger.info(f"Payslip generated for {employee.name}: {payslip_path}")
                        
                        # Send email if enabled
                        if payroll_settings.auto_send_payslip_email:
                            month_name = self._get_month_name(month)
                            result = self.email_service.send_payslip(
                                employee_email=employee.email,
                                employee_name=employee.name,
                                payslip_path=payslip_path,
                                month=month_name,
                                year=year
                            )
                            
                            if result['success']:
                                payroll.email_sent = True
                                db.session.commit()
                                logger.info(f"Payslip email sent to {employee.email}")
                            else:
                                logger.error(f"Failed to send payslip email to {employee.email}: {result['message']}")
                        
                    except Exception as e:
                        logger.error(f"Error generating payroll for {employee.name}: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                        continue
                
                logger.info(f"Automatic payroll generation completed for {month}/{year}")
                
            except Exception as e:
                logger.error(f"Error in automatic payroll generation: {e}")
                import traceback
                logger.error(traceback.format_exc())
    
    def _get_payslip_path(self, year, month, filename):
        """Get full path for payslip PDF storage"""
        payroll_settings = PayrollSettings.get_settings()
        base_path = payroll_settings.payslip_storage_path or 'payrolls'
        return os.path.join(base_path, str(year), f"{month:02d}", filename)
    
    def _get_month_name(self, month):
        """Get month name from month number"""
        months = [
            'January', 'February', 'March', 'April', 'May', 'June',
            'July', 'August', 'September', 'October', 'November', 'December'
        ]
        return months[month - 1]

    def shutdown(self):
        """Shutdown scheduler safely"""
        try:
            if self.scheduler and self.scheduler.running:
                self.scheduler.shutdown(wait=False)
                logger.info("Payroll scheduler shutdown")
        except Exception as e:
            logger.warning(f"Scheduler shutdown skipped: {e}")
    
    def reconcile_missed_payroll(self):
        """
        Reconcile missed payroll periods on application startup.
        
        Detects payroll periods that should have been generated according to
        the payroll_generation_day schedule but were missed because the server
        was OFF at the scheduled time.
        
        This function:
        - Determines which payroll periods should already have been generated
        - Checks if payroll for those periods exists
        - Generates missed payroll using existing generate_monthly_payroll()
        - Is idempotent - safe to run multiple times
        - Has no arbitrary time limits (7/30 days)
        - Works for multiple missed months
        """
        logger.info("PAYROLL RECONCILIATION STARTED")
        
        with self.app.app_context():
            try:
                settings = PayrollSettings.get_settings()
                
                if not settings.auto_generate_payroll:
                    logger.info("Auto payroll generation is disabled - skipping reconciliation")
                    logger.info("PAYROLL RECONCILIATION COMPLETED")
                    return
                
                # Get current date
                now = datetime.now()
                current_month = now.month
                current_year = now.year
                
                # Determine the scheduled payroll generation day/time
                # Payroll runs on the LAST DAY of every month
                payroll_time_str = settings.payroll_generation_time
                hour, minute = map(int, payroll_time_str.split(':'))
                
                # Calculate the scheduled datetime for current month (last day of month)
                from calendar import monthrange
                last_day_of_month = monthrange(current_year, current_month)[1]
                scheduled_day = last_day_of_month
                
                scheduled_datetime = datetime(current_year, current_month, scheduled_day, hour, minute)
                
                # Check if the scheduled time for current month has already passed
                if now < scheduled_datetime:
                    # Scheduled time hasn't passed yet - no payroll should exist for current month
                    logger.info(f"Current month payroll scheduled for {scheduled_datetime} - not yet due")
                    # Check previous month
                    from datetime import timedelta
                    prev_month = now.replace(day=1) - timedelta(days=1)
                    months_to_check = [(prev_month.month, prev_month.year)]
                else:
                    # Scheduled time has passed - current month payroll should exist
                    # Also check previous month in case it was missed
                    from datetime import timedelta
                    prev_month = now.replace(day=1) - timedelta(days=1)
                    months_to_check = [
                        (current_month, current_year),
                        (prev_month.month, prev_month.year)
                    ]
                
                # Check each month that should have payroll
                for check_month, check_year in months_to_check:
                    period = f"{check_month}/{check_year}"
                    
                    # Calculate scheduled datetime for this month (last day of month)
                    last_day = monthrange(check_year, check_month)[1]
                    scheduled_day_check = last_day
                    scheduled_datetime_check = datetime(check_year, check_month, scheduled_day_check, hour, minute)
                    
                    logger.info(f"PAYROLL RECONCILIATION CHECK")
                    logger.info(f"Payroll Period: {period}")
                    logger.info(f"Scheduled Date/Time: {scheduled_datetime_check}")
                    
                    # Check if any payroll records exist for this period
                    existing_payroll_count = Payroll.query.filter_by(
                        month=check_month,
                        year=check_year
                    ).count()
                    
                    should_have_payroll = now >= scheduled_datetime_check
                    
                    if existing_payroll_count > 0:
                        logger.info(f"PAYROLL ALREADY GENERATED")
                        logger.info(f"Payroll Period: {period}")
                        logger.info(f"Action: SKIPPED ({existing_payroll_count} records exist)")
                        continue
                    
                    if not should_have_payroll:
                        logger.info(f"Payroll period {period} not yet due - skipping")
                        continue
                    
                    # Payroll should exist but doesn't - generate it
                    logger.info(f"MISSED PAYROLL DETECTED")
                    logger.info(f"Payroll Period: {period}")
                    logger.info(f"Scheduled Date/Time: {scheduled_datetime_check}")
                    
                    try:
                        # Use existing payroll generation function
                        # This has built-in duplicate protection
                        payrolls = self.payroll_calculator.calculate_monthly_payroll(
                            year=check_year,
                            month=check_month
                        )
                        
                        ifpayrolls = payrolls if payrolls else []
                        
                        logger.info(f"MISSED PAYROLL GENERATED")
                        logger.info(f"Payroll Period: {period}")
                        logger.info(f"Records generated: {len(ifpayrolls)}")
                        
                        # Generate payslips and send emails if enabled
                        company_settings = CompanySettings.get_settings()
                        
                        for payroll in ifpayrolls:
                            employee = payroll.employee
                            if not employee:
                                continue
                            
                            # Generate payslip PDF
                            payslip_filename = f"payslip_{employee.employee_id}_{check_year}_{check_month}.pdf"
                            payslip_path = self._get_payslip_path(check_year, check_month, payslip_filename)
                            
                            import os
                            os.makedirs(os.path.dirname(payslip_path), exist_ok=True)
                            
                            self.pdf_generator.generate_payslip(
                                payroll=payroll,
                                employee=employee,
                                company_settings=company_settings,
                                output_path=payslip_path
                            )
                            
                            payroll.payslip_generated = True
                            payroll.payslip_path = payslip_path
                            
                            logger.info(f"Payslip generated for {employee.name}: {payslip_path}")
                            
                            # Send email if enabled
                            if settings.auto_send_payslip_email:
                                month_name = self._get_month_name(check_month)
                                result = self.email_service.send_payslip(
                                    employee_email=employee.email,
                                    employee_name=employee.name,
                                    payslip_path=payslip_path,
                                    month=month_name,
                                    year=check_year
                                )
                                
                                if result['success']:
                                    payroll.email_sent = True
                                    logger.info(f"Payslip email sent to {employee.email}")
                                else:
                                    logger.error(f"Failed to send payslip email to {employee.email}: {result['message']}")
                        
                        db.session.commit()
                        
                    except Exception as e:
                        logger.error(f"MISSED PAYROLL GENERATION FAILED")
                        logger.error(f"Payroll Period: {period}")
                        logger.error(f"Error: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                        # Continue to next month - don't stop entire reconciliation
                        continue
                
                logger.info("PAYROLL RECONCILIATION COMPLETED")
                
            except Exception as e:
                logger.error(f"Error in payroll reconciliation: {e}")
                import traceback
                logger.error(traceback.format_exc())
                logger.info("PAYROLL RECONCILIATION COMPLETED (with errors)")
    
    # def shutdown(self):
    #     """Shutdown scheduler"""
    #     if self.scheduler:
    #         self.scheduler.shutdown()
    #         logger.info("Payroll scheduler shutdown")

# Global scheduler instance
payroll_scheduler = PayrollScheduler()
