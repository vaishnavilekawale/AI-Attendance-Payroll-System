from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from config import Config
import os

db = SQLAlchemy()
migrate = Migrate()

def init_db(app):
    db.init_app(app)
    migrate.init_app(app, db)
    
    with app.app_context():
        db.create_all()
        
        # Add new columns if they don't exist (for existing databases)
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        
        # Check and add columns for admins table
        admin_columns = [col['name'] for col in inspector.get_columns('admins')]
        if 'force_password_change' not in admin_columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE admins ADD COLUMN force_password_change BOOLEAN DEFAULT FALSE"))
                conn.commit()
        if 'temporary_password_hash' not in admin_columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE admins ADD COLUMN temporary_password_hash VARCHAR(255)"))
                conn.commit()
        if 'temporary_password_created_at' not in admin_columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE admins ADD COLUMN temporary_password_created_at DATETIME"))
                conn.commit()
        
        # Check and add columns for employees table
        columns = [col['name'] for col in inspector.get_columns('employees')]
        
        # Check if new columns exist, if not add them
        if 'username' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN username VARCHAR(80)"))
                conn.commit()
        
        if 'password_hash' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN password_hash VARCHAR(255)"))
                conn.commit()
        
        if 'role' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN role VARCHAR(20) DEFAULT 'employee'"))
                conn.commit()
        
        if 'must_change_password' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN must_change_password BOOLEAN DEFAULT 1"))
                conn.commit()
        
        if 'last_login' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN last_login DATETIME"))
                conn.commit()
        
        # Add new payroll-related columns to employees table
        if 'pan_number' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN pan_number VARCHAR(20)"))
                conn.commit()
        
        if 'uan_number' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN uan_number VARCHAR(20)"))
                conn.commit()
        
        if 'pf_number' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN pf_number VARCHAR(20)"))
                conn.commit()
        
        if 'hra' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN hra FLOAT DEFAULT 0.0"))
                conn.commit()
        
        if 'da' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN da FLOAT DEFAULT 0.0"))
                conn.commit()
        
        if 'medical_allowance' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN medical_allowance FLOAT DEFAULT 0.0"))
                conn.commit()
        
        if 'travel_allowance' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN travel_allowance FLOAT DEFAULT 0.0"))
                conn.commit()
        
        if 'special_allowance' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN special_allowance FLOAT DEFAULT 0.0"))
                conn.commit()
        
        if 'other_allowances' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN other_allowances FLOAT DEFAULT 0.0"))
                conn.commit()
        
        if 'employee_pf_percentage' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN employee_pf_percentage FLOAT DEFAULT 12.0"))
                conn.commit()
        
        if 'esic_percentage' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN esic_percentage FLOAT DEFAULT 0.75"))
                conn.commit()
        
        if 'tds_percentage' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN tds_percentage FLOAT DEFAULT 0.0"))
                conn.commit()

        if 'employer_pf_percentage' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN employer_pf_percentage FLOAT DEFAULT 12.0"))
                conn.commit()

        if 'bus_charges' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN bus_charges FLOAT DEFAULT 0.0"))
                conn.commit()

        if 'other_deduction' not in columns:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE employees ADD COLUMN other_deduction FLOAT DEFAULT 0.0"))
                conn.commit()
        
        # Add new columns to payroll table
        try:
            payroll_columns = [col['name'] for col in inspector.get_columns('payroll')]
            if 'professional_tax' not in payroll_columns:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE payroll ADD COLUMN professional_tax FLOAT DEFAULT 0.0"))
                    conn.commit()
            if 'employee_pf' not in payroll_columns:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE payroll ADD COLUMN employee_pf FLOAT DEFAULT 0.0"))
                    conn.commit()
            if 'esic' not in payroll_columns:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE payroll ADD COLUMN esic FLOAT DEFAULT 0.0"))
                    conn.commit()
            if 'tds' not in payroll_columns:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE payroll ADD COLUMN tds FLOAT DEFAULT 0.0"))
                    conn.commit()
            if 'other_deduction' not in payroll_columns:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE payroll ADD COLUMN other_deduction FLOAT DEFAULT 0.0"))
                    conn.commit()
            if 'total_deductions' not in payroll_columns:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE payroll ADD COLUMN total_deductions FLOAT DEFAULT 0.0"))
                    conn.commit()
            if 'paid_days' not in payroll_columns:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE payroll ADD COLUMN paid_days FLOAT DEFAULT 0.0"))
                    conn.commit()
            if 'lop_days' not in payroll_columns:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE payroll ADD COLUMN lop_days FLOAT DEFAULT 0.0"))
                    conn.commit()
            allowance_columns = {
                'hra': 'hra FLOAT DEFAULT 0.0',
                'da': 'da FLOAT DEFAULT 0.0',
                'medical_allowance': 'medical_allowance FLOAT DEFAULT 0.0',
                'travel_allowance': 'travel_allowance FLOAT DEFAULT 0.0',
                'special_allowance': 'special_allowance FLOAT DEFAULT 0.0',
                'other_allowances': 'other_allowances FLOAT DEFAULT 0.0',
            }
            for col_name, col_def in allowance_columns.items():
                if col_name not in payroll_columns:
                    with db.engine.connect() as conn:
                        conn.execute(db.text(f"ALTER TABLE payroll ADD COLUMN {col_def}"))
                        conn.commit()
            extra_payroll_columns = {
                'lop_deduction': 'lop_deduction FLOAT DEFAULT 0.0',
                'bus_charges': 'bus_charges FLOAT DEFAULT 0.0',
                'employer_pf': 'employer_pf FLOAT DEFAULT 0.0',
                'net_ctc': 'net_ctc FLOAT DEFAULT 0.0',
            }
            for col_name, col_def in extra_payroll_columns.items():
                if col_name not in payroll_columns:
                    with db.engine.connect() as conn:
                        conn.execute(db.text(f"ALTER TABLE payroll ADD COLUMN {col_def}"))
                        conn.commit()
            # Pro-rata reference columns: the employee's original/full
            # monthly basic + allowances at the time payroll was calculated,
            # persisted alongside the already-prorated basic_salary/hra/etc.
            # fields above so the payslip can show "earned" vs "full
            # monthly" for periods with fewer working days than a full
            # month (see compute_payroll_amounts in payroll.py).
            prorata_payroll_columns = {
                'proration_factor': 'proration_factor FLOAT DEFAULT 1.0',
                'full_basic_salary': 'full_basic_salary FLOAT DEFAULT 0.0',
                'full_hra': 'full_hra FLOAT DEFAULT 0.0',
                'full_da': 'full_da FLOAT DEFAULT 0.0',
                'full_medical_allowance': 'full_medical_allowance FLOAT DEFAULT 0.0',
                'full_travel_allowance': 'full_travel_allowance FLOAT DEFAULT 0.0',
                'full_special_allowance': 'full_special_allowance FLOAT DEFAULT 0.0',
                'full_other_allowances': 'full_other_allowances FLOAT DEFAULT 0.0',
            }
            for col_name, col_def in prorata_payroll_columns.items():
                if col_name not in payroll_columns:
                    with db.engine.connect() as conn:
                        conn.execute(db.text(f"ALTER TABLE payroll ADD COLUMN {col_def}"))
                        conn.commit()
        except Exception as e:
            print(f"Note: payroll table may not exist yet: {e}")
        
        # Check and add columns for employee_login table
        try:
            employee_login_columns = [col['name'] for col in inspector.get_columns('employee_login')]
            if 'temporary_password_hash' not in employee_login_columns:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE employee_login ADD COLUMN temporary_password_hash VARCHAR(255)"))
                    conn.commit()
            if 'temporary_password_created_at' not in employee_login_columns:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE employee_login ADD COLUMN temporary_password_created_at DATETIME"))
                    conn.commit()
        except Exception as e:
            print(f"Note: employee_login table may not exist yet: {e}")
        
        # Create default admin user if not exists
        from models import Admin, Employee
        admin = Admin.query.filter_by(username='admin').first()
        if not admin:
            from werkzeug.security import generate_password_hash
            admin = Admin(
                username='admin',
                password_hash=generate_password_hash('admin123'),
                email='admin@company.com'
            )
            db.session.add(admin)
            db.session.commit()
            print("Default admin user created: username=admin, password=admin123")
        
        # ------------------------------------------------------------------
        # Create EmployeeLogin records for existing employees
        # ------------------------------------------------------------------
        from models import Employee, EmployeeLogin

        employees = Employee.query.all()
        created = 0

        for employee in employees:

            login = EmployeeLogin.query.filter_by(employee_id=employee.id).first()

            # Already exists -> DON'T touch password
            if login:
                continue

            default_password = employee.phone if employee.phone else employee.employee_id

            login = EmployeeLogin(
                employee_id=employee.id,
                username=employee.employee_id,
                first_login=True,
                force_password_change=True,
                is_active=True
            )

            login.set_password(default_password)

            db.session.add(login)
            created += 1

        if created:
            db.session.commit()
            print(f"Created {created} EmployeeLogin accounts")
        else:
            print("EmployeeLogin accounts already exist.")

            
# from flask_sqlalchemy import SQLAlchemy
# from flask_migrate import Migrate
# from config import Config
# import os

# db = SQLAlchemy()
# migrate = Migrate()

# def init_db(app):
#     db.init_app(app)
#     migrate.init_app(app, db)
    
#     with app.app_context():
#         db.create_all()
        
#         # Add new columns if they don't exist (for existing databases)
#         from sqlalchemy import inspect
#         inspector = inspect(db.engine)
        
#         # Check and add columns for admins table
#         admin_columns = [col['name'] for col in inspector.get_columns('admins')]
#         if 'force_password_change' not in admin_columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE admins ADD COLUMN force_password_change BOOLEAN DEFAULT FALSE"))
#                 conn.commit()
#         if 'temporary_password_hash' not in admin_columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE admins ADD COLUMN temporary_password_hash VARCHAR(255)"))
#                 conn.commit()
#         if 'temporary_password_created_at' not in admin_columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE admins ADD COLUMN temporary_password_created_at DATETIME"))
#                 conn.commit()
        
#         # Check and add columns for employees table
#         columns = [col['name'] for col in inspector.get_columns('employees')]
        
#         # Check if new columns exist, if not add them
#         if 'username' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN username VARCHAR(80)"))
#                 conn.commit()
        
#         if 'password_hash' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN password_hash VARCHAR(255)"))
#                 conn.commit()
        
#         if 'role' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN role VARCHAR(20) DEFAULT 'employee'"))
#                 conn.commit()
        
#         if 'must_change_password' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN must_change_password BOOLEAN DEFAULT 1"))
#                 conn.commit()
        
#         if 'last_login' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN last_login DATETIME"))
#                 conn.commit()
        
#         # Add new payroll-related columns to employees table
#         if 'pan_number' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN pan_number VARCHAR(20)"))
#                 conn.commit()
        
#         if 'uan_number' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN uan_number VARCHAR(20)"))
#                 conn.commit()
        
#         if 'pf_number' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN pf_number VARCHAR(20)"))
#                 conn.commit()
        
#         if 'hra' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN hra FLOAT DEFAULT 0.0"))
#                 conn.commit()
        
#         if 'da' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN da FLOAT DEFAULT 0.0"))
#                 conn.commit()
        
#         if 'medical_allowance' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN medical_allowance FLOAT DEFAULT 0.0"))
#                 conn.commit()
        
#         if 'travel_allowance' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN travel_allowance FLOAT DEFAULT 0.0"))
#                 conn.commit()
        
#         if 'special_allowance' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN special_allowance FLOAT DEFAULT 0.0"))
#                 conn.commit()
        
#         if 'other_allowances' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN other_allowances FLOAT DEFAULT 0.0"))
#                 conn.commit()
        
#         if 'employee_pf_percentage' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN employee_pf_percentage FLOAT DEFAULT 12.0"))
#                 conn.commit()
        
#         if 'esic_percentage' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN esic_percentage FLOAT DEFAULT 0.75"))
#                 conn.commit()
        
#         if 'tds_percentage' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN tds_percentage FLOAT DEFAULT 0.0"))
#                 conn.commit()

#         if 'employer_pf_percentage' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN employer_pf_percentage FLOAT DEFAULT 12.0"))
#                 conn.commit()

#         if 'bus_charges' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN bus_charges FLOAT DEFAULT 0.0"))
#                 conn.commit()

#         if 'other_deduction' not in columns:
#             with db.engine.connect() as conn:
#                 conn.execute(db.text("ALTER TABLE employees ADD COLUMN other_deduction FLOAT DEFAULT 0.0"))
#                 conn.commit()
        
#         # Add new columns to payroll table
#         try:
#             payroll_columns = [col['name'] for col in inspector.get_columns('payroll')]
#             if 'professional_tax' not in payroll_columns:
#                 with db.engine.connect() as conn:
#                     conn.execute(db.text("ALTER TABLE payroll ADD COLUMN professional_tax FLOAT DEFAULT 0.0"))
#                     conn.commit()
#             if 'employee_pf' not in payroll_columns:
#                 with db.engine.connect() as conn:
#                     conn.execute(db.text("ALTER TABLE payroll ADD COLUMN employee_pf FLOAT DEFAULT 0.0"))
#                     conn.commit()
#             if 'esic' not in payroll_columns:
#                 with db.engine.connect() as conn:
#                     conn.execute(db.text("ALTER TABLE payroll ADD COLUMN esic FLOAT DEFAULT 0.0"))
#                     conn.commit()
#             if 'tds' not in payroll_columns:
#                 with db.engine.connect() as conn:
#                     conn.execute(db.text("ALTER TABLE payroll ADD COLUMN tds FLOAT DEFAULT 0.0"))
#                     conn.commit()
#             if 'other_deduction' not in payroll_columns:
#                 with db.engine.connect() as conn:
#                     conn.execute(db.text("ALTER TABLE payroll ADD COLUMN other_deduction FLOAT DEFAULT 0.0"))
#                     conn.commit()
#             if 'total_deductions' not in payroll_columns:
#                 with db.engine.connect() as conn:
#                     conn.execute(db.text("ALTER TABLE payroll ADD COLUMN total_deductions FLOAT DEFAULT 0.0"))
#                     conn.commit()
#             if 'paid_days' not in payroll_columns:
#                 with db.engine.connect() as conn:
#                     conn.execute(db.text("ALTER TABLE payroll ADD COLUMN paid_days FLOAT DEFAULT 0.0"))
#                     conn.commit()
#             if 'lop_days' not in payroll_columns:
#                 with db.engine.connect() as conn:
#                     conn.execute(db.text("ALTER TABLE payroll ADD COLUMN lop_days FLOAT DEFAULT 0.0"))
#                     conn.commit()
#             allowance_columns = {
#                 'hra': 'hra FLOAT DEFAULT 0.0',
#                 'da': 'da FLOAT DEFAULT 0.0',
#                 'medical_allowance': 'medical_allowance FLOAT DEFAULT 0.0',
#                 'travel_allowance': 'travel_allowance FLOAT DEFAULT 0.0',
#                 'special_allowance': 'special_allowance FLOAT DEFAULT 0.0',
#                 'other_allowances': 'other_allowances FLOAT DEFAULT 0.0',
#             }
#             for col_name, col_def in allowance_columns.items():
#                 if col_name not in payroll_columns:
#                     with db.engine.connect() as conn:
#                         conn.execute(db.text(f"ALTER TABLE payroll ADD COLUMN {col_def}"))
#                         conn.commit()
#             extra_payroll_columns = {
#                 'lop_deduction': 'lop_deduction FLOAT DEFAULT 0.0',
#                 'bus_charges': 'bus_charges FLOAT DEFAULT 0.0',
#                 'employer_pf': 'employer_pf FLOAT DEFAULT 0.0',
#                 'net_ctc': 'net_ctc FLOAT DEFAULT 0.0',
#             }
#             for col_name, col_def in extra_payroll_columns.items():
#                 if col_name not in payroll_columns:
#                     with db.engine.connect() as conn:
#                         conn.execute(db.text(f"ALTER TABLE payroll ADD COLUMN {col_def}"))
#                         conn.commit()
#         except Exception as e:
#             print(f"Note: payroll table may not exist yet: {e}")
        
#         # Check and add columns for employee_login table
#         try:
#             employee_login_columns = [col['name'] for col in inspector.get_columns('employee_login')]
#             if 'temporary_password_hash' not in employee_login_columns:
#                 with db.engine.connect() as conn:
#                     conn.execute(db.text("ALTER TABLE employee_login ADD COLUMN temporary_password_hash VARCHAR(255)"))
#                     conn.commit()
#             if 'temporary_password_created_at' not in employee_login_columns:
#                 with db.engine.connect() as conn:
#                     conn.execute(db.text("ALTER TABLE employee_login ADD COLUMN temporary_password_created_at DATETIME"))
#                     conn.commit()
#         except Exception as e:
#             print(f"Note: employee_login table may not exist yet: {e}")
        
#         # Create default admin user if not exists
#         from models import Admin, Employee
#         admin = Admin.query.filter_by(username='admin').first()
#         if not admin:
#             from werkzeug.security import generate_password_hash
#             admin = Admin(
#                 username='admin',
#                 password_hash=generate_password_hash('admin123'),
#                 email='admin@company.com'
#             )
#             db.session.add(admin)
#             db.session.commit()
#             print("Default admin user created: username=admin, password=admin123")
        
#         # ------------------------------------------------------------------
#         # Create EmployeeLogin records for existing employees
#         # ------------------------------------------------------------------
#         from models import Employee, EmployeeLogin

#         employees = Employee.query.all()
#         created = 0

#         for employee in employees:

#             login = EmployeeLogin.query.filter_by(employee_id=employee.id).first()

#             # Already exists -> DON'T touch password
#             if login:
#                 continue

#             default_password = employee.phone if employee.phone else employee.employee_id

#             login = EmployeeLogin(
#                 employee_id=employee.id,
#                 username=employee.employee_id,
#                 first_login=True,
#                 force_password_change=True,
#                 is_active=True
#             )

#             login.set_password(default_password)

#             db.session.add(login)
#             created += 1

#         if created:
#             db.session.commit()
#             print(f"Created {created} EmployeeLogin accounts")
#         else:
#             print("EmployeeLogin accounts already exist.")
