"""
Customer Database Models (VENDOR side)

This is a SEPARATE database from the main attendance/payroll database. It is
used by the VENDOR (you) to track customers, payments and issued licenses,
and it lives on the vendor's own server - never inside a customer install.

Database file: customers.db
Location:      VENDOR_DATA_DIR (env var) or, by default, the licensing/ folder.

Tables
------
customers  one row per (email, machine fingerprint) - the license holder.
           payment_yn is set to 'Y' by the payment webhook once money has
           actually been received.
orders     one row per checkout attempt. Created BEFORE the customer pays
           (so the webhook can verify the amount and look up the plan), and
           flipped to 'paid' exactly once - which is what makes the webhook
           safe to receive twice (payment gateways retry deliveries).

Older customers.db files (created before plan/expiry tracking existed) are
upgraded in place by init_customer_db(): the missing columns are added,
no data is lost.
"""
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Text, text, inspect,
)
from sqlalchemy.orm import declarative_base, sessionmaker

from .vendor_paths import get_vendor_data_dir

logger = logging.getLogger(__name__)

Base = declarative_base()


def _utcnow() -> datetime:
    """Naive UTC 'now' (SQLite stores naive datetimes; everything here is UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_customers_db_path() -> str:
    return os.path.join(get_vendor_data_dir(), "customers.db")


# Kept for backwards compatibility with code that imported the constants.
CUSTOMERS_DB_PATH = get_customers_db_path()
CUSTOMERS_DB_URI = f"sqlite:///{CUSTOMERS_DB_PATH}"


class Customer(Base):
    """
    License holder.

    Fields:
        id: Auto-incrementing primary key
        name: Customer name (company or individual)
        address: Customer address
        phone: Customer phone number
        email: Customer email address (for license delivery)
        machine_fingerprint: 64-character hex string from the customer's machine
        soft_key: signed Ed25519 license token issued for this customer/machine
                  (see licensing/keygen.py) - variable length, so Text.
        payment_yn: 'Y' once payment is confirmed by the gateway webhook, else 'N'
        software_installed_yn: 'Y' once the installed app reported activation
        plan: 'monthly' | 'yearly' | 'lifetime' (latest purchased plan)
        plan_expires_at: UTC expiry of the current license; NULL = lifetime
        last_payment_id: gateway payment id of the latest successful payment
        amount_paid: amount of the latest payment, smallest currency unit
        license_email_sent_yn: 'Y' once the license email was handed to SMTP
        created_at / updated_at: UTC timestamps
    """
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    address = Column(String(500))
    phone = Column(String(50))
    email = Column(String(255), nullable=False, index=True)
    machine_fingerprint = Column(String(64), nullable=True, index=True)
    soft_key = Column(Text, nullable=True)
    payment_yn = Column(String(1), nullable=False, default="N")
    software_installed_yn = Column(String(1), nullable=False, default="N")
    plan = Column(String(20), nullable=True)
    plan_expires_at = Column(DateTime, nullable=True)
    last_payment_id = Column(String(64), nullable=True)
    amount_paid = Column(Integer, nullable=True)
    license_email_sent_yn = Column(String(1), nullable=False, default="N")
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    def __repr__(self):
        return (f"<Customer(id={self.id}, name='{self.name}', email='{self.email}', "
                f"payment_yn='{self.payment_yn}', plan='{self.plan}')>")


class Order(Base):
    """One checkout attempt. See module docstring."""
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gateway_order_id = Column(String(64), nullable=False, unique=True, index=True)
    gateway_payment_id = Column(String(64), nullable=True, unique=True)
    plan = Column(String(20), nullable=False)
    amount = Column(Integer, nullable=False)          # smallest currency unit
    currency = Column(String(8), nullable=False, default="INR")
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False)
    phone = Column(String(50))
    machine_fingerprint = Column(String(64), nullable=False)
    status = Column(String(12), nullable=False, default="created")  # created | paid | failed
    customer_id = Column(Integer, nullable=True)
    email_sent_yn = Column(String(1), nullable=False, default="N")
    fulfilled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    def __repr__(self):
        return (f"<Order(id={self.id}, gateway_order_id='{self.gateway_order_id}', "
                f"plan='{self.plan}', status='{self.status}')>")


# ---------------------------------------------------------------------------
# Engine / session handling
# ---------------------------------------------------------------------------
_engine_lock = threading.Lock()
_engines = {}
_session_factories = {}


def get_customer_engine():
    """Engine for the current customers.db path (cached per path)."""
    path = get_customers_db_path()
    with _engine_lock:
        engine = _engines.get(path)
        if engine is None:
            engine = create_engine(
                f"sqlite:///{path}", echo=False,
                connect_args={"timeout": 30, "check_same_thread": False},
            )
            _engines[path] = engine
            _session_factories[path] = sessionmaker(bind=engine, expire_on_commit=False)
        return engine


def get_customer_session():
    """A NEW session. The caller is responsible for closing it."""
    engine = get_customer_engine()
    return _session_factories[get_customers_db_path()]()


@contextmanager
def session_scope():
    """Commit on success, roll back on error, always close."""
    session = get_customer_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# Columns added after the first release: (table, column, DDL type)
_ADDED_COLUMNS = (
    ("customers", "plan", "VARCHAR(20)"),
    ("customers", "plan_expires_at", "DATETIME"),
    ("customers", "last_payment_id", "VARCHAR(64)"),
    ("customers", "amount_paid", "INTEGER"),
    ("customers", "license_email_sent_yn", "VARCHAR(1) NOT NULL DEFAULT 'N'"),
)


def _upgrade_schema(engine) -> None:
    """Add columns introduced after the first customers.db format."""
    inspector = inspect(engine)
    for table, column, ddl in _ADDED_COLUMNS:
        if not inspector.has_table(table):
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column not in existing:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            logger.info("customers.db upgraded: added %s.%s", table, column)


def init_customer_db():
    """Create tables (and upgrade an older customers.db in place)."""
    engine = get_customer_engine()
    Base.metadata.create_all(engine)
    _upgrade_schema(engine)
    logger.info("Customer database ready at: %s", get_customers_db_path())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_customer_db()
    print(f"Customer database ready at {get_customers_db_path()}")
