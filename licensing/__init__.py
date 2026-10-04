"""
Licensing Package

Two halves live here, and they run in DIFFERENT places:

CUSTOMER side (ships inside the packaged attendance application)
    license_manager   machine fingerprint, Ed25519 token verification,
                      tamper-resistant 30-day trial, clock-rollback detection
    client_security   the startup gate + "License Activation" lock screen
                      that blocks the whole app once the trial has ended

VENDOR side (runs on YOUR server only - exclude from customer builds)
    keygen                 Ed25519 private-key signing tool
    plans                  Monthly / Yearly / Lifetime prices and expiry rules
    payment_gateway        Razorpay adapter (orders + signature checks)
    customer_models        customers.db (customers + orders)
    customer_service       purchase -> webhook -> licence -> email logic
    license_email_service  licence delivery by email
    customer_routes        password-protected admin API
    vendor_app             the public purchase portal + payment webhook

Importing this package deliberately imports NOTHING heavy. In particular the
customer application must never import (or even contain) the vendor-side
modules, and the vendor server must not drag in the attendance app's config.
The names below are loaded lazily on first access.
"""

_LAZY = {
    # customer side
    'LicenseManager': ('.license_manager', 'LicenseManager'),
    'get_license_manager': ('.license_manager', 'get_license_manager'),
    'check_license_on_startup': ('.license_manager', 'check_license_on_startup'),
    'init_license_gate': ('.client_security', 'init_license_gate'),
    # vendor side
    'Customer': ('.customer_models', 'Customer'),
    'Order': ('.customer_models', 'Order'),
    'init_customer_db': ('.customer_models', 'init_customer_db'),
    'get_customer_session': ('.customer_models', 'get_customer_session'),
    'CustomerService': ('.customer_service', 'CustomerService'),
    'get_customer_service': ('.customer_service', 'get_customer_service'),
    'LicenseEmailService': ('.license_email_service', 'LicenseEmailService'),
    'get_license_email_service': ('.license_email_service', 'get_license_email_service'),
    'vendor_bp': ('.customer_routes', 'vendor_bp'),
}

__all__ = sorted(_LAZY)


def __getattr__(name):
    try:
        module_name, attr = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    import importlib
    value = getattr(importlib.import_module(module_name, __name__), attr)
    globals()[name] = value
    return value
