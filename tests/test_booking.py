"""Booking: holds, confirming, moving and cancelling, and above all: no double booking.

The race tests start real threads, each with its own database connection,
that all try to take the same time at once. Exactly as many may win as
there are free bays and mechanics; everyone else must get "taken".
"""
import threading
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from shiromoto import booking, db, seed, slots
from shiromoto.dates import utcnow
from shiromoto.models import BOOKED, CANCELLED, MECHANIC, Appointment, Bay, BookedSlot, Customer, Hold, Service, \
    Shop, Skill, User, Vehicle

MON = date(2026, 11, 9)
H = 60


def manila(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute) - timedelta(hours=8)


NOW = manila(MON - timedelta(days=2), 9)  # Saturday morning, two days before


@pytest.fixture
def shop_id(app):
    """The starter shop (Lift 1, Moto Stand 1) with one mechanic, Jun, who has every skill."""
    with app.app_context():
        s = db.session()
        shop = seed.starter_shop(s, "ShiroMoto Garage", NOW)
        jun = User(shop_id=shop.id, email="jun@shop.example", name="Jun", role=MECHANIC, password_hash="x",
                   created_at=NOW)
        jun.skills = list(s.scalars(select(Skill).where(Skill.shop_id == shop.id)))
        s.add(jun)
        s.commit()
        return shop.id


def services(s, shop_id, *names):
    return [s.scalar(select(Service).where(Service.shop_id == shop_id, Service.name == n)) for n in names]


def details(**changes):
    return {"name": "Carlo Mendoza", "phone": "0917 555 0142", "email": "carlo@mail.example", "plate": "ABC 1234",
            "make": "Toyota", "model": "Vios", "year": 2019, "notes": "", **changes}


def hold_and_confirm(s, shop, names=("Car Oil Change",), start=8 * H, day=MON, key="key-1", **changes):
    held = booking.hold(s, shop, services(s, shop.id, *names), "car", day, start, NOW)
    return booking.confirm(s, shop, held, details(**changes), key, NOW)


def count(s, model, *where):
    return s.scalar(select(func.count()).select_from(model).where(*where))


# ---------- Small pieces ----------

def test_phone_numbers_and_plates_are_matched_loosely():
    assert booking.phone_key("+63 917 555 0142") == booking.phone_key("(0917) 555-0142") == "09175550142"
    assert booking.plate_key("abc-1234") == booking.plate_key("ABC 1234") == "ABC1234"
    assert booking.phone_problem("123") and booking.plate_problem("A")


def test_cells_cover_every_quarter_hour():
    starts = datetime(2026, 11, 9, 0, 0)
    assert booking.cells(starts, starts + timedelta(minutes=60)) == [starts + timedelta(minutes=m) for m in (0, 15, 30, 45)]
    assert booking.merge([480, 495, 510, 600]) == [(480, 525), (600, 615)]


# ---------- Hold, then confirm ----------

def test_holding_a_time_takes_its_cells(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    held = booking.hold(ctx, shop, services(ctx, shop_id, "Car Oil Change"), "car", MON, 8 * H, NOW)
    # 45 min work + 15 min clean-up = 4 quarter hours, for the bay and for the mechanic.
    assert count(ctx, BookedSlot, BookedSlot.hold_id == held.id) == 8
    assert held.expires_at == NOW + timedelta(minutes=5)
    # While it's held, nobody else sees 8:00 on the only lift.
    choice = slots.day_choice(ctx, shop, slots.need_for(services(ctx, shop_id, "Car Oil Change"), "car")[0], MON, NOW,
                              booking.busy_map(ctx, shop, MON, NOW))
    assert 8 * H not in [slot.start for slot in choice.slots] and 9 * H in [slot.start for slot in choice.slots]
    with pytest.raises(booking.Taken):
        booking.hold(ctx, shop, services(ctx, shop_id, "Car Oil Change"), "car", MON, 8 * H, NOW)


def test_confirming_makes_the_booking_in_one_go(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    appointment = hold_and_confirm(ctx, shop)
    assert appointment.ref.startswith("SM-") and len(appointment.ref) == 8
    assert appointment.starts_at == manila(MON, 8) and appointment.ends_at == manila(MON, 9)
    assert appointment.status == BOOKED and appointment.price_from == 180000
    assert booking.token_matches(appointment, appointment.token)
    assert count(ctx, Hold) == 0
    assert count(ctx, BookedSlot, BookedSlot.appointment_id == appointment.id) == 8
    vehicle = ctx.get(Vehicle, appointment.vehicle_id)
    assert vehicle.plate_key == "ABC1234" and vehicle.label == "Toyota Vios · ABC 1234"


def test_pressing_book_twice_makes_one_booking(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    held = booking.hold(ctx, shop, services(ctx, shop_id, "Car Oil Change"), "car", MON, 8 * H, NOW)
    first = booking.confirm(ctx, shop, held, details(), "same-key", NOW)
    second = booking.confirm(ctx, shop, held, details(), "same-key", NOW)
    assert first.id == second.id and count(ctx, Appointment) == 1


def test_a_hold_that_ran_out_and_was_taken_cant_be_confirmed(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    held = booking.hold(ctx, shop, services(ctx, shop_id, "Car Oil Change"), "car", MON, 8 * H, NOW)
    later = NOW + timedelta(minutes=6)
    # Someone else holds 8:00 after the first hold ran out: that purges the old one.
    other = booking.hold(ctx, shop, services(ctx, shop_id, "Car Oil Change"), "car", MON, 8 * H, later)
    assert other.id != held.id
    with pytest.raises(booking.Gone):
        booking.confirm(ctx, shop, held, details(), "late-key", later)
    assert count(ctx, Appointment) == 0


def test_a_hold_that_ran_out_but_wasnt_taken_still_works(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    held = booking.hold(ctx, shop, services(ctx, shop_id, "Car Oil Change"), "car", MON, 8 * H, NOW)
    appointment = booking.confirm(ctx, shop, held, details(), "slow-key", NOW + timedelta(minutes=8))
    assert appointment.status == BOOKED  # nobody took the cells, so nothing was lost


def test_customers_and_vehicles_are_found_again(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    hold_and_confirm(ctx, shop, key="a")
    hold_and_confirm(ctx, shop, start=10 * H, key="b", phone="+63 917 555 0142", plate="abc-1234")
    assert count(ctx, Customer) == 1 and count(ctx, Vehicle) == 1
    with pytest.raises(booking.BookingError, match="saved as a car"):
        held = booking.hold(ctx, shop, services(ctx, shop_id, "Motorcycle Oil Change"), "moto", MON, 13 * H, NOW)
        booking.confirm(ctx, shop, held, details(), "c", NOW)
    assert count(ctx, Appointment) == 2  # the failed one left nothing behind
    assert count(ctx, BookedSlot, BookedSlot.appointment_id.is_(None)) == 4  # only its hold is left (30 min x 2)


def test_the_booking_is_a_copy_of_the_prices_then(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    appointment = hold_and_confirm(ctx, shop)
    services(ctx, shop_id, "Car Oil Change")[0].price_from = 999900
    ctx.commit()
    assert ctx.get(Appointment, appointment.id).lines[0].price_from == 180000


# ---------- Move and cancel ----------

def test_moving_frees_the_old_time_and_takes_the_new(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    appointment = hold_and_confirm(ctx, shop)
    booking.move(ctx, shop, appointment, MON, 8 * H + 30, NOW)  # half an hour later, into its own old time
    assert appointment.starts_at == manila(MON, 8, 30) and appointment.version == 2
    taken = {row for row in ctx.scalars(select(BookedSlot.slot_start).where(BookedSlot.appointment_id == appointment.id))}
    assert min(taken) == manila(MON, 8, 30) and len(taken) == 4
    assert count(ctx, BookedSlot) == 8  # the old cells are gone


def test_moving_onto_someone_elses_time_fails_and_changes_nothing(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    first = hold_and_confirm(ctx, shop, key="a")
    second = hold_and_confirm(ctx, shop, start=10 * H, key="b", plate="XYZ 9876")
    with pytest.raises(booking.Taken):
        booking.move(ctx, shop, second, MON, 8 * H + 30, NOW)
    ctx.refresh(second)
    assert second.starts_at == manila(MON, 10) and first.starts_at == manila(MON, 8)
    assert count(ctx, BookedSlot, BookedSlot.appointment_id == second.id) == 8


def test_cancelling_frees_the_time(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    appointment = hold_and_confirm(ctx, shop)
    booking.cancel(ctx, shop, appointment, "Car sold", NOW)
    assert appointment.status == CANCELLED and appointment.cancel_reason == "Car sold"
    assert count(ctx, BookedSlot) == 0
    again = hold_and_confirm(ctx, shop, key="again")  # 8:00 is free again
    assert again.starts_at == manila(MON, 8)
    with pytest.raises(booking.BookingError, match="already cancelled"):
        booking.cancel(ctx, shop, appointment, "", NOW)


def test_customers_cant_change_a_booking_close_to_its_start(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    appointment = hold_and_confirm(ctx, shop)
    late = appointment.starts_at - timedelta(hours=11)  # the cut-off is 12 hours
    with pytest.raises(booking.TooLate):
        booking.cancel(ctx, shop, appointment, "", late)
    booking.cancel(ctx, shop, appointment, "Called the shop", late, by_staff=True)  # staff always can
    assert appointment.status == CANCELLED


def test_an_old_form_cant_overwrite_a_newer_change(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    appointment = hold_and_confirm(ctx, shop)
    seen = appointment.version  # the customer opens the page...
    booking.move(ctx, shop, appointment, MON, 9 * H, NOW, version=seen, by_staff=True)  # ...staff move it...
    with pytest.raises(booking.Changed):
        booking.cancel(ctx, shop, appointment, "", NOW, version=seen)  # ...and the old page's Cancel is refused
    assert appointment.status == BOOKED


def test_two_sessions_changing_at_once_one_loses(app, shop_id):
    """Optimistic locking: both load version 1; the second save finds version 2 and fails."""
    from sqlalchemy.orm import Session
    from sqlalchemy.orm.exc import StaleDataError
    with app.app_context():
        s = db.session()
        appointment_id = hold_and_confirm(s, s.get(Shop, shop_id)).id
        first, second = Session(db.engine()), Session(db.engine())
        a, b = first.get(Appointment, appointment_id), second.get(Appointment, appointment_id)
        a.notes = "Bring the old parts back"
        first.commit()
        b.notes = "Customer will wait"
        with pytest.raises(StaleDataError):
            second.commit()
        first.close()
        second.close()


# ---------- Races ----------

def race(app, shop_id, workers, start=8 * H, names=("Car Oil Change",)):
    """`workers` threads hold the same time at the same moment. Returns (holds made, times refused)."""
    barrier = threading.Barrier(workers)
    won, lost, errors = [], [], []

    def one():
        with app.app_context():
            s = db.session()
            shop = s.get(Shop, shop_id)
            chosen = services(s, shop_id, *names)
            barrier.wait()  # everyone goes at once
            try:
                won.append(booking.hold(s, shop, chosen, "car", MON, start, NOW).id)
            except booking.Taken:
                lost.append(1)
            except Exception as error:  # noqa: BLE001 - any other error fails the test below
                errors.append(repr(error))
    threads = [threading.Thread(target=one) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    return won, lost


def no_double_cells(app):
    with app.app_context():
        s = db.session()
        rows = s.execute(select(BookedSlot.resource_kind, BookedSlot.resource_id, BookedSlot.slot_start)).all()
    return len(rows) == len(set(rows))


def test_ten_people_one_lift_one_winner(app, shop_id):
    won, lost = race(app, shop_id, 10)
    assert len(won) == 1 and len(lost) == 9
    assert no_double_cells(app)


def test_ten_people_two_lifts_two_mechanics_two_winners(app, shop_id):
    with app.app_context():
        s = db.session()
        lift = s.scalar(select(Bay).where(Bay.shop_id == shop_id, Bay.name == "Lift 1"))
        s.add(Bay(shop_id=shop_id, name="Lift 2", bay_type_id=lift.bay_type_id, position=9))
        ana = User(shop_id=shop_id, email="ana@shop.example", name="Ana", role=MECHANIC, password_hash="x",
                   created_at=NOW)
        ana.skills = list(s.scalars(select(Skill).where(Skill.shop_id == shop_id)))
        s.add(ana)
        s.commit()
    won, lost = race(app, shop_id, 10)
    assert len(won) == 2 and len(lost) == 8
    with app.app_context():
        held = db.session().scalars(select(Hold).where(Hold.id.in_(won))).all()
        assert len({h.bay_id for h in held}) == 2
        assert len({h.mechanic_id for h in held}) == 2
    assert no_double_cells(app)


def test_overlapping_jobs_race_too(app, shop_id):
    """Different start times that overlap (8:00 for 1 h, 8:30 for 1 h) can't both win the one lift."""
    barrier = threading.Barrier(2)
    results = []

    def one(start):
        with app.app_context():
            s = db.session()
            shop = s.get(Shop, shop_id)
            chosen = services(s, shop_id, "Car Oil Change")
            barrier.wait()
            try:
                booking.hold(s, shop, chosen, "car", MON, start, NOW)
                results.append("won")
            except booking.Taken:
                results.append("taken")
    threads = [threading.Thread(target=one, args=(start,)) for start in (8 * H, 8 * H + 30)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["taken", "won"]
    assert no_double_cells(app)


def test_the_database_itself_refuses_a_double_cell(ctx, shop_id):
    """Even code that forgot to check would be stopped: the UNIQUE rule is in the database."""
    from sqlalchemy.exc import IntegrityError
    shop = ctx.get(Shop, shop_id)
    appointment = hold_and_confirm(ctx, shop)
    ctx.add(BookedSlot(shop_id=shop_id, resource_kind="bay", resource_id=appointment.bay_id,
                       slot_start=appointment.starts_at, appointment_id=appointment.id))
    with pytest.raises(IntegrityError):
        ctx.commit()
    ctx.rollback()


def test_too_many_holds_at_once(ctx, shop_id, monkeypatch):
    monkeypatch.setattr(booking, "MAX_HOLDS", 1)
    shop = ctx.get(Shop, shop_id)
    booking.hold(ctx, shop, services(ctx, shop_id, "Car Oil Change"), "car", MON, 8 * H, NOW)
    with pytest.raises(booking.BookingError, match="very busy"):
        booking.hold(ctx, shop, services(ctx, shop_id, "Car Oil Change"), "car", MON, 10 * H, NOW)


def test_time_rules_still_apply_when_holding(ctx, shop_id):
    shop = ctx.get(Shop, shop_id)
    oil = services(ctx, shop_id, "Car Oil Change")
    with pytest.raises(booking.Taken):
        booking.hold(ctx, shop, oil, "car", MON, 12 * H, NOW)  # lunch
    with pytest.raises(booking.Taken):
        booking.hold(ctx, shop, oil, "car", MON - timedelta(days=1), 9 * H, NOW)  # Sunday
    with pytest.raises(booking.Taken):
        booking.hold(ctx, shop, oil, "car", MON - timedelta(days=2), 9 * H, NOW)  # now: too soon

