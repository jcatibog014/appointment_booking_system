"""The demo: each visitor gets their own sample shop, which can never touch the real
shop, with an example on every page it can open, for every role in View As."""
import re
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from shiromoto import auth, db
from shiromoto.dates import utcnow
from shiromoto.models import Appointment, Bay, Customer, Job, Part, Service, Shop, User, Vehicle

from .conftest import CSRF, PASSWORD, find, post

# Words that only appear when a list is empty. A demo page must show none of them.
EMPTY_PAGE_SIGNS = ["No mechanics yet", "No bays yet", "No Services Yet", "Nothing planned", "open as usual",
                    "No holidays, closures or leave", "No skills set", "services yet",
                    "No services for this vehicle", "No bookings coming up", "Add your car or motorcycle",
                    "Nothing Found", "No Customers Found", "No bookings yet.", "No parts yet",
                    "No changes yet.", "No visits yet.", "Nobody Is Waiting", "No visits came in during this period", "No bookings in this period"]


def start_demo(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    assert post(client, "/demo").status_code == 302
    return client


def visitor(client):
    with client.session_transaction() as session:
        user_id = session["user_id"]
    with client.application.app_context():
        return db.session().get(User, user_id)


def person(app, shop_id, role):
    return find(app, User, User.shop_id == shop_id, User.role == role)[0]


def view_as(client, user):
    return post(client, "/demo/view-as", {"person": user.id})


@pytest.fixture
def demo(app):
    return start_demo(app)


def test_try_demo_opens_a_sample_shop_as_its_owner(app, demo):
    me = visitor(demo)
    [shop] = find(app, Shop, Shop.id == me.shop_id)
    assert shop.is_demo and shop.demo_expires_at > utcnow() and me.role == "owner" and me.name == "Hiro Navarro"
    assert len(find(app, Bay, Bay.shop_id == shop.id)) == 7
    page = demo.get("/desk").get_data(as_text=True)
    assert "Demo shop." in page and "View as" in page and "Jun dela Cruz" in page


def test_two_visitors_get_two_separate_shops(app):
    first, second = start_demo(app), start_demo(app)
    one, two = visitor(first), visitor(second)
    assert one.shop_id != two.shop_id
    service = find(app, Service, Service.shop_id == one.shop_id)[0]
    assert second.get(f"/settings/services/{service.id}").status_code == 404


def test_the_demo_never_touches_the_real_shop(app, shop_id, demo):
    [pms] = find(app, Service, Service.shop_id == visitor(demo).shop_id, Service.name == "Car PMS")
    post(demo, f"/settings/services/{pms.id}", {"name": "Car PMS", "category": "Maintenance", "price_from": "1",
                                                "vehicle_kind": "car", "bay_type_id": pms.bay_type_id,
                                                "work_minutes": "180", "buffer_minutes": "15", "is_active": "1"})
    [real] = find(app, Service, Service.shop_id == shop_id, Service.name == "Car PMS")
    assert real.price_from == 350000
    # Visitors without a login see the real shop, not a demo.
    assert "₱3,500" in app.test_client().get("/").get_data(as_text=True)


def test_demo_people_cant_log_in_with_the_form(app, demo):
    me = visitor(demo)
    with app.app_context():
        assert auth.find_user(db.session(), me.email) is None
    assert post(app.test_client(), "/login", {"email": me.email, "password": PASSWORD}).status_code == 401


def test_the_demo_cant_change_a_password(demo):
    response = post(demo, "/settings", {"action": "password", "current_password": "x",
                                        "new_password": "whatever123", "confirm_password": "whatever123"})
    assert response.status_code == 403


def test_view_as_switches_who_you_are(app, demo):
    shop_id = visitor(demo).shop_id
    mika = person(app, shop_id, "advisor")
    assert view_as(demo, mika).headers["Location"] == "/desk"
    page = demo.get("/desk").get_data(as_text=True)
    assert "Mika" in page and "Finish Setting Up" not in page
    assert demo.get("/settings/services").status_code == 403  # an advisor can't
    carlo = person(app, shop_id, "customer")
    assert view_as(demo, carlo).headers["Location"] == "/garage"
    assert demo.get("/desk").status_code == 403
    # Back to the owner.
    view_as(demo, visitor(demo))
    assert demo.get("/settings/services").status_code == 200


def test_view_as_only_reaches_your_own_sample_people(app, owner_id):
    demo = start_demo(app)
    other = visitor(start_demo(app))
    for target_id in (other.id, owner_id):
        post(demo, "/demo/view-as", {"person": target_id})
        with demo.session_transaction() as session:
            assert session.get("view_as") is None
        assert visitor(demo).role == "owner"
    # Outside the demo, View As doesn't exist.
    stranger = app.test_client()
    with stranger.session_transaction() as session:
        session["csrf"] = CSRF
    assert post(stranger, "/demo/view-as", {"person": other.id}).status_code == 403


def test_an_old_demo_is_logged_out_and_deleted(app, demo):
    shop_id = visitor(demo).shop_id
    with app.app_context():
        s = db.session()
        s.get(Shop, shop_id).demo_expires_at = utcnow() - timedelta(minutes=1)
        s.commit()
    assert demo.get("/desk").status_code == 302  # back to the login page
    start_demo(app)  # the next visitor's demo clears the old one away
    assert find(app, Shop, Shop.id == shop_id) == []
    assert find(app, User, User.shop_id == shop_id) == []


def test_an_old_demo_cookie_never_opens_a_new_visitors_demo(app):
    """Ids of deleted demos are never given out again, so an old cookie can't match a new person."""
    old = start_demo(app)
    me = visitor(old)
    with app.app_context():
        s = db.session()
        s.get(Shop, me.shop_id).demo_expires_at = utcnow() - timedelta(minutes=1)
        s.commit()
    with old.session_transaction() as session:
        cookie = dict(session)
    new = visitor(start_demo(app))  # deletes the old demo, then makes a new one
    assert new.id != me.id and new.shop_id != me.shop_id
    replay = app.test_client()
    with replay.session_transaction() as session:
        session.update(cookie)
    assert replay.get("/desk").status_code == 302


def test_the_demo_has_a_limit(app, monkeypatch):
    monkeypatch.setattr(auth, "MAX_DEMOS", 1)
    start_demo(app)
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    response = post(client, "/demo")
    assert response.headers["Location"] == "/login"
    with app.app_context():
        assert db.session().scalar(select(func.count()).select_from(Shop)) == 1


# ---------- Every page has an example, for every role ----------

def pages_for(app, role, shop_id):
    pms = find(app, Service, Service.shop_id == shop_id, Service.name == "Car PMS")[0]
    pages = ["/", "/settings", "/book", "/book?vehicle=car", "/book?vehicle=moto", f"/book?vehicle=car&service={pms.id}"]
    first_booking = find(app, Appointment, Appointment.shop_id == shop_id, Appointment.status == "booked")[0]
    if role != "customer":
        pages += ["/desk", f"/b/{first_booking.ref}", "/desk/calendar", "/desk/calendar?view=week",
                  "/desk/calendar?by=mechanic"]
    if role in ("owner", "advisor"):
        carlo = find(app, Customer, Customer.shop_id == shop_id, Customer.name == "Carlo Mendoza")[0]
        pages += ["/desk/bookings", "/desk/customers", f"/desk/customers/{carlo.id}", "/desk/parts",
                  f"/desk/parts/{find(app, Part, Part.shop_id == shop_id)[0].id}",
                  f"/desk/vehicles/{find(app, Vehicle, Vehicle.shop_id == shop_id)[0].id}", "/desk/waitlist"]
    if role == "owner":
        pages += ["/desk/insights", "/desk/insights?days=7", "/desk/insights?days=90"]
    if role != "customer":
        pages += ["/desk/jobs"] + [f"/jobs/{job.id}" for job in find(app, Job, Job.shop_id == shop_id)[:3]]
    if role == "customer":
        carlos = find(app, Appointment, Appointment.shop_id == shop_id, Appointment.customer.has(user_id=find(
            app, User, User.shop_id == shop_id, User.role == "customer")[0].id))
        pages += ["/garage"] + [f"/b/{item.ref}" for item in carlos]
    if role == "owner":
        service = find(app, Service, Service.shop_id == shop_id)[0]
        mechanic = person(app, shop_id, "mechanic")
        pages += ["/settings/shop", "/settings/hours", "/settings/closures", "/settings/bays", "/settings/services",
                  f"/settings/services/{service.id}", "/settings/services/new", "/settings/staff",
                  f"/settings/staff/{mechanic.id}"]
    return pages


@pytest.mark.parametrize("role", ["owner", "advisor", "mechanic", "customer"])
def test_no_page_the_demo_can_open_is_empty(app, demo, role):
    shop_id = visitor(demo).shop_id
    view_as(demo, person(app, shop_id, role))
    for page in pages_for(app, role, shop_id):
        response = demo.get(page)
        assert response.status_code == 200, page
        text = response.get_data(as_text=True)
        for sign in EMPTY_PAGE_SIGNS:
            assert sign not in text, f"{page} as {role} shows '{sign}'"


def test_the_demo_shows_free_times_with_mechanics_and_bays(app, demo):
    pms = find(app, Service, Service.shop_id == visitor(demo).shop_id, Service.name == "Car PMS")[0]
    page = demo.get(f"/book?vehicle=car&service={pms.id}").get_data(as_text=True)
    assert "Free mechanics" in page and "Free bays" in page and "Lift " in page
    customer = person(app, visitor(demo).shop_id, "customer")
    view_as(demo, customer)
    page = demo.get(f"/book?vehicle=car&service={pms.id}").get_data(as_text=True)
    assert "Ready around" in page and "Free bays" not in page


def test_the_demo_customer_has_an_estimate_to_approve(app, demo):
    view_as(demo, person(app, visitor(demo).shop_id, "customer"))
    garage = demo.get("/garage").get_data(as_text=True)
    assert "In the Shop" in garage and "Waiting for approval" in garage
    ref = re.search(r'href="/b/(SM-[A-Z0-9]+)">Toyota Vios', garage).group(1)
    page = demo.get(f"/b/{ref}").get_data(as_text=True)
    assert "Resurface the front rotors" in page and "Approve" in page


def test_the_demo_shows_leave_a_part_day_closure_and_a_day_off(app, demo):
    closures = demo.get("/settings/closures").get_data(as_text=True)
    assert "Family trip to Baguio" in closures and "Staff training" in closures and "1:00 PM" in closures
    assert "Last 30 Days" in closures and "Sick leave" in closures
    ana = person(app, visitor(demo).shop_id, "mechanic")  # first mechanic by id is Jun
    assert demo.get(f"/settings/staff/{ana.id}").status_code == 200


def test_the_demo_shows_the_other_service_both_ways(app, demo):
    """The desk has a request to price, and Carlo has a price to confirm or decline."""
    shop_id = visitor(demo).shop_id
    today = demo.get("/desk").get_data(as_text=True)
    assert "Other Services to Price" in today and "The horn stopped working" in today
    view_as(demo, person(app, shop_id, "customer"))
    garage = demo.get("/garage").get_data(as_text=True)
    assert "Price to confirm" in garage
    priced = find(app, Appointment, Appointment.shop_id == shop_id, Appointment.quote_status == "priced")[0]
    page = demo.get(f"/b/{priced.ref}").get_data(as_text=True)
    assert "Confirm ₱850" in page and "Rattle from the front fork" in page and "Decline" in page


def test_the_demo_has_a_move_request_both_ways(app, demo):
    """The desk has a move request to answer, and Carlo sees his waiting for the shop."""
    shop_id = visitor(demo).shop_id
    today = demo.get("/desk").get_data(as_text=True)
    assert "Move Requests" in today and "Carlo Mendoza" in today
    asked = find(app, Appointment, Appointment.shop_id == shop_id, Appointment.move_status == "asked")[0]
    assert "Approve the new time" in demo.get(f"/b/{asked.ref}").get_data(as_text=True)
    view_as(demo, person(app, shop_id, "customer"))
    assert "Move requested" in demo.get("/garage").get_data(as_text=True)
    page = demo.get(f"/b/{asked.ref}").get_data(as_text=True)
    assert "Waiting for the shop" in page and "Withdraw the request" in page and "Ask for another time" not in page
