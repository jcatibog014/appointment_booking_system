"""Logging in and out, staff invite links, the demo, and protection for every form.

People log in with their email address. Passwords are stored only as hashes.
- The owner is made on the command line: flask --app shiromoto create-owner
- Staff (service advisors and mechanics) join with an invite link from the owner.
- Customers sign up themselves (stage 3).

The login cookie holds the user's id and their session_version; load_user()
checks both on every request, so an old cookie is logged out straight away.
"""
import functools
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

import click
from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from flask.cli import with_appcontext
from sqlalchemy import delete, func, select
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, shops
from .dates import utcnow
from .models import ADVISOR, MECHANIC, OWNER, ROLE_LABELS, Invite, Shop, User

bp = Blueprint("auth", __name__)

PASSWORD_MIN_LENGTH = 8
NAME_MAX_LENGTH = 80
EMAIL_MAX_LENGTH = 254
# Good enough to catch typing mistakes. The only real test is an email that arrives.
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
# After 5 wrong passwords in a row, the account waits 15 minutes.
MAX_FAILED_LOGINS = 5
LOCK_TIME = timedelta(minutes=15)
INVITE_LIFETIME = timedelta(days=7)

DEMO_LIFETIME = timedelta(hours=2)
MAX_DEMOS = 200  # demo shops alive at once, so a script can't fill the database

WRONG_LOGIN = "Wrong email or password."
LOCKED = "Too many failed attempts. Please wait 15 minutes and try again."


# ---------- Users in the database ----------

def clean_email(text):
    return (text or "").strip().lower()


def email_problem(email):
    if not email:
        return "Enter your email address."
    if len(email) > EMAIL_MAX_LENGTH or not EMAIL_PATTERN.fullmatch(email):
        return "That doesn't look like an email address."
    return None


def name_problem(name):
    if not name:
        return "Enter a name."
    if len(name) > NAME_MAX_LENGTH:
        return f"Use at most {NAME_MAX_LENGTH} characters."
    return None


def password_problem(password, confirm):
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Use at least {PASSWORD_MIN_LENGTH} characters."
    if password != confirm:
        return "The two passwords are not the same."
    return None


def find_user(s, email):
    """A user of the real shop by email, or None. Demo people can't be found,
    so nobody can open a demo with the login form."""
    return s.scalar(select(User).join(Shop).where(User.email == clean_email(email), Shop.is_demo.is_(False)))


def create_user(s, shop, email, name, password, role, moment):
    """Add a user to a shop. The caller commits."""
    user = User(shop_id=shop.id, email=clean_email(email), name=name.strip(), role=role,
                password_hash=generate_password_hash(password), created_at=moment)
    s.add(user)
    s.flush()
    return user


def set_password(user, password):
    user.password_hash = generate_password_hash(password)
    user.failed_logins = 0
    user.locked_until = None
    user.session_version += 1  # every other device is logged out


def check_login(s, email, password, moment):
    """Return (user, None) if the password is right, else (None, message).
    The message never says whether the email is known."""
    user = find_user(s, email)
    if user is None or not user.is_active:
        return None, WRONG_LOGIN
    if user.locked_until and user.locked_until > moment:
        return None, LOCKED
    if not check_password_hash(user.password_hash, password or ""):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.failed_logins, user.locked_until = 0, moment + LOCK_TIME
        s.commit()
        return None, WRONG_LOGIN
    user.failed_logins, user.locked_until = 0, None
    s.commit()
    return user, None


def home_for(user):
    """Where a person lands after logging in."""
    return url_for("desk.today") if user.is_staff else url_for("public.home")


def safe_next(target, default=None):
    """Only go back to a page on this site (after logging in or saving a form).

    "/settings/bays" is fine. "https://evil.example" and "//evil.example"
    are not: a fake link could send people there after a real login. Browsers
    drop tabs and newlines and read a backslash as a slash, so those are out too.
    """
    if (target and target.startswith("/") and not target.startswith("//")
            and "\\" not in target and not any(ord(c) < 32 for c in target)):
        return target
    return default or url_for("public.home")


def log_in(user):
    session.clear()  # a new session for the new user (old cookies can't be reused)
    session.permanent = True  # stay logged in for PERMANENT_SESSION_LIFETIME
    session["user_id"] = user.id
    session["session_version"] = user.session_version


# ---------- Invite links ----------

def token_hash(token):
    """Invite links carry a random secret. Only its SHA-256 is stored: it is long
    and random, so a slow password hash isn't needed to keep it safe."""
    return hashlib.sha256(token.encode()).hexdigest()


def make_invite(s, shop, name, role, by, moment):
    """A new invite. Returns (invite, token); the link is /join/<token>. The caller commits."""
    token = secrets.token_urlsafe(24)
    invite = Invite(shop_id=shop.id, name=name.strip(), role=role, token_hash=token_hash(token),
                    created_by_id=by.id if by else None, created_at=moment, expires_at=moment + INVITE_LIFETIME)
    s.add(invite)
    s.flush()
    return invite, token


def open_invite(s, token, moment):
    """The invite this link belongs to, if it's still usable."""
    invite = s.scalar(select(Invite).where(Invite.token_hash == token_hash(token or "")))
    if invite is None or invite.used_at or invite.expires_at <= moment:
        return None
    return invite


# ---------- Every request ----------

@bp.before_app_request
def load_user():
    g.user = None
    g.visitor = None  # in the demo: the real visitor, while g.user is who they "view as"
    user_id = session.get("user_id")
    if user_id is None:
        return
    s = db.session()
    user = s.get(User, user_id)
    if (user is None or session.get("session_version") != user.session_version or not user.is_active
            or (user.shop.is_demo and user.shop.demo_expires_at <= utcnow())):
        # Keep the CSRF token: only the login part of this cookie is out of date.
        for key in ("user_id", "session_version", "view_as"):
            session.pop(key, None)
        return
    g.user = user
    if user.shop.is_demo:
        g.visitor = user
        other = s.get(User, session.get("view_as") or 0)
        if other is not None and other.shop_id == user.shop_id:
            g.user = other


def csrf_token():
    """A secret per session, put into every form as a hidden field.

    Another website could make your browser send a form to this site (your
    login cookie goes with it). It can't read this secret, so its form fails.
    """
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@bp.before_app_request
def check_csrf():
    if request.method != "POST":
        return
    sent = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf", "")
    # compare_digest takes the same time for every wrong guess.
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, "This form has expired. Please go back, reload the page and try again.")


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            # Only a GET can be repeated after logging in.
            next_page = request.full_path.rstrip("?") if request.method == "GET" else None
            return redirect(url_for("auth.login", next=next_page))
        return view(*args, **kwargs)
    return wrapped


def roles_required(*roles):
    """Only these roles may open the page. Others get 403 (logged out: the login page)."""
    def decorate(view):
        @functools.wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user.role not in roles:
                abort(403, "Your role can't open this page.")
            return view(*args, **kwargs)
        return wrapped
    return decorate


staff_required = roles_required(OWNER, ADVISOR, MECHANIC)
owner_required = roles_required(OWNER)


# ---------- Pages ----------

@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(home_for(g.user))
    next_page = request.values.get("next", "")
    error = None
    email = ""
    if request.method == "POST":
        email = clean_email(request.form.get("email", ""))[:EMAIL_MAX_LENGTH]
        user, error = check_login(db.session(), email, request.form.get("password", ""), utcnow())
        if user:
            log_in(user)
            return redirect(safe_next(next_page, home_for(user)))
    status = 401 if error else 200
    return render_template("login.html", error=error, email=email, next=next_page), status


@bp.route("/join/<token>", methods=["GET", "POST"])
def join(token):
    """A new staff member makes their login from the owner's invite link."""
    s = db.session()
    moment = utcnow()
    invite = open_invite(s, token, moment)
    if invite is None:
        abort(404, "This invite link has expired or was already used. Ask the owner for a new one.")
    errors = {}
    email = ""
    name = invite.name
    if request.method == "POST":
        email = clean_email(request.form.get("email", ""))
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")
        errors = {key: problem for key, problem in (
            ("email", email_problem(email)), ("name", name_problem(name)),
            ("password", password_problem(password, request.form.get("confirm_password", "")))) if problem}
        if not errors and s.scalar(select(User).where(User.shop_id == invite.shop_id, User.email == email)):
            errors["email"] = "Someone at the shop already uses this email."
        if not errors:
            shop = s.get(Shop, invite.shop_id)
            user = create_user(s, shop, email, name, password, invite.role, moment)
            invite.used_at = moment
            s.commit()
            log_in(user)
            flash(f"Welcome to {shop.name}, {user.first_name}! You're in as {ROLE_LABELS[user.role]}.")
            return redirect(home_for(user))
    return render_template("join.html", invite=invite, errors=errors, email=email, name=name,
                           role_label=ROLE_LABELS[invite.role]), 400 if errors else 200


def purge_demos(s, moment):
    """Delete every demo shop that has run out. Deleting the shop deletes
    everything in it (ON DELETE CASCADE)."""
    return s.execute(delete(Shop).where(Shop.is_demo.is_(True), Shop.demo_expires_at <= moment)
                     .execution_options(synchronize_session=False)).rowcount


def start_demo(s, moment):
    """A fresh sample shop for one visitor, who plays its owner. The caller commits."""
    from . import seed

    purge_demos(s, moment)
    if s.scalar(select(func.count()).select_from(Shop).where(Shop.is_demo.is_(True))) >= MAX_DEMOS:
        return None
    shop = seed.build(s, moment, demo=True)
    shop.demo_expires_at = moment + DEMO_LIFETIME
    return s.scalar(select(User).where(User.shop_id == shop.id, User.role == OWNER))


@bp.post("/demo")
def demo():
    """The visitor's own copy of the sample shop, deleted after 2 hours.
    Nobody sees anyone else's changes, and the next visitor starts clean."""
    if g.visitor is not None:
        return redirect(home_for(g.user))  # this browser already has one
    s = db.session()
    owner = start_demo(s, utcnow())
    if owner is None:
        flash("The demo is very busy right now. Please try again in a little while.", "error")
        return redirect(url_for("auth.login"))
    s.commit()
    log_in(owner)
    flash(f"Welcome to the demo! You're {owner.name}, the owner. Use View As in the menu to see the shop "
          "as a service advisor, a mechanic or a customer.")
    return redirect(home_for(owner))


@bp.post("/demo/view-as")
def view_as():
    """The demo visitor sees the shop through another sample person's eyes."""
    if g.visitor is None:
        abort(403, "View As is only for the demo.")
    person = db.session().get(User, request.form.get("person", type=int) or 0)
    if person is None or person.shop_id != g.visitor.shop_id or person.id == g.visitor.id:
        session.pop("view_as", None)
        person = g.visitor
    else:
        session["view_as"] = person.id
    flash(f"You now see the shop as {person.name} ({ROLE_LABELS[person.role]}).")
    # The page you were on may not open for the new role, so go to their start page.
    return redirect(home_for(person))


@bp.post("/logout")
def logout():
    session.clear()
    flash("You have logged out.")
    return redirect(url_for("auth.login"))


# ---------- Command line ----------

def ask_password():
    password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    if len(password) < PASSWORD_MIN_LENGTH:
        raise click.ClickException(f"Use at least {PASSWORD_MIN_LENGTH} characters.")
    return password


@click.command("create-owner")
@with_appcontext
@click.argument("email")
@click.option("--name", prompt="Your name", help="The owner's name, as staff and customers see it.")
@click.option("--shop", "shop_name", default="ShiroMoto Garage", show_default=True, help="The shop's name.")
def create_owner_command(email, name, shop_name):
    """Make the shop (the first time) and its owner's login."""
    from . import seed

    s = db.session()
    email = clean_email(email)
    problem = email_problem(email) or name_problem(name.strip())
    if problem:
        raise click.ClickException(problem)
    moment = utcnow()
    shop = shops.real_shop(s)
    if shop is None:
        shop = seed.starter_shop(s, shop_name, moment)
    if s.scalar(select(User).where(User.shop_id == shop.id, User.email == email)):
        raise click.ClickException(f"{email} already has a login. Use set-password to change the password.")
    create_user(s, shop, email, name, ask_password(), OWNER, moment)
    s.commit()
    click.echo(f"Made the owner {email} for {shop.name}. Log in at /login, then finish the setup in Settings.")


@click.command("set-password")
@with_appcontext
@click.argument("email")
def set_password_command(email):
    """Change someone's password (and unlock the account). Also ends their sessions."""
    s = db.session()
    user = find_user(s, email)
    if user is None:
        raise click.ClickException(f"Nobody at the shop uses {email}.")
    set_password(user, ask_password())
    s.commit()
    click.echo("The password is changed.")


@click.command("purge-demos")
@with_appcontext
def purge_demos_command():
    """Delete demo shops that have run out (Try Demo also does this by itself)."""
    s = db.session()
    count = purge_demos(s, utcnow())
    s.commit()
    click.echo(f"Deleted {count} old demo shop(s).")


def init_app(app):
    app.jinja_env.globals["csrf_token"] = csrf_token
    for command in (create_owner_command, set_password_command, purge_demos_command):
        app.cli.add_command(command)
