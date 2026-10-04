"""
License plans, pricing and expiry calculation (VENDOR side).

This is the single source of truth for what a customer can buy. The price
of an order is ALWAYS looked up here on the server - the browser only ever
sends a plan *code* ("monthly" / "yearly" / "lifetime"), never an amount,
so a customer cannot buy a lifetime license for the price of a month by
editing the page.

Prices are in the smallest currency unit (paise for INR) because that is
what Razorpay expects. Override them without touching code via environment
variables on the vendor server, e.g.:

    PLAN_PRICE_MONTHLY=99900      # Rs 999.00
    PLAN_PRICE_YEARLY=999900      # Rs 9,999.00
    PLAN_PRICE_LIFETIME=2499900   # Rs 24,999.00
    PLAN_CURRENCY=INR

Expiry rules (see compute_expiry):
    monthly   -> +1 calendar month
    yearly    -> +12 calendar months
    lifetime  -> never expires (expires_at = None => perpetual token)

A renewal of a still-running plan extends from the CURRENT expiry rather
than from today, so paying early never wastes the days already paid for.
"""
import calendar
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

PLAN_MONTHLY = "monthly"
PLAN_YEARLY = "yearly"
PLAN_LIFETIME = "lifetime"


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    amount: int          # smallest currency unit (paise)
    currency: str
    months: Optional[int]  # None => perpetual
    tagline: str

    @property
    def is_lifetime(self) -> bool:
        return self.months is None

    @property
    def display_price(self) -> str:
        """Human readable price, e.g. 'Rs 999' (no decimals when whole)."""
        major = self.amount / 100
        symbol = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}.get(
            self.currency, self.currency + " ")
        if major == int(major):
            return f"{symbol}{int(major):,}"
        return f"{symbol}{major:,.2f}"

    @property
    def duration_label(self) -> str:
        if self.months is None:
            return "one-time, valid forever"
        if self.months == 1:
            return "per month"
        if self.months == 12:
            return "per year"
        return f"per {self.months} months"


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def get_plans() -> Dict[str, Plan]:
    """Build the plan table (re-read each call so env changes apply)."""
    currency = (os.environ.get("PLAN_CURRENCY") or "INR").strip().upper() or "INR"
    return {
        PLAN_MONTHLY: Plan(
            code=PLAN_MONTHLY, name="Monthly Plan",
            amount=_env_int("PLAN_PRICE_MONTHLY", 99900), currency=currency,
            months=1, tagline="Flexible - cancel any time by simply not renewing.",
        ),
        PLAN_YEARLY: Plan(
            code=PLAN_YEARLY, name="Yearly Plan",
            amount=_env_int("PLAN_PRICE_YEARLY", 999900), currency=currency,
            months=12, tagline="Best value for ongoing use - two months free.",
        ),
        PLAN_LIFETIME: Plan(
            code=PLAN_LIFETIME, name="Lifetime Plan",
            amount=_env_int("PLAN_PRICE_LIFETIME", 2499900), currency=currency,
            months=None, tagline="Pay once, use forever on one machine.",
        ),
    }


def get_plan(code: str) -> Optional[Plan]:
    """Look a plan up by code (case-insensitive). None if unknown."""
    if not code:
        return None
    return get_plans().get(str(code).strip().lower())


def add_months(start: datetime, months: int) -> datetime:
    """Add calendar months, clamping the day (Jan 31 + 1 month = Feb 28/29)."""
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def compute_expiry(plan: Plan, now: Optional[datetime] = None,
                   current_expiry: Optional[datetime] = None) -> Optional[datetime]:
    """
    Work out the expiry (timezone-aware UTC) for a purchase of `plan`.

    Args:
        plan: the purchased plan.
        now: "today" (injectable for tests); defaults to the current UTC time.
        current_expiry: expiry of the customer's existing, still-running
            license for the same machine (if any). A renewal is stacked on
            top of it instead of starting from today.

    Returns:
        A UTC datetime, or None for a lifetime (perpetual) plan.
    """
    if plan.is_lifetime:
        return None

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    base = now
    if current_expiry is not None:
        if current_expiry.tzinfo is None:
            current_expiry = current_expiry.replace(tzinfo=timezone.utc)
        if current_expiry > now:
            base = current_expiry

    return add_months(base, plan.months)
