"""
Bank Payment Advice
===================

After a month's payroll is calculated, the company has to tell its bank to
transfer every employee's NET salary to their bank account. This module:

1. builds the *Bank Payment Advice* PDF - one row per employee with name,
   bank, account number, IFSC, mobile, email and net salary, plus a grand
   total and the amount in words;
2. emails that PDF to the bank's payroll desk (recipients are configured in
   Payroll Settings);
3. emails every employee their payslip together with a note saying the
   salary will be credited within "1 to 2 working days" (text is
   configurable);
4. records what was sent in the ``bank_advice`` table so the automatic run
   never emails the bank twice for the same month.

Employees without a bank account number / IFSC are NOT put on the advice
(the bank could not credit them anyway); they are counted and listed in the
result so the admin can fix their profile and re-send.
"""
import logging
import os
import re
import threading
from datetime import datetime

from config import Config
from database import db
from models import (
    BankAdvice, CompanySettings, Employee, Payroll, PayrollSettings,
)

logger = logging.getLogger(__name__)

# Only one bank advice may be built/sent at a time. A double-click (or two admins
# clicking together) would otherwise race on the one-row-per-month bank_advice table.
_send_lock = threading.Lock()

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def parse_emails(raw):
    """Split a comma/semicolon/newline separated string into valid, unique emails."""
    seen, result = set(), []
    for part in re.split(r'[,;\n]+', raw or ''):
        email = part.strip()
        if email and _EMAIL_RE.match(email) and email.lower() not in seen:
            seen.add(email.lower())
            result.append(email)
    return result


def _month_name(month):
    import calendar
    return calendar.month_name[int(month)]


def get_advice_path(year, month):
    """Absolute path of the advice PDF, always under Config.UPLOAD_FOLDER."""
    return os.path.join(
        Config.UPLOAD_FOLDER, 'bank_advice', str(year), f"{int(month):02d}",
        f"bank_advice_{year}_{int(month):02d}.pdf",
    )


def collect_advice_rows(month, year):
    """
    Return ``(rows, skipped)`` for the month.

    rows    - list of dicts ready for the PDF (employees who can be paid)
    skipped - list of ``(employee, reason)`` for employees left out
    """
    payrolls = (
        Payroll.query.filter_by(month=month, year=year)
        .join(Employee, Payroll.employee_id == Employee.id)
        .filter(Employee.status == 'active')
        .order_by(Employee.employee_id)
        .all()
    )
    rows, skipped = [], []
    for payroll in payrolls:
        emp = payroll.employee
        net = round(float(payroll.net_salary or 0), 2)
        if net <= 0:
            skipped.append((emp, 'Net salary is zero'))
            continue
        account = (emp.bank_account_number or '').strip()
        ifsc = (emp.bank_ifsc_code or '').strip().upper()
        if not account:
            skipped.append((emp, 'Bank account number missing'))
            continue
        if not ifsc:
            skipped.append((emp, 'IFSC code missing'))
            continue
        rows.append({
            'employee_id': emp.employee_id,
            'name': emp.name,
            'bank_name': (emp.bank_name or '').strip(),
            'account_number': account,
            'ifsc': ifsc,
            'phone': emp.phone or '',
            'email': emp.email or '',
            'net_salary': net,
        })
    return rows, skipped


# ----------------------------------------------------------------------
# PDF
# ----------------------------------------------------------------------

def generate_advice_pdf(rows, month, year, company, settings, output_path):
    """Render the advice PDF (A4 landscape) to ``output_path``."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )
    from pdf_generator import PDFGenerator, _make_numbered_canvas, _tc

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    pagesize = landscape(A4)
    title = f"Bank Payment Advice - {_month_name(month)} {year}"
    company_name = company.company_name if company else 'Company'

    doc = SimpleDocTemplate(
        output_path, pagesize=pagesize,
        leftMargin=12 * mm, rightMargin=12 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=title, author=company_name,
    )
    base = getSampleStyleSheet()
    h1 = ParagraphStyle('h1', parent=base['Title'], fontName='Helvetica-Bold', fontSize=16,
                        textColor=_tc('ink'), alignment=0, spaceAfter=2)
    sub = ParagraphStyle('sub', parent=base['Normal'], fontSize=9, textColor=_tc('muted'), leading=12)
    body = ParagraphStyle('body', parent=base['Normal'], fontSize=9, leading=12)
    cell = ParagraphStyle('cell', parent=base['Normal'], fontSize=7.5, leading=9)

    total = round(sum(r['net_salary'] for r in rows), 2)
    words = PDFGenerator()._amount_to_words(total)

    story = [Paragraph(company_name, h1), Paragraph(title, sub), Spacer(1, 4 * mm)]

    # --- To / From / Debit account block -------------------------------
    bank_to = settings.bank_recipient_name or 'The Manager, Payroll Services'
    debit_parts = [p for p in (
        settings.company_bank_name,
        f"A/c {settings.company_bank_account_number}" if settings.company_bank_account_number else None,
        f"IFSC {settings.company_bank_ifsc_code}" if settings.company_bank_ifsc_code else None,
    ) if p]
    info = [
        [Paragraph(f"<b>To:</b> {bank_to}", body),
         Paragraph(f"<b>Date:</b> {datetime.now().strftime('%d %b %Y')}", body)],
        [Paragraph("<b>Debit account:</b> " + (', '.join(debit_parts) or 'As per bank records'), body),
         Paragraph(f"<b>Salary month:</b> {_month_name(month)} {year}", body)],
        [Paragraph("Please credit the NET salary below to the beneficiary accounts "
                   "and debit our account for the total.", body), ''],
    ]
    info_table = Table(info, colWidths=[190 * mm, 80 * mm])
    info_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('SPAN', (0, 2), (1, 2)),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    story += [info_table, Spacer(1, 4 * mm)]

    # --- Beneficiary table ----------------------------------------------
    header = ['Sr', 'Emp ID', 'Beneficiary Name', 'Bank', 'Account No.', 'IFSC',
              'Mobile', 'Email', 'Net Salary (Rs.)']
    data = [header]
    for i, r in enumerate(rows, 1):
        data.append([
            str(i), r['employee_id'],
            Paragraph(r['name'], cell), Paragraph(r['bank_name'] or '-', cell),
            r['account_number'], r['ifsc'], r['phone'],
            Paragraph(r['email'], cell), f"{r['net_salary']:,.2f}",
        ])
    data.append(['', '', '', '', '', '', '', Paragraph('<b>TOTAL</b>', cell), f"{total:,.2f}"])

    widths = [9, 20, 44, 34, 36, 26, 24, 52, 28]
    scale = (270 * mm) / (sum(widths) * mm)
    table = Table(data, colWidths=[w * mm * scale for w in widths], repeatRows=1)
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), _tc('ink')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 7.5),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (-1, 0), (-1, -1), 'RIGHT'),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.4, _tc('line')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('BACKGROUND', (0, -1), (-1, -1), _tc('tint')),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTNAME', (-1, 1), (-1, -1), 'Helvetica-Bold'),
    ]
    for i in range(2, len(data) - 1, 2):
        style.append(('BACKGROUND', (0, i), (-1, i), _tc('zebra')))
    table.setStyle(TableStyle(style))
    story.append(table)

    # Keep totals + signature together so the signature never sits alone on a page.
    story.append(KeepTogether([
        Spacer(1, 4 * mm),
        Paragraph(f"<b>Total beneficiaries:</b> {len(rows)} &nbsp;&nbsp; "
                  f"<b>Total amount:</b> Rs. {total:,.2f}", body),
        Paragraph(f"<b>Amount in words:</b> {words}", body),
        Spacer(1, 14 * mm),
        Paragraph("Authorised Signatory<br/>" + company_name, body),
    ]))

    doc.build(story, canvasmaker=_make_numbered_canvas(company_name, title, pagesize))
    return output_path


# ----------------------------------------------------------------------
# send bank advice
# ----------------------------------------------------------------------

def send_bank_advice(month, year, force=False, email_service=None):
    """Thread-safe wrapper: a second concurrent call is refused instead of racing."""
    if not _send_lock.acquire(blocking=False):
        return {'success': False, 'in_progress': True,
                'message': 'Bank advice is already being sent - please wait a moment and check the status above.'}
    try:
        return _send_bank_advice_locked(month, year, force, email_service)
    except Exception:
        db.session.rollback()
        raise
    finally:
        _send_lock.release()


def _send_bank_advice_locked(month, year, force=False, email_service=None):
    """
    Generate the advice PDF for (month, year) and email it to the bank.

    Returns a dict: ``success``, ``message``, plus ``count``, ``total``,
    ``skipped`` (list of "Name - reason") and ``path`` when a PDF was built.
    ``force=False`` refuses to e-mail the bank again if this month was
    already sent (the automatic run uses that); the manual button passes
    ``force=True``.
    """
    settings = PayrollSettings.get_settings()
    if not settings.bank_advice_enabled:
        return {'success': False, 'message': 'Bank payment advice is not enabled in Payroll Settings.'}

    recipients = parse_emails(settings.bank_recipient_emails)
    if not recipients:
        return {'success': False, 'message': 'No valid bank email address saved in Payroll Settings.'}

    record = BankAdvice.query.filter_by(month=month, year=year).first()
    if record and record.status == 'sent' and not force:
        return {'success': False, 'already_sent': True,
                'message': f'Bank advice for {month}/{year} was already sent on '
                           f'{record.sent_at:%d %b %Y %H:%M}.'}

    rows, skipped = collect_advice_rows(month, year)
    skipped_text = [f"{e.name} ({e.employee_id}) - {why}" for e, why in skipped]
    if not rows:
        return {'success': False, 'skipped': skipped_text,
                'message': 'No employee with calculated payroll and complete bank details for this month.'}

    company = CompanySettings.get_settings()
    path = get_advice_path(year, month)
    generate_advice_pdf(rows, month, year, company, settings, path)
    total = round(sum(r['net_salary'] for r in rows), 2)

    if record is None:
        record = BankAdvice(month=month, year=year, status='generated')
        db.session.add(record)
        try:
            db.session.commit()   # claim the month's row before any email goes out
        except Exception:         # IntegrityError: another process created it first
            db.session.rollback()
            record = BankAdvice.query.filter_by(month=month, year=year).first()
            if record is None:
                raise
    record.file_path = path
    record.employee_count = len(rows)
    record.total_amount = total
    record.skipped_count = len(skipped)
    record.sent_to = ', '.join(recipients)
    record.generated_at = datetime.now()

    if email_service is None:
        from services.app_services import get_services
        _, _, email_service, _ = get_services()

    debit = ', '.join(p for p in (
        settings.company_bank_name,
        f"A/c {settings.company_bank_account_number}" if settings.company_bank_account_number else None,
        f"IFSC {settings.company_bank_ifsc_code}" if settings.company_bank_ifsc_code else None,
    ) if p) or None

    failures = []
    for address in recipients:
        result = email_service.send_bank_advice(
            address, settings.bank_recipient_name, month, year, path,
            len(rows), total, debit_account=debit,
        )
        if not result.get('success'):
            failures.append(f"{address}: {result.get('message')}")

    if failures and len(failures) == len(recipients):
        record.status = 'failed'
        record.error_message = '; '.join(failures)[:500]
        db.session.commit()
        return {'success': False, 'path': path, 'count': len(rows), 'total': total,
                'skipped': skipped_text,
                'message': 'Could not email the bank: ' + '; '.join(failures)}

    record.status = 'sent'
    record.sent_at = datetime.now()
    record.error_message = ('; '.join(failures)[:500]) if failures else None
    db.session.commit()
    message = f'Bank advice for {len(rows)} employee(s), total Rs. {total:,.2f}, sent to {", ".join(recipients)}.'
    if failures:
        message += ' Some recipients failed: ' + '; '.join(failures)
    return {'success': True, 'message': message, 'path': path, 'count': len(rows),
            'total': total, 'skipped': skipped_text}


# ----------------------------------------------------------------------
# employee payslips with the "salary will be credited" note
# ----------------------------------------------------------------------

def credit_notice_text(settings=None):
    """Phrase for the payslip email, or None when the feature is off."""
    settings = settings or PayrollSettings.get_settings()
    if not settings.bank_advice_enabled:
        return None
    return (settings.salary_credit_days_text or '1 to 2 working days').strip()


def ensure_payslip(payroll, employee):
    """Generate (or regenerate) the password-protected payslip PDF; returns its path."""
    from pdf_generator import PDFGenerator, generate_payslip_password
    from scheduler_service import get_payslip_full_path, get_payslip_storage_subdir

    filename = f"payslip_{employee.employee_id}_{payroll.year}_{payroll.month}.pdf"
    path = get_payslip_full_path(payroll.year, payroll.month, filename,
                                 base_subdir=get_payslip_storage_subdir())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        os.remove(path)
    PDFGenerator().generate_payslip(
        payroll, employee, CompanySettings.get_settings(), path,
        password=generate_payslip_password(employee),
    )
    payroll.payslip_generated = True
    payroll.payslip_path = path
    db.session.commit()
    return path


def email_payslips_with_credit_notice(month, year, only_unsent=True, email_service=None):
    """
    Email each employee's payslip with the "credited in 1-2 days" note.
    Returns ``(sent, failed)`` where failed is a list of "Name: reason".
    """
    from pdf_generator import generate_payslip_password

    settings = PayrollSettings.get_settings()
    notice = credit_notice_text(settings)
    if email_service is None:
        from services.app_services import get_services
        _, _, email_service, _ = get_services()

    sent, failed = 0, []
    payrolls = (
        Payroll.query.filter_by(month=month, year=year)
        .join(Employee, Payroll.employee_id == Employee.id)
        .filter(Employee.status == 'active').all()
    )
    for payroll in payrolls:
        if only_unsent and payroll.email_sent:
            continue
        emp = payroll.employee
        try:
            path = payroll.payslip_path
            if not payroll.payslip_generated or not path or not os.path.exists(path):
                path = ensure_payslip(payroll, emp)
            result = email_service.send_payslip(
                emp.email, emp.name, path, month, year,
                pdf_password=generate_payslip_password(emp),
                credit_notice=notice,
            )
            if result.get('success'):
                payroll.email_sent = True
                db.session.commit()
                sent += 1
            else:
                failed.append(f"{emp.name}: {result.get('message')}")
        except Exception as exc:  # one bad employee must not stop the rest
            logger.exception("Payslip email failed for %s", emp.name)
            failed.append(f"{emp.name}: {exc}")
    return sent, failed
