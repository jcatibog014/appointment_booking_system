"""Jobs: what happens to a vehicle after check-in, estimates the customer approves,
and parts taken from stock. Every function here is one transaction.

The road a job takes (MOVES lists every step allowed):

    Checked in -> Inspecting -> Waiting for approval -> In progress -> Ready -> Released
                                     ^                   |    ^
                                     |                   v    |
                                     +--- (new findings) Waiting for parts

Two kinds of "at the same time" are guarded here:
- deciding an estimate line is a conditional UPDATE (... WHERE status = 'proposed'),
  so if the customer and the front desk press at once, only one decision counts;
- taking a part from stock is `UPDATE parts SET stock = stock - n WHERE stock >= n`,
  so two jobs can't both take the last brake pad: one gets it, the other is told
  the part must be ordered. Stock can never go below zero (a CHECK rule backs this up).
"""
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm.exc import StaleDataError

from .booking import CHANGED, BookingError, Changed
from .models import APPROVED, AWAITING, CHECK_IN_ITEMS, CHECK_LABELS, CHECKED, DECLINED, INSPECTING, JOB_LABELS, \
    LABOR, PART, PROPOSED, READY, RELEASED, WAITING_PARTS, WORKING, Appointment, ChecklistItem, EstimateLine, Job, \
    JobEvent, Part, StockMove

MOVES = {
    CHECKED: (INSPECTING, WORKING),
    INSPECTING: (AWAITING, WORKING),
    AWAITING: (WORKING, INSPECTING),
    WORKING: (WAITING_PARTS, AWAITING, READY),
    WAITING_PARTS: (WORKING,),
    READY: (RELEASED, WORKING),
    RELEASED: (),
}
# Words for the customer's timeline, so they understand each step.
PUBLIC_TEXT = {
    CHECKED: "Your vehicle is checked in.",
    INSPECTING: "A mechanic is inspecting your vehicle.",
    AWAITING: "We found some things. Please look at the estimate and approve or decline each line.",
    WORKING: "Work is in progress.",
    WAITING_PARTS: "We're waiting for a part.",
    READY: "Your vehicle is ready for pickup.",
    RELEASED: "Released. Thank you, drive safe!",
}


class JobError(BookingError):
    pass


def open_job(s, appointment, now, by=None, fuel=""):
    """Start the job when the vehicle is checked in, with the check-in checklist. The caller commits."""
    job = Job(shop_id=appointment.shop_id, appointment_id=appointment.id, status=CHECKED, fuel_level=fuel,
              created_at=now, updated_at=now)
    job.checklist = [ChecklistItem(position=n, label=label) for n, label in enumerate(CHECK_IN_ITEMS)]
    job.events = [JobEvent(at=now, status=CHECKED, text=PUBLIC_TEXT[CHECKED], by_id=by.id if by else None)]
    s.add(job)
    s.flush()
    return job


def job_for(s, appointment, now, by=None):
    """The visit's job (opened on first use, for visits checked in before jobs existed)."""
    job = s.scalar(select(Job).where(Job.appointment_id == appointment.id))
    if job is None:
        job = open_job(s, appointment, appointment.checked_in_at or now, by)
        s.commit()
    return job


def check_version(job, version):
    """The page's version must be the job's: check BEFORE changing anything. (Reading a
    relationship can flush pending changes, which bumps the version.)"""
    if version is not None and version != job.version:
        raise Changed(CHANGED)


def save(s, job):
    """Commit, turning a lost race on the version (another save got in first) into a message."""
    try:
        s.commit()
    except StaleDataError as error:
        s.rollback()
        raise Changed(CHANGED) from error


def move(s, job, status, now, by=None, version=None, note=""):
    """Move a job one step along MOVES, with a line on its timeline."""
    check_version(job, version)
    if status not in MOVES.get(job.status, ()):
        raise JobError(f"A job that is {JOB_LABELS[job.status].lower()} can't go to {JOB_LABELS[status].lower()}.")
    if status == READY and any(line.status == PROPOSED for line in job.lines):
        raise JobError("The customer still has estimate lines to decide. Wait for them, or decide them by phone.")
    job.status, job.updated_at = status, now
    if status == RELEASED:
        job.released_at = now
    text = PUBLIC_TEXT[status] + (f" {note.strip()}" if note.strip() else "")
    job.events.append(JobEvent(at=now, status=status, text=text[:400], by_id=by.id if by else None))
    save(s, job)
    return job


def add_note(s, job, text, now, by, public):
    text = (text or "").strip()
    if not text:
        raise JobError("Write the note first.")
    job.events.append(JobEvent(at=now, text=text[:400], public=public, by_id=by.id))
    job.updated_at = now
    save(s, job)


def save_checklist(s, job, fuel, states, notes, version=None):
    """states and notes: {item id: value} from the check-in form."""
    from .models import FUEL_LEVELS
    check_version(job, version)
    job.fuel_level = fuel if fuel in FUEL_LEVELS else ""
    for item in job.checklist:
        state = states.get(item.id)
        if state in CHECK_LABELS:
            item.state = state
        item.note = (notes.get(item.id) or "").strip()[:200]
    save(s, job)


# ---------- The estimate ----------

def add_line(s, job, kind, description, reason, quantity, unit_price, now, part=None):
    if job.status in (READY, RELEASED):
        raise JobError("This job is finished. Move it back to In progress to add more work.")
    if kind not in (LABOR, PART):
        raise JobError("Choose labor or a part.")
    description = (description or (part.name if part else "")).strip()
    if not description:
        raise JobError("Say what the work or part is.")
    if not 1 <= quantity <= 999:
        raise JobError("Use a quantity from 1 to 999.")
    line = EstimateLine(job_id=job.id, kind=kind, description=description[:160], reason=(reason or "").strip()[:300],
                        quantity=quantity, unit_price=unit_price, part_id=part.id if part else None, created_at=now)
    s.add(line)
    if job.status in (INSPECTING, WORKING, CHECKED):
        job.status, job.updated_at = AWAITING, now
        job.events.append(JobEvent(at=now, status=AWAITING, text=PUBLIC_TEXT[AWAITING]))
    s.commit()
    return line


def remove_line(s, job, line):
    if line.status != PROPOSED:
        raise JobError("A line the customer already decided stays on the estimate.")
    s.delete(line)
    s.commit()


def decide(s, job, line_id, approve, now, who):
    """The customer (or the desk, for a customer on the phone) approves or declines one line.

    The UPDATE only matches while the line is still proposed. If two people decide at
    the same moment, the second UPDATE matches nothing, and that person is told it was
    already decided. An approved part is taken from stock in the same transaction."""
    status = APPROVED if approve else DECLINED
    changed = s.execute(update(EstimateLine).where(
        EstimateLine.id == line_id, EstimateLine.job_id == job.id, EstimateLine.status == PROPOSED)
        .values(status=status, decided_at=now, decided_by=who[:80])).rowcount
    if changed != 1:
        s.rollback()
        raise JobError("This line was already decided.")
    line = s.get(EstimateLine, line_id)
    s.refresh(line)
    short = False
    if approve and line.part_id:
        short = not take_from_stock(s, line, now, None)
    job.events.append(JobEvent(at=now, text=f"{who.capitalize()} {status} {line.description}"
                                             f" ({line.quantity} × ₱{line.unit_price // 100:,})."[:400]))
    if short:
        job.events.append(JobEvent(at=now, status=WAITING_PARTS,
                                   text=f"{line.part.name} must be ordered. {PUBLIC_TEXT[WAITING_PARTS]}"))
    if not any(other.status == PROPOSED for other in job.lines if other.id != line.id):
        if job.status == AWAITING:
            job.status = WAITING_PARTS if short or any(
                other.status == APPROVED and other.part_id and not other.stock_taken for other in job.lines) \
                else WORKING
            job.events.append(JobEvent(at=now, status=job.status, text=PUBLIC_TEXT[job.status]))
    job.updated_at = now
    s.commit()
    return line


# ---------- Parts and stock ----------

def take_from_stock(s, line, now, by):
    """Take an approved line's parts from stock, atomically. True if there were enough.
    The caller commits (so the approval and the stock move are one transaction)."""
    if line.stock_taken or not line.part_id:
        return True
    taken = s.execute(update(Part).where(Part.id == line.part_id, Part.stock >= line.quantity)
                      .values(stock=Part.stock - line.quantity)).rowcount
    if taken != 1:
        return False
    line.stock_taken = True
    s.add(StockMove(part_id=line.part_id, job_id=line.job_id, change=-line.quantity, at=now,
                    reason=f"Used on job #{line.job_id}", by_id=by.id if by else None))
    return True


def take_now(s, job, line, now, by):
    """The part has arrived: try taking it from stock again."""
    if line.status != APPROVED or line.stock_taken:
        raise JobError("Nothing to take for this line.")
    if not take_from_stock(s, line, now, by):
        s.rollback()
        raise JobError(f"There still aren't enough {line.part.name} in stock.")
    if job.status == WAITING_PARTS and all(other.stock_taken for other in job.lines
                                           if other.status == APPROVED and other.part_id):
        job.events.append(JobEvent(at=now, status=WORKING, text="The part is here. " + PUBLIC_TEXT[WORKING]))
        job.status = WORKING
    s.commit()


def receive(s, part, quantity, reason, now, by):
    """Add stock (a delivery) or count it (a negative change after a stock-take)."""
    if quantity == 0:
        raise JobError("Change the stock by at least 1.")
    if quantity < 0:
        changed = s.execute(update(Part).where(Part.id == part.id, Part.stock >= -quantity)
                            .values(stock=Part.stock + quantity)).rowcount
        if changed != 1:
            s.rollback()
            raise JobError("Stock can't go below zero.")
    else:
        s.execute(update(Part).where(Part.id == part.id).values(stock=Part.stock + quantity))
    s.add(StockMove(part_id=part.id, change=quantity, reason=(reason or ("Received" if quantity > 0 else "Counted"))
                    .strip()[:120], at=now, by_id=by.id if by else None))
    s.commit()
    s.refresh(part)


# ---------- Vehicle history ----------

def add_months(day, months):
    """date + months, keeping the day where it can: 31 Jan + 1 month = 28 Feb (or 29)."""
    import calendar
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


def next_due(visits, service_lookup):
    """For each service with a reminder rule: (name, last date, last km, due date, due km).
    visits: the vehicle's checked-in appointments, newest first; the newest one with the
    service counts. Due is "after N months or N km, whichever comes first"."""
    from .dates import to_local
    found = {}
    for visit in visits:
        for line in visit.lines:
            service = service_lookup.get(line.service_id)
            if service is None or not (service.again_months or service.again_km) or service.id in found:
                continue
            day = to_local(visit.starts_at, "Asia/Manila").date()
            found[service.id] = (service.name, day, visit.mileage_in,
                                 add_months(day, service.again_months) if service.again_months else None,
                                 visit.mileage_in + service.again_km if service.again_km and visit.mileage_in else None)
    return sorted(found.values(), key=lambda row: row[3] or day)


def is_overdue(due_day, today):
    return due_day is not None and due_day <= today


def due_soon(due_day, today, days=30):
    return due_day is not None and today < due_day <= today + timedelta(days=days)


def jobs_for_board(s, shop, mechanic_id=None):
    """Live jobs (not released), oldest first, for the Jobs board."""
    query = (select(Job).join(Appointment, Job.appointment_id == Appointment.id)
             .where(Job.shop_id == shop.id, Job.status != RELEASED).order_by(Job.created_at))
    if mechanic_id:
        query = query.where(Appointment.mechanic_id == mechanic_id)
    return s.scalars(query).all()
