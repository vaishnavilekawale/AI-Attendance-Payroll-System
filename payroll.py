from datetime import date, timedelta
from models import Payroll, Employee, Settings, PayrollSettings
from database import db
from calendar import monthrange
from services.attendance_stats import calculate_employee_attendance_stats
import logging

logger = logging.getLogger(__name__)

# Payroll model columns excluded from payslip earnings/deduction display.
PAYROLL_META_FIELDS = frozenset({
    'id', 'employee_id', 'month', 'year',
    'payslip_generated', 'payslip_path', 'email_sent',
    'created_at', 'updated_at',
})

# Ordered payslip display mappings: (model_field, label).
PAYSLIP_EARNINGS_FIELDS = (
    ('basic_salary', 'Basic Pay'),
    ('hra', 'House Rent Allowance (HRA)'),
    ('da', 'Dearness Allowance (DA)'),
    ('medical_allowance', 'Medical Allowance'),
    ('special_allowance', 'Special Allowance'),
    ('other_allowances', 'Other Allowances'),
)

PAYSLIP_DEDUCTION_FIELDS = (
    ('absent_deduction', 'LOP / Absent Deduction'),
    ('half_day_deduction', 'Half Day Deduction'),
    ('late_deduction', 'Late Deduction'),
    ('employee_pf', 'Employee PF Deduction'),
    ('esic', 'ESIC Deduction'),
    ('professional_tax', 'Professional Tax (PT)'),
    ('tds', 'TDS (Income Tax Deduction)'),
    ('bus_charges', 'Bus Charges / Transport'),
    ('other_deduction', 'Other Deductions'),
)

PAYSLIP_ATTENDANCE_FIELDS = (
    ('working_days', 'Total Working Days'),
    ('present_days', 'Present Days'),
    ('half_days', 'Half Days'),
    ('absent_days', 'Absent Days'),
    ('late_days', 'Late Days'),
    ('paid_days', 'Paid Days'),
    ('lop_days', 'LOP Days'),
)


def is_payroll_eligible(employee, month, year):
    """
    Check if an employee is eligible for payroll for a given month/year.
    Payroll eligibility is based on the employee's joining date.
    """
    if not employee.joining_date:
        return True

    return (
        year > employee.joining_date.year
        or (
            year == employee.joining_date.year
            and month >= employee.joining_date.month
        )
    )


def round_money(amount):
    """Round monetary values to 2 decimal places."""
    return round(float(amount or 0.0), 2)


def round_days(days):
    """Round day counts that may include half-day fractions."""
    return round(float(days or 0.0), 1)


def compute_paid_and_lop_days(present_days, absent_days, half_days):
    """Derive paid and LOP day counts from attendance breakdown."""
    paid_days = round_days(present_days + (half_days * 0.5))
    lop_days = round_days(absent_days + (half_days * 0.5))
    return paid_days, lop_days


def convert_late_marks_to_half_days(late_days):
    """
    Payslip-only rule: for every 3 late marks accumulated in the pay period,
    convert them into 1 half-day.

    1-2 late marks -> 0 converted half-days (no effect)
    3-5 late marks -> 1 converted half-day
    6-8 late marks -> 2 converted half-days
    ... every additional multiple of 3 adds one more (e.g. 21 -> 7)

    This is deliberately a plain integer floor-division (late_days // 3) and
    is applied ONLY when building the Payroll/payslip record in
    compute_payroll_amounts below. It must NEVER be applied to the raw
    attendance figures used by admin/employee reports
    (services/attendance_stats.py, services/admin_reports_service.py) -
    those keep showing the true, unconverted late-day count.
    """
    return int(late_days or 0) // 3


def get_payroll_field_value(payroll, field_name, default=0.0):
    """Read a numeric payroll field directly from the persisted database record."""
    return float(getattr(payroll, field_name, default) or default)


def build_payslip_attendance_rows(payroll):
    """Build attendance summary rows from saved Payroll values."""
    rows = []
    for field_name, label in PAYSLIP_ATTENDANCE_FIELDS:
        value = get_payroll_field_value(payroll, field_name)
        if field_name in ('paid_days', 'lop_days'):
            display = str(int(value)) if value == int(value) else f"{value:.1f}"
        else:
            display = str(int(value))
        rows.append((label, display))
    return rows


def build_payslip_earnings_rows(payroll):
    """Build earnings rows from saved Payroll values."""
    rows = []
    for field_name, label in PAYSLIP_EARNINGS_FIELDS:
        amount = round_money(get_payroll_field_value(payroll, field_name))
        rows.append((label, amount))

    travel_allowance = round_money(get_payroll_field_value(payroll, 'travel_allowance'))
    if travel_allowance:
        rows.append(('Travel Allowance', travel_allowance))

    overtime_bonus = round_money(get_payroll_field_value(payroll, 'overtime_bonus'))
    if overtime_bonus:
        rows.append(('Overtime Bonus', overtime_bonus))

    # FIX 1: Total Gross Earnings reflects basic + allowances + overtime
    rows.append(('Total Gross Earnings', round_money(get_payroll_field_value(payroll, 'gross_salary'))))
    return rows


def build_payslip_deduction_rows(payroll):
    """Build deduction rows from saved Payroll values."""
    rows = []
    for field_name, label in PAYSLIP_DEDUCTION_FIELDS:
        amount = round_money(get_payroll_field_value(payroll, field_name))
        rows.append((label, amount))

    rows.append(('Total Deductions', round_money(get_payroll_field_value(payroll, 'total_deductions'))))
    return rows


def get_month_total_days(year, month):
    """Get exact number of calendar days in a specific month."""
    return monthrange(year, month)[1]


def compute_payroll_amounts(
    employee,
    working_days,
    present_days,
    absent_days,
    half_days,
    late_days,
    total_hours_worked,
    overtime_hours,
    month,
    year,
):
    """
    Compute all payroll earnings, deductions, and summary fields for one employee.
    Returns a dict ready to persist on the Payroll model.
    """
    settings = Settings.get_settings()
    payroll_settings = PayrollSettings.get_settings()

    # ------------------------------------------------------------------
    # PAYSLIP-ONLY: Late Mark -> Half-Day conversion rule.
    # For every 3 late marks in the pay period, add 1 half-day to the
    # figure used on the payslip (attendance summary, Paid/LOP Days, and
    # Half Day Deduction). The ORIGINAL `half_days` (from actual
    # half-day attendance) and `late_days` (raw late count) are kept
    # completely unchanged everywhere else - this conversion only feeds
    # into payslip_half_days below, which is what gets persisted to the
    # Payroll table (and therefore shown on payroll.html,
    # employee_payroll.html and the PDF payslip). Admin/Employee Reports
    # pull attendance directly from attendance_stats and never see this
    # converted value.
    # ------------------------------------------------------------------
    late_mark_half_days = convert_late_marks_to_half_days(late_days)
    payslip_half_days = round_days(half_days + late_mark_half_days)

    paid_days, lop_days = compute_paid_and_lop_days(present_days, absent_days, payslip_half_days)

    basic_salary = round_money(employee.basic_salary)

    # FIX 2: Per Day Salary considers total working evaluation days correctly
    total_eval_days = working_days if working_days > 0 else get_month_total_days(year, month)
    per_day_salary = round_money(basic_salary / total_eval_days) if total_eval_days > 0 else 0.0

    # LOP Deductions - only applied if enabled in settings
    absent_deduction = 0.0
    if settings.absent_deduction_enabled:
        if settings.absent_deduction_per_occurrence > 0:
            absent_deduction = round_money(absent_days * settings.absent_deduction_per_occurrence)
        else:
            absent_deduction = round_money(absent_days * per_day_salary)

    # Half Day Deduction is recalculated against payslip_half_days, which
    # includes the late-mark-converted half-days on top of the actual
    # half-day attendance count.
    half_day_deduction = 0.0
    if settings.half_day_deduction_enabled:
        if settings.half_day_deduction_per_occurrence > 0:
            half_day_deduction = round_money(payslip_half_days * settings.half_day_deduction_per_occurrence)
        else:
            half_day_deduction = round_money(payslip_half_days * (per_day_salary / 2))

    lop_deduction = round_money(absent_deduction + half_day_deduction)

    # Late Deduction
    late_deduction = 0.0
    if settings.late_deduction_enabled:
        late_deduction = round_money(late_days * settings.late_deduction_per_occurrence)

    # Overtime Bonus
    overtime_bonus = 0.0
    if settings.overtime_enabled and settings.working_hours_per_day > 0:
        hourly_rate = per_day_salary / settings.working_hours_per_day
        overtime_bonus = round_money(
            overtime_hours * hourly_rate * settings.overtime_rate
        )

    # Allowances
    hra = round_money(getattr(employee, 'hra', 0.0))
    da = round_money(getattr(employee, 'da', 0.0))
    medical_allowance = round_money(getattr(employee, 'medical_allowance', 0.0))
    travel_allowance = round_money(getattr(employee, 'travel_allowance', 0.0))
    special_allowance = round_money(getattr(employee, 'special_allowance', 0.0))
    other_allowances = round_money(getattr(employee, 'other_allowances', 0.0))

    base_gross_salary = round_money(
        basic_salary
        + hra
        + da
        + medical_allowance
        + travel_allowance
        + special_allowance
        + other_allowances
    )

    # FIX 3: Gross Salary explicitly includes Overtime Bonus
    gross_salary = round_money(base_gross_salary + overtime_bonus)

    # Calculate Earned Basic and Earned Gross for statutory deductions
    earned_basic = max(0.0, round_money(basic_salary - lop_deduction))
    earned_gross = max(0.0, round_money(gross_salary - lop_deduction))

    professional_tax = round_money(payroll_settings.get_professional_tax(month))

    # FIX 4: Statutory Deductions calculated on Earned Wages (Post LOP)
    employee_pf_percentage = getattr(employee, 'employee_pf_percentage', 12.0) or 0.0
    employee_pf = round_money((earned_basic * employee_pf_percentage) / 100)

    employer_pf_percentage = getattr(employee, 'employer_pf_percentage', 12.0) or 0.0
    employer_pf = round_money((earned_basic * employer_pf_percentage) / 100)

    esic_percentage = getattr(employee, 'esic_percentage', 0.75) or 0.0
    esic = round_money((earned_gross * esic_percentage) / 100)

    tds_percentage = getattr(employee, 'tds_percentage', 0.0) or 0.0
    tds = round_money((earned_gross * tds_percentage) / 100)

    bus_charges = round_money(getattr(employee, 'bus_charges', 0.0))
    other_deduction = round_money(getattr(employee, 'other_deduction', 0.0))

    total_deductions = round_money(
        absent_deduction
        + half_day_deduction
        + late_deduction
        + professional_tax
        + employee_pf
        + esic
        + tds
        + bus_charges
        + other_deduction
    )

    # Final Net Calculations
    net_salary = max(0.0, round_money(gross_salary - total_deductions))
    net_ctc = round_money(gross_salary + employer_pf)

    return {
        'basic_salary': basic_salary,
        'working_days': working_days,
        'present_days': present_days,
        'absent_days': absent_days,
        # Half Days shown on the payslip includes the late-mark-converted
        # half-days (see convert_late_marks_to_half_days above). This is a
        # payslip-only figure - actual half-day attendance (`half_days`
        # from attendance_stats) is untouched everywhere else.
        'half_days': payslip_half_days,
        'late_days': late_days,
        'paid_days': paid_days,
        'lop_days': lop_days,
        'total_hours_worked': round_money(total_hours_worked),
        'overtime_hours': round_money(overtime_hours),
        'per_day_salary': per_day_salary,
        'absent_deduction': absent_deduction,
        'lop_deduction': lop_deduction,
        'half_day_deduction': half_day_deduction,
        'late_deduction': late_deduction,
        'overtime_bonus': overtime_bonus,
        'hra': hra,
        'da': da,
        'medical_allowance': medical_allowance,
        'travel_allowance': travel_allowance,
        'special_allowance': special_allowance,
        'other_allowances': other_allowances,
        'professional_tax': professional_tax,
        'employee_pf': employee_pf,
        'employer_pf': employer_pf,
        'esic': esic,
        'tds': tds,
        'bus_charges': bus_charges,
        'other_deduction': other_deduction,
        'total_deductions': total_deductions,
        'gross_salary': gross_salary,
        'net_salary': net_salary,
        'net_ctc': net_ctc,
    }


class PayrollCalculator:
    def __init__(self):
        self._attendance_manager = None

    @property
    def attendance_manager(self):
        if self._attendance_manager is None:
            from attendance import AttendanceManager
            self._attendance_manager = AttendanceManager()
        return self._attendance_manager

    def calculate_monthly_payroll(self, year, month, employee_id=None):
        """Calculate payroll for a specific month and persist all fields to the database."""
        start_date = date(year, month, 1)
        last_day = monthrange(year, month)[1]

        today = date.today()
        if year == today.year and month == today.month:
            end_date = today - timedelta(days=1)
        else:
            end_date = date(year, month, last_day)

        if end_date < start_date:
            logger.warning(
                "Payroll evaluation end date %s is before start date %s for %s/%s",
                end_date,
                start_date,
                month,
                year,
            )
            return []

        if employee_id:
            employees = [Employee.query.get(employee_id)]
        else:
            employees = Employee.query.filter_by(status='active').all()

        payroll_records = []

        for employee in employees:
            if not employee:
                continue

            if not is_payroll_eligible(employee, month, year):
                logger.info(
                    "Skipping payroll for employee %s (%s) - not eligible for %s/%s",
                    employee.id,
                    employee.name,
                    month,
                    year,
                )
                continue

            effective_start_date = (
                max(start_date, employee.joining_date)
                if employee.joining_date
                else start_date
            )

            if effective_start_date > end_date:
                logger.info(
                    "Skipping payroll for employee %s (%s) - joined after evaluation period",
                    employee.id,
                    employee.name,
                )
                continue

            working_days = self._get_working_days(effective_start_date, end_date)

            attendance_stats = calculate_employee_attendance_stats(
                self.attendance_manager,
                employee.id,
                effective_start_date,
                end_date,
            )

            present_days = attendance_stats['present_days']
            absent_days = attendance_stats['absent_days']
            half_days = attendance_stats['half_days']
            late_days = attendance_stats['late_days']

            evaluated_days = present_days + absent_days + half_days
            if evaluated_days != working_days:
                logger.warning(
                    "Attendance breakdown mismatch for employee %s (%s): "
                    "present(%s) + absent(%s) + half(%s) = %s, expected %s working days",
                    employee.id,
                    employee.name,
                    present_days,
                    absent_days,
                    half_days,
                    evaluated_days,
                    working_days,
                )

            payroll_fields = compute_payroll_amounts(
                employee=employee,
                working_days=working_days,
                present_days=present_days,
                absent_days=absent_days,
                half_days=half_days,
                late_days=late_days,
                total_hours_worked=attendance_stats['total_hours_worked'],
                overtime_hours=attendance_stats['overtime_hours'],
                month=month,
                year=year,
            )

            payroll = Payroll.query.filter_by(
                employee_id=employee.id,
                month=month,
                year=year,
            ).first()

            if payroll:
                for field_name, field_value in payroll_fields.items():
                    setattr(payroll, field_name, field_value)
            else:
                payroll = Payroll(
                    employee_id=employee.id,
                    month=month,
                    year=year,
                    **payroll_fields,
                )
                db.session.add(payroll)

            payroll_records.append(payroll)

        db.session.commit()
        logger.info(
            "Calculated payroll for %s employees for %s/%s",
            len(payroll_records),
            month,
            year,
        )

        return payroll_records

    def _get_working_days(self, start_date, end_date):
        """Count working days from start_date through end_date (inclusive).
        
        Sundays are weekly holidays/off days and are EXCLUDED from working days.
        """
        if end_date < start_date:
            return 0

        working_days = 0
        current_date = start_date
        while current_date <= end_date:
            # Sunday is a weekly holiday/off day - exclude from working days
            if current_date.weekday() != 6:  # 6 = Sunday
                working_days += 1
            current_date += timedelta(days=1)

        return working_days

    def get_payroll(self, year, month, employee_id=None):
        """Get payroll records."""
        query = Payroll.query.filter_by(month=month, year=year)

        if employee_id:
            query = query.filter_by(employee_id=employee_id)

        return query.all()

    def get_payroll_summary(self, year, month):
        """Get payroll summary for a month."""
        payrolls = self.get_payroll(year, month)

        return {
            'total_employees': len(payrolls),
            'total_gross_salary': round_money(sum(p.gross_salary for p in payrolls)),
            'total_net_salary': round_money(sum(p.net_salary for p in payrolls)),
            'total_overtime_bonus': round_money(sum(p.overtime_bonus for p in payrolls)),
            'total_deductions': round_money(sum(p.total_deductions for p in payrolls)),
        }
