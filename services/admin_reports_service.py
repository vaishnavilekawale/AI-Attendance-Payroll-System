"""
Admin Reports Service

Centralized service for generating Admin Reports data with filtering and analytics.
Ensures consistent data between screen display and PDF export.
"""
import logging
from datetime import datetime, date, timedelta
from collections import defaultdict

from database import db
from models import Employee, Attendance, AttendanceActivity, Settings
from services.attendance_calculator import AttendanceCalculator
from services.attendance_stats import is_hidden_manual_attendance

logger = logging.getLogger(__name__)


class AdminReportsService:
    """Centralized Admin Reports data generation service"""
    
    def __init__(self):
        self.calculator = AttendanceCalculator()
    
    def generate_report_data(self, filters):
        """
        Generate complete report data based on filters.
        
        This is the SINGLE SOURCE OF TRUTH for Admin Reports data.
        Both HTML display and PDF export must use this function.
        """
        # Log filters
        logger.info("=" * 60)
        logger.info("ADMIN REPORT FILTERS")
        logger.info("=" * 60)
        logger.info(f"Date From: {filters.get('start_date')}")
        logger.info(f"Date To: {filters.get('end_date')}")
        logger.info(f"Department: {filters.get('department')}")
        logger.info(f"Employee ID: {filters.get('employee_id')}")
        logger.info(f"Designation: {filters.get('designation')}")
        logger.info(f"Status: {filters.get('status')}")
        
        # Get filtered employees
        employees = self._filter_employees(filters)
        
        # Get attendance data
        attendances = self._get_attendances(employees, filters)
        
        # Apply status filter if specified
        if filters.get('status'):
            attendances = self._filter_by_status(attendances, filters['status'])
        
        # Log filtered results
        logger.info(f"Filtered Employees: {len(employees)}")
        logger.info(f"Filtered Attendance Records: {len(attendances)}")
        
        # Generate all analytics
        summary = self._calculate_summary(attendances, employees)
        department_analytics = self._calculate_department_analytics(attendances, employees, filters)
        rankings = self._calculate_rankings(attendances, employees, filters)
        employee_summary = self._calculate_employee_summary(attendances, employees, filters)
        late_analysis = self._calculate_late_analysis(attendances, employees, filters)
        daily_trend = self._calculate_daily_trend(attendances, filters)
        
        logger.info(f"Present: {summary['present']}")
        logger.info(f"Absent: {summary['absent']}")
        logger.info(f"Half Day: {summary['half_day']}")
        logger.info(f"Late: {summary['late']}")
        logger.info(f"Total Working Hours: {summary['total_working_hours']}")
        logger.info("=" * 60)
        
        return {
            'attendances': attendances,
            'employees': employees,
            'summary': summary,
            'department_analytics': department_analytics,
            'rankings': rankings,
            'employee_summary': employee_summary,
            'late_analysis': late_analysis,
            'daily_trend': daily_trend,
            'filters': filters
        }
    
    def _filter_employees(self, filters):
        """Filter employees based on department, designation, employee_id"""
        query = Employee.query.filter_by(status='active')
        
        if filters.get('department'):
            query = query.filter_by(department=filters['department'])
        
        if filters.get('designation'):
            query = query.filter_by(designation=filters['designation'])
        
        if filters.get('employee_id'):
            query = query.filter_by(id=filters['employee_id'])
        
        return query.all()
    
    def _get_attendances(self, employees, filters):
        """Get attendance records for filtered employees within date range"""
        from attendance import AttendanceManager
        am = AttendanceManager()
        
        start_date = filters.get('start_date')
        end_date = filters.get('end_date')
        
        logger.info(f"[ADMIN REPORT SERVICE] _get_attendances called")
        logger.info(f"start_date: {start_date}")
        logger.info(f"end_date: {end_date}")
        logger.info(f"employees count: {len(employees)}")
        
        attendances = []
        
        if start_date and end_date:
            logger.info(f"Date range provided, processing per-employee joining date filter")
            
            for employee in employees:
                employee_joining_date = employee.joining_date if employee.joining_date else date.min
                effective_start_date = max(start_date, employee_joining_date)
                
                logger.info(f"ADMIN REPORT DATE FILTER")
                logger.info(f"Employee: {employee.name}")
                logger.info(f"Joining Date: {employee_joining_date}")
                logger.info(f"Requested Start: {start_date}")
                logger.info(f"Requested End: {end_date}")
                logger.info(f"Effective Start: {effective_start_date}")
                
                if effective_start_date > end_date:
                    logger.info(f"Skipping employee (effective start > end date)")
                    continue
                
                if employee_joining_date > start_date:
                    logger.info(f"PRE-JOINING DATES EXCLUDED")
                    logger.info(f"Employee: {employee.name}")
                    logger.info(f"Excluded From: {start_date}")
                    logger.info(f"Excluded To: {employee_joining_date - timedelta(days=1)}")
                
                effective_end_date = min(end_date, date.today() - timedelta(days=1))
                
                current_date = effective_start_date
                while current_date <= effective_end_date:
                    logger.info(f"  Processing date: {current_date}")
                    daily_attendance = am.calculate_attendance_with_absent(current_date)
                    employee_attendance = [att for att in daily_attendance if att.employee.id == employee.id]
                    # Belt-and-suspenders: calculate_attendance_with_absent already
                    # excludes pending/rejected manual attendance, but re-apply the
                    # same rule here so reports never regress even if the source
                    # query changes upstream.
                    employee_attendance = [att for att in employee_attendance if not is_hidden_manual_attendance(
                        getattr(att, 'attendance_type', None), getattr(att, 'approval_status', 'approved')
                    )]
                    logger.info(f"  Attendance count for {current_date}: {len(employee_attendance)}")
                    attendances.extend(employee_attendance)
                    current_date += timedelta(days=1)
        else:
            logger.warning(f"Date range not provided: start_date={start_date}, end_date={end_date}")
        
        logger.info(f"Total attendances collected: {len(attendances)}")
        return attendances
    
    def _filter_by_status(self, attendances, status):
        """Filter attendances by status using effective report status"""
        from app import get_effective_report_status
        
        normalized_status = status.lower().strip()
        
        if normalized_status == 'late':
            return [att for att in attendances if att.late_entry]
        else:
            return [att for att in attendances if get_effective_report_status(att) == normalized_status]
    
    def _calculate_summary(self, attendances, employees):
        """Calculate summary statistics"""
        from app import get_effective_report_status
        
        present = 0
        absent = 0
        half_day = 0
        late = 0
        total_working_hours = 0.0
        
        for att in attendances:
            effective_status = get_effective_report_status(att)
            
            if effective_status == 'present':
                present += 1
            elif effective_status == 'absent':
                absent += 1
            elif effective_status == 'half_day':
                half_day += 1
            
            if att.late_entry:
                late += 1
            
            if att.total_hours:
                total_working_hours += att.total_hours
        
        total_employees = len(employees)
        attendance_percentage = 0.0
        if total_employees > 0 and len(attendances) > 0:
            attendance_percentage = (present / len(attendances)) * 100
        
        return {
            'total_employees': total_employees,
            'present': present,
            'absent': absent,
            'half_day': half_day,
            'late': late,
            'total_working_hours': round(total_working_hours, 2),
            'attendance_percentage': round(attendance_percentage, 2)
        }
    
    def _calculate_department_analytics(self, attendances, employees, filters):
        """Calculate department-wise analytics"""
        from app import get_effective_report_status
        
        dept_employees = defaultdict(list)
        for emp in employees:
            dept_employees[emp.department].append(emp)
        
        dept_stats = {}
        for dept, emp_list in dept_employees.items():
            emp_ids = {emp.id for emp in emp_list}
            dept_attendances = [att for att in attendances if att.employee.id in emp_ids]
            
            present = 0
            absent = 0
            half_day = 0
            late = 0
            total_working_hours = 0.0
            
            for att in dept_attendances:
                effective_status = get_effective_report_status(att)
                
                if effective_status == 'present':
                    present += 1
                elif effective_status == 'absent':
                    absent += 1
                elif effective_status == 'half_day':
                    half_day += 1
                
                if att.late_entry:
                    late += 1
                
                if att.total_hours:
                    total_working_hours += att.total_hours
            
            avg_working_hours = 0.0
            if len(dept_attendances) > 0:
                avg_working_hours = total_working_hours / len(dept_attendances)
            
            attendance_percentage = 0.0
            if len(dept_attendances) > 0:
                attendance_percentage = (present / len(dept_attendances)) * 100
            
            dept_stats[dept] = {
                'total_employees': len(emp_list),
                'present': present,
                'absent': absent,
                'half_day': half_day,
                'late': late,
                'total_working_hours': round(total_working_hours, 2),
                'average_working_hours': round(avg_working_hours, 2),
                'attendance_percentage': round(attendance_percentage, 2)
            }
        
        return dept_stats
    
    def _calculate_rankings(self, attendances, employees, filters):
        """Calculate employee rankings"""
        from app import get_effective_report_status
        
        employee_attendances = defaultdict(list)
        for att in attendances:
            employee_attendances[att.employee.id].append(att)
        
        rankings = {
            'most_present': [],
            'most_absent': [],
            'most_half_day': [],
            'most_late': [],
            'highest_working_hours': [],
            'lowest_working_hours': []
        }
        
        for emp in employees:
            emp_atts = employee_attendances.get(emp.id, [])
            
            present = 0
            absent = 0
            half_day = 0
            late = 0
            total_working_hours = 0.0
            late_minutes = []
            
            for att in emp_atts:
                effective_status = get_effective_report_status(att)
                
                if effective_status == 'present':
                    present += 1
                elif effective_status == 'absent':
                    absent += 1
                elif effective_status == 'half_day':
                    half_day += 1
                
                if att.late_entry:
                    late += 1
                    if att.in_time:
                        settings = Settings.get_settings()
                        office_start = datetime.strptime(settings.office_start_time, '%H:%M').time()
                        office_start_dt = datetime.combine(att.in_time.date(), office_start)
                        grace_period = timedelta(minutes=settings.grace_period_minutes)
                        late_threshold = office_start_dt + grace_period
                        if att.in_time > late_threshold:
                            late_mins = (att.in_time - late_threshold).total_seconds() / 60
                            late_minutes.append(late_mins)
                
                if att.total_hours:
                    total_working_hours += att.total_hours
            
            # Recalculate absent count based on working days to handle missing attendance records
            if len(emp_atts) > 0:
                absent = max(0, len(emp_atts) - (present + half_day))

            total_late_minutes = sum(late_minutes)
            avg_late_minutes = total_late_minutes / len(late_minutes) if late_minutes else 0
            
            attendance_percentage = 0.0
            if len(emp_atts) > 0:
                attendance_percentage = (present / len(emp_atts)) * 100
            
            avg_daily_hours = total_working_hours / len(emp_atts) if emp_atts else 0
            
            emp_data = {
                'employee': emp,
                'employee_id': emp.employee_id,
                'name': emp.name,
                'department': emp.department,
                'present': present,
                'absent': absent,
                'half_day': half_day,
                'late': late,
                'total_late_minutes': round(total_late_minutes, 2),
                'avg_late_minutes': round(avg_late_minutes, 2),
                'total_working_hours': round(total_working_hours, 2),
                'avg_daily_working_hours': round(avg_daily_hours, 2),
                'attendance_percentage': round(attendance_percentage, 2)
            }
            
            rankings['most_present'].append(emp_data.copy())
            rankings['most_absent'].append(emp_data.copy())
            rankings['most_half_day'].append(emp_data.copy())
            rankings['most_late'].append(emp_data.copy())
            rankings['highest_working_hours'].append(emp_data.copy())
            rankings['lowest_working_hours'].append(emp_data.copy())
        
        rankings['most_present'].sort(key=lambda x: x['present'], reverse=True)
        rankings['most_absent'].sort(key=lambda x: x['absent'], reverse=True)
        rankings['most_half_day'].sort(key=lambda x: x['half_day'], reverse=True)
        rankings['most_late'].sort(key=lambda x: (-x['late'], -x['total_late_minutes']))
        rankings['highest_working_hours'].sort(key=lambda x: x['total_working_hours'], reverse=True)
        rankings['lowest_working_hours'].sort(key=lambda x: x['total_working_hours'])
        
        for key in rankings:
            for rank, item in enumerate(rankings[key], 1):
                item['rank'] = rank
        
        return rankings
    
    def _calculate_employee_summary(self, attendances, employees, filters):
        """Calculate employee-wise summary"""
        from app import get_effective_report_status
        
        employee_attendances = defaultdict(list)
        for att in attendances:
            employee_attendances[att.employee.id].append(att)
        
        summary = []
        
        for emp in employees:
            emp_atts = employee_attendances.get(emp.id, [])
            
            total_working_days = len(emp_atts)
            present = 0
            absent = 0
            half_day = 0
            late = 0
            early_checkout = 0
            total_working_hours = 0.0
            
            for att in emp_atts:
                if isinstance(att, type):
                    logger.warning(f"Skipping class object instead of instance: {att}")
                    continue
                
                if not hasattr(att, 'employee') or not att.employee:
                    logger.warning(f"Skipping attendance without employee: {att}")
                    continue
                
                effective_status = get_effective_report_status(att)
                
                if effective_status == 'present':
                    present += 1
                elif effective_status == 'half_day':
                    half_day += 1
                
                try:
                    if att.late_entry:
                        late += 1
                except AttributeError:
                    pass
                
                try:
                    if att.early_exit:
                        early_checkout += 1
                except AttributeError:
                    pass
                
                try:
                    if att.total_hours:
                        total_working_hours += att.total_hours
                except AttributeError:
                    pass
            
            # FIX: Calculate absent days consistently with rankings
            absent = max(0, total_working_days - (present + half_day))

            avg_working_hours = total_working_hours / total_working_days if total_working_days > 0 else 0
            attendance_percentage = (present / total_working_days * 100) if total_working_days > 0 else 0
            punctuality_percentage = ((total_working_days - late) / total_working_days * 100) if total_working_days > 0 else 0
            
            logger.info(f"ADMIN REPORT FINAL CALCULATION")
            logger.info(f"Employee: {emp.name}")
            logger.info(f"Present: {present}")
            logger.info(f"Absent: {absent}")
            logger.info(f"Half Day: {half_day}")
            logger.info(f"Late: {late}")
            logger.info(f"Working Days: {total_working_days}")
            
            summary.append({
                'employee': emp,
                'employee_id': emp.employee_id,
                'name': emp.name,
                'department': emp.department,
                'designation': emp.designation,
                'total_working_days': total_working_days,
                'present': present,
                'absent': absent,
                'half_day': half_day,
                'late': late,
                'early_checkout': early_checkout,
                'total_working_hours': round(total_working_hours, 2),
                'avg_working_hours': round(avg_working_hours, 2),
                'attendance_percentage': round(attendance_percentage, 2),
                'punctuality_percentage': round(punctuality_percentage, 2)
            })
        
        return summary
    
    def _calculate_late_analysis(self, attendances, employees, filters):
        """Calculate late arrival analysis"""
        late_data = []
        
        employee_attendances = defaultdict(list)
        for att in attendances:
            if att.late_entry:
                employee_attendances[att.employee.id].append(att)
        
        for emp in employees:
            emp_atts = employee_attendances.get(emp.id, [])
            
            if not emp_atts:
                continue
            
            late_days = len(emp_atts)
            late_minutes = []
            
            for att in emp_atts:
                if att.in_time:
                    settings = Settings.get_settings()
                    office_start = datetime.strptime(settings.office_start_time, '%H:%M').time()
                    office_start_dt = datetime.combine(att.in_time.date(), office_start)
                    grace_period = timedelta(minutes=settings.grace_period_minutes)
                    late_threshold = office_start_dt + grace_period
                    if att.in_time > late_threshold:
                        late_mins = (att.in_time - late_threshold).total_seconds() / 60
                        late_minutes.append(late_mins)
            
            total_late_minutes = sum(late_minutes)
            avg_late_minutes = total_late_minutes / len(late_minutes) if late_minutes else 0
            max_late_minutes = max(late_minutes) if late_minutes else 0
            
            late_data.append({
                'employee': emp,
                'employee_id': emp.employee_id,
                'name': emp.name,
                'department': emp.department,
                'late_days': late_days,
                'total_late_minutes': round(total_late_minutes, 2),
                'avg_late_minutes': round(avg_late_minutes, 2),
                'max_late_minutes': round(max_late_minutes, 2)
            })
        
        late_data.sort(key=lambda x: x['late_days'], reverse=True)
        return late_data
    
    def _calculate_daily_trend(self, attendances, filters):
        """Calculate date-wise attendance trend"""
        from app import get_effective_report_status
        
        date_attendances = defaultdict(list)
        for att in attendances:
            date_attendances[att.date].append(att)
        
        trend = []
        
        start_date = filters.get('start_date')
        end_date = filters.get('end_date')
        
        if start_date and end_date:
            effective_end_date = min(end_date, date.today() - timedelta(days=1))
            
            current_date = start_date
            while current_date <= effective_end_date:
                day_atts = date_attendances.get(current_date, [])
                
                present = 0
                absent = 0
                half_day = 0
                late = 0
                
                for att in day_atts:
                    effective_status = get_effective_report_status(att)
                    
                    if effective_status == 'present':
                        present += 1
                    elif effective_status == 'absent':
                        absent += 1
                    elif effective_status == 'half_day':
                        half_day += 1
                    
                    if att.late_entry:
                        late += 1
                
                trend.append({
                    'date': current_date,
                    'present': present,
                    'absent': absent,
                    'half_day': half_day,
                    'late': late
                })
                
                current_date += timedelta(days=1)
        
        return trend

# """
# Admin Reports Service

# Centralized service for generating Admin Reports data with filtering and analytics.
# Ensures consistent data between screen display and PDF export.
# """
# import logging
# from datetime import datetime, date, timedelta
# from collections import defaultdict

# from database import db
# from models import Employee, Attendance, AttendanceActivity, Settings
# from services.attendance_calculator import AttendanceCalculator

# logger = logging.getLogger(__name__)


# class AdminReportsService:
#     """Centralized Admin Reports data generation service"""
    
#     def __init__(self):
#         self.calculator = AttendanceCalculator()
    
#     def generate_report_data(self, filters):
#         """
#         Generate complete report data based on filters.
        
#         This is the SINGLE SOURCE OF TRUTH for Admin Reports data.
#         Both HTML display and PDF export must use this function.
        
#         Args:
#             filters: dict with keys:
#                 - start_date: date or None
#                 - end_date: date or None
#                 - department: str or None
#                 - employee_id: int or None
#                 - designation: str or None
#                 - status: str or None (present, absent, half_day, late, etc.)
        
#         Returns:
#             dict with all report data:
#                 - attendances: list of filtered attendance records
#                 - employees: list of filtered employees
#                 - summary: summary statistics
#                 - department_analytics: department-wise data
#                 - rankings: employee rankings
#                 - employee_summary: employee-wise summary
#                 - late_analysis: late arrival analysis
#                 - daily_trend: date-wise trend
#         """
#         # Log filters
#         logger.info("=" * 60)
#         logger.info("ADMIN REPORT FILTERS")
#         logger.info("=" * 60)
#         logger.info(f"Date From: {filters.get('start_date')}")
#         logger.info(f"Date To: {filters.get('end_date')}")
#         logger.info(f"Department: {filters.get('department')}")
#         logger.info(f"Employee ID: {filters.get('employee_id')}")
#         logger.info(f"Designation: {filters.get('designation')}")
#         logger.info(f"Status: {filters.get('status')}")
        
#         # Get filtered employees
#         employees = self._filter_employees(filters)
        
#         # Get attendance data
#         attendances = self._get_attendances(employees, filters)
        
#         # Apply status filter if specified
#         if filters.get('status'):
#             attendances = self._filter_by_status(attendances, filters['status'])
        
#         # Log filtered results
#         logger.info(f"Filtered Employees: {len(employees)}")
#         logger.info(f"Filtered Attendance Records: {len(attendances)}")
        
#         # Generate all analytics
#         summary = self._calculate_summary(attendances, employees)
#         department_analytics = self._calculate_department_analytics(attendances, employees, filters)
#         rankings = self._calculate_rankings(attendances, employees, filters)
#         employee_summary = self._calculate_employee_summary(attendances, employees, filters)
#         late_analysis = self._calculate_late_analysis(attendances, employees, filters)
#         daily_trend = self._calculate_daily_trend(attendances, filters)
        
#         logger.info(f"Present: {summary['present']}")
#         logger.info(f"Absent: {summary['absent']}")
#         logger.info(f"Half Day: {summary['half_day']}")
#         logger.info(f"Late: {summary['late']}")
#         logger.info(f"Total Working Hours: {summary['total_working_hours']}")
#         logger.info("=" * 60)
        
#         return {
#             'attendances': attendances,
#             'employees': employees,
#             'summary': summary,
#             'department_analytics': department_analytics,
#             'rankings': rankings,
#             'employee_summary': employee_summary,
#             'late_analysis': late_analysis,
#             'daily_trend': daily_trend,
#             'filters': filters
#         }
    
#     def _filter_employees(self, filters):
#         """Filter employees based on department, designation, employee_id"""
#         query = Employee.query.filter_by(status='active')
        
#         if filters.get('department'):
#             query = query.filter_by(department=filters['department'])
        
#         if filters.get('designation'):
#             query = query.filter_by(designation=filters['designation'])
        
#         if filters.get('employee_id'):
#             query = query.filter_by(id=filters['employee_id'])
        
#         return query.all()
    
#     def _get_attendances(self, employees, filters):
#         """Get attendance records for filtered employees within date range
        
#         For each employee, effective start date = MAX(selected start_date, employee.joining_date)
#         This ensures no attendance records are shown before an employee's joining date.
#         """
#         from attendance import AttendanceManager
#         am = AttendanceManager()
        
#         start_date = filters.get('start_date')
#         end_date = filters.get('end_date')
        
#         logger.info(f"[ADMIN REPORT SERVICE] _get_attendances called")
#         logger.info(f"start_date: {start_date}")
#         logger.info(f"end_date: {end_date}")
#         logger.info(f"employees count: {len(employees)}")
        
#         attendances = []
        
#         if start_date and end_date:
#             logger.info(f"Date range provided, processing per-employee joining date filter")
            
#             for employee in employees:
#                 # Calculate effective start date for this employee
#                 # Effective start = MAX(selected start_date, employee joining date)
#                 employee_joining_date = employee.joining_date if employee.joining_date else date.min
#                 effective_start_date = max(start_date, employee_joining_date)
                
#                 # Log joining date filter for this employee
#                 logger.info(f"ADMIN REPORT DATE FILTER")
#                 logger.info(f"Employee: {employee.name}")
#                 logger.info(f"Joining Date: {employee_joining_date}")
#                 logger.info(f"Requested Start: {start_date}")
#                 logger.info(f"Requested End: {end_date}")
#                 logger.info(f"Effective Start: {effective_start_date}")
                
#                 # If effective start date is after end date, skip this employee
#                 if effective_start_date > end_date:
#                     logger.info(f"Skipping employee (effective start > end date)")
#                     continue
                
#                 # Log if pre-joining dates are being excluded
#                 if employee_joining_date > start_date:
#                     logger.info(f"PRE-JOINING DATES EXCLUDED")
#                     logger.info(f"Employee: {employee.name}")
#                     logger.info(f"Excluded From: {start_date}")
#                     logger.info(f"Excluded To: {employee_joining_date - timedelta(days=1)}")
                
#                 # Iterate from effective start date to end date for this employee
#                 # Exclude today if it's not yet finalized (before office end time)
#                 # Today should only be counted if it's a past date
#                 effective_end_date = min(end_date, date.today() - timedelta(days=1))
                
#                 current_date = effective_start_date
#                 while current_date <= effective_end_date:
#                     logger.info(f"  Processing date: {current_date}")
#                     # Get attendance data using centralized function
#                     daily_attendance = am.calculate_attendance_with_absent(current_date)
#                     # Filter to only include this employee
#                     employee_attendance = [att for att in daily_attendance if att.employee.id == employee.id]
#                     logger.info(f"  Attendance count for {current_date}: {len(employee_attendance)}")
#                     attendances.extend(employee_attendance)
#                     current_date += timedelta(days=1)
#         else:
#             logger.warning(f"Date range not provided: start_date={start_date}, end_date={end_date}")
        
#         logger.info(f"Total attendances collected: {len(attendances)}")
        
#         # Recalculate attendance for past records to ensure correct status
#         # for att in attendances:
#         #     if att.in_time and att.date < date.today():
#         #         self.calculator.recalculate_attendance(
#         #             att,
#         #             is_final_calculation=True
#         #         )

#         # # Save recalculated values so database status stays synchronized
#         # if attendances:
#         #     db.session.commit()
#         logger.info(f"Total attendances collected: {len(attendances)}")
#         return attendances
#         # for att in attendances:
#         #     if att.in_time and att.date < date.today():
#         #         self.calculator.recalculate_attendance(att, is_final_calculation=True)
        
#         # return attendances
    
#     def _filter_by_status(self, attendances, status):
#         """Filter attendances by status using effective report status"""
#         from app import get_effective_report_status
        
#         normalized_status = status.lower().strip()
        
#         if normalized_status == 'late':
#             # Special handling for Late - use late_entry field
#             return [att for att in attendances if att.late_entry]
#         else:
#             # Use effective report status for other statuses
#             return [att for att in attendances if get_effective_report_status(att) == normalized_status]
    
#     def _calculate_summary(self, attendances, employees):
#         """Calculate summary statistics"""
#         from app import get_effective_report_status
        
#         present = 0
#         absent = 0
#         half_day = 0
#         late = 0
#         total_working_hours = 0.0
        
#         for att in attendances:
#             effective_status = get_effective_report_status(att)
            
#             if effective_status == 'present':
#                 present += 1
#             elif effective_status == 'absent':
#                 absent += 1
#             elif effective_status == 'half_day':
#                 half_day += 1
            
#             if att.late_entry:
#                 late += 1
            
#             if att.total_hours:
#                 total_working_hours += att.total_hours
        
#         total_employees = len(employees)
#         attendance_percentage = 0.0
#         if total_employees > 0 and len(attendances) > 0:
#             attendance_percentage = (present / len(attendances)) * 100
        
#         return {
#             'total_employees': total_employees,
#             'present': present,
#             'absent': absent,
#             'half_day': half_day,
#             'late': late,
#             'total_working_hours': round(total_working_hours, 2),
#             'attendance_percentage': round(attendance_percentage, 2)
#         }
    
#     def _calculate_department_analytics(self, attendances, employees, filters):
#         """Calculate department-wise analytics"""
#         from app import get_effective_report_status
        
#         # Group employees by department
#         dept_employees = defaultdict(list)
#         for emp in employees:
#             dept_employees[emp.department].append(emp)
        
#         # Calculate stats per department
#         dept_stats = {}
#         for dept, emp_list in dept_employees.items():
#             emp_ids = {emp.id for emp in emp_list}
#             dept_attendances = [att for att in attendances if att.employee.id in emp_ids]
            
#             present = 0
#             absent = 0
#             half_day = 0
#             late = 0
#             total_working_hours = 0.0
            
#             for att in dept_attendances:
#                 effective_status = get_effective_report_status(att)
                
#                 if effective_status == 'present':
#                     present += 1
#                 elif effective_status == 'absent':
#                     absent += 1
#                 elif effective_status == 'half_day':
#                     half_day += 1
                
#                 if att.late_entry:
#                     late += 1
                
#                 if att.total_hours:
#                     total_working_hours += att.total_hours
            
#             avg_working_hours = 0.0
#             if len(dept_attendances) > 0:
#                 avg_working_hours = total_working_hours / len(dept_attendances)
            
#             attendance_percentage = 0.0
#             if len(dept_attendances) > 0:
#                 attendance_percentage = (present / len(dept_attendances)) * 100
            
#             dept_stats[dept] = {
#                 'total_employees': len(emp_list),
#                 'present': present,
#                 'absent': absent,
#                 'half_day': half_day,
#                 'late': late,
#                 'total_working_hours': round(total_working_hours, 2),
#                 'average_working_hours': round(avg_working_hours, 2),
#                 'attendance_percentage': round(attendance_percentage, 2)
#             }
        
#         return dept_stats
    
#     def _calculate_rankings(self, attendances, employees, filters):
#         """Calculate employee rankings"""
#         from app import get_effective_report_status
        
#         # Group attendances by employee
#         employee_attendances = defaultdict(list)
#         for att in attendances:
#             employee_attendances[att.employee.id].append(att)
        
#         rankings = {
#             'most_present': [],
#             'most_absent': [],
#             'most_half_day': [],
#             'most_late': [],
#             'highest_working_hours': [],
#             'lowest_working_hours': []
#         }
        
#         for emp in employees:
#             emp_atts = employee_attendances.get(emp.id, [])
            
#             present = 0
#             absent = 0
#             half_day = 0
#             late = 0
#             total_working_hours = 0.0
#             late_minutes = []
            
#             for att in emp_atts:
#                 effective_status = get_effective_report_status(att)
                
#                 if effective_status == 'present':
#                     present += 1
#                 elif effective_status == 'absent':
#                     absent += 1
#                 elif effective_status == 'half_day':
#                     half_day += 1
                
#                 if att.late_entry:
#                     late += 1
#                     # Calculate late minutes
#                     if att.in_time:
#                         settings = Settings.get_settings()
#                         office_start = datetime.strptime(settings.office_start_time, '%H:%M').time()
#                         office_start_dt = datetime.combine(att.in_time.date(), office_start)
#                         grace_period = timedelta(minutes=settings.grace_period_minutes)
#                         late_threshold = office_start_dt + grace_period
#                         if att.in_time > late_threshold:
#                             late_mins = (att.in_time - late_threshold).total_seconds() / 60
#                             late_minutes.append(late_mins)
                
#                 if att.total_hours:
#                     total_working_hours += att.total_hours
            
#             total_late_minutes = sum(late_minutes)
#             avg_late_minutes = total_late_minutes / len(late_minutes) if late_minutes else 0
            
#             attendance_percentage = 0.0
#             if len(emp_atts) > 0:
#                 attendance_percentage = (present / len(emp_atts)) * 100
            
#             avg_daily_hours = total_working_hours / len(emp_atts) if emp_atts else 0
            
#             emp_data = {
#                 'employee': emp,
#                 'employee_id': emp.employee_id,
#                 'name': emp.name,
#                 'department': emp.department,
#                 'present': present,
#                 'absent': absent,
#                 'half_day': half_day,
#                 'late': late,
#                 'total_late_minutes': round(total_late_minutes, 2),
#                 'avg_late_minutes': round(avg_late_minutes, 2),
#                 'total_working_hours': round(total_working_hours, 2),
#                 'avg_daily_working_hours': round(avg_daily_hours, 2),
#                 'attendance_percentage': round(attendance_percentage, 2)
#             }
            
#             rankings['most_present'].append(emp_data.copy())
#             rankings['most_absent'].append(emp_data.copy())
#             rankings['most_half_day'].append(emp_data.copy())
#             rankings['most_late'].append(emp_data.copy())
#             rankings['highest_working_hours'].append(emp_data.copy())
#             rankings['lowest_working_hours'].append(emp_data.copy())
        
#         # Sort rankings
#         rankings['most_present'].sort(key=lambda x: x['present'], reverse=True)
#         rankings['most_absent'].sort(key=lambda x: x['absent'], reverse=True)
#         rankings['most_half_day'].sort(key=lambda x: x['half_day'], reverse=True)
#         rankings['most_late'].sort(key=lambda x: (-x['late'], -x['total_late_minutes']))
#         rankings['highest_working_hours'].sort(key=lambda x: x['total_working_hours'], reverse=True)
#         rankings['lowest_working_hours'].sort(key=lambda x: x['total_working_hours'])
        
#         # Add ranks
#         for key in rankings:
#             for rank, item in enumerate(rankings[key], 1):
#                 item['rank'] = rank
        
#         return rankings
    
#     def _calculate_employee_summary(self, attendances, employees, filters):
#         """Calculate employee-wise summary"""
#         from app import get_effective_report_status
        
#         # Group attendances by employee
#         employee_attendances = defaultdict(list)
#         for att in attendances:
#             employee_attendances[att.employee.id].append(att)
        
#         summary = []
        
#         for emp in employees:
#             emp_atts = employee_attendances.get(emp.id, [])
            
#             total_working_days = len(emp_atts)
#             present = 0
#             absent = 0
#             half_day = 0
#             late = 0
#             early_checkout = 0
#             total_working_hours = 0.0
            
#             for att in emp_atts:
#                 # Skip if att is not a proper attendance object instance
#                 # Check if it's a class object (type) instead of an instance
#                 if isinstance(att, type):
#                     logger.warning(f"Skipping class object instead of instance: {att}")
#                     continue
                
#                 # Skip if att doesn't have employee attribute
#                 if not hasattr(att, 'employee') or not att.employee:
#                     logger.warning(f"Skipping attendance without employee: {att}")
#                     continue
                
#                 effective_status = get_effective_report_status(att)
                
#                 if effective_status == 'present':
#                     present += 1
#                 elif effective_status == 'absent':
#                     absent += 1
#                 elif effective_status == 'half_day':
#                     half_day += 1
                
#                 # Safely access attributes with try-except
#                 try:
#                     if att.late_entry:
#                         late += 1
#                 except AttributeError:
#                     pass
                
#                 try:
#                     if att.early_exit:
#                         early_checkout += 1
#                 except AttributeError:
#                     pass
                
#                 try:
#                     if att.total_hours:
#                         total_working_hours += att.total_hours
#                 except AttributeError:
#                     pass
            
#             avg_working_hours = total_working_hours / total_working_days if total_working_days > 0 else 0
#             attendance_percentage = (present / total_working_days * 100) if total_working_days > 0 else 0
#             punctuality_percentage = ((total_working_days - late) / total_working_days * 100) if total_working_days > 0 else 0
            
#             # Log final calculation for this employee
#             logger.info(f"ADMIN REPORT FINAL CALCULATION")
#             logger.info(f"Employee: {emp.name}")
#             logger.info(f"Present: {present}")
#             logger.info(f"Absent: {absent}")
#             logger.info(f"Half Day: {half_day}")
#             logger.info(f"Late: {late}")
#             logger.info(f"Working Days: {total_working_days}")
            
#             summary.append({
#                 'employee': emp,
#                 'employee_id': emp.employee_id,
#                 'name': emp.name,
#                 'department': emp.department,
#                 'designation': emp.designation,
#                 'total_working_days': total_working_days,
#                 'present': present,
#                 'absent': absent,
#                 'half_day': half_day,
#                 'late': late,
#                 'early_checkout': early_checkout,
#                 'total_working_hours': round(total_working_hours, 2),
#                 'avg_working_hours': round(avg_working_hours, 2),
#                 'attendance_percentage': round(attendance_percentage, 2),
#                 'punctuality_percentage': round(punctuality_percentage, 2)
#             })
        
#         return summary
    
#     def _calculate_late_analysis(self, attendances, employees, filters):
#         """Calculate late arrival analysis"""
#         late_data = []
        
#         # Group attendances by employee
#         employee_attendances = defaultdict(list)
#         for att in attendances:
#             if att.late_entry:
#                 employee_attendances[att.employee.id].append(att)
        
#         for emp in employees:
#             emp_atts = employee_attendances.get(emp.id, [])
            
#             if not emp_atts:
#                 continue
            
#             late_days = len(emp_atts)
#             late_minutes = []
            
#             for att in emp_atts:
#                 if att.in_time:
#                     settings = Settings.get_settings()
#                     office_start = datetime.strptime(settings.office_start_time, '%H:%M').time()
#                     office_start_dt = datetime.combine(att.in_time.date(), office_start)
#                     grace_period = timedelta(minutes=settings.grace_period_minutes)
#                     late_threshold = office_start_dt + grace_period
#                     if att.in_time > late_threshold:
#                         late_mins = (att.in_time - late_threshold).total_seconds() / 60
#                         late_minutes.append(late_mins)
            
#             total_late_minutes = sum(late_minutes)
#             avg_late_minutes = total_late_minutes / len(late_minutes) if late_minutes else 0
#             max_late_minutes = max(late_minutes) if late_minutes else 0
            
#             late_data.append({
#                 'employee': emp,
#                 'employee_id': emp.employee_id,
#                 'name': emp.name,
#                 'department': emp.department,
#                 'late_days': late_days,
#                 'total_late_minutes': round(total_late_minutes, 2),
#                 'avg_late_minutes': round(avg_late_minutes, 2),
#                 'max_late_minutes': round(max_late_minutes, 2)
#             })
        
#         # Sort by late days descending
#         late_data.sort(key=lambda x: x['late_days'], reverse=True)
        
#         return late_data
    
#     def _calculate_daily_trend(self, attendances, filters):
#         """Calculate date-wise attendance trend"""
#         from app import get_effective_report_status
        
#         # Group attendances by date
#         date_attendances = defaultdict(list)
#         for att in attendances:
#             date_attendances[att.date].append(att)
        
#         trend = []
        
#         start_date = filters.get('start_date')
#         end_date = filters.get('end_date')
        
#         if start_date and end_date:
#             # Exclude today if it's not yet finalized (before office end time)
#             # Today should only be counted if it's a past date
#             effective_end_date = min(end_date, date.today() - timedelta(days=1))
            
#             current_date = start_date
#             while current_date <= effective_end_date:
#                 day_atts = date_attendances.get(current_date, [])
                
#                 present = 0
#                 absent = 0
#                 half_day = 0
#                 late = 0
                
#                 for att in day_atts:
#                     effective_status = get_effective_report_status(att)
                    
#                     if effective_status == 'present':
#                         present += 1
#                     elif effective_status == 'absent':
#                         absent += 1
#                     elif effective_status == 'half_day':
#                         half_day += 1
                    
#                     if att.late_entry:
#                         late += 1
                
#                 trend.append({
#                     'date': current_date,
#                     'present': present,
#                     'absent': absent,
#                     'half_day': half_day,
#                     'late': late
#                 })
                
#                 current_date += timedelta(days=1)
        
#         return trend
