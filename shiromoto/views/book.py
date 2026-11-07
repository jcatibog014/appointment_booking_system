"""Booking a time: vehicle -> services -> day -> time -> your details -> booked.

The first steps are GET, with every choice in the address, so they can be
bookmarked and shared: /book?vehicle=car&service=3&service=5&day=2026-11-12.
Pressing a time POSTs to /book/hold, which keeps it for a few minutes
(booking.hold) while the person fills in their details; the details form
then makes the booking (booking.confirm).

Staff use the same page from the menu ("Free Times"): they also see which
mechanics and bays each time would use, and book for a caller.

With ?move=<ref>, the page moves an existing booking instead: the times
lead to /b/<ref>/move, and the booking's own time counts as free.

The Other Service (quotes.py) carries what the customer wrote in ?other=...,
through the days and times, into the hold, and onto the booking.
"""
import secrets
from datetime import timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import auth, booking, db, forms, notices, shops, slots, waitlist
from ..dates import parse_day, to_local, utcnow
from ..models import ANY, ONLINE, PHONE, SOURCE_LABELS, VEHICLE_KINDS, Appointment, Hold, Service
from .bookings import open_booking, remember
from .public import service_menu

bp = Blueprint("book", __name__, url_prefix="/book")

STRIP_DAYS = 7


def chosen_services(s, shop, vehicle, ids):
    """These services, if they exist, are offered and fit the vehicle, in menu order."""
    if not ids:
        return []
    return s.scalars(select(Service).where(
        Service.shop_id == shop.id, Service.id.in_(ids), Service.is_active.is_(True),
        Service.vehicle_kind.in_((vehicle, ANY))).options(selectinload(Service.bay_type), selectinload(Service.skill))
        .order_by(Service.position, Service.name)).all()


def ids_from(values):
    return {int(v) for v in values if str(v).isdigit()}


def address(vehicle, services, other="", **extra):
    """The page's address with these choices (url_for repeats a list as service=1&service=2)."""
    if other and any(sv.is_other for sv in services):
        extra["other"] = other
    return url_for("book.index", vehicle=vehicle, service=[sv.id for sv in services], **extra)


def other_text(values):
    return " ".join((values.get("other") or "").split())[:booking.REQUEST_MAX]


def other_service(s, shop, vehicle):
    """This vehicle's Other Service, if the shop offers one."""
    return s.scalar(select(Service).where(Service.shop_id == shop.id, Service.is_other.is_(True),
                                          Service.is_active.is_(True), Service.vehicle_kind.in_((vehicle, ANY)))
                    .order_by(Service.position).limit(1))


@bp.get("")
def index():
    s = db.session()
    shop = shops.current_or_404()
    staff_view = g.user is not None and g.user.is_staff
    moving = None
    if request.args.get("move"):
        moving = open_booking(request.args["move"])
        if moving.move_waiting(utcnow()):
            flash("A new time for this booking is already waiting for an answer. Withdraw it first."
                  if staff_view else "You already asked for a new time. Wait for the shop's answer, or withdraw it "
                  "first.", "error")
            return redirect(url_for("bookings.show", ref=moving.ref))
        vehicle = moving.vehicle.kind
        ids = {line.service_id for line in moving.lines if line.service_id}
    else:
        vehicle = request.args.get("vehicle")
        ids = ids_from(request.args.getlist("service"))
    if vehicle not in VEHICLE_KINDS:
        return render_template("book.html", step=1, staff_view=staff_view, public_page=not staff_view)
    other = moving.request_text if moving else other_text(request.args)
    other_choice = other_service(s, shop, vehicle)
    if other and other_choice is not None and not moving:
        ids.add(other_choice.id)  # writing what you need is enough to choose the Other Service
    chosen = chosen_services(s, shop, vehicle, ids)
    menu = service_menu(s, shop)[vehicle]
    wants_other = any(sv.is_other for sv in chosen)
    context = {"step": 2, "vehicle": vehicle, "menu": menu, "chosen": chosen, "staff_view": staff_view,
               "public_page": not staff_view, "chosen_ids": {sv.id for sv in chosen}, "moving": moving,
               "other": other, "other_error": None}
    if not moving and wants_other and not other:
        context["other_error"] = booking.OTHER_NEEDS_TEXT
        return render_template("book.html", **context), 400
    if not moving and (not chosen or request.args.get("edit")):
        return render_template("book.html", **context)
    need, problem = slots.need_for(chosen, vehicle)
    extra = {"move": moving.ref} if moving else {}
    if not moving:
        extra["other"] = other if wants_other else None
    context.update(step=3, need=need, problem=problem, price_from=sum(sv.price_from for sv in chosen if not sv.is_other),
                   wants_other=wants_other, change_url=address(vehicle, chosen, other, edit=1))
    if moving and len(chosen) != len(moving.lines):
        problem = "A service on this booking isn't offered any more. Please call the shop to move it."
        context["problem"] = problem
    if problem:
        return render_template("book.html", **context)

    now = utcnow()
    # A new time is held while the other side decides, next to the booking's own time,
    # so the booking's own time counts as busy here (moves.py).
    ignore = None

    def busy_for(day):
        return booking.busy_map(s, shop, day, now, ignore)

    first, last = slots.bookable_days(shop, now)
    strip_start = parse_day(request.args.get("from")) or first
    strip_start = min(max(strip_start, first), last)
    notice = 0 if staff_view else None  # staff book walk-ins from right now
    days = slots.week_of_choices(s, shop, need, strip_start, now, STRIP_DAYS, busy_for, notice)
    wanted = parse_day(request.args.get("day"))
    picked = next((c for c in days if c.day == wanted), None)
    if picked is None and wanted and first <= wanted <= last:
        picked = slots.day_choice(s, shop, need, wanted, now, busy_for(wanted), notice)
    if picked is None:
        picked = next((c for c in days if c.count), days[0])
    earlier = strip_start - timedelta(days=STRIP_DAYS)
    later = strip_start + timedelta(days=STRIP_DAYS)
    context.update(
        days=days, picked=picked,
        morning=[slot for slot in picked.slots if slot.start < 12 * 60],
        afternoon=[slot for slot in picked.slots if slot.start >= 12 * 60],
        earlier_url=address(vehicle, chosen, **{"from": max(earlier, first).isoformat()}, **extra)
        if strip_start > first else None,
        later_url=address(vehicle, chosen, **{"from": later.isoformat()}, **extra) if later <= last else None,
        day_url=lambda day: address(vehicle, chosen, day=day.isoformat(), **{"from": strip_start.isoformat()},
                                    **extra),
    )
    return render_template("book.html", **context)


# ---------- Holding a time ----------

def release_previous_hold(s):
    """One browser holds one time: a new choice gives the last one back."""
    token = session.pop("hold", None)
    if token:
        old = s.scalar(select(Hold).where(Hold.token == token))
        if old is not None:
            booking.release(s, old)


@bp.post("/hold")
def hold():
    s = db.session()
    shop = shops.current_or_404()
    vehicle = request.form.get("vehicle")
    day = parse_day(request.form.get("day"))
    start = request.form.get("start", type=int)
    if vehicle not in VEHICLE_KINDS or day is None or start is None:
        abort(400)
    chosen = chosen_services(s, shop, vehicle, ids_from(request.form.getlist("service")))
    other = other_text(request.form)
    back = address(vehicle, chosen, other, day=day.isoformat())
    release_previous_hold(s)
    try:
        held = booking.hold(s, shop, chosen, vehicle, day, start, utcnow(), by=g.user, request_text=other)
    except booking.BookingError as error:
        flash(str(error), "error")
        return redirect(back)
    session["hold"] = held.token
    return redirect(url_for("book.details", token=held.token))


def find_hold(token):
    shop = shops.current_or_404()
    held = db.session().scalar(select(Hold).where(Hold.token == token, Hold.shop_id == shop.id))
    if held is None:
        return None
    held.services = chosen_services(db.session(), shop, held.vehicle_kind, ids_from(held.service_ids.split(",")))
    return held


def read_details(form, customer_vehicles, wants_other=False):
    """The details form -> (values, errors)."""
    values, errors = {}, {}
    values["name"], errors["name"] = forms.text(form, "name", "your name", auth.NAME_MAX_LENGTH)
    values["phone"], errors["phone"] = forms.text(form, "phone", "a mobile number", 40)
    if not errors["phone"]:
        errors["phone"] = booking.phone_problem(values["phone"])
    values["email"] = auth.clean_email(form.get("email", ""))
    errors["email"] = auth.email_problem(values["email"]) if values["email"] else None
    values["notes"], errors["notes"] = forms.text(form, "notes", "a note", 600, required=False)
    if wants_other:
        values["request"], errors["request"] = forms.text(form, "request", "what you need", booking.REQUEST_MAX)
    vehicle_id = form.get("vehicle_id", type=int)
    saved = next((v for v in customer_vehicles if v.id == vehicle_id), None)
    if saved:
        values.update(plate=saved.plate, make=saved.make, model=saved.model, year=saved.year)
    else:
        values["plate"], errors["plate"] = forms.text(form, "plate", "the plate number", 16)
        if not errors["plate"]:
            errors["plate"] = booking.plate_problem(values["plate"])
        values["make"], errors["make"] = forms.text(form, "make", "the make", 40, required=False)
        values["model"], errors["model"] = forms.text(form, "model", "the model", 40, required=False)
        year = (form.get("year") or "").strip()
        values["year"] = int(year) if year.isdigit() and 1950 <= int(year) <= 2100 else None
        if year and values["year"] is None:
            errors["year"] = "Write the year like 2019."
    return values, {k: v for k, v in errors.items() if v}


@bp.route("/details/<token>", methods=["GET", "POST"])
def details(token):
    s = db.session()
    shop = shops.current_or_404()
    if request.method == "POST":
        # A second press of Book (the hold is used up by the first): same key, same booking.
        done = s.scalar(select(Appointment).where(Appointment.idempotency_key == (request.form.get("key") or token),
                                                  Appointment.shop_id == shop.id))
        if done is not None:
            return redirect(url_for("bookings.show", ref=done.ref))
    held = find_hold(token)
    if held is None:
        flash(booking.GONE, "error")
        return redirect(url_for("book.index"))
    staff_view = g.user is not None and g.user.is_staff
    customer = (booking.customer_for_user(s, shop, g.user)
                if g.user is not None and g.user.role == "customer" else None)
    my_vehicles = [v for v in (customer.vehicles if customer else []) if v.kind == held.vehicle_kind]
    wants_other = any(sv.is_other for sv in held.services)
    errors, values = {}, {"request": held.request_text}
    if customer:
        values.update(name=customer.name, phone=customer.phone, email=customer.email)
    if request.method == "POST":
        values, errors = read_details(request.form, my_vehicles, wants_other)
        source = request.form.get("source") if staff_view else ONLINE
        if source not in SOURCE_LABELS:
            source = PHONE
        if not errors:
            try:
                appointment = booking.confirm(s, shop, held, values, request.form.get("key") or token, utcnow(),
                                              user=g.user, source=source)
            except booking.Gone as error:
                session.pop("hold", None)
                flash(str(error), "error")
                return redirect(address(held.vehicle_kind, held.services, held.request_text,
                                        day=to_local(held.starts_at, shop.timezone).date().isoformat()))
            except booking.BookingError as error:
                errors["plate"] = str(error)
            else:
                session.pop("hold", None)
                notices.safely(s, notices.booked, shop, appointment, utcnow())
                waitlist.booked_from_offer(s, held.token)
                if getattr(appointment, "token", None):
                    remember(appointment.ref, appointment.token)
                flash(f"Booked! Your booking number is {appointment.ref}.")
                return redirect(url_for("bookings.show", ref=appointment.ref))
    if customer and not values.get("name"):
        values.update(name=customer.name, phone=customer.phone, email=customer.email)
    return render_template("book_details.html", held=held, values=values, errors=errors, staff_view=staff_view,
                           wants_other=wants_other,
                           price_from=sum(sv.price_from for sv in held.services if not sv.is_other),
                           public_page=not staff_view, my_vehicles=my_vehicles, key=secrets.token_urlsafe(16),
                           day=to_local(held.starts_at, shop.timezone).date(),
                           start=to_local(held.starts_at, shop.timezone).time(),
                           ready=to_local(held.ends_at, shop.timezone).time(),
                           expires_in=max(0, int((held.expires_at - utcnow()).total_seconds())),
                           sources=[(PHONE, "Phone"), ("walk_in", "Walk-in"), (ONLINE, "Online")]), \
        400 if errors else 200


@bp.post("/details/<token>/release")
def release(token):
    held = find_hold(token)
    if held is None:
        return redirect(url_for("book.index"))
    back = address(held.vehicle_kind, held.services, held.request_text,
                   day=to_local(held.starts_at, shops.current().timezone).date().isoformat())
    booking.release(db.session(), held)
    session.pop("hold", None)
    flash("The time is given back. Choose another one.")
    return redirect(back)

