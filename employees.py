"""
Employee CRUD routes: list, add, edit, delete, view.

Second blueprint migrated out of app.py (after blueprints/setup_wizard.py),
and a worked example of migrating routes that DO have real dependencies
beyond auth (models, face recognition, email, file uploads) - unlike the
setup wizard, which only needed the auth/DB foundation.

Notably NOT included here: /face-registration, /capture-face, /train-ai,
/delete-face-image. Those routes are tightly coupled to live webcam/cv2
frame handling and stay in app.py for now - grouping "plain CRUD" and
"camera-dependent" routes into separate blueprints is a reasonable
architectural boundary on its own merits (the camera routes will likely
want to move into their own blueprint alongside the /api/auto-scan-
attendance family later), not just a shortcut taken here.

IMPORTANT - endpoint naming: because this is a Blueprint, every route's
Flask endpoint name is now "employees.<function_name>" instead of just
"<function_name>" (e.g. url_for('employees.add_employee') instead of
url_for('add_employee')). Every url_for() call across app.py and the
templates that referenced these 5 endpoints has been updated to match -
see the migration notes for the full list of call sites touched.
"""
import os
import shutil
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename

from database import db
from models import (
    Employee, EmployeeLogin, Settings, Attendance, Payroll,
    LogoutApprovalRequest, AttendanceActivity,
)
from auth_decorators import login_required, admin_required
from face_recognition_singleton import get_face_recognizer
from file_helpers import allowed_file
from email_service import EmailService

employees_bp = Blueprint('employees', __name__)


def _employee_image_counts(dataset_folder, employees):
    """Shared helper: count dataset face images per employee (used by
    list/edit/view, previously duplicated three times in app.py)."""
    counts = {}
    for emp in employees:
        emp_folder = os.path.join(dataset_folder, str(emp.id))
        if os.path.exists(emp_folder):
            image_files = [f for f in os.listdir(emp_folder) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
            counts[emp.id] = len(image_files)
        else:
            counts[emp.id] = 0
    return counts


@employees_bp.route('/employees')
@login_required
@admin_required
def employees():
    search = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    per_page = 10

    query = Employee.query.filter_by(status='active')

    if search:
        query = query.filter(
            (Employee.name.contains(search)) |
            (Employee.employee_id.contains(search)) |
            (Employee.department.contains(search))
        )

    employees_page = query.order_by(Employee.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    settings = Settings.get_settings()
    min_face_images = settings.min_face_images_required if settings else 20

    dataset_folder = os.path.join(current_app.root_path, 'dataset')
    employee_image_counts = _employee_image_counts(dataset_folder, employees_page.items)

    return render_template('add_employee.html',
                         employees=employees_page,
                         search=search,
                         min_face_images=min_face_images,
                         employee_image_counts=employee_image_counts)


@employees_bp.route('/employees/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_employee():
    if request.method == 'POST':
        last_employee = Employee.query.order_by(Employee.id.desc()).first()
        if last_employee:
            new_id = f"EMP{last_employee.id + 1:04d}"
        else:
            new_id = "EMP0001"

        name = request.form.get('name')
        department = request.form.get('department')
        designation = request.form.get('designation')
        basic_salary = float(request.form.get('basic_salary'))
        joining_date = datetime.strptime(request.form.get('joining_date'), '%Y-%m-%d').date()
        email = request.form.get('email')
        phone = request.form.get('phone')
        address = request.form.get('address')
        office_location = request.form.get('office_location')
        bank_name = request.form.get('bank_name')
        bank_account_number = request.form.get('bank_account_number')

        pan_number = request.form.get('pan_number', '').strip() or None
        uan_number = request.form.get('uan_number', '').strip() or None
        pf_number = request.form.get('pf_number', '').strip() or None

        dob_raw = request.form.get('dob')
        dob = datetime.strptime(dob_raw, '%Y-%m-%d').date() if dob_raw else None

        def _parse_allowance(field_name):
            raw_value = request.form.get(field_name, '').strip()
            if not raw_value:
                return 0.0
            try:
                return float(raw_value)
            except ValueError:
                return 0.0

        hra = _parse_allowance('hra')
        da = _parse_allowance('da')
        medical_allowance = _parse_allowance('medical_allowance')
        travel_allowance = _parse_allowance('travel_allowance')
        special_allowance = _parse_allowance('special_allowance')
        other_allowances = _parse_allowance('other_allowances')

        employee_pf_percentage = _parse_allowance('employee_pf_percentage')
        esic_percentage = _parse_allowance('esic_percentage')
        tds_percentage = _parse_allowance('tds_percentage')
        bus_charges = _parse_allowance('bus_charges')
        other_deduction = _parse_allowance('other_deduction')

        if Employee.query.filter_by(name=name).first():
            flash('This Employee Name already exists.', 'danger')
            return redirect(url_for('employees.employees'))

        if Employee.query.filter_by(phone=phone).first():
            flash('This Mobile Number already exists.', 'danger')
            return redirect(url_for('employees.employees'))

        if Employee.query.filter_by(email=email).first():
            flash('This Email ID already exists.', 'danger')
            return redirect(url_for('employees.employees'))

        profile_photo = None
        if 'profile_photo' in request.files:
            file = request.files['profile_photo']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{new_id}_{file.filename}")
                profile_photo_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
                file.save(profile_photo_path)
                profile_photo = filename

        employee = Employee(
            employee_id=new_id,
            username=new_id,
            password_hash=generate_password_hash(phone),
            name=name,
            department=department,
            designation=designation,
            basic_salary=basic_salary,
            joining_date=joining_date,
            dob=dob,
            email=email,
            phone=phone,
            address=address,
            profile_photo=profile_photo,
            office_location=office_location,
            bank_name=bank_name,
            bank_account_number=bank_account_number,
            pan_number=pan_number,
            uan_number=uan_number,
            pf_number=pf_number,
            hra=hra,
            da=da,
            medical_allowance=medical_allowance,
            travel_allowance=travel_allowance,
            special_allowance=special_allowance,
            other_allowances=other_allowances,
            employee_pf_percentage=employee_pf_percentage,
            esic_percentage=esic_percentage,
            tds_percentage=tds_percentage,
            bus_charges=bus_charges,
            other_deduction=other_deduction,
            status='active',
            role='employee',
            must_change_password=True
        )

        db.session.add(employee)
        db.session.flush()  # Flush to get the employee ID

        existing_login = EmployeeLogin.query.filter_by(employee_id=employee.id).first()
        if not existing_login:
            login_creds = EmployeeLogin(
                employee_id=employee.id,
                username=new_id,
                first_login=True,
                force_password_change=True,
                is_active=True
            )
            login_creds.set_password(phone)
            employee.password_hash = generate_password_hash(phone)
            employee.username = new_id
            employee.role = 'employee'
            db.session.add(login_creds)

        db.session.commit()

        try:
            email_service = EmailService()
            email_service.send_welcome_email(email, name, new_id, phone)
        except Exception:
            pass

        flash(f'Employee {new_id} added successfully', 'success')
        return redirect(url_for('employees.employees'))

    return render_template('add_employee.html')


@employees_bp.route('/employees/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_employee(id):
    employee = Employee.query.get_or_404(id)
    all_employees = Employee.query.filter_by(status='active').order_by(Employee.created_at.desc()).all()

    settings = Settings.get_settings()
    min_face_images = settings.min_face_images_required if settings else 20

    dataset_folder = os.path.join(current_app.root_path, 'dataset')
    employee_image_counts = _employee_image_counts(dataset_folder, all_employees)

    if request.method == 'POST':
        name = request.form.get('name')
        department = request.form.get('department')
        designation = request.form.get('designation')
        basic_salary = float(request.form.get('basic_salary'))
        email = request.form.get('email')
        phone = request.form.get('phone')
        address = request.form.get('address')
        office_location = request.form.get('office_location')
        bank_name = request.form.get('bank_name')
        bank_account_number = request.form.get('bank_account_number')

        dob_raw = request.form.get('dob', '').strip()
        dob = datetime.strptime(dob_raw, '%Y-%m-%d').date() if dob_raw else None

        pan_number = request.form.get('pan_number', '').strip() or None
        uan_number = request.form.get('uan_number', '').strip() or None
        pf_number = request.form.get('pf_number', '').strip() or None

        def _parse_allowance(field_name):
            raw_value = request.form.get(field_name, '').strip()
            if not raw_value:
                return 0.0
            try:
                return float(raw_value)
            except ValueError:
                return 0.0

        hra = _parse_allowance('hra')
        da = _parse_allowance('da')
        medical_allowance = _parse_allowance('medical_allowance')
        travel_allowance = _parse_allowance('travel_allowance')
        special_allowance = _parse_allowance('special_allowance')
        other_allowances = _parse_allowance('other_allowances')

        employee_pf_percentage = _parse_allowance('employee_pf_percentage')
        esic_percentage = _parse_allowance('esic_percentage')
        tds_percentage = _parse_allowance('tds_percentage')
        bus_charges = _parse_allowance('bus_charges')
        other_deduction = _parse_allowance('other_deduction')

        if Employee.query.filter(Employee.name == name, Employee.id != id).first():
            flash('This Employee Name already exists.', 'danger')
            return redirect(url_for('employees.edit_employee', id=id))

        if Employee.query.filter(Employee.phone == phone, Employee.id != id).first():
            flash('This Mobile Number already exists.', 'danger')
            return redirect(url_for('employees.edit_employee', id=id))

        if Employee.query.filter(Employee.email == email, Employee.id != id).first():
            flash('This Email ID already exists.', 'danger')
            return redirect(url_for('employees.edit_employee', id=id))

        employee.name = name
        employee.department = department
        employee.designation = designation
        employee.basic_salary = basic_salary
        employee.email = email
        employee.phone = phone
        employee.address = address
        employee.office_location = office_location
        employee.bank_name = bank_name
        employee.bank_account_number = bank_account_number
        employee.pan_number = pan_number
        employee.uan_number = uan_number
        employee.pf_number = pf_number

        if dob is not None:
            employee.dob = dob

        employee.hra = hra
        employee.da = da
        employee.medical_allowance = medical_allowance
        employee.travel_allowance = travel_allowance
        employee.special_allowance = special_allowance
        employee.other_allowances = other_allowances
        employee.employee_pf_percentage = employee_pf_percentage
        employee.esic_percentage = esic_percentage
        employee.tds_percentage = tds_percentage
        employee.bus_charges = bus_charges
        employee.other_deduction = other_deduction

        if 'profile_photo' in request.files:
            file = request.files['profile_photo']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{employee.employee_id}_{file.filename}")
                profile_photo_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
                file.save(profile_photo_path)
                employee.profile_photo = filename

        db.session.commit()
        flash('Employee updated successfully', 'success')
        return redirect(url_for('employees.employees'))

    return render_template('add_employee.html', employee=employee, employees=all_employees,
                         edit_mode=True, min_face_images=min_face_images,
                         employee_image_counts=employee_image_counts)


@employees_bp.route('/employees/delete/<int:id>')
@login_required
@admin_required
def delete_employee(id):
    employee = Employee.query.get_or_404(id)

    recognizer = get_face_recognizer()
    recognizer.remove_employee(str(employee.id))

    # Ordered, safe cleanup of every table with a foreign key back to this
    # employee (or to that employee's attendance rows) before the
    # Employee row itself is deleted (see original app.py comment for the
    # full FK-ordering rationale this preserves unchanged).
    attendance_ids = [
        att_id for (att_id,) in
        db.session.query(Attendance.id).filter_by(employee_id=id).all()
    ]

    logout_approval_filters = [
        LogoutApprovalRequest.employee_id == id,
        LogoutApprovalRequest.manager_id == id,
        LogoutApprovalRequest.approved_by == id,
    ]
    if attendance_ids:
        logout_approval_filters.append(LogoutApprovalRequest.attendance_id.in_(attendance_ids))

    logout_approval_requests = LogoutApprovalRequest.query.filter(
        db.or_(*logout_approval_filters)
    ).all()
    for request_row in logout_approval_requests:
        db.session.delete(request_row)

    attendance_activities = AttendanceActivity.query.filter_by(employee_id=id).all()
    for activity in attendance_activities:
        db.session.delete(activity)

    login_creds = EmployeeLogin.query.filter_by(employee_id=id).first()
    if login_creds:
        db.session.delete(login_creds)

    attendance_records = Attendance.query.filter_by(employee_id=id).all()
    for record in attendance_records:
        db.session.delete(record)

    payroll_records = Payroll.query.filter_by(employee_id=id).all()
    for record in payroll_records:
        db.session.delete(record)

    db.session.flush()

    if employee.profile_photo:
        try:
            profile_photo_path = os.path.join(current_app.config['UPLOAD_FOLDER'], employee.profile_photo.split('/')[-1])
            if os.path.exists(profile_photo_path):
                os.remove(profile_photo_path)
        except Exception:
            pass

    dataset_folder = os.path.join(current_app.root_path, 'dataset', str(id))
    if os.path.exists(dataset_folder):
        try:
            shutil.rmtree(dataset_folder)
        except Exception:
            pass

    db.session.delete(employee)
    db.session.commit()

    flash('Employee deleted successfully', 'success')
    return redirect(url_for('employees.employees'))


@employees_bp.route('/employees/<int:id>')
@login_required
@admin_required
def view_employee(id):
    employee = Employee.query.get_or_404(id)
    attendance = Attendance.query.filter_by(employee_id=id).order_by(Attendance.date.desc()).limit(30).all()
    all_employees = Employee.query.filter_by(status='active').order_by(Employee.created_at.desc()).all()

    settings = Settings.get_settings()
    min_face_images = settings.min_face_images_required if settings else 20

    dataset_folder = os.path.join(current_app.root_path, 'dataset')
    employee_image_counts = _employee_image_counts(dataset_folder, all_employees)

    emp_folder = os.path.join(dataset_folder, str(employee.id))
    face_images = []
    current_count = 0
    if os.path.exists(emp_folder):
        image_files = [f for f in os.listdir(emp_folder) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
        image_files.sort()
        current_count = len(image_files)
        face_images = image_files

    return render_template('add_employee.html',
                         employee=employee,
                         attendance=attendance,
                         employees=all_employees,
                         view_mode=True,
                         face_images=face_images,
                         current_face_images=current_count,
                         min_face_images=min_face_images,
                         employee_image_counts=employee_image_counts)
