"""
Payment receipt PDF (VENDOR side).

Built the moment a payment has been captured and attached to the license
email, so the customer has a record of what they paid. It is a PAYMENT
RECEIPT - not a GST tax invoice. If you are GST-registered and must issue
tax invoices, they need extra fields (tax split, SAC code) that are not here.

Look
----
A coloured header with your company name (and logo, if you set one), a
"From / Billed To" block, a PAID badge with the amount in a summary card, an
item table, the payment details (transaction id, payment id, date, status) and
a footer. Your company details come from the vendor environment
(.env.vendor), all optional except the name:

    COMPANY_NAME        shown big in the header
    COMPANY_ADDRESS     use "|" for line breaks:  12 MG Road|Pune 411001|India
    COMPANY_PHONE
    SUPPORT_EMAIL
    COMPANY_WEBSITE
    COMPANY_GSTIN       shown only if set
    COMPANY_LOGO_PATH   path to a PNG/JPG, drawn in the header if the file exists

"Application ID" is the customer's record number on the vendor side
(customers.id) written as APP-00012.

Only plain text in the standard PDF fonts is drawn (they have no rupee sign),
so amounts read "INR 9,999.00".

Input is a plain dict (see CustomerService._receipt_data); nothing in this
module touches the database, so it is easy to test.
"""
import io
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

_IST = timezone(timedelta(hours=5, minutes=30))
_MAX_TEXT = 90


def application_id(customer_id: Optional[int]) -> str:
    """The customer's Application ID, e.g. 12 -> 'APP-00012'."""
    return f"APP-{int(customer_id):05d}" if customer_id else "-"


def receipt_number(order_pk: int, when: Optional[datetime] = None) -> str:
    """Receipt number for an order, e.g. 'RCPT-2026-00042'."""
    when = when or datetime.now(timezone.utc)
    return f"RCPT-{when.year}-{int(order_pk):05d}"


def format_amount(minor_units: Optional[int], currency: str = "INR") -> str:
    """9999900 paise -> 'INR 99,999.00'."""
    if minor_units is None:
        return "-"
    return f"{(currency or 'INR').upper()} {int(minor_units) / 100:,.2f}"


def _fmt_dt(value: Optional[datetime]) -> str:
    """Date and time in IST, e.g. '23 October 2026, 02:41 PM IST'."""
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_IST).strftime("%d %B %Y, %I:%M %p IST")


def _fmt_date(value: Optional[datetime]) -> str:
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_IST).strftime("%d %B %Y")


def _clean(value) -> str:
    """One line, trimmed. ReportLab draws plain table strings as-is (no markup parsing)."""
    text = " ".join(str(value if value not in (None, "") else "-").split())
    return text[:_MAX_TEXT]


def _company() -> Dict[str, str]:
    env = os.environ.get
    return {
        "company_name": env("COMPANY_NAME", "Your Company Name"),
        "company_address": env("COMPANY_ADDRESS", ""),
        "company_phone": env("COMPANY_PHONE", ""),
        "support_email": env("SUPPORT_EMAIL", "support@yourcompany.com"),
        "company_website": env("COMPANY_WEBSITE", "https://example.com"),
        "company_gstin": env("COMPANY_GSTIN", ""),
        "company_logo_path": env("COMPANY_LOGO_PATH", ""),
    }


def receipt_rows(r: Dict[str, object]) -> list:
    """The (label, value) pairs shown on the receipt, in order. Also used in the email body."""
    valid_until = r.get("valid_until")
    return [
        ("Receipt No", _clean(r.get("receipt_no"))),
        ("Application ID", _clean(r.get("application_id"))),
        ("Customer Name", _clean(r.get("customer_name"))),
        ("Email", _clean(r.get("customer_email"))),
        ("Phone", _clean(r.get("customer_phone"))),
        ("Plan", _clean(r.get("plan_name"))),
        ("Amount Paid", format_amount(r.get("amount"), r.get("currency") or "INR")),
        ("Payment Status", _clean(r.get("status") or "Paid")),
        ("Transaction ID", _clean(r.get("order_id"))),
        ("Payment ID", _clean(r.get("payment_id"))),
        ("Payment Received On", _fmt_dt(r.get("paid_at"))),
        ("License Valid Until", "Never (lifetime license)" if valid_until is None else _fmt_date(valid_until)),
    ]


# palette
_INDIGO = colors.HexColor("#312e81")
_ACCENT = colors.HexColor("#6366f1")
_SOFT = colors.HexColor("#eef2ff")
_TEXT = colors.HexColor("#0f172a")
_GREY = colors.HexColor("#64748b")
_LINE = colors.HexColor("#e2e8f0")
_GREEN = colors.HexColor("#16a34a")


def _logo(path: str):
    """An ImageReader for the company logo, or None if unset / unreadable."""
    if not path or not os.path.isfile(path):
        return None
    try:
        return ImageReader(path)
    except Exception:
        return None


def _lines(text: str, font: str, size: float, width: float, limit: int = 3) -> list:
    return simpleSplit(_clean(text), font, size, width)[:limit]


def build_receipt_pdf(r: Dict[str, object]) -> bytes:
    """Render the receipt for `r` and return the PDF bytes."""
    info = {**_company(), **{k: v for k, v in r.items() if k in _company() and v}}
    company = _clean(info["company_name"])
    receipt_no = _clean(r.get("receipt_no"))
    currency = r.get("currency") or "INR"
    amount = format_amount(r.get("amount"), currency)
    plan = _clean(r.get("plan_name"))
    valid_until = r.get("valid_until")
    valid_text = "Never (lifetime)" if valid_until is None else _fmt_date(valid_until)
    status = _clean(r.get("status") or "Paid")

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4, pageCompression=0)
    c.setTitle(f"Payment Receipt {receipt_no}")
    c.setAuthor(company)
    width, height = A4
    margin = 18 * mm
    inner = width - 2 * margin

    # ---- header band ------------------------------------------------------
    band = 38 * mm
    c.setFillColor(_INDIGO)
    c.rect(0, height - band, width, band, stroke=0, fill=1)
    c.setFillColor(_ACCENT)
    c.rect(0, height - band - 1.6 * mm, width, 1.6 * mm, stroke=0, fill=1)

    x = margin
    logo = _logo(str(info.get("company_logo_path") or ""))
    if logo is not None:
        box = 18 * mm
        c.drawImage(logo, x, height - band + (band - box) / 2, width=box, height=box,
                    preserveAspectRatio=True, anchor="c", mask="auto")
        x += box + 5 * mm
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(x, height - 18 * mm, company[:38])
    c.setFont("Helvetica", 9)
    c.setFillColor(colors.HexColor("#c7d2fe"))
    c.drawString(x, height - 24.5 * mm, "AI Attendance & Payroll System")

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 15)
    c.drawRightString(width - margin, height - 16 * mm, "PAYMENT RECEIPT")
    c.setFont("Helvetica", 9)
    c.setFillColor(colors.HexColor("#c7d2fe"))
    c.drawRightString(width - margin, height - 23 * mm, f"Receipt No: {receipt_no}")
    c.drawRightString(width - margin, height - 28 * mm,
                      f"Issued: {_fmt_date(r.get('issued_at') or datetime.now(timezone.utc))}")

    # ---- From / Billed To -------------------------------------------------
    y_top = height - band - 14 * mm
    col_w = inner / 2 - 6 * mm
    right_x = margin + inner / 2 + 6 * mm

    def block(x0, title, rows):
        """rows: list of (text, bold). Returns the y below the block."""
        y = y_top
        c.setFillColor(_ACCENT)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(x0, y, title)
        y -= 5.5 * mm
        for text, bold in rows:
            font = "Helvetica-Bold" if bold else "Helvetica"
            c.setFont(font, 10 if bold else 9)
            c.setFillColor(_TEXT if bold else _GREY)
            for line in _lines(text, font, 10 if bold else 9, col_w):
                c.drawString(x0, y, line)
                y -= 4.6 * mm
        return y

    address = [part.strip() for part in str(info.get("company_address") or "").split("|") if part.strip()]
    from_rows = [(company, True)] + [(a, False) for a in address]
    if info.get("company_phone"):
        from_rows.append((f"Phone: {info['company_phone']}", False))
    from_rows.append((str(info.get("support_email") or ""), False))
    from_rows.append((str(info.get("company_website") or ""), False))
    if info.get("company_gstin"):
        from_rows.append((f"GSTIN: {info['company_gstin']}", False))
    billed_rows = [(_clean(r.get("customer_name")), True),
                   (_clean(r.get("customer_email")), False),
                   (_clean(r.get("customer_phone")), False),
                   (f"Application ID: {_clean(r.get('application_id'))}", False)]
    y = min(block(margin, "FROM", from_rows), block(right_x, "BILLED TO", billed_rows))

    # ---- amount card + PAID badge -----------------------------------------
    card_h = 26 * mm
    card_y = y - 4 * mm - card_h
    c.setFillColor(_SOFT)
    c.setStrokeColor(colors.HexColor("#c7d2fe"))
    c.roundRect(margin, card_y, inner, card_h, 4 * mm, stroke=1, fill=1)
    c.setFillColor(_GREY)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(margin + 7 * mm, card_y + card_h - 8 * mm, "AMOUNT PAID")
    c.setFillColor(_INDIGO)
    c.setFont("Helvetica-Bold", 26)
    c.drawString(margin + 7 * mm, card_y + 6.5 * mm, amount)

    pill_w, pill_h = 26 * mm, 8.5 * mm
    pill_x = margin + inner - 7 * mm - pill_w
    pill_y = card_y + card_h - 7 * mm - pill_h
    paid = status.lower() == "paid"
    c.setFillColor(_GREEN if paid else colors.HexColor("#d97706"))
    c.roundRect(pill_x, pill_y, pill_w, pill_h, pill_h / 2, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(pill_x + pill_w / 2, pill_y + 2.7 * mm, status.upper())
    c.setFillColor(_GREY)
    c.setFont("Helvetica", 8.5)
    c.drawRightString(margin + inner - 7 * mm, card_y + 6.5 * mm, _fmt_dt(r.get("paid_at")))
    y = card_y - 9 * mm

    # ---- item table -------------------------------------------------------
    items = Table(
        [["DESCRIPTION", "VALID UNTIL", "AMOUNT"],
         [f"AI Attendance & Payroll System\n{plan} - 1 computer", valid_text, amount],
         ["", "Total paid", amount]],
        colWidths=[inner * 0.55, inner * 0.23, inner * 0.22])
    items.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _INDIGO),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, 1), 10),
        ("LEADING", (0, 1), (-1, 1), 14),
        ("TEXTCOLOR", (0, 1), (-1, 1), _TEXT),
        ("FONTNAME", (1, 2), (-1, 2), "Helvetica-Bold"),
        ("FONTSIZE", (1, 2), (-1, 2), 10),
        ("TEXTCOLOR", (1, 2), (-1, 2), _INDIGO),
        ("ALIGN", (1, 0), (1, -1), "LEFT"),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("LINEBELOW", (0, 1), (-1, 1), 0.5, _LINE),
        ("BACKGROUND", (0, 2), (-1, 2), _SOFT),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    _w, item_h = items.wrapOn(c, inner, height)
    items.drawOn(c, margin, y - item_h)
    y -= item_h + 10 * mm

    # ---- payment details --------------------------------------------------
    c.setFillColor(_ACCENT)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(margin, y, "PAYMENT DETAILS")
    y -= 3 * mm
    fingerprint = _clean(r.get("machine_fingerprint"))
    half = (len(fingerprint) + 1) // 2
    details = Table(
        [["Payment Status", status],
         ["Transaction ID", _clean(r.get("order_id"))],
         ["Payment ID", _clean(r.get("payment_id"))],
         ["Payment Received On", _fmt_dt(r.get("paid_at"))],
         ["Licensed Computer", f"{fingerprint[:half]}\n{fingerprint[half:]}" if len(fingerprint) > 40 else fingerprint]],
        colWidths=[inner * 0.30, inner * 0.70])
    details.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("LEADING", (0, 0), (-1, -1), 12),
        ("TEXTCOLOR", (0, 0), (0, -1), _GREY),
        ("TEXTCOLOR", (1, 0), (1, -1), _TEXT),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, _LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    _w, det_h = details.wrapOn(c, inner, height)
    details.drawOn(c, margin, y - det_h)

    # ---- footer -----------------------------------------------------------
    c.setStrokeColor(_LINE)
    c.line(margin, 30 * mm, margin + inner, 30 * mm)
    c.setFillColor(_INDIGO)
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(width / 2, 23.5 * mm, "Thank you for your purchase!")
    c.setFillColor(_GREY)
    c.setFont("Helvetica", 8)
    note = ("This is a computer-generated payment receipt and does not need a signature. "
            f"Questions? {_clean(info['support_email'])}  |  {_clean(info['company_website'])}")
    for i, line in enumerate(simpleSplit(note, "Helvetica", 8, inner)):
        c.drawCentredString(width / 2, (18 - 4 * i) * mm, line)

    c.showPage()
    c.save()
    return buffer.getvalue()