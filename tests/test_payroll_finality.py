"""Tests for the "final vs mid-month" payroll decision in scheduler_service."""
from datetime import datetime
from types import SimpleNamespace

from scheduler_service import PayrollScheduler

final = PayrollScheduler._is_payroll_final


def rec(updated, generated=True, emailed=True):
    return SimpleNamespace(updated_at=updated, created_at=updated,
                           payslip_generated=generated, email_sent=emailed)


def test_no_record_is_not_final():
    assert final(None, 10, 2026, True) is False


def test_already_emailed_is_final_even_if_mid_month():
    # one email per employee per month: never recalculated / re-sent
    assert final(rec(datetime(2026, 10, 15, 10, 0)), 10, 2026, True) is True


def test_not_emailed_is_pending_when_auto_email_on():
    assert final(rec(datetime(2026, 10, 15), emailed=False), 10, 2026, True) is False
    assert final(rec(datetime(2026, 11, 1, 16, 10), emailed=False), 10, 2026, True) is False


def test_auto_email_off_final_only_after_month_end_with_pdf():
    assert final(rec(datetime(2026, 10, 15), emailed=False), 10, 2026, False) is False
    assert final(rec(datetime(2026, 11, 1, 16, 10), emailed=False), 10, 2026, False) is True
    assert final(rec(datetime(2026, 11, 1, 16, 10), generated=False, emailed=False), 10, 2026, False) is False


def test_december_rolls_over_to_january():
    assert final(rec(datetime(2026, 12, 20), emailed=False), 12, 2026, False) is False
    assert final(rec(datetime(2027, 1, 1, 12, 5), emailed=False), 12, 2026, False) is True


# ---------------------------------------------------------------------------
# End-to-end: server was off on the 1st; some employees were already emailed
# (mid-month), others were not.
# ---------------------------------------------------------------------------
def test_reconcile_only_processes_employees_not_yet_emailed(app_context, make_employee):
    from unittest.mock import patch
    from datetime import timedelta
    from database import db
    from models import Payroll, PayrollSettings
    import scheduler_service

    scheduler_service._scheduler_initialized = False
    scheduler = PayrollScheduler()
    scheduler.init_app(app_context)

    settings = PayrollSettings.get_settings()
    settings.auto_generate_payroll = True
    settings.auto_send_payslip_email = True
    settings.payroll_generation_time = "00:00"
    db.session.commit()

    mailed_emp = make_employee(employee_id='EMP0001', email='a@example.com')
    pending_emp = make_employee(employee_id='EMP0002', email='b@example.com')
    prev = datetime.now().replace(day=1) - timedelta(days=1)
    mid = datetime(prev.year, prev.month, 15, 10, 0)

    for emp, sent in ((mailed_emp, True), (pending_emp, False)):
        db.session.add(Payroll(employee_id=emp.id, month=prev.month, year=prev.year,
                               basic_salary=1000, gross_salary=1000, net_salary=1000,
                               payslip_generated=True, payslip_path='old.pdf',
                               email_sent=sent, created_at=mid, updated_at=mid))
    db.session.commit()

    def fake_calc(year, month, employee_id=None):
        p = Payroll.query.filter_by(employee_id=employee_id, month=month, year=year).first()
        p.basic_salary = 5000
        db.session.commit()
        return [p]

    month_name = scheduler._get_month_name(prev.month)
    with patch.object(scheduler.payroll_calculator, 'calculate_monthly_payroll', side_effect=fake_calc) as calc, \
         patch.object(scheduler.pdf_generator, 'generate_payslip'), \
         patch.object(scheduler.email_service, 'send_payslip', return_value={'success': True}) as mail:
        def calls(m): return [c for c in m.call_args_list
                              if c.kwargs.get('month') == prev.month or c.kwargs.get('month') == month_name]

        scheduler.reconcile_missed_payroll()
        assert [c.kwargs['employee_id'] for c in calls(calc)] == [pending_emp.id]
        assert [c.kwargs['employee_email'] for c in calls(mail)] == ['b@example.com']

        # Next startup: everybody has been emailed -> nothing more happens.
        scheduler.reconcile_missed_payroll()
        assert len(calls(calc)) == 1 and len(calls(mail)) == 1

    pend = Payroll.query.filter_by(employee_id=pending_emp.id, month=prev.month, year=prev.year).first()
    done = Payroll.query.filter_by(employee_id=mailed_emp.id, month=prev.month, year=prev.year).first()
    assert pend.basic_salary == 5000 and pend.email_sent is True
    assert done.basic_salary == 1000          # already-mailed employee untouched
