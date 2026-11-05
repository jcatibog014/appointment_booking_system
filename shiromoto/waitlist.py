"""The waitlist: when a full day frees up, the first person waiting is offered the time.

offer_free_times() goes through the people waiting, oldest first. For each one, it
looks for the first free time in their days. If there is one:
1. it claims the entry with a conditional UPDATE (status 'waiting' -> 'offered');
   if another timer claimed it first, the UPDATE matches nothing and it moves on;
2. it HOLDS the time for 30 minutes (booking.hold, so the cells guard it from
   everyone else), and the person gets a notice with the link to take it;
3. if the hold loses a race, the claim is given back (one transaction: all or nothing).
An offer nobody took goes back to waiting (up to 3 offers), and the time is free again.
"""
import secrets
from datetime import timedelta

from flask import url_for
from sqlalchemy import select, update

from . import booking, notices, slots
from .dates import local_today
from .formatting import slot_time
from .models import EXPIRED, OFFERED, TAKEN_UP, WAITING, Hold, Service, WaitlistEntry

OFFER_HOLD_MINUTES = 30
MAX_OFFERS = 3
MAX_WAITING = 200  # per shop, so a script can't fill the list


def services_of(s, entry):
    ids = [int(i) for i in entry.service_ids.split(",") if i]
    return s.scalars(select(Service).where(Service.shop_id == entry.shop_id, Service.id.in_(ids),
                                           Service.is_active.is_(True)).order_by(Service.position)).all()


def join(s, shop, name, phone, email, vehicle_kind, services, first_day, last_day, now, user=None, request_text=""):
    if not services:
        raise booking.BookingError("Choose at least one service.")
    if any(sv.is_other for sv in services) and not request_text.strip():
        raise booking.BookingError(booking.OTHER_NEEDS_TEXT)
    if last_day < first_day:
        raise booking.BookingError("The last day can't be before the first day.")
    today, furthest = slots.bookable_days(shop, now)
    if first_day < today or last_day > furthest:
        raise booking.BookingError(f"Choose days from today to {shop.max_days_ahead} days ahead.")
    waiting = s.scalar(select(WaitlistEntry.id).where(WaitlistEntry.shop_id == shop.id,
                                                      WaitlistEntry.status == WAITING).limit(1).offset(MAX_WAITING))
    if waiting is not None:
        raise booking.BookingError("The waitlist is full right now. Please call the shop.")
    entry = WaitlistEntry(shop_id=shop.id, token=secrets.token_urlsafe(18), name=name, phone=phone, email=email,
                          user_id=user.id if user else None, vehicle_kind=vehicle_kind,
                          service_ids=",".join(str(sv.id) for sv in services), first_day=first_day,
                          request_text=request_text.strip()[:booking.REQUEST_MAX],
                          last_day=last_day, status=WAITING, created_at=now)
    s.add(entry)
    s.commit()
    return entry


def first_free(s, shop, entry, need, now):
    """(day, start) of the first free time in the entry's days, or None."""
    today = local_today(shop.timezone, now)
    day = max(entry.first_day, today)
    while day <= entry.last_day:
        choice = slots.day_choice(s, shop, need, day, now, booking.busy_map(s, shop, day, now))
        if choice.slots:
            return day, choice.slots[0].start
        day += timedelta(days=1)
    return None


def offer(s, shop, entry, now):
    """Try to offer this entry a time. True if it was offered."""
    services = services_of(s, entry)
    need, problem = slots.need_for(services, entry.vehicle_kind)
    if problem:
        return False
    found = first_free(s, shop, entry, need, now)
    if found is None:
        return False
    claimed = s.execute(update(WaitlistEntry).where(WaitlistEntry.id == entry.id, WaitlistEntry.status == WAITING)
                        .values(status=OFFERED, offered_at=now)).rowcount
    s.commit()
    if claimed != 1:
        return False  # someone else is already offering this entry a time
    try:
        held = booking.hold(s, shop, services, entry.vehicle_kind, found[0], found[1], now,
                            minutes=OFFER_HOLD_MINUTES, request_text=entry.request_text)
    except booking.BookingError:
        s.execute(update(WaitlistEntry).where(WaitlistEntry.id == entry.id).values(status=WAITING, offered_at=None))
        s.commit()
        return False
    s.refresh(entry)
    entry.hold_token, entry.offer_expires_at, entry.offers = held.token, held.expires_at, entry.offers + 1
    link = url_for("book.details", token=held.token, _external=True)
    when = f"{notices.day(found[0])} at {slot_time(found[1])}"
    notices.queue(s, shop, "waitlist_offer", f"A time opened up: {when}",
                  f"Hi {entry.name.split()[0]}, a time you wanted is free: {when}. We're keeping it for you for "
                  f"{OFFER_HOLD_MINUTES} minutes. Open the link to book it.", link, f"offer:{entry.id}:{entry.offers}",
                  now, email=entry.email)
    s.commit()
    return True


def offer_free_times(s, shop, now, limit=20):
    """Offer freed times to the people waiting, oldest first. Returns how many were offered."""
    made = 0
    for entry in s.scalars(select(WaitlistEntry).where(WaitlistEntry.shop_id == shop.id,
                                                       WaitlistEntry.status == WAITING)
                           .order_by(WaitlistEntry.created_at, WaitlistEntry.id).limit(limit)).all():
        made += offer(s, shop, entry, now)
    return made


def expire_offer_now(s, entry):
    """Give an offered time back straight away (the person left the list). The caller commits."""
    if entry.hold_token:
        held = s.scalar(select(Hold).where(Hold.token == entry.hold_token))
        if held is not None:
            s.delete(held)
        entry.hold_token = ""


def expire(s, shop, now):
    """Offers that ran out go back to waiting (or end after 3 offers, or after their last day)."""
    today = local_today(shop.timezone, now)
    for entry in s.scalars(select(WaitlistEntry).where(
            WaitlistEntry.shop_id == shop.id, WaitlistEntry.status == OFFERED,
            WaitlistEntry.offer_expires_at <= now)).all():
        held = s.scalar(select(Hold).where(Hold.token == entry.hold_token))
        if held is not None:
            s.delete(held)  # give the time back now, not at the next purge
        entry.status = WAITING if entry.offers < MAX_OFFERS and entry.last_day >= today else EXPIRED
        entry.hold_token = ""
    for entry in s.scalars(select(WaitlistEntry).where(
            WaitlistEntry.shop_id == shop.id, WaitlistEntry.status == WAITING, WaitlistEntry.last_day < today)):
        entry.status = EXPIRED
    s.commit()


def booked_from_offer(s, hold_token):
    """Called when a hold becomes a booking: if it was a waitlist offer, the entry is done."""
    s.execute(update(WaitlistEntry).where(WaitlistEntry.hold_token == hold_token, WaitlistEntry.status == OFFERED)
              .values(status=TAKEN_UP))
    s.commit()
