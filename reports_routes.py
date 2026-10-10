"""
Reports routes: the admin Reports page (filterable list + PDF export),
and the employee-self-service Reports page + its own PDF export.

Fifth blueprint migrated out of app.py. Attendance/camera/cv2 routes
deliberately stay in app.py for a later, dedicated pass - these four
routes have no such dependency (they read already-recorded attendance
data, they don't touch the camera or face-recognition pipeline).

Uses services/app_services.py's get_services() and
services/attendance_stats.py's get_effective_report_status() /
normalize_attendance_status() - both already shared, neutral modules -
rather than importing anything from app.py, avoiding a circular import.

Every route here was moved verbatim from app.py - no behavior changes.
The one adaptation: app.config['UPLOAD_FOLDER'] became
current_app.config['UPLOAD_FOLDER'], since a Blueprint module has no
`app` object of its own to import (importing it from app.py would be
the circular import this whole migration avoids).

IMPORTANT - endpoint naming: this Blueprint's endpoints are
"reports.<function_name>". None of these four routes' bodies contained
a url_for() call to another route (verified before migration), so no
external url_for() updates were needed for this blueprint - unlike the
auth/payroll migration, no template or other file needed changes here.
"""
import os
from datetime import datetime, date, timedelta

from flask import Blueprint, render_template, request, session, jsonify, send_file, current_app

from database import db
from models import Employee, Attendance, AttendanceActivity, Settings
from auth_decorators import login_required, admin_required, employee_required
from services.app_services import get_services
from services.attendance_stats import get_effective_report_status, normalize_attendance_status

reports_bp = Blueprint('reports', __name__)


# ==================== ADMIN REPORTS ====================

@reports_bp.route('/reports')
@login_required
@admin_required
def reports():
    """Display attendance report in HTML with enhanced filtering and analytics

    Uses AdminReportsService for centralized data generation.
    Ensures consistent data between screen display and PDF export.
    """
    from services.admin_reports_service import AdminReportsService

    # Get filter parameters
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    employee_id = request.args.get('employee_id')
    department = request.args.get('department')
    designation = request.args.get('designation')
    status = request.args.get('status')
    report_type = request.args.get('type', 'daily')

    # Parse dates
    start_date = None
    end_date = None
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except Exception as e:
            current_app.logger.error(f"Error parsing start_date: {e}")
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except Exception as e:
            current_app.logger.error(f"Error parsing end_date: {e}")

    # Convert employee_id to int if provided
    employee_id_int = None
    if employee_id and employee_id.strip():
        try:
            employee_id_int = int(employee_id)
        except Exception as e:
            current_app.logger.error(f"Error parsing employee_id: {e}")

    # Build filters dict
    filters = {
        'start_date': start_date,
        'end_date': end_date,
        'employee_id': employee_id_int,
        'department': department if department else None,
        'designation': designation if designation else None,
        'status': status if status else None
    }

    # Get all employees for dropdowns
    all_employees = Employee.query.filter_by(status='active').all()
    all_departments = sorted(set(emp.department for emp in all_employees))
    all_designations = sorted(set(emp.designation for emp in all_employees))

    # Generate report data using centralized service
    service = AdminReportsService()
    report_data = service.generate_report_data(filters)

    # Get activities for each attendance record
    am, _, _, _ = get_services()
    activities_by_attendance = {}
    for att in report_data['attendances']:
        if att.employee and not hasattr(att, 'is_dummy'):
            activities = AttendanceActivity.query.filter_by(
                employee_id=att.employee.id,
                attendance_date=att.date
            ).filter(AttendanceActivity.hidden_by_admin.isnot(True)).order_by(AttendanceActivity.activity_time).all()
            activities_by_attendance[(att.employee.id, att.date)] = activities

            # Add display_out_time for UI
            am._add_display_out_time(att, att.date)

    # Determine employee object if specific employee selected
    employee = None
    if employee_id_int:
        employee = db.session.get(Employee, employee_id_int)

    return render_template('reports.html',
                         attendances=report_data['attendances'],
                         employees=all_employees,
                         all_departments=all_departments,
                         all_designations=all_designations,
                         employee=employee,
                         employee_id=employee_id,
                         department=department,
                         designation=designation,
                         status=status,
                         start_date=start_date_str,
                         end_date=end_date_str,
                         report_type=report_type,
                         summary=report_data['summary'],
                         department_analytics=report_data['department_analytics'],
                         rankings=report_data['rankings'],
                         employee_summary=report_data['employee_summary'],
                         late_analysis=report_data['late_analysis'],
                         daily_trend=report_data['daily_trend'],
                         activities_by_attendance=activities_by_attendance)


@reports_bp.route('/reports/export')
@login_required
@admin_required
def export_report():
    """Export Admin Reports PDF using same filtered dataset as screen

    Uses AdminReportsService to ensure PDF contains exactly the same data
    as displayed on the Admin Reports page with applied filters.
    Uses dedicated generate_admin_reports_pdf() function for Admin Reports,
    separate from the employee attendance report generator.
    """
    from services.admin_reports_service import AdminReportsService

    # Get filter parameters (same as screen)
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    employee_id = request.args.get('employee_id')
    department = request.args.get('department')
    designation = request.args.get('designation')
    status = request.args.get('status')
    report_type = request.args.get('type', 'pdf')

    # Parse dates
    start_date = None
    end_date = None
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except Exception as e:
            current_app.logger.error(f"Error parsing start_date: {e}")
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except Exception as e:
            current_app.logger.error(f"Error parsing end_date: {e}")

    # Convert employee_id to int if provided
    employee_id_int = None
    if employee_id and employee_id.strip():
        try:
            employee_id_int = int(employee_id)
        except Exception as e:
            current_app.logger.error(f"Error parsing employee_id: {e}")

    # Build filters dict (same as screen)
    filters = {
        'start_date': start_date,
        'end_date': end_date,
        'employee_id': employee_id_int,
        'department': department if department else None,
        'designation': designation if designation else None,
        'status': status if status else None
    }

    # Generate report data using same service as screen
    service = AdminReportsService()
    report_data = service.generate_report_data(filters)

    # Generate filename
    if employee_id_int:
        filename = f"admin_report_employee_{employee_id_int}_{start_date_str}_to_{end_date_str}.pdf"
    else:
        filename = f"admin_report_{start_date_str}_to_{end_date_str}.pdf"

    output_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)

    # Attach ALL (visible) IN/OUT punches of each day so the PDF lists them in an
    # Activities column, same as the employee report PDF and the Reports page.
    for att in report_data['attendances']:
        if att.employee and not hasattr(att, 'is_dummy'):
            acts = AttendanceActivity.query.filter_by(
                employee_id=att.employee.id,
                attendance_date=att.date
            ).filter(AttendanceActivity.hidden_by_admin.isnot(True)).order_by(
                AttendanceActivity.activity_time, AttendanceActivity.id
            ).all()
            att.activities_text = ', '.join(
                f"{a.activity_time.strftime('%H:%M')} {a.action}" for a in acts
            )

    # Generate PDF using dedicated Admin Reports PDF generator
    _, _, _, pg = get_services()
    company_settings = Settings.get_settings()
    pg.generate_admin_reports_pdf(report_data, filters, output_path, company_settings=company_settings)

    if not os.path.exists(output_path):
        current_app.logger.error("[ADMIN REPORT PDF] PDF generation failed - file not created")
        return jsonify({'success': False, 'message': 'PDF generation failed'})

    return send_file(output_path, as_attachment=True, download_name=filename)


# ==================== EMPLOYEE-SELF-SERVICE REPORTS ====================

@reports_bp.route('/employee-reports')
@login_required
@employee_required
def employee_reports():
    """Employee reports page showing only their own reports"""
    employee_id = session['employee_id']
    employee = db.session.get(Employee, employee_id)

    current_user = employee

    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    status_filter = request.args.get('status')

    am, _, _, _ = get_services()

    # Validation messages
    validation_error = None

    # Default to the CURRENT MONTH (1st of month to today) if no filters were
    # provided (initial page load). Older records are still available by
    # changing From Date / To Date and clicking Apply Filter. If the employee
    # joined mid-month, start from the joining date.
    if not start_date and not end_date and not status_filter:
        today_ = date.today()
        start_date = today_.replace(day=1)
        if employee.joining_date and employee.joining_date > start_date:
            start_date = employee.joining_date
        end_date = today_
    elif start_date or end_date or status_filter:
        # User clicked filter button - validate and apply filters
        if start_date:
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
                # Validate against joining date
                if employee.joining_date and start_date < employee.joining_date:
                    validation_error = "You cannot view attendance records before your joining date."
                    start_date = employee.joining_date
            except ValueError:
                validation_error = "Invalid From Date format."
                start_date = employee.joining_date if employee.joining_date else date.today()
        else:
            start_date = employee.joining_date if employee.joining_date else date.today()

        if end_date:
            try:
                end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
                # Validate end date is not before start date
                if end_date < start_date:
                    validation_error = "To Date cannot be earlier than From Date."
                    end_date = start_date
            except ValueError:
                validation_error = "Invalid To Date format."
                end_date = date.today()
        else:
            end_date = date.today()
    else:
        # Default case
        start_date = employee.joining_date if employee.joining_date else date.today()
        end_date = date.today()

    # Exclude today from the historical report window - today is only
    # finalized after the day ends (evaluated starting midnight / next day).
    end_date = min(end_date, date.today() - timedelta(days=1))

    # Get attendance for this employee only
    attendances = []
    current_date = start_date
    while current_date <= end_date:
        daily_attendance = am.calculate_attendance_with_absent(current_date)
        employee_attendance = [att for att in daily_attendance if att.employee.id == employee_id]
        attendances.extend(employee_attendance)
        current_date += timedelta(days=1)

    # Apply status filter if provided
    if status_filter and status_filter.strip():
        # Normalize the requested filter status
        requested_status = normalize_attendance_status(status_filter)

        if status_filter == 'late':
            # Special handling for Late filter - use late_entry field instead of status
            attendances = [att for att in attendances if att.late_entry]
        else:
            # For other status filters, use effective report status
            # This ensures Half Day records with raw status='Pending' are correctly filtered
            attendances = [
                att for att in attendances
                if get_effective_report_status(att) == requested_status
            ]

    # For template, pass empty strings for date fields if no filter was applied
    # This ensures date fields are empty on initial page load
    template_start_date = request.args.get('start_date', '')
    template_end_date = request.args.get('end_date', '')

    # Get activities for each attendance record
    activities_by_attendance = {}
    for att in attendances:
        if att.employee and not hasattr(att, 'is_dummy'):
            activities = AttendanceActivity.query.filter_by(
                employee_id=att.employee.id,
                attendance_date=att.date
            ).filter(AttendanceActivity.hidden_by_admin.isnot(True)).order_by(AttendanceActivity.activity_time).all()
            activities_by_attendance[(att.employee.id, att.date)] = activities

            # CRITICAL: Apply same status recalculation logic as Employee Dashboard
            # This ensures past records show correct Present/Half Day/Absent status
            # matching the Dashboard exactly
            # If both IN and OUT are set (admin edit), use database values - don't recalculate
            if att.in_time and att.date < date.today():
                if not (att.in_time and att.out_time):
                    # Only recalculate if using activities (not admin-edited)
                    am.calculator.recalculate_attendance(att, is_final_calculation=True, use_activities=True)

            # Add display_out_time for UI (show "-" after new IN until next OUT)
            am._add_display_out_time(att, att.date)

    return render_template('employee_reports.html',
                         employee=employee,
                         current_user=current_user,
                         attendances=attendances,
                         start_date=template_start_date,
                         end_date=template_end_date,
                         status_filter=status_filter,
                         validation_error=validation_error,
                         activities_by_attendance=activities_by_attendance)


@reports_bp.route('/employee-reports/export')
@login_required
@employee_required
def employee_export_report():
    """Export attendance report as PDF for logged-in employee only
    Uses the same filters as the displayed report page for consistency
    """
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    status_filter = request.args.get('status', '')

    if not start_date or not end_date:
        return jsonify({'success': False, 'message': 'Start date and end date are required'})

    employee_id = session['employee_id']
    employee = db.session.get(Employee, employee_id)

    if not employee:
        return jsonify({'success': False, 'message': 'Employee not found'})

    am, _, _, pg = get_services()

    start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
    end_date = datetime.strptime(end_date, '%Y-%m-%d').date()

    # Adjust start_date to employee's joining date if needed
    effective_start_date = start_date
    if employee.joining_date:
        effective_start_date = max(start_date, employee.joining_date)

    # Check if entire report period is before joining date
    if employee.joining_date and end_date < employee.joining_date:
        return jsonify({'success': False, 'message': f"No attendance records are available. You joined on {employee.joining_date.strftime('%d-%m-%Y')}."})

    # Exclude today from the historical report window - today is only
    # finalized after the day ends (evaluated starting midnight / next day).
    end_date = min(end_date, date.today() - timedelta(days=1))

    attendances = []
    current_date = effective_start_date
    while current_date <= end_date:
        # Get attendance data for this date using centralized function
        daily_attendance = am.calculate_attendance_with_absent(current_date)
        # Filter to only include the logged-in employee
        employee_attendance = [att for att in daily_attendance if att.employee.id == employee.id]
        attendances.extend(employee_attendance)
        current_date += timedelta(days=1)

    # Apply status filter if provided (same logic as employee_reports route)
    if status_filter and status_filter.strip():
        if status_filter == 'late':
            # Special handling for Late filter - use late_entry field instead of status
            attendances = [att for att in attendances if att.late_entry]
        else:
            # For other status filters, use status field
            attendances = [att for att in attendances if att.status == status_filter]

    # Add display_out_time for PDF export using the actual Attendance record
    for att in attendances:
        actual_attendance = Attendance.query.filter_by(
            employee_id=employee.id,
            date=att.date
        ).first()

        if actual_attendance:
            am._add_display_out_time(actual_attendance, att.date)
            att.display_out_time = actual_attendance.display_out_time
        else:
            att.display_out_time = None

    # Attach ALL (visible) IN/OUT activities of each day so the PDF lists every
    # punch, same as the Activities column on the Reports page.
    for att in attendances:
        acts = AttendanceActivity.query.filter_by(
            employee_id=employee.id,
            attendance_date=att.date
        ).filter(AttendanceActivity.hidden_by_admin.isnot(True)).order_by(
            AttendanceActivity.activity_time, AttendanceActivity.id
        ).all()
        att.activities_text = ', '.join(
            f"{a.activity_time.strftime('%H:%M')} {a.action}" for a in acts
        )

    filename = f"attendance_report_{employee.employee_id}_{start_date}_to_{end_date}.pdf"
    output_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    company_settings = Settings.get_settings()
    pg.generate_attendance_report(attendances, employee, str(effective_start_date), str(end_date), output_path, company_settings=company_settings)

    if not os.path.exists(output_path):
        return jsonify({'success': False, 'message': 'PDF generation failed'})

    return send_file(output_path, as_attachment=True, download_name=filename)