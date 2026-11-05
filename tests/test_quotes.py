"""The Other Service: the customer writes what they need, books a time, and the shop
sends the price after. The customer confirms or declines it (quotes.py)."""
import importlib.util
import threading
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from shiromoto import booking, db, quotes, seed, shops, waitlist
from shiromoto.dates import local_today, utcnow
from shiromoto.models import ACCEPTED, ADVISOR, BOOKED, CANCELLED, DECLINED, MECHANIC, PRICED, TO_PRICE, \
    Appointment, BayType, BookedSlot, Hold, Notification, Service, Shop, Skill, User

from .conftest import CSRF, find, log_in_as, make_user, post

H = 60
ASKED = "The horn stopped working."


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
    """A weekday at least 2 days away (not a holiday), so notice and cut-offs never get in the way."""
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


def service(app, shop_id, name):
    return find(app, Service, Service.shop_id == shop_id, Service.name == name)[0]


def make_booking(app, shop_id, day, names=("Other Car Service",), start=8 * H, key="k1", request=ASKED):
    """Book through booking.hold and booking.confirm. Returns the appointment's id."""
    with app.app_context():
        s = db.session()
        shop = s.get(Shop, shop_id)
        chosen = [s.scalar(select(Service).where(Service.shop_id == shop_id, Service.name == n)) for n in names]
        held = booking.hold(s, shop, chosen, "car", day, start, utcnow(), request_text=request)
        details = {"name": "Liza Ramos", "phone": "0917 555 0101", "email": "liza@example.com", "plate": "DAF 9022"}
        return booking.confirm(s, shop, held, details, key, utcnow()).id


def price_it(app, shop_id, appointment_id, price=85000, note="New horn relay."):
    with app.app_context():
        s = db.session()
        appointment = s.get(Appointment, appointment_id)
        quotes.send_price(s, s.get(Shop, shop_id), appointment, price, note, utcnow(),
                          s.scalar(select(User).where(User.name == "Jun")))


def cells_of(s, appointment_id):
    return s.scalar(select(func.count()).select_from(BookedSlot).where(BookedSlot.appointment_id == appointment_id))


# ---------- Choosing it ----------

def test_the_services_step_offers_it_with_no_price_yet(app, client, shop_id):
    page = client.get("/book?vehicle=car").get_data(as_text=True)
    assert "Other Car Service" in page and "Price after we check" in page
    assert 'name="other"' in page and "What do you need?" in page
    assert "Other Motorcycle Service" not in page  # each vehicle has its own


def test_ticking_it_without_saying_what_is_refused(app, client, shop_id):
    other = service(app, shop_id, "Other Car Service")
    response = client.get(f"/book?vehicle=car&service={other.id}")
    assert response.status_code == 400
    assert "Tell us what you need" in response.get_data(as_text=True)


def test_writing_what_you_need_is_enough_to_choose_it(app, client, shop_id):
    oil = service(app, shop_id, "Car Oil Change")
    page = client.get(f"/book?vehicle=car&service={oil.id}&other=Check+the+horn").get_data(as_text=True)
    assert "Day and time" in page and "Other Car Service" in page
    assert "“Check the horn”" in page and "from ₱1,800 + the other service" in page
    assert 'name="other" value="Check the horn"' in page  # it goes on into the hold


def test_alone_its_price_is_after_we_check(app, client, shop_id):
    page = client.get("/book?vehicle=car&other=Squeak+from+the+back").get_data(as_text=True)
    assert "After we check" in page


# ---------- Booking it through the pages ----------

def test_a_guest_books_it_and_the_shop_sees_what_they_asked(app, shop_id, day):
    client = browser(app)
    other = service(app, shop_id, "Other Car Service")
    held = post(client, "/book/hold", {"vehicle": "car", "service": other.id, "other": ASKED,
                                       "day": day.isoformat(), "start": 9 * H})
    details_url = held.headers["Location"]
    page = client.get(details_url).get_data(as_text=True)
    assert ASKED in page and "price after we check" in page
    booked = post(client, details_url, {"name": "Liza Ramos", "phone": "0917 555 0101", "plate": "DAF 9022",
                                        "request": ASKED, "key": "form-1"}, follow_redirects=True)
    page = booked.get_data(as_text=True)
    assert "We're checking what you asked for" in page and "Being checked" in page
    with app.app_context():
        [appointment] = db.session().scalars(select(Appointment)).all()
        assert appointment.quote_status == TO_PRICE and appointment.request_text == ASKED
        assert appointment.price_from == 0 and appointment.lines[0].is_other


def test_the_details_step_needs_the_request(app, shop_id, day):
    client = browser(app)
    other = service(app, shop_id, "Other Car Service")
    held = post(client, "/book/hold", {"vehicle": "car", "service": other.id, "other": ASKED,
                                       "day": day.isoformat(), "start": 9 * H})
    response = post(client, held.headers["Location"], {"name": "Liza Ramos", "phone": "0917 555 0101",
                                                       "plate": "DAF 9022", "request": "", "key": "form-1"})
    assert response.status_code == 400 and find(app, Appointment) == []


# ---------- Pricing it, and the customer's answer ----------

def test_staff_send_the_price_and_the_customer_is_told(app, shop_id, day):
    appointment_id = make_booking(app, shop_id, day)
    advisor = log_in_as(app.test_client(), make_user(app, shop_id, "mika@shop.example", ADVISOR, "Mika Reyes"))
    ref = find(app, Appointment)[0].ref
    page = advisor.get(f"/b/{ref}").get_data(as_text=True)
    assert "Send the price" in page and ASKED in page
    sent = post(advisor, f"/b/{ref}/price", {"price": "850", "note": "New horn relay."}, follow_redirects=True)
    assert "Price sent to Liza Ramos" in sent.get_data(as_text=True)
    with app.app_context():
        s = db.session()
        appointment = s.get(Appointment, appointment_id)
        assert appointment.quote_status == PRICED and appointment.quote_price == 85000
        assert appointment.quoted_by == "Mika Reyes"
        notice = s.scalar(select(Notification).where(Notification.kind == "price", Notification.channel == "email"))
        assert notice.to == "liza@example.com" and "₱850" in notice.body and "New horn relay." in notice.body


def test_a_mechanic_can_price_it_but_a_stranger_cannot(app, shop_id, day):
    make_booking(app, shop_id, day)
    ref = find(app, Appointment)[0].ref
    stranger = browser(app)
    assert post(stranger, f"/b/{ref}/price", {"price": "850"}).status_code == 404
    jun = log_in_as(app.test_client(), find(app, User, User.name == "Jun")[0].id)
    post(jun, f"/b/{ref}/price", {"price": "1,200.50"})
    assert find(app, Appointment)[0].quote_price == 120050


def test_a_price_must_be_written(app, shop_id, day):
    appointment_id = make_booking(app, shop_id, day)
    with app.app_context(), pytest.raises(booking.BookingError):
        s = db.session()
        quotes.send_price(s, s.get(Shop, shop_id), s.get(Appointment, appointment_id), 0, "", utcnow(),
                          s.scalar(select(User).where(User.name == "Jun")))


def test_accepting_makes_it_the_services_price(app, shop_id, day):
    appointment_id = make_booking(app, shop_id, day, names=("Car Oil Change", "Other Car Service"))
    price_it(app, shop_id, appointment_id)
    with app.app_context():
        s = db.session()
        appointment = s.get(Appointment, appointment_id)
        quotes.accept(s, s.get(Shop, shop_id), appointment, 85000, utcnow())
        assert appointment.quote_status == ACCEPTED and appointment.quote_decided_at is not None
        assert appointment.price_from == 180000 + 85000 and not appointment.price_open


def test_declining_the_only_service_cancels_and_frees_the_time(app, shop_id, day):
    appointment_id = make_booking(app, shop_id, day)
    price_it(app, shop_id, appointment_id)
    with app.app_context():
        s = db.session()
        appointment = s.get(Appointment, appointment_id)
        quotes.decline(s, s.get(Shop, shop_id), appointment, 85000, utcnow())
        assert appointment.status == CANCELLED and appointment.quote_status == DECLINED
        assert cells_of(s, appointment_id) == 0


def test_declining_with_other_services_keeps_the_rest(app, shop_id, day):
    appointment_id = make_booking(app, shop_id, day, names=("Car Oil Change", "Other Car Service"))
    price_it(app, shop_id, appointment_id)
    with app.app_context():
        s = db.session()
        appointment = s.get(Appointment, appointment_id)
        before, starts = cells_of(s, appointment_id), appointment.starts_at
        quotes.decline(s, s.get(Shop, shop_id), appointment, 85000, utcnow())
        assert appointment.status == BOOKED and [line.name for line in appointment.lines] == ["Car Oil Change"]
        # 45 min of oil change + 15 min clean-up is 1 hour: 4 cells each for the bay and the mechanic.
        assert appointment.starts_at == starts and appointment.ends_at == starts + timedelta(hours=1)
        assert before == 2 * 8 and cells_of(s, appointment_id) == 2 * 4


def test_the_customer_page_answers_and_tells_the_desk(app, shop_id, day):
    appointment_id = make_booking(app, shop_id, day)
    price_it(app, shop_id, appointment_id)
    make_user(app, shop_id, "mika@shop.example", ADVISOR, "Mika Reyes")
    staff = log_in_as(app.test_client(), make_user(app, shop_id, "hiro@shop.example", "owner", "Hiro"))
    ref = find(app, Appointment)[0].ref
    page = staff.get(f"/b/{ref}").get_data(as_text=True)
    assert "They said yes" in page and "Send the new price" in page
    done = post(staff, f"/b/{ref}/price/answer", {"price": "85000", "answer": "accept"}, follow_redirects=True)
    assert "The price is confirmed" in done.get_data(as_text=True)
    with app.app_context():
        s = db.session()
        assert s.get(Appointment, appointment_id).quote_status == ACCEPTED
        told = s.scalars(select(Notification).where(Notification.kind == "price_answer")).all()
        assert len(told) == 2  # the owner and the advisor


def test_an_answer_to_an_old_price_is_refused(app, shop_id, day):
    """The shop changed the price while the customer looked at the old one: their
    "yes" doesn't count for the new price. They see the new one and answer again."""
    appointment_id = make_booking(app, shop_id, day)
    price_it(app, shop_id, appointment_id, 85000)
    price_it(app, shop_id, appointment_id, 95000)
    with app.app_context():
        s = db.session()
        appointment = s.get(Appointment, appointment_id)
        with pytest.raises(booking.Changed):
            quotes.accept(s, s.get(Shop, shop_id), appointment, 85000, utcnow())
        assert s.get(Appointment, appointment_id).quote_status == PRICED
        quotes.accept(s, s.get(Shop, shop_id), appointment, 95000, utcnow())
        assert appointment.price_from == 95000


def test_a_cancelled_booking_takes_no_answer(app, shop_id, day):
    appointment_id = make_booking(app, shop_id, day)
    price_it(app, shop_id, appointment_id)
    with app.app_context():
        s = db.session()
        appointment = s.get(Appointment, appointment_id)
        booking.cancel(s, s.get(Shop, shop_id), appointment, "", utcnow(), by_staff=True)
        with pytest.raises(booking.Changed):
            quotes.accept(s, s.get(Shop, shop_id), appointment, 85000, utcnow())
        assert s.get(Appointment, appointment_id).quote_status == PRICED


def test_the_desk_lists_what_needs_a_price(app, shop_id, day):
    make_booking(app, shop_id, day)
    owner = log_in_as(app.test_client(), make_user(app, shop_id, "hiro@shop.example", "owner", "Hiro"))
    page = owner.get("/desk").get_data(as_text=True)
    assert "Needs an Answer" in page and ASKED in page and "1 for the shop" in page


@pytest.mark.parametrize("run", range(5))
def test_accept_and_decline_at_once_only_one_counts(app, shop_id, day, run):
    """A double tap, or two phones answering at the same moment: exactly one answer wins."""
    appointment_id = make_booking(app, shop_id, day, names=("Car Oil Change", "Other Car Service"))
    price_it(app, shop_id, appointment_id)
    barrier = threading.Barrier(2)
    won, lost = [], []

    def answer(yes):
        with app.app_context():
            s = db.session()
            appointment = s.get(Appointment, appointment_id)
            barrier.wait()
            try:
                (quotes.accept if yes else quotes.decline)(s, s.get(Shop, shop_id), appointment, 85000, utcnow())
                won.append(yes)
            except booking.Changed:
                lost.append(yes)
    threads = [threading.Thread(target=answer, args=(yes,)) for yes in (True, False)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(won) == 1 and len(lost) == 1
    with app.app_context():
        appointment = db.session().get(Appointment, appointment_id)
        if won == [True]:
            assert appointment.quote_status == ACCEPTED and len(appointment.lines) == 2
        else:
            assert appointment.quote_status == DECLINED and len(appointment.lines) == 1


# ---------- The waitlist, Settings and older shops ----------

def test_the_waitlist_keeps_the_request_for_the_offer(app, shop_id, day):
    with app.test_request_context():  # the offer's notice has a link
        s = db.session()
        shop = s.get(Shop, shop_id)
        other = s.scalar(select(Service).where(Service.shop_id == shop_id, Service.name == "Other Car Service"))
        with pytest.raises(booking.BookingError):
            waitlist.join(s, shop, "Ana", "0917 555 0199", "", "car", [other], day, day, utcnow())
        entry = waitlist.join(s, shop, "Ana", "0917 555 0199", "", "car", [other], day, day, utcnow(),
                              request_text=ASKED)
        assert entry.request_text == ASKED
        assert waitlist.offer(s, shop, entry, utcnow()) is True
        held = s.scalar(select(Hold).where(Hold.token == s.get(type(entry), entry.id).hold_token))
        assert held.request_text == ASKED


def test_settings_mark_a_service_as_the_other_service(app, shop_id):
    owner = log_in_as(app.test_client(), make_user(app, shop_id, "hiro@shop.example", "owner", "Hiro"))
    page = owner.get("/settings/services").get_data(as_text=True)
    assert "Price after we check" in page
    other = service(app, shop_id, "Other Car Service")
    form = owner.get(f"/settings/services/{other.id}").get_data(as_text=True)
    assert 'name="is_other" value="1" checked' in form


def test_the_migration_gives_older_shops_an_other_service(app):
    path = Path(__file__).parent.parent / "shiromoto" / "migrations" / "versions" / "0006_other_service.py"
    spec = importlib.util.spec_from_file_location("migration_0006", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with app.app_context():
        s = db.session()
        shop = Shop(name="Old Shop", created_at=utcnow())
        s.add(shop)
        s.flush()
        s.add_all([BayType(shop_id=shop.id, name="Car lift", vehicle_kind="car"),
                   BayType(shop_id=shop.id, name="Motorcycle stand", vehicle_kind="moto")])
        s.commit()
        with db.engine().begin() as connection:
            migration.add_other_services(connection)
            migration.add_other_services(connection)  # twice adds nothing more
        made = s.scalars(select(Service).where(Service.shop_id == shop.id)).all()
        assert sorted(sv.name for sv in made) == ["Other Car Service", "Other Motorcycle Service"]
        assert all(sv.is_other and sv.price_from == 0 for sv in made)
