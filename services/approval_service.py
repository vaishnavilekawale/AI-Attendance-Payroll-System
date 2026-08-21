"""
Approval Service for Manager Auto Logout Approval Workflow
Handles creation, approval, and rejection of logout approval requests
"""
from datetime import datetime, time
from models import Employee, Attendance, LogoutApprovalRequest, AttendanceActivity
from database import db
from sqlalchemy.exc import IntegrityError
import logging

logger = logging.getLogger(__name__)


class ApprovalService:
    """Service for managing logout approval requests"""
    
    def find_department_manager(self, department):
        """
        Find the manager for a given department
        
        Args:
            department: Department name
            
        Returns:
            Employee object who is a manager for this department, or None
        """
        manager = Employee.query.filter_by(
            department=department,
            designation='Manager',
            status='active'
        ).first()
        
        if not manager:
            logger.warning(f"No manager found for department: {department}")
        
        return manager
    
    def create_logout_approval_request(self, attendance):
        """
        Create a logout approval request for an attendance record
        
        Args:
            attendance: Attendance object with IN time but no OUT time
            
        Returns:
            LogoutApprovalRequest object if created, None if already exists
        """
        logger.info(f"create_logout_approval_request called for Attendance ID: {attendance.id}")
        
        # Check if ANY request already exists for this attendance (regardless of status)
        # This prevents creating duplicate requests even if previous one was rejected
        existing_request = LogoutApprovalRequest.query.filter_by(
            attendance_id=attendance.id,
            request_type='auto_logout'
        ).first()
        
        if existing_request:
            logger.warning(f"Request already exists for Attendance ID {attendance.id} - Request ID: {existing_request.id}, Status: {existing_request.status}")
            return None
        
        logger.info(f"No existing request found for Attendance ID {attendance.id}")
        
        # Find the manager for this employee's department
        logger.info(f"Finding manager for department: {attendance.employee.department}")
        manager = self.find_department_manager(attendance.employee.department)
        
        if not manager:
            logger.error(f"Cannot create approval request - no manager found for department {attendance.employee.department}")
            return None
        
        logger.info(f"Manager found - ID: {manager.id}, Name: {manager.name}, Employee ID: {manager.employee_id}")
        
        # Create the approval request
        try:
            request = LogoutApprovalRequest(
                attendance_id=attendance.id,
                employee_id=attendance.employee_id,
                manager_id=manager.id,
                request_type='auto_logout',
                status='pending',
                created_at=datetime.utcnow()
            )
            
            db.session.add(request)
            db.session.commit()
            
            logger.info(f"DB COMMIT SUCCESS - LogoutApprovalRequest created with ID: {request.id}")
            logger.info(f"Request details - attendance_id: {request.attendance_id}, employee_id: {request.employee_id}, manager_id: {request.manager_id}, status: {request.status}, request_type: {request.request_type}")
            
            # Send email notifications
            self._send_approval_notifications(attendance, manager, request)
            
            return request
        except IntegrityError as e:
            logger.warning(f"IntegrityError - Request already exists for Attendance ID {attendance.id}: {e}")
            db.session.rollback()
            return None
        except Exception as e:
            logger.error(f"DB COMMIT FAILED for Attendance ID {attendance.id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            db.session.rollback()
            return None
    
    def _send_approval_notifications(self, attendance, manager, request):
        """
        Send email notifications to employee and manager with duplicate protection
        
        Args:
            attendance: Attendance object
            manager: Manager Employee object
            request: LogoutApprovalRequest object (for tracking sent status)
        """
        try:
            from email_service import EmailService
            email_service = EmailService()
            
            logger.info(f"AUTO LOGOUT EMAIL CHECK")
            logger.info(f"Request ID: {request.id}")
            logger.info(f"Employee Notification Sent: {request.employee_notification_sent}")
            logger.info(f"Manager Notification Sent: {request.manager_notification_sent}")
            
            # Send email to employee (if not already sent)
            if not request.employee_notification_sent:
                logger.info(f"Attempting to send employee notification to: {attendance.employee.email}")
                employee_email_result = email_service.send_logout_approval_employee_notification(
                    to_email=attendance.employee.email,
                    employee_name=attendance.employee.name,
                    date=attendance.date.strftime('%Y-%m-%d')
                )
                
                if employee_email_result['success']:
                    request.employee_notification_sent = True
                    db.session.commit()
                    logger.info(f"AUTO LOGOUT EMPLOYEE EMAIL SENT")
                    logger.info(f"Request ID: {request.id}")
                    logger.info(f"Employee: {attendance.employee.email}")
                else:
                    logger.error(f"AUTO LOGOUT EMPLOYEE EMAIL FAILED")
                    logger.error(f"Request ID: {request.id}")
                    logger.error(f"Error: {employee_email_result['message']}")
            else:
                logger.info(f"AUTO LOGOUT EMPLOYEE EMAIL ALREADY SENT")
                logger.info(f"Request ID: {request.id}")
            
            # Send email to manager (if not already sent)
            if not request.manager_notification_sent:
                logger.info(f"Attempting to send manager notification to: {manager.email}")
                manager_email_result = email_service.send_logout_approval_manager_notification(
                    to_email=manager.email,
                    manager_name=manager.name,
                    employee_name=attendance.employee.name,
                    employee_id=attendance.employee.employee_id,
                    department=attendance.employee.department,
                    date=attendance.date.strftime('%Y-%m-%d')
                )
                
                if manager_email_result['success']:
                    request.manager_notification_sent = True
                    db.session.commit()
                    logger.info(f"AUTO LOGOUT MANAGER EMAIL SENT")
                    logger.info(f"Request ID: {request.id}")
                    logger.info(f"Manager: {manager.email}")
                else:
                    logger.error(f"AUTO LOGOUT MANAGER EMAIL FAILED")
                    logger.error(f"Request ID: {request.id}")
                    logger.error(f"Error: {manager_email_result['message']}")
            else:
                logger.info(f"AUTO LOGOUT MANAGER EMAIL ALREADY SENT")
                logger.info(f"Request ID: {request.id}")
                
        except Exception as e:
            logger.error(f"Error sending approval notifications: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def approve_logout_request(self, request_id, approver_id):
        """
        Approve a logout approval request
        
        Args:
            request_id: ID of the approval request
            approver_id: ID of the manager approving the request
            
        Returns:
            dict with success status and message
        """
        try:
            request = LogoutApprovalRequest.query.get(request_id)
            
            if not request:
                return {'success': False, 'message': 'Approval request not found'}
            
            if request.status != 'pending':
                return {'success': False, 'message': f'Request already {request.status}'}
            
            # Verify the approver is the assigned manager
            if request.manager_id != approver_id:
                return {'success': False, 'message': 'You are not authorized to approve this request'}
            
            # Get the attendance record
            attendance = Attendance.query.get(request.attendance_id)
            
            if not attendance:
                return {'success': False, 'message': 'Attendance record not found'}
            
            # Set OUT time to 11:59 PM of the attendance date
            attendance_date = attendance.date
            attendance.out_time = datetime.combine(attendance_date, time(23, 59))
            
            logger.info(f"AUTO LOGOUT APPROVED")
            logger.info(f"Attendance ID: {attendance.id}")
            logger.info(f"OUT TIME SET: 23:59")
            
            # Create OUT AttendanceActivity if it doesn't already exist (duplicate protection)
            existing_out = AttendanceActivity.query.filter_by(
                employee_id=attendance.employee_id,
                attendance_date=attendance_date,
                activity_time=time(23, 59),
                action='OUT'
            ).first()
            
            if not existing_out:
                out_activity = AttendanceActivity(
                    employee_id=attendance.employee_id,
                    attendance_date=attendance_date,
                    activity_time=time(23, 59),
                    action='OUT'
                )
                db.session.add(out_activity)
                logger.info(f"Created OUT activity for employee {attendance.employee_id} on {attendance_date} at 23:59")
            
            # Calculate working hours using existing attendance calculator
            from attendance import AttendanceManager
            am = AttendanceManager()
            attendance.total_hours = am.calculator.calculate_working_hours(attendance)
            attendance.status = am.calculator.calculate_status(attendance, attendance.total_hours, is_final_calculation=True)
            attendance.overtime_hours = am.calculator.calculate_overtime(attendance, attendance.total_hours)
            attendance.late_entry = am.calculator.calculate_late_status(attendance)
            
            # Update the approval request
            request.status = 'approved'
            request.approved_at = datetime.now()
            request.approved_by = approver_id
            
            db.session.commit()
            
            logger.info(f"Approval request approved - Request ID: {request_id}, Attendance ID: {attendance.id}, OUT Time: 23:59")
            
            return {'success': True, 'message': 'Logout approved successfully'}
            
        except Exception as e:
            logger.error(f"Error approving request: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {'success': False, 'message': str(e)}
    
    def reject_logout_request(self, request_id, approver_id, remarks=None):
        """
        Reject a logout approval request
        
        Args:
            request_id: ID of the approval request
            approver_id: ID of the manager rejecting the request
            remarks: Optional remarks for rejection
            
        Returns:
            dict with success status and message
        """
        try:
            request = LogoutApprovalRequest.query.get(request_id)
            
            if not request:
                return {'success': False, 'message': 'Approval request not found'}
            
            if request.status != 'pending':
                return {'success': False, 'message': f'Request already {request.status}'}
            
            # Verify the approver is the assigned manager
            if request.manager_id != approver_id:
                return {'success': False, 'message': 'You are not authorized to reject this request'}
            
            # Update the approval request
            request.status = 'rejected'
            request.approved_at = datetime.now()
            request.approved_by = approver_id
            request.remarks = remarks
            
            # Get the attendance record so it can be marked ABSENT on rejection
            attendance = Attendance.query.get(request.attendance_id)
            
            logger.info(f"AUTO LOGOUT REJECTED")
            logger.info(f"Attendance ID: {attendance.id if attendance else 'N/A'}")
            logger.info(f"OUT TIME REMAINS: {attendance.out_time if attendance else 'N/A'}")
            
            if attendance:
                # REJECTED logout approvals are treated strictly as ABSENT
                attendance.status = 'absent'
                logger.info(f"Attendance marked ABSENT after logout rejection - Attendance ID: {attendance.id}")
            
            db.session.commit()
            
            logger.info(f"Approval request rejected - Request ID: {request_id}, Remarks: {remarks}")
            
            return {'success': True, 'message': 'Logout rejected successfully'}
            
        except Exception as e:
            logger.error(f"Error rejecting request: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {'success': False, 'message': str(e)}
    
    def get_pending_requests_for_manager(self, manager_id):
        """
        Get all pending approval requests for a specific manager
        
        IMPORTANT: Excludes requests where the manager is the employee themselves.
        A manager's own approval requests are ONLY visible to the Admin dashboard.
        
        Args:
            manager_id: ID of the manager
            
        Returns:
            List of LogoutApprovalRequest objects
        """
        requests = LogoutApprovalRequest.query.filter(
            LogoutApprovalRequest.manager_id == manager_id,
            LogoutApprovalRequest.status == 'pending',
            LogoutApprovalRequest.employee_id != manager_id  # Exclude own requests
        ).order_by(LogoutApprovalRequest.created_at.desc()).all()
        
        return requests
    
    def get_all_requests_for_manager(self, manager_id):
        """
        Get all approval requests (pending, approved, rejected) for a specific manager
        
        IMPORTANT: Excludes requests where the manager is the employee themselves.
        A manager's own approval requests are ONLY visible to the Admin dashboard.
        
        Args:
            manager_id: ID of the manager
            
        Returns:
            List of LogoutApprovalRequest objects
        """
        requests = LogoutApprovalRequest.query.filter(
            LogoutApprovalRequest.manager_id == manager_id,
            LogoutApprovalRequest.employee_id != manager_id  # Exclude own requests
        ).order_by(LogoutApprovalRequest.created_at.desc()).all()
        
        return requests
    
    def get_all_requests_for_admin(self):
        """
        Get all approval requests (pending, approved, rejected) across all managers/departments for Admin
        
        Returns:
            List of LogoutApprovalRequest objects
        """
        requests = LogoutApprovalRequest.query.order_by(
            LogoutApprovalRequest.created_at.desc()
        ).all()
        
        return requests
    
    def approve_logout_request_admin(self, request_id, admin_id):
        """
        Approve a logout approval request (Admin only - bypasses manager_id check)
        
        Args:
            request_id: ID of the approval request
            admin_id: ID of the admin approving the request
            
        Returns:
            dict with success status and message
        """
        try:
            request = LogoutApprovalRequest.query.get(request_id)
            
            if not request:
                return {'success': False, 'message': 'Approval request not found'}
            
            if request.status != 'pending':
                return {'success': False, 'message': f'Request already {request.status}'}
            
            # Get the attendance record
            attendance = Attendance.query.get(request.attendance_id)
            
            if not attendance:
                return {'success': False, 'message': 'Attendance record not found'}
            
            # Set OUT time to 11:59 PM of the attendance date
            attendance_date = attendance.date
            attendance.out_time = datetime.combine(attendance_date, time(23, 59))
            
            # Create OUT AttendanceActivity if it doesn't already exist (duplicate protection)
            existing_out = AttendanceActivity.query.filter_by(
                employee_id=attendance.employee_id,
                attendance_date=attendance_date,
                activity_time=time(23, 59),
                action='OUT'
            ).first()
            
            if not existing_out:
                out_activity = AttendanceActivity(
                    employee_id=attendance.employee_id,
                    attendance_date=attendance_date,
                    activity_time=time(23, 59),
                    action='OUT'
                )
                db.session.add(out_activity)
                logger.info(f"Created OUT activity for employee {attendance.employee_id} on {attendance_date} at 23:59")
            
            # Calculate working hours using existing attendance calculator
            from attendance import AttendanceManager
            am = AttendanceManager()
            attendance.total_hours = am.calculator.calculate_working_hours(attendance)
            attendance.status = am.calculator.calculate_status(attendance, attendance.total_hours, is_final_calculation=True)
            attendance.overtime_hours = am.calculator.calculate_overtime(attendance, attendance.total_hours)
            attendance.late_entry = am.calculator.calculate_late_status(attendance)
            
            # Update the approval request
            request.status = 'approved'
            request.approved_at = datetime.now()
            request.approved_by = admin_id
            
            db.session.commit()
            
            logger.info(f"Approval request approved by Admin - Request ID: {request_id}, Attendance ID: {attendance.id}, OUT Time: 23:59")
            
            return {'success': True, 'message': 'Logout approved successfully'}
            
        except Exception as e:
            logger.error(f"Error approving request: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {'success': False, 'message': str(e)}
    
    def reject_logout_request_admin(self, request_id, admin_id, remarks=None):
        """
        Reject a logout approval request (Admin only - bypasses manager_id check)
        
        Args:
            request_id: ID of the approval request
            admin_id: ID of the admin rejecting the request
            remarks: Optional remarks for rejection
            
        Returns:
            dict with success status and message
        """
        try:
            request = LogoutApprovalRequest.query.get(request_id)
            
            if not request:
                return {'success': False, 'message': 'Approval request not found'}
            
            if request.status != 'pending':
                return {'success': False, 'message': f'Request already {request.status}'}
            
            # Update the approval request
            request.status = 'rejected'
            request.approved_at = datetime.now()
            request.approved_by = admin_id
            request.remarks = remarks
            
            # Get related attendance record
            attendance = Attendance.query.get(request.attendance_id)
            
            if attendance:
                # Mark attendance as ABSENT
                attendance.status = 'absent'
                logger.info(f"Attendance marked ABSENT after admin logout rejection - Attendance ID: {attendance.id}")
            
            db.session.commit()
            
            logger.info(f"Approval request rejected by Admin - Request ID: {request_id}, Remarks: {remarks}")
            
            return {'success': True, 'message': 'Logout rejected successfully'}
            
        except Exception as e:
            logger.error(f"Error rejecting request: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {'success': False, 'message': str(e)}
    
    def has_pending_approval(self, employee_id):
        """
        Check if an employee has any pending logout approval requests
        
        Args:
            employee_id: ID of the employee
            
        Returns:
            True if employee has pending approval requests, False otherwise
        """
        pending_request = LogoutApprovalRequest.query.filter_by(
            employee_id=employee_id,
            status='pending'
        ).first()
        
        return pending_request is not None
    
    def create_daily_approval_requests(self):
        """
        Create approval requests for all employees who forgot to logout today
        This is called by the scheduler at 11:59 PM
        
        Returns:
            dict with count of requests created
        """
        from datetime import date
        
        today = date.today()
        
        logger.info("DAILY APPROVAL CHECK STARTED")
        logger.info(f"Date: {today}")
        
        # Sunday is a weekly holiday/off day - skip approval request creation
        if today.weekday() == 6:  # 6 = Sunday
            logger.info("Today is Sunday (Weekly Off) - skipping approval request creation")
            return {'count': 0, 'date': today}
        
        # Find all attendance records with IN but no OUT for today
        pending_attendance = Attendance.query.filter(
            Attendance.date == today,
            Attendance.in_time.isnot(None),
            Attendance.out_time.is_(None)
        ).all()
        
        logger.info(f"Number of attendance records found: {len(pending_attendance)}")
        
        requests_created = 0
        
        for attendance in pending_attendance:
            logger.info(f"Processing - Employee ID: {attendance.employee_id}, Employee Name: {attendance.employee.name}, Department: {attendance.employee.department}, Attendance ID: {attendance.id}, IN Time: {attendance.in_time}, OUT Time: {attendance.out_time}")
            
            # Verify IN exists and OUT is NULL before creating request
            if attendance.in_time is not None and attendance.out_time is None:
                request = self.create_logout_approval_request(attendance)
                if request:
                    requests_created += 1
            else:
                logger.warning(f"Skipping attendance ID {attendance.id} - IN: {attendance.in_time}, OUT: {attendance.out_time}")
        
        logger.info(f"DAILY APPROVAL CHECK COMPLETED - Requests created: {requests_created}")
        
        return {'count': requests_created, 'date': today}
    
    def reconcile_missed_approval_requests(self):
        """
        Create approval requests for attendance records that missed the 11:59 PM scheduler
        This is called on application startup to handle cases where the server was OFF at 11:59 PM
        
        This function is idempotent - running it multiple times will not create duplicate requests
        due to the duplicate protection in create_logout_approval_request.
        
        This processes ALL eligible previous attendance records (no arbitrary date limit)
        to handle cases where the server/computer was OFF for extended periods.
        
        Returns:
            dict with count of requests created and skipped
        """
        from datetime import date
        
        logger.info("=" * 60)
        logger.info("AUTO LOGOUT RECONCILIATION STARTED")
        logger.info("=" * 60)
        
        today = date.today()
        
        # Find ALL attendance records with IN but no OUT for previous dates
        # No arbitrary date limit - handles extended server downtime
        # Sundays are weekly holidays/off days - excluded from approval requests
        pending_attendance = Attendance.query.filter(
            Attendance.date < today,
            Attendance.in_time.isnot(None),
            Attendance.out_time.is_(None)
        ).all()
        
        # Filter out Sunday records (weekly holiday/off day)
        pending_attendance = [
            att for att in pending_attendance
            if att.date.weekday() != 6  # 6 = Sunday
        ]
        
        logger.info(f"Number of attendance records found: {len(pending_attendance)}")
        
        requests_created = 0
        requests_skipped = 0
        
        for attendance in pending_attendance:
            logger.info(f"AUTO LOGOUT CHECK")
            logger.info(f"Employee: {attendance.employee.name} (ID: {attendance.employee_id})")
            logger.info(f"Date: {attendance.date}")
            logger.info(f"Attendance ID: {attendance.id}")
            logger.info(f"IN: {attendance.in_time}")
            logger.info(f"OUT: {attendance.out_time}")
            
            # Verify IN exists and OUT is NULL before creating request
            if attendance.in_time is not None and attendance.out_time is None:
                # Check if ANY request already exists (regardless of status)
                existing_request = LogoutApprovalRequest.query.filter_by(
                    attendance_id=attendance.id,
                    request_type='auto_logout'
                ).first()
                
                if existing_request:
                    logger.info(f"AUTO LOGOUT REQUEST ALREADY EXISTS")
                    logger.info(f"Attendance ID: {attendance.id}")
                    logger.info(f"Request ID: {existing_request.id}")
                    logger.info(f"Status: {existing_request.status}")
                    requests_skipped += 1
                else:
                    logger.info(f"AUTO LOGOUT REQUEST NOT FOUND - Creating new request")
                    request = self.create_logout_approval_request(attendance)
                    if request:
                        logger.info(f"AUTO LOGOUT REQUEST CREATED")
                        logger.info(f"Attendance ID: {attendance.id}")
                        logger.info(f"Employee: {attendance.employee.name}")
                        logger.info(f"Manager: {request.manager_id}")
                        logger.info(f"Date: {attendance.date}")
                        requests_created += 1
                    else:
                        logger.warning(f"Failed to create request for Attendance ID {attendance.id}")
            else:
                logger.warning(f"Skipping attendance ID {attendance.id} - IN: {attendance.in_time}, OUT: {attendance.out_time}")
                requests_skipped += 1
        
        logger.info("=" * 60)
        logger.info("AUTO LOGOUT RECONCILIATION COMPLETED")
        logger.info(f"Created: {requests_created}")
        logger.info(f"Skipped: {requests_skipped}")
        logger.info("=" * 60)
        
        return {'created': requests_created, 'skipped': requests_skipped}


# Global approval service instance
approval_service = ApprovalService()
