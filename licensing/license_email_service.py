"""
License Key Email Dispatch Service

This module provides automated email dispatch for sending license keys
to customers upon successful payment and key generation.

Features:
- SMTP-based email sending
- HTML email template with professional formatting
- Automatic dispatch when license key is generated
- Error handling and logging
- Configurable SMTP settings via environment variables
"""

import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class LicenseEmailService:
    """Service for sending license keys via email."""
    
    def __init__(self):
        # SMTP Configuration - Load from environment variables (use MAIL_* to match .env)
        self.smtp_server = os.environ.get('MAIL_SERVER', os.environ.get('SMTP_SERVER', 'smtp.gmail.com'))
        self.smtp_port = int(os.environ.get('MAIL_PORT', os.environ.get('SMTP_PORT', '587')))
        self.smtp_username = os.environ.get('MAIL_USERNAME', os.environ.get('SMTP_USERNAME', ''))
        self.smtp_password = os.environ.get('MAIL_PASSWORD', os.environ.get('SMTP_PASSWORD', ''))
        self.smtp_use_tls = os.environ.get('MAIL_USE_TLS', os.environ.get('SMTP_USE_TLS', 'true')).lower() == 'true'
        
        # Email content
        self.sender_email = os.environ.get('MAIL_DEFAULT_SENDER', os.environ.get('SENDER_EMAIL', self.smtp_username))
        self.sender_name = os.environ.get('SENDER_NAME', 'AI Attendance & Payroll System')
        self.support_email = os.environ.get('SUPPORT_EMAIL', 'support@yourcompany.com')
        self.company_name = os.environ.get('COMPANY_NAME', 'Your Company Name')
        self.company_website = os.environ.get('COMPANY_WEBSITE', 'https://example.com')
    
    def send_license_key_email(self, customer_name: str, customer_email: str, 
                             license_key: str, machine_fingerprint: str) -> bool:
        """
        Send license key email to customer.
        
        Args:
            customer_name: Customer's name
            customer_email: Customer's email address
            license_key: signed Ed25519 license token (see licensing/keygen.py) -
                variable length, not the old fixed 16-character code
            machine_fingerprint: Customer's machine fingerprint (for reference)

        Returns:
            bool: True if email sent successfully
        """
        try:
            # Validate inputs
            if not customer_email or '@' not in customer_email:
                logger.error(f"Invalid email address: {customer_email}")
                return False

            if not license_key or '.' not in license_key:
                # A signed token is always "<payload>.<signature>" - see
                # licensing/license_manager.py. Anything else isn't a real token.
                logger.error("Invalid license token: missing payload/signature separator")
                return False
            
            # Check SMTP configuration
            if not self.smtp_username or not self.smtp_password:
                logger.warning("SMTP credentials not configured - skipping email dispatch")
                logger.info(f"License key for {customer_name} ({customer_email}): {license_key}")
                return False
            
            # Create email message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f'Your License Key - {self.company_name}'
            msg['From'] = f'{self.sender_name} <{self.sender_email}>'
            msg['To'] = f'{customer_name} <{customer_email}>'
            
            # Create HTML email body
            html_body = self._create_email_template(
                customer_name, 
                license_key, 
                machine_fingerprint
            )
            
            # Attach HTML body
            html_part = MIMEText(html_body, 'html')
            msg.attach(html_part)
            
            # Send email
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                if self.smtp_use_tls:
                    server.starttls()
                
                server.login(self.smtp_username, self.smtp_password)
                server.send_message(msg)
            
            logger.info(f"License key email sent to {customer_email}")
            return True
            
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"SMTP authentication failed: {e}")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error sending email: {e}")
            return False
        except Exception as e:
            logger.error(f"Error sending license key email: {e}")
            return False
    
    def _create_email_template(self, customer_name: str, license_key: str, 
                              machine_fingerprint: str) -> str:
        """
        Create HTML email template for license key delivery.
        
        Args:
            customer_name: Customer's name
            license_key: signed Ed25519 license token (see licensing/keygen.py)
            machine_fingerprint: Customer's machine fingerprint
        
        Returns:
            str: HTML email body
        """
        current_date = datetime.now().strftime('%B %d, %Y')
        
        html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Your License Key</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
        }}
        .container {{
            background-color: #f9f9f9;
            border-radius: 10px;
            padding: 30px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        .header {{
            text-align: center;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 2px solid #4f46e5;
        }}
        .header h1 {{
            color: #4f46e5;
            margin: 0;
            font-size: 28px;
        }}
        .content {{
            margin-bottom: 30px;
        }}
        .license-box {{
            background-color: #eef2ff;
            border: 2px solid #4f46e5;
            border-radius: 8px;
            padding: 20px;
            text-align: center;
            margin: 20px 0;
        }}
        .license-key {{
            font-family: 'Courier New', monospace;
            font-size: 13px;
            font-weight: bold;
            color: #4f46e5;
            word-break: break-all;
            margin: 10px 0;
        }}
        .info-box {{
            background-color: #f0f9ff;
            border-left: 4px solid #0284c7;
            padding: 15px;
            margin: 20px 0;
        }}
        .info-box h3 {{
            margin-top: 0;
            color: #0284c7;
        }}
        .steps {{
            background-color: #fff;
            padding: 20px;
            border-radius: 8px;
            margin: 20px 0;
        }}
        .steps ol {{
            padding-left: 20px;
        }}
        .steps li {{
            margin-bottom: 10px;
        }}
        .footer {{
            text-align: center;
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #e2e8f0;
            color: #64748b;
            font-size: 12px;
        }}
        .footer a {{
            color: #4f46e5;
            text-decoration: none;
        }}
        .warning {{
            background-color: #fff1f2;
            border-left: 4px solid #e11d48;
            padding: 15px;
            margin: 20px 0;
            color: #881337;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎉 Your License Key is Ready!</h1>
        </div>
        
        <div class="content">
            <p>Dear <strong>{customer_name}</strong>,</p>
            
            <p>Thank you for your purchase of the <strong>AI Attendance & Payroll System</strong>. 
            Your payment has been successfully processed, and your license key is ready for activation.</p>
            
            <div class="license-box">
                <h3>Your License Key</h3>
                <div class="license-key">{license_key}</div>
                <p style="margin: 0; font-size: 14px; color: #64748b;">Save this exactly as shown as "license.lic" - do not add or remove characters</p>
            </div>
            
            <div class="info-box">
                <h3>📋 Installation Instructions</h3>
                <p>Please follow these steps to activate your software:</p>
                
                <div class="steps">
                    <ol>
                        <li>Download and install the AI Attendance & Payroll System</li>
                        <li>Launch the application on your computer</li>
                        <li>When prompted, enter the license key above</li>
                        <li>The system will automatically validate and activate your license</li>
                        <li>Start using the system with all features unlocked!</li>
                    </ol>
                </div>
            </div>
            
            <div class="warning">
                <strong>⚠️ Important Notes:</strong>
                <ul style="margin: 10px 0 0 20px; padding: 0;">
                    <li>This license key is tied to your specific computer (machine ID)</li>
                    <li>Do not share this license key with anyone</li>
                    <li>Store this email for your records</li>
                    <li>You are allowed up to 2 machine activations per license</li>
                </ul>
            </div>
            
            <p><strong>Machine Fingerprint Reference:</strong> {machine_fingerprint[:32]}...</p>
            
            <p>If you encounter any issues during installation or activation, please don't hesitate to contact our support team.</p>
        </div>
        
        <div class="footer">
            <p><strong>{self.company_name}</strong></p>
            <p>Website: <a href="{self.company_website}">{self.company_website}</a></p>
            <p>Support: <a href="mailto:{self.support_email}">{self.support_email}</a></p>
            <p>Generated on {current_date}</p>
        </div>
    </div>
</body>
</html>
"""
        return html
    
    def send_payment_confirmation_email(self, customer_name: str, customer_email: str) -> bool:
        """
        Send payment confirmation email (before license key generation).
        
        Args:
            customer_name: Customer's name
            customer_email: Customer's email address
        
        Returns:
            bool: True if email sent successfully
        """
        try:
            if not self.smtp_username or not self.smtp_password:
                logger.warning("SMTP credentials not configured - skipping payment confirmation email")
                return False
            
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f'Payment Confirmation - {self.company_name}'
            msg['From'] = f'{self.sender_name} <{self.sender_email}>'
            msg['To'] = f'{customer_name} <{customer_email}>'
            
            html_body = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Payment Confirmation</title>
</head>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="background-color: #f9f9f9; padding: 30px; border-radius: 10px;">
        <h1 style="color: #4f46e5;">Payment Received ✅</h1>
        <p>Dear {customer_name},</p>
        <p>We have received your payment for the AI Attendance & Payroll System.</p>
        <p>Your license key will be sent to this email address shortly once your machine fingerprint is registered.</p>
        <p>If you haven't provided your machine fingerprint yet, please contact support to complete the activation process.</p>
        <p>Thank you for your business!</p>
        <hr style="margin: 20px 0; border: none; border-top: 1px solid #e2e8f0;">
        <p style="font-size: 12px; color: #64748b;">
            <strong>{self.company_name}</strong><br>
            Support: {self.support_email}
        </p>
    </div>
</body>
</html>
"""
            
            html_part = MIMEText(html_body, 'html')
            msg.attach(html_part)
            
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                if self.smtp_use_tls:
                    server.starttls()
                server.login(self.smtp_username, self.smtp_password)
                server.send_message(msg)
            
            logger.info(f"Payment confirmation email sent to {customer_email}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending payment confirmation email: {e}")
            return False


# Singleton instance
_license_email_service = None


def get_license_email_service() -> LicenseEmailService:
    """Get the singleton LicenseEmailService instance."""
    global _license_email_service
    if _license_email_service is None:
        _license_email_service = LicenseEmailService()
    return _license_email_service