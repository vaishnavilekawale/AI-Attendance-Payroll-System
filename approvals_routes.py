"""
Admin/Manager Approvals routes: the manager and admin approval
dashboards, logout-approval-request approve/reject (both manager-side
and admin-side), manual-attendance approve/reject, the manager/admin
attendance-edit routes reached from the approvals dashboards, and the
two development-only manual-trigger endpoints for daily approval-request
creation.

Seventh blueprint migrated out of app.py - the largest one so far (14
routes). Attendance/camera/cv2 routes deliberately stay in app.py for a
later, dedicated pass; none of these 14 routes touch the camera or
face-recognition pipeline, so they were safe to move now.

The four IST date-filter helper functions these routes depend on
(parse_approvals_filter_date, matches_ist_date, matches_ist_date_range,
parse_approvals_date_range) moved here too, verbatim, from app.py -
they're approvals-page-specific and were not used anywhere else in
app.py (confirmed by search before migration).

Uses services/approval_service.py's approval_service singleton and
services/app_services.py's get_services() - both already-neutral shared
modules - rather than importing anything from app.py, avoiding a
circular import.

Every route here was moved verbatim from app.py - no behavior changes,
with one exception: dev_trigger_approval_requests_test() referenced the
real `app.debug` Flask app object directly; since a Blueprint module has
no `app` object of its own to import without a circular import, this is
now current_app.debug, which is exactly the same value at runtime.

IMPORTANT - endpoint naming: this Blueprint's endpoints are
"approvals.<function_name>". Every url_for()/redirect(url_for(...)) call
elsewhere that referenced these 14 endpoints (manager_approvals,
admin_approvals, manager_edit_attendance, admin_edit_attendance, etc.)
has been updated project-wide (app.py, templates) to the
"approvals."-prefixed form.
"""
from datetime import datetime, date

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session, current_app

from database import db
from models import Employee, Attendance, LogoutApprovalRequest, now_ist
from auth_decorators import login_required, admin_required
from services.app_services import get_services

approvals_bp = Blueprint('approvals', __name__)


# ============================================================
# APPROVALS DATE-FILTER HELPER FUNCTIONS
# ============================================================
# Manager/Admin Approvals pages default to showing only TODAY's
# requests, with an optional ?date=YYYY-MM-DD query param to view a
# different day. Request/attendance timestamps are recorded directly in
# IST (see models.now_ist(), used as the default/onupdate for every
# DateTime column) rather than naive UTC shifted to IST at display time.


def parse_approvals_filter_date():
    """
    Read the `date` query parameter (format 'YYYY-MM-DD') used by the
    Manager/Admin Approvals pages. Defaults to today when the parameter is
    missing, blank, or not a valid date, so the pages always load safely.
    """
    raw_value = request.args.get('date', '').strip()
    if not raw_value:
        return date.today()
    try:
        return datetime.strptime(raw_value, '%Y-%m-%d').date()
    except ValueError:
        return date.today()


def matches_ist_date(ist_dt, selected_date):
    """
    True if an IST datetime (e.g. LogoutApprovalRequest.created_at or
    Attendance.submission_timestamp - both already stored in IST, see
    models.now_ist()) falls on `selected_date`.
    """
    if not ist_dt:
        return False
    return ist_dt.date() == selected_date


def matches_ist_date_range(ist_dt, date_from, date_to):
    """
    True if an IST datetime (e.g. LogoutApprovalRequest.created_at or
    Attendance.submission_timestamp - both already stored in IST, see
    models.now_ist()) falls within [date_from, date_to] (inclusive on
    both ends).
    """
    if not ist_dt:
        return False
    return date_from <= ist_dt.date() <= date_to


def parse_approvals_date_range():
    """
    Read the `date_from` / `date_to` query params (format 'YYYY-MM-DD')
    used by the Manager/Admin Approvals pages' history date-range filter.

    - No params at all -> defaults to TODAY only (both ends), so the page
      always loads with the clean "today's records" default view.
    - The legacy single `date` param is still honoured for backward
      compatibility with any previously bookmarked/shared links.
    - Invalid/partial values fall back to today; a reversed range is
      swapped so `date_from` is never after `date_to`.
    """
    def _parse(raw):
        raw = (raw or '').strip()
        if not raw:
            return None
        try:
            return datetime.strptime(raw, '%Y-%m-%d').date()
        except ValueError:
            return None

    today = date.today()
    raw_from = request.args.get('date_from', '')
    raw_to = request.args.get('date_to', '')

    if not raw_from.strip() and not raw_to.strip():
        legacy = _parse(request.args.get('date', ''))
        if legacy:
            return legacy, legacy
        return today, today

    date_from = _parse(raw_from) or today
    date_to = _parse(raw_to) or today

    if date_from > date_to:
        date_from, date_to = date_to, date_from

    return date_from, date_to


# ============================================================
# MANAGER APPROVALS
# ============================================================

@approvals_bp.route('/manager/approvals')
@login_required
def manager_approvals():
    """Manager approval dashboard - shows pending, approved, and rejected requests"""
    # Check if user is a manager
    if session.get('user_role') != 'employee':
        flash('Access denied. Managers only.', 'danger')
        return redirect(url_for('attendance.dashboard'))

    employee_id = session.get('employee_id')
    employee = db.session.get(Employee, employee_id)

    if not employee or employee.designation != 'Manager':
        flash('Access denied. You are not a manager.', 'danger')
        return redirect(url_for('attendance.employee_dashboard'))

    from services.approval_service import approval_service

    # Date-range filter: defaults to TODAY only, or the ?date_from=&date_to=
    # query params (legacy ?date= is also honoured). This applies ONLY to
    # the completed Approved/Rejected history sections below - Pending
    # requests are always shown regardless of date so nothing awaiting
    # action ever silently disappears from view.
    date_from, date_to = parse_approvals_date_range()

    # Get all logout requests for this manager
    all_requests = approval_service.get_all_requests_for_manager(employee_id)

    # created_at is already stored in IST (see models.now_ist()) - no
    # offset needed here. `.created_at_ist` is kept as an attribute name
    # for backward compatibility with any code/template reading it.
    for approval_request in all_requests:
        if approval_request.created_at:
            approval_request.created_at_ist = approval_request.created_at

    # Pending: no date filter - always show every outstanding request.
    pending_requests = [r for r in all_requests if r.status == 'pending']
    # Approved/Rejected history: scoped to the selected date range.
    approved_requests = [
        r for r in all_requests
        if r.status == 'approved' and matches_ist_date_range(r.created_at, date_from, date_to)
    ]
    rejected_requests = [
        r for r in all_requests
        if r.status == 'rejected' and matches_ist_date_range(r.created_at, date_from, date_to)
    ]

    # Get pending manual attendance requests for this manager's department
    # No date filter: pending requests should be visible regardless of submission date
    pending_manual_attendance = approval_service.get_pending_manual_attendance_requests(manager_id=employee_id, admin_view=False)

    # Get already-approved/rejected manual attendance requests so rejection
    # remarks are visible in a history table on this dashboard, scoped to
    # the selected date range (by submission timestamp, in IST).
    approved_manual_attendance, rejected_manual_attendance = approval_service.get_processed_manual_attendance_requests(
        manager_id=employee_id, admin_view=False
    )
    approved_manual_attendance = [
        a for a in approved_manual_attendance
        if matches_ist_date_range(a.submission_timestamp, date_from, date_to)
    ]
    rejected_manual_attendance = [
        a for a in rejected_manual_attendance
        if matches_ist_date_range(a.submission_timestamp, date_from, date_to)
    ]

    return render_template('manager_approvals.html',
                         pending_requests=pending_requests,
                         approved_requests=approved_requests,
                         rejected_requests=rejected_requests,
                         pending_manual_attendance=pending_manual_attendance,
                         approved_manual_attendance=approved_manual_attendance,
                         rejected_manual_attendance=rejected_manual_attendance,
                         manager=employee,
                         date_from=date_from,
                         date_to=date_to,
                         today=date.today())


@approvals_bp.route('/manager/approve-logout/<int:request_id>', methods=['POST'])
@login_required
def approve_logout_request(request_id):
    """Approve a logout approval request"""
    # Check if user is a manager
    if session.get('user_role') != 'employee':
        return jsonify({'success': False, 'message': 'Access denied. Managers only.'})

    employee_id = session.get('employee_id')
    employee = db.session.get(Employee, employee_id)

    if not employee or employee.designation != 'Manager':
        return jsonify({'success': False, 'message': 'Access denied. You are not a manager.'})

    from services.approval_service import approval_service

    result = approval_service.approve_logout_request(request_id, employee_id)

    if result['success']:
        current_app.logger.info(f"Logout request {request_id} approved by manager {employee.name}")

    return jsonify(result)


@approvals_bp.route('/manager/reject-logout/<int:request_id>', methods=['POST'])
@login_required
def reject_logout_request(request_id):
    """
    Reject a logout approval request.
    Manager can only reject requests assigned to them.
    Rejected auto-logout attendance is marked Absent.
    """

    try:
        # ============================================================
        # VERIFY MANAGER LOGIN
        # ============================================================

        if session.get('user_role') != 'employee':
            return jsonify({
                'success': False,
                'message': 'Access denied. Managers only.'
            }), 403

        approver_id = session.get('employee_id')

        if not approver_id:
            return jsonify({
                'success': False,
                'message': 'Manager session not found.'
            }), 401

        # Verify logged-in employee is actually a Manager
        manager = db.session.get(Employee, approver_id)

        if not manager or manager.designation != 'Manager':
            return jsonify({
                'success': False,
                'message': 'Access denied. You are not a manager.'
            }), 403

        # ============================================================
        # GET REMARKS FROM REQUEST
        # ============================================================

        data = request.get_json(silent=True) or {}
        remarks = data.get('remarks')

        current_app.logger.info(
            f"MANAGER REJECT REQUEST - "
            f"Request ID: {request_id}, "
            f"Manager ID: {approver_id}"
        )

        # ============================================================
        # FIND APPROVAL REQUEST
        # ============================================================

        approval_request = db.session.get(LogoutApprovalRequest, request_id)

        if not approval_request:
            return jsonify({
                'success': False,
                'message': 'Approval request not found'
            }), 404

        # ============================================================
        # CHECK REQUEST STATUS
        # ============================================================

        if approval_request.status != 'pending':
            return jsonify({
                'success': False,
                'message': f'Request already {approval_request.status}'
            }), 400

        # ============================================================
        # VERIFY ASSIGNED MANAGER
        # ============================================================

        if approval_request.manager_id != approver_id:
            current_app.logger.warning(
                f"UNAUTHORIZED REJECT ATTEMPT - "
                f"Request ID: {request_id}, "
                f"Assigned Manager ID: {approval_request.manager_id}, "
                f"Attempted Manager ID: {approver_id}"
            )

            return jsonify({
                'success': False,
                'message': 'You are not authorized to reject this request'
            }), 403

        # ============================================================
        # REJECT AUTO LOGOUT
        # ============================================================

        approval_request.status = 'rejected'
        # BUG FIX: recorded in IST via now_ist(), consistent with
        # created_at and every other timestamp in this app, rather than
        # the naive server-local datetime.now() this route used previously.
        approval_request.approved_at = now_ist()
        approval_request.approved_by = approver_id
        approval_request.remarks = remarks

        # ============================================================
        # GET RELATED ATTENDANCE
        # ============================================================

        attendance = db.session.get(Attendance, approval_request.attendance_id)

        if attendance:

            # Mark attendance as ABSENT
            attendance.status = 'Absent'

            # IMPORTANT:
            # DO NOT set OUT time
            # DO NOT calculate working hours
            # DO NOT modify IN time

            current_app.logger.info(
                f"Attendance marked ABSENT after logout rejection - "
                f"Attendance ID: {attendance.id}, "
                f"Employee ID: {attendance.employee_id}, "
                f"Date: {attendance.date}"
            )

        else:

            current_app.logger.warning(
                f"Attendance not found for rejected approval request - "
                f"Request ID: {request_id}, "
                f"Attendance ID: {approval_request.attendance_id}"
            )

        # ============================================================
        # COMMIT
        # ============================================================

        db.session.commit()

        current_app.logger.info(
            f"MANAGER REJECT SUCCESS - "
            f"Request ID: {request_id}, "
            f"Manager ID: {approver_id}, "
            f"Attendance ID: {approval_request.attendance_id}"
        )

        # ============================================================
        # RETURN JSON
        # ============================================================

        return jsonify({
            'success': True,
            'message': 'Logout request rejected and attendance marked as Absent'
        }), 200

    except Exception as e:

        db.session.rollback()

        current_app.logger.error(
            f"ERROR REJECTING LOGOUT REQUEST - "
            f"Request ID: {request_id}, "
            f"Error: {e}"
        )

        import traceback
        current_app.logger.error(traceback.format_exc())

        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


# ============================================================
# MANUAL ATTENDANCE APPROVE / REJECT (MANAGER + ADMIN)
# ============================================================

@approvals_bp.route('/manual-attendance/approve/<int:attendance_id>', methods=['POST'])
@login_required
def approve_manual_attendance(attendance_id):
    """Approve a manual attendance request"""
    from services.approval_service import approval_service

    # Check if user is manager or admin
    user_role = session.get('user_role')
    if user_role not in ['employee', 'admin']:
        return jsonify({'success': False, 'message': 'Access denied'}), 403

    approver_id = None
    if user_role == 'employee':
        approver_id = session.get('employee_id')
        manager = db.session.get(Employee, approver_id)
        if not manager or manager.designation != 'Manager':
            return jsonify({'success': False, 'message': 'Only managers can approve manual attendance'}), 403

        # A Manager's own manual attendance must ONLY be finalized by the
        # Admin - never by a manager (self or peer).
        target = db.session.get(Attendance, attendance_id)
        if target and target.employee and target.employee.designation == 'Manager':
            return jsonify({'success': False, 'message': "A Manager's manual attendance can only be approved by the Admin"}), 403
    elif user_role == 'admin':
        approver_id = session.get('admin_id')

    result = approval_service.approve_manual_attendance(attendance_id, approver_id)

    if result.get('success'):
        return jsonify(result), 200
    else:
        return jsonify(result), 400


@approvals_bp.route('/manual-attendance/reject/<int:attendance_id>', methods=['POST'])
@login_required
def reject_manual_attendance(attendance_id):
    """Reject a manual attendance request"""
    from services.approval_service import approval_service

    # Check if user is manager or admin
    user_role = session.get('user_role')
    if user_role not in ['employee', 'admin']:
        return jsonify({'success': False, 'message': 'Access denied'}), 403

    approver_id = None
    if user_role == 'employee':
        approver_id = session.get('employee_id')
        manager = db.session.get(Employee, approver_id)
        if not manager or manager.designation != 'Manager':
            return jsonify({'success': False, 'message': 'Only managers can reject manual attendance'}), 403

        # A Manager's own manual attendance must ONLY be finalized by the
        # Admin - never by a manager (self or peer).
        target = db.session.get(Attendance, attendance_id)
        if target and target.employee and target.employee.designation == 'Manager':
            return jsonify({'success': False, 'message': "A Manager's manual attendance can only be rejected by the Admin"}), 403
    elif user_role == 'admin':
        approver_id = session.get('admin_id')

    data = request.get_json(silent=True) or {}
    remarks = data.get('remarks', None)

    result = approval_service.reject_manual_attendance(attendance_id, approver_id, remarks)

    if result.get('success'):
        return jsonify(result), 200
    else:
        return jsonify(result), 400


@approvals_bp.route('/manager/pending-manual-attendance')
@login_required
def manager_pending_manual_attendance():
    """Manager view for pending manual attendance requests"""
    # Check if user is a manager
    if session.get('user_role') != 'employee':
        flash('Access denied.', 'danger')
        return redirect(url_for('attendance.dashboard'))

    employee_id = session.get('employee_id')
    manager = db.session.get(Employee, employee_id)

    if not manager or manager.designation != 'Manager':
        flash('Access denied. Only managers can view pending manual attendance.', 'danger')
        return redirect(url_for('attendance.dashboard'))

    from services.approval_service import approval_service
    pending_requests = approval_service.get_pending_manual_attendance_requests(manager_id=employee_id, admin_view=False)

    return render_template('manager_pending_manual_attendance.html',
                          pending_requests=pending_requests,
                          manager=manager)


@approvals_bp.route('/admin/pending-manual-attendance')
@login_required
@admin_required
def admin_pending_manual_attendance():
    """Admin view for all pending manual attendance requests"""
    from services.approval_service import approval_service
    pending_requests = approval_service.get_pending_manual_attendance_requests(admin_view=True)

    return render_template('admin_pending_manual_attendance.html',
                          pending_requests=pending_requests)


# ============================================================
# ATTENDANCE EDIT (REACHED FROM THE APPROVALS DASHBOARDS)
# ============================================================

@approvals_bp.route('/manager/edit-attendance/<int:attendance_id>', methods=['GET', 'POST'])
@login_required
def manager_edit_attendance(attendance_id):
    """Manager attendance edit is DISABLED - Managers can only Approve/Reject requests"""
    # Check if user is a manager
    if session.get('user_role') != 'employee':
        flash('Access denied.', 'danger')
        return redirect(url_for('attendance.dashboard'))

    employee_id = session.get('employee_id')
    manager = db.session.get(Employee, employee_id)

    if not manager or manager.designation != 'Manager':
        flash('Access denied.', 'danger')
        return redirect(url_for('attendance.employee_dashboard'))

    # BLOCK all manager attendance edit attempts
    current_app.logger.warning(f"[Manager Attendance Edit Blocked] Manager ID: {manager.id}, Attempted Attendance ID: {attendance_id}")
    flash('Managers can only Approve or Reject logout requests. Attendance editing is not permitted.', 'danger')
    return redirect(url_for('approvals.manager_approvals'))


@approvals_bp.route('/admin/edit-attendance/<int:attendance_id>', methods=['GET', 'POST'])
@login_required
def admin_edit_attendance(attendance_id):
    """Admin can edit attendance time for any employee"""
    # Check if user is admin
    if session.get('user_role') != 'admin':
        flash('Access denied. Admins only.', 'danger')
        return redirect(url_for('attendance.dashboard'))

    attendance = db.session.get(Attendance, attendance_id)

    if not attendance:
        flash('Attendance record not found.', 'danger')
        return redirect(url_for('approvals.admin_approvals'))

    if request.method == 'POST':
        try:
            current_app.logger.info("ADMIN ATTENDANCE EDIT - POST Request")
            current_app.logger.info(f"Attendance ID: {attendance_id}")
            current_app.logger.info(f"Employee ID: {attendance.employee_id}")
            current_app.logger.info(f"Employee Name: {attendance.employee.name}")
            current_app.logger.info(f"Date: {attendance.date}")
            current_app.logger.info(f"OLD IN: {attendance.in_time}")
            current_app.logger.info(f"OLD OUT: {attendance.out_time}")
            current_app.logger.info(f"OLD STATUS: {attendance.status}")
            current_app.logger.info(f"OLD TOTAL HOURS: {attendance.total_hours}")

            # Get edited times
            in_time_str = request.form.get('in_time')
            out_time_str = request.form.get('out_time')

            current_app.logger.info(f"NEW IN (raw): {in_time_str}")
            current_app.logger.info(f"NEW OUT (raw): {out_time_str}")

            # Parse times
            if in_time_str:
                attendance.in_time = datetime.strptime(in_time_str, '%Y-%m-%dT%H:%M')

            if out_time_str:
                attendance.out_time = datetime.strptime(out_time_str, '%Y-%m-%dT%H:%M')
            else:
                attendance.out_time = None

            current_app.logger.info(f"NEW IN (parsed): {attendance.in_time}")
            current_app.logger.info(f"NEW OUT (parsed): {attendance.out_time}")

            # Skip AttendanceActivity sync when admin edits attendance
            # The calculation will use attendance.in_time and attendance.out_time directly
            # This preserves cross-day datetime information that AttendanceActivity cannot store
            # (AttendanceActivity only stores time + attendance_date, not full datetime)

            # Clear any rejected logout approval request when admin manually edits attendance
            # Admin's manual edit overrides the automatic rejection
            rejected_request = LogoutApprovalRequest.query.filter_by(
                attendance_id=attendance.id,
                status='rejected'
            ).first()
            if rejected_request:
                current_app.logger.info(f"Clearing rejected logout approval request ID: {rejected_request.id} for Attendance ID: {attendance.id}")
                db.session.delete(rejected_request)
                # Flush the deletion so has_rejected_approval doesn't find it during recalculation
                db.session.flush()

            # Recalculate attendance fields using edited times (skip AttendanceActivity)
            from attendance import AttendanceManager
            am = AttendanceManager()
            am.calculator.recalculate_attendance(attendance, is_final_calculation=True, use_activities=False)

            current_app.logger.info(f"NEW STATUS: {attendance.status}")
            current_app.logger.info(f"NEW TOTAL HOURS: {attendance.total_hours}")
            current_app.logger.info(f"NEW OVERTIME HOURS: {attendance.overtime_hours}")
            current_app.logger.info(f"NEW LATE ENTRY: {attendance.late_entry}")

            attendance.updated_at = now_ist()
            db.session.commit()

            current_app.logger.info("DATABASE COMMIT SUCCESS")
            current_app.logger.info(f"Attendance {attendance_id} edited by admin")
            flash('Attendance updated successfully!', 'success')
            return redirect(url_for('approvals.admin_approvals'))

        except Exception as e:
            current_app.logger.error(f"Error editing attendance: {e}")
            import traceback
            current_app.logger.error(traceback.format_exc())
            flash(f'Error updating attendance: {str(e)}', 'danger')

    return render_template('admin_edit_attendance.html', attendance=attendance)


# ============================================================
# ADMIN APPROVALS DASHBOARD
# ============================================================

@approvals_bp.route('/admin/approvals')
@login_required
def admin_approvals():
    """Admin approval dashboard - shows manager approval requests and employee approval history"""
    # Check if user is admin
    if session.get('user_role') != 'admin':
        flash('Access denied. Admins only.', 'danger')
        return redirect(url_for('attendance.dashboard'))

    from services.approval_service import approval_service

    # Date-range filter: defaults to TODAY only, or the ?date_from=&date_to=
    # query params (legacy ?date= is also honoured). This applies to the
    # completed Approved/Rejected/History sections and the all-employees
    # attendance table below - Pending requests are always shown regardless
    # of date so nothing awaiting action ever silently disappears from view.
    date_from, date_to = parse_approvals_date_range()

    # Get all requests across all managers/departments
    all_requests = approval_service.get_all_requests_for_admin()

    # created_at is already stored in IST (see models.now_ist()) - no
    # offset needed here. `.created_at_ist` is kept as an attribute name
    # for backward compatibility with any code/template reading it.
    for approval_request in all_requests:
        if approval_request.created_at:
            approval_request.created_at_ist = approval_request.created_at

    # Filter to show ONLY Manager requests in top sections (employee.designation == 'Manager')
    # Employee (non-manager) requests must never reach the Admin queue at
    # the initial/pending stage - they are handled exclusively by the
    # employee's department manager.
    manager_requests = [r for r in all_requests if r.employee.designation == 'Manager']

    # Pending: no date filter - always show every outstanding Manager request.
    pending_requests = [r for r in manager_requests if r.status == 'pending']
    # Approved/Rejected history (Manager requests only): scoped to the
    # selected date range.
    approved_requests = [
        r for r in manager_requests
        if r.status == 'approved' and matches_ist_date_range(r.created_at, date_from, date_to)
    ]
    rejected_requests = [
        r for r in manager_requests
        if r.status == 'rejected' and matches_ist_date_range(r.created_at, date_from, date_to)
    ]

    # Get employee approval history (all approval requests for normal employees)
    # This shows which manager handled which employee's approval
    employee_approval_history = [
        r for r in all_requests
        if r.employee.designation != 'Manager' and matches_ist_date_range(r.created_at, date_from, date_to)
    ]

    # ============================================================
    # REQUIREMENT 2: ADMIN APPROVAL HISTORY
    # Combined approval history showing ALL completed actions
    # (approved/rejected) across both managers and employees.
    # This is displayed in the 'Approval History' section below.
    # Scoped to the selected date range.
    # ============================================================
    approval_history = [
        r for r in all_requests
        if r.status in ('approved', 'rejected') and matches_ist_date_range(r.created_at, date_from, date_to)
    ]

    # Get attendance records for ALL active employees, scoped to the
    # selected date range (defaults to today only)
    # This is for the "Attendance Records (All Employees)" section

    # Get all active employees (not just managers)
    all_active_employees = Employee.query.filter_by(status='active').all()
    all_employee_ids = [emp.id for emp in all_active_employees]

    # Get attendance records for all active employees within the selected date range
    week_attendance = Attendance.query.filter(
        Attendance.employee_id.in_(all_employee_ids),
        Attendance.date >= date_from,
        Attendance.date <= date_to
    ).order_by(Attendance.date.desc(), Attendance.employee_id).all()

    # Add display_out_time for UI
    am, _, _, _ = get_services()
    for att in week_attendance:
        am._add_display_out_time(att, att.date)

    # Get pending manual attendance requests for admin view (Managers' own
    # requests only - see get_pending_manual_attendance_requests).
    # No date filter: pending requests should be visible regardless of submission date
    pending_manual_attendance = approval_service.get_pending_manual_attendance_requests(admin_view=True)

    # Get already-approved/rejected manual attendance requests (all departments)
    # so rejection remarks are visible in a history table on this dashboard,
    # scoped to the selected date range (by submission timestamp, in IST).
    approved_manual_attendance, rejected_manual_attendance = approval_service.get_processed_manual_attendance_requests(
        admin_view=True
    )
    approved_manual_attendance = [
        a for a in approved_manual_attendance
        if matches_ist_date_range(a.submission_timestamp, date_from, date_to)
    ]
    rejected_manual_attendance = [
        a for a in rejected_manual_attendance
        if matches_ist_date_range(a.submission_timestamp, date_from, date_to)
    ]

    return render_template('admin_approvals.html',
                         pending_requests=pending_requests,
                         approved_requests=approved_requests,
                         rejected_requests=rejected_requests,
                         employee_approval_history=employee_approval_history,
                         approval_history=approval_history,
                         week_attendance=week_attendance,
                         pending_manual_attendance=pending_manual_attendance,
                         approved_manual_attendance=approved_manual_attendance,
                         rejected_manual_attendance=rejected_manual_attendance,
                         date_from=date_from,
                         date_to=date_to,
                         today=date.today())


@approvals_bp.route('/admin/approve-logout/<int:request_id>', methods=['POST'])
@login_required
def admin_approve_logout_request(request_id):
    """Approve a logout approval request (Admin only)"""
    # Check if user is admin
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'message': 'Access denied. Admins only.'})

    from services.approval_service import approval_service

    result = approval_service.approve_logout_request_admin(request_id, session.get('admin_id'))

    if result['success']:
        current_app.logger.info(f"Logout request {request_id} approved by admin")

    return jsonify(result)


@approvals_bp.route('/admin/reject-logout/<int:request_id>', methods=['POST'])
@login_required
def admin_reject_logout_request(request_id):
    """Reject a logout approval request (Admin only)"""
    # Check if user is admin
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'message': 'Access denied. Admins only.'})

    from services.approval_service import approval_service

    remarks = request.form.get('remarks')
    result = approval_service.reject_logout_request_admin(request_id, session.get('admin_id'), remarks)

    if result['success']:
        current_app.logger.info(f"Logout request {request_id} rejected by admin")

    return jsonify(result)


# ============================================================
# DEVELOPMENT-ONLY MANUAL TRIGGERS
# ============================================================

@approvals_bp.route('/admin/dev/trigger-approval-requests', methods=['POST'])
@login_required
def dev_trigger_approval_requests():
    """
    DEVELOPMENT ONLY: Manually trigger daily approval request creation
    This allows testing the approval workflow without waiting for 23:59
    Requires admin login and session.
    """
    # Check if user is admin
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'message': 'Access denied. Admins only.'})

    current_app.logger.warning("DEVELOPMENT: Manual approval request trigger initiated by admin")

    from services.approval_service import approval_service
    result = approval_service.create_daily_approval_requests()

    current_app.logger.warning(f"DEVELOPMENT: Manual trigger completed - {result}")

    return jsonify({
        'success': True,
        'message': f"Approval request creation triggered manually. {result['count']} requests created for {result['date']}",
        'result': result
    })


@approvals_bp.route('/admin/dev/trigger-approval-requests-test', methods=['POST'])
def dev_trigger_approval_requests_test():
    """
    DEVELOPMENT ONLY: Manually trigger daily approval request creation without login
    This allows testing the approval workflow from terminal without browser session
    Protected by Flask debug mode check only - DO NOT use in production.
    """
    # Development-only protection: only allow in Flask debug mode.
    # current_app.debug is exactly the same value app.debug was in app.py -
    # this Blueprint just has no `app` object of its own to import.
    if not current_app.debug:
        current_app.logger.error("SECURITY: Attempted to access dev test route in non-debug mode")
        return jsonify({'success': False, 'message': 'Development route only available in debug mode'}), 403

    current_app.logger.warning("DEVELOPMENT TEST ROUTE: Manual approval request trigger initiated (no auth)")

    from services.approval_service import approval_service
    result = approval_service.create_daily_approval_requests()

    current_app.logger.warning(f"DEVELOPMENT TEST ROUTE: Manual trigger completed - {result}")

    return jsonify({
        'success': True,
        'message': f"Approval request creation triggered manually. {result['count']} requests created for {result['date']}",
        'count': result['count'],
        'date': str(result['date'])
    })
