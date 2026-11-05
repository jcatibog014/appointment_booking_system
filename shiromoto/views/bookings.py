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

from .. import booking, calendar_file, db, shops
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
    if g.user is not None and (g.user.is_staff or appointment.customer.user_id == g.user.id):
        return appointment
    if booking.token_matches(appointment, session.get("bookings", {}).get(appointment.ref)):
        return appointment
    abort(404, "We couldn't find that booking.")


def manage_link(appointment):
    """The full link to give a guest (only while we know the secret: right after booking)."""
    token = session.get("bookings", {}).get(appointment.ref)
    if not booking.token_matches(appointment, token):
        return None
    return url_for("bookings.show", ref=appointment.ref, t=token, _external=True)


def by_staff():
    return g.user is not None and g.user.is_staff


@bp.get("/<ref>")
def show(ref):
    appointment = open_booking(ref)
    shop = shops.current()
    now = utcnow()
    if request.args.get("t"):
        return redirect(url_for("bookings.show", ref=appointment.ref))  # keep the secret out of the address bar
    can_change = appointment.status == BOOKED and appointment.ends_at > now and (
        by_staff() or booking.customer_can_change(shop, appointment, now))
    mine = g.user is not None and appointment.customer.user_id == g.user.id
    return render_template(
        "booking.html", appointment=appointment, link=manage_link(appointment), can_change=can_change,
        starts=to_local(appointment.starts_at, shop.timezone), ends=to_local(appointment.ends_at, shop.timezone),
        staff_view=by_staff(), public_page=not by_staff(), mine=mine,
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
