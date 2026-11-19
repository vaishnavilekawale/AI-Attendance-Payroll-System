"""
Weekly-off / paid-holiday helpers.

A day is an "off day" when it is a Sunday (weekly off) OR an admin has added
it as a Holiday in Settings. Every place that used to hard-code
``weekday() == 6`` now goes through is_off_day() so Sundays and holidays
behave identically (no attendance, no absence, no pay deduction).

Holiday lookups never raise: outside an app context, or if the holidays table
is not there yet, they simply return "no holidays" so Sunday-only behaviour is
preserved.
"""
from datetime import date, timedelta

from sqlalchemy.exc import SQLAlchemyError

SUNDAY = 6


def _query_holidays(start, end):
    try:
        from database import db
        from models import Holiday
        try:
            rows = Holiday.query.filter(Holiday.date >= start, Holiday.date <= end).all()
        except SQLAlchemyError:
            db.session.rollback()
            return {}
        return {r.date: r.name for r in rows}
    except RuntimeError:  # no Flask app context
        return {}


def get_holidays(start, end):
    """{date: name} of holidays between start and end (inclusive)."""
    if end < start:
        return {}
    return _query_holidays(start, end)


def get_holiday_name(d):
    """Holiday name for date d, or None."""
    return get_holidays(d, d).get(d)


def is_off_day(d, holidays=None):
    """True for Sunday or a configured holiday.

    Pass ``holidays`` (a set/dict of dates from get_holidays) when checking many
    dates in a loop to avoid one query per day.
    """
    if d.weekday() == SUNDAY:
        return True
    if holidays is None:
        holidays = get_holidays(d, d)
    return d in holidays


def off_day_reason(d):
    """'Sunday (Weekly Off)', the holiday name, or None for a normal day."""
    if d.weekday() == SUNDAY:
        return 'Sunday (Weekly Off)'
    return get_holiday_name(d)


def count_working_days(start, end):
    """Days from start..end inclusive that are not Sundays or holidays."""
    if end < start:
        return 0
    holidays = get_holidays(start, end)
    count = 0
    d = start
    while d <= end:
        if d.weekday() != SUNDAY and d not in holidays:
            count += 1
        d += timedelta(days=1)
    return count
