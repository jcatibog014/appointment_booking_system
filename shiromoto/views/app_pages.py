"""The phone app: the service worker, the offline page, and push sign-up.

The service worker must be served from the site's root (/sw.js), not /static/,
so it may look after every page. It keeps a copy of the look (CSS, scripts,
fonts, icons) and an offline page, and shows push notices.
"""
from flask import Blueprint, abort, current_app, g, jsonify, render_template, request, send_from_directory
from sqlalchemy import select

from .. import db, shops
from ..dates import utcnow
from ..models import PushSubscription
from .bookings import open_booking

bp = Blueprint("app_pages", __name__)


@bp.get("/sw.js")
def service_worker():
    response = send_from_directory(current_app.static_folder, "sw.js", max_age=0)
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"  # a new version reaches phones at once
    return response


@bp.get("/offline")
def offline():
    return render_template("offline.html", public_page=True)


@bp.post("/push/subscribe")
def subscribe():
    """Save this phone's push address: for a booking (guests), or for the logged-in person."""
    data = request.get_json(silent=True) or {}
    keys = data.get("keys") or {}
    endpoint, p256dh, auth = data.get("endpoint", ""), keys.get("p256dh", ""), keys.get("auth", "")
    if not endpoint.startswith("https://") or not p256dh or not auth or len(endpoint) > 600:
        abort(400, "That isn't a push subscription.")
    shop = shops.current_or_404()
    appointment = open_booking(data["ref"]) if data.get("ref") else None
    if appointment is None and g.user is None:
        abort(403)
    s = db.session()
    row = s.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    if row is None:
        row = PushSubscription(shop_id=shop.id, endpoint=endpoint, created_at=utcnow())
        s.add(row)
    row.p256dh, row.auth = p256dh[:200], auth[:100]
    row.appointment_id = appointment.id if appointment else row.appointment_id
    row.user_id = g.user.id if g.user is not None and appointment is None else row.user_id
    s.commit()
    return jsonify(ok=True)


@bp.post("/push/unsubscribe")
def unsubscribe():
    data = request.get_json(silent=True) or {}
    s = db.session()
    row = s.scalar(select(PushSubscription).where(PushSubscription.endpoint == data.get("endpoint", "")))
    if row is not None and row.shop_id == shops.current_or_404().id:
        s.delete(row)
        s.commit()
    return jsonify(ok=True)
