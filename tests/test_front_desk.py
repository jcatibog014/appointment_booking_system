"""The front desk: check-in, no-shows, walk-ins, the timeline, the calendar, and the
bookings and customer lists."""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from shiromoto import booking, db, seed, slots, timeline
from shiromoto.dates import utcnow
from shiromoto.models import ADVISOR, BOOKED, CHECKED_IN, MECHANIC, NO_SHOW, Appointment, BookedSlot, Customer, \
    Service, Shop, Skill, User

from .conftest import find, log_in_as, make_user, post

MON = date(2026, 11, 9)
H = 60


def manila(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute) - timedelta(hours=8)


BEFORE = manila(MON - timedelta(days=2), 9)


@pytest.fixture
def shop_id(app):
    """The starter shop (Lift 1, Moto Stand 1) with Jun, who has every skill."""
    with app.app_context():
        s = db.session()
        shop = seed.starter_shop(s, "ShiroMoto Garage", BEFORE)
        jun = User(shop_id=shop.id, email="jun@shop.example", name="Jun", role=MECHANIC, password_hash="x",
                   created_at=BEFORE)
        jun.skills = list(s.scalars(select(Skill).where(Skill.shop_id == shop.id)))
        s.add(jun)
        s.commit()
        return shop.id


def oil(s, shop_id):
    return [s.scalar(select(Service).where(Service.shop_id == shop_id, Service.name == "Car Oil Change"))]


def booked(s, shop, start=8 * H, key="k1", plate="ABC 1234", phone="0917 555 0142", name="Carlo Mendoza"):
    held = booking.hold(s, shop, oil(s, shop.id), "car", MON, start, BEFORE)
    return booking.confirm(s, shop, held, {"name": name, "phone": phone, "plate": plate, "make": "Toyota",
                                           "model": "Vios"}, key, BEFORE)


def cells(s, appointment):
    return s.scalar(select(func.count()).select_from(BookedSlot).where(BookedSlot.appointment_id == appointment.id))


# ---------- Check in ----------

def test_check_in_window(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    visit = booked(ctx, shop)
    with pytest.raises(booking.BookingError, match="too early"):
        booking.check_in(ctx, shop, visit, manila(MON, 5, 59))
    booking.check_in(ctx, shop, visit, manila(MON, 7, 50), mileage=42150)
    assert visit.status == CHECKED_IN and visit.mileage_in == 42150 and visit.checked_in_at == manila(MON, 7, 50)
    assert cells(ctx, visit) == 8  # it still holds the bay and the mechanic
    with pytest.raises(booking.BookingError, match="Only a booked visit"):
        booking.check_in(ctx, shop, visit, manila(MON, 8))


def test_checking_in_after_the_time_is_over_is_refused(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    visit = booked(ctx, shop)
    with pytest.raises(booking.BookingError, match="time is over"):
        booking.check_in(ctx, shop, visit, manila(MON, 9))


# ---------- No-shows ----------

def test_a_no_show_waits_15_minutes_then_frees_the_rest(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    visit = booked(ctx, shop)
    with pytest.raises(booking.BookingError, match="15 minutes"):
        booking.mark_no_show(ctx, shop, visit, manila(MON, 8, 10))
    booking.mark_no_show(ctx, shop, visit, manila(MON, 8, 20))
    assert visit.status == NO_SHOW
    # 8:00 and the 8:15 cells stay (the past); 8:15 is the quarter hour of "now", so it is freed:
    # cells from 8:15 on are given back, for the bay and the mechanic.
    assert cells(ctx, visit) == 2


def test_a_walk_in_can_take_a_no_shows_time(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    visit = booked(ctx, shop)
    booking.mark_no_show(ctx, shop, visit, manila(MON, 8, 20))
    jun = ctx.scalar(select(User).where(User.role == MECHANIC))
    walk_in = booking.hold(ctx, shop, oil(ctx, shop_id), "car", MON, 8 * H + 30, manila(MON, 8, 25), by=jun)
    assert walk_in.bay_id == visit.bay_id  # the same lift, straight away


def test_undoing_a_no_show(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    visit = booked(ctx, shop)
    booking.mark_no_show(ctx, shop, visit, manila(MON, 8, 20))
    booking.undo_no_show(ctx, shop, visit, manila(MON, 8, 25))
    assert visit.status == BOOKED and visit.no_show_at is None and cells(ctx, visit) == 8


def test_undo_fails_when_the_time_was_given_away(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    visit = booked(ctx, shop)
    booking.mark_no_show(ctx, shop, visit, manila(MON, 8, 20))
    owner = User(shop_id=shop_id, email="o@shop.example", name="Owner", role="owner", password_hash="x",
                 created_at=BEFORE)
    ctx.add(owner)
    ctx.commit()
    held = booking.hold(ctx, shop, oil(ctx, shop_id), "car", MON, 8 * H + 30, manila(MON, 8, 25), by=owner)
    assert held is not None
    with pytest.raises(booking.Taken, match="can't be undone"):
        booking.undo_no_show(ctx, shop, visit, manila(MON, 8, 26))
    ctx.refresh(visit)
    assert visit.status == NO_SHOW  # nothing changed


# ---------- Staff book from right now ----------

def test_staff_skip_the_notice_but_customers_dont(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    now = manila(MON, 9, 10)
    with pytest.raises(booking.Taken):
        booking.hold(ctx, shop, oil(ctx, shop_id), "car", MON, 9 * H + 15, now)  # a customer: 2 h notice
    jun = ctx.scalar(select(User).where(User.role == MECHANIC))
    held = booking.hold(ctx, shop, oil(ctx, shop_id), "car", MON, 9 * H + 15, now, by=jun)
    assert held.starts_at == manila(MON, 9, 15)
    with pytest.raises(booking.Taken):
        booking.hold(ctx, shop, oil(ctx, shop_id), "car", MON, 9 * H, now, by=jun)  # but never in the past


# ---------- The timeline ----------

def test_the_timeline_places_bookings_in_quarter_hour_columns(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    booked(ctx, shop, start=10 * H)
    board = timeline.build(ctx, shop, MON, BEFORE)
    assert board.opens == 8 * H and board.closes == 17 * H and board.last == 38  # 36 quarter hours + name column
    lift = next(row for row in board.rows if row.name == "Lift 1")
    block = next(b for b in lift.blocks if b.kind == "booking")
    assert (block.gs, block.ge) == (10, 14)  # 10:00 is column 10; one hour is 4 columns
    lunch = next(b for b in lift.blocks if b.kind == "break")
    assert (lunch.gs, lunch.ge) == (18, 22)
    assert [label for label, _, _ in board.hours][:2] == ["8 AM", "9 AM"]


def test_the_timeline_by_mechanic_and_on_a_closed_day(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    booked(ctx, shop)
    board = timeline.build(ctx, shop, MON, BEFORE, by="mechanic")
    assert [row.name for row in board.rows] == ["Jun"] and any(b.kind == "booking" for b in board.rows[0].blocks)
    assert timeline.build(ctx, shop, MON - timedelta(days=1), BEFORE).closed == "Closed on Sundays"


def test_the_timeline_shows_holds(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    booking.hold(ctx, shop, oil(ctx, shop_id), "car", MON, 9 * H, BEFORE)
    board = timeline.build(ctx, shop, MON, BEFORE)
    assert any(b.kind == "hold" for row in board.rows for b in row.blocks)


def test_the_css_has_a_column_rule_for_every_quarter_hour():
    from pathlib import Path
    css = (Path(__file__).parent.parent / "shiromoto" / "static" / "style.css").read_text(encoding="utf-8")
    for n in (1, 38, timeline.MAX_COLUMNS + 2):
        assert f".gs-{n}{{grid-column-start:{n}}}" in css and f".ge-{n}{{grid-column-end:{n}}}" in css


# ---------- Pages ----------

@pytest.fixture
def desk(app, shop_id):
    """A service advisor's browser, and a few bookings."""
    with app.app_context():
        s = db.session()
        shop = s.get(Shop, shop_id)
        booked(s, shop, 8 * H, "a")
        booked(s, shop, 10 * H, "b", plate="XYZ 9876", phone="0918 222 3401", name="Liza Ramos")
    return log_in_as(app.test_client(), make_user(app, shop_id, "mika@shop.example", ADVISOR, "Mika Reyes"))


def test_the_calendar_day_and_week(desk):
    day = desk.get(f"/desk/calendar?day={MON}").get_data(as_text=True)
    assert "Monday, 9 November 2026" in day and "ABC 1234" in day and "XYZ 9876" in day and "Day after" in day
    by_mechanic = desk.get(f"/desk/calendar?day={MON}&by=mechanic").get_data(as_text=True)
    assert "Jun" in by_mechanic
    week = desk.get(f"/desk/calendar?view=week&day={MON + timedelta(days=3)}").get_data(as_text=True)
    assert "Mon 9 Nov" in week and "2 bookings" in week and "Week after" in week
    assert desk.get("/desk/calendar?day=nonsense").status_code == 200


def test_searching_bookings(desk):
    def found(query):
        return desk.get(f"/desk/bookings?{query}").get_data(as_text=True)
    assert "Carlo Mendoza" in found("q=carlo") and "Liza Ramos" not in found("q=carlo")
    assert "Liza Ramos" in found("q=xyz9876") and "Liza Ramos" in found("q=0918 222")
    assert "Liza Ramos" in found("q=%2B63 918 222 3401")  # the same number, written the other way
    assert "Nothing Found" in found("q=nobody")
    assert "Nothing Found" in found("status=no_show")
    assert "2 bookings" in found(f"from={MON}&to={MON}")
    assert found("q=%25") and found("status=bogus&page=-3")  # odd input never breaks it


def test_the_customer_list_and_page(app, desk):
    page = desk.get("/desk/customers?q=liza").get_data(as_text=True)
    assert "Liza Ramos" in page and "Carlo Mendoza" not in page
    [liza] = find(app, Customer, Customer.name == "Liza Ramos")
    detail = desk.get(f"/desk/customers/{liza.id}").get_data(as_text=True)
    assert "XYZ 9876" in detail and "Shop notes" in detail
    post(desk, f"/desk/customers/{liza.id}", {"name": "Liza Ramos", "phone": "0918 222 3401", "email": "",
                                             "notes": "Prefers text messages."})
    assert find(app, Customer, Customer.id == liza.id)[0].notes == "Prefers text messages."
    assert post(desk, f"/desk/customers/{liza.id}", {"name": "", "phone": "1"}).status_code == 400


def test_the_desk_buttons_through_the_pages(app, desk, monkeypatch):
    [visit] = find(app, Appointment, Appointment.idempotency_key == "a")
    from shiromoto.views import bookings as views
    monkeypatch.setattr(views, "utcnow", lambda: manila(MON, 7, 55))
    post(desk, f"/b/{visit.ref}/check-in", {"version": visit.version, "mileage": "42,150"})
    visit = find(app, Appointment, Appointment.id == visit.id)[0]
    assert visit.status == CHECKED_IN and visit.mileage_in == 42150
    [late] = find(app, Appointment, Appointment.idempotency_key == "b")
    monkeypatch.setattr(views, "utcnow", lambda: manila(MON, 10, 30))
    post(desk, f"/b/{late.ref}/no-show", {"version": late.version, "next": "/desk"})
    assert find(app, Appointment, Appointment.id == late.id)[0].status == NO_SHOW
    page = post(desk, f"/b/{late.ref}/no-show", {"version": 1}, follow_redirects=True).get_data(as_text=True)
    assert "Only a booked visit can be a no-show." in page


def test_only_the_front_desk_uses_the_lists_and_buttons(app, shop_id, desk):
    jun = find(app, User, User.role == MECHANIC)[0]
    mechanic = log_in_as(app.test_client(), jun.id)
    assert mechanic.get("/desk/calendar").status_code == 200
    for page in ("/desk/bookings", "/desk/customers"):
        assert mechanic.get(page).status_code == 403
    [visit] = find(app, Appointment, Appointment.idempotency_key == "a")
    assert post(mechanic, f"/b/{visit.ref}/check-in", {"version": 1}).status_code == 403


def test_today_shows_the_floor_and_the_queue(desk):
    page = desk.get("/desk").get_data(as_text=True)
    assert "The Floor" in page and "The Queue" in page and "By mechanic" in page and "Walk-in or caller" in page


def test_staff_dont_need_notice_on_the_free_times_page(app, desk, shop_id):
    oil_id = find(app, Service, Service.shop_id == shop_id, Service.name == "Car Oil Change")[0].id
    with app.app_context():
        s = db.session()
        s.get(Shop, shop_id).min_notice_minutes = 24 * 60
        s.commit()
    staff_day = desk.get(f"/book?vehicle=car&service={oil_id}").get_data(as_text=True)
    guest_day = app.test_client().get(f"/book?vehicle=car&service={oil_id}").get_data(as_text=True)
    assert "too soon" not in staff_day or "too soon" in guest_day
    assert slots.earliest_minute(find(app, Shop, Shop.id == shop_id)[0], MON, manila(MON, 9, 10), notice=0) == 555
    assert utcnow()  # (the page uses the real clock)
