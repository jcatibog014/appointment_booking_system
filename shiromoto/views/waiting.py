"""The waitlist's pages: join from the Book page, see your place, leave; staff see the list."""
from datetime import timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from .. import auth, booking, db, forms, shops, slots, waitlist
from ..auth import roles_required
from ..dates import parse_day, utcnow
from ..models import ADVISOR, LEFT, OFFERED, OWNER, VEHICLE_KINDS, WAITING, WaitlistEntry
from .book import chosen_services, ids_from, other_text

bp = Blueprint("waiting", __name__)


@bp.route("/book/waitlist", methods=["GET", "POST"])
def join():
    s = db.session()
    shop = shops.current_or_404()
    source = request.values
    vehicle = source.get("vehicle")
    if vehicle not in VEHICLE_KINDS:
        return redirect(url_for("book.index"))
    services = chosen_services(s, shop, vehicle, ids_from(source.getlist("service")))
    if not services:
        return redirect(url_for("book.index", vehicle=vehicle))
    other = other_text(source) if any(sv.is_other for sv in services) else ""
    first, last = slots.bookable_days(shop, utcnow())
    wanted = parse_day(source.get("day")) or first
    values = {"name": "", "phone": "", "email": "", "first_day": wanted, "last_day": min(wanted + timedelta(days=6),
                                                                                          last)}
    if g.user is not None and g.user.role == "customer":
        values.update(name=g.user.name, phone=g.user.phone, email=g.user.email)
    errors = {}
    if request.method == "POST":
        values["name"], errors["name"] = forms.text(request.form, "name", "your name", auth.NAME_MAX_LENGTH)
        values["phone"], errors["phone"] = forms.text(request.form, "phone", "a mobile number", 40)
        if not errors["phone"]:
            errors["phone"] = booking.phone_problem(values["phone"])
        values["email"] = auth.clean_email(request.form.get("email", ""))
        errors["email"] = auth.email_problem(values["email"]) if values["email"] else None
        values["first_day"], errors["first_day"] = forms.day(request.form, "first_day")
        values["last_day"], errors["last_day"] = forms.day(request.form, "last_day")
        errors = {k: v for k, v in errors.items() if v}
        if not errors:
            try:
                entry = waitlist.join(s, shop, values["name"], values["phone"], values["email"], vehicle, services,
                                      values["first_day"], values["last_day"], utcnow(),
                                      g.user if g.user is not None and g.user.role == "customer" else None,
                                      request_text=other)
            except booking.BookingError as error:
                errors["last_day"] = str(error)
            else:
                flash("You're on the waitlist. When a time opens up, we keep it for you and send you a link.")
                return redirect(url_for("waiting.show", token=entry.token))
    return render_template("waitlist_join.html", vehicle=vehicle, services=services, values=values, errors=errors,
                           other=other,
                           first=first, last=last, public_page=True), 400 if errors else 200


def entry_or_404(token):
    entry = db.session().scalar(select(WaitlistEntry).where(WaitlistEntry.token == token,
                                                            WaitlistEntry.shop_id == shops.current_or_404().id))
    if entry is None:
        abort(404, "We couldn't find that waitlist place.")
    return entry


@bp.get("/waitlist/<token>")
def show(token):
    entry = entry_or_404(token)
    s = db.session()
    ahead = s.scalar(select(WaitlistEntry.id).where(
        WaitlistEntry.shop_id == entry.shop_id, WaitlistEntry.status == WAITING,
        WaitlistEntry.created_at < entry.created_at).limit(1)) is not None
    return render_template("waitlist.html", entry=entry, services=waitlist.services_of(s, entry), ahead=ahead,
                           now=utcnow(), offer_seconds=waitlist.OFFER_HOLD_MINUTES * 60, public_page=True)


@bp.post("/waitlist/<token>/leave")
def leave(token):
    entry = entry_or_404(token)
    s = db.session()
    if entry.status in (WAITING, OFFERED):
        waitlist.expire_offer_now(s, entry)
        entry.status = LEFT
        s.commit()
        flash("You've left the waitlist.")
    return redirect(url_for("waiting.show", token=entry.token))


@bp.route("/desk/waitlist", methods=["GET", "POST"])
@roles_required(OWNER, ADVISOR)
def desk():
    s = db.session()
    shop = shops.current()
    if request.method == "POST":
        made = waitlist.offer_free_times(s, shop, utcnow())
        flash(f"Offered {made} freed time{'s' * (made != 1)}." if made else "No free time fits anyone waiting yet.")
        return redirect(url_for("waiting.desk"))
    rows = s.scalars(select(WaitlistEntry).where(WaitlistEntry.shop_id == shop.id)
                     .order_by(WaitlistEntry.status != WAITING, WaitlistEntry.created_at.desc()).limit(100)).all()
    return render_template("desk/waitlist.html", rows=rows, now=utcnow(),
                           offer_seconds=waitlist.OFFER_HOLD_MINUTES * 60,
                           services={row.id: waitlist.services_of(s, row) for row in rows})
