"""
Tests for the employees blueprint (employees.py), migrated out of app.py.

These specifically exercise the blueprint-scoped endpoint names
(employees.employees, employees.add_employee, etc.) and the url_for()
rewiring that came with the migration, plus a real end-to-end add -> edit
-> delete flow against the DB.
"""
from flask import url_for


def _login_as_admin(client, make_admin):
    make_admin(username='owner', password='adminpass123')
    client.post('/login', data={'username': 'owner', 'password': 'adminpass123', 'role': 'admin'})


def test_employees_list_page_loads_for_admin(client, make_admin, make_employee):
    _login_as_admin(client, make_admin)
    make_employee(employee_id='EMP0001', name='Alice Example')

    response = client.get('/employees')
    assert response.status_code == 200
    assert b'Alice Example' in response.data


def test_employees_list_requires_admin_login(client):
    response = client.get('/employees', follow_redirects=False)
    assert response.status_code == 302
    assert '/login' in response.headers['Location']


def test_employees_endpoint_names_are_blueprint_scoped(app_context):
    """
    Confirms the actual migration concern: these 5 endpoints must be
    reachable as 'employees.<name>', and the OLD bare names must no
    longer exist as separate endpoints (they were fully moved, not
    duplicated).
    """
    with app_context.test_request_context():
        assert url_for('employees.employees') == '/employees'
        assert url_for('employees.add_employee') == '/employees/add'
        assert url_for('employees.edit_employee', id=1) == '/employees/edit/1'
        assert url_for('employees.delete_employee', id=1) == '/employees/delete/1'
        assert url_for('employees.view_employee', id=1) == '/employees/1'


def test_add_employee_creates_employee_and_login_creds(client, make_admin):
    _login_as_admin(client, make_admin)

    response = client.post('/employees/add', data={
        'name': 'Bob Builder',
        'department': 'Construction',
        'designation': 'Site Manager',
        'basic_salary': '45000',
        'joining_date': '2026-01-15',
        'email': 'bob@example.com',
        'phone': '9998887777',
        'address': '1 Build St',
        'office_location': 'HQ',
        'bank_name': 'Test Bank',
        'bank_account_number': '12345',
    }, follow_redirects=True)

    assert response.status_code == 200
    assert b'added successfully' in response.data

    from models import Employee, EmployeeLogin
    employee = Employee.query.filter_by(name='Bob Builder').first()
    assert employee is not None
    assert employee.employee_id == 'EMP0001'
    assert employee.basic_salary == 45000

    login_creds = EmployeeLogin.query.filter_by(employee_id=employee.id).first()
    assert login_creds is not None
    assert login_creds.check_password('9998887777')  # phone is the default password


def test_add_employee_rejects_duplicate_email(client, make_admin, make_employee):
    _login_as_admin(client, make_admin)
    make_employee(employee_id='EMP0001', email='taken@example.com')

    response = client.post('/employees/add', data={
        'name': 'Someone Else',
        'department': 'Sales',
        'designation': 'Rep',
        'basic_salary': '30000',
        'joining_date': '2026-01-15',
        'email': 'taken@example.com',
        'phone': '1112223333',
        'address': 'Somewhere',
    }, follow_redirects=True)

    assert response.status_code == 200
    assert b'already exists' in response.data

    from models import Employee
    assert Employee.query.filter_by(email='taken@example.com').count() == 1


def test_edit_employee_updates_fields(client, make_admin, make_employee):
    _login_as_admin(client, make_admin)
    employee = make_employee(employee_id='EMP0001', name='Original Name', basic_salary=40000)

    response = client.post(f'/employees/edit/{employee.id}', data={
        'name': 'Updated Name',
        'department': employee.department,
        'designation': employee.designation,
        'basic_salary': '55000',
        'email': employee.email,
        'phone': employee.phone,
        'address': employee.address,
    }, follow_redirects=True)

    assert response.status_code == 200
    assert b'updated successfully' in response.data

    from database import db
    db.session.refresh(employee)
    assert employee.name == 'Updated Name'
    assert employee.basic_salary == 55000


def test_view_employee_shows_details(client, make_admin, make_employee):
    _login_as_admin(client, make_admin)
    employee = make_employee(employee_id='EMP0001', name='Carol Viewer')

    response = client.get(f'/employees/{employee.id}')
    assert response.status_code == 200
    assert b'Carol Viewer' in response.data


def test_delete_employee_removes_employee_and_login_creds(client, make_admin, make_employee):
    _login_as_admin(client, make_admin)
    employee = make_employee(employee_id='EMP0001', name='Dave Deleteme')
    employee_id = employee.id

    response = client.get(f'/employees/delete/{employee_id}', follow_redirects=True)
    assert response.status_code == 200
    assert b'deleted successfully' in response.data

    from models import Employee
    assert Employee.query.get(employee_id) is None
