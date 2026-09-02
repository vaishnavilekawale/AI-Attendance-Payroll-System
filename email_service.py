import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from email.utils import formataddr, formatdate, make_msgid
import os
from config import Config
import logging

# Removed logging.basicConfig() to avoid conflict with app.py logging configuration
# Logging is now configured centrally in app.py with force=True to ensure all logs appear
logger = logging.getLogger(__name__)

class EmailService:
    def __init__(self):
        self.smtp_server = Config.MAIL_SERVER
        self.smtp_port = Config.MAIL_PORT
        self.smtp_username = Config.MAIL_USERNAME
        self.smtp_password = Config.MAIL_PASSWORD
        self.use_tls = Config.MAIL_USE_TLS
        self.default_sender = Config.MAIL_DEFAULT_SENDER
        self.company_name = Config.COMPANY_NAME if hasattr(Config, 'COMPANY_NAME') else 'AI Attendance System'

        logger.info(
            "[Email Service] SMTP configuration loaded: server=%s, port=%s, username=%s, password=%s, sender=%s",
            self.smtp_server,
            self.smtp_port,
            "SET" if self.smtp_username else "NOT SET",
            "SET" if self.smtp_password else "NOT SET",
            self.default_sender or "NOT SET"
        )
    
    def send_email(self, to_email, subject, html_body, text_body=None, attachments=None):
        """
        Send an email with a proper multipart/mixed + multipart/alternative
        MIME structure - the standard, deliverability-friendly layout for
        "HTML + plain-text fallback + file attachment(s)":

            multipart/mixed                  <- outer message
              multipart/alternative          <- the actual message content
                text/plain                   <- fallback for clients/filters
                                                 that can't/won't render HTML
                text/html
              application/pdf (or similar)   <- attachment(s), as siblings
                                                 of the alternative part, NOT
                                                 nested inside it

        Mixing attachments directly into a flat multipart/alternative (as a
        3rd "alternative" alongside plain/html) is a common anti-pattern:
        some mail/spam filters interpret an attachment sitting inside
        multipart/alternative as an ambiguous/malformed alternative
        representation of the message body, which can hurt inbox placement.
        Nesting it this way avoids that.

        Always supplying a text_body (auto-derived from the HTML if the
        caller doesn't pass one) also matters for deliverability: an
        HTML-only email with no plain-text part is itself a common spam
        signal.
        """
        logger.info(f"[Email Service] Attempting to send email")
        logger.info(f"[Email Service] Recipient (to_email): {to_email}")
        logger.info(f"[Email Service] Subject: {subject}")
        logger.info(f"[Email Service] Sender (default_sender): {self.default_sender}")
        logger.info(f"[Email Service] SMTP Server: {self.smtp_server}:{self.smtp_port}")
        logger.info(f"[Email Service] SMTP Username: {self.smtp_username}")

        if not self.smtp_username or not self.smtp_password:
            logger.error("[Email Service] ERROR: SMTP credentials not configured")
            return {'success': False, 'message': 'SMTP credentials not configured'}

        if not text_body:
            text_body = self._html_to_plain_text(html_body)

        try:
            # Outer container: mixed (holds the alternative body + attachments)
            msg = MIMEMultipart('mixed')
            msg['From'] = formataddr((self.company_name, self.default_sender))
            msg['To'] = formataddr(('', to_email))
            msg['Reply-To'] = self.default_sender
            msg['Subject'] = subject
            msg['Date'] = formatdate(localtime=True)
            msg['Message-ID'] = make_msgid()
            # Helps some spam filters identify this as a genuine
            # transactional/bulk-to-one message rather than mass mail.
            msg['X-Mailer'] = f"{self.company_name} HR Payroll System"

            # Inner container: alternative (plain-text fallback + HTML)
            alt_part = MIMEMultipart('alternative')
            alt_part.attach(MIMEText(text_body, 'plain', _charset='utf-8'))
            alt_part.attach(MIMEText(html_body, 'html', _charset='utf-8'))
            msg.attach(alt_part)

            # Attachments live as siblings of the alternative part, not
            # inside it, with a proper content type per file (so PDFs show
            # up as PDFs in the recipient's mail client rather than a
            # generic "unknown file").
            if attachments:
                for attachment_path in attachments:
                    if not os.path.exists(attachment_path):
                        logger.warning(f"[Email Service] Attachment not found, skipping: {attachment_path}")
                        continue

                    filename = os.path.basename(attachment_path)
                    ext = os.path.splitext(filename)[1].lower()

                    if ext == '.pdf':
                        main_type, sub_type = 'application', 'pdf'
                    else:
                        main_type, sub_type = 'application', 'octet-stream'

                    with open(attachment_path, 'rb') as attachment_file:
                        part = MIMEBase(main_type, sub_type)
                        part.set_payload(attachment_file.read())
                        encoders.encode_base64(part)
                        part.add_header(
                            'Content-Disposition',
                            f'attachment; filename="{filename}"'
                        )
                        msg.attach(part)

            # Connect to SMTP server
            logger.info(f"[Email Service] Connecting to SMTP server...")
            if self.use_tls:
                server = smtplib.SMTP(self.smtp_server, self.smtp_port)
                server.starttls()
                logger.info(f"[Email Service] TLS enabled")
            else:
                server = smtplib.SMTP(self.smtp_server, self.smtp_port)
                logger.info(f"[Email Service] TLS disabled")
            
            logger.info(f"[Email Service] Logging in to SMTP server...")
            server.login(self.smtp_username, self.smtp_password)
            logger.info(f"[Email Service] SMTP login successful")
            
            logger.info(f"[Email Service] Sending email to {to_email}...")
            server.send_message(msg)
            server.quit()
            
            logger.info(f"[Email Service] ✓ Email sent successfully to {to_email}")
            return {'success': True, 'message': 'Email sent successfully'}
        
        except Exception as e:
            logger.error(f"[Email Service] ✗ ERROR sending email to {to_email}: {e}")
            import traceback
            logger.error(f"[Email Service] Full traceback: {traceback.format_exc()}")
            return {'success': False, 'message': str(e)}

    @staticmethod
    def _html_to_plain_text(html_body):
        """
        Very small, dependency-free HTML->plain-text fallback generator,
        used only when a caller doesn't supply its own text_body. Good
        enough for the simple, templated emails this service sends (it
        doesn't need to handle arbitrary HTML) - just enough structure so
        the plain-text alternative isn't a wall of tags for anyone whose
        mail client renders it instead of the HTML part.
        """
        import re
        text = re.sub(r'(?i)<br\s*/?>', '\n', html_body)
        text = re.sub(r'(?i)</p\s*>', '\n\n', text)
        text = re.sub(r'(?i)</h[1-6]\s*>', '\n\n', text)
        text = re.sub(r'(?i)</li\s*>', '\n', text)
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def send_payslip(self, employee_email, employee_name, payslip_path, month, year, pdf_password=None):
        """
        Send payslip PDF to employee with a clean, deliverability-friendly
        email format.

        Security note: the ACTUAL password is intentionally NEVER placed
        in the email body, regardless of what is passed in `pdf_password`.
        `pdf_password` is only used as a boolean flag (has the PDF been
        protected at all?) to decide whether to show the "how to unlock
        this PDF" instructions. The instructions explain the *rule* used
        to build the password (name + DOB), with a fixed, generic
        worked example - never the recipient's own real password - so an
        intercepted email can never be used on its own to open the
        attachment; the recipient still needs to know their own DOB.
        """
        subject = f"Payslip for {month} {year} - {self.company_name}"

        password_notice_html = ""
        password_notice_text = ""
        if pdf_password:
            password_notice_html = """
            <div style="margin: 16px 0; padding: 12px 16px; background-color: #F5F7FA; border-left: 4px solid #0B3D91; border-radius: 4px;">
                <p style="margin: 0 0 8px 0;"><strong>This payslip PDF is password protected for your privacy.</strong></p>
                <p style="margin: 0 0 8px 0;">
                    To open it, use the password format:
                    <strong>first 4 letters of your name in CAPITALS + your date of birth (DDMM)</strong>.
                </p>
                <p style="margin: 0; font-size: 13px; color: #555;">
                    Example: if your name is <strong>RAMESH</strong> and your date of birth is
                    <strong>15th August</strong>, your password would be <strong>RAME1508</strong>.
                </p>
            </div>
            """
            password_notice_text = (
                "\nThis payslip PDF is password protected for your privacy.\n"
                "To open it, use the password format: first 4 letters of your name in "
                "CAPITALS + your date of birth (DDMM).\n"
                "Example: if your name is RAMESH and your date of birth is 15th August, "
                "your password would be RAME1508.\n"
            )

        html_body = f"""
        <html>
        <head></head>
        <body style="font-family: Arial, Helvetica, sans-serif; color: #222;">
            <p>Dear {employee_name},</p>
            <p>Please find attached your payslip for <strong>{month} {year}</strong>.</p>
            {password_notice_html}
            <p>If you have any questions about this payslip, please contact HR.</p>
            <p>Thank you,<br>HR Department<br>{self.company_name}</p>
        </body>
        </html>
        """

        text_body = (
            f"Dear {employee_name},\n\n"
            f"Please find attached your payslip for {month} {year}.\n"
            f"{password_notice_text}\n"
            "If you have any questions about this payslip, please contact HR.\n\n"
            f"Thank you,\nHR Department\n{self.company_name}\n"
        )
        
        return self.send_email(
            to_email=employee_email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            attachments=[payslip_path]
        )
    
    def send_attendance_report(self, to_email, employee_name, report_path, start_date, end_date):
        """Send attendance report to employee"""
        subject = f"Attendance Report - {start_date} to {end_date}"
        
        body = f"""
        <html>
        <head></head>
        <body>
            <h2>Dear {employee_name},</h2>
            <p>Please find attached your attendance report from {start_date} to {end_date}.</p>
            <p><strong>Company:</strong> {Config.COMPANY_NAME}</p>
            <p>If you have any questions, please contact HR.</p>
            <br>
            <p>Best regards,</p>
            <p>{Config.COMPANY_NAME} HR Team</p>
        </body>
        </html>
        """
        
        return self.send_email(
            to_email=to_email,
            subject=subject,
            html_body=body,
            attachments=[report_path]
        )
    
    def send_password_reset(self, to_email, username, new_password):
        """Send password reset email to user"""
        subject = f"Password Reset - {Config.COMPANY_NAME}"
        
        text_body = f"""
Password Reset Request

Dear {username},

Your password has been reset successfully.

Your new password is: {new_password}

Please login with your new password and change it immediately for security.

Company: {Config.COMPANY_NAME}

If you did not request this password reset, please contact your administrator immediately.

Best regards,
{Config.COMPANY_NAME} Admin Team
"""
        
        body = f"""
        <html>
        <head></head>
        <body>
            <h2>Password Reset Request</h2>
            <p>Dear {username},</p>
            <p>Your password has been reset successfully.</p>
            <p><strong>Your new password is:</strong> <code>{new_password}</code></p>
            <p>Please login with your new password and change it immediately for security.</p>
            <p><strong>Company:</strong> {Config.COMPANY_NAME}</p>
            <br>
            <p>If you did not request this password reset, please contact your administrator immediately.</p>
            <br>
            <p>Best regards,</p>
            <p>{Config.COMPANY_NAME} Admin Team</p>
        </body>
        </html>
        """
        
        return self.send_email(to_email, subject, body, text_body)
    
    def send_employee_password_reset(self, to_email, employee_name, reset_link):
        """Send password reset link to employee"""
        subject = f"Password Reset Request - {Config.COMPANY_NAME}"
        
        text_body = f"""
Password Reset Request

Dear {employee_name},

We received a request to reset your password for the AI Attendance System.

Please click the link below to reset your password:
{reset_link}

Or copy and paste this link into your browser:
{reset_link}

This link will expire in 1 hour.

Company: {Config.COMPANY_NAME}

If you did not request this password reset, please ignore this email or contact your administrator immediately.

Best regards,
{Config.COMPANY_NAME} HR Team
"""
        
        body = f"""
        <html>
        <head></head>
        <body>
            <h2>Password Reset Request</h2>
            <p>Dear {employee_name},</p>
            <p>We received a request to reset your password for the AI Attendance System.</p>
            <p>Please click the link below to reset your password:</p>
            <p><a href="{reset_link}" style="background-color: #667eea; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Reset Password</a></p>
            <p>Or copy and paste this link into your browser:</p>
            <p><code>{reset_link}</code></p>
            <p><strong>This link will expire in 1 hour.</strong></p>
            <p><strong>Company:</strong> {Config.COMPANY_NAME}</p>
            <br>
            <p>If you did not request this password reset, please ignore this email or contact your administrator immediately.</p>
            <br>
            <p>Best regards,</p>
            <p>{Config.COMPANY_NAME} HR Team</p>
        </body>
        </html>
        """
        
        return self.send_email(to_email, subject, body, text_body)
    
    def send_employee_temp_password(self, to_email, employee_name, employee_id, temp_password):
        """Send temporary password to employee"""
        subject = f"Your {self.company_name} Password Reset Request"
        
        # Plain-text version
        text_body = f"""
Password Reset Request

Dear {employee_name},

We received a request to reset your password for the {self.company_name} employee portal.

Employee ID: {employee_id}
Your Temporary Password: {temp_password}

Important Security Information:
- This is a temporary password that must be changed immediately after logging in.
- You will be redirected to the Change Password page upon login.
- You will not be able to access other features until you change your password.
- For security reasons, please change this password as soon as possible.
- This temporary password will expire in 30 minutes.

If you did not request this password reset, please contact your administrator immediately.

© {self.company_name}. All rights reserved.
This is an automated email. Please do not reply.
"""
        
        # HTML version
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Password Reset Request</title>
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    background-color: #f4f4f4;
                    margin: 0;
                    padding: 20px;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    background-color: #ffffff;
                    border-radius: 10px;
                    overflow: hidden;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                }}
                .header {{
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 30px;
                    text-align: center;
                }}
                .header h1 {{
                    margin: 0;
                    font-size: 24px;
                    font-weight: 600;
                }}
                .content {{
                    padding: 30px;
                }}
                .content h2 {{
                    color: #1e3c72;
                    font-size: 20px;
                    margin-top: 0;
                }}
                .password-box {{
                    background-color: #f8f9fa;
                    border-left: 4px solid #667eea;
                    padding: 20px;
                    margin: 20px 0;
                    border-radius: 5px;
                }}
                .password-box p {{
                    margin: 10px 0;
                    font-size: 16px;
                }}
                .password {{
                    background-color: #e9ecef;
                    padding: 15px;
                    border-radius: 5px;
                    font-family: 'Courier New', monospace;
                    font-size: 18px;
                    font-weight: bold;
                    text-align: center;
                    letter-spacing: 2px;
                    margin: 10px 0;
                }}
                .important {{
                    background-color: #fff3cd;
                    border-left: 4px solid #ffc107;
                    padding: 15px;
                    margin: 20px 0;
                    border-radius: 5px;
                }}
                .important ul {{
                    margin: 10px 0;
                    padding-left: 20px;
                }}
                .important li {{
                    margin: 5px 0;
                }}
                .footer {{
                    background-color: #f8f9fa;
                    padding: 20px;
                    text-align: center;
                    color: #6c757d;
                    font-size: 14px;
                    border-top: 1px solid #e9ecef;
                }}
                .footer p {{
                    margin: 5px 0;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>{self.company_name}</h1>
                </div>
                <div class="content">
                    <h2>Password Reset Request</h2>
                    <p>Dear {employee_name},</p>
                    <p>We received a request to reset your password for the {self.company_name} employee portal.</p>
                    
                    <div class="password-box">
                        <p><strong>Employee ID:</strong> {employee_id}</p>
                        <p><strong>Your Temporary Password:</strong></p>
                        <div class="password">{temp_password}</div>
                    </div>
                    
                    <div class="important">
                        <p><strong>Important Security Information:</strong></p>
                        - This is a <strong>temporary password</strong> that must be changed immediately after logging in.<br>
                        - You will be redirected to the Change Password page upon login.<br>
                        - You will not be able to access other features until you change your password.<br>
                        - For security reasons, please change this password as soon as possible.<br>
                        - This temporary password will expire in 30 minutes.
                    </div>
                    
                    <p>If you did not request this password reset, please contact your administrator immediately.</p>
                </div>
                <div class="footer">
                    <p>&copy; {self.company_name}. All rights reserved.</p>
                    <p>This is an automated email. Please do not reply.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return self.send_email(to_email, subject, html_body, text_body)
    
    def send_admin_temp_password(self, to_email, admin_username, temp_password):
        """Send temporary password to admin"""
        subject = f"Your {self.company_name} Password Reset Request"
        
        # Plain-text version
        text_body = f"""
Password Reset Request

Dear Administrator,

We received a request to reset your password for the {self.company_name} admin account.

Username: {admin_username}
Your Temporary Password: {temp_password}

Important Security Information:
- This is a temporary password that must be changed immediately after logging in.
- You will be redirected to the Change Password page upon login.
- You will not be able to access other features until you change your password.
- For security reasons, please change this password as soon as possible.
- This temporary password will expire in 30 minutes.

If you did not request this password reset, please contact your system administrator immediately.

© {self.company_name}. All rights reserved.
This is an automated email. Please do not reply.
"""
        
        # HTML version
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Password Reset Request</title>
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    background-color: #f4f4f4;
                    margin: 0;
                    padding: 20px;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    background-color: #ffffff;
                    border-radius: 10px;
                    overflow: hidden;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                }}
                .header {{
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 30px;
                    text-align: center;
                }}
                .header h1 {{
                    margin: 0;
                    font-size: 24px;
                    font-weight: 600;
                }}
                .content {{
                    padding: 30px;
                }}
                .content h2 {{
                    color: #1e3c72;
                    font-size: 20px;
                    margin-top: 0;
                }}
                .password-box {{
                    background-color: #f8f9fa;
                    border-left: 4px solid #667eea;
                    padding: 20px;
                    margin: 20px 0;
                    border-radius: 5px;
                }}
                .password-box p {{
                    margin: 10px 0;
                    font-size: 16px;
                }}
                .password {{
                    background-color: #e9ecef;
                    padding: 15px;
                    border-radius: 5px;
                    font-family: 'Courier New', monospace;
                    font-size: 18px;
                    font-weight: bold;
                    text-align: center;
                    letter-spacing: 2px;
                    margin: 10px 0;
                }}
                .important {{
                    background-color: #fff3cd;
                    border-left: 4px solid #ffc107;
                    padding: 15px;
                    margin: 20px 0;
                    border-radius: 5px;
                }}
                .important ul {{
                    margin: 10px 0;
                    padding-left: 20px;
                }}
                .important li {{
                    margin: 5px 0;
                }}
                .footer {{
                    background-color: #f8f9fa;
                    padding: 20px;
                    text-align: center;
                    color: #6c757d;
                    font-size: 14px;
                    border-top: 1px solid #e9ecef;
                }}
                .footer p {{
                    margin: 5px 0;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>{self.company_name}</h1>
                </div>
                <div class="content">
                    <h2>Password Reset Request</h2>
                    <p>Dear Administrator,</p>
                    <p>We received a request to reset your password for the {self.company_name} admin account.</p>
                    
                    <div class="password-box">
                        <p><strong>Username:</strong> {admin_username}</p>
                        <p><strong>Your Temporary Password:</strong></p>
                        <div class="password">{temp_password}</div>
                    </div>
                    
                    <div class="important">
                        <p><strong>Important Security Information:</strong></p>
                        - This is a <strong>temporary password</strong> that must be changed immediately after logging in.<br>
                        - You will be redirected to the Change Password page upon login.<br>
                        - You will not be able to access other features until you change your password.<br>
                        - For security reasons, please change this password as soon as possible.<br>
                        - This temporary password will expire in 30 minutes.
                    </div>
                    
                    <p>If you did not request this password reset, please contact your system administrator immediately.</p>
                </div>
                <div class="footer">
                    <p>&copy; {self.company_name}. All rights reserved.</p>
                    <p>This is an automated email. Please do not reply.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return self.send_email(to_email, subject, html_body, text_body)
    
    def send_welcome_email(self, to_email, employee_name, employee_id, temp_password):
        """Send welcome email to new employee with login credentials"""
        subject = f"Welcome to {self.company_name}"
        
        # Plain-text version
        text_body = f"""
Welcome to {self.company_name}

Dear {employee_name},

Your account has been created successfully.

Employee ID: {employee_id}
Temporary Password: {temp_password}

Please log in using these credentials and change your password after your first login.

Regards,
{self.company_name}
"""
        
        # HTML version
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Welcome to {self.company_name}</title>
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    background-color: #f4f4f4;
                    margin: 0;
                    padding: 20px;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    background-color: #ffffff;
                    border-radius: 10px;
                    overflow: hidden;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                }}
                .header {{
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 30px;
                    text-align: center;
                }}
                .header h1 {{
                    margin: 0;
                    font-size: 24px;
                    font-weight: 600;
                }}
                .content {{
                    padding: 30px;
                }}
                .content h2 {{
                    color: #1e3c72;
                    font-size: 20px;
                    margin-top: 0;
                }}
                .credentials-box {{
                    background-color: #f8f9fa;
                    border-left: 4px solid #667eea;
                    padding: 20px;
                    margin: 20px 0;
                    border-radius: 5px;
                }}
                .credentials-box p {{
                    margin: 10px 0;
                    font-size: 16px;
                }}
                .password {{
                    background-color: #e9ecef;
                    padding: 15px;
                    border-radius: 5px;
                    font-family: 'Courier New', monospace;
                    font-size: 18px;
                    font-weight: bold;
                    text-align: center;
                    letter-spacing: 2px;
                    margin: 10px 0;
                }}
                .important {{
                    background-color: #fff3cd;
                    border-left: 4px solid #ffc107;
                    padding: 15px;
                    margin: 20px 0;
                    border-radius: 5px;
                }}
                .footer {{
                    background-color: #f8f9fa;
                    padding: 20px;
                    text-align: center;
                    color: #6c757d;
                    font-size: 14px;
                    border-top: 1px solid #e9ecef;
                }}
                .footer p {{
                    margin: 5px 0;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>{self.company_name}</h1>
                </div>
                <div class="content">
                    <h2>Welcome aboard!</h2>
                    <p>Dear {employee_name},</p>
                    <p>Your account has been created successfully.</p>
                    
                    <div class="credentials-box">
                        <p><strong>Employee ID:</strong> {employee_id}</p>
                        <p><strong>Temporary Password:</strong></p>
                        <div class="password">{temp_password}</div>
                    </div>
                    
                    <div class="important">
                        <p><strong>Please log in using these credentials and change your password after your first login.</strong></p>
                    </div>
                    
                    <p>Regards,<br>{self.company_name}</p>
                </div>
                <div class="footer">
                    <p>&copy; {self.company_name}. All rights reserved.</p>
                    <p>This is an automated email. Please do not reply.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return self.send_email(to_email, subject, html_body, text_body)
    
    def send_manual_attendance_submission_notification(self, employee_email, employee_name, employee_id, submission_timestamp, is_manager=False):
        """
        Send email notification when an employee submits manual attendance.
        
        This email includes the exact timestamp when the employee clicked "Mark Attendance"
        as proof of check-in time.
        
        For regular employees: notification sent to employee (confirming submission to manager)
        For managers: notification sent to employee and Admin (confirming submission to Admin)
        """
        formatted_timestamp = submission_timestamp.strftime('%Y-%m-%d %H:%M:%S')
        
        if is_manager:
            subject = f"Manual Attendance Submitted - {employee_name} ({employee_id})"
            approver = "Admin"
        else:
            subject = f"Manual Attendance Submitted - {self.company_name}"
            approver = "your manager"

        def _build_bodies(greeting_name, intro_line):
            """Build the plain-text and HTML bodies for a given recipient.

            greeting_name: who the email is addressed to ("Dear ...,")
            intro_line: the first confirmation sentence, which differs
                between the employee's own copy and the Admin's copy so the
                broken pattern of string-replacing an already-interpolated
                f-string (which never matched anything) is avoided entirely.
            """
            text = f"""
Manual Attendance Submission Confirmation

Dear {greeting_name},

{intro_line}

Employee ID: {employee_id}
Submission Timestamp: {formatted_timestamp}

This attendance will be visible on the dashboard, reports, and payroll calculations only after it is approved by {approver}.

"""
            if is_manager:
                text += """
A copy of this notification has been sent to the Admin for approval.

"""
            text += f"""
This timestamp serves as proof of the check-in time.

If this request was not submitted intentionally, please contact your administrator immediately.

Best regards,
{self.company_name} HR Team
"""

            html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Manual Attendance Submitted</title>
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    background-color: #f4f4f4;
                    margin: 0;
                    padding: 20px;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    background-color: #ffffff;
                    border-radius: 10px;
                    overflow: hidden;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                }}
                .header {{
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 30px;
                    text-align: center;
                }}
                .header h1 {{
                    margin: 0;
                    font-size: 24px;
                    font-weight: 600;
                }}
                .content {{
                    padding: 30px;
                }}
                .content h2 {{
                    color: #1e3c72;
                    font-size: 20px;
                    margin-top: 0;
                }}
                .info-box {{
                    background-color: #f8f9fa;
                    border-left: 4px solid #667eea;
                    padding: 20px;
                    margin: 20px 0;
                    border-radius: 5px;
                }}
                .info-box p {{
                    margin: 10px 0;
                    font-size: 16px;
                }}
                .timestamp {{
                    background-color: #e9ecef;
                    padding: 15px;
                    border-radius: 5px;
                    font-family: 'Courier New', monospace;
                    font-size: 18px;
                    font-weight: bold;
                    text-align: center;
                    letter-spacing: 2px;
                    margin: 10px 0;
                    color: #667eea;
                }}
                .important {{
                    background-color: #fff3cd;
                    border-left: 4px solid #ffc107;
                    padding: 15px;
                    margin: 20px 0;
                    border-radius: 5px;
                }}
                .footer {{
                    background-color: #f8f9fa;
                    padding: 20px;
                    text-align: center;
                    color: #6c757d;
                    font-size: 14px;
                    border-top: 1px solid #e9ecef;
                }}
                .footer p {{
                    margin: 5px 0;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>{self.company_name}</h1>
                </div>
                <div class="content">
                    <h2>Manual Attendance Submitted</h2>
                    <p>Dear {greeting_name},</p>
                    <p>{intro_line}</p>
                    
                    <div class="info-box">
                        <p><strong>Employee ID:</strong> {employee_id}</p>
                        <p><strong>Submission Timestamp:</strong></p>
                        <div class="timestamp">{formatted_timestamp}</div>
                    </div>
                    
                    <div class="important">
                        <p><strong>Important:</strong> This attendance will be visible on the dashboard, reports, and payroll calculations only after it is approved by {approver}.</p>
                    </div>
                    
                    <p>This timestamp serves as proof of the check-in time.</p>
"""
            if is_manager:
                html += """
                    <div class="important">
                        <p>A copy of this notification has been sent to the Admin for approval.</p>
                    </div>
"""
            html += f"""
                    <p>If this request was not submitted intentionally, please contact your administrator immediately.</p>
                    
                    <p>Best regards,<br>{self.company_name} HR Team</p>
                </div>
                <div class="footer">
                    <p>&copy; {self.company_name}. All rights reserved.</p>
                    <p>This is an automated email. Please do not reply.</p>
                </div>
            </div>
        </body>
        </html>
        """
            return text, html

        # Employee's own copy - always sent.
        employee_text, employee_html = _build_bodies(
            greeting_name=employee_name,
            intro_line="Your manual attendance request has been submitted successfully."
        )
        result = self.send_email(employee_email, subject, employee_html, employee_text)

        # If a Manager submitted their OWN manual attendance, the Admin also
        # gets a distinct, properly-addressed copy (not a broken find/replace
        # of the employee's already-interpolated email) so they can review
        # and approve/reject it - Managers' manual attendance may only be
        # finalized by the Admin.
        if is_manager and result.get('success'):
            from models import Admin
            admin = Admin.query.first()
            if admin and admin.email:
                admin_subject = f"Manager Manual Attendance - {employee_name} ({employee_id})"
                admin_text, admin_html = _build_bodies(
                    greeting_name="Administrator",
                    intro_line=f"Manager {employee_name} ({employee_id}) has submitted a manual attendance request."
                )
                self.send_email(admin.email, admin_subject, admin_html, admin_text)
        
        return result
    
    def test_email_connection(self):
        """Test email connection"""
        if not self.smtp_username or not self.smtp_password:
            return {'success': False, 'message': 'SMTP credentials not configured'}
        
        try:
            if self.use_tls:
                server = smtplib.SMTP(self.smtp_server, self.smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            
            server.login(self.smtp_username, self.smtp_password)
            server.quit()
            
            return {'success': True, 'message': 'Email connection successful'}
        
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    def send_logout_approval_employee_notification(self, to_email, employee_name, date):
        """Send email to employee when logout approval request is created"""
        subject = "Action Required: Logout Approval Pending"
        
        # Plain-text version
        text_body = f"""
Action Required: Logout Approval Pending

Hello {employee_name},

Our system detected that you have an IN record for {date}, but no OUT
record was recorded.

Your automatic logout request has been sent to your Manager for approval.

Your attendance has NOT been automatically logged out yet.

Please contact your Manager regarding this pending logout approval.

Once your Manager approves the request, your OUT time will be recorded
as 23:59 and your attendance will be finalized.

Regards,
AI Attendance System
"""
        
        # HTML version
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Attendance Logout Pending</title>
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    background-color: #f4f4f4;
                    margin: 0;
                    padding: 20px;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    background-color: #ffffff;
                    border-radius: 10px;
                    overflow: hidden;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                }}
                .header {{
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 30px;
                    text-align: center;
                }}
                .header h1 {{
                    margin: 0;
                    font-size: 24px;
                    font-weight: 600;
                }}
                .content {{
                    padding: 30px;
                }}
                .content h2 {{
                    color: #1e3c72;
                    font-size: 20px;
                    margin-top: 0;
                }}
                .warning-box {{
                    background-color: #fff3cd;
                    border-left: 4px solid #ffc107;
                    padding: 20px;
                    margin: 20px 0;
                    border-radius: 5px;
                }}
                .footer {{
                    background-color: #f8f9fa;
                    padding: 20px;
                    text-align: center;
                    color: #6c757d;
                    font-size: 14px;
                    border-top: 1px solid #e9ecef;
                }}
                .footer p {{
                    margin: 5px 0;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>{self.company_name}</h1>
                </div>
                <div class="content">
                    <h2>Attendance Logout Pending - Manager Approval Required</h2>
                    <p>Dear {employee_name},</p>
                    <p>Your logout for today ({date}) has not been recorded.</p>
                    
                    <div class="warning-box">
                        <p><strong>Important:</strong></p>
                        <p>An Auto Logout request has been sent to your Manager for approval.</p>
                        <p>Until your Manager approves the request, your attendance will remain pending.</p>
                        <p>Please contact your Manager if required.</p>
                    </div>
                    
                    <p>Regards,<br>{self.company_name} HR Team</p>
                </div>
                <div class="footer">
                    <p>&copy; {self.company_name}. All rights reserved.</p>
                    <p>This is an automated email. Please do not reply.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return self.send_email(to_email, subject, html_body, text_body)
    
    def send_logout_approval_manager_notification(self, to_email, manager_name, employee_name, employee_id, department, date):
        """Send email to manager when logout approval request is created"""
        subject = f"Employee Logout Approval Required - {employee_name}"
        
        # Plain-text version
        text_body = f"""
Employee Logout Approval Required

Dear {manager_name},

Employee Name: {employee_name}
Employee ID: {employee_id}
Department: {department}
Date: {date}

The employee has forgotten to logout.

Please approve or reject the Auto Logout request from Manager Dashboard.

Regards,
{self.company_name} HR Team
"""
        
        # HTML version
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Employee Logout Approval Required</title>
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    background-color: #f4f4f4;
                    margin: 0;
                    padding: 20px;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    background-color: #ffffff;
                    border-radius: 10px;
                    overflow: hidden;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                }}
                .header {{
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 30px;
                    text-align: center;
                }}
                .header h1 {{
                    margin: 0;
                    font-size: 24px;
                    font-weight: 600;
                }}
                .content {{
                    padding: 30px;
                }}
                .content h2 {{
                    color: #1e3c72;
                    font-size: 20px;
                    margin-top: 0;
                }}
                .info-box {{
                    background-color: #f8f9fa;
                    border-left: 4px solid #667eea;
                    padding: 20px;
                    margin: 20px 0;
                    border-radius: 5px;
                }}
                .info-box p {{
                    margin: 10px 0;
                    font-size: 16px;
                }}
                .info-box strong {{
                    color: #1e3c72;
                }}
                .action-box {{
                    background-color: #d1ecf1;
                    border-left: 4px solid #17a2b8;
                    padding: 20px;
                    margin: 20px 0;
                    border-radius: 5px;
                }}
                .footer {{
                    background-color: #f8f9fa;
                    padding: 20px;
                    text-align: center;
                    color: #6c757d;
                    font-size: 14px;
                    border-top: 1px solid #e9ecef;
                }}
                .footer p {{
                    margin: 5px 0;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>{self.company_name}</h1>
                </div>
                <div class="content">
                    <h2>Employee Logout Approval Required</h2>
                    <p>Dear {manager_name},</p>
                    
                    <div class="info-box">
                        <p><strong>Employee Name:</strong> {employee_name}</p>
                        <p><strong>Employee ID:</strong> {employee_id}</p>
                        <p><strong>Department:</strong> {department}</p>
                        <p><strong>Date:</strong> {date}</p>
                    </div>
                    
                    <div class="action-box">
                        <p><strong>Action Required:</strong></p>
                        <p>The employee has forgotten to logout.</p>
                        <p>Please approve or reject the Auto Logout request from Manager Dashboard.</p>
                    </div>
                    
                    <p>Regards,<br>{self.company_name} HR Team</p>
                </div>
                <div class="footer">
                    <p>&copy; {self.company_name}. All rights reserved.</p>
                    <p>This is an automated email. Please do not reply.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return self.send_email(to_email, subject, html_body, text_body)
