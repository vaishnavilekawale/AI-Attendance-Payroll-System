"""
Customer Database Models

This module defines the database schema for managing customer records.
This is a SEPARATE database from the main attendance/payroll database.
This database is used by the VENDOR to manage customers, payments, and license keys.

Database: customers.db
Location: BASE_DIR/customers.db
"""

import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from config import BASE_DIR

# Customer database path (now in licensing/ folder)
CUSTOMERS_DB_PATH = os.path.join(BASE_DIR, 'licensing', 'customers.db')
CUSTOMERS_DB_URI = f'sqlite:///{CUSTOMERS_DB_PATH}'

# Create engine and session
customer_engine = create_engine(CUSTOMERS_DB_URI, echo=False)
CustomerSession = sessionmaker(bind=customer_engine)
customer_session = CustomerSession()

Base = declarative_base()


class Customer(Base):
    """
    Customer model for managing customer records.
    
    Fields:
        id: Auto-incrementing primary key
        name: Customer name (company or individual)
        address: Customer address
        phone: Customer phone number
        email: Customer email address (for license key delivery)
        machine_fingerprint: 64-character hex string from customer's machine
        soft_key: signed Ed25519 license token issued for this customer/machine
                  (see licensing/keygen.py) - variable length, so this is a
                  Text column, not a fixed-width code like the old scheme
        payment_yn: Payment status ('Y' for paid, 'N' for unpaid)
        software_installed_yn: Installation status ('Y' for installed, 'N' for not installed)
        created_at: Record creation timestamp
        updated_at: Record last update timestamp
    """
    __tablename__ = 'customers'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    address = Column(String(500))
    phone = Column(String(50))
    email = Column(String(255), nullable=False)
    machine_fingerprint = Column(String(64), nullable=True)  # 64-char hex
    soft_key = Column(Text, nullable=True)  # signed Ed25519 license token (variable length)
    payment_yn = Column(String(1), nullable=False, default='N')  # 'Y' or 'N'
    software_installed_yn = Column(String(1), nullable=False, default='N')  # 'Y' or 'N'
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<Customer(id={self.id}, name='{self.name}', email='{self.email}', payment_yn='{self.payment_yn}')>"


def init_customer_db():
    """Initialize the customer database and create tables."""
    Base.metadata.create_all(customer_engine)
    print(f"✓ Customer database initialized at: {CUSTOMERS_DB_PATH}")


def get_customer_session():
    """Get a new customer database session."""
    return CustomerSession()


if __name__ == '__main__':
    # Initialize database for testing
    init_customer_db()
    print("Customer database schema created successfully.")