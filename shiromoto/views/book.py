"""Finding a free time: vehicle -> services -> day -> time.

Everything is in the address (GET), so every step can be bookmarked, shared
and opened again: /book?vehicle=car&service=3&service=5&day=2026-11-12.
Stage 2 shows the free times; stage 3 turns a time into a booking.

Staff use the same page from the menu ("Free Times") and also see which
mechanics and bays each time would use.
"""
from datetime import timedelta

from flask import Blueprint, g, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import db, shops, slots
from ..dates import parse_day, utcnow
from ..models import ANY, VEHICLE_KINDS, Service
from .public import service_menu

bp = Blueprint("book", __name__, url_prefix="/book")

STRIP_DAYS = 7


def chosen_services(s, shop, vehicle):
    """The services in the address that exist, are offered and fit the vehicle, in menu order."""
    ids = {int(v) for v in request.args.getlist("service") if v.isdigit()}
    if not ids:
        return []
    return s.scalars(select(Service).where(
        Service.shop_id == shop.id, Service.id.in_(ids), Service.is_active.is_(True),
        Service.vehicle_kind.in_((vehicle, ANY))).options(selectinload(Service.bay_type), selectinload(Service.skill))
        .order_by(Service.position, Service.name)).all()


def address(vehicle, services, **extra):
    """The page's address with these choices (url_for repeats a list as service=1&service=2)."""
    return url_for("book.index", vehicle=vehicle, service=[sv.id for sv in services], **extra)


@bp.get("")
def index():
    s = db.session()
    shop = shops.current_or_404()
    staff_view = g.user is not None and g.user.is_staff
    vehicle = request.args.get("vehicle")
    if vehicle not in VEHICLE_KINDS:
        return render_template("book.html", step=1, staff_view=staff_view, public_page=not staff_view)
    chosen = chosen_services(s, shop, vehicle)
    menu = service_menu(s, shop)[vehicle]
    context = {"step": 2, "vehicle": vehicle, "menu": menu, "chosen": chosen, "staff_view": staff_view,
               "public_page": not staff_view, "chosen_ids": {sv.id for sv in chosen}}
    if not chosen or request.args.get("edit"):
        return render_template("book.html", **context)
    need, problem = slots.need_for(chosen, vehicle)
    context.update(step=3, need=need, problem=problem, price_from=sum(sv.price_from for sv in chosen),
                   change_url=address(vehicle, chosen, edit=1))
    if problem:
        return render_template("book.html", **context)

    now = utcnow()
    first, last = slots.bookable_days(shop, now)
    strip_start = parse_day(request.args.get("from")) or first
    strip_start = min(max(strip_start, first), last)
    days = slots.week_of_choices(s, shop, need, strip_start, now, STRIP_DAYS)
    wanted = parse_day(request.args.get("day"))
    picked = next((c for c in days if c.day == wanted), None)
    if picked is None and wanted and first <= wanted <= last:
        picked = slots.day_choice(s, shop, need, wanted, now)
    if picked is None:
        picked = next((c for c in days if c.count), days[0])
    earlier = strip_start - timedelta(days=STRIP_DAYS)
    later = strip_start + timedelta(days=STRIP_DAYS)
    context.update(
        days=days, picked=picked,
        morning=[slot for slot in picked.slots if slot.start < 12 * 60],
        afternoon=[slot for slot in picked.slots if slot.start >= 12 * 60],
        earlier_url=address(vehicle, chosen, **{"from": max(earlier, first).isoformat()}) if strip_start > first
        else None,
        later_url=address(vehicle, chosen, **{"from": later.isoformat()}) if later <= last else None,
        day_url=lambda day: address(vehicle, chosen, day=day.isoformat(), **{"from": strip_start.isoformat()}),
        change_url=address(vehicle, chosen, edit=1),
    )
    return render_template("book.html", **context)
