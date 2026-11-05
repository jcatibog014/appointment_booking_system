"""The shop's public pages: what we do, what it costs, and when we're open."""
from datetime import timedelta

from flask import Blueprint, g, redirect, render_template, request, url_for
from sqlalchemy import select

from .. import db, shops
from ..dates import local_today
from ..models import CAR, MOTO, Service

bp = Blueprint("public", __name__)


def service_menu(s, shop):
    """Active services as {"car": {category: [services]}, "moto": {...}}, in the owner's order.
    A service for any vehicle is listed under both."""
    services = s.scalars(select(Service).where(Service.shop_id == shop.id, Service.is_active.is_(True))
                         .order_by(Service.position, Service.name)).all()
    menu = {CAR: {}, MOTO: {}}
    for service in services:
        for kind in (CAR, MOTO):
            if service.vehicle_kind in (kind, "any"):
                menu[kind].setdefault(service.category, []).append(service)
    return menu


@bp.get("/")
def home():
    """The start page. A logged-in customer starts at My Garage, their dashboard. The
    phone app opens /?from=app, so staff start at the desk too. The shop's page is
    still at /shop for everyone."""
    if g.user is not None and (g.user.role == "customer" or request.args.get("from") == "app"):
        from ..auth import home_for
        return redirect(home_for(g.user))
    return shop_page()


@bp.get("/shop")
def shop_page():
    s = db.session()
    shop = shops.current()
    if shop is None:
        return render_template("not_set_up.html"), 503
    today = local_today(shop.timezone)
    upcoming = [c for c in shops.closures_between(s, shop, today, today + timedelta(days=30)) if c.user_id is None]
    return render_template("home.html", menu=service_menu(s, shop), hours=shops.hours_by_weekday(s, shop),
                           today=today, closures=upcoming, public_page=True)
