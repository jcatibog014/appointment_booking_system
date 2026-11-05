"""Settings: your own account for everyone, and the shop's setup for the owner.

The owner sets the week (opening hours and lunch), closures and leave,
bays, services and prices, and the staff (invites, roles, skills, hours).
In the demo all of it works on the visitor's own sandbox shop, except
changing a password.
"""
from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select
from werkzeug.security import check_password_hash

from .. import auth, db, forms, notices, shops
from ..auth import login_required, owner_required
from ..dates import local_today, utcnow
from ..formatting import duration
from ..models import ADVISOR, ANY, CLOSURE_LABELS, LEAVE, MECHANIC, OWNER, VEHICLE_KINDS, VEHICLE_LABELS, Bay, \
    BayType, Closure, Invite, Service, ShopHours, Skill, StaffHours, User

bp = Blueprint("settings", __name__, url_prefix="/settings")

WORK_MINUTES = list(range(15, 8 * 60 + 1, 15))  # 15 min to 8 h
BUFFER_MINUTES = [0, 15, 30, 45, 60]


def shop_row(model, row_id):
    """A row of this shop, or 404. Another shop's id looks exactly like a missing one."""
    row = db.session().get(model, row_id)
    if row is None or row.shop_id != shops.current().id:
        abort(404)
    return row


def done(message, endpoint, **values):
    flash(message)
    return redirect(url_for(endpoint, **values))


# ---------- Everyone: the hub and your account ----------

@bp.route("", methods=["GET", "POST"])
@login_required
def index():
    s = db.session()
    errors = {}
    if request.method == "POST":
        action = request.form.get("action")
        if action == "profile":
            name, errors["name"] = forms.text(request.form, "name", "your name", auth.NAME_MAX_LENGTH)
            phone, errors["phone"] = forms.text(request.form, "phone", "a phone number", 40, required=False)
            errors = {k: v for k, v in errors.items() if v}
            if not errors:
                g.user.name, g.user.phone = name, phone
                s.commit()
                return done("Your name and phone are saved.", "settings.index")
        elif action == "password":
            if g.visitor is not None:
                abort(403, "The demo can't change passwords.")
            if not check_password_hash(g.user.password_hash, request.form.get("current_password", "")):
                errors["current_password"] = "That isn't your current password."
            problem = auth.password_problem(request.form.get("new_password", ""),
                                            request.form.get("confirm_password", ""))
            if problem:
                errors["new_password"] = problem
            if not errors:
                auth.set_password(g.user, request.form["new_password"])
                s.commit()
                auth.log_in(g.user)  # this device stays logged in; the others are logged out
                return done("Your password is changed. Other devices are logged out.", "settings.index")
        else:
            abort(400)
    summary = shop_summary(s) if g.user.role == OWNER else None
    return render_template("settings/index.html", errors=errors, summary=summary, email_ready=notices.email_ready(),
                           push_ready=notices.push_ready(),
                           tick_ready=bool(current_app.config.get("TICK_KEY"))), 400 if errors else 200


def shop_summary(s):
    """One line for each setup page on the hub."""
    shop = shops.current()

    def count(model, *where):
        return s.scalar(select(func.count()).select_from(model).where(model.shop_id == shop.id, *where))
    today = local_today(shop.timezone)
    open_days = count(ShopHours, ShopHours.is_open.is_(True))
    return {
        "hours": f"Open {open_days} day{'s' * (open_days != 1)} a week",
        "closures": f"{count(Closure, Closure.ends_on >= today)} coming up",
        "bays": f"{count(Bay, Bay.is_active.is_(True))} bays in use",
        "services": f"{count(Service, Service.is_active.is_(True))} services offered",
        "staff": f"{count(User, User.role != 'customer', User.is_active.is_(True))} people",
    }


# ---------- Shop details and booking rules ----------

@bp.route("/shop", methods=["GET", "POST"])
@owner_required
def shop():
    s = db.session()
    shop = shops.current()
    errors = {}
    if request.method == "POST":
        action = request.form.get("action")
        if action == "details":
            values = {}
            for name, label, length, required in (("name", "the shop's name", 80, True),
                                                  ("tagline", "a tagline", 160, False),
                                                  ("phone", "a phone number", 40, False),
                                                  ("email", "an email address", 254, False),
                                                  ("address", "an address", 240, False)):
                values[name], errors[name] = forms.text(request.form, name, label, length, required)
            if values["email"] and not errors["email"]:
                errors["email"] = auth.email_problem(values["email"].lower())
            errors = {k: v for k, v in errors.items() if v}
            if not errors:
                for name, value in values.items():
                    setattr(shop, name, value)
                s.commit()
                return done("The shop's details are saved.", "settings.shop")
        elif action == "rules":
            rules = {}
            for name, low, high, label in (("min_notice_minutes", 0, 48 * 60, "the notice"),
                                           ("max_days_ahead", 1, 180, "how far ahead"),
                                           ("cancel_cutoff_hours", 0, 72, "the cut-off"),
                                           ("hold_minutes", 2, 30, "the hold time")):
                rules[name], problem = forms.whole_number(request.form, name, low, high, label)
                if problem:
                    errors[name] = problem
            if not errors:
                for name, value in rules.items():
                    setattr(shop, name, value)
                s.commit()
                return done("The booking rules are saved.", "settings.shop")
        else:
            abort(400)
    return render_template("settings/shop.html", errors=errors), 400 if errors else 200


# ---------- Opening hours ----------

def read_week(form):
    """The seven rows of the hours form -> ({weekday: values}, {field: problem})."""
    week, errors = {}, {}
    for day in range(7):
        is_open = forms.checked(form, f"open-{day}")
        row = {"is_open": is_open, "opens": None, "closes": None, "break_starts": None, "break_ends": None}
        if is_open:
            for field in ("opens", "closes"):
                row[field], problem = forms.quarter_time(form, f"{field}-{day}")
                if problem:
                    errors[f"{field}-{day}"] = problem
            for field in ("break_starts", "break_ends"):
                row[field], problem = forms.quarter_time(form, f"{field}-{day}", required=False)
                if problem:
                    errors[f"{field}-{day}"] = problem
            problem = forms.time_window(row["opens"], row["closes"], "Closing")
            if problem:
                errors[f"closes-{day}"] = problem
            starts, ends = row["break_starts"], row["break_ends"]
            if bool(starts) != bool(ends):
                errors[f"break_ends-{day}"] = "Give the break a start and an end, or leave both empty."
            elif starts and ends:
                problem = forms.time_window(starts, ends, "The break's end")
                if problem:
                    errors[f"break_ends-{day}"] = problem
                elif row["opens"] and row["closes"] and not forms.inside(starts, ends, row["opens"], row["closes"]):
                    errors[f"break_ends-{day}"] = "The break must be inside the opening hours."
        week[day] = row
    return week, errors


@bp.route("/hours", methods=["GET", "POST"])
@owner_required
def hours():
    s = db.session()
    shop = shops.current()
    current = shops.hours_by_weekday(s, shop)
    errors = {}
    if request.method == "POST":
        week, errors = read_week(request.form)
        if not errors:
            for day, values in week.items():
                row = current[day]
                if row.id is None:
                    s.add(row)
                for field, value in values.items():
                    setattr(row, field, value)
            s.commit()
            return done("The opening hours are saved. New bookings follow them straight away.", "settings.hours")
        # Show what was typed, with the problems next to it.
        for day, values in week.items():
            for field, value in values.items():
                setattr(current[day], field, value)
    return render_template("settings/hours.html", week=current, errors=errors), 400 if errors else 200


# ---------- Closures and leave ----------

@bp.route("/closures", methods=["GET", "POST"])
@owner_required
def closures():
    s = db.session()
    shop = shops.current()
    today = local_today(shop.timezone)
    errors, values = {}, {}
    if request.method == "POST":
        form = request.form
        kind = form.get("kind")
        if kind not in CLOSURE_LABELS:
            abort(400)
        values["reason"], errors["reason"] = forms.text(form, "reason", "a reason", 120)
        values["starts_on"], errors["starts_on"] = forms.day(form, "starts_on")
        values["ends_on"], errors["ends_on"] = forms.day(form, "ends_on") if form.get("ends_on") else (
            values["starts_on"], None)
        values["from_time"], errors["from_time"] = forms.quarter_time(form, "from_time", required=False)
        values["to_time"], errors["to_time"] = forms.quarter_time(form, "to_time", required=False)
        errors = {k: v for k, v in errors.items() if v}
        user_id = None
        if kind == LEAVE:
            mechanic = s.get(User, form.get("user_id", type=int) or 0)
            if mechanic is None or mechanic.shop_id != shop.id or mechanic.role == "customer":
                errors["user_id"] = "Choose who is on leave."
            else:
                user_id = mechanic.id
        if not errors:
            if values["ends_on"] < values["starts_on"]:
                errors["ends_on"] = "The last day can't be before the first day."
            elif (values["ends_on"] - values["starts_on"]).days > 366:
                errors["ends_on"] = "Make it at most a year."
            if bool(values["from_time"]) != bool(values["to_time"]):
                errors["to_time"] = "Give both times for part of a day, or leave both empty for whole days."
            elif values["from_time"]:
                errors["to_time"] = forms.time_window(values["from_time"], values["to_time"])
            errors = {k: v for k, v in errors.items() if v}
        if not errors:
            s.add(Closure(shop_id=shop.id, user_id=user_id, kind=kind, **values))
            s.commit()
            return done(f"Added: {values['reason']}.", "settings.closures")
        values["kind"], values["user_id"] = kind, user_id
    rows = s.scalars(select(Closure).where(Closure.shop_id == shop.id, Closure.ends_on >= today - timedelta(days=30))
                     .order_by(Closure.starts_on, Closure.id)).all()
    staff = [p for p in shops.people(s, shop) if p.role != "customer"]
    return render_template("settings/closures.html", upcoming=[c for c in rows if c.ends_on >= today],
                           past=[c for c in rows if c.ends_on < today][::-1],
                           staff_options=[(p.id, p.name) for p in staff], errors=errors,
                           values=values, today=today, year=today.year), 400 if errors else 200


@bp.post("/closures/<int:closure_id>/delete")
@owner_required
def delete_closure(closure_id):
    s = db.session()
    closure = shop_row(Closure, closure_id)
    s.delete(closure)
    s.commit()
    return done(f"Removed: {closure.reason}.", "settings.closures")


@bp.post("/closures/holidays")
@owner_required
def add_holidays():
    s = db.session()
    shop = shops.current()
    year = request.form.get("year", type=int)
    this_year = local_today(shop.timezone).year
    if year not in (this_year, this_year + 1):
        abort(400)
    added = shops.add_ph_holidays(s, shop, year)
    s.commit()
    if added:
        return done(f"Added {added} Philippine holidays for {year}.", "settings.closures")
    return done(f"The {year} holidays were already in the list.", "settings.closures")


# ---------- Bays ----------

@bp.route("/bays", methods=["GET", "POST"])
@owner_required
def bays():
    s = db.session()
    shop = shops.current()
    errors = {}
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_bay":
            name, problem = forms.text(request.form, "name", "the bay's name", 60)
            bay_type = s.get(BayType, request.form.get("bay_type_id", type=int) or 0)
            if problem:
                errors["name"] = problem
            elif s.scalar(select(Bay).where(Bay.shop_id == shop.id, Bay.name == name)):
                errors["name"] = "There is already a bay with this name."
            if bay_type is None or bay_type.shop_id != shop.id:
                errors["bay_type_id"] = "Choose a bay type."
            if not errors:
                last = s.scalar(select(func.max(Bay.position)).where(Bay.shop_id == shop.id)) or 0
                s.add(Bay(shop_id=shop.id, name=name, bay_type_id=bay_type.id, position=last + 1))
                s.commit()
                return done(f"{name} is added.", "settings.bays")
        elif action == "add_type":
            name, problem = forms.text(request.form, "type_name", "the type's name", 60)
            kind = request.form.get("vehicle_kind")
            if problem:
                errors["type_name"] = problem
            elif s.scalar(select(BayType).where(BayType.shop_id == shop.id, BayType.name == name)):
                errors["type_name"] = "There is already a bay type with this name."
            if kind not in (*VEHICLE_KINDS, ANY):
                errors["vehicle_kind"] = "Choose which vehicles it takes."
            if not errors:
                s.add(BayType(shop_id=shop.id, name=name, vehicle_kind=kind))
                s.commit()
                return done(f"The bay type {name} is added.", "settings.bays")
        else:
            abort(400)
    types = s.scalars(select(BayType).where(BayType.shop_id == shop.id).order_by(BayType.name)).all()
    return render_template("settings/bays.html", types=types, type_options=[(t.id, t.name) for t in types],
                           errors=errors), 400 if errors else 200


@bp.post("/bays/<int:bay_id>")
@owner_required
def edit_bay(bay_id):
    s = db.session()
    bay = shop_row(Bay, bay_id)
    if request.form.get("action") == "toggle":
        bay.is_active = not bay.is_active
        s.commit()
        state = "back in use" if bay.is_active else "out of use. It won't get new bookings"
        return done(f"{bay.name} is {state}.", "settings.bays")
    name, problem = forms.text(request.form, "name", "the bay's name", 60)
    clash = s.scalar(select(Bay).where(Bay.shop_id == bay.shop_id, Bay.name == name, Bay.id != bay.id))
    if problem or clash:
        flash(problem or "There is already a bay with this name.", "error")
        return redirect(url_for("settings.bays"))
    bay.name = name
    s.commit()
    return done(f"Renamed to {name}.", "settings.bays")


# ---------- Services ----------

@bp.get("/services")
@owner_required
def services():
    s = db.session()
    rows = s.scalars(select(Service).where(Service.shop_id == shops.current().id)
                     .order_by(Service.position, Service.name)).all()
    groups = {}
    for service in rows:
        groups.setdefault(service.category, []).append(service)
    return render_template("settings/services.html", groups=groups)


def read_service(s, shop, form, service_id=None):
    values, errors = {}, {}
    values["name"], errors["name"] = forms.text(form, "name", "the service's name", 80)
    values["category"], errors["category"] = forms.text(form, "category", "a category", 40)
    values["description"], errors["description"] = forms.text(form, "description", "a description", 600,
                                                              required=False)
    values["price_from"], errors["price_from"] = forms.pesos(form, "price_from")
    values["work_minutes"], errors["work_minutes"] = forms.whole_number(form, "work_minutes", 15, 480,
                                                                        "how long it takes")
    if values["work_minutes"] and values["work_minutes"] % 15:
        errors["work_minutes"] = "Choose a length in steps of 15 minutes."
    values["buffer_minutes"], errors["buffer_minutes"] = forms.whole_number(form, "buffer_minutes", 0, 60,
                                                                            "the clean-up time")
    if values["buffer_minutes"] is not None and values["buffer_minutes"] % 15:
        errors["buffer_minutes"] = "Choose a clean-up time in steps of 15 minutes."
    values["vehicle_kind"] = form.get("vehicle_kind")
    if values["vehicle_kind"] not in (*VEHICLE_KINDS, ANY):
        errors["vehicle_kind"] = "Choose which vehicles it is for."
    bay_type = s.get(BayType, form.get("bay_type_id", type=int) or 0)
    if bay_type is None or bay_type.shop_id != shop.id:
        errors["bay_type_id"] = "Choose the bay it needs."
    else:
        values["bay_type_id"] = bay_type.id
        if values["vehicle_kind"] in VEHICLE_KINDS and bay_type.vehicle_kind not in (values["vehicle_kind"], ANY):
            errors["bay_type_id"] = f"A {bay_type.name.lower()} can't take this vehicle."
    skill_id = form.get("skill_id", type=int)
    skill = s.get(Skill, skill_id) if skill_id else None
    if skill_id and (skill is None or skill.shop_id != shop.id):
        errors["skill_id"] = "Choose a skill."
    values["skill_id"] = skill.id if skill else None
    values["is_active"] = forms.checked(form, "is_active")
    values["again_months"], errors["again_months"] = forms.whole_number(form, "again_months", 0, 36,
                                                                        "the months") if form.get("again_months") else (0, None)
    values["again_km"], errors["again_km"] = forms.whole_number(form, "again_km", 0, 100000,
                                                                "the km") if form.get("again_km") else (0, None)
    if values["name"] and s.scalar(select(Service).where(Service.shop_id == shop.id, Service.name == values["name"],
                                                         Service.id != (service_id or 0))):
        errors["name"] = "There is already a service with this name."
    return values, {k: v for k, v in errors.items() if v}


def service_form(service, errors, status=200):
    s = db.session()
    shop = shops.current()
    types = s.scalars(select(BayType).where(BayType.shop_id == shop.id).order_by(BayType.name)).all()
    skills = s.scalars(select(Skill).where(Skill.shop_id == shop.id).order_by(Skill.name)).all()
    categories = s.scalars(select(Service.category).where(Service.shop_id == shop.id).distinct()
                           .order_by(Service.category)).all()
    return render_template(
        "settings/service_form.html", service=service, errors=errors, categories=categories,
        bay_options=[(t.id, f"{t.name} ({VEHICLE_LABELS[t.vehicle_kind].lower()})") for t in types],
        skill_options=[(skill.id, skill.name) for skill in skills],
        minute_options=[(m, duration(m)) for m in WORK_MINUTES],
        buffer_options=[(m, duration(m) if m else "None") for m in BUFFER_MINUTES]), status


@bp.route("/services/new", methods=["GET", "POST"])
@owner_required
def new_service():
    s = db.session()
    shop = shops.current()
    service = Service(shop_id=shop.id, vehicle_kind=ANY, work_minutes=60, buffer_minutes=0, price_from=0,
                      category="Maintenance", is_active=True, name="", description="")
    if request.method == "POST":
        values, errors = read_service(s, shop, request.form)
        for field, value in values.items():
            setattr(service, field, value)
        if errors:
            return service_form(service, errors, 400)
        service.position = (s.scalar(select(func.max(Service.position)).where(Service.shop_id == shop.id)) or 0) + 1
        s.add(service)
        s.commit()
        return done(f"{service.name} is added to the services.", "settings.services")
    return service_form(service, {})


@bp.route("/services/<int:service_id>", methods=["GET", "POST"])
@owner_required
def edit_service(service_id):
    s = db.session()
    service = shop_row(Service, service_id)
    if request.method == "POST":
        values, errors = read_service(s, shops.current(), request.form, service.id)
        if errors:
            with s.no_autoflush:
                for field, value in values.items():
                    setattr(service, field, value)
                response = service_form(service, errors, 400)
            s.rollback()
            return response
        for field, value in values.items():
            setattr(service, field, value)
        s.commit()
        return done(f"{service.name} is saved.", "settings.services")
    return service_form(service, {})


# ---------- Staff ----------

@bp.route("/staff", methods=["GET", "POST"])
@owner_required
def staff():
    s = db.session()
    shop = shops.current()
    moment = utcnow()
    errors = {}
    new_link = None
    if request.method == "POST":
        name, problem = forms.text(request.form, "name", "their name", auth.NAME_MAX_LENGTH)
        role = request.form.get("role")
        if problem:
            errors["name"] = problem
        if role not in (ADVISOR, MECHANIC, OWNER):
            errors["role"] = "Choose a role."
        if not errors:
            invite, token = auth.make_invite(s, shop, name, role, g.user, moment)
            s.commit()
            new_link = url_for("auth.join", token=token, _external=True)
            flash(f"Invite link made for {invite.name}. Send it to them: it works once, for 7 days.")
    people = [p for p in shops.people(s, shop) if p.role != "customer"]
    invites = s.scalars(select(Invite).where(Invite.shop_id == shop.id, Invite.used_at.is_(None),
                                             Invite.expires_at > moment).order_by(Invite.created_at)).all()
    return render_template("settings/staff.html", people=people, invites=invites, errors=errors,
                           new_link=new_link), 400 if errors else 200


@bp.post("/invites/<int:invite_id>/cancel")
@owner_required
def cancel_invite(invite_id):
    s = db.session()
    invite = shop_row(Invite, invite_id)
    s.delete(invite)
    s.commit()
    return done(f"The invite for {invite.name} is cancelled. Its link no longer works.", "settings.staff")


@bp.route("/staff/<int:user_id>", methods=["GET", "POST"])
@owner_required
def person(user_id):
    s = db.session()
    person = shop_row(User, user_id)
    if person.role == "customer":
        abort(404)
    shop = shops.current()
    errors = {}
    if request.method == "POST":
        action = request.form.get("action")
        if action == "role":
            role = request.form.get("role")
            if person.id == g.user.id:
                abort(403, "You can't change your own role.")
            if role not in (OWNER, ADVISOR, MECHANIC):
                abort(400)
            person.role = role
            s.commit()
            return done(f"{person.name} is now a {auth.ROLE_LABELS[role].lower()}.", "settings.person",
                        user_id=person.id)
        if action == "skills":
            chosen = {int(v) for v in request.form.getlist("skill") if v.isdigit()}
            person.skills = s.scalars(select(Skill).where(Skill.shop_id == shop.id, Skill.id.in_(chosen))).all()
            s.commit()
            return done(f"{person.first_name}'s skills are saved.", "settings.person", user_id=person.id)
        if action == "hours":
            rows, errors = read_staff_week(s, shop, request.form)
            if not errors:
                existing = {h.weekday: h for h in person.hours}
                for day, values in rows.items():
                    row = existing.get(day) or StaffHours(user_id=person.id, weekday=day)
                    for field, value in values.items():
                        setattr(row, field, value)
                    s.add(row)
                s.commit()
                return done(f"{person.first_name}'s hours are saved.", "settings.person", user_id=person.id)
        elif action == "same_as_shop":
            for row in list(person.hours):
                s.delete(row)
            s.commit()
            return done(f"{person.first_name} now works the shop's hours.", "settings.person", user_id=person.id)
        elif action == "active":
            if person.id == g.user.id:
                abort(403, "You can't switch off your own login.")
            person.is_active = not person.is_active
            if not person.is_active:
                person.session_version += 1  # logged out everywhere, now
            s.commit()
            state = "can log in again" if person.is_active else "can't log in any more, and gets no new jobs"
            return done(f"{person.name} {state}.", "settings.person", user_id=person.id)
        elif action is not None:
            abort(400)
    skills = s.scalars(select(Skill).where(Skill.shop_id == shop.id).order_by(Skill.name)).all()
    week = shops.hours_by_weekday(s, shop)
    own = {h.weekday: h for h in person.hours}
    return render_template("settings/person.html", person=person, skills=skills, week=week, own=own,
                           errors=errors), 400 if errors else 200


def read_staff_week(s, shop, form):
    """A mechanic's seven rows. Their hours must fit inside the shop's."""
    week = shops.hours_by_weekday(s, shop)
    rows, errors = {}, {}
    for day in range(7):
        works = forms.checked(form, f"works-{day}")
        row = {"works": works, "starts": None, "ends": None}
        if works:
            if not week[day].is_open:
                errors[f"starts-{day}"] = "The shop is closed on this day."
            for field in ("starts", "ends"):
                row[field], problem = forms.quarter_time(form, f"{field}-{day}")
                if problem:
                    errors[f"{field}-{day}"] = problem
            if row["starts"] and row["ends"] and f"starts-{day}" not in errors:
                problem = forms.time_window(row["starts"], row["ends"])
                if problem:
                    errors[f"ends-{day}"] = problem
                elif not forms.inside(row["starts"], row["ends"], week[day].opens, week[day].closes):
                    errors[f"ends-{day}"] = "Keep it inside the shop's opening hours."
        rows[day] = row
    return rows, errors

