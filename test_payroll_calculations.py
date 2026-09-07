"""
Core payroll math tests: the small pure helper functions in payroll.py,
plus end-to-end tests of compute_payroll_amounts() (the actual computation
engine PayrollCalculator.calculate_monthly_payroll delegates to) against
independently hand-verified expected values, not just re-invoking the
same function under test to produce its own "expected" answer.
"""
import pytest

from payroll import (
    round_money,
    round_days,
    compute_paid_and_lop_days,
    convert_late_marks_to_half_days,
    get_month_total_days,
    get_month_working_days,
    get_proration_factor,
    compute_payroll_amounts,
)


# ---------------------------------------------------------------------
# Small pure helpers - no DB, no Flask app context needed at all.
# ---------------------------------------------------------------------

def test_round_money_rounds_to_2dp():
    assert round_money(1234.5678) == 1234.57
    assert round_money(None) == 0.0
    assert round_money(0) == 0.0
    assert round_money("50.005") == 50.01


def test_round_days_rounds_to_1dp():
    assert round_days(4.55) == 4.5
    assert round_days(None) == 0.0
    assert round_days(3) == 3.0


@pytest.mark.parametrize("present,absent,half,expected_paid,expected_lop", [
    (20, 0, 0, 20.0, 0.0),
    (0, 20, 0, 0.0, 20.0),
    (18, 1, 2, 19.0, 2.0),   # 2 half-days -> 1.0 paid, 1.0 lop
    (0, 0, 0, 0.0, 0.0),
])
def test_compute_paid_and_lop_days(present, absent, half, expected_paid, expected_lop):
    paid, lop = compute_paid_and_lop_days(present, absent, half)
    assert paid == expected_paid
    assert lop == expected_lop


@pytest.mark.parametrize("late_days,expected_half_days", [
    (0, 0),
    (1, 0),
    (2, 0),
    (3, 1),
    (5, 1),
    (6, 2),
    (8, 2),
    (9, 3),
    (21, 7),
])
def test_convert_late_marks_to_half_days(late_days, expected_half_days):
    """
    Documented rule: every 3 late marks converts to 1 half-day, floor
    division - 1-2 late marks have no effect, exactly matching the
    docstring's own worked examples in payroll.py.
    """
    assert convert_late_marks_to_half_days(late_days) == expected_half_days


def test_get_proration_factor_zero_paid_days_is_zero():
    assert get_proration_factor(0, 24) == 0.0
    assert get_proration_factor(-1, 24) == 0.0


def test_get_proration_factor_full_attendance_is_one():
    assert get_proration_factor(24, 24) == 1.0


def test_get_proration_factor_partial_attendance():
    assert get_proration_factor(12, 24) == 0.5


def test_get_proration_factor_never_exceeds_one():
    # More paid days than the month has working days shouldn't be possible
    # in practice, but the function must never pay out more than a full
    # month regardless.
    assert get_proration_factor(30, 24) == 1.0


def test_get_proration_factor_defensive_fallback_for_zero_working_days():
    # Should not normally happen, but must not divide by zero.
    assert get_proration_factor(5, 0) == 1.0


def test_get_month_total_days():
    assert get_month_total_days(2026, 2) == 28   # Feb 2026 is not a leap year
    assert get_month_total_days(2026, 4) == 30
    assert get_month_total_days(2024, 2) == 29   # 2024 IS a leap year


def test_get_month_working_days_against_hand_verified_calendar():
    """
    Independently hand-verified via Python's own date.weekday() (not by
    calling get_month_working_days itself): February 2026 has 28 days and
    4 Sundays -> 24 working days. April 2026 has 30 days and 4 Sundays ->
    26 working days.
    """
    assert get_month_working_days(2026, 2) == 24
    assert get_month_working_days(2026, 4) == 26


# ---------------------------------------------------------------------
# compute_payroll_amounts() - the actual computation engine. These need a
# real Settings/PayrollSettings row (via get_settings()'s auto-create) and
# a real Employee row, so they use the DB-backed app_context/make_employee
# fixtures from conftest.py rather than being pure unit tests - but the
# math itself is still checked against independently-derived expected
# values, not against the function's own output.
# ---------------------------------------------------------------------

FULL_MONTH_WORKING_DAYS_FEB_2026 = 24  # hand-verified above


def test_compute_payroll_amounts_full_attendance_has_no_proration(app_context, make_employee):
    """With paid_days == the full month's working days, proration_factor
    must be exactly 1.0 and every prorated figure must equal its raw,
    un-prorated value - regardless of the exact salary numbers chosen."""
    employee = make_employee(
        basic_salary=50000, hra=10000, da=2000,
        medical_allowance=1000, travel_allowance=1000,
        special_allowance=0, other_allowances=0,
        bus_charges=0, other_deduction=0,
    )

    result = compute_payroll_amounts(
        employee=employee,
        working_days=FULL_MONTH_WORKING_DAYS_FEB_2026,
        present_days=FULL_MONTH_WORKING_DAYS_FEB_2026,
        absent_days=0,
        half_days=0,
        late_days=0,
        total_hours_worked=0,
        overtime_hours=0,
        month=2,
        year=2026,
    )

    assert result['proration_factor'] == 1.0
    assert result['basic_salary'] == 50000.0
    assert result['hra'] == 10000.0
    assert result['da'] == 2000.0
    assert result['absent_deduction'] == 0.0
    assert result['half_day_deduction'] == 0.0

    # Statutory deductions computed on earned (= full, since no LOP here)
    # basic/gross, using this Employee's default PF/ESIC/TDS percentages
    # (12% / 12% / 0.75% / 0% - see models.Employee defaults).
    expected_gross = 50000 + 10000 + 2000 + 1000 + 1000  # no overtime configured
    assert result['gross_salary'] == expected_gross
    assert result['employee_pf'] == round_money(50000 * 0.12)
    assert result['esic'] == round_money(expected_gross * 0.0075)

    # Professional tax for February specifically is 300 (PayrollSettings
    # default professional_tax_feb=300.0, distinct from every other
    # month's default of 200.0 - see models.py), so using Feb here also
    # verifies get_professional_tax(month) is wired correctly.
    assert result['professional_tax'] == 300.0


def test_compute_payroll_amounts_zero_attendance_zeroes_earnings(app_context, make_employee):
    """Zero paid days must zero out every prorated earning (basic, HRA,
    etc.) - only flat, non-prorated deductions and net-salary flooring
    should still apply."""
    employee = make_employee(basic_salary=50000, hra=10000)

    result = compute_payroll_amounts(
        employee=employee,
        working_days=FULL_MONTH_WORKING_DAYS_FEB_2026,
        present_days=0,
        absent_days=FULL_MONTH_WORKING_DAYS_FEB_2026,
        half_days=0,
        late_days=0,
        total_hours_worked=0,
        overtime_hours=0,
        month=2,
        year=2026,
    )

    assert result['proration_factor'] == 0.0
    assert result['basic_salary'] == 0.0
    assert result['hra'] == 0.0
    assert result['gross_salary'] == 0.0
    # net_salary must floor at 0, never go negative even though flat
    # per-occurrence deductions (absent_deduction: 24 days * Rs 500) would
    # otherwise push the raw gross-minus-deductions figure well below 0.
    assert result['net_salary'] == 0.0
    assert result['absent_deduction'] == round_money(FULL_MONTH_WORKING_DAYS_FEB_2026 * 500.0)


def test_compute_payroll_amounts_partial_attendance_prorates_proportionally(app_context, make_employee):
    """Half the month's working days paid -> proration_factor of exactly
    0.5, and every prorated earning exactly halved."""
    employee = make_employee(basic_salary=50000, hra=10000, da=0,
                              medical_allowance=0, travel_allowance=0,
                              special_allowance=0, other_allowances=0)

    half_days_present = FULL_MONTH_WORKING_DAYS_FEB_2026 / 2  # = 12

    result = compute_payroll_amounts(
        employee=employee,
        working_days=FULL_MONTH_WORKING_DAYS_FEB_2026,
        present_days=half_days_present,
        absent_days=FULL_MONTH_WORKING_DAYS_FEB_2026 - half_days_present,
        half_days=0,
        late_days=0,
        total_hours_worked=0,
        overtime_hours=0,
        month=2,
        year=2026,
    )

    assert result['proration_factor'] == 0.5
    assert result['basic_salary'] == 25000.0
    assert result['hra'] == 5000.0
    assert result['full_basic_salary'] == 50000.0  # the un-prorated reference figure is preserved


def test_compute_payroll_amounts_late_marks_convert_to_half_day_deduction(app_context, make_employee):
    """9 late marks -> 3 converted half-days (see
    test_convert_late_marks_to_half_days), which must show up in the
    payslip's half_days figure and its associated deduction."""
    employee = make_employee(basic_salary=50000)

    result = compute_payroll_amounts(
        employee=employee,
        working_days=FULL_MONTH_WORKING_DAYS_FEB_2026,
        present_days=FULL_MONTH_WORKING_DAYS_FEB_2026,
        absent_days=0,
        half_days=0,
        late_days=9,
        total_hours_worked=0,
        overtime_hours=0,
        month=2,
        year=2026,
    )

    # 9 // 3 = 3 converted half-days, added on top of the raw half_days=0
    assert result['half_days'] == 3.0
    # Settings default: half_day_deduction_enabled=True,
    # half_day_deduction_per_occurrence=200.0 (see models.Settings)
    assert result['half_day_deduction'] == round_money(3 * 200.0)


def test_compute_payroll_amounts_net_ctc_includes_employer_pf(app_context, make_employee):
    employee = make_employee(basic_salary=50000, hra=0, da=0, medical_allowance=0,
                              travel_allowance=0, special_allowance=0, other_allowances=0)

    result = compute_payroll_amounts(
        employee=employee,
        working_days=FULL_MONTH_WORKING_DAYS_FEB_2026,
        present_days=FULL_MONTH_WORKING_DAYS_FEB_2026,
        absent_days=0, half_days=0, late_days=0,
        total_hours_worked=0, overtime_hours=0,
        month=2, year=2026,
    )

    # Net CTC = gross salary + employer PF (12% of earned basic, per
    # models.Employee's default employer_pf_percentage)
    expected_employer_pf = round_money(50000 * 0.12)
    assert result['employer_pf'] == expected_employer_pf
    assert result['net_ctc'] == round_money(result['gross_salary'] + expected_employer_pf)
