"""Booking through the pages: hold -> details -> booked, the manage link, moving,
cancelling, accounts and My Garage, and the calendar file."""
import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from shiromoto import calendar_file, db, seed, shops
from shiromoto.dates import local_today, utcnow
from shiromoto.models import BOOKED, CANCELLED, MECHANIC, Appointment, Customer, Hold, Service, Shop, Skill, User

from .conftest import CSRF, find, get, log_in_as, make_user, post

H = 60


@pytest.fixture
def shop_id(app):
    with app.app_context():
        s = db.session()
        shop = seed.starter_shop(s, "ShiroMoto Garage", utcnow())
        jun = User(shop_id=shop.id, email="jun@shop.example", name="Jun", role=MECHANIC, password_hash="x",
                   created_at=utcnow())
        jun.skills = list(s.scalars(select(Skill).where(Skill.shop_id == shop.id)))
        s.add(jun)
        s.commit()
        return shop.id


@pytest.fixture
def day(app, shop_id):
    """The next weekday (not a holiday) at least 2 days away, so notice and cut-offs never get in the way."""
    with app.app_context():
        s = db.session()
        shop = s.get(Shop, shop_id)
        candidate = local_today() + timedelta(days=2)
        while candidate.weekday() > 4 or shops.closures_between(s, shop, candidate, candidate):
            candidate += timedelta(days=1)
        return candidate


def browser(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    return client


def service_id(app, shop_id, name="Car Oil Change"):
    return find(app, Service, Service.shop_id == shop_id, Service.name == name)[0].id


def hold(client, app, shop_id, day, start=8 * H, name="Car Oil Change", vehicle="car"):
    return post(client, "/book/hold", {"vehicle": vehicle, "service": service_id(app, shop_id, name),
                                       "day": day.isoformat(), "start": start})


def guest_details(**changes):
    return {"name": "Carlo Mendoza", "phone": "0917 555 0142", "email": "", "plate": "ABC 1234", "make": "Toyota",
            "model": "Vios", "year": "2019", "notes": "Squeaky brakes", "key": "form-key-1", **changes}


def book(client, app, shop_id, day, start=8 * H, **changes):
    """Hold, then fill in the details. Returns the booking page's response (after the redirect)."""
    response = hold(client, app, shop_id, day, start)
    assert response.status_code == 302 and "/book/details/" in response.headers["Location"]
    return post(client, response.headers["Location"], guest_details(**changes), follow_redirects=True)


def the_booking(app):
    return find(app, Appointment)[0]


# ---------- A guest books ----------

def test_a_guest_books_a_time(app, shop_id, day):
    client = browser(app)
    details = client.get(hold(client, app, shop_id, day).headers["Location"]).get_data(as_text=True)
    assert "Kept for you for" in details and "8:00 AM" in details and "Book 8:00 AM" in details
    page = book(browser(app), app, shop_id, day, start=10 * H).get_data(as_text=True)
    assert "Booked! Your booking number is SM-" in page
    assert "Keep this link." in page and "/b/SM-" in page and "?t=" in page
    appointment = find(app, Appointment, Appointment.notes == "Squeaky brakes")[0]
    assert appointment.status == BOOKED and appointment.source == "online"


def test_pressing_book_twice_makes_one_booking(app, shop_id, day):
    client = browser(app)
    details_url = hold(client, app, shop_id, day).headers["Location"]
    first = post(client, details_url, guest_details())
    second = post(client, details_url, guest_details())  # the hold is used up, but the key is the same
    assert first.headers["Location"] == second.headers["Location"]
    assert len(find(app, Appointment)) == 1


def test_a_time_someone_else_holds_is_refused(app, shop_id, day):
    hold(browser(app), app, shop_id, day)
    client = browser(app)
    response = hold(client, app, shop_id, day)
    assert response.status_code == 302 and response.headers["Location"].startswith("/book?")
    assert "That time was just taken" in client.get(response.headers["Location"]).get_data(as_text=True)
    assert len(find(app, Hold)) == 1


def test_one_browser_holds_one_time(app, shop_id, day):
    client = browser(app)
    hold(client, app, shop_id, day, 8 * H)
    hold(client, app, shop_id, day, 10 * H)
    [held] = find(app, Hold)
    assert held.starts_at.hour == 2  # 10:00 in Manila is 02:00 UTC: the 8:00 hold was given back


def test_giving_a_hold_back(app, shop_id, day):
    client = browser(app)
    details_url = hold(client, app, shop_id, day).headers["Location"]
    response = post(client, details_url + "/release")
    assert response.status_code == 302 and find(app, Hold) == []


def test_an_old_hold_link_explains_what_happened(app, shop_id, day):
    client = browser(app)
    response = client.get("/book/details/made-up-token", follow_redirects=True)
    assert "Your held time ran out" in response.get_data(as_text=True)


def test_the_details_form_checks_its_fields(app, shop_id, day):
    client = browser(app)
    details_url = hold(client, app, shop_id, day).headers["Location"]
    response = post(client, details_url, guest_details(phone="12", plate="", year="nineteen"))
    text = response.get_data(as_text=True)
    assert response.status_code == 400
    assert "Enter a mobile number" in text and "Enter the plate number." in text and "Write the year like 2019." in text
    assert find(app, Appointment) == []


# ---------- The manage link ----------

def test_only_the_link_opens_a_guests_booking(app, shop_id, day):
    client = browser(app)
    book(client, app, shop_id, day)
    appointment = the_booking(app)
    assert client.get(f"/b/{appointment.ref}").status_code == 200  # this browser remembers it
    stranger = browser(app)
    assert stranger.get(f"/b/{appointment.ref}").status_code == 404
    assert stranger.get(f"/b/{appointment.ref}?t=wrong").status_code == 404
    page = client.get(f"/b/{appointment.ref}").get_data(as_text=True)
    token = re.search(r"\?t=([\w-]+)", page).group(1)
    opened = stranger.get(f"/b/{appointment.ref}?t={token}")
    assert opened.status_code == 302  # the secret is moved out of the address bar...
    assert stranger.get(f"/b/{appointment.ref}").status_code == 200  # ...and remembered


def test_staff_open_any_booking(app, shop_id, day, owner_id):
    book(browser(app), app, shop_id, day)
    owner = log_in_as(app.test_client(), owner_id)
    page = owner.get(f"/b/{the_booking(app).ref}").get_data(as_text=True)
    assert "Mechanic and bay" in page and "Jun · Lift 1" in page and "0917 555 0142" in page


# ---------- Move and cancel ----------

def test_a_guest_asks_for_a_new_time_and_staff_approve_it(app, shop_id, day):
    client = browser(app)
    book(client, app, shop_id, day)
    appointment = the_booking(app)
    response = client.get(f"/b/{appointment.ref}/move")
    assert response.headers["Location"] == f"/book?move={appointment.ref}"
    times = client.get(response.headers["Location"] + f"&day={day}").get_data(as_text=True)
    assert "Ask for a New Time" in times and "Keep the current time" in times
    confirm = client.get(f"/b/{appointment.ref}/move/confirm?day={day}&start={9 * H}").get_data(as_text=True)
    assert "9:00 AM" in confirm and "Send the request" in confirm
    page = post(client, f"/b/{appointment.ref}/move/confirm", {"day": day.isoformat(), "start": 9 * H, "version": 1,
                                                                "note": "After work"}, follow_redirects=True)
    assert "Request sent" in page.get_data(as_text=True) and "Waiting for the shop" in page.get_data(as_text=True)
    asked = the_booking(app)
    assert asked.starts_at.hour == 0 and asked.move_status == "asked"  # still 8:00 Manila (00:00 UTC)
    staff = log_in_as(app.test_client(), find(app, User, User.name == "Jun")[0].id)
    post(staff, f"/b/{appointment.ref}/move/answer", {"answer": "approve"})
    moved = the_booking(app)
    assert moved.starts_at.hour == 1 and moved.move_status == "approved"  # 9:00 Manila = 01:00 UTC


def test_an_old_page_cant_cancel_after_a_move_request(app, shop_id, day):
    client = browser(app)
    book(client, app, shop_id, day)
    ref = the_booking(app).ref
    post(client, f"/b/{ref}/move/confirm", {"day": day.isoformat(), "start": 9 * H, "version": 1})
    page = post(client, f"/b/{ref}/cancel", {"version": 1}, follow_redirects=True).get_data(as_text=True)
    assert "Someone else changed this booking" in page
    assert the_booking(app).status == BOOKED


def test_a_guest_cancels(app, shop_id, day):
    client = browser(app)
    book(client, app, shop_id, day)
    appointment = the_booking(app)
    page = post(client, f"/b/{appointment.ref}/cancel", {"version": 1, "reason": "Car sold"},
                follow_redirects=True).get_data(as_text=True)
    assert "is cancelled" in page and "Car sold" in page
    assert the_booking(app).status == CANCELLED
    # The time is free again for the next person.
    assert book(browser(app), app, shop_id, day, key="another").status_code == 200
    assert len(find(app, Appointment, Appointment.status == BOOKED)) == 1


def test_a_stranger_cant_cancel(app, shop_id, day):
    book(browser(app), app, shop_id, day)
    assert post(browser(app), f"/b/{the_booking(app).ref}/cancel", {"version": 1}).status_code == 404
    assert the_booking(app).status == BOOKED


def test_close_to_the_start_only_the_shop_can_change_it(app, shop_id, day, owner_id):
    client = browser(app)
    book(client, app, shop_id, day)
    with app.app_context():
        s = db.session()
        s.get(Shop, shop_id).cancel_cutoff_hours = 24 * 30  # everything is "too close" now
        s.commit()
    ref = the_booking(app).ref
    page = client.get(f"/b/{ref}").get_data(as_text=True)
    assert "be changed online" in page and "Cancel booking" not in page
    assert "less than" in post(client, f"/b/{ref}/cancel", {"version": 1}, follow_redirects=True).get_data(as_text=True)
    owner = log_in_as(app.test_client(), owner_id)
    post(owner, f"/b/{ref}/cancel", {"version": 1, "reason": "Customer called"})
    assert the_booking(app).status == CANCELLED


# ---------- Accounts and My Garage ----------

def signup(client, **changes):
    return post(client, "/signup", {"name": "Carlo Mendoza", "email": "carlo@mail.example", "phone": "0917 555 0142",
                                    "password": "secret pass", "confirm_password": "secret pass", **changes})


def test_signing_up_opens_my_garage(app, shop_id):
    client = browser(app)
    response = signup(client)
    assert response.headers["Location"] == "/garage"
    page = client.get("/garage").get_data(as_text=True)
    assert "My Garage" in page and "Hello, Carlo" in page and "No bookings coming up" in page
    [customer] = find(app, Customer)
    assert customer.user_id is not None and customer.phone_key == "09175550142"
    assert signup(browser(app)).status_code == 400  # the same email twice


def test_a_customer_adds_a_vehicle_and_books_with_it(app, shop_id, day):
    client = browser(app)
    signup(client)
    post(client, "/garage/vehicles", {"kind": "car", "plate": "nab 4821", "make": "Toyota", "model": "Vios"})
    page = client.get("/garage").get_data(as_text=True)
    assert "NAB 4821" in page
    details_url = hold(client, app, shop_id, day).headers["Location"]
    form = client.get(details_url).get_data(as_text=True)
    assert "Toyota Vios · NAB 4821" in form and 'value="Carlo Mendoza"' in form
    vehicle_id = re.search(r'name="vehicle_id" value="(\d+)"', form).group(1)
    post(client, details_url, {"name": "Carlo Mendoza", "phone": "0917 555 0142", "vehicle_id": vehicle_id,
                               "key": "garage-key"})
    garage = client.get("/garage").get_data(as_text=True)
    assert the_booking(app).ref in garage and "Coming Up" in garage


def test_a_guest_booking_can_be_saved_to_an_account(app, shop_id, day):
    client = browser(app)
    book(client, app, shop_id, day)
    ref = the_booking(app).ref
    signup(client)  # the same browser still remembers the booking
    page = client.get(f"/b/{ref}").get_data(as_text=True)
    assert "Save to My Garage" in page
    post(client, f"/b/{ref}/save")
    assert ref in client.get("/garage").get_data(as_text=True)


def test_customer_pages_are_for_customers(app, shop_id, owner_id):
    assert browser(app).get("/garage").status_code == 302  # to log in
    assert log_in_as(app.test_client(), owner_id).get("/garage").status_code == 403
    other = log_in_as(app.test_client(), make_user(app, shop_id, "zed@mail.example", "customer"))
    assert other.get("/garage").status_code == 200


# ---------- Staff book for a caller ----------

def test_staff_book_for_a_caller(app, shop_id, day, owner_id):
    owner = log_in_as(app.test_client(), owner_id)
    details_url = hold(owner, app, shop_id, day).headers["Location"]
    form = owner.get(details_url).get_data(as_text=True)
    assert "Book for a Customer" in form and "Booked by" in form and "Jun · Lift 1" in form
    post(owner, details_url, guest_details(source="walk_in"))
    appointment = the_booking(app)
    assert appointment.source == "walk_in" and appointment.created_by_id == owner_id
    desk_day = owner.get("/desk").get_data(as_text=True)
    assert "Walk-in or caller" in desk_day


# ---------- The calendar file ----------

def test_the_calendar_file(app, shop_id, day):
    client = browser(app)
    book(client, app, shop_id, day)
    appointment = the_booking(app)
    response = client.get(f"/b/{appointment.ref}/calendar.ics")
    assert response.mimetype == "text/calendar"
    text = response.get_data(as_text=True)
    assert f"DTSTART;TZID=Asia/Manila:{day:%Y%m%d}T080000" in text
    assert f"DTEND;TZID=Asia/Manila:{day:%Y%m%d}T090000" in text  # 45 min + 15 min clean-up
    assert "TZOFFSETTO:+0800" in text and f"UID:{appointment.ref}@shiromoto" in text
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    assert get(app, Appointment, appointment.id).version == 1


def test_calendar_text_is_escaped_and_folded():
    assert calendar_file.escape("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"
    folded = calendar_file.fold("DESCRIPTION:" + "₱" * 40)
    assert all(len(part.encode()) <= 75 for part in folded.split("\r\n"))
    assert folded.replace("\r\n ", "") == "DESCRIPTION:" + "₱" * 40
    assert calendar_file.offset_text(timedelta(hours=-4, minutes=-30)) == "-0430"


def test_a_customer_starts_at_my_garage(app, shop_id):
    """My Garage is a customer's start page; the shop's page is still at /shop. The
    phone app opens /?from=app, so staff start at the desk."""
    customer = log_in_as(app.test_client(), make_user(app, shop_id, "carlo@example.com", "customer", "Carlo"))
    assert customer.get("/").headers["Location"] == "/garage"
    page = customer.get("/shop").get_data(as_text=True)
    assert "Services and Prices" in page and ">Hours<" not in page and ">My Garage<" in page
    assert 'href="/shop">Services<' not in page and 'href="/shop" aria-current="page">Services<' in page
    assert "#services" not in customer.get("/garage").get_data(as_text=True)  # the page's top, not a section
    staff = log_in_as(app.test_client(), make_user(app, shop_id, "hiro@shop.example", "owner", "Hiro"))
    assert staff.get("/").status_code == 200  # Shop Website
    assert staff.get("/?from=app").headers["Location"] == "/desk"
    assert "Next visit" in customer.get("/garage").get_data(as_text=True)


def test_available_slots_lists_only_what_is_open(app, shop_id, day):
    owner = log_in_as(app.test_client(), make_user(app, shop_id, "hiro@shop.example", "owner", "Hiro"))
    page = owner.get(f"/desk/free?day={day}&by=mechanic").get_data(as_text=True)
    # Jun works 8:00 to 5:00 with lunch at 12:00: two slots, and nothing to choose first.
    assert "Available Slots" in page and "Jun" in page and "8 h" in page
    assert "8:00 AM – 12:00 PM" in page and "1:00 PM – 5:00 PM" in page
    assert '2 slots<span class="day-count-more"> available</span>' in page
    assert "Vehicle" not in page and "Choose" not in page
    book(browser(app), app, shop_id, day, start=8 * H)  # an oil change, 8:00 to 9:00
    page = owner.get(f"/desk/free?day={day}&by=bay").get_data(as_text=True)
    assert "9:00 AM – 12:00 PM" in page and "Lift 1" in page
