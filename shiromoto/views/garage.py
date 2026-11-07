"""Customer accounts: sign up, and My Garage (your vehicles and bookings).

An account is optional: anyone can book as a guest with their manage link.
With an account, bookings and vehicles are kept together, the booking form
fills itself in, and a guest booking can be saved to the account by opening
its link while logged in.
"""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import auth, booking, db, forms, jobs, shops
from ..auth import roles_required
from ..dates import utcnow
from ..models import BOOKED, CUSTOMER, RELEASED, VEHICLE_KINDS, Appointment, Service, Vehicle

bp = Blueprint("garage", __name__)


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if g.user is not None:
        return redirect(auth.home_for(g.user))
    s = db.session()
    shop = shops.current_or_404()
    errors, values = {}, {}
    if request.method == "POST":
        if request.form.get("website"):  # a field only robots fill in
            return redirect(url_for("auth.login"))
        values["name"], errors["name"] = forms.text(request.form, "name", "your name", auth.NAME_MAX_LENGTH)
        values["email"] = auth.clean_email(request.form.get("email", ""))
        errors["email"] = auth.email_problem(values["email"])
        values["phone"], errors["phone"] = forms.text(request.form, "phone", "a mobile number", 40)
        if not errors["phone"]:
            errors["phone"] = booking.phone_problem(values["phone"])
        errors["password"] = auth.password_problem(request.form.get("password", ""),
                                                   request.form.get("confirm_password", ""))
        errors = {k: v for k, v in errors.items() if v}
        if not errors:
            try:
                user = auth.create_user(s, shop, values["email"], values["name"], request.form["password"], CUSTOMER,
                                        utcnow())
                user.phone = values["phone"]
                booking.customer_for_user(s, shop, user)
                s.commit()
            except IntegrityError:
                s.rollback()
                errors["email"] = "This email already has an account. Log in instead."
            else:
                auth.log_in(user)
                flash(f"Welcome, {user.first_name}! Your account is ready. Add your vehicle, then book a time.")
                return redirect(url_for("garage.index"))
    return render_template("signup.html", errors=errors, values=values), 400 if errors else 200


@bp.get("/garage")
@roles_required(CUSTOMER)
def index():
    s = db.session()
    shop = shops.current()
    customer = booking.customer_for_user(s, shop, g.user)
    s.commit()
    now = utcnow()
    bookings = s.scalars(select(Appointment).where(Appointment.customer_id == customer.id)
                         .order_by(Appointment.starts_at.desc())).all()
    in_shop = [b for b in bookings if b.job is not None and b.job.status != RELEASED]
    upcoming = sorted((b for b in bookings if b.status == BOOKED and b.ends_at > now), key=lambda b: b.starts_at)
    past = [b for b in bookings if b not in upcoming and b not in in_shop]
    lookup = {sv.id: sv for sv in s.scalars(select(Service).where(Service.shop_id == shop.id))}
    due = {v.id: jobs.next_due([b for b in bookings if b.vehicle_id == v.id and b.checked_in_at], lookup)
           for v in customer.vehicles}
    # The dashboard's top: what needs the customer's answer, and the next service due.
    answers = [("estimate", b) for b in in_shop if any(line.status == "proposed" for line in b.job.lines)]
    answers += [("price", b) for b in upcoming if b.quote_status == "priced"]
    coming_due = sorted(((due_day, name, vehicle) for vehicle in customer.vehicles
                         for name, _, _, due_day, _ in due[vehicle.id] if due_day), key=lambda item: item[0])
    return render_template("garage.html", customer=customer, upcoming=upcoming, past=past[:12], in_shop=in_shop,
                           due=due, errors={}, public_page=True, now=now, answers=answers,
                           next_due=coming_due[0] if coming_due else None)


def read_vehicle(form):
    values, errors = {}, {}
    values["kind"] = form.get("kind")
    if values["kind"] not in VEHICLE_KINDS:
        errors["kind"] = "Choose car or motorcycle."
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


@bp.post("/garage/vehicles")
@roles_required(CUSTOMER)
def add_vehicle():
    s = db.session()
    shop = shops.current()
    customer = booking.customer_for_user(s, shop, g.user)
    values, errors = read_vehicle(request.form)
    if not errors:
        key = booking.plate_key(values["plate"])
        if s.scalar(select(Vehicle).where(Vehicle.shop_id == shop.id, Vehicle.plate_key == key)):
            errors["plate"] = "This plate is already with the shop. If it's yours, call us and we'll move it to you."
    if errors:
        flash(next(iter(errors.values())), "error")
        return redirect(url_for("garage.index"))
    s.add(Vehicle(shop_id=shop.id, customer_id=customer.id, kind=values["kind"], plate=values["plate"].upper(),
                  plate_key=booking.plate_key(values["plate"]), make=values["make"], model=values["model"],
                  year=values["year"], created_at=utcnow()))
    s.commit()
    flash(f"{values['plate'].upper()} is added to My Garage.")
    return redirect(url_for("garage.index"))

