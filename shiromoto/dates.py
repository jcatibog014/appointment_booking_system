"""Times and days.

The database holds:
- moments (a booking's start, a login) as naive UTC datetimes, from utcnow();
- opening hours as plain times on the shop's own clock;
- closures as plain dates on the shop's own calendar.

Everyone sees moments on the shop's clock (shops.timezone, Asia/Manila),
because a booking happens at the shop, wherever the customer is.
"""
from datetime import UTC, date, datetime, time, timedelta
from functools import cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Asia/Manila"


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


@cache
def zone(name):
    """The time zone called name, or Manila's if that name is unknown."""
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def to_local(moment, tz_name=DEFAULT_TIMEZONE):
    """Naive UTC from the database -> an aware datetime on the shop's clock."""
    return moment.replace(tzinfo=UTC).astimezone(zone(tz_name))


def to_utc(local, tz_name=DEFAULT_TIMEZONE):
    """A naive datetime on the shop's clock -> naive UTC for the database."""
    return local.replace(tzinfo=zone(tz_name)).astimezone(UTC).replace(tzinfo=None)


def local_today(tz_name=DEFAULT_TIMEZONE, moment=None):
    """The shop's calendar day now (or at moment, naive UTC)."""
    return to_local(moment or utcnow(), tz_name).date()


def at(day, clock, tz_name=DEFAULT_TIMEZONE):
    """A day and a time on the shop's clock -> naive UTC."""
    return to_utc(datetime.combine(day, clock), tz_name)


def parse_day(text):
    """What a <input type="date"> sends ("2026-11-12") -> a date, or None if empty or wrong."""
    try:
        return date.fromisoformat((text or "").strip())
    except ValueError:
        return None


def parse_time(text):
    """What a <input type="time"> sends ("08:30") -> a time, or None if empty or wrong."""
    try:
        return time.fromisoformat((text or "").strip())
    except ValueError:
        return None


def minutes(clock):
    """08:30 -> 510: minutes since midnight. Easy to add and compare."""
    return clock.hour * 60 + clock.minute


def clock(total):
    """510 -> 08:30. The other way round (only for times inside one day)."""
    return time(total // 60, total % 60)


def week_start(day):
    """The Monday on or before day."""
    return day - timedelta(days=day.weekday())


def days(first, last):
    """Every day from first to last, both included."""
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]
