"""Insights: every number checked by hand against a small, known week."""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from shiromoto import booking, db, insights, jobs, seed
from shiromoto.models import ADVISOR, LABOR, MECHANIC, Service, Shop, Skill, User

from .conftest import log_in_as, make_user

MON = date(2026, 11, 9)


def manila(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute) - timedelta(hours=8)


BEFORE = manila(MON - timedelta(days=2), 9)
FRIDAY_NIGHT = manila(MON + timedelta(days=4), 20)  # the 7-day period is Sat 7 to Fri 13 Nov


@pytest.fixture
def week(app):
    """Four bookings on Monday: two came in (one with approved extra work), one no-show, one cancelled."""
    with app.app_context():
        s = db.session()
        shop = seed.starter_shop(s, "ShiroMoto Garage", BEFORE)
        jun = User(shop_id=shop.id, email="jun@shop.example", name="Jun", role=MECHANIC, password_hash="x",
                   created_at=BEFORE)
        jun.skills = list(s.scalars(select(Skill).where(Skill.shop_id == shop.id)))
        s.add(jun)
        s.commit()
        oil = [s.scalar(select(Service).where(Service.shop_id == shop.id, Service.name == "Car Oil Change"))]

        def book(start, key, plate):
            held = booking.hold(s, shop, oil, "car", MON, start, BEFORE)
            return booking.confirm(s, shop, held, {"name": "C", "phone": "0917 555 0142", "plate": plate}, key, BEFORE)
        first, second = book(8 * 60, "a", "AAA 111"), book(9 * 60, "b", "BBB 222")
        missed, gone = book(13 * 60, "c", "CCC 333"), book(15 * 60, "d", "DDD 444")
        booking.check_in(s, shop, first, manila(MON, 7, 55))
        booking.check_in(s, shop, second, manila(MON, 8, 55))
        jobs.add_line(s, second.job, LABOR, "Clean throttle body", "", 1, 80000, manila(MON, 9, 30))
        jobs.decide(s, second.job, second.job.lines[0].id, True, manila(MON, 9, 40), "the customer")
        booking.mark_no_show(s, shop, missed, manila(MON, 13, 20))
        booking.cancel(s, shop, gone, "Car sold", BEFORE)
        return shop.id


def build(app, shop_id, days=7):
    with app.app_context():
        s = db.session()
        return insights.build(s, s.get(Shop, shop_id), days, FRIDAY_NIGHT)


def test_the_tiles(app, week):
    data = build(app, week)
    assert (data["first"], data["last"]) == (MON - timedelta(days=2), MON + timedelta(days=4))
    tiles = data["tiles"]
    assert tiles["bookings"] == 3  # the cancelled one doesn't count
    assert tiles["money"] == "₱4,400"  # 2 oil changes (₱1,800 each) + ₱800 approved extra
    assert tiles["no_show_rate"] == "33%"  # 1 of the 3 that were due
    assert tiles["cancel_rate"] == "25%"  # 1 of 4 made
    assert tiles["work"] == "1 h 30 min"  # two 45-minute jobs came in


def test_bookings_per_day(app, week):
    bars = build(app, week)["day_bars"]
    assert [bar.value for bar in bars] == [0, 0, 3, 0, 0, 0, 0]
    monday = bars[2]
    assert monday.label == "Mon 9" and monday.labelled
    assert monday.height == pytest.approx(180 * 3 / 5)  # the axis goes to the next round number, 5


def test_busiest_hours(app, week):
    data = build(app, week)
    monday = dict((name, cells) for name, cells in data["heat_rows"])["Mon"]
    counts = {hour: count for hour, count, _ in monday}
    assert counts[8] == counts[9] == counts[13] == 1 and counts[15] == 0  # the cancelled one isn't busy
    assert data["peak"] == 1 and all(shade == 5 for _, count, shade in monday if count)
    assert "Sun" not in [name for name, _ in data["heat_rows"]]  # closed, and nothing booked


def test_bay_use(app, week):
    use = {bar.label: bar.text for bar in build(app, week)["bay_bars"]}
    # Open: Mon-Fri 8 h each (9 h minus lunch) + Sat 6 h = 46 h. Lift 1 was booked:
    # 2 h (the two oil changes, 1 h each with clean-up) + 15 min (the no-show's start before it was freed).
    assert use["Lift 1"] == f"{100 * 2.25 / 46:.0f}%" and use["Moto Stand 1"] == "0%"


def test_money_by_service_and_mechanic(app, week):
    data = build(app, week)
    services = {bar.label: bar.text for bar in data["service_bars"]}
    assert services == {"Car Oil Change": "₱3,600", "Approved extra work": "₱800"}
    assert {bar.label: bar.text for bar in data["mechanic_bars"]} == {"Jun": "₱4,400"}
    assert data["reasons"] == [("Car sold", 1)]
    assert data["sources"]["online"] == 3


def test_an_empty_period(app, week):
    with app.app_context():
        s = db.session()
        data = insights.build(s, s.get(Shop, week), 7, FRIDAY_NIGHT + timedelta(days=60))
    assert data["tiles"]["bookings"] == 0 and data["tiles"]["no_show_rate"] == "—" and data["peak"] == 0


def test_chart_geometry():
    assert [insights.nice_top(v) for v in (0, 1, 3, 7, 23, 140, 1000)] == [1, 1, 5, 10, 25, 200, 1000]
    path = insights.column_path(10, 50, 20, 130)
    assert path.startswith("M10.0,180.0V54.0") and path.endswith("V180.0Z")  # square at the base, round on top
    assert insights.step(0, 9) == 0 and insights.step(9, 9) == 5 and insights.step(1, 9) == 1


def test_only_the_owner_sees_insights(app, week, owner_id):
    owner = log_in_as(app.test_client(), owner_id)
    page = owner.get("/desk/insights?days=7").get_data(as_text=True)
    assert "Busiest Hours" in page and "Show as a table" in page and "Bay Use" in page
    assert owner.get("/desk/insights?days=999").status_code == 200  # back to 30 days
    advisor = log_in_as(app.test_client(), make_user(app, week, "mika@shop.example", ADVISOR))
    assert advisor.get("/desk/insights").status_code == 403


@pytest.fixture
def owner_id(app, week):
    return make_user(app, week, "owner@shop.example", "owner", "Hiro")
