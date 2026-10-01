"""
Customer Management Service

This module provides CRUD operations for customer records and integrates
with the license generation system. This is used by the VENDOR to manage
customers, payments, and license key generation.

Key Features:
- Add/Update customers
- Automatic license key generation when payment_yn = 'Y'
- Installation tracking (software_installed_yn)
- Integration with email dispatch for license key delivery
"""

import logging
from datetime import datetime
from typing import Optional, List, Dict
from sqlalchemy.orm import Session

from .customer_models import Customer, get_customer_session
from .license_manager import LicenseManager
from .keygen import issue_license_token
from .license_email_service import get_license_email_service

logger = logging.getLogger(__name__)


class CustomerService:
    """Service for managing customer records and license generation."""
    
    def __init__(self):
        self.license_manager = LicenseManager()
        self.email_service = get_license_email_service()
    
    def add_customer(self, name: str, email: str, address: str = None, 
                   phone: str = None, machine_fingerprint: str = None,
                   payment_yn: str = 'N') -> Customer:
        """
        Add a new customer record.
        
        Args:
            name: Customer name
            email: Customer email address
            address: Customer address (optional)
            phone: Customer phone number (optional)
            machine_fingerprint: Customer's machine fingerprint (optional, can be added later)
            payment_yn: Payment status ('Y' or 'N', default 'N')
        
        Returns:
            Customer: The created customer record
        
        Raises:
            ValueError: If required fields are missing or invalid
        """
        session = get_customer_session()
        
        try:
            # Validate inputs
            if not name or not name.strip():
                raise ValueError("Customer name is required")
            
            if not email or not email.strip():
                raise ValueError("Customer email is required")
            
            if payment_yn not in ('Y', 'N'):
                raise ValueError("payment_yn must be 'Y' or 'N'")
            
            # Check if email already exists
            existing = session.query(Customer).filter_by(email=email.strip()).first()
            if existing:
                raise ValueError(f"Customer with email '{email}' already exists")
            
            # Create customer record
            customer = Customer(
                name=name.strip(),
                email=email.strip(),
                address=address.strip() if address else None,
                phone=phone.strip() if phone else None,
                machine_fingerprint=machine_fingerprint.strip() if machine_fingerprint else None,
                payment_yn=payment_yn,
                software_installed_yn='N'
            )
            
            # Generate license key if payment is confirmed
            if payment_yn == 'Y' and machine_fingerprint:
                self._generate_and_assign_license_key(customer, session)
            
            session.add(customer)
            session.commit()
            
            logger.info(f"Customer added: {customer.id} - {customer.name} ({customer.email})")
            
            return customer
            
        except Exception as e:
            session.rollback()
            logger.error(f"Error adding customer: {e}")
            raise
    
    def update_customer(self, customer_id: int, **kwargs) -> Customer:
        """
        Update an existing customer record.
        
        Args:
            customer_id: Customer ID to update
            **kwargs: Fields to update (name, email, address, phone, 
                      machine_fingerprint, payment_yn, software_installed_yn)
        
        Returns:
            Customer: The updated customer record
        
        Raises:
            ValueError: If customer not found or invalid data
        """
        session = get_customer_session()
        
        try:
            customer = session.query(Customer).filter_by(id=customer_id).first()
            
            if not customer:
                raise ValueError(f"Customer with ID {customer_id} not found")
            
            # Track if payment status changes from N to Y
            payment_changed = False
            old_payment_status = customer.payment_yn
            
            # Update fields
            for key, value in kwargs.items():
                if hasattr(customer, key):
                    if key == 'payment_yn' and value != old_payment_status:
                        payment_changed = True
                    
                    setattr(customer, key, value)
            
            # Validate payment_yn if changed
            if 'payment_yn' in kwargs and kwargs['payment_yn'] not in ('Y', 'N'):
                raise ValueError("payment_yn must be 'Y' or 'N'")
            
            # Validate software_installed_yn if changed
            if 'software_installed_yn' in kwargs and kwargs['software_installed_yn'] not in ('Y', 'N'):
                raise ValueError("software_installed_yn must be 'Y' or 'N'")
            
            # Generate license key if payment changed to Y and fingerprint exists
            if payment_changed and customer.payment_yn == 'Y':
                if customer.machine_fingerprint:
                    self._generate_and_assign_license_key(customer, session)
                else:
                    logger.warning(f"Payment confirmed for customer {customer_id} but no machine fingerprint - cannot generate license key")
            
            customer.updated_at = datetime.utcnow()
            session.commit()
            
            logger.info(f"Customer updated: {customer.id} - {customer.name}")
            
            return customer
            
        except Exception as e:
            session.rollback()
            logger.error(f"Error updating customer {customer_id}: {e}")
            raise
    
    def get_customer(self, customer_id: int) -> Optional[Customer]:
        """
        Get a customer by ID.
        
        Args:
            customer_id: Customer ID
        
        Returns:
            Customer or None if not found
        """
        session = get_customer_session()
        
        try:
            customer = session.query(Customer).filter_by(id=customer_id).first()
            return customer
        except Exception as e:
            logger.error(f"Error getting customer {customer_id}: {e}")
            return None
    
    def get_customer_by_email(self, email: str) -> Optional[Customer]:
        """
        Get a customer by email address.
        
        Args:
            email: Customer email address
        
        Returns:
            Customer or None if not found
        """
        session = get_customer_session()
        
        try:
            customer = session.query(Customer).filter_by(email=email.strip()).first()
            return customer
        except Exception as e:
            logger.error(f"Error getting customer by email {email}: {e}")
            return None
    
    def get_all_customers(self, payment_status: str = None, 
                         installed_status: str = None) -> List[Customer]:
        """
        Get all customers, optionally filtered by payment or installation status.
        
        Args:
            payment_status: Filter by payment_yn ('Y' or 'N')
            installed_status: Filter by software_installed_yn ('Y' or 'N')
        
        Returns:
            List of Customer records
        """
        session = get_customer_session()
        
        try:
            query = session.query(Customer)
            
            if payment_status in ('Y', 'N'):
                query = query.filter_by(payment_yn=payment_status)
            
            if installed_status in ('Y', 'N'):
                query = query.filter_by(software_installed_yn=installed_status)
            
            customers = query.order_by(Customer.created_at.desc()).all()
            return customers
        except Exception as e:
            logger.error(f"Error getting customers: {e}")
            return []
    
    def delete_customer(self, customer_id: int) -> bool:
        """
        Delete a customer record (soft delete recommended).
        
        Args:
            customer_id: Customer ID to delete
        
        Returns:
            bool: True if deleted successfully
        """
        session = get_customer_session()
        
        try:
            customer = session.query(Customer).filter_by(id=customer_id).first()
            
            if not customer:
                logger.warning(f"Customer {customer_id} not found for deletion")
                return False
            
            session.delete(customer)
            session.commit()
            
            logger.info(f"Customer deleted: {customer_id} - {customer.name}")
            return True
            
        except Exception as e:
            session.rollback()
            logger.error(f"Error deleting customer {customer_id}: {e}")
            return False
    
    def _generate_and_assign_license_key(self, customer: Customer, session: Session):
        """
        Issue and assign a signed Ed25519 license token to a customer.

        This is called internally when payment_yn = 'Y' and machine_fingerprint is available.
        Automatically sends the license token via email upon successful generation.

        NOTE: this calls licensing.keygen.issue_license_token(), which requires the
        vendor's Ed25519 PRIVATE key (licensing/vendor_private_key.pem, generated once
        via `python keygen.py init-keys`) to be present on THIS machine - i.e. wherever
        the vendor portal itself runs, never on a customer's installation.

        Args:
            customer: Customer record
            session: Database session
        """
        try:
            if not customer.machine_fingerprint:
                logger.warning(f"Cannot generate license key for customer {customer.id} - no machine fingerprint")
                return

            # Issue a signed license token bound to this customer's machine.
            # Perpetual by default (expires_at=None) to match the previous
            # scheme's semantics; pass expires_at=... here if you want to
            # start issuing time-limited licenses.
            license_key = issue_license_token(
                customer.machine_fingerprint,
                customer.name,
                expires_at=None,
            )

            # Assign to customer
            customer.soft_key = license_key
            customer.updated_at = datetime.utcnow()
            
            logger.info(f"License key generated for customer {customer.id}: {license_key}")
            
            # Automatically send email with license key
            email_sent = self.email_service.send_license_key_email(
                customer_name=customer.name,
                customer_email=customer.email,
                license_key=license_key,
                machine_fingerprint=customer.machine_fingerprint
            )
            
            if email_sent:
                logger.info(f"License key email sent to {customer.email}")
            else:
                logger.warning(f"Failed to send license key email to {customer.email}")
            
        except Exception as e:
            logger.error(f"Error generating license key for customer {customer.id}: {e}")
            raise
    
    def mark_as_installed(self, customer_id: int) -> bool:
        """
        Mark a customer's software as installed.
        
        This is called when the customer successfully activates the software.
        
        Args:
            customer_id: Customer ID
        
        Returns:
            bool: True if updated successfully
        """
        try:
            self.update_customer(customer_id, software_installed_yn='Y')
            logger.info(f"Customer {customer_id} marked as installed")
            return True
        except Exception as e:
            logger.error(f"Error marking customer {customer_id} as installed: {e}")
            return False
    
    def get_customer_stats(self) -> Dict:
        """
        Get statistics about customers.
        
        Returns:
            Dict with customer statistics
        """
        session = get_customer_session()
        
        try:
            total_customers = session.query(Customer).count()
            paid_customers = session.query(Customer).filter_by(payment_yn='Y').count()
            unpaid_customers = session.query(Customer).filter_by(payment_yn='N').count()
            installed_customers = session.query(Customer).filter_by(software_installed_yn='Y').count()
            pending_installation = session.query(Customer).filter_by(
                payment_yn='Y', software_installed_yn='N'
            ).count()
            
            return {
                'total_customers': total_customers,
                'paid_customers': paid_customers,
                'unpaid_customers': unpaid_customers,
                'installed_customers': installed_customers,
                'pending_installation': pending_installation
            }
        except Exception as e:
            logger.error(f"Error getting customer stats: {e}")
            return {}


# Singleton instance
_customer_service = None


def get_customer_service() -> CustomerService:
    """Get the singleton CustomerService instance."""
    global _customer_service
    if _customer_service is None:
        _customer_service = CustomerService()
    return _customer_service