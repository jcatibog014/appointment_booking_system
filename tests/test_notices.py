"""Notices (queued once, sent once), the timer, the waitlist, and the phone app."""
import json
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from shiromoto import booking, db, notices, seed, waitlist
from shiromoto.models import EMAIL, FAILED, MECHANIC, OFFERED, PUSH, SKIPPED, TAKEN_UP, WAITING, \
    Appointment, Hold, Notification, PushSubscription, Service, Shop, Skill, User, WaitlistEntry

from .conftest import CSRF, find, post

MON = date(2026, 11, 9)


def manila(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute) - timedelta(hours=8)


BEFORE = manila(MON - timedelta(days=2), 9)


@pytest.fixture
def shop_id(app):
    with app.app_context():
        s = db.session()
        shop = seed.starter_shop(s, "ShiroMoto Garage", BEFORE)
        jun = User(shop_id=shop.id, email="jun@shop.example", name="Jun", role=MECHANIC, password_hash="x",
                   created_at=BEFORE)
        jun.skills = list(s.scalars(select(Skill).where(Skill.shop_id == shop.id)))
        s.add(jun)
        s.commit()
        return shop.id


@pytest.fixture
def mail_on(app):
    app.config.update(MAIL_USERNAME="shop@gmail.example", MAIL_PASSWORD="app password")


class FakeSMTP:
    sent = []
    fail = False

    def __init__(self, host, port, timeout):
        assert (host, port) == ("smtp.gmail.com", 465)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def login(self, user, password):
        assert user == "shop@gmail.example"

    def send_message(self, message):
        if FakeSMTP.fail:
            raise OSError("Gmail is down")
        FakeSMTP.sent.append(message)


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.fail = [], False
    monkeypatch.setattr(notices.smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def oil(s, shop_id):
    return [s.scalar(select(Service).where(Service.shop_id == shop_id, Service.name == "Car Oil Change"))]


def book(s, shop_id, start=8 * 60, key="k", email="carlo@mail.example", day=MON, now=BEFORE):
    shop = s.get(Shop, shop_id)
    held = booking.hold(s, shop, oil(s, shop_id), "car", day, start, now)
    return booking.confirm(s, shop, held, {"name": "Carlo", "phone": "0917 555 0142", "plate": "ABC 1234",
                                           "email": email}, key, now)


def count(s, model, *where):
    return s.scalar(select(func.count()).select_from(model).where(*where))


# ---------- The outbox ----------

def test_a_notice_is_queued_once(app, shop_id):
    with app.test_request_context():
        s = db.session()
        visit = book(s, shop_id)
        shop = s.get(Shop, shop_id)
        for _ in range(3):
            notices.booked(s, shop, visit, BEFORE)
        s.commit()
        rows = s.scalars(select(Notification).where(Notification.kind == "confirmed")).all()
        assert sorted(row.channel for row in rows) == [EMAIL, PUSH]
        assert rows[0].to == "carlo@mail.example" or rows[1].to == "carlo@mail.example"
        assert "Car Oil Change" in rows[0].subject and "/b/" in rows[0].link and "s=" in rows[0].link


def test_without_email_only_push_is_queued(app, shop_id):
    with app.test_request_context():
        s = db.session()
        visit = book(s, shop_id, email="")
        notices.booked(s, s.get(Shop, shop_id), visit, BEFORE)
        s.commit()
        assert [row.channel for row in s.scalars(select(Notification))] == [PUSH]


def test_sending_email(app, shop_id, mail_on, smtp):
    with app.test_request_context():
        s = db.session()
        notices.booked(s, s.get(Shop, shop_id), book(s, shop_id), BEFORE)
        s.commit()
        sent, failed, skipped = notices.send_pending(s, BEFORE)
        assert (sent, failed, skipped) == (1, 0, 1)  # the email went; push has no phone yet
        [message] = smtp.sent
        assert message["To"] == "carlo@mail.example" and "Booked:" in message["Subject"]
        assert notices.send_pending(s, BEFORE) == (0, 0, 0)  # nothing is sent twice
        assert len(smtp.sent) == 1


def test_no_mail_settings_means_in_app_only(app, shop_id, smtp):
    with app.test_request_context():
        s = db.session()
        notices.booked(s, s.get(Shop, shop_id), book(s, shop_id), BEFORE)
        s.commit()
        notices.send_pending(s, BEFORE)
        assert {row.status for row in s.scalars(select(Notification))} == {SKIPPED} and smtp.sent == []


def test_a_failed_email_is_tried_again_then_given_up(app, shop_id, mail_on, smtp):
    smtp.fail = True
    with app.test_request_context():
        s = db.session()
        notices.booked(s, s.get(Shop, shop_id), book(s, shop_id), BEFORE)
        s.commit()
        moment = BEFORE
        for attempt in range(1, 4):
            notices.send_pending(s, moment)
            row = s.scalar(select(Notification).where(Notification.channel == EMAIL))
            assert row.attempts == attempt and "Gmail is down" in row.error
            moment = row.send_after
        assert row.status == FAILED


def test_two_timers_send_an_email_once(app, shop_id, mail_on, smtp):
    with app.test_request_context():
        s = db.session()
        notices.booked(s, s.get(Shop, shop_id), book(s, shop_id), BEFORE)
        s.commit()
    barrier = threading.Barrier(2)

    def one():
        with app.test_request_context():
            barrier.wait()
            notices.send_pending(db.session(), BEFORE)
    threads = [threading.Thread(target=one) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(smtp.sent) == 1


def test_push(app, shop_id, monkeypatch):
    import pywebpush
    app.config.update(VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="priv")
    pushed = []

    class Gone:
        status_code = 410

    def fake(subscription_info, data, **kwargs):
        if "gone" in subscription_info["endpoint"]:
            raise pywebpush.WebPushException("gone", response=Gone())
        pushed.append(json.loads(data))
    monkeypatch.setattr(pywebpush, "webpush", fake)
    with app.test_request_context():
        s = db.session()
        visit = book(s, shop_id, email="")
        for endpoint in ("https://push.example/phone", "https://push.example/gone"):
            s.add(PushSubscription(shop_id=shop_id, appointment_id=visit.id, endpoint=endpoint, p256dh="p",
                                   auth="a", created_at=BEFORE))
        notices.booked(s, s.get(Shop, shop_id), visit, BEFORE)
        s.commit()
        assert notices.send_pending(s, BEFORE) == (1, 0, 0)
        assert pushed[0]["title"].startswith("Booked:") and "/b/" in pushed[0]["url"]
        assert [row.endpoint for row in s.scalars(select(PushSubscription))] == ["https://push.example/phone"]


def test_demo_notices_are_never_sent(app, smtp, mail_on):
    with app.test_request_context():
        s = db.session()
        shop = seed.build(s, BEFORE, demo=True)
        visit = s.scalar(select(Appointment).where(Appointment.shop_id == shop.id))
        visit.customer.email = "real@mail.example"
        notices.booked(s, shop, visit, BEFORE)
        s.commit()
        notices.send_pending(s, BEFORE)
        assert smtp.sent == []


# ---------- Reminders ----------

def test_the_day_before_reminder(app, shop_id):
    with app.test_request_context():
        s = db.session()
        shop = s.get(Shop, shop_id)
        visit = book(s, shop_id)
        sunday_morning = manila(MON - timedelta(days=1), 9)
        assert notices.queue_day_before(s, shop, sunday_morning) == 2
        assert notices.queue_day_before(s, shop, sunday_morning) == 0  # once only
        s.commit()
        row = s.scalar(select(Notification).where(Notification.kind == "day_before", Notification.channel == EMAIL))
        assert row.send_after == manila(MON - timedelta(days=1), 17)  # from 5 PM the day before
        assert notices.send_pending(s, sunday_morning) == (0, 0, 0)  # too early to send
        booking.move(s, shop, visit, MON, 10 * 60, BEFORE)
        assert notices.queue_day_before(s, shop, sunday_morning) == 2  # a new time: a new reminder


def test_service_due_reminder(app, shop_id):
    with app.test_request_context():
        s = db.session()
        shop = s.get(Shop, shop_id)
        visit = book(s, shop_id)
        booking.check_in(s, shop, visit, manila(MON, 7, 55), mileage=40000)
        due_soon = manila(date(2027, 3, 4), 9)  # oil change again after 4 months: 9 Mar 2027
        assert notices.queue_service_due(s, shop, due_soon) == 2
        assert notices.queue_service_due(s, shop, due_soon) == 0
        assert notices.queue_service_due(s, shop, manila(date(2027, 1, 4), 9)) == 0  # not yet


# ---------- Signed links ----------

def test_a_signed_link_opens_the_booking(app, shop_id):
    with app.test_request_context():
        s = db.session()
        visit = book(s, shop_id)
        link = notices.booking_link(visit)
        assert notices.link_ref(link.split("s=")[1]) == visit.ref
        assert notices.link_ref("made-up") is None
        ref = visit.ref
    client = app.test_client()
    path = link.replace("http://localhost", "")
    assert client.get(path).status_code == 302  # opened, and the secret moved out of the address
    assert client.get(f"/b/{ref}").status_code == 200
    assert app.test_client().get(f"/b/{ref}?s=tampered").status_code == 404


# ---------- The timer ----------

def test_the_timer_needs_its_key(app, shop_id):
    client = app.test_client()
    assert client.post("/tasks/tick").status_code == 404  # off without TICK_KEY
    app.config["TICK_KEY"] = "secret"
    assert client.post("/tasks/tick", headers={"X-Tick-Key": "wrong"}).status_code == 404
    response = client.post("/tasks/tick", headers={"X-Tick-Key": "secret"})
    assert response.status_code == 200 and set(response.get_json()) >= {"sent", "offers", "reminders"}


def test_the_tick_command(app, shop_id):
    result = app.test_cli_runner().invoke(args=["tick"])
    assert result.exit_code == 0, result.output
    assert "sent:" in result.output


def test_vapid_keys_work_with_pywebpush(app):
    from py_vapid import Vapid
    output = app.test_cli_runner().invoke(args=["make-vapid-keys"]).output
    keys = dict(line.split("=", 1) for line in output.strip().splitlines())
    assert Vapid.from_string(private_key=keys["VAPID_PRIVATE_KEY"]).sign({"sub": "mailto:a@b.example",
                                                                          "aud": "https://push.example"})
    assert len(keys["VAPID_PUBLIC_KEY"]) == 87  # 65 bytes, base64url without padding


# ---------- The waitlist ----------

def join(s, shop_id, name="Liza", first=MON, last=MON, now=BEFORE):
    return waitlist.join(s, s.get(Shop, shop_id), name, "0918 222 3401", "liza@mail.example", "car",
                         oil(s, shop_id), first, last, now)


def fill_monday(s, shop_id):
    """Book every Monday oil-change time on the one lift."""
    made = []
    for n, start in enumerate([8 * 60, 9 * 60, 10 * 60, 11 * 60, 13 * 60, 14 * 60, 15 * 60, 16 * 60]):
        made.append(book(s, shop_id, start, key=f"fill{n}", email=""))
    return made


def test_a_freed_time_is_offered_to_the_first_waiting(app, shop_id):
    with app.test_request_context():
        s = db.session()
        shop = s.get(Shop, shop_id)
        visits = fill_monday(s, shop_id)
        first, second = join(s, shop_id, "Liza"), join(s, shop_id, "Ben")
        assert waitlist.offer_free_times(s, shop, BEFORE) == 0  # Monday is full
        booking.cancel(s, shop, visits[2], "", BEFORE)  # 10:00 frees up
        assert waitlist.offer_free_times(s, shop, BEFORE) == 1
        s.refresh(first)
        s.refresh(second)
        assert first.status == OFFERED and second.status == WAITING
        held = s.scalar(select(Hold).where(Hold.token == first.hold_token))
        assert held.starts_at == manila(MON, 10) and held.expires_at == BEFORE + timedelta(minutes=30)
        offer = s.scalar(select(Notification).where(Notification.kind == "waitlist_offer",
                                                    Notification.channel == EMAIL))
        assert "10:00 AM" in offer.body and "/book/details/" in offer.link


def test_an_offer_nobody_takes_goes_to_the_next_person(app, shop_id):
    with app.test_request_context():
        s = db.session()
        shop = s.get(Shop, shop_id)
        visits = fill_monday(s, shop_id)
        first, second = join(s, shop_id, "Liza"), join(s, shop_id, "Ben")
        booking.cancel(s, shop, visits[2], "", BEFORE)
        waitlist.offer_free_times(s, shop, BEFORE)
        later = BEFORE + timedelta(minutes=31)
        waitlist.expire(s, shop, later)
        s.refresh(first)
        assert first.status == WAITING and first.offers == 1 and count(s, Hold) == 0
        waitlist.offer_free_times(s, shop, later)
        s.refresh(first)
        assert first.status == OFFERED  # still first in line, so offered again


def test_booking_from_an_offer_ends_the_wait(app, shop_id):
    with app.test_request_context():
        s = db.session()
        shop = s.get(Shop, shop_id)
        visits = fill_monday(s, shop_id)
        entry = join(s, shop_id)
        booking.cancel(s, shop, visits[0], "", BEFORE)
        waitlist.offer_free_times(s, shop, BEFORE)
        s.refresh(entry)
        token = entry.hold_token
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    post(client, f"/book/details/{token}", {"name": "Liza", "phone": "0918 222 3401", "plate": "DAF 9022",
                                            "key": "from-offer"})
    assert find(app, WaitlistEntry)[0].status == TAKEN_UP


def test_two_timers_offer_one_entry_once(app, shop_id):
    with app.test_request_context():
        s = db.session()
        visits = fill_monday(s, shop_id)
        join(s, shop_id)
        booking.cancel(s, s.get(Shop, shop_id), visits[0], "", BEFORE)
    barrier = threading.Barrier(2)

    def one():
        with app.test_request_context():
            s = db.session()
            barrier.wait()
            waitlist.offer_free_times(s, s.get(Shop, shop_id), BEFORE)
    threads = [threading.Thread(target=one) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    with app.app_context():
        s = db.session()
        assert count(s, Hold) == 1 and s.scalar(select(WaitlistEntry)).offers == 1


def test_waitlist_pages(app, shop_id):
    oil_id = find(app, Service, Service.shop_id == shop_id, Service.name == "Car Oil Change")[0].id
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    with app.app_context():
        from shiromoto.dates import local_today
        first = local_today() + timedelta(days=1)
    form = {"vehicle": "car", "service": oil_id, "first_day": first.isoformat(),
            "last_day": (first + timedelta(days=3)).isoformat(), "name": "Liza", "phone": "0918 222 3401", "email": ""}
    response = post(client, "/book/waitlist", form)
    assert response.status_code == 302 and "/waitlist/" in response.headers["Location"]
    page = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Your Waitlist Place" in page and "first in line" in page
    post(client, response.headers["Location"] + "/leave")
    assert find(app, WaitlistEntry)[0].status == "left"
    bad = post(client, "/book/waitlist", {**form, "last_day": (first - timedelta(days=2)).isoformat()})
    assert bad.status_code == 400 and b"can&#39;t be before" in bad.data


# ---------- The phone app ----------

def test_the_service_worker_and_manifest(app, shop_id):
    client = app.test_client()
    worker = client.get("/sw.js")
    assert worker.status_code == 200 and worker.headers["Service-Worker-Allowed"] == "/"
    assert b"showNotification" in worker.data
    manifest = json.loads(client.get("/static/site.webmanifest").data)
    static = Path(app.static_folder)
    for item in manifest["icons"]:
        assert (static / item["src"].removeprefix("/static/")).exists()
    assert any(item.get("purpose") == "maskable" for item in manifest["icons"])
    for path in [line.strip(' ",') for line in worker.get_data(as_text=True).split("const KEEP = [")[1]
                 .split("];")[0].splitlines() if line.strip().startswith('"/static')]:
        assert (static / path.removeprefix("/static/")).exists(), path
    assert client.get("/offline").status_code == 200
    assert b'rel="manifest"' in client.get("/").data


def test_push_sign_up(app, shop_id):
    with app.test_request_context():
        visit = book(db.session(), shop_id)
        ref = visit.ref
    stranger = app.test_client()
    with stranger.session_transaction() as session:
        session["csrf"] = CSRF
    body = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "key", "auth": "secret"}, "ref": ref}
    headers = {"X-CSRF-Token": CSRF}
    assert stranger.post("/push/subscribe", json=body, headers=headers).status_code == 404  # not their booking
    assert stranger.post("/push/subscribe", json={**body, "ref": None}, headers=headers).status_code == 403
    assert stranger.post("/push/subscribe", json={**body, "endpoint": "http://x"}, headers=headers).status_code == 400
    with app.app_context():
        s = db.session()
        visit = s.scalar(select(Appointment).where(Appointment.ref == ref))
        visit.token_hash = booking.token_hash("tok")
        s.commit()
    stranger.get(f"/b/{ref}?t=tok")
    assert stranger.post("/push/subscribe", json=body, headers=headers).status_code == 200
    [row] = find(app, PushSubscription)
    assert row.appointment_id is not None and row.auth == "secret"
    stranger.post("/push/unsubscribe", json={"endpoint": body["endpoint"]}, headers=headers)
    assert find(app, PushSubscription) == []

