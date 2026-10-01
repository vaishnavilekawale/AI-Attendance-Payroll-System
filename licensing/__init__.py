"""
Licensing Package

This package contains all licensing and customer management modules for the
AI Attendance & Payroll System.

Modules:
- license_manager: Machine fingerprinting and license validation
- keygen: License key generation tool (vendor use)
- customer_models: Customer database schema (vendor-side only)
- customer_service: Customer CRUD operations (vendor-side only)
- license_email_service: License key email dispatch (vendor-side only)
- customer_routes: Vendor API endpoints (vendor-side only)

NOTE: Customer management modules (customer_models, customer_service, 
license_email_service, customer_routes) are for the VENDOR PORTAL only.
They should NOT be imported in the main attendance application.
The customer database is initialized separately in the vendor portal.
"""

from .license_manager import LicenseManager, get_license_manager, check_license_on_startup

# Customer management modules are vendor-side only
# Import them conditionally to avoid database initialization issues in frozen builds
try:
    from .customer_models import Customer, init_customer_db, get_customer_session
    from .customer_service import CustomerService, get_customer_service
    from .license_email_service import LicenseEmailService, get_license_email_service
    from .customer_routes import vendor_bp
    
    _customer_modules_available = True
except Exception as e:
    # Customer modules failed to load (likely database path issue in frozen build)
    # This is expected in the main attendance application
    _customer_modules_available = False
    print(f"[licensing] Customer modules not loaded (expected in main app): {e}")

__all__ = [
    'LicenseManager',
    'get_license_manager',
    'check_license_on_startup',
]

# Only export customer modules if they loaded successfully
if _customer_modules_available:
    __all__.extend([
        'Customer',
        'init_customer_db',
        'get_customer_session',
        'CustomerService',
        'get_customer_service',
        'LicenseEmailService',
        'get_license_email_service',
        'vendor_bp',
    ])
