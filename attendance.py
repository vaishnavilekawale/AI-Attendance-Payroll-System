import logging
import threading
from datetime import datetime, time, timedelta

from flask import current_app
from database import db
from models import Attendance, Employee, Settings, AttendanceActivity
from services.attendance_calculator import AttendanceCalculator
from services.attendance_stats import has_rejected_approval, is_hidden_manual_attendance

logger = logging.getLogger(__name__)


def auto_checkout_pending_attendance():
    """
    Automatically checkout employees who forgot to punch OUT.
    
    This function is called by the scheduler at 11:59 PM daily.
    With unlimited IN/OUT support, it finds all attendance records for the current day where:
    - in_time IS NOT NULL
    - The last activity for the day is IN (employee is currently checked in)
    - date = today
    
    For each matching record:
    - Sets out_time = 23:59:59 of the same date
    - Calculates working hours from first IN to auto checkout time
    - Recalculates attendance status
    - Saves to database
    """
    try:
        today = datetime.now().date()
        
        # Find all attendance records for today with IN time
        all_attendance = Attendance.query.filter(
            Attendance.in_time.isnot(None),
            Attendance.out_time.is_(None)
        ).all()
        
        if not all_attendance:
            return
        
        am = AttendanceManager()
        processed_count = 0
        
        for attendance in all_attendance:
            try:
                # Future dates skip करा
                if attendance.date > today:
                    continue
                employee = Employee.query.get(attendance.employee_id)
                if not employee:
                    logger.warning(f"Employee not found for attendance ID {attendance.id}")
                    continue
                
                # Check if the last activity for this employee today is IN
                # This means they are currently checked in and need auto checkout
                last_activity = AttendanceActivity.query.filter_by(
                    employee_id=attendance.employee_id,
                    attendance_date=attendance.date
                ).order_by(
                    AttendanceActivity.activity_time.desc()
                ).first()
                
                needs_auto_checkout = False
                if not last_activity:
                    # No activities recorded but attendance has IN - needs auto checkout
                    needs_auto_checkout = True
                elif last_activity.action == 'IN':
                    # Last activity was IN - employee is currently checked in
                    needs_auto_checkout = True
                
                if not needs_auto_checkout:
                    continue
                
                # Apply auto checkout with commit=True for scheduled job
                am.apply_auto_checkout(attendance, commit_to_db=True)
                
                processed_count += 1
                
            except Exception as e:
                logger.error(f"Error processing attendance ID {attendance.id}: {e}")
                continue
        
        logger.info(f"AUTO CHECKOUT COMPLETED - Processed {processed_count} records")
        
    except Exception as e:
        logger.error(f"Error in auto_checkout_pending_attendance: {e}")


class AttendanceManager:
    def __init__(self):
        # Settings are loaded fresh from database on each operation
        self.calculator = AttendanceCalculator()

    def apply_auto_checkout(self, attendance, commit_to_db=False):
        return self.calculator.process_auto_checkout(attendance, commit_to_db)

    def recalculate_all_attendance(self):
        """
        Recalculate all attendance records using current system settings.
        Call this whenever admin changes system settings.
        """
        all_attendance = Attendance.query.all()

        for attendance in all_attendance:
            if not attendance.in_time:
                continue
            
            # Use centralized calculator for all calculations
            self.calculator.recalculate_attendance(attendance)

        db.session.commit()
    
    def mark_attendance(self, employee_id, confidence=None):
        """Mark attendance for employee (IN or OUT)"""
        today = datetime.now().date()
        employee = Employee.query.get(employee_id)
        
        if not employee:
            return {'success': False, 'message': 'Employee not found'}
        
        # Check if attendance already exists for today
        attendance = Attendance.query.filter_by(
            employee_id=employee_id,
            date=today
        ).first()
        
        if attendance:
            # Check if OUT can be marked (first cycle)
            if attendance.in_time and not attendance.out_time:
                return self.mark_out(attendance, confidence)
            elif attendance.in_time and attendance.out_time:
                # First cycle complete - allow unlimited IN/OUT
                return self.mark_additional_activity(attendance, confidence)
            else:
                return {'success': False, 'message': 'Invalid attendance state'}
        else:
            # Mark IN (first cycle)
            return self.mark_in(employee, confidence)
    
    def _schedule_attendance_calculation(self, attendance_id, is_final_calculation=True):
        """
        Schedule working-hours / status calculations in a background thread
        so the attendance-marking response stays fast.

        The attendance record (IN/OUT time + activity) is already committed
        by the caller.  The background task re-queries the record with a
        fresh DB session and persists the derived fields (total_hours,
        status, overtime, late_entry, early_exit) so that the Dashboard and
        Admin Reports remain 100 % accurate without any data loss.
        """
        try:
            app = current_app._get_current_object()
        except RuntimeError:
            # No application context available (e.g. running outside a request)
            # - fall back to a synchronous calculation to avoid data loss.
            logger.info("No app context for async calculation; using sync fallback")
            self._calculate_attendance_fields(attendance_id, is_final_calculation)
            return

        def _run_in_background():
            with app.app_context():
                self._calculate_attendance_fields(attendance_id, is_final_calculation)

        thread = threading.Thread(target=_run_in_background, daemon=True)
        thread.start()
        logger.info(
            f"Scheduled background attendance calculation for "
            f"attendance ID {attendance_id}"
        )

    def _calculate_attendance_fields(self, attendance_id, is_final_calculation=True):
        """
        (Re)calculate and persist working-hours / status fields for an
        attendance record.

        Designed to run inside a background thread that has its own
        application context and database session.  Re-queries the record by
        ID to avoid detached-instance / cross-session issues.
        """
        try:
            attendance = Attendance.query.get(attendance_id)
            if attendance is None:
                logger.error(
                    f"Background calculation: attendance record "
                    f"{attendance_id} not found"
                )
                return

            if attendance.out_time:
                # Final calculation (OUT marked): compute everything
                attendance.total_hours = (
                    self.calculator.calculate_working_hours(attendance)
                )
                attendance.status = self.calculator.calculate_status(
                    attendance,
                    attendance.total_hours,
                    is_final_calculation=True,
                )
                attendance.overtime_hours = self.calculator.calculate_overtime(
                    attendance, attendance.total_hours
                )
                attendance.late_entry = self.calculator.calculate_late_status(attendance)

                # Early-exit check
                settings = Settings.get_settings()
                office_end = self._parse_time(settings.office_end_time)
                attendance.early_exit = attendance.out_time.time() < office_end
            else:
                # Intermediate calculation (IN only, not final)
                attendance.total_hours = (
                    self.calculator.calculate_working_hours(attendance)
                )
                attendance.status = self.calculator.calculate_status(
                    attendance,
                    attendance.total_hours,
                    is_final_calculation=False,
                )
                attendance.late_entry = self.calculator.calculate_late_status(attendance)

            db.session.commit()
            logger.info(
                f"Background attendance calculation completed for ID "
                f"{attendance_id} - status: {attendance.status}, "
                f"hours: {attendance.total_hours}"
            )
        except Exception as e:
            logger.error(
                f"Background attendance calculation error for ID "
                f"{attendance_id}: {e}"
            )
            db.session.rollback()

    def mark_in(self, employee, confidence=None):
        """Mark IN attendance
        
        Sunday is a weekly holiday/off day - attendance marking is blocked.
        
        NEW LOGIC:
        - Always set status='present' regardless of late arrival
        - Late employees are still counted as Present, but late_entry flag is set
        - If employee has NO IN before Office End Time: prevent first IN after Office End Time
        - If employee has at least one IN before Office End Time: allow unlimited IN/OUT even after office hours
        """
        today = datetime.now().date()
        
        # Sunday is a weekly holiday/off day - block attendance marking
        if today.weekday() == 6:  # 6 = Sunday
            return {
                'success': False,
                'message': 'Today is Sunday (Weekly Off). Attendance cannot be marked.'
            }
        
        now = datetime.now()
        
        # Check if current time is after Office End Time
        settings = Settings.get_settings()
        office_end = self._parse_time(settings.office_end_time)
        current_time = now.time()
        
        if current_time > office_end:
            # Check if employee has any IN activity before Office End Time today
            has_valid_in_before_office_end = AttendanceActivity.query.filter(
                AttendanceActivity.employee_id == employee.id,
                AttendanceActivity.attendance_date == today,
                AttendanceActivity.action == 'IN',
                AttendanceActivity.activity_time <= office_end
            ).first()
            
            if not has_valid_in_before_office_end:
                # Employee never performed IN before Office End Time - attendance is closed
                return {
                    'success': False,
                    'message': 'Attendance closed for today.'
                }
            # else: Employee has valid IN before office end - allow IN even after office hours
        
        # Calculate late threshold
        office_start = self._parse_time(settings.office_start_time)
        office_start_datetime = datetime.combine(now.date(), office_start)
        grace_period = timedelta(minutes=settings.grace_period_minutes)
        late_threshold = office_start_datetime + grace_period
        
        is_late = now > late_threshold
        
        # Always set status='present', even if late
        # Late employees are still present, just with late_entry=True flag
        attendance = Attendance(
            employee_id=employee.id,
            date=today,
            in_time=now,
            status='present',  # Always present on IN (final status calculated on OUT)
            late_entry=is_late,  # Flag indicates late arrival
            confidence=confidence
        )
        
        db.session.add(attendance)
        db.session.commit()
        
        # CRITICAL: Add debug logs for status changes
        # logger.info(f"Attendance Status Updated - Employee ID: {employee.id}, Date: {today}, Action: IN, Time: {now}, Total Hours: 0, Status: {attendance.status}")
        
        # CRITICAL: Log the status immediately after commit to verify it's saved correctly
        # logger.info(f"ATTENDANCE SAVED TO DB - ID: {attendance.id}, Employee: {employee.name}, IN: {now}, Status: {attendance.status}, Late: {is_late}")
        
        # Verify the saved status
        db.session.refresh(attendance)
        # logger.info(f"ATTENDANCE VERIFIED FROM DB - ID: {attendance.id}, Status in DB: {attendance.status}")
        
        # Log IN activity
        activity = AttendanceActivity(
            employee_id=employee.id,
            attendance_date=today,
            activity_time=now.time(),
            action='IN'
        )
        db.session.add(activity)
        db.session.commit()
        
        # logger.info(f"IN marked for {employee.name} at {now} (Late: {is_late})")
        
        return {
            'success': True,
            'message': f'IN marked successfully for {employee.name}',
            'attendance_id': attendance.id,
            'in_time': now.strftime('%H:%M:%S'),
            'is_late': is_late,
            'status': attendance.status  # Return the status for verification
        }
    
    def mark_out(self, attendance, confidence=None):
        """Mark OUT attendance."""
        now = datetime.now()

        # Persist OUT time immediately so the record is
        # definitively marked as checked-out.
        attendance.out_time = now
        attendance.confidence = confidence
        db.session.commit()

        # Log OUT activity (committed before background calc
        # so the activity is visible for working-hours computation).
        activity = AttendanceActivity(
            employee_id=attendance.employee_id,
            attendance_date=attendance.date,
            activity_time=now.time(),
            action='OUT'
        )
        db.session.add(activity)
        db.session.commit()

        # Defer working-hours calculations to a background thread.
        self._schedule_attendance_calculation(
            attendance.id, is_final_calculation=True
        )

        return {
            'success': True,
            'message': 'OUT marked successfully',
            'out_time': now.strftime('%H:%M:%S'),
            'total_hours': 0.0,
            'overtime_hours': 0.0,
            'is_early_exit': False
        }
    def mark_additional_activity(self, attendance, confidence=None):
        """Mark additional IN/OUT activity after first attendance cycle is complete
        
        NEW LOGIC:
        - Allows unlimited IN/OUT events while preserving the original attendance record structure
        - in_time remains as the first IN of the day
        - out_time is updated to the latest OUT
        - status is recalculated based on total hours worked from all IN-OUT pairs
        - working hours are calculated from all IN-OUT pairs using calculator
        - Status is only finalized on OUT (not during IN)
        """
        now = datetime.now()
        today = attendance.date
        
        # Get the last activity for this employee today
        last_activity = AttendanceActivity.query.filter_by(
            employee_id=attendance.employee_id,
            attendance_date=today
        ).order_by(AttendanceActivity.activity_time.desc()).first()
        
        # Determine the next action (alternate from last activity)
        if last_activity and last_activity.action == 'OUT':
            next_action = 'IN'
        else:
            next_action = 'OUT'
        
        # Log the activity
        activity = AttendanceActivity(
            employee_id=attendance.employee_id,
            attendance_date=today,
            activity_time=now.time(),
            action=next_action
        )
        db.session.add(activity)

        # Persist the activity first; working-hours / status calculations
        # are deferred to a background thread so the response stays fast.
        if next_action == 'OUT':
            # Update out_time immediately and persist
            attendance.out_time = now
            attendance.confidence = confidence
        else:
            # IN action - CRITICAL: Set out_time to NULL because employee is
            # working again. This ensures display shows "-" for OUT time
            # when employee is currently IN.
            attendance.out_time = None
            attendance.confidence = confidence

        db.session.commit()

        # Defer working-hours / status calculations to a background thread.
        # The activity (and out_time for OUT actions) is already committed;
        # the background task re-queries with a fresh session and persists
        # the derived fields for Dashboard & Admin Reports.
        is_final = (next_action == 'OUT')
        self._schedule_attendance_calculation(
            attendance.id, is_final_calculation=is_final
        )

        return {
            'success': True,
            'message': f'{next_action} marked successfully',
            'action': next_action,
            'time': now.strftime('%H:%M:%S'),
            'total_hours': None,
            'status': attendance.status
        }
    def get_today_attendance(self, employee_id=None):
        """Get today's attendance with display OUT time based on last activity
        
        This method adds a computed property 'display_out_time' to attendance objects:
        - If last activity is IN: display_out_time = None (shows "-" in UI)
        - If last activity is OUT: display_out_time = attendance.out_time
        - If no activities: display_out_time = attendance.out_time
        
        This ensures that after a new IN, the OUT time appears empty until next OUT.
        """
        today = datetime.now().date()
        
        if employee_id:
            attendance = Attendance.query.filter_by(
                employee_id=employee_id,
                date=today
            ).first()
            if attendance:
                self._add_display_out_time(attendance, today)
            return attendance
        else:
            attendances = Attendance.query.filter_by(date=today).all()
            for att in attendances:
                self._add_display_out_time(att, today)
            return attendances
    
    def _add_display_out_time(self, attendance, today):
        """Add display_out_time property based on attendance.out_time
        
        This is a display-only helper that doesn't modify the database.
        It adds a computed property to determine what OUT time to show in UI.
        
        IMPORTANT: attendance.out_time is the authoritative source of truth.
        After manager approval, attendance.out_time = 23:59:00 must be displayed.
        
        Args:
            attendance: Attendance object
            today: Current date (used to determine if this is today's attendance)
        """
        # If attendance.out_time exists, use it directly (authoritative source)
        # This includes approved auto logout (23:59:00)
        if attendance.out_time:
            attendance.display_out_time = attendance.out_time
            return
        
        # If no attendance.out_time, check activities for display purposes
        # This handles the case where employee is currently checked IN
        last_activity = AttendanceActivity.query.filter_by(
            employee_id=attendance.employee_id,
            attendance_date=attendance.date
        ).order_by(AttendanceActivity.activity_time.desc()).first()
        
        # If last activity is IN, don't show OUT time (show "-" in UI)
        if last_activity and last_activity.action == 'IN':
            attendance.display_out_time = None
        else:
            # No OUT time and no IN activity - show None
            attendance.display_out_time = None
    
    def get_attendance_by_date_range(self, start_date, end_date, employee_id=None):
        """Get attendance by date range"""
        query = Attendance.query.filter(
            Attendance.date >= start_date,
            Attendance.date <= end_date
        )
        
        if employee_id:
            query = query.filter_by(employee_id=employee_id)
        
        return query.all()
    
    def get_monthly_attendance(self, year, month, employee_id=None):
        """Get monthly attendance"""
        start_date = datetime(year, month, 1).date()
        if month == 12:
            end_date = datetime(year + 1, 1, 1).date() - timedelta(days=1)
        else:
            end_date = datetime(year, month + 1, 1).date() - timedelta(days=1)
        
        return self.get_attendance_by_date_range(start_date, end_date, employee_id)
    
    def get_attendance_stats(self, start_date, end_date):
        """Get attendance statistics for date range
        
        FIX: Present includes status='present' OR status='late' (to handle old records)
        """
        attendances = self.get_attendance_by_date_range(start_date, end_date)
        
        total_employees = Employee.query.filter_by(status='active').count()
        # FIX: Present includes status='present' OR status='late' OR status='half_day'
        # Half Day employees attended office, so they count as Present
        present = len([a for a in attendances if a.status == 'present' or a.status == 'late' or a.status == 'half_day'])
        absent = len([a for a in attendances if a.status == 'absent'])
        half_day = len([a for a in attendances if a.status == 'half_day'])
        late = len([a for a in attendances if a.late_entry])
        
        return {
            'total_employees': total_employees,
            'present': present,
            'absent': absent,
            'half_day': half_day,
            'late': late
        }
    
    def get_department_stats(self, start_date, end_date):
        """Get department-wise attendance statistics
        
        Uses centralized attendance calculation for consistent absent logic.
        """
        from datetime import timedelta
        
        dept_stats = {}
        
        # Initialize with all active employees by department
        all_active_employees = Employee.query.filter_by(status='active').all()
        for employee in all_active_employees:
            dept = employee.department
            if dept not in dept_stats:
                dept_stats[dept] = {
                    'total': 0,
                    'present': 0,
                    'absent': 0,
                    'half_day': 0,
                    'late': 0
                }
            dept_stats[dept]['total'] += 1
        
        # Use centralized attendance calculation for each date in range
        current_date = start_date
        while current_date <= end_date:
            daily_attendance = self.calculate_attendance_with_absent(current_date)
            
            for attendance in daily_attendance:
                dept = attendance.employee.department if attendance.employee else "Unknown"
                if dept not in dept_stats:
                    dept_stats[dept] = {
                        'total': 0,
                        'present': 0,
                        'absent': 0,
                        'half_day': 0,
                        'late': 0
                    }
                
                # Count based on status
                if attendance.status == 'present' or attendance.status == 'late':
                    dept_stats[dept]['present'] += 1
                elif attendance.status == 'half_day':
                    # Half Day counts as both Present and Half Day
                    dept_stats[dept]['present'] += 1
                    dept_stats[dept]['half_day'] += 1
                elif attendance.status == 'absent':
                    dept_stats[dept]['absent'] += 1
                if attendance.late_entry:
                    dept_stats[dept]['late'] += 1
            
            current_date += timedelta(days=1)
        
        return dept_stats
    
    def _parse_time(self, time_str):
        """Parse time string to time object"""
        hours, minutes = map(int, time_str.split(':'))
        return time(hours, minutes)
    
    def can_mark_in(self, employee_id):
        """Check if employee can mark IN"""
        today = datetime.now().date()
        attendance = Attendance.query.filter_by(
            employee_id=employee_id,
            date=today
        ).first()
        
        return attendance is None
    
    def can_mark_out(self, employee_id):
        """Check if employee can mark OUT"""
        today = datetime.now().date()
        attendance = Attendance.query.filter_by(
            employee_id=employee_id,
            date=today
        ).first()
        
        return attendance and attendance.in_time and not attendance.out_time
    
    def apply_auto_checkout(self, attendance, commit_to_db=False):
        """
        Apply automatic checkout at 11:59 PM for attendance records with IN but no OUT.
        
        This is used when viewing historical attendance or after the day is complete.
        Auto checkout is applied to ensure accurate working hours and overtime calculations.
        
        Also recalculates attendance status based on working hours to ensure consistency.
        
        CRITICAL: When committing to database (scheduled job), also log the OUT activity
        to AttendanceActivity table to maintain complete activity history.
        
        Args:
            attendance: Attendance object with IN time but no OUT time
            commit_to_db: If True, commit changes to database (only for scheduled job)
            
        Returns:
            Attendance object with auto checkout applied (if applicable)
        """
        # Use centralized calculator for auto checkout
        return self.calculator.process_auto_checkout(attendance, commit_to_db)

    def calculate_attendance_with_absent(self, target_date):
        """
        Centralized attendance calculation function.
        
        Returns attendance data for ALL active employees for the given date.
        Applies business rules for absent calculation:
        
        - If attendance exists: 
          - Return attendance record as-is (do NOT apply auto checkout for today)
          - For past dates, apply auto checkout for display only
        - If no attendance:
          - Future date (> today): Status = "-", IN = "-", OUT = "-", Hours = "-"
          - Today + before office end time: Status = "Pending", IN = "-", OUT = "-"
          - Today + after office end time: Status = "ABSENT", IN = "-", OUT = "-", Hours = 0
          - Past date (< today): Status = "ABSENT", IN = "-", OUT = "-", Hours = 0
        
        Auto Checkout Rule:
        - NEVER apply auto checkout for today (any time)
        - For past dates: If IN exists but OUT is NULL, set OUT = 23:59:00 for display only
        - Auto checkout should ONLY be applied by scheduler at 11:59 PM
        
        Args:
            target_date: datetime.date object for the date to calculate
            
        Returns:
            List of attendance-like objects for all active employees
        """
        from datetime import datetime as dt
        
        today = dt.now().date()
        current_time = dt.now().time()
        
        # Sunday is a weekly holiday/off day - completely excluded from attendance
        if target_date.weekday() == 6:  # 6 = Sunday
            return []
        
        # Get office end time from settings
        settings = Settings.get_settings()
        office_end_time_str = settings.office_end_time if settings else '18:00'
        office_end_time = self._parse_time(office_end_time_str)
        
        # Auto checkout threshold: 11:59 PM
        auto_checkout_time = time(23, 59, 0)
        
        # Determine if auto checkout should be applied
        # Apply auto checkout ONLY for: past dates (< today)
        # For today (any time), do NOT apply auto checkout at all
        # The scheduler at 11:59 PM will handle today's auto checkout
        should_apply_auto_checkout = (target_date < today)
        
        # Load all active employees
        all_active_employees = Employee.query.filter_by(status='active').all()
        
        # Get attendance records for target date
        # Filter out pending AND rejected manual attendance - pending stays
        # hidden until a decision is made, rejected never reflects anywhere
        # (Hidden-Until-Approved Rule / No Dashboard Reflection Rule).
        attendances = Attendance.query.filter_by(date=target_date).all()
        attendances = [att for att in attendances if not is_hidden_manual_attendance(
            att.attendance_type, att.approval_status
        )]
        attendance_by_employee = {att.employee_id: att for att in attendances}
        
        result = []
        
        for employee in all_active_employees:
            attendance = attendance_by_employee.get(employee.id)
            
            # Check if target date is before employee's joining date
            # If so, skip this employee entirely for this date
            if employee.joining_date and target_date < employee.joining_date:
                # Pre-joining date - do not create any attendance record
                continue
            
            if attendance:
                # Employee has a real attendance record.
                # Always calculate status from actual IN/OUT activities.
                # This keeps Admin Attendance and Reports consistent with
                # Employee Dashboard/Report calculations.

                is_final = bool(attendance.out_time)

                # Recalculate working hours from all IN-OUT pairs
                attendance.total_hours = self.calculator.calculate_working_hours(attendance)

                # Recalculate status from working hours
                # OUT exists = final calculation
                # No OUT = temporary Present while employee is currently IN
                attendance.status = self.calculator.calculate_status(
                    attendance,
                    attendance.total_hours,
                    is_final_calculation=is_final
                )

                # Recalculate overtime
                attendance.overtime_hours = self.calculator.calculate_overtime(
                    attendance,
                    attendance.total_hours
                )

                # Recalculate late status
                attendance.late_entry = self.calculator.calculate_late_status(attendance)

                # REJECTED logout approvals are ALWAYS treated as ABSENT -
                # never overwrite with hours-based status.
                if has_rejected_approval(attendance):
                    attendance.status = 'absent'

                # Add display_out_time for UI
                self._add_display_out_time(attendance, target_date)

                result.append(attendance)
            else:
                # Employee has no attendance record - apply business rules
                if target_date > today:
                    # CASE A: Future date
                    dummy_attendance = type('obj', (object,), {
                        'employee': employee,
                        'date': target_date,
                        'in_time': None,
                        'out_time': None,
                        'status': '-',
                        'total_hours': 0,  # Use 0 for calculations, display as "-" in template
                        'late_entry': False,
                        'overtime_hours': 0,
                        'is_dummy': True
                    })
                elif target_date == today:
                    # CASE B or C: Today
                    if current_time < office_end_time:
                        # Before office end time - Pending
                        dummy_attendance = type('obj', (object,), {
                            'employee': employee,
                            'date': target_date,
                            'in_time': None,
                            'out_time': None,
                            'status': 'Pending',
                            'total_hours': 0,  # Use 0 for calculations, display as "-" in template
                            'late_entry': False,
                            'overtime_hours': 0,
                            'is_dummy': True
                        })
                    else:
                        # After office end time with no clock-in/clock-out at
                        # all - align with the automated absent-detection
                        # logic (auto checkout treats a missing IN as ABSENT)
                        # instead of leaving it as "Pending" on the dashboard.
                        dummy_attendance = type('obj', (object,), {
                            'employee': employee,
                            'date': target_date,
                            'in_time': None,
                            'out_time': None,
                            'status': 'absent',
                            'total_hours': 0,
                            'late_entry': False,
                            'overtime_hours': 0,
                            'is_dummy': True
                        })
                else:
                    # CASE D: Past date - Absent
                    dummy_attendance = type('obj', (object,), {
                        'employee': employee,
                        'date': target_date,
                        'in_time': None,
                        'out_time': None,
                        'status': 'absent',
                        'total_hours': 0,
                        'late_entry': False,
                        'overtime_hours': 0,
                        'is_dummy': True
                    })
                
                result.append(dummy_attendance)
        
        return result


class WorkingHoursCalculator:
    def __init__(self):
        # Settings are loaded fresh from database on each operation
        pass
    
    def calculate_working_hours(self, attendance):
        """Calculate working hours for attendance record"""
        if attendance.in_time and attendance.out_time:
            total_hours = (attendance.out_time - attendance.in_time).total_seconds() / 3600
            attendance.total_hours = round(total_hours, 2)
            
            # Load settings fresh from database
            settings = Settings.get_settings()
            office_start = self._parse_time(settings.office_start_time)
            office_start_datetime = datetime.combine(attendance.in_time.date(), office_start)
            grace_period = timedelta(minutes=settings.grace_period_minutes)
            late_threshold = office_start_datetime + grace_period
            
            if attendance.in_time > late_threshold:
                attendance.late_entry = True
            
            # Check for early exit
            office_end = self._parse_time(settings.office_end_time)
            if attendance.out_time.time() < office_end:
                attendance.early_exit = True
            
            # Calculate overtime
            working_hours = settings.working_hours_per_day
            if attendance.total_hours > working_hours:
                attendance.overtime_hours = round(attendance.total_hours - working_hours, 2)
            else:
                attendance.overtime_hours = 0.0
            
            return attendance.total_hours
        return 0.0
    
    def _parse_time(self, time_str):
        """Parse time string to time object"""
        hours, minutes = map(int, time_str.split(':'))
        return time(hours, minutes)
