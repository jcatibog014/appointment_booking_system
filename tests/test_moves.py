"""Move requests: a customer asks for a new time, and staff approve or decline it (moves.py).
The starter shop has one car lift and one mechanic here, so a held time really blocks."""
import threading
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from shiromoto import booking, db, moves, seed, shops
from shiromoto.dates import local_today, utcnow
from shiromoto.models import ADVISOR, BOOKED, CANCELLED, MECHANIC, MOVE_APPROVED, MOVE_ASKED, MOVE_DECLINED, \
    MOVE_EXPIRED, MOVE_WITHDRAWN, Appointment, BookedSlot, Hold, Notification, Service, Shop, Skill, User

from .conftest import CSRF, find, log_in_as, make_user, post

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
    """A weekday at least 2 days away (not a holiday), with another open weekday after it."""
    with app.app_context():
        s = db.session()
        shop = s.get(Shop, shop_id)
        candidate = local_today() + timedelta(days=2)
        while candidate.weekday() > 3 or shops.closures_between(s, shop, candidate, candidate + timedelta(days=1)):
            candidate += timedelta(days=1)
        return candidate


def oil(s, shop_id):
    return s.scalar(select(Service).where(Service.shop_id == shop_id, Service.name == "Car Oil Change"))


def make_booking(app, shop_id, day, start=8 * H, key="k1"):
    with app.app_context():
        s = db.session()
        shop = s.get(Shop, shop_id)
        held = booking.hold(s, shop, [oil(s, shop_id)], "car", day, start, utcnow())
        details = {"name": "Liza Ramos", "phone": "0917 555 0101", "email": "liza@example.com", "plate": "DAF 9022"}
        return booking.confirm(s, shop, held, details, key, utcnow()).id


def ask(app, shop_id, appointment_id, day, start=10 * H, now=None):
    with app.app_context():
        s = db.session()
        appointment = s.get(Appointment, appointment_id)
        moves.ask(s, s.get(Shop, shop_id), appointment, day, start, now or utcnow(), "After work")
        return appointment.move_hold_token


def jun(s):
    return s.scalar(select(User).where(User.name == "Jun"))


def count(s, model, *where):
    return s.scalar(select(func.count()).select_from(model).where(*where))


# ---------- Asking ----------

def test_asking_holds_the_new_time_and_keeps_the_booking(app, shop_id, day):
    appointment_id = make_booking(app, shop_id, day)
    ask(app, shop_id, appointment_id, day + timedelta(days=1))
    with app.app_context():
        s = db.session()
        appointment = s.get(Appointment, appointment_id)
        assert appointment.status == BOOKED and appointment.move_status == MOVE_ASKED
        assert appointment.starts_at == appointment.move_from_at  # it hasn't moved
        assert appointment.move_expires_at <= utcnow() + moves.MOVE_HOLD
        # Nobody else can take the time it asked for: the one lift and the one mechanic are held.
        with pytest.raises(booking.Taken):
            booking.hold(s, s.get(Shop, shop_id), [oil(s, shop_id)], "car", day + timedelta(days=1), 10 * H,
                         utcnow())


def test_asking_twice_is_refused_until_answered(app, shop_id, day):
    appointment_id = make_booking(app, shop_id, day)
    ask(app, shop_id, appointment_id, day + timedelta(days=1))
    with pytest.raises(booking.BookingError, match="already asked"):
        ask(app, shop_id, appointment_id, day + timedelta(days=1), 13 * H)
    with app.app_context():
        s = db.session()
        moves.decline(s, s.get(Shop, shop_id), s.get(Appointment, appointment_id), utcnow(), jun(s), "Full that day")
    ask(app, shop_id, appointment_id, day + timedelta(days=1), 13 * H)  # after an answer, asking again is fine
    with app.app_context():
        assert db.session().get(Appointment, appointment_id).move_status == MOVE_ASKED


def test_no_asking_close_to_the_booking(app, shop_id, day):
    appointment_id = make_booking(app, shop_id, day)
    with app.app_context():
        late = db.session().get(Appointment, appointment_id).starts_at - timedelta(hours=3)
    with pytest.raises(booking.TooLate):
        ask(app, shop_id, appointment_id, day + timedelta(days=1), now=late)


# ---------- Answering ----------

def test_approving_moves_it_and_frees_the_old_time(app, shop_id, day):
    appointment_id = make_booking(app, shop_id, day)
    token = ask(app, shop_id, appointment_id, day + timedelta(days=1))
    with app.app_context():
        s = db.session()
        shop = s.get(Shop, shop_id)
        appointment = s.get(Appointment, appointment_id)
        wanted = appointment.move_starts_at
        moves.approve(s, shop, appointment, utcnow(), jun(s), "See you then")
        assert appointment.starts_at == wanted and appointment.move_status == MOVE_APPROVED
        assert appointment.move_decided_by == "Jun" and appointment.move_reply == "See you then"
        assert count(s, Hold, Hold.token == token) == 0
        assert count(s, BookedSlot, BookedSlot.appointment_id == appointment_id) == 2 * 4  # 1 hour, bay + mechanic
        assert booking.hold(s, shop, [oil(s, shop_id)], "car", day, 8 * H, utcnow()) is not None  # old time free


def test_declining_keeps_the_time_and_gives_the_held_one_back(app, shop_id, day):
    appointment_id = make_booking(app, shop_id, day)
    ask(app, shop_id, appointment_id, day + timedelta(days=1))
    with app.app_context():
        s = db.session()
        shop = s.get(Shop, shop_id)
        appointment = s.get(Appointment, appointment_id)
        before = appointment.starts_at
        moves.decline(s, shop, appointment, utcnow(), jun(s))
        assert appointment.starts_at == before and appointment.move_status == MOVE_DECLINED
        assert count(s, Hold) == 0
        assert booking.hold(s, shop, [oil(s, shop_id)], "car", day + timedelta(days=1), 10 * H, utcnow())


def test_the_customer_withdraws(app, shop_id, day):
    appointment_id = make_booking(app, shop_id, day)
    ask(app, shop_id, appointment_id, day + timedelta(days=1))
    with app.app_context():
        s = db.session()
        appointment = s.get(Appointment, appointment_id)
        moves.withdraw(s, s.get(Shop, shop_id), appointment, utcnow())
        assert appointment.move_status == MOVE_WITHDRAWN and count(s, Hold) == 0
        with pytest.raises(booking.Changed):  # nothing left to approve
            moves.approve(s, s.get(Shop, shop_id), appointment, utcnow(), jun(s))


def test_a_request_runs_out(app, shop_id, day):
    appointment_id = make_booking(app, shop_id, day)
    ask(app, shop_id, appointment_id, day + timedelta(days=1))
    with app.test_request_context():
        s = db.session()
        shop = s.get(Shop, shop_id)
        appointment = s.get(Appointment, appointment_id)
        later = appointment.move_expires_at + timedelta(minutes=1)
        with pytest.raises(booking.Changed):
            moves.approve(s, shop, appointment, later, jun(s))
        from shiromoto import tick
        tick.run(s, later)
        assert s.get(Appointment, appointment_id).move_status == MOVE_EXPIRED
        assert count(s, Notification, Notification.kind == "move_expired") >= 1


def test_cancelling_or_a_staff_move_ends_the_request(app, shop_id, day):
    first = make_booking(app, shop_id, day)
    ask(app, shop_id, first, day + timedelta(days=1))
    with app.app_context():
        s = db.session()
        booking.cancel(s, s.get(Shop, shop_id), s.get(Appointment, first), "", utcnow())
        appointment = s.get(Appointment, first)
        assert appointment.status == CANCELLED and appointment.move_status == MOVE_WITHDRAWN and count(s, Hold) == 0
    second = make_booking(app, shop_id, day, 13 * H, key="k2")
    ask(app, shop_id, second, day + timedelta(days=1))
    with app.app_context():
        s = db.session()
        booking.move(s, s.get(Shop, shop_id), s.get(Appointment, second), day, 14 * H, utcnow(), by_staff=True)
        assert s.get(Appointment, second).move_status == MOVE_WITHDRAWN and count(s, Hold) == 0


# ---------- The pages ----------

def test_the_desk_sees_it_and_is_told(app, shop_id, day):
    appointment_id = make_booking(app, shop_id, day)
    make_user(app, shop_id, "mika@shop.example", ADVISOR, "Mika Reyes")
    owner = log_in_as(app.test_client(), make_user(app, shop_id, "hiro@shop.example", "owner", "Hiro"))
    ref = find(app, Appointment)[0].ref
    with app.test_request_context():
        s = db.session()
        appointment = s.get(Appointment, appointment_id)
        moves.ask(s, s.get(Shop, shop_id), appointment, day + timedelta(days=1), 10 * H, utcnow())
        from shiromoto import notices
        notices.move_asked(s, s.get(Shop, shop_id), appointment, utcnow())
        s.commit()
        told = {n.user_id for n in s.scalars(select(Notification).where(Notification.kind == "move_request"))}
        assert len(told) == 3  # the owner, the advisor and Jun, the booking's mechanic
    today = owner.get("/desk").get_data(as_text=True)
    assert "Move Requests" in today and "Liza Ramos" in today
    page = owner.get(f"/b/{ref}").get_data(as_text=True)
    assert "Approve the new time" in page and "with Jun in Lift 1" in page


def test_only_staff_can_answer(app, shop_id, day):
    appointment_id = make_booking(app, shop_id, day)
    ask(app, shop_id, appointment_id, day + timedelta(days=1))
    ref = find(app, Appointment)[0].ref
    guest = app.test_client()
    with guest.session_transaction() as session:
        session["csrf"] = CSRF
    assert post(guest, f"/b/{ref}/move/answer", {"answer": "approve"}).status_code == 404
    advisor = log_in_as(app.test_client(), make_user(app, shop_id, "mika@shop.example", ADVISOR, "Mika Reyes"))
    done = post(advisor, f"/b/{ref}/move/answer", {"answer": "decline", "reply": "We're full"}, follow_redirects=True)
    assert "Declined" in done.get_data(as_text=True)
    assert find(app, Appointment)[0].move_reply == "We're full"


# ---------- Races ----------

@pytest.mark.parametrize("run", range(5))
def test_approve_and_withdraw_at_once_only_one_counts(app, shop_id, day, run):
    appointment_id = make_booking(app, shop_id, day)
    ask(app, shop_id, appointment_id, day + timedelta(days=1))
    barrier = threading.Barrier(2)
    won = []

    def answer(kind):
        with app.app_context():
            s = db.session()
            appointment = s.get(Appointment, appointment_id)
            barrier.wait()
            try:
                if kind == "approve":
                    moves.approve(s, s.get(Shop, shop_id), appointment, utcnow(), jun(s))
                else:
                    moves.withdraw(s, s.get(Shop, shop_id), appointment, utcnow())
                won.append(kind)
            except booking.BookingError:
                pass
    threads = [threading.Thread(target=answer, args=(kind,)) for kind in ("approve", "withdraw")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(won) == 1
    with app.app_context():
        s = db.session()
        appointment = s.get(Appointment, appointment_id)
        assert count(s, Hold) == 0  # used (approve) or given back (withdraw), never left behind
        cells = count(s, BookedSlot, BookedSlot.appointment_id == appointment_id)
        assert cells == 2 * 4
        if won == ["approve"]:
            assert appointment.move_status == MOVE_APPROVED and appointment.starts_at == appointment.move_starts_at
        else:
            assert appointment.move_status == MOVE_WITHDRAWN and appointment.starts_at == appointment.move_from_at


@pytest.mark.parametrize("run", range(5))
def test_two_asks_at_once_make_one_request(app, shop_id, day, run):
    """Two tabs ask for two different times at the same moment: one request, one held time."""
    appointment_id = make_booking(app, shop_id, day)
    barrier = threading.Barrier(2)
    results = []

    def one(start):
        with app.app_context():
            s = db.session()
            appointment = s.get(Appointment, appointment_id)
            barrier.wait()
            try:
                moves.ask(s, s.get(Shop, shop_id), appointment, day + timedelta(days=1), start, utcnow())
                results.append("asked")
            except booking.BookingError:
                results.append("refused")
    threads = [threading.Thread(target=one, args=(start,)) for start in (10 * H, 14 * H)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["asked", "refused"]
    with app.app_context():
        s = db.session()
        appointment = s.get(Appointment, appointment_id)
        [held] = s.scalars(select(Hold)).all()
        assert held.token == appointment.move_hold_token and held.starts_at == appointment.move_starts_at
