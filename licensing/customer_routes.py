"""
Customer Management Routes (VENDOR admin API)

A Flask blueprint, registered by licensing/vendor_app.py, for the vendor to
manage customers. It is NOT part of the customer-facing attendance app.

EVERY route except /vendor/activate requires HTTP Basic authentication with
the credentials set in the vendor server's environment:

    VENDOR_ADMIN_USER=admin
    VENDOR_ADMIN_PASSWORD=<long random password>

If VENDOR_ADMIN_PASSWORD is not set, the admin API is switched OFF (503)
rather than left open - these routes return license tokens and customer
contact details.

Routes:
- GET    /vendor/customers               list (filters: payment_status, installed_status)
- POST   /vendor/customers               add a customer manually
- GET    /vendor/customers/<id>          one customer
- PUT    /vendor/customers/<id>          update (payment_yn: N->Y issues + emails a license)
- DELETE /vendor/customers/<id>          delete
- POST   /vendor/customers/<id>/resend   re-send the license email
- GET    /vendor/stats                   counts
- POST   /vendor/activate                called by an installed app after activation (public)
"""

import hmac
import logging
import os
from functools import wraps

from flask import Blueprint, request, jsonify, Response

from .customer_service import get_customer_service

logger = logging.getLogger(__name__)

vendor_bp = Blueprint('vendor', __name__, url_prefix='/vendor')


def _iso(value):
    return value.isoformat() if value else None


def _customer_dict(customer, include_contact=True):
    data = {
        'id': customer.id,
        'name': customer.name,
        'email': customer.email,
        'soft_key': customer.soft_key,
        'payment_yn': customer.payment_yn,
        'software_installed_yn': customer.software_installed_yn,
        'plan': customer.plan,
        'plan_expires_at': _iso(customer.plan_expires_at),
        'license_email_sent_yn': customer.license_email_sent_yn,
    }
    if include_contact:
        data.update({
            'phone': customer.phone,
            'address': customer.address,
            'machine_fingerprint': customer.machine_fingerprint,
            'last_payment_id': customer.last_payment_id,
            'amount_paid': customer.amount_paid,
            'created_at': _iso(customer.created_at),
            'updated_at': _iso(customer.updated_at),
        })
    return data


def admin_required(view):
    """HTTP Basic auth against VENDOR_ADMIN_USER / VENDOR_ADMIN_PASSWORD."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        expected_user = os.environ.get('VENDOR_ADMIN_USER', 'admin')
        expected_password = os.environ.get('VENDOR_ADMIN_PASSWORD', '')
        if not expected_password:
            return jsonify({'success': False,
                            'error': 'Admin API disabled: set VENDOR_ADMIN_PASSWORD on the server'}), 503

        auth = request.authorization
        ok = bool(auth and auth.username is not None and auth.password is not None
                  and hmac.compare_digest(auth.username.encode(), expected_user.encode())
                  and hmac.compare_digest(auth.password.encode(), expected_password.encode()))
        if not ok:
            return Response('Authentication required', 401,
                            {'WWW-Authenticate': 'Basic realm="Vendor admin"'})
        return view(*args, **kwargs)
    return wrapper


def _error(message, status):
    return jsonify({'success': False, 'error': message}), status


@vendor_bp.route('/customers', methods=['GET'])
@admin_required
def list_customers():
    try:
        customers = get_customer_service().get_all_customers(
            payment_status=request.args.get('payment_status'),
            installed_status=request.args.get('installed_status'))
        data = [_customer_dict(c) for c in customers]
        return jsonify({'success': True, 'customers': data, 'count': len(data)})
    except Exception as e:
        logger.error("Error listing customers: %s", e)
        return _error(str(e), 500)


@vendor_bp.route('/customers', methods=['POST'])
@admin_required
def add_customer():
    data = request.get_json(silent=True)
    if not data:
        return _error('No data provided', 400)
    try:
        customer = get_customer_service().add_customer(
            name=data.get('name'), email=data.get('email'),
            address=data.get('address'), phone=data.get('phone'),
            machine_fingerprint=data.get('machine_fingerprint'),
            payment_yn=data.get('payment_yn', 'N'), plan=data.get('plan'))
        return jsonify({'success': True, 'customer': _customer_dict(customer, include_contact=False),
                        'message': 'Customer added successfully'})
    except ValueError as e:
        return _error(str(e), 400)
    except Exception as e:
        logger.error("Error adding customer: %s", e)
        return _error(str(e), 500)


@vendor_bp.route('/customers/<int:customer_id>', methods=['PUT'])
@admin_required
def update_customer(customer_id):
    data = request.get_json(silent=True)
    if not data:
        return _error('No data provided', 400)
    try:
        customer = get_customer_service().update_customer(customer_id, **data)
        return jsonify({'success': True, 'customer': _customer_dict(customer, include_contact=False),
                        'message': 'Customer updated successfully'})
    except ValueError as e:
        return _error(str(e), 400)
    except Exception as e:
        logger.error("Error updating customer %s: %s", customer_id, e)
        return _error(str(e), 500)


@vendor_bp.route('/customers/<int:customer_id>', methods=['DELETE'])
@admin_required
def delete_customer(customer_id):
    try:
        if get_customer_service().delete_customer(customer_id):
            return jsonify({'success': True, 'message': f'Customer {customer_id} deleted successfully'})
        return _error(f'Customer {customer_id} not found', 404)
    except Exception as e:
        logger.error("Error deleting customer %s: %s", customer_id, e)
        return _error(str(e), 500)


@vendor_bp.route('/customers/<int:customer_id>', methods=['GET'])
@admin_required
def get_customer(customer_id):
    try:
        customer = get_customer_service().get_customer(customer_id)
        if not customer:
            return _error(f'Customer {customer_id} not found', 404)
        return jsonify({'success': True, 'customer': _customer_dict(customer)})
    except Exception as e:
        logger.error("Error getting customer %s: %s", customer_id, e)
        return _error(str(e), 500)


@vendor_bp.route('/customers/<int:customer_id>/resend', methods=['POST'])
@admin_required
def resend_license(customer_id):
    try:
        sent = get_customer_service().resend_license_email(customer_id)
        if sent:
            return jsonify({'success': True, 'message': 'License email sent'})
        return _error('Email could not be sent - check the SMTP settings and server log', 502)
    except ValueError as e:
        return _error(str(e), 404)
    except Exception as e:
        logger.error("Error resending license for %s: %s", customer_id, e)
        return _error(str(e), 500)


@vendor_bp.route('/stats', methods=['GET'])
@admin_required
def get_stats():
    try:
        return jsonify({'success': True, 'stats': get_customer_service().get_customer_stats()})
    except Exception as e:
        logger.error("Error getting customer stats: %s", e)
        return _error(str(e), 500)


@vendor_bp.route('/activate', methods=['POST'])
def activate_installation():
    """
    Called (best effort) by an installed app after a license was activated, so
    the vendor can see which paid customers actually installed. Public, but it
    only flips software_installed_yn and requires the exact token + fingerprint.

    Request body (JSON): {"license_key": "<token>", "machine_fingerprint": "<64 hex>"}
    """
    data = request.get_json(silent=True) or {}
    license_key = (data.get('license_key') or '').strip()
    machine_fingerprint = (data.get('machine_fingerprint') or '').strip()

    if not license_key or '.' not in license_key or len(license_key) > 4000:
        return _error('Invalid license key', 400)
    if len(machine_fingerprint) != 64:
        return _error('Invalid machine fingerprint', 400)

    try:
        name = get_customer_service().mark_installed_by_token(license_key, machine_fingerprint)
        if not name:
            return _error('License key not found or does not match this machine', 404)
        logger.info("Installation activated for %s", name)
        return jsonify({'success': True, 'message': 'Installation activated successfully'})
    except Exception as e:
        logger.error("Error activating installation: %s", e)
        return _error('Server error', 500)
