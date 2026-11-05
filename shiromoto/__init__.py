"""ShiroMoto Garage: car and motorcycle service bookings, bays, mechanics and jobs.

Flask builds the app inside create_app() (the "app factory" pattern).
Tests call it with their own settings, so each test gets a fresh app and database.
"""
import mimetypes
import os
from datetime import timedelta
from pathlib import Path

from flask import Flask, g, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

# Browsers want the right type for the app manifest. Python doesn't know this one.
mimetypes.add_type("application/manifest+json", ".webmanifest")

DEV_SECRET_KEY = "dev-only-not-secret"

# Content-Security-Policy: the browser may only load scripts, styles, fonts
# and images from this site. Even if someone sneaked a <script> into a
# booking note, the browser would refuse to run it.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "font-src 'self'",
    "img-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    on_render = bool(os.environ.get("RENDER"))  # Render sets RENDER=true on its servers
    app.config.from_mapping(
        # Signs the login cookie. On Render it comes from an environment variable.
        SECRET_KEY=os.environ.get("SECRET_KEY", DEV_SECRET_KEY),
        # Local SQLite file, in the instance/ folder (git ignores it).
        DATABASE=str(Path(app.instance_path) / "shiromoto.sqlite"),
        # Set on the live site: then we use PostgreSQL (Neon) instead of SQLite.
        DATABASE_URL=os.environ.get("DATABASE_URL"),
        # Cookie safety: not readable by JavaScript (Flask's default), not sent
        # with forms from other sites, and only over HTTPS on the live site.
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=on_render,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        MAX_CONTENT_LENGTH=6 * 1024 * 1024,  # check-in photos (stage 5), plus the form around them
        # The site's address, for links in notices sent by the timer from the command line.
        BASE_URL=os.environ.get("BASE_URL", ""),
        # Email through Gmail with an app password. Without them, notices stay in the app.
        MAIL_USERNAME=os.environ.get("MAIL_USERNAME", ""),
        MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD", ""),
        MAIL_FROM=os.environ.get("MAIL_FROM", ""),
        # Push notifications to phones (flask --app shiromoto make-vapid-keys makes them).
        VAPID_PUBLIC_KEY=os.environ.get("VAPID_PUBLIC_KEY", ""),
        VAPID_PRIVATE_KEY=os.environ.get("VAPID_PRIVATE_KEY", ""),
        VAPID_SUBJECT=os.environ.get("VAPID_SUBJECT", ""),
        # The secret the GitHub timer sends to /tasks/tick. Empty: the endpoint is off.
        TICK_KEY=os.environ.get("TICK_KEY", ""),
    )
    if test_config:
        app.config.update(test_config)
    if on_render:
        # Render's proxy talks HTTPS to the visitor and HTTP to us. This makes
        # Flask believe the X-Forwarded-Proto header, so links start with https://.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    if on_render and app.config["SECRET_KEY"] == DEV_SECRET_KEY:
        raise RuntimeError("Set the SECRET_KEY environment variable on Render.")
    if on_render and not app.config["DATABASE_URL"]:
        # Without it we'd use a SQLite file, and Render forgets files on every restart.
        raise RuntimeError("Set the DATABASE_URL environment variable on Render (Neon's connection string).")

    from . import auth, booking, db, formatting, shops, tick
    from .models import (CHECK_LABELS, CLOSURE_LABELS, JOB_LABELS, LINE_LABELS, ROLE_LABELS, SOURCE_LABELS,
                         STATUS_LABELS, VEHICLE_LABELS, WAITLIST_LABELS, WEEKDAYS)
    from .views import app_pages, book, bookings, desk, garage, jobs, public, settings, waiting

    db.init_app(app)
    auth.init_app(app)
    formatting.init_app(app)
    tick.init_app(app)
    # auth comes first: its before_request (load_user) must run before the others'.
    for blueprint in (auth.bp, public.bp, book.bp, bookings.bp, garage.bp, desk.bp, jobs.bp, waiting.bp,
                      app_pages.bp, tick.bp, settings.bp):
        app.register_blueprint(blueprint)

    app.jinja_env.globals.update(APP_NAME="ShiroMoto Garage", ROLE_LABELS=ROLE_LABELS,
                                 VEHICLE_LABELS=VEHICLE_LABELS, CLOSURE_LABELS=CLOSURE_LABELS, WEEKDAYS=WEEKDAYS,
                                 STATUS_LABELS=STATUS_LABELS, SOURCE_LABELS=SOURCE_LABELS,
                                 CHECK_IN_EARLY=booking.CHECK_IN_EARLY, NO_SHOW_GRACE=booking.NO_SHOW_GRACE,
                                 JOB_LABELS=JOB_LABELS, LINE_LABELS=LINE_LABELS, CHECK_LABELS=CHECK_LABELS,
                                 WAITLIST_LABELS=WAITLIST_LABELS)

    @app.context_processor
    def shop_and_demo():
        """Every page knows the shop, and the demo bar knows who can be "viewed as"."""
        values = {"shop": shops.current(), "vapid_key": app.config.get("VAPID_PUBLIC_KEY", "")}
        if g.get("visitor") is not None:
            values["demo_people"] = shops.people(db.session(), g.visitor.shop)
        return values

    @app.get("/healthz")
    def healthz():
        return "ok"  # Render checks this after a deploy

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")  # trust our file types
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")  # no showing us inside other sites
        if on_render:
            # Always use HTTPS for this site from now on (for a year).
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    def error_page(code, message):
        return render_template("error.html", code=code, message=message), code

    @app.errorhandler(400)
    def bad_request(error):
        return error_page(400, error.description or "Something in that request was wrong.")

    @app.errorhandler(403)
    def forbidden(error):
        return error_page(403, error.description or "You can't do that.")

    @app.errorhandler(404)
    def not_found(error):
        # Flask's own text ("The requested URL was not found...") is for developers.
        mine = error.description if error.description and "requested URL" not in error.description else None
        return error_page(404, mine or "We couldn't find that page.")

    @app.errorhandler(413)
    def too_large(error):
        return error_page(413, "That file is too big.")

    return app
