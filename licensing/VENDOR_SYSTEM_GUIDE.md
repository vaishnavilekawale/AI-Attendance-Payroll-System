# Vendor Licensing System - Implementation Guide

## Overview

The licensing system has been updated to match the reference notes with a complete customer management database, automated license key generation, email dispatch, and installation tracking.

## New Files Created

### 1. customer_models.py
- **Purpose**: Database schema for customer records
- **Database**: `customers.db` (separate from main attendance database)
- **Model Fields**:
  - `id`: Auto-incrementing primary key
  - `name`: Customer name
  - `address`: Customer address
  - `phone`: Customer phone number
  - `email`: Customer email (for license key delivery)
  - `machine_fingerprint`: 64-character hex string from customer's machine
  - `soft_key`: 16-character auto-generated license key
  - `payment_yn`: Payment status ('Y' or 'N')
  - `software_installed_yn`: Installation status ('Y' or 'N')
  - `created_at`: Record creation timestamp
  - `updated_at`: Record last update timestamp

### 2. customer_service.py
- **Purpose**: CRUD operations for customer management
- **Key Features**:
  - Add/Update customers
  - Automatic license key generation when `payment_yn = 'Y'`
  - Automatic email dispatch when license key is generated
  - Installation tracking (`software_installed_yn`)
  - Customer statistics

### 3. license_email_service.py
- **Purpose**: Automated email dispatch for license keys
- **Features**:
  - SMTP-based email sending
  - Professional HTML email template
  - Automatic dispatch when license key is generated
  - Configurable via environment variables

### 4. customer_routes.py
- **Purpose**: Flask API endpoints for vendor portal
- **Endpoints**:
  - `GET /vendor/customers` - List all customers
  - `POST /vendor/customers` - Add new customer
  - `PUT /vendor/customers/<id>` - Update customer
  - `DELETE /vendor/customers/<id>` - Delete customer
  - `GET /vendor/customers/<id>` - Get specific customer
  - `GET /vendor/stats` - Get customer statistics
  - `POST /vendor/activate` - Installation activation endpoint

### 5. Updated license_manager.py
- **New Features**:
  - Online validation against vendor API (optional)
  - Activation reporting to vendor database
  - Fallback to offline validation if API unavailable
  - Configurable via environment variables

## Environment Variables

Add these to your `.env` file for the vendor system:

```bash
# SMTP Configuration for License Key Emails
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_USE_TLS=true
SENDER_EMAIL=your-email@gmail.com
SENDER_NAME=AI Attendance & Payroll System
SUPPORT_EMAIL=support@yourcompany.com
COMPANY_NAME=Your Company Name
COMPANY_WEBSITE=https://yourcompany.com

# Vendor API Configuration (Optional - for online validation)
VENDOR_API_URL=http://localhost:5000
VENDOR_API_TIMEOUT=10
ENABLE_ONLINE_VALIDATION=false
```

## Workflow

### 1. Customer Registration (Vendor Side)

```python
from customer_service import get_customer_service

service = get_customer_service()

# Add customer without payment (payment_yn = 'N')
customer = service.add_customer(
    name="ABC Corporation",
    email="contact@abccorp.com",
    phone="+91 9876543210",
    address="123 Business Park, Mumbai",
    machine_fingerprint=None,  # Will be provided later
    payment_yn='N'
)
```

### 2. Payment Confirmation & Machine Fingerprint

When customer provides payment and machine fingerprint:

```python
# Update customer with payment and machine fingerprint
service.update_customer(
    customer.id,
    payment_yn='Y',
    machine_fingerprint='ceca45fe425940ffc98248e22c2bdeaad409cf7025e982ccbf2d81ae3ec5b6a9'
)
```

**What happens automatically:**
1. License key is generated (16 characters)
2. License key is assigned to customer record
3. Email is sent to customer with license key
4. Customer record is updated with timestamp

### 3. Customer Receives License Key

Customer receives professional HTML email with:
- License key prominently displayed
- Installation instructions
- Machine fingerprint reference
- Support contact information

### 4. Customer Installation & Activation

Customer installs software and enters license key. The software:

1. Validates license key offline (using `license_manager.py`)
2. If online validation is enabled, validates against vendor API
3. Reports activation to vendor API (updates `software_installed_yn = 'Y'`)

```python
from license_manager import get_license_manager

lm = get_license_manager()

# Validate license key
is_valid, message = lm.validate_license_key('Y2NKZTVMZJI0MZAY')

# Report activation (if online validation enabled)
if is_valid:
    lm.report_activation('Y2NKZTVMZJI0MZAY')
```

### 5. Installation Tracking (Vendor Side)

The `/vendor/activate` endpoint automatically updates the customer record:

```bash
curl -X POST http://localhost:5000/vendor/activate \
  -H "Content-Type: application/json" \
  -d '{
    "license_key": "Y2NKZTVMZJI0MZAY",
    "machine_fingerprint": "ceca45fe425940ffc98248e22c2bdeaad409cf7025e982ccbf2d81ae3ec5b6a9"
  }'
```

Response:
```json
{
  "success": true,
  "message": "Installation activated successfully",
  "customer_name": "ABC Corporation"
}
```

## API Usage Examples

### Add Customer (Unpaid)

```bash
curl -X POST http://localhost:5000/vendor/customers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ABC Corporation",
    "email": "contact@abccorp.com",
    "phone": "+91 9876543210",
    "address": "123 Business Park, Mumbai",
    "payment_yn": "N"
  }'
```

### Update Customer (Payment + Fingerprint)

```bash
curl -X PUT http://localhost:5000/vendor/customers/1 \
  -H "Content-Type: application/json" \
  -d '{
    "payment_yn": "Y",
    "machine_fingerprint": "ceca45fe425940ffc98248e22c2bdeaad409cf7025e982ccbf2d81ae3ec5b6a9"
  }'
```

**Response includes generated license key:**
```json
{
  "success": true,
  "customer": {
    "id": 1,
    "name": "ABC Corporation",
    "email": "contact@abccorp.com",
    "soft_key": "Y2NKZTVMZJI0MZAY",
    "payment_yn": "Y",
    "software_installed_yn": "N"
  },
  "message": "Customer updated successfully"
}
```

### List All Customers

```bash
curl http://localhost:5000/vendor/customers
```

### Filter by Payment Status

```bash
curl http://localhost:5000/vendor/customers?payment_status=Y
```

### Filter by Installation Status

```bash
curl http://localhost:5000/vendor/customers?installed_status=N
```

### Get Customer Statistics

```bash
curl http://localhost:5000/vendor/stats
```

**Response:**
```json
{
  "success": true,
  "stats": {
    "total_customers": 10,
    "paid_customers": 7,
    "unpaid_customers": 3,
    "installed_customers": 5,
    "pending_installation": 2
  }
}
```

## Integration with Main Application

### Option 1: Separate Vendor Portal (Recommended)

Create a separate Flask application for the vendor portal:

```python
# vendor_app.py
from flask import Flask
from customer_routes import vendor_bp

app = Flask(__name__)
app.register_blueprint(vendor_bp)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)
```

### Option 2: Integrate into Main Application

Add the vendor blueprint to the main `app.py`:

```python
from customer_routes import vendor_bp
app.register_blueprint(vendor_bp)
```

**Note**: This is NOT recommended for production as it exposes vendor management to the same application as the customer-facing system.

## Security Considerations

1. **Vendor Portal Security**:
   - Implement authentication for vendor portal
   - Use API keys for programmatic access
   - Rate limiting on API endpoints
   - HTTPS for production

2. **License Key Security**:
   - Keys are encrypted in storage
   - Machine fingerprint binding prevents key sharing
   - Online validation provides additional security

3. **Email Security**:
   - Use app-specific passwords for Gmail
   - Consider using transactional email services (SendGrid, Mailgun)
   - Never log full license keys in plain text

## Testing

### Test Customer Database Initialization

```bash
python customer_models.py
```

### Test Customer Service

```python
from customer_service import get_customer_service
from customer_models import init_customer_db

# Initialize database
init_customer_db()

# Add customer
service = get_customer_service()
customer = service.add_customer(
    name="Test Customer",
    email="test@example.com",
    payment_yn='N'
)
print(f"Customer added: {customer.id}")
```

### Test License Key Generation

```python
# Update with payment and fingerprint
service.update_customer(
    customer.id,
    payment_yn='Y',
    machine_fingerprint='a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2'
)
print(f"License key: {customer.soft_key}")
```

### Test Email Dispatch

Ensure SMTP credentials are configured in `.env` and test:

```python
from license_email_service import get_license_email_service

email_service = get_license_email_service()
email_service.send_license_key_email(
    customer_name="Test Customer",
    customer_email="test@example.com",
    license_key="TESTKEY1234567890",
    machine_fingerprint="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
)
```

## Troubleshooting

### Issue: License key not generated
- **Cause**: `payment_yn` is not 'Y' or `machine_fingerprint` is missing
- **Solution**: Ensure both conditions are met before updating customer

### Issue: Email not sent
- **Cause**: SMTP credentials not configured or incorrect
- **Solution**: Check `.env` file for SMTP configuration
- **Note**: System logs warning and continues if email fails

### Issue: Installation not tracked
- **Cause**: `ENABLE_ONLINE_VALIDATION` is disabled or API unreachable
- **Solution**: Enable online validation or check vendor API URL

### Issue: Customer database not found
- **Cause**: Database not initialized
- **Solution**: Run `python customer_models.py` to initialize

## Summary

The licensing system now provides:
- ✅ Customer database with exact schema from reference notes
- ✅ Automatic license key generation on payment confirmation
- ✅ Automated email dispatch with professional HTML template
- ✅ Installation tracking via API endpoint
- ✅ Online validation with offline fallback
- ✅ Complete CRUD operations for customer management
- ✅ Statistics and reporting capabilities

All modules include proper error handling, logging, and are production-ready.
