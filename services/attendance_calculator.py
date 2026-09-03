"""
Centralized Attendance Calculator Service

This module provides unified attendance calculation functions used across:
- Dashboard
- Reports  
- Payroll
- Attendance marking

All calculations are driven by Settings table values.
"""
import logging
from datetime import datetime, time, timedelta

from database import db
from models import Attendance, AttendanceActivity, Settings
from services.attendance_stats import has_rejected_approval

logger = logging.getLogger(__name__)


class AttendanceCalculator:
    """Centralized attendance calculation service"""
    
    def __init__(self):
        # Settings are loaded fresh from database on each operation
        pass


    
    def calculate_working_hours(self, attendance):
        """
        Calculate total working hours from all IN-OUT pairs for a day.
        
        This handles unlimited IN/OUT activities by summing all valid pairs.
        After manager approval, the OUT activity (23:59) is created, so this
        correctly calculates hours without double-counting attendance.out_time.
        
        IMPORTANT: When attendance.in_time and attendance.out_time are both set
        (e.g., after admin edit), use them directly to preserve cross-day datetime
        information that AttendanceActivity cannot store (it only stores time + date).
        
        Args:
            attendance: Attendance object with in_time and date
            
        Returns:
            float: Total working hours
        """
        if not attendance.in_time:
            return 0.0
        
        # If both in_time and out_time are set, use them directly
        # This handles admin edits with cross-day shifts correctly
        if attendance.in_time and attendance.out_time:
            total_seconds = (attendance.out_time - attendance.in_time).total_seconds()
            result = round(total_seconds / 3600, 2)
            return result
        
        # Get all activities for this employee on this date
        activities = AttendanceActivity.query.filter_by(
            employee_id=attendance.employee_id,
            attendance_date=attendance.date
        ).order_by(AttendanceActivity.activity_time).all()
        
        if not activities:
            # No activities recorded and no out_time, return 0
            return 0.0
        
        # Calculate working hours from IN-OUT pairs
        total_hours = 0.0
        in_time = None
        pair_count = 0
        
        for activity in activities:
            if activity.action == 'IN':
                # Convert time to datetime for calculation
                in_datetime = datetime.combine(attendance.date, activity.activity_time)
                in_time = in_datetime
            elif activity.action == 'OUT' and in_time:
                # Calculate duration for this IN-OUT pair
                out_datetime = datetime.combine(attendance.date, activity.activity_time)
                # Handle case where OUT is next day (cross-day shift)
                if out_datetime < in_time:
                    out_datetime = out_datetime + timedelta(days=1)
                duration = (out_datetime - in_time).total_seconds() / 3600
                pair_count += 1
                total_hours += duration
                in_time = None
        
        result = round(total_hours, 2)
        return result
    
    def calculate_status(self, attendance, working_hours=None, is_final_calculation=False):
        """
        Calculate final attendance status based on working hours.
        
        Rules:
        - If employee has no IN: status = 'absent'
        - If employee has IN:
            - During IN (not final): status = 'present' (temporary, employee is active)
            - Final calculation (on OUT or auto checkout):
                - If working_hours >= office_hours: status = 'present'
                - Else if working_hours >= half_day_hours: status = 'half_day'
                - Else: status = 'absent'
        
        Note: Late flag is separate and doesn't affect status.
              Late employees are still counted as Present.
        
        Args:
            attendance: Attendance object
            working_hours: Optional pre-calculated working hours
            is_final_calculation: True if this is final calculation (OUT or auto checkout)
                                  False if this is during IN (temporary status)
            
        Returns:
            str: Status ('present', 'half_day', 'absent')
        """
        # Determine the timestamp when attendance status is being finalized
        # This is used to select the correct historical settings
        if is_final_calculation and attendance.out_time:
            # Use OUT time as the calculation timestamp (when status was finalized)
            calculation_timestamp = attendance.out_time
        elif attendance.updated_at:
            # Use updated_at as fallback (when status was last calculated)
            calculation_timestamp = attendance.updated_at
        else:
            # Fallback to current time (shouldn't happen in normal flow)
            calculation_timestamp = datetime.now()
        
        # Use historical settings for the attendance calculation timestamp
        from models import AttendanceSettingsHistory
        historical_settings = AttendanceSettingsHistory.get_settings_for_datetime(calculation_timestamp)
        
        if historical_settings:
            office_hours = historical_settings.working_hours_per_day
            half_day_threshold = historical_settings.half_day_hours if historical_settings.half_day_hours else (office_hours / 2)
            settings_effective_from = historical_settings.effective_from
        else:
            # Fall back to current settings if no history exists
            settings = Settings.get_settings()
            office_hours = settings.working_hours_per_day
            half_day_threshold = settings.half_day_hours if settings.half_day_hours else (office_hours / 2)
            settings_effective_from = "current settings (no history)"
        
        # If no IN time, status is absent
        if not attendance.in_time:
            status = 'absent'
            logger.info(
                f"ATTENDANCE STATUS CALCULATION - "
                f"Employee ID: {attendance.employee_id}, "
                f"Attendance ID: {attendance.id}, "
                f"Date: {attendance.date}, "
                f"Calculation Timestamp: {calculation_timestamp}, "
                f"Working Hours: 0.0, "
                f"Status: {status}, "
                f"Settings Effective From: {settings_effective_from}"
            )
            return status
        
        # If not final calculation (during IN), always return present (temporary)
        # Employee should NEVER be marked absent during IN
        if not is_final_calculation:
            status = 'present'
            logger.info(
                f"ATTENDANCE STATUS CALCULATION (TEMPORARY) - "
                f"Employee ID: {attendance.employee_id}, "
                f"Attendance ID: {attendance.id}, "
                f"Date: {attendance.date}, "
                f"Calculation Timestamp: {calculation_timestamp}, "
                f"Working Hours: {working_hours if working_hours else 'calculating...'}, "
                f"Status: {status}, "
                f"Settings Effective From: {settings_effective_from}"
            )
            return status
        
        # Final calculation (on OUT or auto checkout)
        # Use provided working hours or calculate
        if working_hours is None:
            working_hours = attendance.total_hours if attendance.total_hours else 0.0
        
        # Calculate status based on hours worked (three-tier system)
        if working_hours >= office_hours:
            status = 'present'
        elif working_hours >= half_day_threshold:
            status = 'half_day'
        else:
            status = 'absent'
        
        logger.info(
            f"ATTENDANCE STATUS CALCULATION - "
            f"Employee ID: {attendance.employee_id}, "
            f"Attendance ID: {attendance.id}, "
            f"Date: {attendance.date}, "
            f"Calculation Timestamp: {calculation_timestamp}, "
            f"Working Hours: {working_hours}, "
            f"Status: {status}, "
            f"Settings Effective From: {settings_effective_from}"
        )
        
        return status
    
    def calculate_late_status(self, attendance):
        """
        Calculate if employee arrived late based on office start time + grace period.
        
        Args:
            attendance: Attendance object with in_time
            
        Returns:
            bool: True if late, False otherwise
        """
        if not attendance.in_time:
            return False
        
        # Determine the timestamp when attendance was calculated
        calculation_timestamp = attendance.in_time
        
        # Use historical settings for the attendance calculation timestamp
        from models import AttendanceSettingsHistory
        historical_settings = AttendanceSettingsHistory.get_settings_for_datetime(calculation_timestamp)
        
        if historical_settings:
            office_start_str = historical_settings.office_start_time
            grace_minutes = historical_settings.grace_period_minutes
        else:
            # Fall back to current settings if no history exists
            settings = Settings.get_settings()
            office_start_str = settings.office_start_time
            grace_minutes = settings.grace_period_minutes
        
        # Parse office start time
        office_start = datetime.strptime(office_start_str, '%H:%M').time()
        office_start_datetime = datetime.combine(attendance.in_time.date(), office_start)
        
        # Calculate late threshold (office start + grace period)
        late_threshold = office_start_datetime + timedelta(minutes=grace_minutes)
        
        # Check if IN time is after late threshold
        return attendance.in_time > late_threshold
    
    def calculate_overtime(self, attendance, working_hours=None):
        """
        Calculate overtime hours based on configured working hours per day.
        
        Args:
            attendance: Attendance object
            working_hours: Optional pre-calculated working hours
            
        Returns:
            float: Overtime hours (0 if no overtime)
        """
        # Determine the timestamp when attendance was calculated
        calculation_timestamp = attendance.out_time if attendance.out_time else attendance.updated_at
        
        # Use historical settings for the attendance calculation timestamp
        from models import AttendanceSettingsHistory
        historical_settings = AttendanceSettingsHistory.get_settings_for_datetime(calculation_timestamp)
        
        if historical_settings:
            working_hours_per_day = historical_settings.working_hours_per_day
        else:
            # Fall back to current settings if no history exists
            settings = Settings.get_settings()
            working_hours_per_day = settings.working_hours_per_day
        
        if working_hours is None:
            working_hours = attendance.total_hours if attendance.total_hours else 0.0
        
        if working_hours > working_hours_per_day:
            return round(working_hours - working_hours_per_day, 2)
        return 0.0
    
    def process_auto_checkout(self, attendance, commit_to_db=False):
        """
        Apply automatic checkout at 23:59 for attendance records with IN but no OUT.
        
        This is used by the scheduler at 11:59 PM daily.
        
        Args:
            attendance: Attendance object with IN time but no OUT time
            commit_to_db: If True, commit changes to database (only for scheduled job)
            
        Returns:
            Attendance object with auto checkout applied (if applicable)
        """
        logger.info(
            f"process_auto_checkout called: attendance_id={attendance.id}, commit_to_db={commit_to_db}"
        )
        if attendance.in_time and not attendance.out_time:
            # Employee has IN but no OUT - apply auto checkout
            attendance_date = attendance.in_time.date()
            auto_out_time = datetime.combine(attendance_date, time(23, 59, 0))
            attendance.out_time = auto_out_time
            
            # Recalculate working hours using all IN-OUT pairs
            attendance.total_hours = self.calculate_working_hours(attendance)
            
            # Recalculate status based on working hours (final calculation)
            attendance.status = self.calculate_status(attendance, attendance.total_hours, is_final_calculation=True)
            
            # Recalculate overtime
            attendance.overtime_hours = self.calculate_overtime(attendance, attendance.total_hours)
            
            # Recalculate late status
            attendance.late_entry = self.calculate_late_status(attendance)
            
            # Only commit to database if explicitly requested (for scheduled job)
            if commit_to_db:
                activity = AttendanceActivity(
                    employee_id=attendance.employee_id,
                    attendance_date=attendance_date,
                    activity_time=time(23, 59, 0),
                    action='OUT'
                )

                db.session.add(activity)
                db.session.add(attendance)   # <-- ही line add कर

                db.session.commit()

                logger.info(
                    f"Auto checkout applied and committed for employee "
                    f"{attendance.employee_id} on {attendance_date}"
                )
            # if commit_to_db:
            #     # Log the auto checkout OUT activity to maintain complete history
            #     activity = AttendanceActivity(
            #         employee_id=attendance.employee_id,
            #         attendance_date=attendance_date,
            #         activity_time=time(23, 59, 0),
            #         action='OUT'
            #     )
            #     db.session.add(activity)
            #     db.session.commit()
            #     logger.info(f"Auto checkout applied and committed for employee {attendance.employee_id} on {attendance_date} - Status: {attendance.status}, Hours: {attendance.total_hours}")
            # else:
            #     logger.info(f"Auto checkout calculated (not committed) for employee {attendance.employee_id} on {attendance_date} - Status: {attendance.status}, Hours: {attendance.total_hours}")
            #     pass
        
        return attendance
    
    def recalculate_attendance(self, attendance, is_final_calculation=False):
        """
        Recalculate all attendance fields using historical settings for the attendance timestamp.
        
        This uses the settings that were effective at the time of attendance calculation to preserve
        historical accuracy. When admin changes settings, old attendance records remain
        unchanged.
        
        Args:
            attendance: Attendance object to recalculate
            is_final_calculation: True if this is final calculation (OUT or auto checkout)
                                  False if this is during IN (should never be absent)
        """
        # Recalculate working hours from all IN-OUT pairs
        attendance.total_hours = self.calculate_working_hours(attendance)
        
        # Recalculate status based on working hours (uses historical settings)
        attendance.status = self.calculate_status(attendance, attendance.total_hours, is_final_calculation=is_final_calculation)
        
        # Recalculate overtime (uses historical settings)
        attendance.overtime_hours = self.calculate_overtime(attendance, attendance.total_hours)
        
        # Recalculate late status (uses historical settings)
        attendance.late_entry = self.calculate_late_status(attendance)
        
        # Check for early exit (uses historical settings)
        from models import AttendanceSettingsHistory
        calculation_timestamp = attendance.out_time if attendance.out_time else attendance.updated_at
        historical_settings = AttendanceSettingsHistory.get_settings_for_datetime(calculation_timestamp)
        
        if historical_settings:
            office_end_str = historical_settings.office_end_time
        else:
            settings = Settings.get_settings()
            office_end_str = settings.office_end_time
        
        if attendance.out_time:
            office_end = datetime.strptime(office_end_str, '%H:%M').time()
            attendance.early_exit = attendance.out_time.time() < office_end
        
        # REJECTED logout approvals are ALWAYS treated as ABSENT
        if has_rejected_approval(attendance):
            attendance.status = 'absent'

        # logger.info(f"Recalculated attendance ID {attendance.id} - Status: {attendance.status}, Hours: {attendance.total_hours}, Late: {attendance.late_entry}")
