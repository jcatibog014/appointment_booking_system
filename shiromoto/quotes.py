"""The Other Service: a job that isn't on the list, priced after it's booked.

1. The customer ticks "Other ... Service", writes what they need, and books a
   time like any other service (the Other Service's own time, bay and skill
   come from Settings). Its price is 0 for now: quote_status = TO_PRICE.
2. The owner, a service advisor or a mechanic reads the request and sends a
   price (send_price): PRICED, and the customer gets a notice.
3. The customer accepts or declines it from their booking link:
   - accept: ACCEPTED, and the price becomes the Other Service's price on the booking;
   - decline: DECLINED, and the Other Service comes off the booking. If it was the
     only service, the booking is cancelled and its time is free again.

The answer is a conditional UPDATE: "... WHERE quote_status = 'priced' AND
quote_price = <the price the customer saw>". So a double tap counts once, an
accept and a decline can't both win, and if the shop changed the price a
moment ago, the customer's answer to the old price is refused, and they see
the new one. Everything about one answer is one transaction.
"""
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm.exc import StaleDataError

from . import booking, slots
from .dates import to_local
from .models import ACCEPTED, BOOKED, CANCELLED, CHECKED_IN, DECLINED, PRICED, QUOTE_OPEN, TO_PRICE, Appointment, \
    AppointmentService, BookedSlot

NOTE_MAX = 300
MAX_PRICE = 100_000_000  # centavos: ₱1,000,000, a typing slip guard
ANSWERED = "This price was changed or already answered a moment ago. Here it is as it is now."


def other_line(appointment):
    return next((line for line in appointment.lines if line.is_other), None)


def send_price(s, shop, appointment, price, note, now, by, version=None):
    """Staff send (or change) the Other Service's price. Commits; the caller queues the notice."""
    if appointment.quote_status not in QUOTE_OPEN or appointment.status not in (BOOKED, CHECKED_IN):
        raise booking.BookingError("This booking has no price to send.")
    if price is None or not 0 < price <= MAX_PRICE:
        raise booking.BookingError("Write the price, like 850.")
    if version is not None and version != appointment.version:
        raise booking.Changed(booking.CHANGED)
    appointment.quote_price = price
    appointment.quote_note = (note or "").strip()[:NOTE_MAX]
    appointment.quote_status = PRICED
    appointment.quoted_at = appointment.updated_at = now
    appointment.quoted_by = by.name
    try:
        s.commit()  # the version check happens here
    except StaleDataError as error:
        s.rollback()
        raise booking.Changed(booking.CHANGED) from error
    return appointment


def claim_answer(s, appointment, shown_price, answer, now):
    """One answer wins: True if this request moved the price from PRICED to `answer`."""
    done = s.execute(
        update(Appointment).where(Appointment.id == appointment.id, Appointment.quote_status == PRICED,
                                  Appointment.quote_price == shown_price,
                                  Appointment.status.in_((BOOKED, CHECKED_IN)))
        .values(quote_status=answer, quote_decided_at=now, updated_at=now, version=Appointment.version + 1)
        .execution_options(synchronize_session=False)).rowcount
    return done == 1


def accept(s, shop, appointment, shown_price, now):
    """The customer says yes to the price they saw."""
    if not claim_answer(s, appointment, shown_price, ACCEPTED, now):
        s.rollback()
        raise booking.Changed(ANSWERED)
    s.execute(update(AppointmentService).where(AppointmentService.appointment_id == appointment.id,
                                               AppointmentService.is_other.is_(True))
              .values(price_from=shown_price).execution_options(synchronize_session=False))
    s.commit()
    s.refresh(appointment)
    return appointment


def decline(s, shop, appointment, shown_price, now):
    """The customer says no: the Other Service comes off the booking, in the same
    transaction as the answer. Alone on the booking, it cancels the booking."""
    if not claim_answer(s, appointment, shown_price, DECLINED, now):
        s.rollback()
        raise booking.Changed(ANSWERED)
    s.refresh(appointment)
    line = other_line(appointment)
    rest = [item for item in appointment.lines if not item.is_other]
    if not rest:
        if appointment.status == BOOKED:
            s.execute(delete(BookedSlot).where(BookedSlot.appointment_id == appointment.id))
            appointment.status = CANCELLED
            appointment.cancelled_at = now
            appointment.cancel_reason = "Declined the price for the other service"
    elif line is not None:
        appointment.lines.remove(line)
        if appointment.status == BOOKED:
            shorten(s, shop, appointment, rest, now)
    appointment.updated_at = now
    try:
        s.commit()
    except StaleDataError as error:
        s.rollback()
        raise booking.Changed(ANSWERED) from error
    return appointment


def shorten(s, shop, appointment, rest, now):
    """Fit the booking to the services left, at the same start: its own cells count as
    free, so the shorter job always fits. Doesn't commit (decline does)."""
    services = booking.services_by_ids(s, shop, [line.service_id for line in rest if line.service_id])
    if len(services) != len(rest):
        return  # a service was removed from the menu since: keep the time as it is
    need, problem = slots.need_for(services, appointment.vehicle.kind)
    if problem:
        return
    local = to_local(appointment.starts_at, shop.timezone)
    start = local.hour * 60 + local.minute
    slot = booking.find_slot(s, shop, need, local.date(), start, now, ignore_appointment=appointment.id,
                             rules=False)
    if slot is None:
        return
    option, bay = booking.pick(slot)
    s.execute(delete(BookedSlot).where(BookedSlot.appointment_id == appointment.id))
    appointment.ends_at = slots.start_moment(shop, local.date(), option.end)
    appointment.work_minutes = need.work_minutes
    appointment.bay_id, appointment.mechanic_id = bay.id, option.mechanic.id
    s.flush()
    booking.add_cells(s, shop, appointment.starts_at, appointment.ends_at, bay.id, option.mechanic.id, appointment)


def open_requests(s, shop, now):
    """Bookings whose Other Service price isn't agreed yet, soonest first: for the desk."""
    return s.scalars(select(Appointment).where(
        Appointment.shop_id == shop.id, Appointment.quote_status.in_(QUOTE_OPEN),
        Appointment.status.in_((BOOKED, CHECKED_IN)), Appointment.ends_at > now - timedelta(days=1))
        .order_by(Appointment.quote_status.desc(), Appointment.starts_at)).all()  # "to_price" before "priced"

