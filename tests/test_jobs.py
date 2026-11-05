"""Jobs after check-in: the road to release, estimates the customer decides, parts
taken from stock without ever going below zero, photos, and vehicle history."""
import threading
from datetime import date, datetime, timedelta
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import select

from shiromoto import booking, db, jobs, photos, seed
from shiromoto.models import ADVISOR, APPROVED, AWAITING, CHECKED, DECLINED, INSPECTING, LABOR, MECHANIC, PART, \
    PROPOSED, READY, RELEASED, WAITING_PARTS, WORKING, Appointment, EstimateLine, Job, Part, Service, Shop, Skill, \
    StockMove, User

from .conftest import CSRF, find, log_in_as, make_user, post

MON = date(2026, 11, 9)


def manila(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute) - timedelta(hours=8)


BEFORE = manila(MON - timedelta(days=2), 9)
AT = manila(MON, 7, 55)


@pytest.fixture
def shop_id(app):
    with app.app_context():
        s = db.session()
        shop = seed.starter_shop(s, "ShiroMoto Garage", BEFORE)
        jun = User(shop_id=shop.id, email="jun@shop.example", name="Jun", role=MECHANIC, password_hash="x",
                   created_at=BEFORE)
        jun.skills = list(s.scalars(select(Skill).where(Skill.shop_id == shop.id)))
        s.add(jun)
        s.add(Part(shop_id=shop.id, name="Brake pads", stock=1, low_level=1, price=220000, cost=145000))
        s.commit()
        return shop.id


def checked_in(s, shop_id, start=8 * 60, key="k", plate="ABC 1234"):
    shop = s.get(Shop, shop_id)
    oil = [s.scalar(select(Service).where(Service.shop_id == shop_id, Service.name == "Car Oil Change"))]
    held = booking.hold(s, shop, oil, "car", MON, start, BEFORE)
    visit = booking.confirm(s, shop, held, {"name": "Carlo", "phone": "0917 555 0142", "plate": plate}, key, BEFORE)
    booking.check_in(s, shop, visit, AT, mileage=40000, fuel="1/2")
    return visit.job


def pads(s, shop_id):
    return s.scalar(select(Part).where(Part.shop_id == shop_id, Part.name == "Brake pads"))


# ---------- The road ----------

def test_check_in_opens_the_job_with_a_checklist(ctx, shop_id):
    job = checked_in(ctx, shop_id)
    assert job.status == CHECKED and job.fuel_level == "1/2"
    assert len(job.checklist) == 6 and job.events[0].text == "Your vehicle is checked in."


def test_a_job_only_moves_along_the_road(ctx, shop_id):
    job = checked_in(ctx, shop_id)
    with pytest.raises(jobs.JobError, match="can't go to"):
        jobs.move(ctx, job, RELEASED, AT)
    for status in (INSPECTING, WORKING, READY, RELEASED):
        jobs.move(ctx, job, status, AT, note="Ready after 3 PM." if status == READY else "")
    assert job.status == RELEASED and job.released_at == AT
    assert "Ready after 3 PM." in job.events[-2].text
    assert jobs.MOVES[RELEASED] == ()


def test_an_old_page_cant_move_a_job(ctx, shop_id):
    job = checked_in(ctx, shop_id)
    seen = job.version
    jobs.move(ctx, job, INSPECTING, AT, version=seen)
    with pytest.raises(booking.Changed):
        jobs.move(ctx, job, WORKING, AT, version=seen)


# ---------- The estimate ----------

def test_a_finding_waits_for_the_customer(ctx, shop_id):
    job = checked_in(ctx, shop_id)
    jobs.move(ctx, job, INSPECTING, AT)
    jobs.add_line(ctx, job, LABOR, "Resurface rotors", "They have grooves.", 1, 120000, AT)
    assert job.status == AWAITING
    jobs.move(ctx, job, WORKING, AT)  # the booked work can go on meanwhile...
    with pytest.raises(jobs.JobError, match="still has estimate lines"):
        jobs.move(ctx, job, READY, AT)  # ...but it can't be ready before the customer decides
    line = job.lines[0]
    jobs.decide(ctx, job, line.id, True, AT, "the customer")
    assert line.status == APPROVED and job.status == WORKING and job.total() == 120000


def test_a_line_is_decided_once(ctx, shop_id):
    job = checked_in(ctx, shop_id)
    line = jobs.add_line(ctx, job, LABOR, "Resurface rotors", "", 1, 120000, AT)
    jobs.decide(ctx, job, line.id, False, AT, "the customer")
    with pytest.raises(jobs.JobError, match="already decided"):
        jobs.decide(ctx, job, line.id, True, AT, "Mika")
    assert line.status == DECLINED and job.total() == 0


def test_approving_a_part_takes_it_from_stock(ctx, shop_id):
    job = checked_in(ctx, shop_id)
    part = pads(ctx, shop_id)
    line = jobs.add_line(ctx, job, PART, "", "Worn to 2 mm.", 1, part.price, AT, part)
    assert line.description == "Brake pads"
    jobs.decide(ctx, job, line.id, True, AT, "the customer")
    ctx.refresh(part)
    assert part.stock == 0 and line.stock_taken and job.status == WORKING
    [used] = ctx.scalars(select(StockMove).where(StockMove.part_id == part.id)).all()
    assert used.change == -1 and used.job_id == job.id


def test_not_enough_stock_means_waiting_for_parts(ctx, shop_id):
    job = checked_in(ctx, shop_id)
    part = pads(ctx, shop_id)
    line = jobs.add_line(ctx, job, PART, "", "", 2, part.price, AT, part)  # 2 wanted, 1 in stock
    jobs.decide(ctx, job, line.id, True, AT, "the customer")
    ctx.refresh(part)
    assert part.stock == 1 and not line.stock_taken and job.status == WAITING_PARTS
    with pytest.raises(jobs.JobError, match="still aren't enough"):
        jobs.take_now(ctx, job, line, AT, None)
    jobs.receive(ctx, part, 5, "Delivery", AT, None)
    jobs.take_now(ctx, job, line, AT, None)
    ctx.refresh(part)
    assert part.stock == 4 and line.stock_taken and job.status == WORKING


def test_stock_never_goes_below_zero(ctx, shop_id):
    part = pads(ctx, shop_id)
    with pytest.raises(jobs.JobError, match="below zero"):
        jobs.receive(ctx, part, -2, "Counted", AT, None)
    jobs.receive(ctx, part, -1, "Counted", AT, None)
    assert part.stock == 0
    from sqlalchemy.exc import IntegrityError
    part.stock = -1
    with pytest.raises(IntegrityError):  # the CHECK rule in the database, too
        ctx.commit()
    ctx.rollback()


def test_two_jobs_race_for_the_last_part(app, shop_id):
    """Two customers approve brake pads at the same moment; there is one in stock.
    Exactly one job gets it, the other waits for parts, and stock ends at 0."""
    with app.app_context():
        s = db.session()
        part = pads(s, shop_id)
        first, second = checked_in(s, shop_id, key="a"), checked_in(s, shop_id, 9 * 60, "b", "XYZ 9876")
        lines = [jobs.add_line(s, job, PART, "", "", 1, part.price, AT, part).id for job in (first, second)]
        job_ids = [first.id, second.id]
    barrier = threading.Barrier(2)
    errors = []

    def approve(job_id, line_id):
        with app.app_context():
            s = db.session()
            job = s.get(Job, job_id)
            barrier.wait()
            try:
                jobs.decide(s, job, line_id, True, AT, "the customer")
            except Exception as error:  # noqa: BLE001
                errors.append(repr(error))
    threads = [threading.Thread(target=approve, args=pair) for pair in zip(job_ids, lines)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    with app.app_context():
        s = db.session()
        assert pads(s, shop_id).stock == 0
        taken = s.scalars(select(EstimateLine).where(EstimateLine.id.in_(lines))).all()
        assert sorted(line.stock_taken for line in taken) == [False, True]
        assert sorted(s.get(Job, job_id).status for job_id in job_ids) == sorted([WORKING, WAITING_PARTS])


def test_finished_jobs_take_no_new_lines(ctx, shop_id):
    job = checked_in(ctx, shop_id)
    for status in (WORKING, READY):
        jobs.move(ctx, job, status, AT)
    with pytest.raises(jobs.JobError, match="finished"):
        jobs.add_line(ctx, job, LABOR, "More", "", 1, 100, AT)


# ---------- Photos ----------

def jpeg_with_gps():
    image = Image.new("RGB", (3000, 2000), (120, 40, 200))
    exif = Image.Exif()
    exif[0x8825] = {2: (14.0, 39.0, 0.0), 4: (121.0, 6.0, 0.0)}  # GPS: somewhere in Marikina
    exif[0x0110] = "Phone X"  # the camera model
    out = BytesIO()
    image.save(out, "JPEG", exif=exif)
    return out.getvalue()


def test_photos_are_smaller_webp_without_location():
    raw = jpeg_with_gps()
    assert b"Phone X" in raw
    data, thumb, width, height = photos.process(raw)
    assert (width, height) == (1600, 1067)
    with Image.open(BytesIO(data)) as image:
        assert image.format == "WEBP" and not image.getexif()
    assert b"Phone X" not in data and len(thumb) < len(data)


def test_only_photos_are_accepted():
    for raw, message in ((b"", "Choose a photo"), (b"%PDF-1.7 not an image", "isn't a photo"),
                         (b"x" * (photos.MAX_UPLOAD + 1), "too big")):
        with pytest.raises(photos.PhotoError, match=message):
            photos.process(raw)
    gif = BytesIO()
    Image.new("RGB", (10, 10)).save(gif, "GIF")
    with pytest.raises(photos.PhotoError, match="JPEG, PNG or WebP"):
        photos.process(gif.getvalue())


# ---------- Vehicle history ----------

def test_add_months_keeps_the_day_where_it_can():
    assert jobs.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert jobs.add_months(date(2027, 12, 31), 2) == date(2028, 2, 29)  # a leap year
    assert jobs.add_months(date(2026, 11, 9), 6) == date(2027, 5, 9)


def test_next_service_due(ctx, shop_id):
    job = checked_in(ctx, shop_id)
    oil = ctx.scalar(select(Service).where(Service.shop_id == shop_id, Service.name == "Car Oil Change"))
    assert (oil.again_months, oil.again_km) == (4, 5000)
    [(name, last_day, last_km, due_day, due_km)] = jobs.next_due([job.appointment], {oil.id: oil})
    assert (name, last_day, last_km, due_day, due_km) == ("Car Oil Change", MON, 40000, date(2027, 3, 9), 45000)


# ---------- Pages ----------

@pytest.fixture
def setup(app, shop_id):
    with app.app_context():
        s = db.session()
        job = checked_in(s, shop_id)
        return {"job": job.id, "ref": job.appointment.ref,
                "desk": log_in_as(app.test_client(), make_user(app, shop_id, "mika@shop.example", ADVISOR, "Mika"))}


def test_the_board_and_the_job_page(app, setup):
    desk = setup["desk"]
    board = desk.get("/desk/jobs").get_data(as_text=True)
    assert "Checked in" in board and "ABC 1234" in board
    page = desk.get(f"/jobs/{setup['job']}").get_data(as_text=True)
    assert "Check-in Checklist" in page and "Add work or a part" in page and "Inspecting" in page
    post(desk, f"/jobs/{setup['job']}/move", {"status": "inspecting", "version": 1})
    assert find(app, Job, Job.id == setup["job"])[0].status == INSPECTING


def test_staff_propose_and_the_customer_approves_from_the_link(app, setup):
    desk = setup["desk"]
    post(desk, f"/jobs/{setup['job']}/lines", {"kind": "labor", "description": "Resurface rotors",
                                                "reason": "Grooves.", "quantity": "1", "unit_price": "1,200"})
    [line] = find(app, EstimateLine)
    assert line.unit_price == 120000 and line.status == PROPOSED
    stranger = app.test_client()
    with stranger.session_transaction() as session:
        session["csrf"] = CSRF
    assert post(stranger, f"/b/{setup['ref']}/estimate/{line.id}", {"decision": "approve"}).status_code == 404
    with app.app_context():
        visit = db.session().scalar(select(Appointment).where(Appointment.ref == setup["ref"]))
        token = "t0ken"
        visit.token_hash = booking.token_hash(token)
        db.session().commit()
    customer = app.test_client()
    with customer.session_transaction() as session:
        session["csrf"] = CSRF
    page = customer.get(f"/b/{setup['ref']}?t={token}", follow_redirects=True).get_data(as_text=True)
    assert "Resurface rotors" in page and "Approve" in page and "Waiting for approval" in page
    post(customer, f"/b/{setup['ref']}/estimate/{line.id}", {"decision": "approve"})
    assert find(app, EstimateLine)[0].status == APPROVED


def test_photos_through_the_pages(app, setup):
    desk = setup["desk"]
    response = desk.post(f"/jobs/{setup['job']}/photos", data={"csrf_token": CSRF, "caption": "Rear bumper",
                                                              "photo": (BytesIO(jpeg_with_gps()), "car.jpg")},
                         content_type="multipart/form-data")
    assert response.status_code == 302
    with app.app_context():
        [photo_id] = [p.id for p in db.session().get(Job, setup["job"]).photos]
    shown = desk.get(f"/photos/{photo_id}?size=thumb")
    assert shown.mimetype == "image/webp"
    assert app.test_client().get(f"/photos/{photo_id}").status_code == 404  # strangers can't


def test_parts_pages(app, setup):
    desk = setup["desk"]
    post(desk, "/desk/parts", {"name": "Wiper blade", "sku": "WB", "price": "550", "cost": "310", "stock": "3",
                               "low_level": "2"})
    [wiper] = find(app, Part, Part.name == "Wiper blade")
    assert wiper.stock == 3 and wiper.price == 55000
    assert "Low Stock" in desk.get("/desk/parts").get_data(as_text=True)  # the brake pads (1 at or below 1)
    post(desk, f"/desk/parts/{wiper.id}", {"change": "-5"})
    assert find(app, Part, Part.id == wiper.id)[0].stock == 3
    post(desk, f"/desk/parts/{wiper.id}", {"change": "10", "reason": "Delivery"})
    assert find(app, Part, Part.id == wiper.id)[0].stock == 13
    jun = find(app, User, User.role == MECHANIC)[0]
    assert log_in_as(app.test_client(), jun.id).get("/desk/parts").status_code == 403


def test_the_vehicle_page(app, setup):
    with app.app_context():
        vehicle_id = db.session().get(Job, setup["job"]).appointment.vehicle_id
    page = setup["desk"].get(f"/desk/vehicles/{vehicle_id}").get_data(as_text=True)
    assert "Car Oil Change" in page and "45,000 km" in page and "Visits" in page
