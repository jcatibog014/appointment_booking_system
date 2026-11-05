"""The timer: everything that should happen even when nobody opens a page.

Render's free plan has no timer and sleeps when idle, so a free GitHub Actions
schedule (.github/workflows/tick.yml) calls POST /tasks/tick every 15 minutes
with a secret key (TICK_KEY). From the computer: flask --app shiromoto tick.

Each run, for every shop:
1. purge holds that ran out (their time is free again);
2. end waitlist offers nobody took, then offer freed times to the next people;
3. queue the day-before reminders and the "service due" reminders;
then send the notices that are due. Running it twice at once is safe: notices
are queued once (UNIQUE dedupe_key) and sent once (claimed before sending).
"""
import hmac

import click
from flask import Blueprint, abort, current_app, jsonify, request
from flask.cli import with_appcontext

from . import booking, db, moves, notices, waitlist
from .dates import utcnow

bp = Blueprint("tick", __name__)


def run(s, now):
    report = {"offers": 0, "reminders": 0}
    for shop in notices.shops_to_tick(s):
        booking.purge_holds(s, shop, now)
        s.commit()
        for appointment in moves.expire(s, shop, now):
            notices.safely(s, notices.move_expired, shop, appointment, now)
        waitlist.expire(s, shop, now)
        report["offers"] += waitlist.offer_free_times(s, shop, now)
        if not shop.is_demo:
            report["reminders"] += notices.queue_day_before(s, shop, now) + notices.queue_service_due(s, shop, now)
        s.commit()
    report["sent"], report["failed"], report["skipped"] = notices.send_pending(s, now)
    return report


@bp.post("/tasks/tick")
def tick():
    key = current_app.config.get("TICK_KEY") or ""
    sent = request.headers.get("X-Tick-Key", "")
    if not key or not hmac.compare_digest(sent, key):
        abort(404)
    return jsonify(run(db.session(), utcnow()))


@click.command("tick")
@with_appcontext
def tick_command():
    """Run the timer once (reminders, waitlist offers, sending notices)."""
    base = current_app.config.get("BASE_URL") or "http://127.0.0.1:5008"
    with current_app.test_request_context(base_url=base):  # links in notices need the site's address
        report = run(db.session(), utcnow())
    click.echo(", ".join(f"{name}: {value}" for name, value in report.items()))


@click.command("make-vapid-keys")
def make_vapid_keys_command():
    """Make the two keys for push notifications. Put them in Render's environment."""
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid, b64urlencode
    keys = Vapid()
    keys.generate_keys()
    private = b64urlencode(keys.private_key.private_bytes(serialization.Encoding.DER,
                                                          serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()))
    public = b64urlencode(keys.public_key.public_bytes(serialization.Encoding.X962,
                                                       serialization.PublicFormat.UncompressedPoint))
    click.echo(f"VAPID_PUBLIC_KEY={public}\nVAPID_PRIVATE_KEY={private}")


def init_app(app):
    app.cli.add_command(tick_command)
    app.cli.add_command(make_vapid_keys_command)
