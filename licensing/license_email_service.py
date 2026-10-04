"""
License Key Email Dispatch Service (VENDOR side)

Sends the signed license token and installation instructions to the
customer the moment the payment webhook has issued it.

Features:
- SMTP (STARTTLS on 587 by default, implicit SSL on 465)
- Multipart plain-text + HTML body
- The token is also attached as a ready-to-use `license.lic` file
- Plan name and expiry date shown in the message
- Every customer-supplied value is HTML-escaped and stripped of line breaks
  before it goes into the message (no HTML / header injection through the
  "Name" field of the public purchase form)
- Configurable via environment variables on the vendor server:
    MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD, MAIL_USE_TLS,
    MAIL_DEFAULT_SENDER, SENDER_NAME, SUPPORT_EMAIL, COMPANY_NAME,
    COMPANY_WEBSITE   (SMTP_* names are accepted as fallbacks)
"""

import html
import logging
import os
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Optional

logger = logging.getLogger(__name__)

_SMTP_TIMEOUT_SECONDS = 30


def _one_line(value: Optional[str], limit: int = 200) -> str:
    """Collapse whitespace/newlines so a value is safe inside a header."""
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _esc(value: Optional[str]) -> str:
    return html.escape(_one_line(value), quote=True)


class LicenseEmailService:
    """Service for sending license keys via email."""

    def __init__(self):
        env = os.environ.get
        self.smtp_server = env('MAIL_SERVER', env('SMTP_SERVER', 'smtp.gmail.com'))
        self.smtp_port = int(env('MAIL_PORT', env('SMTP_PORT', '587')))
        self.smtp_username = env('MAIL_USERNAME', env('SMTP_USERNAME', ''))
        self.smtp_password = env('MAIL_PASSWORD', env('SMTP_PASSWORD', ''))
        self.smtp_use_tls = env('MAIL_USE_TLS', env('SMTP_USE_TLS', 'true')).lower() == 'true'

        self.sender_email = env('MAIL_DEFAULT_SENDER', env('SENDER_EMAIL', self.smtp_username))
        self.sender_name = env('SENDER_NAME', 'AI Attendance & Payroll System')
        self.support_email = env('SUPPORT_EMAIL', 'support@yourcompany.com')
        self.company_name = env('COMPANY_NAME', 'Your Company Name')
        self.company_website = env('COMPANY_WEBSITE', 'https://example.com')

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def is_configured(self) -> bool:
        return bool(self.smtp_username and self.smtp_password)

    def send_license_key_email(self, customer_name: str, customer_email: str,
                               license_key: str, machine_fingerprint: str,
                               plan_name: Optional[str] = None,
                               expires_at: Optional[datetime] = None) -> bool:
        """
        Send the license token to the customer.

        Args:
            customer_name: Customer's name
            customer_email: Customer's email address
            license_key: signed Ed25519 license token ("<payload>.<signature>")
            machine_fingerprint: the fingerprint the token is bound to
            plan_name: e.g. "Yearly Plan" (optional, shown in the email)
            expires_at: UTC expiry, or None for a lifetime license

        Returns:
            bool: True if the message was accepted by the SMTP server
        """
        try:
            customer_email = _one_line(customer_email)
            if not customer_email or '@' not in customer_email:
                logger.error("Invalid email address: %r", customer_email)
                return False

            if not license_key or '.' not in license_key:
                logger.error("Invalid license token: missing payload/signature separator")
                return False

            if not self.is_configured:
                logger.warning("SMTP credentials not configured - license email to %s NOT sent "
                               "(the token is stored on the customer record; resend it from the "
                               "admin API once SMTP is configured)", customer_email)
                return False

            msg = self._build_message(customer_name, customer_email, license_key,
                                      machine_fingerprint, plan_name, expires_at)
            self._deliver(msg)
            logger.info("License key email sent to %s", customer_email)
            return True

        except smtplib.SMTPAuthenticationError as e:
            logger.error("SMTP authentication failed: %s", e)
            return False
        except smtplib.SMTPException as e:
            logger.error("SMTP error sending email: %s", e)
            return False
        except Exception as e:
            logger.error("Error sending license key email: %s", e)
            return False

    def send_payment_confirmation_email(self, customer_name: str, customer_email: str) -> bool:
        """Send a short 'payment received' note (no license attached)."""
        try:
            customer_email = _one_line(customer_email)
            if not self.is_configured:
                logger.warning("SMTP credentials not configured - skipping payment confirmation email")
                return False
            if not customer_email or '@' not in customer_email:
                return False

            msg = MIMEMultipart('alternative')
            msg['Subject'] = f'Payment Confirmation - {_one_line(self.company_name)}'
            msg['From'] = formataddr((_one_line(self.sender_name), self.sender_email))
            msg['To'] = formataddr((_one_line(customer_name), customer_email))

            body = f"""
<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Payment Confirmation</title></head>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
  <div style="background-color: #f9f9f9; padding: 30px; border-radius: 10px;">
    <h1 style="color: #4f46e5;">Payment Received</h1>
    <p>Dear {_esc(customer_name)},</p>
    <p>We have received your payment for the AI Attendance &amp; Payroll System.</p>
    <p>Your license key is being prepared and will arrive in a separate email shortly.</p>
    <hr style="margin: 20px 0; border: none; border-top: 1px solid #e2e8f0;">
    <p style="font-size: 12px; color: #64748b;">
      <strong>{_esc(self.company_name)}</strong><br>Support: {_esc(self.support_email)}
    </p>
  </div>
</body></html>
"""
            msg.attach(MIMEText(
                f"Dear {_one_line(customer_name)},\n\nWe have received your payment. "
                f"Your license key will arrive in a separate email shortly.\n\n"
                f"{_one_line(self.company_name)}\nSupport: {_one_line(self.support_email)}\n",
                'plain', 'utf-8'))
            msg.attach(MIMEText(body, 'html', 'utf-8'))
            self._deliver(msg)
            logger.info("Payment confirmation email sent to %s", customer_email)
            return True
        except Exception as e:
            logger.error("Error sending payment confirmation email: %s", e)
            return False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _deliver(self, msg) -> None:
        """Hand a finished message to the SMTP server. Raises on failure.
        (Separate method so tests can replace it without a network.)"""
        if self.smtp_port == 465:
            with smtplib.SMTP_SSL(self.smtp_server, self.smtp_port,
                                  timeout=_SMTP_TIMEOUT_SECONDS,
                                  context=ssl.create_default_context()) as server:
                server.login(self.smtp_username, self.smtp_password)
                server.send_message(msg)
            return

        with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=_SMTP_TIMEOUT_SECONDS) as server:
            if self.smtp_use_tls:
                server.starttls(context=ssl.create_default_context())
            server.login(self.smtp_username, self.smtp_password)
            server.send_message(msg)

    @staticmethod
    def _format_expiry(expires_at: Optional[datetime]) -> str:
        if expires_at is None:
            return "Never - this is a lifetime license"
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at.astimezone(timezone.utc).strftime('%d %B %Y (UTC)')

    def _build_message(self, customer_name, customer_email, license_key,
                       machine_fingerprint, plan_name, expires_at):
        msg = MIMEMultipart('mixed')
        msg['Subject'] = f'Your License Key - {_one_line(self.company_name)}'
        msg['From'] = formataddr((_one_line(self.sender_name), self.sender_email))
        msg['To'] = formataddr((_one_line(customer_name), customer_email))

        alternative = MIMEMultipart('alternative')
        alternative.attach(MIMEText(
            self._create_plain_text(customer_name, license_key, machine_fingerprint,
                                    plan_name, expires_at), 'plain', 'utf-8'))
        alternative.attach(MIMEText(
            self._create_email_template(customer_name, license_key, machine_fingerprint,
                                        plan_name, expires_at), 'html', 'utf-8'))
        msg.attach(alternative)

        attachment = MIMEApplication(license_key.strip().encode('utf-8'), Name='license.lic')
        attachment['Content-Disposition'] = 'attachment; filename="license.lic"'
        msg.attach(attachment)
        return msg

    def _create_plain_text(self, customer_name, license_key, machine_fingerprint,
                           plan_name, expires_at) -> str:
        return (
            f"Dear {_one_line(customer_name)},\n\n"
            f"Thank you for your purchase. Your payment was received and your license is ready.\n\n"
            f"Plan:    {_one_line(plan_name) or 'Standard'}\n"
            f"Expires: {self._format_expiry(expires_at)}\n"
            f"Machine: {_one_line(machine_fingerprint)[:16]}... (the computer this license is bound to)\n\n"
            f"YOUR LICENSE KEY (copy everything between the lines):\n"
            f"----------------------------------------\n"
            f"{license_key.strip()}\n"
            f"----------------------------------------\n\n"
            f"HOW TO ACTIVATE\n"
            f"  1. Open the AI Attendance & Payroll System on the computer you purchased for.\n"
            f"  2. On the 'License Activation' screen, paste the key into the box and click Activate.\n"
            f"  Alternative: save the attached 'license.lic' file in the application's folder\n"
            f"  (next to the .exe) and restart the application.\n\n"
            f"IMPORTANT\n"
            f"  - This key only works on the computer whose Machine Fingerprint you gave us.\n"
            f"  - Do not share it. Keep this email for your records.\n\n"
            f"Need help? {_one_line(self.support_email)}\n"
            f"{_one_line(self.company_name)} - {_one_line(self.company_website)}\n"
        )

    def _create_email_template(self, customer_name: str, license_key: str,
                               machine_fingerprint: str, plan_name: Optional[str] = None,
                               expires_at: Optional[datetime] = None) -> str:
        """HTML email body. Every interpolated value is escaped."""
        current_date = datetime.now().strftime('%B %d, %Y')
        name = _esc(customer_name)
        plan = _esc(plan_name) or "Standard"
        expiry = _esc(self._format_expiry(expires_at))
        fingerprint_short = _esc(machine_fingerprint)[:16]
        token = html.escape(license_key.strip(), quote=True)
        company = _esc(self.company_name)
        website = _esc(self.company_website)
        support = _esc(self.support_email)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Your License Key</title>
<style>
  body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #333;
         max-width: 600px; margin: 0 auto; padding: 20px; }}
  .container {{ background-color: #f9f9f9; border-radius: 10px; padding: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
  .header {{ text-align: center; margin-bottom: 30px; padding-bottom: 20px; border-bottom: 2px solid #4f46e5; }}
  .header h1 {{ color: #4f46e5; margin: 0; font-size: 26px; }}
  .plan {{ background: #fff; border-radius: 8px; padding: 12px 18px; margin: 18px 0; }}
  .plan td {{ padding: 3px 12px 3px 0; font-size: 14px; }}
  .license-box {{ background-color: #eef2ff; border: 2px solid #4f46e5; border-radius: 8px; padding: 20px;
                 text-align: center; margin: 20px 0; }}
  .license-key {{ font-family: 'Courier New', monospace; font-size: 12px; font-weight: bold; color: #4f46e5;
                 word-break: break-all; margin: 10px 0; text-align: left; }}
  .info-box {{ background-color: #f0f9ff; border-left: 4px solid #0284c7; padding: 15px; margin: 20px 0; }}
  .info-box h3 {{ margin-top: 0; color: #0284c7; }}
  .warning {{ background-color: #fff1f2; border-left: 4px solid #e11d48; padding: 15px; margin: 20px 0; color: #881337; }}
  .footer {{ text-align: center; margin-top: 30px; padding-top: 20px; border-top: 1px solid #e2e8f0;
            color: #64748b; font-size: 12px; }}
  .footer a {{ color: #4f46e5; text-decoration: none; }}
</style>
</head>
<body>
<div class="container">
  <div class="header"><h1>Your License Key is Ready</h1></div>

  <p>Dear <strong>{name}</strong>,</p>
  <p>Thank you for your purchase of the <strong>AI Attendance &amp; Payroll System</strong>.
  Your payment was received and your license has been issued automatically.</p>

  <table class="plan">
    <tr><td><strong>Plan</strong></td><td>{plan}</td></tr>
    <tr><td><strong>Valid until</strong></td><td>{expiry}</td></tr>
    <tr><td><strong>Bound to machine</strong></td><td><code>{fingerprint_short}&hellip;</code></td></tr>
  </table>

  <div class="license-box">
    <h3 style="margin:0;">Your License Key</h3>
    <div class="license-key">{token}</div>
    <p style="margin:0;font-size:13px;color:#64748b;">Copy the whole key exactly as shown. It is also attached as <strong>license.lic</strong>.</p>
  </div>

  <div class="info-box">
    <h3>Activation instructions</h3>
    <ol>
      <li>Open the AI Attendance &amp; Payroll System on the computer you purchased for.</li>
      <li>On the <strong>License Activation</strong> screen, paste the key into the box.</li>
      <li>Click <strong>Activate</strong> - the application unlocks immediately.</li>
    </ol>
    <p style="margin-bottom:0;"><em>Alternative:</em> save the attached <code>license.lic</code> in the
    application's folder (next to the .exe) and restart the application.</p>
  </div>

  <div class="warning">
    <strong>Important</strong>
    <ul style="margin: 10px 0 0 20px; padding: 0;">
      <li>This key only works on the computer whose Machine Fingerprint you provided.</li>
      <li>Do not share this key with anyone.</li>
      <li>Keep this email for your records.</li>
    </ul>
  </div>

  <p>If you have any trouble activating, just reply to this email or contact our support team.</p>

  <div class="footer">
    <p><strong>{company}</strong></p>
    <p>Website: <a href="{website}">{website}</a></p>
    <p>Support: <a href="mailto:{support}">{support}</a></p>
    <p>Generated on {current_date}</p>
  </div>
</div>
</body>
</html>
"""


# Singleton instance
_license_email_service = None


def get_license_email_service() -> LicenseEmailService:
    """Get the singleton LicenseEmailService instance."""
    global _license_email_service
    if _license_email_service is None:
        _license_email_service = LicenseEmailService()
    return _license_email_service
