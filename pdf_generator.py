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


def generate_payslip_password(employee):
    """
    Build the deterministic payslip PDF open-password for `employee`.

    Rule (primary):
        First 4 UPPERCASE letters of the employee's name + DOB in DDMM format
        e.g. name="Vaishnavi Lekawale", dob=1998-03-15  ->  "VAIS1503"

    Fallback (used only if the name doesn't yield 4 alphabetic characters,
    e.g. a very short or non-alphabetic name):
        Employee ID + DOB in DDMM format
        e.g. employee_id="EMP0001", dob=1998-03-15  ->  "EMP00011503"

    If the employee has no date of birth on file, falls back to their
    joining_date (always present, NOT NULL) so a password can still
    always be produced deterministically - a warning is logged so admins
    know to add a proper DOB for that employee.

    This function is pure (no DB writes) and deterministic: calling it
    again for the same employee always yields the same password, so the
    password never needs to be persisted anywhere.
    """
    employee_label = getattr(employee, 'employee_id', None) or getattr(employee, 'id', '?')

    # --- date-of-birth component (DDMM) ---
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

    # --- name component: first 4 uppercase letters ---
    raw_name = getattr(employee, 'name', '') or ''
    letters_only = re.sub(r'[^A-Za-z]', '', raw_name).upper()
    name_part = letters_only[:4]

    if len(name_part) == 4:
        return f"{name_part}{ddmm}"

    # Fallback: name too short / non-alphabetic -> Employee ID + DOB(DDMM)
    employee_id = (getattr(employee, 'employee_id', '') or '').upper()
    employee_id = re.sub(r'[^A-Z0-9]', '', employee_id)
    return f"{employee_id}{ddmm}"


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
            rightMargin=24,
            leftMargin=24,
            topMargin=24,
            bottomMargin=24,
        )

        story = []

        style_company_name = ParagraphStyle(
            'CompName',
            parent=self.styles['Normal'],
            fontSize=11,
            leading=14,
            fontName='Helvetica-Bold',
            textColor=colors.HexColor('#0B3D91'),
            spaceAfter=4,
            wordWrap='CJK',
        )
        style_company_info = ParagraphStyle(
            'CompInfo',
            parent=self.styles['Normal'],
            fontName='Helvetica',
            fontSize=8,
            leading=11,
            textColor=colors.black,
            spaceAfter=2,
        )
        style_section = ParagraphStyle(
            'SectionTitle',
            parent=self.styles['Normal'],
            fontSize=9,
            leading=11,
            fontName='Helvetica-Bold',
            textColor=colors.HexColor('#0B3D91'),
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
            fontSize=9,
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
                logo_cell = Image(logo_path, width=0.9 * inch, height=0.9 * inch)
            except Exception:
                logo_cell = ''

        header_table = Table(
            [[logo_cell, company_lines]],
            colWidths=[1.0 * inch, 6.3 * inch],
        )
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (0, 0), (0, 0), 'CENTER'),
            ('ALIGN', (1, 0), (1, 0), 'LEFT'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ('LEFTPADDING', (1, 0), (1, 0), 8),
            ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#CCCCCC')),
        ]))
        story.append(header_table)
        story.append(Spacer(1, 0.1 * inch))

        month_names = [
            'January', 'February', 'March', 'April', 'May', 'June',
            'July', 'August', 'September', 'October', 'November', 'December',
        ]
        period = f"{month_names[payroll.month - 1]} {payroll.year}"

        title_table = Table(
            [[Paragraph(f'PAY SLIP - {period.upper()}', style_title)]],
            colWidths=[7.3 * inch],
        )
        title_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#EAEAEA')),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(title_table)
        story.append(Spacer(1, 0.08 * inch))

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
        ], colWidths=[1.3 * inch, 2.35 * inch, 1.3 * inch, 2.35 * inch])
        employee_table.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            # Corporate polish: soft blue tint on the label columns so the
            # label/value pairs are visually distinct at a glance. Column
            # 2's tint stops before the last (PF) row, since that row's
            # value cell is merged (SPANned) across columns 1-3 - painting
            # column 2's background independently there would bleed a
            # stray colored strip through the merged white value cell.
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#EEF2FA')),
            ('BACKGROUND', (2, 0), (2, 3), colors.HexColor('#EEF2FA')),
            # PF row: merge the value cell across the remaining 3 columns
            # (label, value, value, value) so there's no dangling empty
            # cell when only one field is present on the last row.
            ('SPAN', (1, 4), (3, 4)),
        ]))
        story.append(employee_table)
        story.append(Spacer(1, 0.08 * inch))

        story.append(Paragraph('Attendance Summary', style_section))
        story.append(Spacer(1, 0.04 * inch))

        attendance_rows = build_payslip_attendance_rows(payroll)
        attendance_headers = [Paragraph(label, style_attendance_header) for label, _ in attendance_rows]
        attendance_values = [Paragraph(value, style_attendance_value) for _, value in attendance_rows]
        col_width = 7.3 * inch / len(attendance_rows)

        attendance_table = Table(
            [attendance_headers, attendance_values],
            colWidths=[col_width] * len(attendance_rows),
        )
        attendance_table.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F5F5F5')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 3),
            ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(attendance_table)
        story.append(Spacer(1, 0.08 * inch))

        earnings_rows = build_payslip_earnings_rows(payroll)
        deduction_rows = build_payslip_deduction_rows(payroll)

        max_rows = max(len(earnings_rows), len(deduction_rows))
        earnings_rows += [(' ', 0.0)] * (max_rows - len(earnings_rows))
        deduction_rows += [(' ', 0.0)] * (max_rows - len(deduction_rows))

        salary_table_data = [
            [
                Paragraph('Earnings', style_center_bold),
                Paragraph('Amount', style_center_bold),
                Paragraph('Deductions', style_center_bold),
                Paragraph('Amount', style_center_bold),
            ]
        ]

        for (earn_label, earn_amount), (ded_label, ded_amount) in zip(earnings_rows, deduction_rows):
            is_earn_total = earn_label == 'Total Gross Earnings'
            is_ded_total = ded_label == 'Total Deductions'
            earn_style = style_bold if is_earn_total else style_normal
            ded_style = style_bold if is_ded_total else style_normal

            earn_amount_cell = (
                self._format_currency(earn_amount)
                if earn_label.strip()
                else ''
            )
            ded_amount_cell = (
                self._format_currency(ded_amount)
                if ded_label.strip()
                else ''
            )
            salary_table_data.append([
                Paragraph(earn_label, earn_style),
                Paragraph(earn_amount_cell, earn_style),
                Paragraph(ded_label, ded_style),
                Paragraph(ded_amount_cell, ded_style),
            ])

        salary_table = Table(
            salary_table_data,
            colWidths=[2.0 * inch, 1.65 * inch, 2.0 * inch, 1.65 * inch],
        )
        salary_table.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0B3D91')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('ALIGN', (1, 1), (1, -1), 'RIGHT'),
            ('ALIGN', (3, 1), (3, -1), 'RIGHT'),
            ('BACKGROUND', (0, -1), (1, -1), colors.HexColor('#F5F5F5')),
            ('BACKGROUND', (2, -1), (3, -1), colors.HexColor('#F5F5F5')),
        ]))
        story.append(salary_table)
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
                    f'<b>Net Pay: {self._format_currency(net_salary)}</b>',
                    ParagraphStyle(
                        'NetPay',
                        parent=style_bold,
                        fontSize=10,
                        alignment=TA_RIGHT,
                        textColor=colors.HexColor('#0B3D91'),
                    ),
                ),
            ]
        ], colWidths=[4.8 * inch, 2.5 * inch])
        net_table.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#0B3D91')),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#E8F0FE')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(net_table)
        story.append(Spacer(1, 0.06 * inch))
        story.append(
            Paragraph(
                '* This payslip is computer generated and does not require a signature. *',
                style_footer,
            )
        )

        doc.build(story)

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
    
    def generate_admin_reports_pdf(self, report_data, filters, output_path):
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
        """
        import logging
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
        from reportlab.platypus import PageBreak, KeepTogether, Table, LongTable, Paragraph, Spacer
        from reportlab.lib import colors
        logger = logging.getLogger(__name__)
        
        logger.info("=" * 60)
        logger.info("[ADMIN REPORT PDF] Starting PDF generation")
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
            topMargin=35,
            bottomMargin=35
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
        
        # COMPANY HEADER
        if self.company_logo and os.path.exists(self.company_logo):
            try:
                logo = Image(self.company_logo, width=0.8*inch, height=0.8*inch)
                logo.hAlign = 'CENTER'
                story.append(logo)
                story.append(Spacer(1, 0.05*inch))
            except:
                pass
        
        story.append(Paragraph(self.company_name, title_style))
        story.append(Paragraph("ADMIN ATTENDANCE REPORT", subtitle_style))
        story.append(Paragraph(f"Generated: {datetime.now().strftime('%d-%b-%Y %H:%M')}", normal_style))
        story.append(Spacer(1, 0.1*inch))
        
        # APPLIED FILTERS SECTION
        story.append(Paragraph("Applied Filters", section_header_style))
        
        filter_data = []
        filter_data.append(['Filter', 'Value'])
        
        start_date = filters.get('start_date')
        end_date = filters.get('end_date')
        department = filters.get('department') or 'All'
        employee_id = filters.get('employee_id')
        designation = filters.get('designation') or 'All'
        status = filters.get('status') or 'All'
        
        filter_data.append(['Date From', start_date.strftime('%d-%b-%Y') if start_date else 'All'])
        filter_data.append(['Date To', end_date.strftime('%d-%b-%Y') if end_date else 'All'])
        filter_data.append(['Department', department])
        filter_data.append(['Employee', 'All Employees' if not employee_id else f'ID: {employee_id}'])
        filter_data.append(['Designation', designation])
        filter_data.append(['Attendance Status', status])
        
        # Calculate column widths based on available width
        filter_col_widths = [available_width * 0.25, available_width * 0.75]
        logger.info(f"Filter table column widths: {filter_col_widths}")
        
        filter_table = Table(filter_data, colWidths=filter_col_widths, hAlign='LEFT')
        filter_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('TOPPADDING', (0, 0), (-1, 0), 6),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(filter_table)
        story.append(Spacer(1, 0.15*inch))
        
        # SUMMARY SECTION
        summary = report_data.get('summary', {})
        story.append(Paragraph("Summary", section_header_style))
        
        summary_data = []
        summary_data.append(['Metric', 'Value'])
        summary_data.append(['Total Employees', str(summary.get('total_employees', 0))])
        summary_data.append(['Present', str(summary.get('present', 0))])
        summary_data.append(['Absent', str(summary.get('absent', 0))])
        summary_data.append(['Half Day', str(summary.get('half_day', 0))])
        summary_data.append(['Late', str(summary.get('late', 0))])
        summary_data.append(['Attendance %', f"{summary.get('attendance_percentage', 0):.1f}%"])
        
        # Calculate column widths based on available width
        summary_col_widths = [available_width * 0.4, available_width * 0.6]
        logger.info(f"Summary table column widths: {summary_col_widths}")
        
        summary_table = Table(summary_data, colWidths=summary_col_widths, hAlign='LEFT')
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('TOPPADDING', (0, 0), (-1, 0), 6),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(summary_table)
        story.append(Spacer(1, 0.15*inch))
        
        # DEPARTMENT ANALYTICS
        dept_analytics = report_data.get('department_analytics', {})
        if dept_analytics:
            story.append(Paragraph("Department-wise Analytics", section_header_style))
            
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
            
            dept_table = LongTable(dept_data, colWidths=dept_col_widths, hAlign='LEFT', repeatRows=1)
            dept_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 5),
                ('TOPPADDING', (0, 0), (-1, 0), 5),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ]))
            story.append(dept_table)
            story.append(Spacer(1, 0.15*inch))
        
        # EMPLOYEE SUMMARY
        emp_summary = report_data.get('employee_summary', [])
        if emp_summary:
            story.append(Paragraph("Employee Summary", section_header_style))
            
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
            
            emp_table = LongTable(emp_data, colWidths=emp_col_widths, hAlign='LEFT', repeatRows=1)
            emp_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
                ('TOPPADDING', (0, 0), (-1, 0), 4),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ]))
            story.append(emp_table)
            story.append(Spacer(1, 0.15*inch))
        
        # EMPLOYEE ATTENDANCE DETAILS
        attendances = report_data.get('attendances', [])
        if attendances:
            story.append(Paragraph("Employee Attendance Details", section_header_style))
            
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
                emp_header = f"<b>Employee:</b> {emp.name} | <b>ID:</b> {emp.employee_id} | <b>Dept:</b> {emp.department}"
                story.append(Paragraph(emp_header, normal_style))
                story.append(Spacer(1, 0.05*inch))
                
                # Attendance data
                att_data = [['Date', 'IN Time', 'OUT Time', 'Total Hours', 'Status', 'Late', 'Overtime']]
                
                for att in atts:
                    att_data.append([
                        att.date.strftime('%d-%b-%Y') if hasattr(att, 'date') else str(att.date),
                        att.in_time.strftime('%H:%M') if att.in_time else '-',
                        att.display_out_time.strftime('%H:%M') if hasattr(att, 'display_out_time') and att.display_out_time else '-',
                        f"{att.total_hours:.2f}" if att.total_hours and att.total_hours != 0 else '-',
                        att.status.upper() if att.status else '-',
                        'Yes' if att.late_entry else 'No',
                        f"{att.overtime_hours:.2f}" if att.overtime_hours and att.overtime_hours != 0 else '-'
                    ])
                
                # Calculate column widths based on available width
                att_col_widths = [
                    available_width * 0.15,  # Date
                    available_width * 0.12,  # IN Time
                    available_width * 0.12,  # OUT Time
                    available_width * 0.12,  # Total Hours
                    available_width * 0.12,  # Status
                    available_width * 0.10,  # Late
                    available_width * 0.12   # Overtime
                ]
                logger.info(f"Attendance table column widths: {att_col_widths}")
                
                att_table = LongTable(att_data, colWidths=att_col_widths, hAlign='LEFT', repeatRows=1)
                att_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, -1), 7),
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
                    ('TOPPADDING', (0, 0), (-1, 0), 4),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
                    ('LEFTPADDING', (0, 0), (-1, -1), 3),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                ]))
                story.append(att_table)
                story.append(Spacer(1, 0.08*inch))
            
            story.append(Spacer(1, 0.1*inch))
        
        # RANKINGS
        rankings = report_data.get('rankings', {})
        
        # Most Present
        if rankings.get('most_present'):
            story.append(Paragraph("Most Present", section_header_style))
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
            
            present_table = LongTable(present_data, colWidths=present_col_widths, hAlign='LEFT', repeatRows=1)
            present_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
                ('TOPPADDING', (0, 0), (-1, 0), 4),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ]))
            story.append(present_table)
            story.append(Spacer(1, 0.08*inch))
        
        # Most Absent
        if rankings.get('most_absent'):
            story.append(Paragraph("Most Absent", section_header_style))
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
            
            absent_table = LongTable(absent_data, colWidths=present_col_widths, hAlign='LEFT', repeatRows=1)
            absent_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
                ('TOPPADDING', (0, 0), (-1, 0), 4),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ]))
            story.append(absent_table)
            story.append(Spacer(1, 0.08*inch))
        
        # Most Half Day
        if rankings.get('most_half_day'):
            story.append(Paragraph("Most Half Days", section_header_style))
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
            
            half_table = LongTable(half_data, colWidths=present_col_widths, hAlign='LEFT', repeatRows=1)
            half_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
                ('TOPPADDING', (0, 0), (-1, 0), 4),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ]))
            story.append(half_table)
            story.append(Spacer(1, 0.08*inch))
        
        # Most Late
        if rankings.get('most_late'):
            story.append(Paragraph("Most Late", section_header_style))
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
            
            late_table = LongTable(late_data, colWidths=late_col_widths, hAlign='LEFT', repeatRows=1)
            late_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
                ('TOPPADDING', (0, 0), (-1, 0), 4),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ]))
            story.append(late_table)
            story.append(Spacer(1, 0.08*inch))
        
        # Highest Working Hours
        if rankings.get('highest_working_hours'):
            story.append(Paragraph("Highest Working Hours", section_header_style))
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
            
            high_table = LongTable(high_data, colWidths=hours_col_widths, hAlign='LEFT', repeatRows=1)
            high_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
                ('TOPPADDING', (0, 0), (-1, 0), 4),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ]))
            story.append(high_table)
            story.append(Spacer(1, 0.08*inch))
        
        # Lowest Working Hours
        if rankings.get('lowest_working_hours'):
            story.append(Paragraph("Lowest Working Hours", section_header_style))
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
            
            low_table = LongTable(low_data, colWidths=hours_col_widths, hAlign='LEFT', repeatRows=1)
            low_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
                ('TOPPADDING', (0, 0), (-1, 0), 4),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ]))
            story.append(low_table)
            story.append(Spacer(1, 0.1*inch))
        
        # LATE ANALYSIS
        late_analysis = report_data.get('late_analysis', [])
        if late_analysis:
            story.append(Paragraph("Late Analysis", section_header_style))
            
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
            
            late_anal_table = LongTable(late_anal_data, colWidths=late_anal_col_widths, hAlign='LEFT', repeatRows=1)
            late_anal_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
                ('TOPPADDING', (0, 0), (-1, 0), 4),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ]))
            story.append(late_anal_table)
            story.append(Spacer(1, 0.1*inch))
        
        # DAILY ATTENDANCE TREND
        daily_trend = report_data.get('daily_trend', [])
        if daily_trend:
            story.append(Paragraph("Daily Attendance Trend", section_header_style))
            
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
            
            trend_table = LongTable(trend_data, colWidths=trend_col_widths, hAlign='LEFT', repeatRows=1)
            trend_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
                ('TOPPADDING', (0, 0), (-1, 0), 4),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ]))
            story.append(trend_table)
        
        # Build PDF
        doc.build(story)
        
        logger.info("[ADMIN REPORT PDF] Rendering complete")
        logger.info("=" * 60)

    def generate_attendance_report(self, attendances, employee, start_date, end_date, output_path):
        """Generate attendance report PDF with improved layout
        
        Args:
            attendances: List of attendance records
            employee: Employee object (None for All Employees report)
            start_date: Start date string
            end_date: End date string
            output_path: Output PDF file path
        """
        # Determine if we need landscape orientation based on column count
        is_all_employees = employee is None
        num_columns = 10 if is_all_employees else 7
        
        # Use landscape for All Employees report (more columns)
        pagesize = landscape(A4) if is_all_employees else A4
        
        # Create document with proper margins
        doc = SimpleDocTemplate(
            output_path,
            pagesize=pagesize,
            rightMargin=0.75*inch,
            leftMargin=0.75*inch,
            topMargin=1*inch,
            bottomMargin=0.75*inch
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
        
        # Company Logo
        if self.company_logo and os.path.exists(self.company_logo):
            try:
                logo = Image(self.company_logo, width=1.2*inch, height=1.2*inch)
                logo.hAlign = 'CENTER'
                story.append(logo)
                story.append(Spacer(1, 0.1*inch))
            except:
                pass
        
        # Header
        story.append(Paragraph(self.company_name, title_style))
        story.append(Paragraph("Attendance Report", title_style))
        story.append(Spacer(1, 0.15*inch))
        
        # Report Details
        story.append(Paragraph(f"<b>Generated:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", normal_style))
        
        if is_all_employees:
            story.append(Paragraph("<b>Employee:</b> All Employees", normal_style))
            story.append(Paragraph("<b>Department:</b> All Departments", normal_style))
        else:
            story.append(Paragraph(f"<b>Employee:</b> {employee.name}", normal_style))
            story.append(Paragraph(f"<b>Employee ID:</b> {employee.employee_id}", normal_style))
            story.append(Paragraph(f"<b>Department:</b> {employee.department}", normal_style))
        
        story.append(Paragraph(f"<b>Period:</b> {start_date} to {end_date}", normal_style))
        story.append(Spacer(1, 0.2*inch))
        
        # Attendance Table
        if attendances:
            # For All Employees report, include Employee ID, Name, Department columns
            if is_all_employees:
                # Calculate automatic column widths based on content
                # Use relative widths that sum to available page width
                available_width = pagesize[0] - (1.5*inch)  # Total width minus margins
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
                        att.status.upper(),
                        'Yes' if att.late_entry else 'No',
                        f"{att.overtime_hours:.2f}" if att.overtime_hours and att.overtime_hours != 0 else '-'
                    ])
            else:
                # Single Employee report
                available_width = pagesize[0] - (1.5*inch)
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
                
                for att in attendances:
                    data.append([
                        att.date.strftime('%Y-%m-%d'),
                        att.in_time.strftime('%H:%M') if att.in_time else '-',
                        att.display_out_time.strftime('%H:%M') if hasattr(att, 'display_out_time') and att.display_out_time else '-',
                        f"{att.total_hours:.2f}" if att.total_hours and att.total_hours != 0 else '-',
                        att.status.upper(),
                        'Yes' if att.late_entry else 'No',
                        f"{att.overtime_hours:.2f}" if att.overtime_hours and att.overtime_hours != 0 else '-'
                    ])
            
            table = Table(data, colWidths=col_widths, repeatRows=1)
            
            # Define table style with proper alignment
            table_style = TableStyle([
                # Header row styling
                ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
                ('TOPPADDING', (0, 0), (-1, 0), 10),
                
                # Data row styling
                ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 8),
                ('TOPPADDING', (0, 1), (-1, -1), 8),
                
                # Column alignment
                # Text columns (left aligned)
                ('ALIGN', (0, 1), (2, -1), 'LEFT') if is_all_employees else ('ALIGN', (0, 1), (0, -1), 'LEFT'),
                # Date column (center aligned)
                ('ALIGN', (3, 1), (3, -1), 'CENTER') if is_all_employees else ('ALIGN', (0, 1), (0, -1), 'CENTER'),
                # Numeric columns (center aligned)
                ('ALIGN', (4, 1), (-1, -1), 'CENTER') if is_all_employees else ('ALIGN', (1, 1), (-1, -1), 'CENTER'),
                
                # Word wrap for long text (employee names)
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ])
            
            table.setStyle(table_style)
            story.append(table)
        else:
            story.append(Paragraph("No attendance records found for this period.", normal_style))
        
        story.append(Spacer(1, 0.3*inch))
        
        # Summary - works for both report types
        # Use effective report status so REJECTED approvals count strictly as ABSENT
        from app import get_effective_report_status
        present = len([a for a in attendances if get_effective_report_status(a) == 'present'])
        absent = len([a for a in attendances if get_effective_report_status(a) == 'absent'])
        half_day = len([a for a in attendances if get_effective_report_status(a) == 'half_day'])
        late = len([a for a in attendances if a.late_entry])
        
        summary_data = [
            ['Total Records:', str(len(attendances))],
            ['Present:', str(present)],
            ['Absent:', str(absent)],
            ['Half Days:', str(half_day)],
            ['Late Arrivals:', str(late)]
        ]
        
        summary_table = Table(summary_data, colWidths=[2*inch, 2*inch])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.lightgrey),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        story.append(summary_table)
        
        # Page number function
        def on_page(canvas, doc):
            canvas.saveState()
            # Page number at bottom center
            page_num = canvas.getPageNumber()
            canvas.setFont('Helvetica', 9)
            canvas.setFillColor(colors.grey)
            canvas.drawCentredString(
                pagesize[0] / 2,
                0.5*inch,
                f"Page {page_num}"
            )
            canvas.restoreState()
        
        # Build PDF with page numbers
        doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
        
        return output_path
