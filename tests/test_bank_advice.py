"""
Tests for the Bank Payment Advice feature (bank_advice.py + routes + emails).
"""
import os
from datetime import date

import pytest


@pytest.fixture(autouse=True)
def _isolated_uploads(tmp_path, monkeypatch):
    """Write advice/payslip PDFs under tmp_path, never the real uploads/ folder."""
    import bank_advice
    import scheduler_service
    monkeypatch.setattr(bank_advice.Config, 'UPLOAD_FOLDER', str(tmp_path))
    monkeypatch.setattr(scheduler_service.Config, 'UPLOAD_FOLDER', str(tmp_path))
    return tmp_path


@pytest.fixture
def sent_mail(monkeypatch):
    """Capture every send_email() call (the suite already blocks real SMTP)."""
    import email_service
    calls = []

    def _fake(self, to_email, subject, html_body, text_body=None, attachments=None):
        calls.append({'to': to_email, 'subject': subject, 'html': html_body,
                      'text': text_body, 'attachments': attachments or []})
        return {'success': True, 'message': 'ok'}

    monkeypatch.setattr(email_service.EmailService, 'send_email', _fake)
    return calls


def _payroll(employee, net=40000.0, month=10, year=2026):
    from database import db
    from models import Payroll
    p = Payroll(employee_id=employee.id, month=month, year=year, basic_salary=50000,
                gross_salary=net + 2000, net_salary=net, total_deductions=2000)
    db.session.add(p)
    db.session.commit()
    return p


def _enable(bank_emails='payroll@bank.com', auto=False):
    from database import db
    from models import PayrollSettings
    s = PayrollSettings.get_settings()
    s.bank_advice_enabled = True
    s.bank_advice_auto_send = auto
    s.bank_recipient_name = 'Payroll Desk'
    s.bank_recipient_emails = bank_emails
    s.company_bank_name = 'HDFC Bank'
    s.company_bank_account_number = '50100000000001'
    s.company_bank_ifsc_code = 'HDFC0001234'
    db.session.commit()
    return s


def _bank_employee(make_employee, emp_id='EMP0001', **kw):
    defaults = dict(bank_name='SBI', bank_account_number='1234567890', bank_ifsc_code='SBIN0000001')
    defaults.update(kw)
    return make_employee(employee_id=emp_id, **defaults)


# ---------------------------------------------------------------- helpers

def test_parse_emails_filters_invalid_and_duplicates(app_context):
    from bank_advice import parse_emails
    assert parse_emails('a@b.com, bad, A@B.com; c@d.org\n') == ['a@b.com', 'c@d.org']
    assert parse_emails(None) == []


def test_collect_rows_skips_missing_bank_details_and_zero_net(app_context, make_employee):
    from bank_advice import collect_advice_rows
    ok = _bank_employee(make_employee, 'EMP0001')
    no_acc = make_employee(employee_id='EMP0002', bank_ifsc_code='SBIN0000001')
    no_ifsc = make_employee(employee_id='EMP0003', bank_account_number='999')
    zero = _bank_employee(make_employee, 'EMP0004')
    for e in (ok, no_acc, no_ifsc):
        _payroll(e)
    _payroll(zero, net=0)

    rows, skipped = collect_advice_rows(10, 2026)

    assert [r['employee_id'] for r in rows] == ['EMP0001']
    reasons = {e.employee_id: why for e, why in skipped}
    assert 'account' in reasons['EMP0002']
    assert 'IFSC' in reasons['EMP0003']
    assert 'zero' in reasons['EMP0004']


def test_collect_rows_ignores_inactive_employees(app_context, make_employee):
    from bank_advice import collect_advice_rows
    emp = _bank_employee(make_employee, status='inactive')
    _payroll(emp)
    rows, skipped = collect_advice_rows(10, 2026)
    assert rows == [] and skipped == []


# -------------------------------------------------------------------- PDF

def test_generate_pdf_contains_total_and_is_valid(app_context, make_employee, tmp_path):
    from bank_advice import collect_advice_rows, generate_advice_pdf
    from models import CompanySettings
    for i, net in enumerate((40000, 25500.50), 1):
        _payroll(_bank_employee(make_employee, f'EMP000{i}'), net=net)
    settings = _enable()
    rows, _ = collect_advice_rows(10, 2026)
    out = str(tmp_path / 'advice.pdf')

    generate_advice_pdf(rows, 10, 2026, CompanySettings.get_settings(), settings, out)

    data = open(out, 'rb').read()
    assert data.startswith(b'%PDF')
    assert os.path.getsize(out) > 1000
    try:
        import pypdf
    except ImportError:
        pytest.skip('pypdf not installed - PDF text check skipped')
    text = ''.join(p.extract_text() for p in pypdf.PdfReader(out).pages)
    assert '65,500.50' in text
    assert '1234567890' in text and 'SBIN0000001' in text


# ------------------------------------------------------------ bank sending

def test_send_bank_advice_emails_pdf_to_every_recipient(app_context, make_employee, sent_mail):
    from bank_advice import send_bank_advice
    from models import BankAdvice
    _payroll(_bank_employee(make_employee))
    _enable('payroll@bank.com, rm@bank.com')

    result = send_bank_advice(10, 2026)

    assert result['success'] and result['count'] == 1 and result['total'] == 40000.0
    assert [m['to'] for m in sent_mail] == ['payroll@bank.com', 'rm@bank.com']
    assert sent_mail[0]['attachments'][0].endswith('bank_advice_2026_10.pdf')
    assert os.path.exists(sent_mail[0]['attachments'][0])
    assert 'October 2026' in sent_mail[0]['subject']
    rec = BankAdvice.query.filter_by(month=10, year=2026).one()
    assert rec.status == 'sent' and rec.sent_at is not None


def test_send_bank_advice_not_repeated_unless_forced(app_context, make_employee, sent_mail):
    from bank_advice import send_bank_advice
    _payroll(_bank_employee(make_employee))
    _enable()

    assert send_bank_advice(10, 2026)['success']
    again = send_bank_advice(10, 2026)
    assert not again['success'] and again['already_sent']
    assert len(sent_mail) == 1

    assert send_bank_advice(10, 2026, force=True)['success']
    assert len(sent_mail) == 2


def test_send_bank_advice_requires_enabled_and_valid_email(app_context, make_employee, sent_mail):
    from bank_advice import send_bank_advice
    _payroll(_bank_employee(make_employee))
    assert 'not enabled' in send_bank_advice(10, 2026)['message']
    _enable(bank_emails='not-an-email')
    assert 'No valid bank email' in send_bank_advice(10, 2026)['message']
    assert sent_mail == []


def test_send_bank_advice_failure_is_recorded_and_retryable(app_context, make_employee, monkeypatch):
    import email_service
    from bank_advice import send_bank_advice
    from models import BankAdvice
    _payroll(_bank_employee(make_employee))
    _enable()
    monkeypatch.setattr(email_service.EmailService, 'send_email',
                        lambda self, *a, **k: {'success': False, 'message': 'SMTP down'})

    result = send_bank_advice(10, 2026)

    assert not result['success'] and 'SMTP down' in result['message']
    assert BankAdvice.query.one().status == 'failed'
    # a failed attempt must not block the automatic retry
    monkeypatch.setattr(email_service.EmailService, 'send_email',
                        lambda self, *a, **k: {'success': True, 'message': 'ok'})
    assert send_bank_advice(10, 2026)['success']


def test_send_bank_advice_with_nobody_payable(app_context, make_employee, sent_mail):
    from bank_advice import send_bank_advice
    _payroll(make_employee(employee_id='EMP0001'))  # no bank details
    _enable()
    result = send_bank_advice(10, 2026)
    assert not result['success'] and result['skipped']
    assert sent_mail == []


def test_concurrent_send_is_refused_not_duplicated(app_context, make_employee, sent_mail):
    import bank_advice
    _payroll(_bank_employee(make_employee))
    _enable()
    assert bank_advice._send_lock.acquire(blocking=False)   # simulate a click already in progress
    try:
        result = bank_advice.send_bank_advice(10, 2026, force=True)
    finally:
        bank_advice._send_lock.release()
    assert result.get('in_progress') and not result['success']
    assert sent_mail == []


def test_existing_row_is_reused_on_forced_resend(app_context, make_employee, sent_mail):
    from bank_advice import send_bank_advice
    from models import BankAdvice
    _payroll(_bank_employee(make_employee))
    _enable()
    send_bank_advice(10, 2026)
    assert send_bank_advice(10, 2026, force=True)['success']
    assert BankAdvice.query.filter_by(month=10, year=2026).count() == 1


# ------------------------------------------------------- employee payslips

def test_payslip_email_has_credit_note_only_when_given(app_context, sent_mail):
    from email_service import EmailService
    es = EmailService()
    es.send_payslip('a@b.com', 'Asha', '/x.pdf', 10, 2026)
    es.send_payslip('a@b.com', 'Asha', '/x.pdf', 10, 2026, credit_notice='1 to 2 working days')
    assert 'will be credited' not in sent_mail[0]['html']
    assert 'within <strong>1 to 2 working days</strong>' in sent_mail[1]['html']
    assert '1 to 2 working days' in sent_mail[1]['text']


def test_email_payslips_with_credit_notice_marks_sent(app_context, make_employee, sent_mail):
    from bank_advice import email_payslips_with_credit_notice
    emp = _bank_employee(make_employee)
    payroll = _payroll(emp)
    _enable()

    sent, failed = email_payslips_with_credit_notice(10, 2026)

    assert (sent, failed) == (1, [])
    assert payroll.email_sent and payroll.payslip_generated
    assert os.path.exists(payroll.payslip_path)
    assert '1 to 2 working days' in sent_mail[0]['html']
    # second run only touches unsent payslips
    assert email_payslips_with_credit_notice(10, 2026) == (0, [])


def test_credit_notice_text_off_when_feature_disabled(app_context):
    from bank_advice import credit_notice_text
    assert credit_notice_text() is None
    _enable()
    assert credit_notice_text() == '1 to 2 working days'


# ------------------------------------------------------------------ routes

def _login(client, make_admin):
    make_admin(username='owner', password='adminpass123')
    client.post('/login', data={'username': 'owner', 'password': 'adminpass123', 'role': 'admin'})


def test_payroll_settings_saves_bank_fields(client, make_admin):
    _login(client, make_admin)
    client.post('/payroll-settings', data={
        'payroll_generation_day': '1', 'payroll_generation_time': '18:00',
        'payslip_storage_path': 'payrolls',
        'bank_advice_enabled': 'on', 'bank_advice_auto_send': 'on',
        'bank_recipient_name': 'Desk', 'bank_recipient_emails': 'x@bank.com, junk',
        'company_bank_name': 'HDFC', 'company_bank_account_number': '111',
        'company_bank_ifsc_code': 'hdfc0000001', 'salary_credit_days_text': '2 days',
        'company_name': 'Acme', 'company_address': 'A', 'company_phone': '1', 'company_email': 'a@b.com',
    }, follow_redirects=True)
    from models import PayrollSettings
    s = PayrollSettings.get_settings()
    assert s.bank_advice_enabled and s.bank_advice_auto_send
    assert s.bank_recipient_emails == 'x@bank.com'
    assert s.company_bank_ifsc_code == 'HDFC0000001'
    assert s.salary_credit_days_text == '2 days'


def test_bank_advice_send_route_emails_bank_and_employees(client, make_admin, make_employee, sent_mail):
    _login(client, make_admin)
    _payroll(_bank_employee(make_employee))
    _enable()

    resp = client.post('/payroll/bank-advice/send',
                       data={'month': '10', 'year': '2026', 'notify_employees': 'on'},
                       follow_redirects=True)

    assert resp.status_code == 200
    assert b'sent to payroll@bank.com' in resp.data
    recipients = [m['to'] for m in sent_mail]
    assert 'payroll@bank.com' in recipients and 'emp0001@example.com' in recipients


def test_send_route_does_not_resend_payslip_to_already_emailed_employee(client, make_admin, make_employee, sent_mail):
    _login(client, make_admin)
    done = _payroll(_bank_employee(make_employee, 'EMP0001'))
    done.email_sent = True
    _payroll(_bank_employee(make_employee, 'EMP0002', bank_account_number='555'))
    from database import db
    db.session.commit()
    _enable()

    client.post('/payroll/bank-advice/send',
                data={'month': '10', 'year': '2026', 'notify_employees': 'on'}, follow_redirects=True)
    employee_mail = [m['to'] for m in sent_mail if m['to'].endswith('@example.com')]
    assert employee_mail == ['emp0002@example.com']

    sent_mail.clear()
    client.post('/payroll/bank-advice/send',
                data={'month': '10', 'year': '2026', 'notify_employees': 'on', 'resend_to_all': 'on'},
                follow_redirects=True)
    assert sorted(m['to'] for m in sent_mail if m['to'].endswith('@example.com')) == \
        ['emp0001@example.com', 'emp0002@example.com']


def test_bank_advice_preview_downloads_pdf_without_emailing(client, make_admin, make_employee, sent_mail):
    _login(client, make_admin)
    _payroll(_bank_employee(make_employee))
    _enable()
    resp = client.get('/payroll/bank-advice/preview?month=10&year=2026')
    assert resp.status_code == 200 and resp.data.startswith(b'%PDF')
    assert sent_mail == []


def test_bank_advice_routes_require_admin(client):
    assert client.post('/payroll/bank-advice/send', data={'month': 10, 'year': 2026}).status_code in (302, 401, 403)
    assert client.get('/payroll/bank-advice/preview').status_code in (302, 401, 403)


def test_payroll_page_shows_buttons_only_when_enabled(client, make_admin):
    _login(client, make_admin)
    assert b'Send to Bank' not in client.get('/payroll?month=10&year=2026').data
    _enable()
    assert b'Send to Bank' in client.get('/payroll?month=10&year=2026').data


def test_employee_ifsc_saved_uppercase(client, make_admin):
    _login(client, make_admin)
    client.post('/employees/add', data={
        'name': 'Bob', 'department': 'X', 'designation': 'Y', 'basic_salary': '45000',
        'joining_date': '2026-01-15', 'email': 'bob@example.com', 'phone': '9998887777',
        'address': 'a', 'office_location': 'HQ', 'bank_name': 'SBI',
        'bank_account_number': '12345', 'bank_ifsc_code': ' sbin0000001 ',
    }, follow_redirects=True)
    from models import Employee
    assert Employee.query.filter_by(name='Bob').one().bank_ifsc_code == 'SBIN0000001'


# --------------------------------------------------------------- scheduler

def test_scheduler_sends_bank_advice_and_credit_note(app_context, make_employee, sent_mail, monkeypatch):
    """Auto payroll run: payslip mail carries the note, bank gets the advice once."""
    from datetime import datetime
    from scheduler_service import PayrollScheduler
    from models import PayrollSettings
    from database import db

    emp = _bank_employee(make_employee)
    settings = _enable(auto=True)
    settings.auto_generate_payroll = True
    settings.auto_send_payslip_email = True
    db.session.commit()

    from types import SimpleNamespace
    from pdf_generator import PDFGenerator
    from email_service import EmailService

    # init_app() normally creates these services; build them by hand so the test
    # does not start the real APScheduler thread.
    sched = PayrollScheduler(app_context)
    sched.app = app_context
    sched.pdf_generator = PDFGenerator()
    sched.email_service = EmailService()

    def _fake_calc(year, month, employee_id=None):
        return [_payroll(emp, month=month, year=year)]
    sched.payroll_calculator = SimpleNamespace(calculate_monthly_payroll=_fake_calc)

    sched.generate_monthly_payroll(month=10, year=2026)

    recipients = [m['to'] for m in sent_mail]
    assert 'emp0001@example.com' in recipients and 'payroll@bank.com' in recipients
    emp_mail = next(m for m in sent_mail if m['to'] == 'emp0001@example.com')
    assert '1 to 2 working days' in emp_mail['html']

    # a second run (e.g. startup reconcile) must not mail the bank again
    sched.generate_monthly_payroll(month=10, year=2026)
    assert recipients.count('payroll@bank.com') == 1
    assert [m['to'] for m in sent_mail].count('payroll@bank.com') == 1


# --------------------------------------------- shared bank account warning

_PHONES = {}


def _add_form(name, email, account, ifsc):
    # the app rejects duplicate name / phone / email, so give each employee its own phone
    phone = _PHONES.setdefault(email, f"98{len(_PHONES):08d}")
    return {'name': name, 'department': 'X', 'designation': 'Y', 'basic_salary': '1000',
            'joining_date': '2026-01-15', 'email': email, 'phone': phone,
            'address': 'a', 'office_location': 'HQ', 'bank_name': 'SBI',
            'bank_account_number': account, 'bank_ifsc_code': ifsc}


def test_same_account_and_ifsc_warns_but_still_saves(client, make_admin):
    _login(client, make_admin)
    client.post('/employees/add', data=_add_form('Asha', 'asha@example.com', '123456', 'SBIN0000001'),
                follow_redirects=True)
    resp = client.post('/employees/add', data=_add_form('Ravi', 'ravi@example.com', '123456', 'SBIN0000001'),
                       follow_redirects=True)
    assert b'Warning: the same bank account' in resp.data
    assert b'Asha' in resp.data
    from models import Employee
    assert Employee.query.filter_by(name='Ravi').count() == 1      # not blocked
    assert Employee.query.count() == 2


def test_same_account_different_ifsc_or_unique_does_not_warn(client, make_admin):
    _login(client, make_admin)
    client.post('/employees/add', data=_add_form('Asha', 'asha@example.com', '123456', 'SBIN0000001'),
                follow_redirects=True)
    other_bank = client.post('/employees/add', data=_add_form('Ravi', 'ravi@example.com', '123456', 'HDFC0000001'),
                             follow_redirects=True)
    same_branch = client.post('/employees/add', data=_add_form('Meena', 'meena@example.com', '999999', 'SBIN0000001'),
                              follow_redirects=True)
    from models import Employee
    assert Employee.query.count() == 3          # all three really saved
    assert b'Warning: the same bank account' not in other_bank.data
    assert b'Warning: the same bank account' not in same_branch.data