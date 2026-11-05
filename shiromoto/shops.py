"""Which shop a request is about, a new shop's starting setup, and Philippine holidays.

The real shop is the one that isn't a demo. A demo visitor (and the
people they "view as") only ever see their own sandbox shop.
"""
from datetime import date, time, timedelta

from flask import abort, g
from sqlalchemy import select

from . import db
from .models import CLOSED, HOLIDAY, MECHANIC, ROLES, Closure, Shop, ShopHours, User


def real_shop(s):
    return s.scalar(select(Shop).where(Shop.is_demo.is_(False)).order_by(Shop.id).limit(1))


def current():
    """This request's shop: the logged-in person's, else the real one. Cached in g."""
    if "shop" not in g:
        g.shop = g.user.shop if g.get("user") is not None else real_shop(db.session())
    return g.shop


def current_or_404():
    shop = current()
    if shop is None:
        abort(404, "The shop isn't set up yet.")
    return shop


def people(s, shop):
    """Everyone with a login at the shop: staff first (by role), then customers."""
    order = {role: n for n, role in enumerate(ROLES)}
    found = s.scalars(select(User).where(User.shop_id == shop.id).order_by(User.name)).all()
    return sorted(found, key=lambda person: order[person.role])


def mechanics(s, shop, active_only=True):
    query = select(User).where(User.shop_id == shop.id, User.role == MECHANIC).order_by(User.name)
    if active_only:
        query = query.where(User.is_active.is_(True))
    return s.scalars(query).all()


def closures_between(s, shop, first, last):
    """Closures (shop-wide and leave) that touch any day from first to last."""
    return s.scalars(select(Closure).where(
        Closure.shop_id == shop.id, Closure.starts_on <= last, Closure.ends_on >= first)
        .order_by(Closure.starts_on, Closure.id)).all()


def day_plan(s, shop, day):
    """Who and what is working on one day, for the desk's overview.

    Returns a dict: hours (ShopHours), shop_closures (closures for the whole
    shop), and mechanics: a list of (mechanic, starts, ends, off) where off
    is the reason they aren't working (or None).
    """
    hours = hours_by_weekday(s, shop)[day.weekday()]
    closures = closures_between(s, shop, day, day)
    shop_closures = [c for c in closures if c.user_id is None]
    whole_day_shut = not hours.is_open or any(c.from_time is None for c in shop_closures)
    crew = []
    for mechanic in mechanics(s, shop):
        own = next((h for h in mechanic.hours if h.weekday == day.weekday()), None)
        starts, ends = (own.starts, own.ends) if own else (hours.opens, hours.closes)
        leave = next((c for c in closures if c.user_id == mechanic.id), None)
        if whole_day_shut:
            off = "Shop closed"
        elif own and not own.works:
            off = "Day off"
        elif leave:
            off = leave.reason
        else:
            off = None
        crew.append((mechanic, starts, ends, off))
    return {"hours": hours, "shop_closures": shop_closures, "closed": whole_day_shut, "mechanics": crew}


# ---------- A new shop's starting week ----------

STARTING_HOURS = {
    # weekday: (opens, closes) with lunch 12:00-13:00; Sunday closed.
    0: (time(8), time(17)), 1: (time(8), time(17)), 2: (time(8), time(17)),
    3: (time(8), time(17)), 4: (time(8), time(17)), 5: (time(8), time(15)),
}
LUNCH = (time(12), time(13))


def add_starting_hours(s, shop):
    for weekday in range(7):
        hours = STARTING_HOURS.get(weekday)
        if hours:
            s.add(ShopHours(shop_id=shop.id, weekday=weekday, is_open=True, opens=hours[0], closes=hours[1],
                            break_starts=LUNCH[0], break_ends=LUNCH[1]))
        else:
            s.add(ShopHours(shop_id=shop.id, weekday=weekday, is_open=False))


def hours_by_weekday(s, shop):
    """{0: ShopHours, ... 6: ShopHours}. A missing day counts as closed."""
    rows = s.scalars(select(ShopHours).where(ShopHours.shop_id == shop.id)).all()
    found = {row.weekday: row for row in rows}
    return {day: found.get(day) or ShopHours(shop_id=shop.id, weekday=day, is_open=False) for day in range(7)}


# ---------- Philippine holidays ----------

def easter(year):
    """Easter Sunday (Gregorian), by the "anonymous" algorithm from 1876.
    Holy Week moves every year, so it has to be worked out, not typed in."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g_ = (b - f + 1) // 3
    h = (19 * a + b - d - g_ + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = (h + l_ - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def last_monday(year, month):
    """The last Monday of a month (National Heroes Day is the last Monday of August)."""
    next_month = date(year + (month == 12), month % 12 + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=last.weekday())


def ph_holidays(year):
    """The regular holidays and the usual special non-working days, as (day, name).
    Eid'l Fitr, Eid'l Adha and Chinese New Year are announced each year, so
    the owner adds those by hand."""
    sunday = easter(year)
    days = [
        (date(year, 1, 1), "New Year's Day"),
        (sunday - timedelta(days=3), "Maundy Thursday"),
        (sunday - timedelta(days=2), "Good Friday"),
        (sunday - timedelta(days=1), "Black Saturday"),
        (date(year, 4, 9), "Araw ng Kagitingan"),
        (date(year, 5, 1), "Labor Day"),
        (date(year, 6, 12), "Independence Day"),
        (date(year, 8, 21), "Ninoy Aquino Day"),
        (last_monday(year, 8), "National Heroes Day"),
        (date(year, 11, 1), "All Saints' Day"),
        (date(year, 11, 30), "Bonifacio Day"),
        (date(year, 12, 8), "Feast of the Immaculate Conception"),
        (date(year, 12, 24), "Christmas Eve"),
        (date(year, 12, 25), "Christmas Day"),
        (date(year, 12, 30), "Rizal Day"),
        (date(year, 12, 31), "Last Day of the Year"),
    ]
    return sorted(days)


def add_ph_holidays(s, shop, year):
    """Add the year's holidays as closures, skipping days that already have a shop closure.
    Returns how many were added."""
    taken = set(s.scalars(select(Closure.starts_on).where(
        Closure.shop_id == shop.id, Closure.user_id.is_(None), Closure.kind.in_((HOLIDAY, CLOSED)),
        Closure.starts_on >= date(year, 1, 1), Closure.starts_on <= date(year, 12, 31))))
    added = 0
    for day, name in ph_holidays(year):
        if day not in taken:
            s.add(Closure(shop_id=shop.id, kind=HOLIDAY, reason=name, starts_on=day, ends_on=day))
            added += 1
    return added
