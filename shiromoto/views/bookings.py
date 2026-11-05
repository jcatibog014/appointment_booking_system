"""One booking's own page: /b/<ref>. See it, add it to a calendar, move it or cancel it.

Who can open it:
- anyone with its manage link (/b/<ref>?t=<secret>): guests have no login,
  so the link is their key. Only a hash of the secret is stored;
- the browser that made it or opened the link before (the secret is kept in
  the signed session cookie, so the link isn't needed again);
- the customer it belongs to, when logged in;
- the shop's staff.
Anyone else gets 404, the same as a booking that doesn't exist.
"""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import select

from .. import booking, calendar_file, db, notices, shops
from ..auth import safe_next
from ..dates import parse_day, to_local, utcnow
from ..models import BOOKED, Appointment

bp = Blueprint("bookings", __name__, url_prefix="/b")

REMEMBERED = 12  # bookings one browser remembers


def remember(ref, token):
    saved = dict(session.get("bookings", {}))
    saved.pop(ref, None)
    saved[ref] = token
    session["bookings"] = dict(list(saved.items())[-REMEMBERED:])


def find(ref):
    shop = shops.current_or_404()
    return db.session().scalar(select(Appointment).where(Appointment.ref == (ref or "").upper(),
                                                         Appointment.shop_id == shop.id))


def open_booking(ref):
    """The booking, if this visitor may open it; else 404."""
    appointment = find(ref)
    if appointment is None:
        abort(404, "We couldn't find that booking.")
    token = request.values.get("t")
    if token and booking.token_matches(appointment, token):
        remember(appointment.ref, token)
        return appointment
    signed = request.values.get("s")  # a link from a notice, signed by the app (notices.booking_link)
    if signed and notices.link_ref(signed) == appointment.ref:
        remember(appointment.ref, "s:" + signed)
        return appointment
    if g.user is not None and (g.user.is_staff or appointment.customer.user_id == g.user.id):
        return appointment
    remembered = session.get("bookings", {}).get(appointment.ref) or ""
    if remembered.startswith("s:") and notices.link_ref(remembered[2:]) == appointment.ref:
        return appointment
    if booking.token_matches(appointment, remembered):
        return appointment
    abort(404, "We couldn't find that booking.")


def manage_link(appointment):
    """The full link to give a guest (only while we know the secret: right after booking)."""
    token = session.get("bookings", {}).get(appointment.ref)
    if not booking.token_matches(appointment, token):
        return None
    return url_for("bookings.show", ref=appointment.ref, t=token, _external=True)


def offer_freed_time(s):
    """A time just came free: offer it to the waitlist straight away (the timer would, later)."""
    from .. import waitlist
    try:
        waitlist.offer_free_times(s, shops.current(), utcnow(), limit=5)
    except Exception:  # noqa: BLE001 - the action is done; the timer tries again
        s.rollback()


def by_staff():
    return g.user is not None and g.user.is_staff


@bp.get("/<ref>")
def show(ref):
    appointment = open_booking(ref)
    shop = shops.current()
    now = utcnow()
    if request.args.get("t") or request.args.get("s"):
        return redirect(url_for("bookings.show", ref=appointment.ref))  # keep the secret out of the address bar
    can_change = appointment.status == BOOKED and appointment.ends_at > now and (
        by_staff() or booking.customer_can_change(shop, appointment, now))
    mine = g.user is not None and appointment.customer.user_id == g.user.id
    return render_template(
        "booking.html", appointment=appointment, link=manage_link(appointment), can_change=can_change,
        starts=to_local(appointment.starts_at, shop.timezone), ends=to_local(appointment.ends_at, shop.timezone),
        staff_view=by_staff(), public_page=not by_staff(), mine=mine, now=now,
        decide_url=lambda line: url_for("bookings.decide", ref=appointment.ref, line_id=line.id),
        can_save=g.user is not None and g.user.role == "customer" and appointment.customer.user_id is None)


@bp.get("/<ref>/calendar.ics")
def calendar(ref):
    appointment = open_booking(ref)
    link = url_for("bookings.show", ref=appointment.ref, _external=True)
    body = calendar_file.booking_ics(appointment, shops.current(), utcnow(), link)
    return body, 200, {"Content-Type": "text/calendar; charset=utf-8",
                       "Content-Disposition": f'attachment; filename="{appointment.ref}.ics"'}


@bp.post("/<ref>/cancel")
def cancel(ref):
    appointment = open_booking(ref)
    s = db.session()
    try:
        booking.cancel(s, shops.current(), appointment, request.form.get("reason", ""), utcnow(),
                       version=request.form.get("version", type=int), by_staff=by_staff())
    except booking.BookingError as error:
        s.rollback()
        flash(str(error), "error")
    else:
        flash(f"Booking {appointment.ref} is cancelled. The time is free for someone else now.")
        notices.safely(s, notices.cancelled, shops.current(), appointment, utcnow())
        offer_freed_time(s)
    return redirect(url_for("bookings.show", ref=appointment.ref))


@bp.get("/<ref>/move")
def choose_new_time(ref):
    appointment = open_booking(ref)
    return redirect(url_for("book.index", move=appointment.ref))


@bp.route("/<ref>/move/confirm", methods=["GET", "POST"])
def move(ref):
    appointment = open_booking(ref)
    shop = shops.current()
    day = parse_day(request.values.get("day"))
    start = request.values.get("start", type=int)
    if day is None or start is None or not 0 <= start < 24 * 60:
        abort(400)
    if request.method == "POST":
        s = db.session()
        try:
            booking.move(s, shop, appointment, day, start, utcnow(), version=request.form.get("version", type=int),
                         by_staff=by_staff())
        except booking.Taken as error:
            flash(str(error), "error")
            return redirect(url_for("book.index", move=appointment.ref, day=day.isoformat()))
        except booking.BookingError as error:
            s.rollback()
            flash(str(error), "error")
            return redirect(url_for("bookings.show", ref=appointment.ref))
        flash(f"Moved. Booking {appointment.ref} is now on the new time.")
        notices.safely(s, notices.moved, shop, appointment, utcnow())
        offer_freed_time(s)
        return redirect(url_for("bookings.show", ref=appointment.ref))
    return render_template("booking_move.html", appointment=appointment, day=day, start=start,
                           old=to_local(appointment.starts_at, shop.timezone), staff_view=by_staff(),
                           public_page=not by_staff())


@bp.post("/<ref>/save")
def save(ref):
    """A logged-in customer keeps a booking they made as a guest: it moves to their My Garage.
    Only someone who can open the booking (so has its link) can do this."""
    appointment = open_booking(ref)
    if g.user is None or g.user.role != "customer":
        abort(403)
    s = db.session()
    mine = booking.customer_for_user(s, shops.current(), g.user)
    if appointment.customer.user_id not in (None, g.user.id):
        abort(403, "This booking belongs to another account.")
    old = appointment.customer
    appointment.customer_id = mine.id
    for vehicle in old.vehicles:
        if vehicle.id == appointment.vehicle_id:
            vehicle.customer_id = mine.id
    s.commit()
    flash(f"Booking {appointment.ref} is saved to My Garage.")
    return redirect(url_for("garage.index"))


# ---------- The front desk's buttons ----------

def desk_action(ref, act, done_message, after=None):
    """Run one front-desk action on a booking, then go back where the button was."""
    appointment = open_booking(ref)
    if g.user is None or g.user.role not in ("owner", "advisor"):
        abort(403, "Only the front desk can do this.")
    s = db.session()
    try:
        act(s, appointment)
    except booking.BookingError as error:
        s.rollback()
        flash(str(error), "error")
    else:
        flash(done_message(appointment))
        if after:
            after(s)
    return redirect(safe_back(url_for("bookings.show", ref=appointment.ref)))


def safe_back(default):
    return safe_next(request.form.get("next"), default)


@bp.post("/<ref>/check-in")
def check_in(ref):
    mileage = request.form.get("mileage", "").replace(",", "").strip()
    km = int(mileage) if mileage.isdigit() and int(mileage) < 2_000_000 else None
    return desk_action(ref, lambda s, a: booking.check_in(s, shops.current(), a, utcnow(),
                                                          request.form.get("version", type=int), km, by=g.user,
                                                          fuel=request.form.get("fuel", "")),
                       lambda a: f"{a.customer.name} is checked in ({a.vehicle.plate}).")


@bp.post("/<ref>/no-show")
def no_show(ref):
    return desk_action(ref, lambda s, a: booking.mark_no_show(s, shops.current(), a, utcnow(),
                                                              request.form.get("version", type=int)),
                       lambda a: f"{a.ref} is marked as a no-show. The rest of its time is free for walk-ins.",
                       after=offer_freed_time)


@bp.post("/<ref>/undo-no-show")
def undo_no_show(ref):
    return desk_action(ref, lambda s, a: booking.undo_no_show(s, shops.current(), a, utcnow(),
                                                              request.form.get("version", type=int)),
                       lambda a: f"{a.customer.name} came after all: {a.ref} is booked again.")


@bp.post("/<ref>/estimate/<int:line_id>")
def decide(ref, line_id):
    """The customer approves or declines one line of their job's estimate."""
    from .. import jobs
    appointment = open_booking(ref)
    job = appointment.job
    if job is None:
        abort(404)
    s = db.session()
    approve = request.form.get("decision") == "approve"
    try:
        line = jobs.decide(s, job, line_id, approve, utcnow(), "the customer")
    except booking.BookingError as error:
        s.rollback()
        flash(str(error), "error")
    else:
        flash(f"{'Approved' if approve else 'Declined'}: {line.description}. Thank you!")
    return redirect(url_for("bookings.show", ref=appointment.ref) + "#estimate")


# ---------- The Other Service's price (quotes.py) ----------

@bp.post("/<ref>/price")
def send_price(ref):
    """Staff send or change the price of what the customer asked for."""
    from .. import forms, quotes
    appointment = open_booking(ref)
    if not by_staff():
        abort(404)
    s = db.session()
    price, problem = forms.pesos(request.form, "price")
    try:
        if problem:
            raise booking.BookingError(problem)
        quotes.send_price(s, shops.current(), appointment, price, request.form.get("note", ""), utcnow(), g.user,
                          version=request.form.get("version", type=int))
    except booking.BookingError as error:
        s.rollback()
        flash(str(error), "error")
    else:
        notices.safely(s, notices.price_sent, shops.current(), appointment, utcnow())
        flash(f"Price sent to {appointment.customer.name}. They confirm or decline it from their link.")
    return redirect(url_for("bookings.show", ref=appointment.ref) + "#other-service")


@bp.post("/<ref>/price/answer")
def answer_price(ref):
    """The customer confirms or declines the price they were shown."""
    from .. import quotes
    appointment = open_booking(ref)
    s = db.session()
    shown = request.form.get("price", type=int)
    accept = request.form.get("answer") == "accept"
    try:
        if accept:
            quotes.accept(s, shops.current(), appointment, shown, utcnow())
        else:
            quotes.decline(s, shops.current(), appointment, shown, utcnow())
    except booking.BookingError as error:
        s.rollback()
        flash(str(error), "error")
    else:
        notices.safely(s, notices.price_answered, shops.current(), appointment, utcnow())
        if accept:
            flash("Thank you! The price is confirmed, and your booking stays as it is.")
        elif appointment.status == "cancelled":
            flash(f"Declined. Booking {appointment.ref} is cancelled, and nothing is charged.")
            offer_freed_time(s)
        else:
            flash("Declined. We took the other service off your booking. The rest stays as it is.")
            offer_freed_time(s)
    return redirect(url_for("bookings.show", ref=appointment.ref) + "#other-service")
