"""The "Payroll Generation Day" setting must actually decide the run day."""
from datetime import datetime
from unittest.mock import patch

import pytest


@pytest.mark.parametrize("year,month,day,expected", [
    (2026, 12, 15, 15),
    (2026, 12, 1, 1),
    (2026, 2, 30, 28),   # short month -> last day
    (2028, 2, 31, 29),   # leap year
    (2026, 4, 31, 30),
    (2026, 1, 31, 31),
    (2026, 6, None, 1),  # bad value -> 1st
])
def test_effective_payroll_day(year, month, day, expected):
    from scheduler_service import effective_payroll_day
    assert effective_payroll_day(year, month, day) == expected


def _scheduler(app_context):
    from scheduler_service import PayrollScheduler
    s = PayrollScheduler()
    s.app = app_context
    return s


def _freeze(monkeypatch, when):
    import scheduler_service

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return when
    monkeypatch.setattr(scheduler_service, 'datetime', _DT)


def test_daily_job_runs_payroll_only_on_selected_day(app_context, monkeypatch):
    from database import db
    from models import PayrollSettings
    s = PayrollSettings.get_settings()
    s.payroll_generation_day = 15
    db.session.commit()
    sched = _scheduler(app_context)

    with patch.object(sched, 'generate_monthly_payroll') as gen:
        _freeze(monkeypatch, datetime(2026, 12, 1, 18, 0))
        sched.run_scheduled_payroll()
        _freeze(monkeypatch, datetime(2026, 12, 14, 18, 0))
        sched.run_scheduled_payroll()
        assert gen.call_count == 0
        _freeze(monkeypatch, datetime(2026, 12, 15, 18, 0))
        sched.run_scheduled_payroll()
        assert gen.call_count == 1


def test_day_31_runs_on_last_day_of_short_month(app_context, monkeypatch):
    from database import db
    from models import PayrollSettings
    s = PayrollSettings.get_settings()
    s.payroll_generation_day = 31
    db.session.commit()
    sched = _scheduler(app_context)

    with patch.object(sched, 'generate_monthly_payroll') as gen:
        _freeze(monkeypatch, datetime(2026, 4, 30, 18, 0))
        sched.run_scheduled_payroll()
    assert gen.call_count == 1


def test_default_day_is_first(app_context, monkeypatch):
    sched = _scheduler(app_context)
    with patch.object(sched, 'generate_monthly_payroll') as gen:
        _freeze(monkeypatch, datetime(2026, 12, 1, 18, 0))
        sched.run_scheduled_payroll()
    assert gen.call_count == 1


def test_reconcile_not_due_before_selected_day(app_context, monkeypatch):
    """With day 15, on 5 Dec last month's (Nov) payroll is NOT yet due."""
    from database import db
    from models import PayrollSettings, Employee
    from datetime import date
    s = PayrollSettings.get_settings()
    s.auto_generate_payroll = True
    s.payroll_generation_day = 15
    s.payroll_generation_time = '00:00'
    db.session.commit()
    db.session.add(Employee(
        employee_id='E1', name='A', username='E1', department='d', designation='x',
        basic_salary=1000, joining_date=date(2026, 11, 1), email='a@b.com', phone='1',
        address='a', password_hash='x'))
    db.session.commit()
    sched = _scheduler(app_context)
    _freeze(monkeypatch, datetime(2026, 12, 5, 10, 0))
    with patch.object(sched, 'generate_monthly_payroll') as gen:
        sched.reconcile_missed_payroll()
    months = [c.kwargs.get('month') for c in gen.call_args_list]
    assert 11 not in months   # Nov not due until 15 Dec

    _freeze(monkeypatch, datetime(2026, 12, 16, 10, 0))
    with patch.object(sched, 'generate_monthly_payroll') as gen:
        sched.reconcile_missed_payroll()
    assert 11 in [c.kwargs.get('month') for c in gen.call_args_list]
