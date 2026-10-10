try:
    from reportlab.lib.pagesizes import letter, A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    reportlab_available = True
except ImportError:
    reportlab_available = False

try:
    import pikepdf
    pikepdf_available = True
except ImportError:
    pikepdf_available = False

try:
    import qrcode
    qrcode_available = True
except ImportError:
    qrcode_available = False

import os
import re
import logging
from datetime import datetime
from config import Config
import io

logger = logging.getLogger(__name__)


# ======================================================================
# Shared "professional" report theme (used by Admin + Employee reports)
# ======================================================================
THEME = {
    'ink': '#1B2437',        # headings / header band
    'accent': '#2563EB',     # accent rule + highlights
    'muted': '#6B7280',      # secondary text
    'line': '#E3E7ED',       # hairline borders
    'zebra': '#F6F8FB',      # alternate row
    'tint': '#EEF3FF',       # light accent tint (cards)
    'good': '#157347',
    'bad': '#B42318',
    'warn': '#B45309',
}


def _tc(key):
    from reportlab.lib import colors as _c
    return _c.HexColor(THEME[key])


def _pro_table_style(font_size=7.5, align='CENTER', left_cols=(0,), pad=5):
    """Clean modern table: dark header, zebra rows, hairline horizontal rules."""
    from reportlab.platypus import TableStyle
    from reportlab.lib import colors as _c
    cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), _tc('ink')),
        ('TEXTCOLOR', (0, 0), (-1, 0), _c.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), font_size),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), font_size),
        ('TEXTCOLOR', (0, 1), (-1, -1), _tc('ink')),
        ('ALIGN', (0, 0), (-1, -1), align),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), pad),
        ('BOTTOMPADDING', (0, 0), (-1, -1), pad),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [_c.white, _tc('zebra')]),
        ('LINEBELOW', (0, 1), (-1, -1), 0.4, _tc('line')),
        ('LINEBELOW', (0, -1), (-1, -1), 0.8, _tc('line')),
        ('BOX', (0, 0), (-1, -1), 0.4, _tc('line')),
    ]
    for c in left_cols:
        cmds.append(('ALIGN', (c, 0), (c, -1), 'LEFT'))
    return TableStyle(cmds)


def _logo_image(path, max_w=130, max_h=52):
    """Logo scaled to fit the box while keeping its original aspect ratio."""
    from reportlab.platypus import Image
    from reportlab.lib.utils import ImageReader
    iw, ih = ImageReader(path).getSize()
    k = min(max_w / float(iw), max_h / float(ih))
    return Image(path, width=iw * k, height=ih * k, hAlign='RIGHT')


def _fit(widths, total):
    """Scale column widths so the table spans the full content width."""
    k = total / float(sum(widths))
    return [w * k for w in widths]


def _status_cmds(data, status_col, late_col=None):
    """Per-cell text colours for Status / Late columns of attendance rows."""
    cmds = []
    colour_for = {'PRESENT': 'good', 'ABSENT': 'bad', 'HALF DAY': 'warn', 'HALF_DAY': 'warn'}
    for r in range(1, len(data)):
        st = str(data[r][status_col]).strip().upper()
        key = colour_for.get(st)
        if key:
            cmds.append(('TEXTCOLOR', (status_col, r), (status_col, r), _tc(key)))
            cmds.append(('FONTNAME', (status_col, r), (status_col, r), 'Helvetica-Bold'))
        elif st in ('-', 'PENDING', ''):
            cmds.append(('TEXTCOLOR', (status_col, r), (status_col, r), _tc('muted')))
        if late_col is not None and str(data[r][late_col]).strip() == 'Yes':
            cmds.append(('TEXTCOLOR', (late_col, r), (late_col, r), _tc('bad')))
            cmds.append(('FONTNAME', (late_col, r), (late_col, r), 'Helvetica-Bold'))
    return cmds


def _section_heading(title, width):
    """Section title with an accent bar on the left and a hairline underneath."""
    from reportlab.platypus import Table, TableStyle, Paragraph
    from reportlab.lib.styles import ParagraphStyle
    st = ParagraphStyle('ProSection', fontName='Helvetica-Bold', fontSize=11,
                        leading=14, textColor=_tc('ink'))
    t = Table([[Paragraph(title, st)]], colWidths=[width], hAlign='LEFT')
    t.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LINEBEFORE', (0, 0), (0, 0), 3, _tc('accent')),
        ('LINEBELOW', (0, 0), (-1, -1), 0.5, _tc('line')),
    ]))
    t.spaceBefore = 10
    t.spaceAfter = 6
    t.keepWithNext = True
    return t


def _kpi_cards(items, width):
    """Row of KPI cards. items = [(label, value, colour_key_or_None), ...]"""
    from reportlab.platypus import Table, TableStyle, Paragraph
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    lab_style = ParagraphStyle('KpiLab', fontName='Helvetica', fontSize=7.5, leading=10,
                               alignment=TA_CENTER, textColor=_tc('muted'))
    n = len(items)
    gap = 6
    cell_w = (width - gap * (n - 1)) / n
    row, widths = [], []
    for i, (label, value, col) in enumerate(items):
        val_style = ParagraphStyle('KpiVal', fontName='Helvetica-Bold', fontSize=18, leading=22,
                                   alignment=TA_CENTER, textColor=_tc(col or 'ink'))
        card = Table([[Paragraph(str(value), val_style)], [Paragraph(label.upper(), lab_style)]],
                     colWidths=[cell_w])
        card.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), _tc('tint')),
            ('BOX', (0, 0), (-1, -1), 0.4, _tc('line')),
            ('LINEABOVE', (0, 0), (-1, 0), 2, _tc(col or 'accent')),
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 0),
            ('TOPPADDING', (0, 1), (-1, 1), 2),
            ('BOTTOMPADDING', (0, 1), (-1, 1), 8),
        ]))
        row.append(card)
        widths.append(cell_w)
        if i < n - 1:
            row.append('')
            widths.append(gap)
    outer = Table([row], colWidths=widths, hAlign='LEFT')
    outer.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    return outer


def _info_card(pairs, width, cols=3):
    """Compact label-over-value block (applied filters, employee details)."""
    from reportlab.platypus import Table, TableStyle, Paragraph
    from reportlab.lib.styles import ParagraphStyle
    lab = ParagraphStyle('InfoLab', fontName='Helvetica', fontSize=7, leading=9, textColor=_tc('muted'))
    val = ParagraphStyle('InfoVal', fontName='Helvetica-Bold', fontSize=9, leading=12, textColor=_tc('ink'))
    cells = [[Paragraph(l.upper(), lab), Paragraph(str(v), val)] for l, v in pairs]
    data = []
    for i in range(0, len(cells), cols):
        chunk = cells[i:i + cols]
        while len(chunk) < cols:
            chunk.append('')
        data.append(chunk)
    t = Table(data, colWidths=[width / cols] * cols, hAlign='LEFT')
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), _tc('zebra')),
        ('BOX', (0, 0), (-1, -1), 0.4, _tc('line')),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    return t


def _make_numbered_canvas(company_name, report_title, pagesize):
    """Canvas class drawing the accent top bar + footer with 'Page X of Y'."""
    from reportlab.pdfgen import canvas as _canvas
    from datetime import datetime as _dt
    stamp = _dt.now().strftime('%d %b %Y, %H:%M')

    class NumberedCanvas(_canvas.Canvas):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for st in self._saved:
                self.__dict__.update(st)
                self._decorate(total)
                super().showPage()
            super().save()

        def _decorate(self, total):
            w, h = pagesize
            self.saveState()
            self.setFillColor(_tc('ink'))
            self.rect(0, h - 10, w, 10, stroke=0, fill=1)
            self.setFillColor(_tc('accent'))
            self.rect(0, h - 10, 90, 10, stroke=0, fill=1)
            self.setStrokeColor(_tc('line'))
            self.setLineWidth(0.5)
            self.line(30, 34, w - 30, 34)
            self.setFont('Helvetica', 7.5)
            self.setFillColor(_tc('muted'))
            self.drawString(30, 22, f"{company_name}  |  {report_title}")
            self.drawCentredString(w / 2, 22, f"Generated {stamp}")
            self.drawRightString(w - 30, 22, f"Page {self._pageNumber} of {total}")
            self.restoreState()

    return NumberedCanvas



def generate_payslip_password(employee):
    """
    Return the payslip PDF open-password for `employee`.

    1) Custom override (recommended): if the admin (or the employee, via
       their portal) has set a custom payslip password, it is stored
       encrypted-at-rest on Employee.payslip_password_override
       (see crypto_utils.py / models.py) and returned as-is, decrypted.

    2) Default, strengthened deterministic formula (used when no override
       is set): first 2 UPPERCASE letters of the employee's name + last 4
       digits of their phone number + DOB in DDMM format + last 2
       characters of their employee ID.
       e.g. name="Vaishnavi Lekawale", phone="9876543210",
            dob=1998-03-15, employee_id="EMP0001"  ->  "VA3210150301"

       This deliberately replaces the old "first 4 letters of name + DOB"
       rule: that scheme's only two inputs (name, birthdate) are commonly
       public information (social media, HR directories), making the
       8-character password guessable by someone who isn't the employee.
       Mixing in the phone number (not usually public) and part of the
       employee ID meaningfully raises the bar while keeping the password
       self-derivable by the employee - nothing has to be emailed or
       otherwise transmitted in-band (see email_service.send_payslip()).

    If the employee has no date of birth on file, falls back to their
    joining_date (always present, NOT NULL) so a password can still
    always be produced - a warning is logged so admins know to add a
    proper DOB for that employee.

    This function is pure from the caller's perspective (no DB writes)
    and deterministic given the employee's current data: calling it again
    for the same employee (with the same override / phone / DOB / name /
    ID) always yields the same password.
    """
    employee_label = getattr(employee, 'employee_id', None) or getattr(employee, 'id', '?')

    # --- 1) custom override takes priority ---
    encrypted_override = getattr(employee, 'payslip_password_override', None)
    if encrypted_override:
        try:
            import crypto_utils
            return crypto_utils.decrypt_str(encrypted_override)
        except Exception as e:
            logger.error(
                f"Could not decrypt custom payslip password override for "
                f"employee {employee_label} ({e}); falling back to the "
                f"default generated password."
            )

    # --- 2) default strengthened formula ---
    # date-of-birth component (DDMM)
    dob = getattr(employee, 'dob', None)
    if dob:
        ddmm = dob.strftime('%d%m')
    else:
        joining_date = getattr(employee, 'joining_date', None)
        if joining_date:
            ddmm = joining_date.strftime('%d%m')
            logger.warning(
                f"Employee {employee_label} has no DOB on file; using joining "
                f"date instead to build the payslip PDF password."
            )
        else:
            # Should not happen (joining_date is NOT NULL), but never let
            # password generation crash payslip creation.
            ddmm = '0101'
            logger.warning(
                f"Employee {employee_label} has neither DOB nor joining date "
                f"on file; using a placeholder date for the payslip PDF password."
            )

    # name component: first 2 uppercase letters (falls back to 'XX' if the
    # name has fewer than 2 alphabetic characters)
    raw_name = getattr(employee, 'name', '') or ''
    letters_only = re.sub(r'[^A-Za-z]', '', raw_name).upper()
    name_part = (letters_only[:2] or 'XX').ljust(2, 'X')

    # phone component: last 4 digits (falls back to '0000' if missing/short
    # - phone is NOT NULL in the schema, but never let this crash generation)
    raw_phone = re.sub(r'\D', '', getattr(employee, 'phone', '') or '')
    phone_part = raw_phone[-4:].rjust(4, '0') if raw_phone else '0000'

    # employee ID component: last 2 characters
    employee_id = re.sub(r'[^A-Z0-9]', '', (getattr(employee, 'employee_id', '') or '').upper())
    id_part = employee_id[-2:] if employee_id else 'XX'

    return f"{name_part}{phone_part}{ddmm}{id_part}"


def encrypt_pdf(input_path, output_path, user_password, owner_password=None):
    """
    Password-protect an existing PDF file on disk using pikepdf.

    Args:
        input_path: path to the unprotected PDF (as written by ReportLab).
        output_path: path to write the encrypted PDF to. May be the same
            as input_path - pikepdf reads the source fully into memory
            before writing, so encrypting "in place" (write to a temp
            file, then move over the original) is safe; see
            PDFGenerator.generate_payslip for how this is done safely
            with a temp file.
        user_password: the password required to OPEN/view the PDF. This
            is the "employee-facing" password built by
            generate_payslip_password().
        owner_password: the password that grants full permissions
            (printing, editing, etc.). Defaults to user_password so a
            single password unlocks everything - appropriate for a
            single-recipient payslip where there's no separate "admin"
            audience for the PDF itself.

    Uses AES-256 (R=6) encryption via pikepdf/QPDF. Permissions are
    locked down to viewing + high-res printing only; copying, editing,
    annotating, and re-assembling the document are disabled.

    Raises pikepdf.PasswordError / OSError / etc. on failure - the caller
    is responsible for deciding how to handle a failed encryption attempt
    (e.g. abort payslip generation rather than silently serving an
    unprotected file).
    """
    if not pikepdf_available:
        raise ImportError(
            "pikepdf is not installed. Run 'pip install pikepdf' to enable "
            "password-protected payslip PDFs."
        )

    if owner_password is None:
        owner_password = user_password

    permissions = pikepdf.Permissions(
        accessibility=True,      # screen readers may still extract text
        extract=False,           # no copy/paste or text extraction
        modify_annotation=False,
        modify_assembly=False,
        modify_form=False,
        modify_other=False,
        print_lowres=True,
        print_highres=True,
    )

    encryption = pikepdf.Encryption(
        owner=owner_password,
        user=user_password,
        R=6,          # PDF 2.0 / AES-256 revision
        allow=permissions,
        aes=True,
    )

    with pikepdf.open(input_path) as pdf:
        pdf.save(output_path, encryption=encryption)

    return True


class PDFGenerator:
    def __init__(self):
        if not reportlab_available:
            raise ImportError("ReportLab is not installed. PDF generation will not work.")
        self.styles = getSampleStyleSheet()
        self.company_name = Config.COMPANY_NAME
        self.company_logo = Config.COMPANY_LOGO
    
    def _format_currency(self, amount):
        """Format currency with Rs. prefix, thousand separators, and 2 decimals."""
        return f"Rs. {float(amount or 0.0):,.2f}"

    def _format_days(self, days):
        """Format day counts, showing one decimal only when needed."""
        value = float(days or 0.0)
        if value.is_integer():
            return str(int(value))
        return f"{value:.1f}"

    def generate_payslip(self, payroll, employee, company_settings, output_path, password=None):
        """
        Generate a one-page payslip PDF using persisted Payroll database values.

        Args:
            password: optional open-password. When provided, the PDF is
                built normally with ReportLab to a temporary file, then
                encrypted in place using pikepdf (see encrypt_pdf() above)
                so the final file at `output_path` is the password-
                protected version. When None (default), the PDF is
                written directly to `output_path` unprotected, exactly as
                before - fully backward compatible.
        """
        from payroll import (
            build_payslip_attendance_rows,
            build_payslip_earnings_rows,
            build_payslip_deduction_rows,
            get_payroll_field_value,
        )

        # If password protection is requested, build the ReportLab PDF to a
        # temporary, unprotected path first, then encrypt it with pikepdf
        # into the real output_path. This keeps the (large, existing)
        # ReportLab layout code below completely unaware of encryption.
        build_path = output_path
        if password:
            build_path = f"{output_path}.unprotected.tmp"

        doc = SimpleDocTemplate(
            build_path,
            pagesize=A4,
            rightMargin=30,
            leftMargin=30,
            topMargin=34,
            bottomMargin=30,
        )

        story = []
        W = A4[0] - 60  # content width

        style_company_name = ParagraphStyle(
            'CompName',
            parent=self.styles['Normal'],
            fontSize=15,
            leading=19,
            fontName='Helvetica-Bold',
            textColor=_tc('ink'),
            spaceAfter=4,
            wordWrap='CJK',
        )
        style_company_info = ParagraphStyle(
            'CompInfo',
            parent=self.styles['Normal'],
            fontName='Helvetica',
            fontSize=8,
            leading=11,
            textColor=_tc('muted'),
            spaceAfter=1,
        )
        style_section = ParagraphStyle(
            'SectionTitle',
            parent=self.styles['Normal'],
            fontSize=9,
            leading=11,
            fontName='Helvetica-Bold',
            textColor=_tc('ink'),
        )
        style_normal = ParagraphStyle(
            'CellNormal',
            parent=self.styles['Normal'],
            fontSize=8,
            leading=10,
        )
        style_bold = ParagraphStyle(
            'CellBold',
            parent=self.styles['Normal'],
            fontSize=8,
            leading=10,
            fontName='Helvetica-Bold',
        )
        style_center_bold = ParagraphStyle(
            'CenterBold',
            parent=self.styles['Normal'],
            fontSize=8,
            leading=11,
            fontName='Helvetica-Bold',
            alignment=TA_CENTER,
            textColor=colors.white,
        )
        style_title = ParagraphStyle(
            'TitleStyle',
            parent=self.styles['Normal'],
            fontSize=11,
            leading=13,
            fontName='Helvetica-Bold',
            alignment=TA_CENTER,
            textColor=colors.HexColor('#0B3D91'),
        )
        style_footer = ParagraphStyle(
            'FooterStyle',
            parent=self.styles['Normal'],
            fontSize=7,
            leading=9,
            alignment=TA_CENTER,
            textColor=colors.grey,
        )
        style_attendance_header = ParagraphStyle(
            'AttendanceHeader',
            parent=style_bold,
            fontSize=7,
            leading=9,
            alignment=TA_CENTER,
            fontName='Helvetica',
            textColor=_tc('muted'),
        )
        style_attendance_value = ParagraphStyle(
            'AttendanceValue',
            parent=style_normal,
            fontSize=8,
            leading=10,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold',
        )

        comp_name = (
            company_settings.company_name
            if company_settings and getattr(company_settings, 'company_name', None)
            else self.company_name
        )
        comp_addr = (
            company_settings.company_address
            if company_settings and getattr(company_settings, 'company_address', None)
            else '123 Business Street, City, Country'
        )
        comp_phone = (
            company_settings.company_phone
            if company_settings and getattr(company_settings, 'company_phone', None)
            else '+91 9876543210'
        )
        comp_email = (
            company_settings.company_email
            if company_settings and getattr(company_settings, 'company_email', None)
            else 'hr@company.com'
        )
        comp_web = (
            company_settings.company_website
            if company_settings and getattr(company_settings, 'company_website', None)
            else 'www.company.com'
        )
        logo_path = (
            company_settings.company_logo
            if company_settings and getattr(company_settings, 'company_logo', None)
            else self.company_logo
        )

        company_lines = [
            Paragraph(comp_name.upper(), style_company_name),
            Paragraph(comp_addr.replace('\n', '<br/>'), style_company_info),
            Paragraph(
                f'Phone: {comp_phone} &nbsp;&nbsp;|&nbsp;&nbsp; Email: {comp_email}',
                style_company_info,
            ),
            Paragraph(f'Website: {comp_web}', style_company_info),
        ]

        logo_cell = ''
        if logo_path and os.path.exists(logo_path):
            try:
                logo_cell = _logo_image(logo_path, max_w=95, max_h=60)
                logo_cell.hAlign = 'LEFT'
            except Exception:
                logo_cell = ''

        header_table = Table(
            [[logo_cell, company_lines]],
            colWidths=[1.45 * inch, W - 1.45 * inch],
        )
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ('LEFTPADDING', (0, 0), (0, 0), 0),
            ('LEFTPADDING', (1, 0), (1, 0), 6),
            ('LINEBELOW', (0, 0), (-1, 0), 1.2, _tc('accent')),
        ]))
        story.append(header_table)
        story.append(Spacer(1, 0.12 * inch))

        month_names = [
            'January', 'February', 'March', 'April', 'May', 'June',
            'July', 'August', 'September', 'October', 'November', 'December',
        ]
        period = f"{month_names[payroll.month - 1]} {payroll.year}"

        style_title_l = ParagraphStyle('PayslipTitleL', parent=self.styles['Normal'], fontSize=12,
                                       leading=15, fontName='Helvetica-Bold', textColor=colors.white)
        style_title_r = ParagraphStyle('PayslipTitleR', parent=self.styles['Normal'], fontSize=9,
                                       leading=15, fontName='Helvetica', textColor=colors.white,
                                       alignment=TA_RIGHT)
        title_table = Table(
            [[Paragraph('PAYSLIP', style_title_l), Paragraph(period.upper(), style_title_r)]],
            colWidths=[W * 0.5, W * 0.5],
        )
        title_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), _tc('ink')),
            ('LINEBEFORE', (0, 0), (0, 0), 4, _tc('accent')),
            ('LEFTPADDING', (0, 0), (-1, -1), 12),
            ('RIGHTPADDING', (0, 0), (-1, -1), 12),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(title_table)
        story.append(Spacer(1, 0.1 * inch))

        employee_table = Table([
            [
                Paragraph('Employee ID', style_bold),
                Paragraph(employee.employee_id, style_normal),
                Paragraph('Department', style_bold),
                Paragraph(employee.department or 'N/A', style_normal),
            ],
            [
                Paragraph('Employee Name', style_bold),
                Paragraph(employee.name, style_normal),
                Paragraph('Designation', style_bold),
                Paragraph(employee.designation or 'N/A', style_normal),
            ],
            [
                Paragraph('Location', style_bold),
                Paragraph(
                    employee.office_location if getattr(employee, 'office_location', None) else 'N/A',
                    style_normal,
                ),
                Paragraph('Pay Period', style_bold),
                Paragraph(period, style_normal),
            ],
            [
                Paragraph('PAN Number', style_bold),
                Paragraph(getattr(employee, 'pan_number', None) or 'N/A', style_normal),
                Paragraph('UAN Number', style_bold),
                Paragraph(getattr(employee, 'uan_number', None) or 'N/A', style_normal),
            ],
            [
                Paragraph('PF Account No.', style_bold),
                Paragraph(getattr(employee, 'pf_number', None) or 'N/A', style_normal),
                '', '',
            ],
        ], colWidths=_fit([1.3, 2.35, 1.3, 2.35], W))
        employee_table.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 0.4, _tc('line')),
            ('LINEBELOW', (0, 0), (-1, -2), 0.4, _tc('line')),
            ('BACKGROUND', (0, 0), (0, -1), _tc('zebra')),
            ('BACKGROUND', (2, 0), (2, 3), _tc('zebra')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('SPAN', (1, 4), (3, 4)),
        ]))
        story.append(employee_table)
        story.append(Spacer(1, 0.08 * inch))

        story.append(_section_heading('Attendance Summary', W))

        attendance_rows = build_payslip_attendance_rows(payroll)
        attendance_headers = [Paragraph(label.upper(), style_attendance_header) for label, _ in attendance_rows]
        attendance_values = [Paragraph(value, style_attendance_value) for _, value in attendance_rows]
        col_width = W / len(attendance_rows)

        attendance_table = Table(
            [attendance_headers, attendance_values],
            colWidths=[col_width] * len(attendance_rows),
        )
        attendance_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), _tc('tint')),
            ('BOX', (0, 0), (-1, -1), 0.4, _tc('line')),
            ('LINEAFTER', (0, 0), (-2, -1), 0.4, colors.white),
            ('LINEABOVE', (0, 0), (-1, 0), 2, _tc('accent')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 3),
            ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(attendance_table)
        story.append(Spacer(1, 0.14 * inch))

        earnings_rows = build_payslip_earnings_rows(payroll)
        deduction_rows = build_payslip_deduction_rows(payroll)

        max_rows = max(len(earnings_rows), len(deduction_rows))
        # Pad BEFORE the total row so both "Total" lines share the last row.
        earnings_rows = earnings_rows[:-1] + [(' ', 0.0, None)] * (max_rows - len(earnings_rows)) + earnings_rows[-1:]
        deduction_rows = deduction_rows[:-1] + [(' ', 0.0)] * (max_rows - len(deduction_rows)) + deduction_rows[-1:]

        style_hdr_l = ParagraphStyle('HdrL', parent=style_center_bold, alignment=TA_LEFT)
        style_hdr_r = ParagraphStyle('HdrR', parent=style_center_bold, alignment=TA_RIGHT)
        style_amt = ParagraphStyle('Amt', parent=style_normal, alignment=TA_RIGHT)
        style_amt_b = ParagraphStyle('AmtB', parent=style_bold, alignment=TA_RIGHT)
        salary_table_data = [
            [
                Paragraph('EARNINGS', style_hdr_l),
                Paragraph('AMOUNT', style_hdr_r),
                Paragraph('DEDUCTIONS', style_hdr_l),
                Paragraph('AMOUNT', style_hdr_r),
            ]
        ]

        was_prorated = False

        for (earn_label, earn_amount, earn_full_amount), (ded_label, ded_amount) in zip(earnings_rows, deduction_rows):
            is_earn_total = earn_label == 'Total Gross Earnings'
            is_ded_total = ded_label == 'Total Deductions'
            earn_style = style_bold if is_earn_total else style_normal
            ded_style = style_bold if is_ded_total else style_normal

            earn_amount_cell = ''
            if earn_label.strip():
                earn_amount_cell = self._format_currency(earn_amount)
                # Show the employee's original/full monthly figure alongside
                # the earned (pro-rated) amount whenever they differ - i.e.
                # this payroll covered fewer working days than a full month.
                if (
                    earn_full_amount is not None
                    and round(float(earn_full_amount), 2) != round(float(earn_amount), 2)
                ):
                    was_prorated = True
                    earn_amount_cell += (
                        "<br/><font size=6 color='grey'>(Full: "
                        f"{self._format_currency(earn_full_amount)})</font>"
                    )
            ded_amount_cell = (
                self._format_currency(ded_amount)
                if ded_label.strip()
                else ''
            )
            salary_table_data.append([
                Paragraph(earn_label, earn_style),
                Paragraph(earn_amount_cell, style_amt_b if is_earn_total else style_amt),
                Paragraph(ded_label, ded_style),
                Paragraph(ded_amount_cell, style_amt_b if is_ded_total else style_amt),
            ])

        salary_table = Table(
            salary_table_data,
            colWidths=_fit([2.0, 1.65, 2.0, 1.65], W),
        )
        n_rows = len(salary_table_data)
        salary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), _tc('ink')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('BOX', (0, 0), (-1, -1), 0.4, _tc('line')),
            ('LINEBELOW', (0, 1), (-1, -2), 0.4, _tc('line')),
            ('LINEAFTER', (1, 0), (1, -1), 0.8, _tc('line')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, _tc('zebra')]),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (2, 0), (2, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('ALIGN', (3, 0), (3, 0), 'RIGHT'),
            ('BACKGROUND', (0, -1), (-1, -1), _tc('tint')),
            ('LINEABOVE', (0, -1), (-1, -1), 1, _tc('ink')),
        ]))
        story.append(_section_heading('Earnings & Deductions', W))
        story.append(salary_table)

        if was_prorated:
            story.append(Spacer(1, 0.03 * inch))
            story.append(Paragraph(
                "* \"Full\" is the employee's original monthly amount; the earned "
                "figure above is pro-rated for a shortened working-days period.",
                style_footer,
            ))

        story.append(Spacer(1, 0.08 * inch))

        net_salary = get_payroll_field_value(payroll, 'net_salary')
        overtime_bonus = get_payroll_field_value(payroll, 'overtime_bonus')
        employer_pf = get_payroll_field_value(payroll, 'employer_pf')
        net_ctc = get_payroll_field_value(payroll, 'net_ctc')

        net_salary_words = self._amount_to_words(net_salary)
        summary_parts = [
            f'<b>Net Pay In Words:</b> Rupees {net_salary_words} Only',
        ]
        if overtime_bonus:
            summary_parts.append(
                f'<b>Overtime Bonus:</b> {self._format_currency(overtime_bonus)}'
            )
        if employer_pf or net_ctc:
            ctc_parts = []
            if employer_pf:
                ctc_parts.append(f'Employer PF: {self._format_currency(employer_pf)}')
            if net_ctc:
                ctc_parts.append(f'Net CTC: {self._format_currency(net_ctc)}')
            summary_parts.append(f"<b>{' &nbsp;&nbsp;|&nbsp;&nbsp; '.join(ctc_parts)}</b>")

        net_table = Table([
            [
                Paragraph('<br/>'.join(summary_parts), style_normal),
                Paragraph(
                    f"<font size=8 color='#B9C4DA'>NET PAY</font><br/><b>{self._format_currency(net_salary)}</b>",
                    ParagraphStyle(
                        'NetPay',
                        parent=style_bold,
                        fontSize=16,
                        leading=20,
                        alignment=TA_RIGHT,
                        textColor=colors.white,
                    ),
                ),
            ]
        ], colWidths=[W * 0.62, W * 0.38])
        net_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, 0), _tc('tint')),
            ('BACKGROUND', (1, 0), (1, 0), _tc('ink')),
            ('BOX', (0, 0), (-1, -1), 0.4, _tc('line')),
            ('LINEBEFORE', (0, 0), (0, 0), 4, _tc('accent')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 12),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ('LEFTPADDING', (0, 0), (-1, -1), 12),
            ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ]))
        story.append(net_table)
        story.append(Spacer(1, 0.06 * inch))
        story.append(
            Paragraph(
                '* This payslip is computer generated and does not require a signature. *',
                style_footer,
            )
        )

        def _payslip_page(canvas, doc_):
            w, h = A4
            canvas.saveState()
            canvas.setFillColor(_tc('ink'))
            canvas.rect(0, h - 10, w, 10, stroke=0, fill=1)
            canvas.setFillColor(_tc('accent'))
            canvas.rect(0, h - 10, 90, 10, stroke=0, fill=1)
            canvas.setStrokeColor(_tc('line'))
            canvas.setLineWidth(0.5)
            canvas.line(30, 30, w - 30, 30)
            canvas.setFont('Helvetica', 7)
            canvas.setFillColor(_tc('muted'))
            canvas.drawString(30, 19, comp_name)
            canvas.drawRightString(w - 30, 19, f"Payslip - {period}")
            canvas.restoreState()

        # Guarantee a single page: if the content is ever taller than the page
        # (long address, many rows, wrapped labels) it is scaled down to fit
        # instead of spilling onto a second page.
        from reportlab.platypus import KeepInFrame
        doc.build(
            [KeepInFrame(doc.width, doc.height, story, mode='shrink')],
            onFirstPage=_payslip_page, onLaterPages=_payslip_page,
        )

        if password:
            # Encrypt the just-built PDF (at build_path) into the real
            # output_path using pikepdf, then remove the temporary
            # unprotected file. If encryption fails for any reason, the
            # unprotected temp file is left in place and the exception
            # propagates - we never want to silently serve an unprotected
            # payslip when protection was explicitly requested.
            encrypt_pdf(build_path, output_path, user_password=password)
            os.remove(build_path)

        return output_path

    def _amount_to_words(self, amount):
        """Convert a currency amount (with paise) to words safely."""
        try:
            val = float(amount or 0.0)
            if val < 0:
                val = abs(val)  # Handle negative values safely
            rupees = int(val)
            paise = int(round((val - rupees) * 100))
            
            words = self._number_to_words(rupees)
            if paise > 0:
                words += f' and {self._number_to_words(paise)} Paise'
            return words
        except Exception:
            return "Zero"
    # def _amount_to_words(self, amount):
    #     """Convert a currency amount (with paise) to words."""
    #     rupees = int(amount)
    #     paise = int(round((float(amount) - rupees) * 100))
    #     words = self._number_to_words(rupees)
    #     if paise:
    #         words += f' and {self._number_to_words(paise)} Paise'
    #     return words
    
    def _number_to_words(self, num):
        """Convert number to words (Indian numbering system)
        
        Robust implementation that handles values from 0 to crores without IndexError.
        Supports Indian currency format: Lakhs, Thousands, Hundreds.
        """
        if num == 0:
            return "Zero"
        
        # Define word arrays with bounds checking
        ones = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine',
                'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen',
                'Seventeen', 'Eighteen', 'Nineteen']
        tens = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']
        
        def convert_less_than_thousand(n):
            """Convert numbers less than 1000 to words securely."""
            n = int(n)
            if n <= 0:
                return ''
            elif n < 20:
                return ones[n] if 0 <= n < len(ones) else str(n)
            elif n < 100:
                tens_idx = n // 10
                tens_word = tens[tens_idx] if 0 <= tens_idx < len(tens) else str(tens_idx)
                remainder = n % 10
                if remainder > 0 and remainder < len(ones):
                    return f"{tens_word} {ones[remainder]}"
                return tens_word
            else:  # 100 <= n < 1000
                hundreds_idx = n // 100
                hundreds_word = ones[hundreds_idx] if 0 <= hundreds_idx < len(ones) else str(hundreds_idx)
                remainder = n % 100
                if remainder > 0:
                    return f"{hundreds_word} Hundred and {convert_less_than_thousand(remainder)}"
                return f"{hundreds_word} Hundred"
        # def convert_less_than_thousand(n):
        #     """Convert numbers less than 1000 to words"""
        #     if n == 0:
        #         return ''
        #     elif n < 20:
        #         # Bounds check: ensure n is within ones array range
        #         if n < len(ones):
        #             return ones[n]
        #         else:
        #             return str(n)  # Fallback for unexpected values
        #     elif n < 100:
        #         # Bounds check: ensure tens index is valid
        #         tens_idx = n // 10
        #         if tens_idx < len(tens):
        #             tens_word = tens[tens_idx]
        #         else:
        #             tens_word = str(tens_idx)
        #         remainder = n % 10
        #         if remainder != 0 and remainder < len(ones):
        #             return tens_word + ' ' + ones[remainder]
        #         elif remainder != 0:
        #             return tens_word + ' ' + str(remainder)
        #         else:
        #             return tens_word
        #     else:  # n >= 100 and n < 1000
        #         # Bounds check: ensure hundreds index is valid
        #         hundreds_idx = n // 100
        #         if hundreds_idx < len(ones):
        #             hundreds_word = ones[hundreds_idx]
        #         else:
        #             hundreds_word = str(hundreds_idx)
        #         remainder = n % 100
        #         if remainder != 0:
        #             return hundreds_word + ' Hundred and ' + convert_less_than_thousand(remainder)
        #         else:
        #             return hundreds_word + ' Hundred'
        
        def convert_less_than_lakh(n):
            """Convert numbers less than 1 lakh (100,000) to words"""
            if n == 0:
                return ''
            elif n < 1000:
                return convert_less_than_thousand(n)
            else:
                thousands = n // 1000
                remainder = n % 1000
                thousands_word = convert_less_than_thousand(thousands)
                if remainder != 0:
                    return thousands_word + ' Thousand ' + convert_less_than_thousand(remainder)
                else:
                    return thousands_word + ' Thousand'
        
        def convert_less_than_crore(n):
            """Convert numbers less than 1 crore (10,000,000) to words"""
            if n == 0:
                return ''
            elif n < 100000:
                return convert_less_than_lakh(n)
            else:
                lakhs = n // 100000
                remainder = n % 100000
                lakhs_word = convert_less_than_thousand(lakhs)
                if remainder != 0:
                    return lakhs_word + ' Lakh ' + convert_less_than_lakh(remainder)
                else:
                    return lakhs_word + ' Lakh'
        
        def convert_less_than_arab(n):
            """Convert numbers less than 1 arab (100 crore) to words"""
            if n == 0:
                return ''
            elif n < 10000000:  # 1 crore
                return convert_less_than_crore(n)
            else:
                crores = n // 10000000
                remainder = n % 10000000
                crores_word = convert_less_than_thousand(crores)
                if remainder != 0:
                    return crores_word + ' Crore ' + convert_less_than_crore(remainder)
                else:
                    return crores_word + ' Crore'
        
        # Main conversion logic with proper scaling
        if num < 1000:
            return convert_less_than_thousand(num)
        elif num < 100000:  # Less than 1 lakh
            return convert_less_than_lakh(num)
        elif num < 10000000:  # Less than 1 crore
            return convert_less_than_crore(num)
        elif num < 1000000000:  # Less than 1 arab (100 crore)
            return convert_less_than_arab(num)
        else:
            # For very large numbers, handle recursively
            arab = num // 1000000000
            remainder = num % 1000000000
            arab_word = convert_less_than_thousand(arab)
            if remainder != 0:
                return arab_word + ' Arab ' + convert_less_than_arab(remainder)
            else:
                return arab_word + ' Arab'
    
    def generate_admin_reports_pdf(self, report_data, filters, output_path, company_settings=None):
        """Generate comprehensive Admin Reports PDF with all analytics sections
        
        This is a dedicated function for Admin Reports export, separate from
        the employee attendance report generator. It includes all sections:
        - Applied Filters
        - Summary
        - Department Analytics
        - Employee Summary
        - Employee Attendance Details
        - Rankings (Most Present, Most Absent, Most Half Day, Most Late, Highest/Lowest Hours)
        - Late Analysis
        - Daily Attendance Trend
        
        Args:
            report_data: Dictionary from AdminReportsService.generate_report_data()
            filters: Dictionary of applied filters
            output_path: Output PDF file path
            company_settings: Optional Settings object from database (uses Config fallback if None)
        """
        import logging
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
        from reportlab.platypus import PageBreak, KeepTogether, Table, LongTable, Paragraph, Spacer
        from reportlab.lib import colors
        logger = logging.getLogger(__name__)
        
        # Fetch company settings from database or use Config fallback
        if company_settings and hasattr(company_settings, 'company_name'):
            company_name = company_settings.company_name
            company_logo = company_settings.company_logo
        else:
            company_name = self.company_name
            company_logo = self.company_logo
        
        logger.info("=" * 60)
        logger.info("[ADMIN REPORT PDF] Starting PDF generation")
        logger.info(f"Company Name: {company_name}")
        logger.info(f"Company Logo: {company_logo}")
        logger.info(f"Filters: {filters}")
        logger.info(f"Summary: {report_data.get('summary')}")
        logger.info(f"Department Analytics count: {len(report_data.get('department_analytics', {}))}")
        logger.info(f"Employee Summary count: {len(report_data.get('employee_summary', []))}")
        logger.info(f"Attendance Records count: {len(report_data.get('attendances', []))}")
        logger.info(f"Rankings count: {len(report_data.get('rankings', {}))}")
        logger.info(f"Late Analysis count: {len(report_data.get('late_analysis', []))}")
        logger.info(f"Daily Trend count: {len(report_data.get('daily_trend', []))}")
        
        # Use landscape orientation for wide tables
        pagesize = landscape(A4)
        page_width, page_height = pagesize
        
        # Calculate available width (page width - margins)
        left_margin = 30
        right_margin = 30
        available_width = page_width - left_margin - right_margin  # ~781 points
        
        logger.info(f"Page width: {page_width:.2f} points")
        logger.info(f"Available width: {available_width:.2f} points")
        
        # Create document with proper margins (in points: 1 inch = 72 points)
        # Left/Right: 30 points (~0.42 inch), Top/Bottom: 35 points (~0.49 inch)
        doc = SimpleDocTemplate(
            output_path,
            pagesize=pagesize,
            rightMargin=right_margin,
            leftMargin=left_margin,
            topMargin=40,
            bottomMargin=50
        )
        
        story = []
        
        # STYLES
        title_style = ParagraphStyle(
            'AdminTitle',
            parent=self.styles['Heading1'],
            fontSize=14,
            textColor=colors.darkblue,
            alignment=TA_CENTER,
            spaceAfter=10,
            fontName='Helvetica-Bold'
        )
        
        subtitle_style = ParagraphStyle(
            'AdminSubtitle',
            parent=self.styles['Heading2'],
            fontSize=11,
            textColor=colors.darkblue,
            alignment=TA_CENTER,
            spaceAfter=15,
            fontName='Helvetica-Bold'
        )
        
        section_header_style = ParagraphStyle(
            'SectionHeader',
            parent=self.styles['Heading2'],
            fontSize=12,
            textColor=colors.darkblue,
            spaceBefore=12,
            spaceAfter=6,
            fontName='Helvetica-Bold'
        )
        
        normal_style = ParagraphStyle(
            'AdminNormal',
            parent=self.styles['Normal'],
            fontSize=8,
            spaceAfter=3,
            fontName='Helvetica'
        )
        
        # Text wrapping style for long names/departments
        wrap_style = ParagraphStyle(
            'WrapText',
            parent=self.styles['Normal'],
            fontSize=7,
            leading=9,
            fontName='Helvetica'
        )
        
        # COMPANY HEADER - Logo on right, company name on left
        company_header_style = ParagraphStyle(
            'CompanyHeader',
            parent=self.styles['Normal'],
            fontSize=14,
            fontName='Helvetica-Bold',
            textColor=colors.darkblue,
            alignment=TA_LEFT,
            spaceAfter=2
        )
        
        report_title_style = ParagraphStyle(
            'ReportTitle',
            parent=self.styles['Normal'],
            fontSize=11,
            fontName='Helvetica-Bold',
            textColor=colors.darkblue,
            alignment=TA_LEFT,
            spaceAfter=2
        )
        
        timestamp_style = ParagraphStyle(
            'Timestamp',
            parent=self.styles['Normal'],
            fontSize=8,
            fontName='Helvetica',
            textColor=colors.grey,
            alignment=TA_LEFT
        )
        
        # Build header content
        period_txt = ''
        if filters.get('start_date') or filters.get('end_date'):
            sd, ed = filters.get('start_date'), filters.get('end_date')
            period_txt = f"Period: {sd.strftime('%d %b %Y') if sd else 'Start'} to {ed.strftime('%d %b %Y') if ed else 'Today'}"
        company_header_style = ParagraphStyle(
            'CompanyHeader', parent=self.styles['Normal'], fontSize=18, leading=22,
            fontName='Helvetica-Bold', textColor=_tc('ink'), alignment=TA_LEFT, spaceAfter=2)
        report_title_style = ParagraphStyle(
            'ReportTitle', parent=self.styles['Normal'], fontSize=9, leading=12,
            fontName='Helvetica-Bold', textColor=_tc('accent'), alignment=TA_LEFT, spaceAfter=2)
        timestamp_style = ParagraphStyle(
            'Timestamp', parent=self.styles['Normal'], fontSize=8, leading=11,
            fontName='Helvetica', textColor=_tc('muted'), alignment=TA_LEFT)
        company_content = [
            Paragraph(company_name, company_header_style),
            Paragraph("ADMIN ATTENDANCE REPORT", report_title_style),
            Paragraph(period_txt or f"Generated {datetime.now().strftime('%d %b %Y, %H:%M')}", timestamp_style)
        ]

        logo_cell = ''
        if company_logo and os.path.exists(company_logo):
            try:
                logo_cell = _logo_image(company_logo)
            except Exception:
                pass

        header_table = Table(
            [[company_content, logo_cell]],
            colWidths=[available_width * 0.8, available_width * 0.2]
        )
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ('LEFTPADDING', (0, 0), (0, 0), 0),
            ('RIGHTPADDING', (1, 0), (1, 0), 0),
            ('LINEBELOW', (0, 0), (-1, 0), 1.2, _tc('accent')),
        ]))
        story.append(header_table)
        story.append(Spacer(1, 0.08*inch))

        # APPLIED FILTERS SECTION
        story.append(_section_heading("Applied Filters", available_width))

        start_date = filters.get('start_date')
        end_date = filters.get('end_date')
        department = filters.get('department') or 'All'
        employee_id = filters.get('employee_id')
        designation = filters.get('designation') or 'All'
        status = filters.get('status') or 'All'

        story.append(_info_card([
            ('Date From', start_date.strftime('%d-%b-%Y') if start_date else 'All'),
            ('Date To', end_date.strftime('%d-%b-%Y') if end_date else 'All'),
            ('Attendance Status', str(status).replace('_', ' ').title()),
            ('Department', department),
            ('Employee', 'All Employees' if not employee_id else f'ID: {employee_id}'),
            ('Designation', designation),
        ], available_width, cols=3))
        story.append(Spacer(1, 0.12*inch))

        # SUMMARY SECTION
        summary = report_data.get('summary', {})
        story.append(_section_heading("Summary", available_width))
        story.append(_kpi_cards([
            ('Total Employees', summary.get('total_employees', 0), None),
            ('Present', summary.get('present', 0), 'good'),
            ('Absent', summary.get('absent', 0), 'bad'),
            ('Half Day', summary.get('half_day', 0), 'warn'),
            ('Late', summary.get('late', 0), 'bad'),
            ('Attendance %', f"{summary.get('attendance_percentage', 0):.1f}%", 'accent'),
        ], available_width))
        story.append(Spacer(1, 0.12*inch))

        # DEPARTMENT ANALYTICS
        dept_analytics = report_data.get('department_analytics', {})
        if dept_analytics:
            story.append(_section_heading("Department-wise Analytics", available_width))
            
            dept_data = [['Department', 'Total Emp', 'Present', 'Absent', 'Half Day', 'Late', 'Total Hours', 'Avg Hours', 'Att %']]
            
            for dept, stats in dept_analytics.items():
                dept_data.append([
                    Paragraph(dept, wrap_style),
                    str(stats.get('total_employees', 0)),
                    str(stats.get('present', 0)),
                    str(stats.get('absent', 0)),
                    str(stats.get('half_day', 0)),
                    str(stats.get('late', 0)),
                    f"{stats.get('total_working_hours', 0):.1f}",
                    f"{stats.get('average_working_hours', 0):.1f}",
                    f"{stats.get('attendance_percentage', 0):.1f}%"
                ])
            
            # Calculate column widths based on available width
            dept_col_widths = [
                available_width * 0.18,  # Department
                available_width * 0.08,  # Total Emp
                available_width * 0.08,  # Present
                available_width * 0.08,  # Absent
                available_width * 0.08,  # Half Day
                available_width * 0.07,  # Late
                available_width * 0.10,  # Total Hours
                available_width * 0.09,  # Avg Hours
                available_width * 0.08   # Att %
            ]
            logger.info(f"Department table column widths: {dept_col_widths}")
            
            dept_table = LongTable(dept_data, colWidths=_fit(dept_col_widths, available_width), hAlign='LEFT', repeatRows=1)
            dept_table.setStyle(_pro_table_style(font_size=7, left_cols=(0,)))
            story.append(dept_table)
            story.append(Spacer(1, 0.15*inch))
        
        # EMPLOYEE SUMMARY
        emp_summary = report_data.get('employee_summary', [])
        if emp_summary:
            story.append(_section_heading("Employee Summary", available_width))
            
            emp_data = [['Employee', 'Emp ID', 'Dept', 'Designation', 'Days', 'Present', 'Absent', 'Half Day', 'Late', 'Total Hours', 'Avg Hours', 'Att %', 'Punct %']]
            
            for emp in emp_summary:
                emp_data.append([
                    Paragraph(emp.get('name', ''), wrap_style),
                    str(emp.get('employee_id', '')),
                    Paragraph(emp.get('department', ''), wrap_style),
                    Paragraph(emp.get('designation', ''), wrap_style),
                    str(emp.get('total_working_days', 0)),
                    str(emp.get('present', 0)),
                    str(emp.get('absent', 0)),
                    str(emp.get('half_day', 0)),
                    str(emp.get('late', 0)),
                    f"{emp.get('total_working_hours', 0):.1f}",
                    f"{emp.get('avg_working_hours', 0):.1f}",
                    f"{emp.get('attendance_percentage', 0):.1f}%",
                    f"{emp.get('punctuality_percentage', 0):.1f}%"
                ])
            
            # Calculate column widths based on available width
            emp_col_widths = [
                available_width * 0.12,  # Employee
                available_width * 0.06,  # Emp ID
                available_width * 0.10,  # Dept
                available_width * 0.10,  # Designation
                available_width * 0.05,  # Days
                available_width * 0.05,  # Present
                available_width * 0.05,  # Absent
                available_width * 0.05,  # Half Day
                available_width * 0.05,  # Late
                available_width * 0.08,  # Total Hours
                available_width * 0.07,  # Avg Hours
                available_width * 0.05,  # Att %
                available_width * 0.07   # Punct %
            ]
            logger.info(f"Employee Summary table column widths: {emp_col_widths}")
            
            emp_table = LongTable(emp_data, colWidths=_fit(emp_col_widths, available_width), hAlign='LEFT', repeatRows=1)
            emp_table.setStyle(_pro_table_style(font_size=6, left_cols=(0, 2, 3)))
            story.append(emp_table)
            story.append(Spacer(1, 0.15*inch))
        
        # EMPLOYEE ATTENDANCE DETAILS
        attendances = report_data.get('attendances', [])
        if attendances:
            story.append(_section_heading("Employee Attendance Details", available_width))
            
            # Group attendances by employee
            from collections import defaultdict
            emp_attendances = defaultdict(list)
            for att in attendances:
                if att.employee:
                    emp_attendances[att.employee].append(att)
            
            # Add display_out_time for each attendance
            from app import get_services
            am, _, _, _ = get_services()
            for emp, atts in emp_attendances.items():
                for att in atts:
                    if not hasattr(att, 'is_dummy'):
                        am._add_display_out_time(att, att.date)
            
            # Create attendance table for each employee
            for emp, atts in emp_attendances.items():
                # Employee header
                emp_bar_style = ParagraphStyle('EmpBar', parent=self.styles['Normal'], fontSize=8.5,
                                               leading=11, textColor=_tc('ink'), fontName='Helvetica')
                emp_bar = Table([[Paragraph(
                    f"<b>{emp.name}</b> &nbsp;&nbsp;<font color='{THEME['muted']}'>ID {emp.employee_id}"
                    f" &nbsp;|&nbsp; {emp.department}</font>", emp_bar_style)]],
                    colWidths=[available_width], hAlign='LEFT')
                emp_bar.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), _tc('tint')),
                    ('LINEBEFORE', (0, 0), (0, 0), 3, _tc('accent')),
                    ('LEFTPADDING', (0, 0), (-1, -1), 8),
                    ('TOPPADDING', (0, 0), (-1, -1), 5),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ]))
                emp_bar.keepWithNext = True
                emp_bar.spaceBefore = 8
                emp_bar.spaceAfter = 3
                story.append(emp_bar)
                
                # Attendance data
                att_data = [['Date', 'IN Time', 'OUT Time', 'Total Hours', 'Status', 'Late', 'Overtime', 'Activities (IN/OUT)']]
                activities_style = ParagraphStyle(
                    'AdminActivitiesCell',
                    parent=self.styles['Normal'],
                    fontSize=7,
                    leading=9,
                    alignment=TA_LEFT
                )
                
                for att in atts:
                    att_data.append([
                        att.date.strftime('%d-%b-%Y') if hasattr(att, 'date') else str(att.date),
                        att.in_time.strftime('%H:%M') if att.in_time else '-',
                        att.display_out_time.strftime('%H:%M') if hasattr(att, 'display_out_time') and att.display_out_time else '-',
                        f"{att.total_hours:.2f}" if att.total_hours and att.total_hours != 0 else '-',
                        att.status.upper().replace('_', ' ') if att.status else '-',
                        'Yes' if att.late_entry else 'No',
                        f"{att.overtime_hours:.2f}" if att.overtime_hours and att.overtime_hours != 0 else '-',
                        Paragraph(getattr(att, 'activities_text', None) or '-', activities_style)
                    ])
                
                # Calculate column widths based on available width
                att_col_widths = [
                    available_width * 0.11,  # Date
                    available_width * 0.08,  # IN Time
                    available_width * 0.08,  # OUT Time
                    available_width * 0.09,  # Total Hours
                    available_width * 0.09,  # Status
                    available_width * 0.06,  # Late
                    available_width * 0.09,  # Overtime
                    available_width * 0.40   # Activities (all IN/OUT punches)
                ]
                logger.info(f"Attendance table column widths: {att_col_widths}")
                
                att_table = LongTable(att_data, colWidths=_fit(att_col_widths, available_width), hAlign='LEFT', repeatRows=1)
                att_table.setStyle(_pro_table_style(font_size=7, left_cols=(0, 7)))
                att_table.setStyle(TableStyle(_status_cmds(att_data, 4, 5)))
                story.append(att_table)
                story.append(Spacer(1, 0.08*inch))
            
            story.append(Spacer(1, 0.1*inch))
        
        # RANKINGS
        rankings = report_data.get('rankings', {})
        
        # Most Present
        if rankings.get('most_present'):
            story.append(_section_heading("Most Present", available_width))
            present_data = [['Rank', 'Employee', 'Emp ID', 'Department', 'Present Days', 'Att %']]
            for item in rankings['most_present']:
                present_data.append([
                    str(item.get('rank', '')),
                    Paragraph(item.get('name', ''), wrap_style),
                    str(item.get('employee_id', '')),
                    Paragraph(item.get('department', ''), wrap_style),
                    str(item.get('present', 0)),
                    f"{item.get('attendance_percentage', 0):.1f}%"
                ])
            
            # Calculate column widths based on available width
            present_col_widths = [
                available_width * 0.08,  # Rank
                available_width * 0.22,  # Employee
                available_width * 0.10,  # Emp ID
                available_width * 0.18,  # Department
                available_width * 0.12,  # Present Days
                available_width * 0.10   # Att %
            ]
            logger.info(f"Most Present table column widths: {present_col_widths}")
            
            present_table = LongTable(present_data, colWidths=_fit(present_col_widths, available_width), hAlign='LEFT', repeatRows=1)
            present_table.setStyle(_pro_table_style(font_size=7, left_cols=(1, 3)))
            story.append(present_table)
            story.append(Spacer(1, 0.08*inch))
        
        # Most Absent
        if rankings.get('most_absent'):
            story.append(_section_heading("Most Absent", available_width))
            absent_data = [['Rank', 'Employee', 'Emp ID', 'Department', 'Absent Days', 'Att %']]
            for item in rankings['most_absent']:
                absent_data.append([
                    str(item.get('rank', '')),
                    Paragraph(item.get('name', ''), wrap_style),
                    str(item.get('employee_id', '')),
                    Paragraph(item.get('department', ''), wrap_style),
                    str(item.get('absent', 0)),
                    f"{item.get('attendance_percentage', 0):.1f}%"
                ])
            
            absent_table = LongTable(absent_data, colWidths=_fit(present_col_widths, available_width), hAlign='LEFT', repeatRows=1)
            absent_table.setStyle(_pro_table_style(font_size=7, left_cols=(1, 3)))
            story.append(absent_table)
            story.append(Spacer(1, 0.08*inch))
        
        # Most Half Day
        if rankings.get('most_half_day'):
            story.append(_section_heading("Most Half Days", available_width))
            half_data = [['Rank', 'Employee', 'Emp ID', 'Department', 'Half Days', 'Att %']]
            for item in rankings['most_half_day']:
                half_data.append([
                    str(item.get('rank', '')),
                    Paragraph(item.get('name', ''), wrap_style),
                    str(item.get('employee_id', '')),
                    Paragraph(item.get('department', ''), wrap_style),
                    str(item.get('half_day', 0)),
                    f"{item.get('attendance_percentage', 0):.1f}%"
                ])
            
            half_table = LongTable(half_data, colWidths=_fit(present_col_widths, available_width), hAlign='LEFT', repeatRows=1)
            half_table.setStyle(_pro_table_style(font_size=7, left_cols=(1, 3)))
            story.append(half_table)
            story.append(Spacer(1, 0.08*inch))
        
        # Most Late
        if rankings.get('most_late'):
            story.append(_section_heading("Most Late", available_width))
            late_data = [['Rank', 'Employee', 'Emp ID', 'Department', 'Late Days', 'Total Late Mins', 'Avg Late Mins']]
            for item in rankings['most_late']:
                late_data.append([
                    str(item.get('rank', '')),
                    Paragraph(item.get('name', ''), wrap_style),
                    str(item.get('employee_id', '')),
                    Paragraph(item.get('department', ''), wrap_style),
                    str(item.get('late', 0)),
                    str(item.get('total_late_minutes', 0)),
                    f"{item.get('avg_late_minutes', 0):.1f}"
                ])
            
            # Calculate column widths based on available width
            late_col_widths = [
                available_width * 0.08,  # Rank
                available_width * 0.20,  # Employee
                available_width * 0.10,  # Emp ID
                available_width * 0.16,  # Department
                available_width * 0.10,  # Late Days
                available_width * 0.12,  # Total Late Mins
                available_width * 0.12   # Avg Late Mins
            ]
            logger.info(f"Most Late table column widths: {late_col_widths}")
            
            late_table = LongTable(late_data, colWidths=_fit(late_col_widths, available_width), hAlign='LEFT', repeatRows=1)
            late_table.setStyle(_pro_table_style(font_size=7, left_cols=(1, 3)))
            story.append(late_table)
            story.append(Spacer(1, 0.08*inch))
        
        # Highest Working Hours
        if rankings.get('highest_working_hours'):
            story.append(_section_heading("Highest Working Hours", available_width))
            high_data = [['Rank', 'Employee', 'Emp ID', 'Department', 'Total Hours', 'Avg Daily Hours']]
            for item in rankings['highest_working_hours']:
                high_data.append([
                    str(item.get('rank', '')),
                    Paragraph(item.get('name', ''), wrap_style),
                    str(item.get('employee_id', '')),
                    Paragraph(item.get('department', ''), wrap_style),
                    f"{item.get('total_working_hours', 0):.1f}",
                    f"{item.get('avg_daily_working_hours', 0):.1f}"
                ])
            
            # Calculate column widths based on available width
            hours_col_widths = [
                available_width * 0.08,  # Rank
                available_width * 0.22,  # Employee
                available_width * 0.10,  # Emp ID
                available_width * 0.18,  # Department
                available_width * 0.12,  # Total Hours
                available_width * 0.12   # Avg Daily Hours
            ]
            logger.info(f"Highest Working Hours table column widths: {hours_col_widths}")
            
            high_table = LongTable(high_data, colWidths=_fit(hours_col_widths, available_width), hAlign='LEFT', repeatRows=1)
            high_table.setStyle(_pro_table_style(font_size=7, left_cols=(1, 3)))
            story.append(high_table)
            story.append(Spacer(1, 0.08*inch))
        
        # Lowest Working Hours
        if rankings.get('lowest_working_hours'):
            story.append(_section_heading("Lowest Working Hours", available_width))
            low_data = [['Rank', 'Employee', 'Emp ID', 'Department', 'Total Hours', 'Avg Daily Hours']]
            for item in rankings['lowest_working_hours']:
                low_data.append([
                    str(item.get('rank', '')),
                    Paragraph(item.get('name', ''), wrap_style),
                    str(item.get('employee_id', '')),
                    Paragraph(item.get('department', ''), wrap_style),
                    f"{item.get('total_working_hours', 0):.1f}",
                    f"{item.get('avg_daily_working_hours', 0):.1f}"
                ])
            
            low_table = LongTable(low_data, colWidths=_fit(hours_col_widths, available_width), hAlign='LEFT', repeatRows=1)
            low_table.setStyle(_pro_table_style(font_size=7, left_cols=(1, 3)))
            story.append(low_table)
            story.append(Spacer(1, 0.1*inch))
        
        # LATE ANALYSIS
        late_analysis = report_data.get('late_analysis', [])
        if late_analysis:
            story.append(_section_heading("Late Analysis", available_width))
            
            late_anal_data = [['Employee', 'Emp ID', 'Department', 'Late Days', 'Total Late Mins', 'Avg Late Mins', 'Max Late Mins']]
            
            for item in late_analysis:
                late_anal_data.append([
                    Paragraph(item.get('name', ''), wrap_style),
                    str(item.get('employee_id', '')),
                    Paragraph(item.get('department', ''), wrap_style),
                    str(item.get('late_days', 0)),
                    str(item.get('total_late_minutes', 0)),
                    f"{item.get('avg_late_minutes', 0):.1f}",
                    str(item.get('max_late_minutes', 0))
                ])
            
            # Calculate column widths based on available width
            late_anal_col_widths = [
                available_width * 0.14,  # Employee
                available_width * 0.08,  # Emp ID
                available_width * 0.12,  # Department
                available_width * 0.10,  # Late Days
                available_width * 0.12,  # Total Late Mins
                available_width * 0.12,  # Avg Late Mins
                available_width * 0.12   # Max Late Mins
            ]
            logger.info(f"Late Analysis table column widths: {late_anal_col_widths}")
            
            late_anal_table = LongTable(late_anal_data, colWidths=_fit(late_anal_col_widths, available_width), hAlign='LEFT', repeatRows=1)
            late_anal_table.setStyle(_pro_table_style(font_size=7, left_cols=(0, 2)))
            story.append(late_anal_table)
            story.append(Spacer(1, 0.1*inch))
        
        # DAILY ATTENDANCE TREND
        daily_trend = report_data.get('daily_trend', [])
        if daily_trend:
            story.append(_section_heading("Daily Attendance Trend", available_width))
            
            trend_data = [['Date', 'Present', 'Absent', 'Half Day', 'Late']]
            
            for day in daily_trend:
                # daily_trend items are dictionaries, not objects
                date_val = day.get('date')
                if date_val:
                    date_str = date_val.strftime('%d-%b-%Y') if hasattr(date_val, 'strftime') else str(date_val)
                else:
                    date_str = str(date_val)
                
                trend_data.append([
                    date_str,
                    str(day.get('present', 0)),
                    str(day.get('absent', 0)),
                    str(day.get('half_day', 0)),
                    str(day.get('late', 0))
                ])
            
            # Calculate column widths based on available width
            trend_col_widths = [
                available_width * 0.20,  # Date
                available_width * 0.15,  # Present
                available_width * 0.15,  # Absent
                available_width * 0.15,  # Half Day
                available_width * 0.15   # Late
            ]
            logger.info(f"Daily Trend table column widths: {trend_col_widths}")
            
            trend_table = LongTable(trend_data, colWidths=_fit(trend_col_widths, available_width), hAlign='LEFT', repeatRows=1)
            trend_table.setStyle(_pro_table_style(font_size=7, left_cols=(0,)))
            story.append(trend_table)
        
        # Build PDF
        doc.build(story, canvasmaker=_make_numbered_canvas(company_name, 'Admin Attendance Report', pagesize))
        
        logger.info("[ADMIN REPORT PDF] Rendering complete")
        logger.info("=" * 60)

    def generate_attendance_report(self, attendances, employee, start_date, end_date, output_path, company_settings=None):
        """Generate attendance report PDF with improved layout
        
        Args:
            attendances: List of attendance records
            employee: Employee object (None for All Employees report)
            start_date: Start date string
            end_date: End date string
            output_path: Output PDF file path
            company_settings: Optional Settings object from database (uses Config fallback if None)
        """
        # Determine if we need landscape orientation based on column count
        is_all_employees = employee is None
        num_columns = 10 if is_all_employees else 7
        
        # Single-employee report shows an Activities column (all IN/OUT punches)
        # when the caller attached `activities_text` to the records.
        show_activities = (not is_all_employees) and any(
            getattr(a, 'activities_text', None) is not None for a in attendances
        )

        # Use landscape for All Employees report (more columns) and for the
        # single-employee report when the Activities column is shown
        pagesize = landscape(A4) if (is_all_employees or show_activities) else A4
        
        # Create document with proper margins
        doc = SimpleDocTemplate(
            output_path,
            pagesize=pagesize,
            rightMargin=30,
            leftMargin=30,
            topMargin=40,
            bottomMargin=50
        )
        
        story = []
        
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=self.styles['Heading1'],
            fontSize=18,
            textColor=colors.darkblue,
            alignment=TA_CENTER,
            spaceAfter=20
        )
        
        header_style = ParagraphStyle(
            'CustomHeader',
            parent=self.styles['Heading2'],
            fontSize=12,
            textColor=colors.darkblue,
            spaceAfter=10
        )
        
        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=self.styles['Normal'],
            fontSize=10,
            spaceAfter=5
        )
        
        # Fetch company settings from database or use Config fallback
        if company_settings and hasattr(company_settings, 'company_name'):
            company_name = company_settings.company_name
            company_logo = company_settings.company_logo
        else:
            company_name = self.company_name
            company_logo = self.company_logo
        
        # COMPANY HEADER - company info on left, logo on right
        company_header_style = ParagraphStyle(
            'CompanyHeader', parent=self.styles['Normal'], fontSize=18, leading=22,
            fontName='Helvetica-Bold', textColor=_tc('ink'), alignment=TA_LEFT, spaceAfter=2)
        report_title_style = ParagraphStyle(
            'ReportTitle', parent=self.styles['Normal'], fontSize=9, leading=12,
            fontName='Helvetica-Bold', textColor=_tc('accent'), alignment=TA_LEFT, spaceAfter=2)
        sub_style = ParagraphStyle(
            'ReportSub', parent=self.styles['Normal'], fontSize=8, leading=11,
            fontName='Helvetica', textColor=_tc('muted'), alignment=TA_LEFT)

        company_content = [
            Paragraph(company_name, company_header_style),
            Paragraph("ATTENDANCE REPORT", report_title_style),
            Paragraph(f"Period: {start_date} to {end_date}", sub_style),
        ]

        logo_cell = ''
        if company_logo and os.path.exists(company_logo):
            try:
                logo_cell = _logo_image(company_logo)
            except Exception:
                pass

        available_width = pagesize[0] - 60

        header_table = Table(
            [[company_content, logo_cell]],
            colWidths=[available_width * 0.8, available_width * 0.2]
        )
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ('LEFTPADDING', (0, 0), (0, 0), 0),
            ('RIGHTPADDING', (1, 0), (1, 0), 0),
            ('LINEBELOW', (0, 0), (-1, 0), 1.2, _tc('accent')),
        ]))
        story.append(header_table)
        story.append(Spacer(1, 0.12*inch))

        # Report details card
        if is_all_employees:
            story.append(_info_card([
                ('Employee', 'All Employees'),
                ('Department', 'All Departments'),
                ('Period', f"{start_date} to {end_date}"),
            ], available_width, cols=3))
        else:
            story.append(_info_card([
                ('Employee', employee.name),
                ('Employee ID', employee.employee_id),
                ('Department', employee.department),
            ], available_width, cols=3))
        story.append(Spacer(1, 0.18*inch))

        
        # Summary - works for both report types
        # Use effective report status so REJECTED approvals count strictly as ABSENT
        from app import get_effective_report_status
        present = len([a for a in attendances if get_effective_report_status(a) == 'present'])
        absent = len([a for a in attendances if get_effective_report_status(a) == 'absent'])
        half_day = len([a for a in attendances if get_effective_report_status(a) == 'half_day'])
        late = len([a for a in attendances if a.late_entry])
        
        story.append(_section_heading("Summary", available_width))
        story.append(_kpi_cards([
            ('Total Records', len(attendances), None),
            ('Present', present, 'good'),
            ('Absent', absent, 'bad'),
            ('Half Days', half_day, 'warn'),
            ('Late Arrivals', late, 'bad'),
        ], available_width))
        story.append(Spacer(1, 0.14*inch))

        # Attendance Table
        if attendances:
            # For All Employees report, include Employee ID, Name, Department columns
            if is_all_employees:
                # Calculate automatic column widths based on content
                # Use relative widths that sum to available page width
                available_width = pagesize[0] - 60
                col_widths = [
                    0.8*inch,   # Employee ID
                    1.5*inch,   # Employee Name (wider for wrapping)
                    1.2*inch,   # Department
                    0.9*inch,   # Date
                    0.7*inch,   # IN Time
                    0.7*inch,   # OUT Time
                    0.8*inch,   # Total Hours
                    0.8*inch,   # Status
                    0.6*inch,   # Late
                    0.8*inch    # Overtime
                ]
                
                data = [['Employee ID', 'Employee Name', 'Department', 'Date', 'IN Time', 'OUT Time', 'Total Hours', 'Status', 'Late', 'Overtime']]
                
                for att in attendances:
                    data.append([
                        att.employee.employee_id,
                        att.employee.name,  # Will wrap if too long
                        att.employee.department,
                        att.date.strftime('%Y-%m-%d'),
                        att.in_time.strftime('%H:%M') if att.in_time else '-',
                        att.display_out_time.strftime('%H:%M') if hasattr(att, 'display_out_time') and att.display_out_time else '-',
                        f"{att.total_hours:.2f}" if att.total_hours and att.total_hours != 0 else '-',
                        att.status.upper().replace('_', ' '),
                        'Yes' if att.late_entry else 'No',
                        f"{att.overtime_hours:.2f}" if att.overtime_hours and att.overtime_hours != 0 else '-'
                    ])
            else:
                # Single Employee report
                available_width = pagesize[0] - 60
                col_widths = [
                    1.0*inch,   # Date
                    1.0*inch,   # IN Time
                    1.0*inch,   # OUT Time
                    1.0*inch,   # Total Hours
                    1.0*inch,   # Status
                    0.8*inch,   # Late
                    1.0*inch    # Overtime
                ]
                
                data = [['Date', 'IN Time', 'OUT Time', 'Total Hours', 'Status', 'Late', 'Overtime']]

                if show_activities:
                    # Wider page (landscape): give the Activities column all the
                    # remaining width; it wraps so every punch is listed.
                    activities_width = available_width - sum(col_widths)
                    col_widths = col_widths + [activities_width]
                    data[0].append('Activities')
                    activities_style = ParagraphStyle(
                        'ActivitiesCell',
                        parent=self.styles['Normal'],
                        fontSize=7,
                        leading=9,
                        alignment=TA_LEFT
                    )
                
                for att in attendances:
                    row = [
                        att.date.strftime('%Y-%m-%d'),
                        att.in_time.strftime('%H:%M') if att.in_time else '-',
                        att.display_out_time.strftime('%H:%M') if hasattr(att, 'display_out_time') and att.display_out_time else '-',
                        f"{att.total_hours:.2f}" if att.total_hours and att.total_hours != 0 else '-',
                        att.status.upper().replace('_', ' '),
                        'Yes' if att.late_entry else 'No',
                        f"{att.overtime_hours:.2f}" if att.overtime_hours and att.overtime_hours != 0 else '-'
                    ]
                    if show_activities:
                        row.append(Paragraph(getattr(att, 'activities_text', None) or '-', activities_style))
                    data.append(row)
            
            table = Table(data, colWidths=_fit(col_widths, available_width), repeatRows=1, hAlign="LEFT")
            
            if is_all_employees:
                table_style = _pro_table_style(font_size=8, left_cols=(0, 1, 2), pad=6)
                status_idx, late_idx = 7, 8
            else:
                table_style = _pro_table_style(font_size=8, left_cols=((0, 7) if show_activities else (0,)), pad=6)
                status_idx, late_idx = 4, 5
            table.setStyle(table_style)
            table.setStyle(TableStyle(_status_cmds(data, status_idx, late_idx)))
            story.append(table)
        else:
            story.append(Paragraph("No attendance records found for this period.", normal_style))
        
        doc.build(story, canvasmaker=_make_numbered_canvas(company_name, 'Attendance Report', pagesize))

        return output_path