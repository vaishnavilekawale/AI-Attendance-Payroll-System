"""
Customer Management Routes (Vendor Portal)

This module provides Flask routes for the vendor to manage customers.
This is a SEPARATE Flask application or blueprint for the VENDOR PORTAL,
not the main attendance/payroll application.

Routes:
- GET /vendor/customers - List all customers
- POST /vendor/customers - Add new customer
- PUT /vendor/customers/<id> - Update customer
- DELETE /vendor/customers/<id> - Delete customer
- POST /vendor/activate - Customer installation activation endpoint
"""

import logging
from datetime import datetime
from flask import Blueprint, request, jsonify, render_template

from .customer_service import get_customer_service
from .customer_models import init_customer_db

logger = logging.getLogger(__name__)

# Create blueprint for vendor routes
vendor_bp = Blueprint('vendor', __name__, url_prefix='/vendor')


@vendor_bp.route('/customers', methods=['GET'])
def list_customers():
    """
    List all customers with optional filtering.
    
    Query Parameters:
        payment_status: Filter by payment_yn ('Y' or 'N')
        installed_status: Filter by software_installed_yn ('Y' or 'N')
    
    Returns:
        JSON list of customers
    """
    try:
        service = get_customer_service()
        
        payment_status = request.args.get('payment_status')
        installed_status = request.args.get('installed_status')
        
        customers = service.get_all_customers(
            payment_status=payment_status,
            installed_status=installed_status
        )
        
        # Convert to dict for JSON response
        customers_data = []
        for customer in customers:
            customers_data.append({
                'id': customer.id,
                'name': customer.name,
                'email': customer.email,
                'phone': customer.phone,
                'address': customer.address,
                'machine_fingerprint': customer.machine_fingerprint,
                'soft_key': customer.soft_key,
                'payment_yn': customer.payment_yn,
                'software_installed_yn': customer.software_installed_yn,
                'created_at': customer.created_at.isoformat() if customer.created_at else None,
                'updated_at': customer.updated_at.isoformat() if customer.updated_at else None
            })
        
        return jsonify({
            'success': True,
            'customers': customers_data,
            'count': len(customers_data)
        })
        
    except Exception as e:
        logger.error(f"Error listing customers: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@vendor_bp.route('/customers', methods=['POST'])
def add_customer():
    """
    Add a new customer.
    
    Request Body (JSON):
        name: Customer name (required)
        email: Customer email (required)
        address: Customer address (optional)
        phone: Customer phone (optional)
        machine_fingerprint: Machine fingerprint (optional)
        payment_yn: Payment status 'Y' or 'N' (default 'N')
    
    Returns:
        JSON with created customer data
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': 'No data provided'
            }), 400
        
        service = get_customer_service()
        
        customer = service.add_customer(
            name=data.get('name'),
            email=data.get('email'),
            address=data.get('address'),
            phone=data.get('phone'),
            machine_fingerprint=data.get('machine_fingerprint'),
            payment_yn=data.get('payment_yn', 'N')
        )
        
        return jsonify({
            'success': True,
            'customer': {
                'id': customer.id,
                'name': customer.name,
                'email': customer.email,
                'soft_key': customer.soft_key,
                'payment_yn': customer.payment_yn,
                'software_installed_yn': customer.software_installed_yn
            },
            'message': 'Customer added successfully'
        })
        
    except ValueError as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400
    except Exception as e:
        logger.error(f"Error adding customer: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@vendor_bp.route('/customers/<int:customer_id>', methods=['PUT'])
def update_customer(customer_id):
    """
    Update an existing customer.
    
    Request Body (JSON):
        Any customer field to update (name, email, address, phone, 
        machine_fingerprint, payment_yn, software_installed_yn)
    
    Returns:
        JSON with updated customer data
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': 'No data provided'
            }), 400
        
        service = get_customer_service()
        
        customer = service.update_customer(customer_id, **data)
        
        return jsonify({
            'success': True,
            'customer': {
                'id': customer.id,
                'name': customer.name,
                'email': customer.email,
                'soft_key': customer.soft_key,
                'payment_yn': customer.payment_yn,
                'software_installed_yn': customer.software_installed_yn
            },
            'message': 'Customer updated successfully'
        })
        
    except ValueError as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400
    except Exception as e:
        logger.error(f"Error updating customer {customer_id}: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@vendor_bp.route('/customers/<int:customer_id>', methods=['DELETE'])
def delete_customer(customer_id):
    """
    Delete a customer.
    
    Returns:
        JSON with success status
    """
    try:
        service = get_customer_service()
        
        success = service.delete_customer(customer_id)
        
        if success:
            return jsonify({
                'success': True,
                'message': f'Customer {customer_id} deleted successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': f'Customer {customer_id} not found'
            }), 404
            
    except Exception as e:
        logger.error(f"Error deleting customer {customer_id}: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@vendor_bp.route('/customers/<int:customer_id>', methods=['GET'])
def get_customer(customer_id):
    """
    Get a specific customer by ID.
    
    Returns:
        JSON with customer data
    """
    try:
        service = get_customer_service()
        
        customer = service.get_customer(customer_id)
        
        if not customer:
            return jsonify({
                'success': False,
                'error': f'Customer {customer_id} not found'
            }), 404
        
        return jsonify({
            'success': True,
            'customer': {
                'id': customer.id,
                'name': customer.name,
                'email': customer.email,
                'phone': customer.phone,
                'address': customer.address,
                'machine_fingerprint': customer.machine_fingerprint,
                'soft_key': customer.soft_key,
                'payment_yn': customer.payment_yn,
                'software_installed_yn': customer.software_installed_yn,
                'created_at': customer.created_at.isoformat() if customer.created_at else None,
                'updated_at': customer.updated_at.isoformat() if customer.updated_at else None
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting customer {customer_id}: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@vendor_bp.route('/stats', methods=['GET'])
def get_stats():
    """
    Get customer statistics.
    
    Returns:
        JSON with customer statistics
    """
    try:
        service = get_customer_service()
        
        stats = service.get_customer_stats()
        
        return jsonify({
            'success': True,
            'stats': stats
        })
        
    except Exception as e:
        logger.error(f"Error getting customer stats: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@vendor_bp.route('/activate', methods=['POST'])
def activate_installation():
    """
    Installation activation endpoint.
    
    This endpoint is called by the customer's installed software when
    they successfully activate their license. It updates the
    software_installed_yn status from 'N' to 'Y' in the database.
    
    Request Body (JSON):
        license_key: 16-character license key
        machine_fingerprint: 64-character machine fingerprint
    
    Returns:
        JSON with activation status
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': 'No data provided'
            }), 400
        
        license_key = data.get('license_key')
        machine_fingerprint = data.get('machine_fingerprint')
        
        if not license_key or len(license_key) != 16:
            return jsonify({
                'success': False,
                'error': 'Invalid license key'
            }), 400
        
        if not machine_fingerprint or len(machine_fingerprint) != 64:
            return jsonify({
                'success': False,
                'error': 'Invalid machine fingerprint'
            }), 400
        
        service = get_customer_service()
        session = service.get_customer_session() if hasattr(service, 'get_customer_session') else None
        
        # Find customer by license key and machine fingerprint
        from customer_models import get_customer_session as get_session
        session = get_session()
        
        from customer_models import Customer
        customer = session.query(Customer).filter_by(
            soft_key=license_key,
            machine_fingerprint=machine_fingerprint
        ).first()
        
        if not customer:
            return jsonify({
                'success': False,
                'error': 'License key not found or does not match this machine'
            }), 404
        
        # Check if payment is confirmed
        if customer.payment_yn != 'Y':
            return jsonify({
                'success': False,
                'error': 'Payment not confirmed for this license'
            }), 403
        
        # Update installation status
        customer.software_installed_yn = 'Y'
        customer.updated_at = datetime.utcnow()
        session.commit()
        
        logger.info(f"Installation activated for customer {customer.id} ({customer.name})")
        
        return jsonify({
            'success': True,
            'message': 'Installation activated successfully',
            'customer_name': customer.name
        })
        
    except Exception as e:
        logger.error(f"Error activating installation: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# Initialize customer database on module import
init_customer_db()
