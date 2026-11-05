"""Booking without double booking: hold a time, confirm it, move it, cancel it.

The guard is the booked_slots table. A booking (or a hold) writes one row
per quarter hour for its bay AND one per quarter hour for its mechanic.
UNIQUE(resource_kind, resource_id, slot_start) means the database refuses
a second row for the same bay or mechanic at the same quarter hour.

So a race goes like this. Two people press "9:00" at the same instant. Both
requests see 9:00 free, and both try to write their cells. One commits first;
the other's INSERT breaks the UNIQUE rule (IntegrityError). It is rolled back
WHOLE (nothing half-saved), reads the day again, and either finds another bay
or mechanic for 9:00, or tells the person "That time was just taken".

On PostgreSQL the request also locks the bay and mechanic rows it might use
(SELECT ... FOR UPDATE) before reading, so the second request simply waits
for the first and then sees its cells. And an exclusion constraint on
appointments (models.NO_OVERLAP_SQL) refuses overlapping bookings even if
the cells were ever wrong. Three locks on one door, on purpose: this is the
part the project is here to practise.

Every function here does its own commit or rollback: one call is one
transaction, so a caller can never leave half a booking behind.
"""
import hashlib
import hmac
import re
import secrets
from datetime import time, timedelta

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm.exc import StaleDataError

from . import slots
from .dates import at, to_local
from .models import BOOKED, CANCELLED, CHECKED_IN, NO_SHOW, ONLINE, TO_PRICE, Appointment, AppointmentService, \
    Bay, BookedSlot, Customer, Hold, Service, User, Vehicle

CELL = timedelta(minutes=slots.STEP)
MAX_ATTEMPTS = 3  # a lost race is tried again this many times, with a fresh look at the day
REQUEST_MAX = 400  # characters for "what do you need?" on the Other Service
OTHER_NEEDS_TEXT = "Tell us what you need, so we can check it and send you the price."
MAX_HOLDS = 40  # live holds per shop, so a script can't hold every time at once
REF_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L: easy to read out on the phone


class BookingError(Exception):
    """A message for the person: why it didn't work."""


class Taken(BookingError):
    pass


class Gone(BookingError):
    pass


class TooLate(BookingError):
    pass


class Changed(BookingError):
    pass


TAKEN = "That time was just taken. Here are the times that are still free."
GONE = "Your held time ran out and someone else took it. Please choose a time again."
CHANGED = "Someone else changed this booking a moment ago. Here it is as it is now."


# ---------- Small pieces ----------

def phone_key(text):
    """"0917 555 0142", "+63 917 555 0142", "(0917) 555-0142" -> "09175550142"."""
    digits = re.sub(r"\D", "", text or "")
    if digits.startswith("63") and len(digits) == 12:
        digits = "0" + digits[2:]
    return digits


def phone_problem(text):
    return None if 7 <= len(phone_key(text)) <= 15 else "Enter a mobile number, like 0917 555 0142."


def plate_key(text):
    """"abc-1234" and "ABC 1234" are the same plate: "ABC1234"."""
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def plate_problem(text):
    return None if 2 <= len(plate_key(text)) <= 12 else "Enter the plate number, like ABC 1234."


def token_hash(token):
    return hashlib.sha256((token or "").encode()).hexdigest()


def token_matches(appointment, token):
    return bool(token) and hmac.compare_digest(token_hash(token), appointment.token_hash)


def cells(starts, ends):
    """Every quarter hour from starts (included) to ends (not included), in UTC."""
    found, moment = [], starts
    while moment < ends:
        found.append(moment)
        moment += CELL
    return found


def new_ref(s):
    for _ in range(20):
        ref = "SM-" + "".join(secrets.choice(REF_ALPHABET) for _ in range(5))
        if s.scalar(select(Appointment.id).where(Appointment.ref == ref)) is None:
            return ref
    raise RuntimeError("Couldn't find a free booking reference.")


def is_postgres(s):
    return s.get_bind().dialect.name == "postgresql"


def lock_resources(s, shop, need):
    """PostgreSQL: lock every bay and mechanic this job could use, always in id order
    (the same order for everyone, so two requests can never wait on each other in a
    circle). A second request for the same kind of job waits here until the first
    commits, then reads the day fresh. SQLite has no row locks; its cells do the job."""
    if not is_postgres(s):
        return
    s.execute(select(Bay.id).where(Bay.shop_id == shop.id, Bay.bay_type_id == need.bay_type_id)
              .order_by(Bay.id).with_for_update())
    s.execute(select(User.id).where(User.shop_id == shop.id, User.role == "mechanic")
              .order_by(User.id).with_for_update())


# ---------- Reading what is taken ----------

def busy_map(s, shop, day, now, ignore_appointment=None):
    """Taken quarter hours on one day, for slots.load_day:
    {("bay", 3): [(480, 600), ...], ("mechanic", 7): [...]} in shop minutes.
    A hold's cells stop counting once it has run out."""
    first = at(day, time(0), shop.timezone)
    query = select(BookedSlot.resource_kind, BookedSlot.resource_id, BookedSlot.slot_start).where(
        BookedSlot.shop_id == shop.id, BookedSlot.slot_start >= first, BookedSlot.slot_start < first + timedelta(days=1),
        or_(BookedSlot.expires_at.is_(None), BookedSlot.expires_at > now))
    if ignore_appointment is not None:
        query = query.where(or_(BookedSlot.appointment_id.is_(None), BookedSlot.appointment_id != ignore_appointment))
    minutes_by = {}
    for kind, resource_id, moment in s.execute(query):
        local = to_local(moment, shop.timezone)
        minutes_by.setdefault((kind, resource_id), []).append(local.hour * 60 + local.minute)
    return {key: merge(values) for key, values in minutes_by.items()}


def merge(starts):
    """[480, 495, 510, 600] -> [(480, 525), (600, 615)]: cells joined into intervals."""
    intervals = []
    for start in sorted(starts):
        if intervals and intervals[-1][1] == start:
            intervals[-1] = (intervals[-1][0], start + slots.STEP)
        else:
            intervals.append((start, start + slots.STEP))
    return intervals


def find_slot(s, shop, need, day, start, now, ignore_appointment=None, rules=True, notice=None):
    """The free slot at `start` on `day`, or None. rules=False skips the booking
    window and notice (for sample data). notice=0 is staff booking "from now on"."""
    busy = busy_map(s, shop, day, now, ignore_appointment)
    if rules:
        found = slots.day_choice(s, shop, need, day, now, busy, notice).slots
    else:
        found, _ = slots.free_slots(slots.load_day(s, shop, day, busy), need)
    return next((slot for slot in found if slot.start == start), None)


def pick(slot):
    """Which mechanic and bay: the mechanic with the fewest booked minutes that day
    (so work is shared out), then the first free bay in the shop's order."""
    option = min(slot.options, key=lambda o: (sum(b - a for a, b in o.mechanic.busy), o.mechanic.name))
    return option, option.bays[0]


def add_cells(s, shop, starts, ends, bay_id, mechanic_id, appointment=None, hold=None):
    owner = {"appointment_id": appointment.id} if appointment else {"hold_id": hold.id, "expires_at": hold.expires_at}
    for moment in cells(starts, ends):
        for kind, resource_id in (("bay", bay_id), ("mechanic", mechanic_id)):
            s.add(BookedSlot(shop_id=shop.id, resource_kind=kind, resource_id=resource_id, slot_start=moment, **owner))
    s.flush()  # the UNIQUE rule is checked here, so a lost race shows up inside the try


def services_by_ids(s, shop, ids):
    return s.scalars(select(Service).where(Service.shop_id == shop.id, Service.id.in_(ids), Service.is_active.is_(True))
                     .order_by(Service.position, Service.name)).all()


# ---------- Holding a time ----------

def purge_holds(s, shop, now):
    """Delete holds that ran out (their cells go with them: ON DELETE CASCADE)."""
    s.execute(delete(Hold).where(Hold.shop_id == shop.id, Hold.expires_at <= now)
              .execution_options(synchronize_session=False))


def hold(s, shop, services, vehicle_kind, day, start, now, by=None, minutes=None, request_text=""):
    """Keep `start` on `day` for shop.hold_minutes. Returns the Hold, or raises Taken.
    Staff (`by` is staff) may book from right now: a walk-in needs no notice."""
    notice = 0 if by is not None and by.is_staff else None
    need, problem = slots.need_for(services, vehicle_kind)
    if problem:
        raise BookingError(problem)
    for _ in range(MAX_ATTEMPTS):
        try:
            purge_holds(s, shop, now)
            # Short holds only: a move request keeps its time for up to a day (moves.py), and
            # shouldn't stop people booking.
            if s.scalar(select(func.count()).select_from(Hold).where(
                    Hold.shop_id == shop.id, Hold.expires_at <= now + timedelta(hours=1))) >= MAX_HOLDS:
                raise BookingError("Booking is very busy right now. Please try again in a few minutes.")
            lock_resources(s, shop, need)
            slot = find_slot(s, shop, need, day, start, now, notice=notice)
            if slot is None:
                raise Taken(TAKEN)
            option, bay = pick(slot)
            held = Hold(shop_id=shop.id, token=secrets.token_urlsafe(24), vehicle_kind=vehicle_kind,
                        service_ids=",".join(str(sv.id) for sv in services), bay_id=bay.id,
                        request_text=request_text if any(sv.is_other for sv in services) else "",
                        mechanic_id=option.mechanic.id, starts_at=slots.start_moment(shop, day, start),
                        ends_at=slots.start_moment(shop, day, option.end), work_minutes=need.work_minutes,
                        created_by_id=by.id if by else None, created_at=now,
                        expires_at=now + timedelta(minutes=minutes or shop.hold_minutes))
            s.add(held)
            s.flush()
            add_cells(s, shop, held.starts_at, held.ends_at, bay.id, option.mechanic.id, hold=held)
            s.commit()
            return held
        except (IntegrityError, OperationalError):
            s.rollback()  # lost the race (or SQLite was busy): look again
        except BookingError:
            s.rollback()
            raise
    raise Taken(TAKEN)


def release(s, held):
    """Give a held time back straight away."""
    s.delete(held)
    s.commit()


# ---------- Turning a hold into a booking ----------

def customer_for_user(s, shop, user):
    """A logged-in customer's own customer row (made on first use)."""
    customer = s.scalar(select(Customer).where(Customer.shop_id == shop.id, Customer.user_id == user.id))
    if customer is None:
        customer = Customer(shop_id=shop.id, user_id=user.id, name=user.name, phone=user.phone,
                            phone_key=phone_key(user.phone), email=user.email, created_at=user.created_at)
        s.add(customer)
        s.flush()
    return customer


def find_customer(s, shop, details, user, now):
    if user is not None and user.role == "customer":
        return customer_for_user(s, shop, user)
    key = phone_key(details["phone"])
    customer = s.scalar(select(Customer).where(Customer.shop_id == shop.id, Customer.phone_key == key,
                                               Customer.user_id.is_(None)).order_by(Customer.id).limit(1))
    if customer is None:
        customer = Customer(shop_id=shop.id, name=details["name"], phone=details["phone"], phone_key=key,
                            email=details.get("email", ""), created_at=now)
        s.add(customer)
        s.flush()
    return customer


def find_vehicle(s, shop, customer, kind, details, now):
    """The vehicle by its plate. A plate seen before keeps its history (and its owner's
    details); a new plate is added to this customer."""
    key = plate_key(details["plate"])
    vehicle = s.scalar(select(Vehicle).where(Vehicle.shop_id == shop.id, Vehicle.plate_key == key))
    if vehicle is None:
        vehicle = Vehicle(shop_id=shop.id, customer_id=customer.id, kind=kind, plate=details["plate"].strip().upper(),
                          plate_key=key, make=details.get("make", ""), model=details.get("model", ""),
                          year=details.get("year"), created_at=now)
        s.add(vehicle)
        s.flush()
    elif vehicle.kind != kind:
        raise BookingError(f"The plate {vehicle.plate} is saved as a {'car' if vehicle.kind == 'car' else 'motorcycle'}.")
    return vehicle


def confirm(s, shop, held, details, key, now, user=None, source=ONLINE):
    """Make the booking from a hold, all in one transaction. Returns the Appointment,
    with .token (the manage link's secret) set once, here.

    The same `key` (the form's one-time key) always gives the same booking: a
    double-click, or pressing Back and Book again, never makes two."""
    existing = s.scalar(select(Appointment).where(Appointment.idempotency_key == key))
    if existing is not None:
        return existing
    try:
        # Ask the database, not the session's memory: another request may have purged it.
        if held is None or s.scalar(select(Hold.id).where(Hold.id == held.id)) is None:
            raise Gone(GONE)
        services = services_by_ids(s, shop, [int(i) for i in held.service_ids.split(",") if i])
        if not services:
            raise BookingError("These services aren't offered any more. Please call the shop.")
        request_text = (details.get("request") or held.request_text or "").strip()
        if any(sv.is_other for sv in services) and not request_text:
            raise BookingError(OTHER_NEEDS_TEXT)
        customer = find_customer(s, shop, details, user, now)
        vehicle = find_vehicle(s, shop, customer, held.vehicle_kind, details, now)
        token = secrets.token_urlsafe(18)
        appointment = Appointment(
            shop_id=shop.id, ref=new_ref(s), token_hash=token_hash(token), customer_id=customer.id,
            vehicle_id=vehicle.id, bay_id=held.bay_id, mechanic_id=held.mechanic_id, starts_at=held.starts_at,
            ends_at=held.ends_at, work_minutes=held.work_minutes, status=BOOKED, source=source,
            notes=details.get("notes", ""), idempotency_key=key, created_by_id=user.id if user else None,
            created_at=now, updated_at=now)
        appointment.lines = lines_for(services)
        set_request(appointment, services, request_text)
        s.add(appointment)
        s.flush()
        # The hold's cells become the booking's cells. If someone purged the hold
        # (it ran out), its cells are gone and the count is short: start again.
        expected = 2 * len(cells(held.starts_at, held.ends_at))
        moved = s.execute(update(BookedSlot).where(BookedSlot.hold_id == held.id).values(
            appointment_id=appointment.id, hold_id=None, expires_at=None)).rowcount
        if moved != expected:
            raise Gone(GONE)
        s.execute(delete(Hold).where(Hold.id == held.id))
        s.commit()
    except (IntegrityError, OperationalError, Gone) as error:
        s.rollback()
        again = s.scalar(select(Appointment).where(Appointment.idempotency_key == key))
        if again is not None:
            return again  # the other click won; it's the same booking
        raise Gone(GONE) from error
    except BookingError:
        s.rollback()
        raise
    appointment.token = token
    return appointment


def lines_for(services):
    """A booking's copy of its services. The Other Service's price is 0 until the customer
    accepts the shop's price (quotes.accept)."""
    return [AppointmentService(service_id=sv.id, name=sv.name, price_from=0 if sv.is_other else sv.price_from,
                               work_minutes=sv.work_minutes, is_other=sv.is_other) for sv in services]


def set_request(appointment, services, request_text):
    """An Other Service on the booking: keep what the customer asked for; the shop prices it next."""
    if any(sv.is_other for sv in services):
        appointment.request_text = request_text.strip()[:REQUEST_MAX]
        appointment.quote_status = TO_PRICE


def place(s, shop, services, vehicle, customer, day, start, now, source=ONLINE, notes="", status=BOOKED,
          request_text=""):
    """Book straight away, without a hold or the booking rules: for sample data.
    Returns the Appointment, or None if that time isn't free."""
    need, problem = slots.need_for(services, vehicle.kind)
    if problem:
        raise BookingError(problem)
    slot = find_slot(s, shop, need, day, start, now, rules=False)
    if slot is None:
        return None
    option, bay = pick(slot)
    appointment = Appointment(
        shop_id=shop.id, ref=new_ref(s), token_hash=token_hash(secrets.token_urlsafe(18)), customer_id=customer.id,
        vehicle_id=vehicle.id, bay_id=bay.id, mechanic_id=option.mechanic.id,
        starts_at=slots.start_moment(shop, day, start), ends_at=slots.start_moment(shop, day, option.end),
        work_minutes=need.work_minutes, status=status, source=source, notes=notes, created_at=now, updated_at=now)
    appointment.lines = lines_for(services)
    set_request(appointment, services, request_text or "Please check it.")
    s.add(appointment)
    s.flush()
    if status == BOOKED:
        add_cells(s, shop, appointment.starts_at, appointment.ends_at, bay.id, option.mechanic.id, appointment)
    return appointment


# ---------- Changing a booking ----------

def customer_can_change(shop, appointment, now):
    """Customers may move or cancel online until cancel_cutoff_hours before the start."""
    return now <= appointment.starts_at - timedelta(hours=shop.cancel_cutoff_hours)


def check_change(shop, appointment, version, now, by_staff):
    if appointment.status != BOOKED:
        raise BookingError("This booking is already cancelled.")
    if version is not None and version != appointment.version:
        raise Changed(CHANGED)
    if not by_staff and not customer_can_change(shop, appointment, now):
        raise TooLate(f"It's less than {shop.cancel_cutoff_hours} hours before your booking, so it can't be "
                      "changed online. Please call the shop.")


def need_of(s, shop, appointment):
    services = services_by_ids(s, shop, [line.service_id for line in appointment.lines if line.service_id])
    if len(services) != len(appointment.lines):
        raise BookingError("A service on this booking isn't offered any more. Please call the shop to move it.")
    need, problem = slots.need_for(services, appointment.vehicle.kind)
    if problem:
        raise BookingError(problem)
    return need


def move(s, shop, appointment, day, start, now, version=None, by_staff=False):
    """Move a booking to a new time in one transaction: free its old cells and
    take new ones. The booking's own old time counts as free, so it can shift
    by half an hour into itself."""
    check_change(shop, appointment, version, now, by_staff)
    need = need_of(s, shop, appointment)
    for _ in range(MAX_ATTEMPTS):
        try:
            lock_resources(s, shop, need)
            slot = find_slot(s, shop, need, day, start, now, ignore_appointment=appointment.id,
                             notice=0 if by_staff else None)
            if slot is None:
                raise Taken(TAKEN)
            option, bay = pick(slot)
            from . import moves
            moves.drop_request(s, appointment, now)  # staff moved it: a waiting request ends here
            s.execute(delete(BookedSlot).where(BookedSlot.appointment_id == appointment.id))
            appointment.starts_at = slots.start_moment(shop, day, start)
            appointment.ends_at = slots.start_moment(shop, day, option.end)
            appointment.bay_id, appointment.mechanic_id = bay.id, option.mechanic.id
            appointment.updated_at = now
            s.flush()  # the version check happens here
            add_cells(s, shop, appointment.starts_at, appointment.ends_at, bay.id, option.mechanic.id, appointment)
            s.commit()
            return appointment
        except StaleDataError as error:
            s.rollback()
            raise Changed(CHANGED) from error
        except (IntegrityError, OperationalError):
            s.rollback()
            s.refresh(appointment)
        except BookingError:
            s.rollback()
            raise
    raise Taken(TAKEN)


def cancel(s, shop, appointment, reason, now, version=None, by_staff=False):
    """Cancel in one transaction: the status changes and the cells are freed together."""
    check_change(shop, appointment, version, now, by_staff)
    from . import moves
    try:
        moves.drop_request(s, appointment, now)  # its held new time is given back too
        s.execute(delete(BookedSlot).where(BookedSlot.appointment_id == appointment.id))
        appointment.status = CANCELLED
        appointment.cancel_reason = (reason or "").strip()[:200]
        appointment.cancelled_at = appointment.updated_at = now
        s.commit()
    except StaleDataError as error:
        s.rollback()
        raise Changed(CHANGED) from error
    return appointment


# ---------- At the front desk ----------

CHECK_IN_EARLY = timedelta(hours=2)  # a customer may arrive this early
NO_SHOW_GRACE = timedelta(minutes=15)  # wait this long before calling it a no-show


def quarter_floor(moment):
    """The quarter hour on or before moment: 9:20 -> 9:15."""
    return moment.replace(minute=moment.minute - moment.minute % slots.STEP, second=0, microsecond=0)


def check_in(s, shop, appointment, now, version=None, mileage=None, by=None, fuel=""):
    """The vehicle is here. Allowed from 2 hours before the start until the booking ends.
    It opens the job (jobs.py) in the same transaction."""
    from .jobs import open_job
    if appointment.status != BOOKED:
        raise BookingError("Only a booked visit can be checked in.")
    if version is not None and version != appointment.version:
        raise Changed(CHANGED)
    if now < appointment.starts_at - CHECK_IN_EARLY:
        raise BookingError("It's too early to check in: this booking is more than 2 hours away.")
    if now >= appointment.ends_at:
        raise BookingError("This booking's time is over. Book a new time instead.")
    try:
        appointment.status, appointment.checked_in_at, appointment.updated_at = CHECKED_IN, now, now
        appointment.mileage_in = mileage
        s.flush()
        open_job(s, appointment, now, by, fuel)
        s.commit()
    except StaleDataError as error:
        s.rollback()
        raise Changed(CHANGED) from error
    return appointment


def mark_no_show(s, shop, appointment, now, version=None):
    """Nobody came. The rest of the booking's time is freed at once (from this quarter
    hour on), so a walk-in can have the bay; the past part stays as it was."""
    if appointment.status != BOOKED:
        raise BookingError("Only a booked visit can be a no-show.")
    if version is not None and version != appointment.version:
        raise Changed(CHANGED)
    if now < appointment.starts_at + NO_SHOW_GRACE:
        raise BookingError("Wait until 15 minutes after the start before marking a no-show.")
    try:
        s.execute(delete(BookedSlot).where(BookedSlot.appointment_id == appointment.id,
                                           BookedSlot.slot_start >= quarter_floor(now)))
        appointment.status, appointment.no_show_at, appointment.updated_at = NO_SHOW, now, now
        s.commit()
    except StaleDataError as error:
        s.rollback()
        raise Changed(CHANGED) from error
    return appointment


def undo_no_show(s, shop, appointment, now, version=None):
    """They came after all. Take back the cells that are still to come. If someone
    else was booked into that time meanwhile, the UNIQUE rule refuses, and nothing changes."""
    if appointment.status != NO_SHOW:
        raise BookingError("This booking isn't a no-show.")
    if version is not None and version != appointment.version:
        raise Changed(CHANGED)
    if now >= appointment.ends_at:
        raise BookingError("This booking's time is over. Book a new time instead.")
    try:
        have = set(s.scalars(select(BookedSlot.slot_start).where(BookedSlot.appointment_id == appointment.id,
                                                                  BookedSlot.resource_kind == "bay")))
        for moment in cells(max(appointment.starts_at, quarter_floor(now)), appointment.ends_at):
            if moment not in have:
                for kind, resource_id in (("bay", appointment.bay_id), ("mechanic", appointment.mechanic_id)):
                    s.add(BookedSlot(shop_id=shop.id, resource_kind=kind, resource_id=resource_id, slot_start=moment,
                                     appointment_id=appointment.id))
        appointment.status, appointment.no_show_at, appointment.updated_at = BOOKED, None, now
        s.commit()
    except StaleDataError as error:
        s.rollback()
        raise Changed(CHANGED) from error
    except (IntegrityError, OperationalError) as error:
        s.rollback()
        raise Taken("Someone else was booked into that time after the no-show, so it can't be undone. "
                    "Book a new time instead.") from error
    return appointment
