"""The workshop: the Jobs board, one job's page, parts and stock, and a vehicle's history.

Every staff member works on jobs (move them, fill in the check-in checklist, add
photos, propose estimate lines, write notes). Parts are run by the owner and the
service advisors. The customer sees their job from their booking page (/b/<ref>):
its public timeline, the checklist, the photos, and the estimate to approve.
"""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import db, forms, jobs, notices, photos, shops
from ..auth import roles_required, staff_required
from ..booking import BookingError
from ..dates import local_today, utcnow
from ..models import ADVISOR, CHECK_LABELS, FUEL_LEVELS, JOB_LABELS, JOB_ORDER, LABOR, OWNER, PART, READY, RELEASED, \
    Appointment, Job, Part, Photo, Service, StockMove, Vehicle
from .bookings import open_booking

bp = Blueprint("jobs", __name__)

front_desk_required = roles_required(OWNER, ADVISOR)


def shop_job(job_id):
    job = db.session().get(Job, job_id)
    if job is None or job.shop_id != shops.current().id:
        abort(404)
    return job


def back_to(job):
    return redirect(url_for("jobs.show", job_id=job.id))


def attempt(job, action, message):
    s = db.session()
    try:
        action(s)
    except (BookingError, photos.PhotoError) as error:
        s.rollback()
        flash(str(error), "error")
    else:
        flash(message)
    return back_to(job)


# ---------- The board and one job ----------

@bp.get("/desk/jobs")
@staff_required
def board():
    s = db.session()
    shop = shops.current()
    mine = g.user.role == "mechanic" and request.args.get("all") != "1"
    live = jobs.jobs_for_board(s, shop, g.user.id if mine else None)
    columns = [(status, [job for job in live if job.status == status]) for status in JOB_ORDER if status != RELEASED]
    return render_template("jobs/board.html", columns=columns, mine=mine, count=len(live))


@bp.get("/jobs/<int:job_id>")
@staff_required
def show(job_id):
    job = shop_job(job_id)
    s = db.session()
    parts = s.scalars(select(Part).where(Part.shop_id == job.shop_id, Part.is_active.is_(True))
                      .order_by(Part.name)).all()
    return render_template(
        "jobs/job.html", job=job, appointment=job.appointment, moves=jobs.MOVES[job.status],
        part_options=[(p.id, f"{p.name} ({p.stock} in stock)") for p in parts],
        fuel_options=[(level, level) for level in FUEL_LEVELS], check_labels=CHECK_LABELS,
        decide_url=(lambda line: url_for("jobs.decide_for_customer", job_id=job.id, line_id=line.id))
        if g.user.role in (OWNER, ADVISOR) else None)


@bp.post("/jobs/<int:job_id>/move")
@staff_required
def move(job_id):
    job = shop_job(job_id)
    status = request.form.get("status")
    if status not in JOB_LABELS:
        abort(400)
    def act(s):
        jobs.move(s, job, status, utcnow(), g.user, request.form.get("version", type=int), request.form.get("note", ""))
        if status == READY:
            notices.safely(s, notices.ready, shops.current(), job, utcnow())
    return attempt(job, act, f"The job is now: {JOB_LABELS[status]}.")


@bp.post("/jobs/<int:job_id>/checklist")
@staff_required
def checklist(job_id):
    job = shop_job(job_id)
    states = {item.id: request.form.get(f"state-{item.id}") for item in job.checklist}
    notes = {item.id: request.form.get(f"note-{item.id}") for item in job.checklist}
    return attempt(job, lambda s: jobs.save_checklist(s, job, request.form.get("fuel", ""), states, notes,
                                                      request.form.get("version", type=int)),
                   "The check-in checklist is saved.")


@bp.post("/jobs/<int:job_id>/notes")
@staff_required
def note(job_id):
    job = shop_job(job_id)
    public = request.form.get("public") == "1"
    return attempt(job, lambda s: jobs.add_note(s, job, request.form.get("text", ""), utcnow(), g.user, public),
                   "The note is added." + (" The customer can see it." if public else ""))


# ---------- Photos ----------

@bp.post("/jobs/<int:job_id>/photos")
@staff_required
def add_photo(job_id):
    job = shop_job(job_id)
    upload = request.files.get("photo")

    def save(s):
        data, thumb, width, height = photos.process(upload.read() if upload else b"")
        s.add(Photo(job_id=job.id, caption=request.form.get("caption", "").strip()[:120], data=data, thumb=thumb,
                    width=width, height=height, created_at=utcnow()))
        s.commit()
    return attempt(job, save, "The photo is added.")


@bp.post("/jobs/<int:job_id>/photos/<int:photo_id>/delete")
@staff_required
def delete_photo(job_id, photo_id):
    job = shop_job(job_id)
    photo = db.session().get(Photo, photo_id)
    if photo is None or photo.job_id != job.id:
        abort(404)

    def remove(s):
        s.delete(photo)
        s.commit()
    return attempt(job, remove, "The photo is removed.")


@bp.get("/photos/<int:photo_id>")
def photo(photo_id):
    """A photo, for staff, or for whoever can open the booking it belongs to."""
    picture = db.session().get(Photo, photo_id)
    if picture is None:
        abort(404)
    job = db.session().get(Job, picture.job_id)
    if job.shop_id != shops.current_or_404().id:
        abort(404)
    if not (g.user is not None and g.user.is_staff):
        open_booking(job.appointment.ref)  # 404 unless this visitor may see the booking
    body = picture.thumb if request.args.get("size") == "thumb" else picture.data
    return body, 200, {"Content-Type": "image/webp", "Cache-Control": "private, max-age=86400"}


# ---------- The estimate ----------

@bp.post("/jobs/<int:job_id>/lines")
@staff_required
def add_line(job_id):
    job = shop_job(job_id)
    s = db.session()
    kind = request.form.get("kind")
    part = None
    if kind == PART and request.form.get("part_id"):
        part = s.get(Part, request.form.get("part_id", type=int) or 0)
        if part is None or part.shop_id != job.shop_id:
            abort(400)
    price, problem = forms.pesos(request.form, "unit_price")
    if not request.form.get("unit_price") and part is not None:
        price, problem = part.price, None
    if problem:
        flash(problem, "error")
        return back_to(job)
    quantity = request.form.get("quantity", type=int) or 1
    def act(s):
        jobs.add_line(s, job, kind, request.form.get("description", ""), request.form.get("reason", ""), quantity,
                      price, utcnow(), part)
        s.refresh(job)
        notices.safely(s, notices.estimate_waiting, shops.current(), job, utcnow())
    return attempt(job, act, "Added to the estimate. The customer can approve it from their booking page.")


@bp.post("/jobs/<int:job_id>/lines/<int:line_id>/remove")
@staff_required
def remove_line(job_id, line_id):
    job = shop_job(job_id)
    line = next((x for x in job.lines if x.id == line_id), None)
    if line is None:
        abort(404)
    return attempt(job, lambda s: jobs.remove_line(s, job, line), "The line is removed from the estimate.")


@bp.post("/jobs/<int:job_id>/lines/<int:line_id>/decide")
@front_desk_required
def decide_for_customer(job_id, line_id):
    """The customer said yes or no on the phone, and the desk records it."""
    job = shop_job(job_id)
    approve = request.form.get("decision") == "approve"
    who = f"{g.user.first_name} (for the customer, by phone)"
    return attempt(job, lambda s: jobs.decide(s, job, line_id, approve, utcnow(), who),
                   "Approved." if approve else "Declined.")


@bp.post("/jobs/<int:job_id>/lines/<int:line_id>/take")
@staff_required
def take_part(job_id, line_id):
    job = shop_job(job_id)
    line = next((x for x in job.lines if x.id == line_id), None)
    if line is None:
        abort(404)
    return attempt(job, lambda s: jobs.take_now(s, job, line, utcnow(), g.user), "Taken from stock.")


# ---------- Parts ----------

@bp.route("/desk/parts", methods=["GET", "POST"])
@front_desk_required
def parts():
    s = db.session()
    shop = shops.current()
    errors = {}
    if request.method == "POST":
        name, errors["name"] = forms.text(request.form, "name", "the part's name", 80)
        sku, errors["sku"] = forms.text(request.form, "sku", "a code", 40, required=False)
        price, errors["price"] = forms.pesos(request.form, "price")
        cost, errors["cost"] = forms.pesos(request.form, "cost")
        stock, errors["stock"] = forms.whole_number(request.form, "stock", 0, 99999, "the stock") \
            if request.form.get("stock") else (0, None)
        low, errors["low_level"] = forms.whole_number(request.form, "low_level", 0, 9999, "the low level") \
            if request.form.get("low_level") else (2, None)
        errors = {k: v for k, v in errors.items() if v}
        if not errors:
            try:
                part = Part(shop_id=shop.id, name=name, sku=sku, price=price, cost=cost, stock=0, low_level=low)
                s.add(part)
                s.flush()
                if stock:
                    jobs.receive(s, part, stock, "First count", utcnow(), g.user)
                s.commit()
            except IntegrityError:
                s.rollback()
                errors["name"] = "There is already a part with this name."
            else:
                flash(f"{name} is added to the parts.")
                return redirect(url_for("jobs.parts"))
    rows = s.scalars(select(Part).where(Part.shop_id == shop.id).order_by(Part.name)).all()
    return render_template("jobs/parts.html", rows=rows, low=[p for p in rows if p.is_active and p.is_low],
                           errors=errors), 400 if errors else 200


@bp.route("/desk/parts/<int:part_id>", methods=["GET", "POST"])
@front_desk_required
def part(part_id):
    s = db.session()
    item = s.get(Part, part_id)
    if item is None or item.shop_id != shops.current().id:
        abort(404)
    if request.method == "POST":
        change = request.form.get("change", type=int) or 0
        try:
            jobs.receive(s, item, change, request.form.get("reason", ""), utcnow(), g.user)
        except BookingError as error:
            flash(str(error), "error")
        else:
            flash(f"{item.name}: {'+' if change > 0 else ''}{change}. In stock now: {item.stock}.")
        return redirect(url_for("jobs.part", part_id=item.id))
    moves = s.scalars(select(StockMove).where(StockMove.part_id == item.id).order_by(StockMove.at.desc())
                      .limit(50)).all()
    return render_template("jobs/part.html", item=item, moves=moves)


# ---------- A vehicle's history ----------

@bp.get("/desk/vehicles/<int:vehicle_id>")
@staff_required
def vehicle(vehicle_id):
    s = db.session()
    shop = shops.current()
    item = s.get(Vehicle, vehicle_id)
    if item is None or item.shop_id != shop.id:
        abort(404)
    visits = s.scalars(select(Appointment).where(Appointment.vehicle_id == item.id)
                       .order_by(Appointment.starts_at.desc())).all()
    lookup = {sv.id: sv for sv in s.scalars(select(Service).where(Service.shop_id == shop.id))}
    done = [v for v in visits if v.checked_in_at]
    return render_template("jobs/vehicle.html", item=item, visits=visits, today=local_today(shop.timezone),
                           due=jobs.next_due(done, lookup), last_km=next((v.mileage_in for v in done if v.mileage_in),
                                                                          None),
                           is_overdue=jobs.is_overdue, due_soon=jobs.due_soon, labor=LABOR)
