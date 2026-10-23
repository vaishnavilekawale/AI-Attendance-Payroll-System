"""
Customer & Order Service (VENDOR side)

This is the business logic behind the purchase portal, the payment webhook
and the admin API. It ties together:

    plans.py                 what was bought, and when it expires
    receipt_generator.py     the PDF payment receipt attached to the email
    customer_models.py       customers.db (customers + orders)
    keygen.py                Ed25519 token signing
    license_email_service.py instant delivery of the token

Trials
------
start_or_get_trial()       called (by the installed app, over the internet) when
                           a new admin finishes setup. The FIRST call for a
                           machine fixes the start date; every later call - a
                           reinstall, a re-run of the wizard - gets that same
                           date back, so a reinstall cannot give a new trial.
                           The answer is a signed trial token (keygen.issue_trial_token).
list_trials()              the vendor's "who is on a trial" list.

Purchase flow
-------------
1. create_pending_order()   called when the buyer presses "Pay": validates the
                            form, prices the plan SERVER-side, asks the gateway
                            for an order and stores it as status='created'.
2. fulfill_paid_order()     called by the gateway webhook after money has
                            really been received. In one go it:
                              - claims the order atomically (safe against the
                                same event being delivered twice, or two event
                                types - payment.captured + order.paid - arriving
                                at the same instant),
                              - verifies the paid amount/currency match,
                              - upserts the customer with payment_yn = 'Y',
                                the plan and the calculated expiry date,
                              - signs a license token bound to the buyer's
                                machine fingerprint (keygen.py),
                              - emails it (license_email_service.py).
   If anything fails BEFORE the license is stored, the claim is released and
   the error is raised so the webhook answers 5xx and the gateway retries.
   If only the email fails, the license is still saved and a later webhook
   retry (or the admin 'resend' endpoint) delivers it.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import func, update

from sqlalchemy.exc import IntegrityError

from .customer_models import Customer, Order, Trial, session_scope, init_customer_db
from .keygen import (
    issue_license_for_plan, issue_license_token, issue_trial_token, is_valid_fingerprint,
)
from .license_email_service import get_license_email_service
from .plans import get_plan, PLAN_LIFETIME, TRIAL_DAYS, trial_days_remaining, trial_end
from .receipt_generator import application_id, build_receipt_pdf, receipt_number

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?[0-9][0-9 \-()]{6,19}$")

# A claim older than this is assumed to belong to a crashed worker.
_STALE_CLAIM = timedelta(minutes=5)

# Fields an admin may change through update_customer().
_UPDATABLE_FIELDS = {
    "name", "email", "address", "phone", "machine_fingerprint",
    "payment_yn", "software_installed_yn", "plan",
}


class ValidationError(ValueError):
    """The submitted purchase / customer data is not acceptable."""


@dataclass
class FulfillmentResult:
    status: str                # 'fulfilled' | 'duplicate' | 'in_progress' | 'rejected' | 'unknown_order'
    order_id: Optional[str] = None
    customer_id: Optional[int] = None
    email_sent: bool = False
    detail: str = ""


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_aware_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _to_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return _to_aware_utc(value).replace(tzinfo=None)


def validate_purchase_form(name, email, phone, machine_fingerprint, plan_code) -> Dict[str, str]:
    """Validate and normalise the public purchase form. Raises ValidationError."""
    name = re.sub(r"\s+", " ", (name or "")).strip()
    email = (email or "").strip().lower()
    phone = (phone or "").strip()
    fingerprint = re.sub(r"\s+", "", machine_fingerprint or "").lower()

    if len(name) < 2 or len(name) > 120:
        raise ValidationError("Please enter your full name.")
    if len(email) > 254 or not _EMAIL_RE.match(email):
        raise ValidationError("Please enter a valid email address - your license is sent there.")
    if not _PHONE_RE.match(phone):
        raise ValidationError("Please enter a valid phone number (digits, optionally starting with +).")
    if not is_valid_fingerprint(fingerprint):
        raise ValidationError(
            "The Machine Fingerprint must be the 64-character code shown on the application's "
            "License Activation screen. Use its Copy button and paste it here.")
    plan = get_plan(plan_code)
    if plan is None:
        raise ValidationError("Please choose a plan.")

    return {"name": name, "email": email, "phone": phone,
            "machine_fingerprint": fingerprint, "plan": plan.code}


def validate_trial_form(admin_name, email, company, machine_fingerprint) -> Dict[str, str]:
    """Validate and normalise what an installed app sends when a trial starts."""
    admin_name = re.sub(r"\s+", " ", (admin_name or "")).strip()
    email = (email or "").strip().lower()
    company = re.sub(r"\s+", " ", (company or "")).strip()
    fingerprint = re.sub(r"\s+", "", machine_fingerprint or "").lower()

    if len(admin_name) < 1 or len(admin_name) > 120:
        raise ValidationError("Admin name is required.")
    if len(email) > 254 or not _EMAIL_RE.match(email):
        raise ValidationError("A valid admin email is required.")
    if len(company) > 255:
        raise ValidationError("Company name is too long.")
    if not is_valid_fingerprint(fingerprint):
        raise ValidationError("Invalid machine fingerprint.")
    return {"admin_name": admin_name, "email": email, "company": company,
            "machine_fingerprint": fingerprint}


def _parse_claimed_start(value, now: datetime) -> Optional[datetime]:
    """
    A re-installing app may say "my local trial began on X" (it ran offline
    before it could reach us). Believe it only if it is in the past and not
    absurdly old. Claiming an EARLIER date can only shorten the claimant's own
    trial, never extend it. Returns naive UTC, or None to ignore the claim.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    parsed = _to_naive_utc(parsed)
    if parsed > now or parsed < now - timedelta(days=365):
        return None
    return parsed


class CustomerService:
    """Customer records, orders and license issuing."""

    def __init__(self, email_service=None):
        init_customer_db()
        self.email_service = email_service or get_license_email_service()

    # ------------------------------------------------------------------
    # Step 1 - checkout
    # ------------------------------------------------------------------
    def create_pending_order(self, form: Dict[str, str], gateway) -> Dict[str, object]:
        """
        Validate the form, create the gateway order and remember it.

        Returns a dict for the browser's Checkout widget:
            {order_id, amount, currency, key_id, plan_name, name, email, phone}
        """
        clean = validate_purchase_form(
            form.get("name"), form.get("email"), form.get("phone"),
            form.get("machine_fingerprint"), form.get("plan"))
        plan = get_plan(clean["plan"])

        # Never take money from someone who already owns a lifetime license for
        # this email + computer: a paid monthly/yearly order would be silently
        # "kept as lifetime" (see _issue_for_customer) and they would get nothing new.
        self._refuse_if_already_lifetime(clean["email"], clean["machine_fingerprint"])

        gateway_order_id = gateway.create_order(
            amount=plan.amount, currency=plan.currency,
            receipt=f"aiaps-{clean['machine_fingerprint'][:12]}-{int(datetime.now(timezone.utc).timestamp())}",
            notes={
                "plan": plan.code,
                "email": clean["email"],
                "name": clean["name"][:60],
                "machine_fingerprint": clean["machine_fingerprint"],
            },
        )

        with session_scope() as session:
            session.add(Order(
                gateway_order_id=gateway_order_id, plan=plan.code,
                amount=plan.amount, currency=plan.currency,
                name=clean["name"], email=clean["email"], phone=clean["phone"],
                machine_fingerprint=clean["machine_fingerprint"], status="created",
            ))

        logger.info("Order %s created: plan=%s email=%s", gateway_order_id, plan.code, clean["email"])
        return {
            "order_id": gateway_order_id, "amount": plan.amount, "currency": plan.currency,
            "key_id": gateway.public_key, "plan_name": plan.name,
            "name": clean["name"], "email": clean["email"], "phone": clean["phone"],
        }

    def get_order_status(self, gateway_order_id: str) -> Optional[Dict[str, object]]:
        """Public-safe order status for the thank-you page poller (no token!)."""
        with session_scope() as session:
            order = session.query(Order).filter_by(gateway_order_id=gateway_order_id).first()
            if not order:
                return None
            return {
                "status": order.status,
                "email_sent": order.email_sent_yn == "Y",
                "email": _mask_email(order.email),
                "plan": order.plan,
            }

    # ------------------------------------------------------------------
    # Step 2 - webhook fulfilment
    # ------------------------------------------------------------------
    def fulfill_paid_order(self, gateway_order_id: str, gateway_payment_id: str,
                           paid_amount: Optional[int] = None,
                           paid_currency: Optional[str] = None,
                           paid_at: Optional[int] = None) -> FulfillmentResult:
        """
        Turn a successful payment into a customer record + license + email
        (with the PDF payment receipt attached). `paid_at` is the gateway's
        payment time as a Unix timestamp (Razorpay: payment.created_at).
        Idempotent - see module docstring. May raise: the caller (webhook)
        should then answer with a 5xx so the gateway retries.
        """
        now = _utcnow_naive()

        # ---- atomically claim the order ---------------------------------
        with session_scope() as session:
            order = session.query(Order).filter_by(gateway_order_id=gateway_order_id).first()
            if order is None:
                logger.warning("Webhook for unknown order %s", gateway_order_id)
                return FulfillmentResult("unknown_order", gateway_order_id, detail="unknown order")
            order_pk = order.id

            claimed = session.execute(
                update(Order)
                .where(Order.id == order_pk)
                .where((Order.status == "created") |
                       ((Order.status == "processing") & (Order.updated_at < now - _STALE_CLAIM)))
                .values(status="processing", gateway_payment_id=gateway_payment_id, updated_at=now)
            ).rowcount

        if claimed != 1:
            return self._handle_unclaimed(order_pk, gateway_order_id)

        # ---- we own the order: verify, issue, store ---------------------
        try:
            with session_scope() as session:
                order = session.get(Order, order_pk)

                if paid_amount is not None and int(paid_amount) != order.amount:
                    order.status = "failed"
                    logger.error("Order %s: paid amount %s != expected %s - NOT issuing a license",
                                 gateway_order_id, paid_amount, order.amount)
                    return FulfillmentResult("rejected", gateway_order_id,
                                             detail="paid amount does not match the order")
                if paid_currency and paid_currency.upper() != order.currency.upper():
                    order.status = "failed"
                    logger.error("Order %s: currency %s != expected %s - NOT issuing a license",
                                 gateway_order_id, paid_currency, order.currency)
                    return FulfillmentResult("rejected", gateway_order_id,
                                             detail="paid currency does not match the order")

                customer = self._find_customer(session, order.email, order.machine_fingerprint)
                if customer is None:
                    customer = Customer(
                        name=order.name, email=order.email, phone=order.phone,
                        machine_fingerprint=order.machine_fingerprint,
                        software_installed_yn="N", payment_yn="N",
                    )
                    session.add(customer)
                else:
                    customer.name = order.name
                    customer.phone = order.phone or customer.phone

                token, plan_code, expires_at = self._issue_for_customer(customer, order.plan)

                customer.soft_key = token
                customer.plan = plan_code
                customer.plan_expires_at = _to_naive_utc(expires_at)
                customer.payment_yn = "Y"
                customer.last_payment_id = gateway_payment_id
                customer.amount_paid = order.amount
                customer.license_email_sent_yn = "N"
                customer.updated_at = _utcnow_naive()
                session.flush()

                order.customer_id = customer.id
                order.status = "paid"
                order.fulfilled_at = _utcnow_naive()
                order.paid_at = self._paid_at_naive(paid_at) or order.fulfilled_at
                order.receipt_no = order.receipt_no or receipt_number(order.id, _to_aware_utc(order.paid_at))
                receipt = self._receipt_data(order, customer, plan_code, expires_at)

                customer_id = customer.id
                name, email, fingerprint = customer.name, customer.email, customer.machine_fingerprint
        except Exception:
            self._release_claim(order_pk)
            raise

        logger.info("Order %s fulfilled: customer=%s plan=%s expires=%s",
                    gateway_order_id, customer_id, plan_code, expires_at)

        # ---- deliver (outside the DB transaction; SMTP can be slow) -----
        email_sent = self._send_and_record(order_pk, customer_id, name, email, token,
                                           fingerprint, plan_code, expires_at, receipt=receipt)
        return FulfillmentResult("fulfilled", gateway_order_id, customer_id, email_sent, "license issued")

    def _handle_unclaimed(self, order_pk: int, gateway_order_id: str) -> FulfillmentResult:
        """The order was not in a claimable state: duplicate delivery or a concurrent worker."""
        with session_scope() as session:
            order = session.get(Order, order_pk)
            if order.status == "paid":
                customer_id = order.customer_id
                email_sent = order.email_sent_yn == "Y"
                resend_args = None
                resend_receipt = None
                if not email_sent and customer_id:
                    customer = session.get(Customer, customer_id)
                    if customer and customer.soft_key:
                        resend_args = (customer.name, customer.email, customer.soft_key,
                                       customer.machine_fingerprint, customer.plan,
                                       _to_aware_utc(customer.plan_expires_at))
                        resend_receipt = self._receipt_data(
                            order, customer, customer.plan, _to_aware_utc(customer.plan_expires_at))
            elif order.status == "processing":
                return FulfillmentResult("in_progress", gateway_order_id,
                                         detail="another worker is processing this order")
            else:
                return FulfillmentResult("rejected", gateway_order_id,
                                         detail=f"order is '{order.status}'")

        if resend_args:
            logger.info("Order %s already paid but email unsent - retrying delivery", gateway_order_id)
            email_sent = self._send_and_record(order_pk, customer_id, *resend_args,
                                               receipt=resend_receipt)
        return FulfillmentResult("duplicate", gateway_order_id, customer_id, email_sent,
                                 "already fulfilled")

    @staticmethod
    def _paid_at_naive(epoch) -> Optional[datetime]:
        """Gateway payment time (Unix seconds) -> naive UTC; None if missing or implausible."""
        try:
            value = datetime.fromtimestamp(int(epoch), tz=timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError, OverflowError, OSError):
            return None
        return value if datetime(2020, 1, 1) < value <= _utcnow_naive() + timedelta(days=1) else None

    @staticmethod
    def _receipt_data(order: Order, customer: Customer, plan_code: Optional[str],
                      expires_at: Optional[datetime]) -> Dict[str, object]:
        """Everything the receipt shows, as a plain dict. Needs the rows loaded (open session)."""
        plan = get_plan(plan_code) if plan_code else None
        paid_at = _to_aware_utc(order.paid_at or order.fulfilled_at)
        return {
            "receipt_no": order.receipt_no or receipt_number(order.id, paid_at),
            "issued_at": datetime.now(timezone.utc),
            "application_id": application_id(customer.id),
            "customer_name": order.name or customer.name,
            "customer_email": order.email or customer.email,
            "customer_phone": order.phone or customer.phone,
            "plan_name": plan.name if plan else (plan_code or "-"),
            "amount": order.amount,
            "currency": order.currency,
            "status": "Paid" if order.status == "paid" else str(order.status).title(),
            "order_id": order.gateway_order_id,
            "payment_id": order.gateway_payment_id,
            "paid_at": paid_at,
            "valid_until": _to_aware_utc(expires_at),
            "machine_fingerprint": order.machine_fingerprint,
        }

    def get_receipt_pdf(self, gateway_order_id: str) -> Optional[bytes]:
        """The PDF receipt of a PAID order (admin download), or None if unknown / not paid."""
        with session_scope() as session:
            order = session.query(Order).filter_by(gateway_order_id=gateway_order_id).first()
            if order is None or order.status != "paid" or not order.customer_id:
                return None
            customer = session.get(Customer, order.customer_id)
            if customer is None:
                return None
            data = self._receipt_data(order, customer, order.plan, _to_aware_utc(customer.plan_expires_at))
        return build_receipt_pdf(data)

    @staticmethod
    def _release_claim(order_pk: int) -> None:
        """Give the order back so the gateway's retry can process it."""
        try:
            with session_scope() as session:
                session.execute(
                    update(Order).where(Order.id == order_pk).where(Order.status == "processing")
                    .values(status="created", updated_at=_utcnow_naive()))
        except Exception as e:  # pragma: no cover - last-ditch cleanup
            logger.error("Could not release claim on order %s: %s", order_pk, e)

    # ------------------------------------------------------------------
    # License issuing helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _find_customer(session, email: str, fingerprint: str) -> Optional[Customer]:
        return (session.query(Customer)
                .filter(func.lower(Customer.email) == email.lower())
                .filter(Customer.machine_fingerprint == fingerprint)
                .order_by(Customer.id.asc()).first())

    @classmethod
    def _refuse_if_already_lifetime(cls, email: str, fingerprint: str) -> None:
        """
        Raise ValidationError (BEFORE any payment is started) when this email +
        machine already holds a paid lifetime license. Monthly / yearly holders
        - including expired ones - are NOT blocked: they can renew, or upgrade
        to lifetime.
        """
        with session_scope() as session:
            customer = cls._find_customer(session, email, fingerprint)
            owns_lifetime = bool(
                customer is not None and customer.payment_yn == "Y"
                and customer.plan == PLAN_LIFETIME and customer.plan_expires_at is None)
        if owns_lifetime:
            raise ValidationError(
                "This computer already has a lifetime license for this email address, so no "
                "purchase is needed and you have not been charged. If you have lost your key, "
                "contact support and we will send it again.")

    @staticmethod
    def _issue_for_customer(customer: Customer, requested_plan: str):
        """
        Sign a token for `customer`'s machine for `requested_plan`.

        - A renewal stacks onto the existing expiry while it is still in the future;
          once the old license has expired the new period starts from today.
        - A monthly / yearly customer may upgrade to lifetime (the lifetime plan has no expiry).
        - A customer who already owns a lifetime license is never downgraded
          (create_pending_order refuses such orders up front; this is the safety net
          for a payment that was already in flight).

        Returns (token, effective_plan_code, expires_at_aware_or_None)
        """
        plan_code = requested_plan
        current_expiry = None
        if customer.payment_yn == "Y":
            if customer.plan == PLAN_LIFETIME and customer.plan_expires_at is None:
                if requested_plan != PLAN_LIFETIME:
                    logger.warning(
                        "Customer %s already has a lifetime license but a '%s' order was paid - "
                        "keeping lifetime; consider refunding that payment.",
                        customer.id, requested_plan)
                plan_code = PLAN_LIFETIME
            else:
                current_expiry = _to_aware_utc(customer.plan_expires_at)

        token, expires_at = issue_license_for_plan(
            customer.machine_fingerprint, customer.name, plan_code,
            current_expiry=current_expiry)
        return token, plan_code, expires_at

    def _send_and_record(self, order_pk, customer_id, name, email, token, fingerprint,
                         plan_code, expires_at, receipt=None) -> bool:
        # Atomically claim the right to send, so two concurrent workers (or a
        # webhook retry racing the original delivery) never email the same
        # license twice. 'S' = a worker is sending right now.
        now = _utcnow_naive()
        with session_scope() as session:
            claimed = session.execute(
                update(Order)
                .where(Order.id == order_pk)
                .where((Order.email_sent_yn == "N") |
                       ((Order.email_sent_yn == "S") & (Order.updated_at < now - _STALE_CLAIM)))
                .values(email_sent_yn="S", updated_at=now)
            ).rowcount
        if claimed != 1:
            logger.info("License email for order %s is already sent or being sent - skipping", order_pk)
            return False

        plan = get_plan(plan_code)
        try:
            sent = bool(self.email_service.send_license_key_email(
                customer_name=name, customer_email=email, license_key=token,
                machine_fingerprint=fingerprint,
                plan_name=plan.name if plan else plan_code, expires_at=expires_at,
                receipt=receipt))
        except Exception as e:
            logger.error("License email for customer %s raised: %s", customer_id, e)
            sent = False

        if sent:
            try:
                with session_scope() as session:
                    order = session.get(Order, order_pk)
                    if order:
                        order.email_sent_yn = "Y"
                    customer = session.get(Customer, customer_id)
                    if customer:
                        customer.license_email_sent_yn = "Y"
            except Exception as e:
                logger.error("Could not record email delivery for order %s: %s", order_pk, e)
        else:
            logger.warning("License email for customer %s NOT sent - resend via the admin API", customer_id)
            try:  # release the claim so a retry / admin resend can deliver it
                with session_scope() as session:
                    session.execute(
                        update(Order).where(Order.id == order_pk).where(Order.email_sent_yn == "S")
                        .values(email_sent_yn="N", updated_at=_utcnow_naive()))
            except Exception as e:  # pragma: no cover
                logger.error("Could not release email claim on order %s: %s", order_pk, e)
        return sent

    # ------------------------------------------------------------------
    # Free trials
    # ------------------------------------------------------------------
    def start_or_get_trial(self, admin_name: str, email: str, company: str,
                           machine_fingerprint: str, claimed_start=None,
                           now: Optional[datetime] = None) -> Dict[str, object]:
        """
        Register this machine's trial, or return the record that already exists.

        The start date is set ONCE. A second call (reinstall) gets the same
        date back and only refreshes the contact details and last-seen time.

        Returns a dict with: status ('started' | 'existing'), started_at,
        expires_at, days_remaining, expired, trial_token.
        """
        form = validate_trial_form(admin_name, email, company, machine_fingerprint)
        now = _to_naive_utc(now) if now else _utcnow_naive()
        start_guess = _parse_claimed_start(claimed_start, now) or now

        status = "existing"
        for attempt in (1, 2):
            try:
                with session_scope() as session:
                    trial = session.query(Trial).filter_by(
                        machine_fingerprint=form["machine_fingerprint"]).first()
                    if trial is None:
                        trial = Trial(machine_fingerprint=form["machine_fingerprint"],
                                      admin_name=form["admin_name"], email=form["email"],
                                      company=form["company"], started_at=start_guess,
                                      last_seen_at=now, checkins=1)
                        session.add(trial)
                        status = "started"
                    else:
                        trial.admin_name = form["admin_name"]
                        trial.email = form["email"]
                        if form["company"]:
                            trial.company = form["company"]
                        trial.last_seen_at = now
                        trial.checkins = (trial.checkins or 0) + 1
                    started_at, admin = trial.started_at, trial.admin_name
                break
            except IntegrityError:
                # Two first-time requests raced; the other one won. Read its row.
                if attempt == 2:
                    raise
                status = "existing"

        started_aware = _to_aware_utc(started_at)
        days = trial_days_remaining(started_aware, _to_aware_utc(now))
        token = issue_trial_token(form["machine_fingerprint"], admin, started_aware)
        logger.info("Trial %s for %s <%s> (%s): %s day(s) left", status, admin, form["email"],
                    form["company"] or "-", days)
        return {
            "status": status,
            "started_at": started_aware.isoformat(),
            "expires_at": trial_end(started_aware).isoformat(),
            "days_remaining": days,
            "expired": days <= 0,
            "trial_token": token,
        }

    def list_trials(self, now: Optional[datetime] = None) -> List[Dict[str, object]]:
        """Every trial, newest first, with days left (the vendor's 'trials' list)."""
        now = _to_aware_utc(_to_naive_utc(now)) if now else datetime.now(timezone.utc)
        with session_scope() as session:
            rows = session.query(Trial).order_by(Trial.started_at.desc()).all()
            paying = {
                (c.machine_fingerprint or "").lower()
                for c in session.query(Customer).filter_by(payment_yn="Y").all()
            }
            result = []
            for t in rows:
                started = _to_aware_utc(t.started_at)
                days = trial_days_remaining(started, now)
                fp = (t.machine_fingerprint or "").lower()
                result.append({
                    "id": t.id,
                    "admin_name": t.admin_name,
                    "email": t.email,
                    "company": t.company,
                    "machine_fingerprint": fp,
                    "started_at": started.isoformat(),
                    "expires_at": trial_end(started).isoformat(),
                    "days_remaining": days,
                    "trial_total_days": TRIAL_DAYS,
                    "status": "converted" if fp in paying else ("expired" if days <= 0 else "active"),
                    "last_seen_at": _to_aware_utc(t.last_seen_at).isoformat() if t.last_seen_at else None,
                    "checkins": t.checkins,
                })
            return result

    # ------------------------------------------------------------------
    # Admin / manual operations (used by customer_routes.py)
    # ------------------------------------------------------------------
    def add_customer(self, name: str, email: str, address: str = None, phone: str = None,
                     machine_fingerprint: str = None, payment_yn: str = 'N',
                     plan: str = None) -> Customer:
        """Manually add a customer (e.g. an offline sale). Issues a license when
        payment_yn='Y' and a fingerprint is given (default plan: lifetime)."""
        if not name or not name.strip():
            raise ValueError("Customer name is required")
        if not email or not _EMAIL_RE.match(email.strip()):
            raise ValueError("A valid customer email is required")
        if payment_yn not in ('Y', 'N'):
            raise ValueError("payment_yn must be 'Y' or 'N'")
        if machine_fingerprint and not is_valid_fingerprint(machine_fingerprint):
            raise ValueError("machine_fingerprint must be a 64-character hex string")
        if plan and get_plan(plan) is None:
            raise ValueError(f"Unknown plan '{plan}'")

        with session_scope() as session:
            existing = session.query(Customer).filter(
                func.lower(Customer.email) == email.strip().lower()).first()
            if existing:
                raise ValueError(f"Customer with email '{email}' already exists")

            customer = Customer(
                name=name.strip(), email=email.strip().lower(),
                address=address.strip() if address else None,
                phone=phone.strip() if phone else None,
                machine_fingerprint=machine_fingerprint.strip().lower() if machine_fingerprint else None,
                payment_yn='N', software_installed_yn='N',
            )
            session.add(customer)
            session.flush()
            if payment_yn == 'Y':
                customer.payment_yn = 'Y'
                self._manual_issue(customer, plan)
            logger.info("Customer added: %s - %s (%s)", customer.id, customer.name, customer.email)
        return self._reload(customer.id)

    def update_customer(self, customer_id: int, **kwargs) -> Customer:
        """Update a customer. Flipping payment_yn N->Y (with a fingerprint) issues a license."""
        unknown = set(kwargs) - _UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"Fields not allowed: {', '.join(sorted(unknown))}")
        if 'payment_yn' in kwargs and kwargs['payment_yn'] not in ('Y', 'N'):
            raise ValueError("payment_yn must be 'Y' or 'N'")
        if 'software_installed_yn' in kwargs and kwargs['software_installed_yn'] not in ('Y', 'N'):
            raise ValueError("software_installed_yn must be 'Y' or 'N'")
        if kwargs.get('machine_fingerprint') and not is_valid_fingerprint(kwargs['machine_fingerprint']):
            raise ValueError("machine_fingerprint must be a 64-character hex string")
        if kwargs.get('plan') and get_plan(kwargs['plan']) is None:
            raise ValueError(f"Unknown plan '{kwargs['plan']}'")

        with session_scope() as session:
            customer = session.get(Customer, customer_id)
            if not customer:
                raise ValueError(f"Customer with ID {customer_id} not found")

            payment_became_y = kwargs.get('payment_yn') == 'Y' and customer.payment_yn != 'Y'
            for key, value in kwargs.items():
                if key == 'machine_fingerprint' and value:
                    value = value.strip().lower()
                setattr(customer, key, value)

            if payment_became_y:
                if customer.machine_fingerprint:
                    self._manual_issue(customer, kwargs.get('plan') or customer.plan)
                else:
                    logger.warning("Payment confirmed for customer %s but no machine fingerprint - "
                                   "cannot generate a license yet", customer_id)
            customer.updated_at = _utcnow_naive()
        return self._reload(customer_id)

    def _manual_issue(self, customer: Customer, plan_code: Optional[str]) -> None:
        """Issue + store + email a license for an admin-driven change. Needs an open session."""
        if not customer.machine_fingerprint:
            return
        token, effective_plan, expires_at = self._issue_for_customer(customer, plan_code or PLAN_LIFETIME)
        customer.soft_key = token
        customer.plan = effective_plan
        customer.plan_expires_at = _to_naive_utc(expires_at)
        customer.license_email_sent_yn = "N"
        plan = get_plan(effective_plan)
        try:
            sent = bool(self.email_service.send_license_key_email(
                customer_name=customer.name, customer_email=customer.email, license_key=token,
                machine_fingerprint=customer.machine_fingerprint,
                plan_name=plan.name if plan else effective_plan, expires_at=expires_at))
        except Exception as e:
            logger.error("License email raised: %s", e)
            sent = False
        customer.license_email_sent_yn = "Y" if sent else "N"

    def resend_license_email(self, customer_id: int) -> bool:
        """Re-send the stored license token (e.g. after fixing SMTP settings)."""
        with session_scope() as session:
            customer = session.get(Customer, customer_id)
            if not customer or not customer.soft_key:
                raise ValueError("Customer not found or no license has been issued yet")
            args = (customer.name, customer.email, customer.soft_key, customer.machine_fingerprint,
                    customer.plan, _to_aware_utc(customer.plan_expires_at))
            # Attach the receipt of the latest paid order, if there is one
            # (licenses added by hand have no payment, so no receipt).
            latest = (session.query(Order).filter_by(customer_id=customer_id, status="paid")
                      .order_by(Order.id.desc()).first())
            receipt = self._receipt_data(latest, customer, customer.plan, args[5]) if latest else None
        plan = get_plan(args[4])
        sent = bool(self.email_service.send_license_key_email(
            customer_name=args[0], customer_email=args[1], license_key=args[2],
            machine_fingerprint=args[3], plan_name=plan.name if plan else args[4], expires_at=args[5],
            receipt=receipt))
        if sent:
            with session_scope() as session:
                session.get(Customer, customer_id).license_email_sent_yn = "Y"
        return sent

    def reissue_license(self, customer_id: int) -> bool:
        """
        Sign the customer's CURRENT plan again, with the same expiry, using the
        current private key, and email it. Use after rotating the signing key:
        licenses signed with the old key no longer verify in new builds.
        """
        with session_scope() as session:
            customer = session.get(Customer, customer_id)
            if not customer:
                raise ValueError("Customer not found")
            if customer.payment_yn != "Y" or not customer.plan or not customer.machine_fingerprint:
                raise ValueError("Only paid customers with a plan and a machine fingerprint can be re-issued")
            expires_at = _to_aware_utc(customer.plan_expires_at)
            token = issue_license_token(customer.machine_fingerprint, customer.name,
                                        expires_at=expires_at, edition=customer.plan)
            customer.soft_key = token
            customer.license_email_sent_yn = "N"
            customer.updated_at = _utcnow_naive()
            args = (customer.name, customer.email, token, customer.machine_fingerprint, customer.plan)
            latest = (session.query(Order).filter_by(customer_id=customer_id, status="paid")
                      .order_by(Order.id.desc()).first())
            receipt = self._receipt_data(latest, customer, customer.plan, expires_at) if latest else None
        plan = get_plan(args[4])
        try:
            sent = bool(self.email_service.send_license_key_email(
                customer_name=args[0], customer_email=args[1], license_key=args[2],
                machine_fingerprint=args[3], plan_name=plan.name if plan else args[4],
                expires_at=expires_at, receipt=receipt))
        except Exception as e:
            logger.error("Re-issued license email for customer %s raised: %s", customer_id, e)
            sent = False
        if sent:
            with session_scope() as session:
                session.get(Customer, customer_id).license_email_sent_yn = "Y"
        return sent

    def _reload(self, customer_id: int) -> Customer:
        with session_scope() as session:
            customer = session.get(Customer, customer_id)
            session.expunge(customer)
            return customer

    def get_customer(self, customer_id: int) -> Optional[Customer]:
        with session_scope() as session:
            customer = session.get(Customer, customer_id)
            if customer:
                session.expunge(customer)
            return customer

    def get_customer_by_email(self, email: str) -> Optional[Customer]:
        with session_scope() as session:
            customer = session.query(Customer).filter(
                func.lower(Customer.email) == (email or "").strip().lower()).first()
            if customer:
                session.expunge(customer)
            return customer

    def get_all_customers(self, payment_status: str = None, installed_status: str = None) -> List[Customer]:
        with session_scope() as session:
            query = session.query(Customer)
            if payment_status in ('Y', 'N'):
                query = query.filter_by(payment_yn=payment_status)
            if installed_status in ('Y', 'N'):
                query = query.filter_by(software_installed_yn=installed_status)
            customers = query.order_by(Customer.created_at.desc()).all()
            session.expunge_all()
            return customers

    def delete_customer(self, customer_id: int) -> bool:
        with session_scope() as session:
            customer = session.get(Customer, customer_id)
            if not customer:
                return False
            session.delete(customer)
        logger.info("Customer deleted: %s", customer_id)
        return True

    def mark_installed_by_token(self, license_token: str, machine_fingerprint: str) -> Optional[str]:
        """Called when an installed app reports a successful activation.
        Returns the customer's name, or None if the token/fingerprint pair is unknown."""
        if not license_token or not is_valid_fingerprint(machine_fingerprint):
            return None
        with session_scope() as session:
            customer = session.query(Customer).filter_by(
                soft_key=license_token.strip(),
                machine_fingerprint=machine_fingerprint.strip().lower(),
                payment_yn='Y').first()
            if not customer:
                return None
            customer.software_installed_yn = 'Y'
            customer.updated_at = _utcnow_naive()
            return customer.name

    def get_customer_stats(self) -> Dict[str, int]:
        with session_scope() as session:
            return {
                'total_customers': session.query(Customer).count(),
                'paid_customers': session.query(Customer).filter_by(payment_yn='Y').count(),
                'unpaid_customers': session.query(Customer).filter_by(payment_yn='N').count(),
                'installed_customers': session.query(Customer).filter_by(software_installed_yn='Y').count(),
                'pending_installation': session.query(Customer).filter_by(
                    payment_yn='Y', software_installed_yn='N').count(),
                'trials_started': session.query(Trial).count(),
            }


def _mask_email(email: str) -> str:
    """a***@example.com - the thank-you page shows where the key went, not the address."""
    local, _, domain = (email or "").partition("@")
    if not domain:
        return ""
    return f"{local[:1]}***@{domain}"


# Singleton instance
_customer_service = None


def get_customer_service() -> CustomerService:
    """Get the singleton CustomerService instance."""
    global _customer_service
    if _customer_service is None:
        _customer_service = CustomerService()
    return _customer_service