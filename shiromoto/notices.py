"""Notices: queue a message, then send it by email and push (the timer does the sending).

- queue() writes rows into the outbox (notifications). Each has a UNIQUE dedupe_key,
  so the same notice is never queued twice, even when two timers run together.
- send_pending() claims each row with a conditional UPDATE (pending -> sending)
  before sending it, so two timers never send the same email twice.
- Email goes through Gmail with an app password (MAIL_USERNAME, MAIL_PASSWORD).
  Without them, notices are only shown in the app and marked "skipped".
- Push goes to every phone that asked for it (push_subscriptions), signed with the
  shop's VAPID keys (VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY). A phone that has left
  (the push service answers 404 or 410) is forgotten.
- Links in notices are signed with SECRET_KEY (itsdangerous), so a guest's later
  emails can open their booking without the app ever storing their secret link.

Defaults like smtplib.SMTP_SSL and pywebpush.webpush are looked up when used,
so tests can replace them (tests never go online).
"""
import json
import smtplib
from datetime import time, timedelta
from email.message import EmailMessage

from flask import current_app, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from .dates import at, local_today, to_local
from .formatting import clock, day, peso
from .models import ADVISOR, EMAIL, FAILED, LIVE_STATUSES, OWNER, PENDING, PUSH, SENT, SKIPPED, Appointment, \
    Notification, PushSubscription, Shop, User

SENDING = "sending"
MAX_ATTEMPTS = 3
LINK_LIFETIME = 60 * 60 * 24 * 45  # a signed booking link works for 45 days
REMIND_AT = time(17)  # the day-before reminder goes out from 5 PM the day before


# ---------- Signed links ----------

def signer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="booking-link")


def booking_link(appointment):
    """A link that opens this booking for anyone who has it, for 45 days."""
    return url_for("bookings.show", ref=appointment.ref, s=signer().dumps(appointment.ref), _external=True)


def link_ref(signed):
    """The booking ref a signed link is for, or None if it's wrong or too old."""
    try:
        return signer().loads(signed or "", max_age=LINK_LIFETIME)
    except BadSignature:
        return None


# ---------- Settings ----------

def email_ready():
    config = current_app.config
    return bool(config.get("MAIL_USERNAME") and config.get("MAIL_PASSWORD"))


def push_ready():
    config = current_app.config
    return bool(config.get("VAPID_PUBLIC_KEY") and config.get("VAPID_PRIVATE_KEY"))


# ---------- Queueing ----------

def safely(s, action, *args):
    """Queue notices after an action has been saved. If queueing fails, the action
    still stands (it's already committed); the problem is only written to the log."""
    try:
        action(s, *args)
        s.commit()
    except Exception:  # noqa: BLE001
        s.rollback()
        current_app.logger.exception("Couldn't queue a notice")


def queue(s, shop, kind, subject, body, link, dedupe, now, appointment=None, user=None, email="",
          send_after=None):
    """Queue one notice by email (if there's an address) and by push. Duplicates are
    ignored. Doesn't commit: the caller's transaction decides whether it happens."""
    made = []
    for channel, to in ((EMAIL, email), (PUSH, "")):
        if channel == EMAIL and not to:
            continue
        row = Notification(shop_id=shop.id, appointment_id=appointment.id if appointment else None,
                           user_id=user.id if user else None, kind=kind, channel=channel, to=to,
                           subject=subject[:160], body=body, link=link[:400], dedupe_key=f"{dedupe}:{channel}"[:120],
                           status=SKIPPED if shop.is_demo else PENDING, send_after=send_after or now, created_at=now)
        try:
            with s.begin_nested():  # a savepoint: a duplicate undoes only this one row
                s.add(row)
        except IntegrityError:
            continue
        made.append(row)
    return made


def for_customer(s, shop, appointment, kind, subject, body, now, dedupe=None, send_after=None):
    customer = appointment.customer
    user = s.get(User, customer.user_id) if customer.user_id else None
    return queue(s, shop, kind, subject, body, booking_link(appointment), dedupe or f"{kind}:{appointment.id}", now,
                 appointment=appointment, user=user, email=customer.email, send_after=send_after)


def when(appointment, shop):
    local = to_local(appointment.starts_at, shop.timezone)
    return f"{day(local.date())} at {clock(local.time())}"


def booked(s, shop, appointment, now):
    services = " + ".join(line.name for line in appointment.lines)
    for_customer(s, shop, appointment, "confirmed", f"Booked: {services}, {when(appointment, shop)}",
                 f"Hi {appointment.customer.name.split()[0]}, your booking {appointment.ref} at {shop.name} is "
                 f"confirmed: {services} for {appointment.vehicle.plate} on {when(appointment, shop)}. "
                 + ("We'll check what you asked for and send you its price. " if appointment.price_open else "")
                 + "You can move or cancel it from the link.", now)
    if appointment.source == "online":
        to_staff(s, shop, "new_booking", f"New online booking: {appointment.vehicle.plate}",
                 f"{appointment.customer.name} booked {services} for {when(appointment, shop)}."
                 + (f" Needs a price: “{appointment.request_text}”" if appointment.price_open else ""),
                 url_for("bookings.show", ref=appointment.ref, _external=True), f"new:{appointment.id}", now)


def price_sent(s, shop, appointment, now):
    """The shop priced the Other Service: the customer decides from their link."""
    for_customer(s, shop, appointment, "price", f"Your price: {peso(appointment.quote_price)}",
                 f"Hi {appointment.customer.name.split()[0]}, we checked what you asked for on booking "
                 f"{appointment.ref} (“{appointment.request_text}”). The price is "
                 f"{peso(appointment.quote_price)}."
                 + (f" {appointment.quote_note}" if appointment.quote_note else "")
                 + " Please open the link to confirm it or decline it.", now,
                 dedupe=f"price:{appointment.id}:{appointment.version}")


def price_answered(s, shop, appointment, now):
    """Tell the desk what the customer decided."""
    answer = "accepted" if appointment.quote_status == "accepted" else "declined"
    to_staff(s, shop, "price_answer", f"Price {answer}: {appointment.vehicle.plate}",
             f"{appointment.customer.name} {answer} {peso(appointment.quote_price)} for booking {appointment.ref}."
             + (" The booking is cancelled." if appointment.status == "cancelled" else ""),
             url_for("bookings.show", ref=appointment.ref, _external=True), f"answer:{appointment.id}", now)


def moved(s, shop, appointment, now):
    for_customer(s, shop, appointment, "moved", f"Moved to {when(appointment, shop)}",
                 f"Your booking {appointment.ref} is now on {when(appointment, shop)}.", now,
                 dedupe=f"moved:{appointment.id}:{appointment.version}")


def cancelled(s, shop, appointment, now):
    for_customer(s, shop, appointment, "cancelled", f"Cancelled: booking {appointment.ref}",
                 f"Your booking {appointment.ref} on {when(appointment, shop)} is cancelled."
                 + (f" Reason: {appointment.cancel_reason}" if appointment.cancel_reason else ""), now)


def at_time(moment, shop):
    local = to_local(moment, shop.timezone)
    return f"{day(local.date())} at {clock(local.time())}"


def move_asked(s, shop, appointment, now):
    """A customer asked for a new time: the desk and the booking's mechanic decide."""
    subject = f"Move request: {appointment.vehicle.plate}"
    body = (f"{appointment.customer.name} asks to move booking {appointment.ref} from {when(appointment, shop)} "
            f"to {at_time(appointment.move_starts_at, shop)}."
            + (f" Their note: “{appointment.move_note}”" if appointment.move_note else ""))
    link = url_for("bookings.show", ref=appointment.ref, _external=True) + "#move-request"
    dedupe = f"move-ask:{appointment.id}:{appointment.version}"
    to_staff(s, shop, "move_request", subject, body, link, dedupe, now)
    if appointment.mechanic.role == "mechanic":
        queue(s, shop, "move_request", subject, body, link, f"{dedupe}:m", now, user=appointment.mechanic)


def move_approved(s, shop, appointment, now):
    for_customer(s, shop, appointment, "moved", f"Moved to {when(appointment, shop)}",
                 f"Good news: the shop approved your new time. Booking {appointment.ref} is now on "
                 f"{when(appointment, shop)}." + (f" {appointment.move_reply}" if appointment.move_reply else ""),
                 now, dedupe=f"moved:{appointment.id}:{appointment.version}")


def move_declined(s, shop, appointment, now):
    for_customer(s, shop, appointment, "move_declined", f"We couldn't move booking {appointment.ref}",
                 f"Sorry, the shop can't take {at_time(appointment.move_starts_at, shop)}. Your booking stays on "
                 f"{when(appointment, shop)}." + (f" {appointment.move_reply}" if appointment.move_reply else "")
                 + " You can ask for another time from the link.", now,
                 dedupe=f"move-no:{appointment.id}:{appointment.version}")


def move_expired(s, shop, appointment, now):
    for_customer(s, shop, appointment, "move_expired", f"Your move request for {appointment.ref} ran out",
                 f"The shop couldn't answer before the time you asked for was given back. Your booking stays on "
                 f"{when(appointment, shop)}. You can ask for another time from the link.", now,
                 dedupe=f"move-out:{appointment.id}:{appointment.version}")


def estimate_waiting(s, shop, job, now):
    waiting = [line for line in job.lines if line.status == "proposed"]
    if not waiting:
        return
    visit = job.appointment
    for_customer(s, shop, visit, "estimate", f"Please approve: {waiting[0].description}",
                 f"Our mechanic found something on {visit.vehicle.plate}. Please look at the estimate and approve "
                 "or decline each line. We only do what you approve.", now,
                 dedupe=f"estimate:{job.id}:{waiting[-1].id}")


def ready(s, shop, job, now):
    visit = job.appointment
    for_customer(s, shop, visit, "ready", f"{visit.vehicle.plate} is ready for pickup",
                 f"Good news: your {visit.vehicle.label} is ready at {shop.name}.", now,
                 dedupe=f"ready:{job.id}:{len(job.events)}")


def to_staff(s, shop, kind, subject, body, link, dedupe, now):
    """Push to the owner's and service advisors' phones (if they turned it on)."""
    for person in s.scalars(select(User).where(User.shop_id == shop.id, User.role.in_((OWNER, ADVISOR)),
                                               User.is_active.is_(True))):
        queue(s, shop, kind, subject, body, link, f"{dedupe}:u{person.id}", now, user=person)


# ---------- The timer's reminders ----------

def queue_day_before(s, shop, now):
    """Tomorrow's bookings get a reminder, sent from 5 PM today. A booking moved to
    another day gets a new reminder (the key has its start time in it)."""
    today = local_today(shop.timezone, now)
    tomorrow = today + timedelta(days=1)
    first = at(tomorrow, time(0), shop.timezone)
    count = 0
    for visit in s.scalars(select(Appointment).where(
            Appointment.shop_id == shop.id, Appointment.status.in_(LIVE_STATUSES),
            Appointment.starts_at >= first, Appointment.starts_at < first + timedelta(days=1))):
        count += len(for_customer(
            s, shop, visit, "day_before", f"Tomorrow: {visit.lines[0].name if visit.lines else 'your booking'}",
            f"See you tomorrow, {when(visit, shop)}, at {shop.name}{', ' + shop.address if shop.address else ''}. "
            "Need another time? Move it from the link.", now,
            dedupe=f"day_before:{visit.id}:{visit.starts_at:%Y%m%d%H%M}",
            send_after=at(today, REMIND_AT, shop.timezone)))
    return count


def queue_service_due(s, shop, now, days=7):
    """"Your PMS is due" a week before the date worked out from the last visit."""
    from . import jobs
    from .models import Service, Vehicle
    today = local_today(shop.timezone, now)
    lookup = {sv.id: sv for sv in s.scalars(select(Service).where(Service.shop_id == shop.id))}
    count = 0
    for vehicle in s.scalars(select(Vehicle).where(Vehicle.shop_id == shop.id)):
        visits = s.scalars(select(Appointment).where(Appointment.vehicle_id == vehicle.id,
                                                     Appointment.checked_in_at.is_not(None))
                           .order_by(Appointment.starts_at.desc())).all()
        if not visits:
            continue
        for name, _, _, due_day, _ in jobs.next_due(visits, lookup):
            if due_day and today <= due_day <= today + timedelta(days=days):
                count += len(for_customer(
                    s, shop, visits[0], "service_due", f"{name} is due for {vehicle.plate}",
                    f"Your {vehicle.label} is due for {name} by {day(due_day)}. Book a time online.", now,
                    dedupe=f"due:{vehicle.id}:{name}:{due_day}"))
    return count


# ---------- Sending ----------

def send_email(row):
    config = current_app.config
    message = EmailMessage()
    message["From"] = config.get("MAIL_FROM") or config["MAIL_USERNAME"]
    message["To"] = row.to
    message["Subject"] = row.subject
    message.set_content(f"{row.body}\n\n{row.link}\n" if row.link else row.body)
    with smtplib.SMTP_SSL(config.get("MAIL_HOST", "smtp.gmail.com"), 465, timeout=15) as server:
        server.login(config["MAIL_USERNAME"], config["MAIL_PASSWORD"])
        server.send_message(message)


def subscriptions_for(s, row):
    conditions = []
    if row.appointment_id:
        conditions.append(PushSubscription.appointment_id == row.appointment_id)
    if row.user_id:
        conditions.append(PushSubscription.user_id == row.user_id)
    if not conditions:
        return []
    return s.scalars(select(PushSubscription).where(or_(*conditions))).all()


def send_push(s, row):
    """Push to every phone for this notice. Returns how many got it."""
    import pywebpush
    config = current_app.config
    payload = json.dumps({"title": row.subject, "body": row.body[:240], "url": row.link})
    delivered = 0
    for subscription in subscriptions_for(s, row):
        try:
            pywebpush.webpush(
                subscription_info={"endpoint": subscription.endpoint,
                                   "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}},
                data=payload, vapid_private_key=config["VAPID_PRIVATE_KEY"],
                vapid_claims={"sub": config.get("VAPID_SUBJECT") or "mailto:hello@shiromoto.example"}, timeout=10)
            delivered += 1
        except pywebpush.WebPushException as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            if status in (404, 410):  # the phone unsubscribed or the browser was reset
                s.delete(subscription)
            else:
                raise
    return delivered


def send_pending(s, now, limit=50):
    """Send what's due. Returns (sent, failed, skipped)."""
    counts = {SENT: 0, FAILED: 0, SKIPPED: 0}
    rows = s.scalars(select(Notification.id).where(Notification.status == PENDING, Notification.send_after <= now)
                     .order_by(Notification.id).limit(limit)).all()
    for row_id in rows:
        claimed = s.execute(update(Notification).where(Notification.id == row_id, Notification.status == PENDING)
                            .values(status=SENDING)).rowcount
        s.commit()
        if claimed != 1:
            continue  # another timer took it
        row = s.get(Notification, row_id)
        s.refresh(row)
        ready_to = email_ready() if row.channel == EMAIL else push_ready()
        if not ready_to or (row.channel == PUSH and not subscriptions_for(s, row)):
            row.status = SKIPPED
        else:
            try:
                if row.channel == EMAIL:
                    send_email(row)
                elif not send_push(s, row):
                    raise RuntimeError("No phone took the push.")
                row.status, row.sent_at = SENT, now
            except Exception as error:  # noqa: BLE001 - any failure is written down and tried again
                row.attempts += 1
                row.error = str(error)[:300]
                row.status = FAILED if row.attempts >= MAX_ATTEMPTS else PENDING
                row.send_after = now + timedelta(minutes=10 * row.attempts)
        counts[row.status if row.status in counts else FAILED] += row.status != PENDING
        s.commit()
    return counts[SENT], counts[FAILED], counts[SKIPPED]


def shops_to_tick(s):
    return s.scalars(select(Shop)).all()
