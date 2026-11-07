"""Moving a booking takes both sides. Whoever wants the new time asks; the other
side approves or declines it.
- A customer asks (move_by = "customer"); the owner, an advisor or a mechanic answers.
- Staff propose (move_by = "shop"); the customer answers from their link, or tells
  the desk by phone and staff record it. (Staff can still move at once, with
  booking.move, when the customer already agreed: the page asks them to say so.)

1. ask: the new time is HELD (a booking.hold, kept for up to 24 hours, and never
   past the new time's start or the booking's own), so nobody else takes it while
   the other side decides. The booking stays at its time until then.
2. approve: in ONE transaction, the old cells go, the hold's cells become the
   booking's (the same UPDATE booking.confirm uses, with its count check), the
   booking gets the new time, bay and mechanic, and the hold is deleted.
3. decline or withdraw: the hold is given back; the booking stays as it was.
   A request nobody answered in time runs out (expire, from the timer).

Every answer is claimed first with "UPDATE ... WHERE move_status = 'asked'":
two answers at the same moment (Approve and Withdraw, or two staff pressing
Approve) can't both count. And asking twice at once makes one request: the
second ask's claim fails, and its hold is given back.
"""
from datetime import timedelta

from sqlalchemy import delete, or_, select, update

from . import booking, slots
from .models import BOOKED, MOVE_APPROVED, MOVE_ASKED, MOVE_DECLINED, MOVE_EXPIRED, MOVE_WITHDRAWN, Appointment, \
    BookedSlot, Hold

MOVE_HOLD = timedelta(hours=24)  # the longest a requested time is kept for the other side to decide
CUSTOMER_SIDE, SHOP_SIDE = "customer", "shop"
NOTE_MAX = 300
ALREADY_ASKED = "You already asked for a new time. Wait for the shop's answer, or withdraw it first."
ANSWERED = "This request was already answered, withdrawn or ran out a moment ago. Here it is as it is now."
WAITING_ALREADY = "A new time for this booking is already waiting for an answer. Withdraw it first."


def services_of(s, shop, appointment):
    services = booking.services_by_ids(s, shop, [line.service_id for line in appointment.lines if line.service_id])
    if len(services) != len(appointment.lines):
        raise booking.BookingError("A service on this booking isn't offered any more. Please call the shop.")
    return services


def name_of(by):
    """A staff User, or already a name like "the customer"."""
    return getattr(by, "name", by) or ""


def ask(s, shop, appointment, day, start, now, note="", by=None):
    """Hold the new time and record the request. by=None: the customer asks.
    by=a staff User: the shop proposes it, and staff may pick any time from now
    (no notice and no cut-off). Commits. Raises Taken if the time is gone."""
    shop_side = by is not None
    if appointment.status != BOOKED:
        raise booking.BookingError("Only a booking that is still to come can be moved.")
    if not shop_side and not booking.customer_can_change(shop, appointment, now):
        raise booking.TooLate(f"It's less than {shop.cancel_cutoff_hours} hours before your booking, so it can't "
                              "be moved online. Please call the shop.")
    if appointment.move_waiting(now):
        raise booking.BookingError(WAITING_ALREADY if shop_side else ALREADY_ASKED)
    wanted = slots.start_moment(shop, day, start)
    if wanted == appointment.starts_at:
        raise booking.BookingError("That's the time the booking already has.")
    # An answer after the booking (or the new time) has started is too late to count.
    keep = min(MOVE_HOLD, wanted - now, appointment.starts_at - now)
    if keep <= timedelta(0):
        raise booking.BookingError("That time has passed. Please choose another one.")
    services = services_of(s, shop, appointment)
    held = booking.hold(s, shop, services, appointment.vehicle.kind, day, start, now, by=by,
                        minutes=max(1, int(keep.total_seconds() // 60)), request_text=appointment.request_text)
    claimed = s.execute(
        update(Appointment).where(
            Appointment.id == appointment.id, Appointment.status == BOOKED,
            or_(Appointment.move_status != MOVE_ASKED, Appointment.move_expires_at <= now))
        .values(move_status=MOVE_ASKED, move_by=SHOP_SIDE if shop_side else CUSTOMER_SIDE,
                move_hold_token=held.token, move_from_at=appointment.starts_at,
                move_starts_at=held.starts_at, move_ends_at=held.ends_at, move_expires_at=held.expires_at,
                move_asked_at=now, move_note=(note or "").strip()[:NOTE_MAX], move_reply="", move_decided_by="",
                move_decided_at=None, updated_at=now, version=Appointment.version + 1)
        .execution_options(synchronize_session=False)).rowcount
    if claimed != 1:
        s.rollback()
        booking.release(s, s.get(Hold, held.id))  # the other ask won: give this time back
        raise booking.BookingError(WAITING_ALREADY if shop_side else ALREADY_ASKED)
    s.commit()
    s.refresh(appointment)
    return appointment


def claim(s, appointment, answer, now, by_name="", reply=""):
    """One answer wins: True if this request moved it from "asked" to `answer`."""
    return s.execute(
        update(Appointment).where(Appointment.id == appointment.id, Appointment.move_status == MOVE_ASKED)
        .values(move_status=answer, move_decided_at=now, move_decided_by=by_name,
                move_reply=(reply or "").strip()[:NOTE_MAX], updated_at=now, version=Appointment.version + 1)
        .execution_options(synchronize_session=False)).rowcount == 1


def held_time(s, appointment):
    if not appointment.move_hold_token:
        return None
    return s.scalar(select(Hold).where(Hold.token == appointment.move_hold_token))


def approve(s, shop, appointment, now, by, reply=""):
    """Move the booking to the asked-for time, in one transaction. `by` answers: a staff
    User for a customer's request, or the customer ("the customer") for the shop's."""
    if appointment.status != BOOKED or not appointment.move_waiting(now) or not claim(
            s, appointment, MOVE_APPROVED, now, name_of(by), reply):
        s.rollback()
        raise booking.Changed(ANSWERED)
    held = held_time(s, appointment)
    if held is None or held.expires_at <= now:
        s.rollback()
        raise booking.Gone("The new time ran out and was given back. Please choose again.")
    s.execute(delete(BookedSlot).where(BookedSlot.appointment_id == appointment.id))
    expected = 2 * len(booking.cells(held.starts_at, held.ends_at))
    moved = s.execute(update(BookedSlot).where(BookedSlot.hold_id == held.id).values(
        appointment_id=appointment.id, hold_id=None, expires_at=None)).rowcount
    if moved != expected:
        s.rollback()
        raise booking.Gone("The new time ran out and was given back. Please choose again.")
    s.refresh(appointment)  # the claim changed its version
    appointment.starts_at, appointment.ends_at = held.starts_at, held.ends_at
    appointment.bay_id, appointment.mechanic_id = held.bay_id, held.mechanic_id
    appointment.updated_at = now
    s.execute(delete(Hold).where(Hold.id == held.id))
    s.commit()
    return appointment


def close(s, appointment, answer, now, by_name="", reply=""):
    """Decline or withdraw: claim the answer and give the held time back, together."""
    if not claim(s, appointment, answer, now, by_name, reply):
        s.rollback()
        raise booking.Changed(ANSWERED)
    if appointment.move_hold_token:
        s.execute(delete(Hold).where(Hold.token == appointment.move_hold_token))
    s.commit()
    s.refresh(appointment)
    return appointment


def decline(s, shop, appointment, now, by, reply=""):
    return close(s, appointment, MOVE_DECLINED, now, name_of(by), reply)


def withdraw(s, shop, appointment, now):
    return close(s, appointment, MOVE_WITHDRAWN, now)


def drop_request(s, appointment, now):
    """The booking is being cancelled or moved by staff: a waiting request ends with it.
    Doesn't commit: it's part of the caller's transaction."""
    if appointment.move_status != MOVE_ASKED:
        return
    if appointment.move_hold_token:
        s.execute(delete(Hold).where(Hold.token == appointment.move_hold_token))
    appointment.move_status, appointment.move_decided_at = MOVE_WITHDRAWN, now


def expire(s, shop, now):
    """For the timer: requests nobody answered before their held time ran out.
    Returns the bookings that changed, so the customer can be told."""
    ran_out = []
    for appointment in s.scalars(select(Appointment).where(
            Appointment.shop_id == shop.id, Appointment.move_status == MOVE_ASKED,
            Appointment.move_expires_at <= now)).all():
        if claim(s, appointment, MOVE_EXPIRED, now):
            ran_out.append(appointment)
    s.commit()
    for appointment in ran_out:
        s.refresh(appointment)  # the claim was an UPDATE the session didn't see
    return ran_out


def open_requests(s, shop, now):
    """New times waiting for an answer, oldest first: for the desk. The customer's wait
    for the shop; the shop's wait for the customer (move_by tells which)."""
    return s.scalars(select(Appointment).where(
        Appointment.shop_id == shop.id, Appointment.move_status == MOVE_ASKED, Appointment.status == BOOKED,
        Appointment.move_expires_at > now).order_by(Appointment.move_asked_at)).all()


def place_request(s, shop, appointment, day, start, now, note="", side=CUSTOMER_SIDE):
    """A request for the sample data: like ask, without the booking rules or a commit."""
    services = services_of(s, shop, appointment)
    need, problem = slots.need_for(services, appointment.vehicle.kind)
    slot = None if problem else booking.find_slot(s, shop, need, day, start, now, rules=False)
    if slot is None:
        return None
    option, bay = booking.pick(slot)
    starts, ends = slots.start_moment(shop, day, start), slots.start_moment(shop, day, option.end)
    held = Hold(shop_id=shop.id, token=booking.secrets.token_urlsafe(24), vehicle_kind=appointment.vehicle.kind,
                service_ids=",".join(str(sv.id) for sv in services), bay_id=bay.id, mechanic_id=option.mechanic.id,
                starts_at=starts, ends_at=ends, work_minutes=need.work_minutes, created_at=now,
                expires_at=min(now + MOVE_HOLD, starts, appointment.starts_at))
    s.add(held)
    s.flush()
    booking.add_cells(s, shop, starts, ends, bay.id, option.mechanic.id, hold=held)
    appointment.move_status, appointment.move_hold_token, appointment.move_by = MOVE_ASKED, held.token, side
    appointment.move_from_at, appointment.move_starts_at, appointment.move_ends_at = appointment.starts_at, starts, ends
    appointment.move_expires_at, appointment.move_asked_at, appointment.move_note = held.expires_at, now, note
    return appointment
