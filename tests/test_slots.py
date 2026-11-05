"""Free times: shop hours, mechanic hours, the lunch break, holidays, leave, notice
and the booking window, all together. Every test uses fixed days and a fixed
"now", so it gives the same answer on any day it runs.

The test week (no holidays in it), on the shop's clock (Manila, UTC+8):
    Mon 9 Nov 2026 ... Sat 14 Nov (closes 3:00 PM), Sun 15 Nov (closed).
The starter week: 8:00 AM to 5:00 PM, lunch 12:00 to 1:00.
"""
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from shiromoto import seed, slots
from shiromoto.dates import utcnow
from shiromoto.formatting import slot_time
from shiromoto.models import CLOSED, LEAVE, MECHANIC, Bay, BayType, Closure, Service, Shop, Skill, StaffHours, User

MON, TUE, WED, THU, FRI, SAT, SUN = (date(2026, 11, 9) + timedelta(days=n) for n in range(7))
H = 60  # minutes in an hour, so 8 * H + 30 is 8:30 AM


def manila(day, hour, minute=0):
    """A moment on the shop's clock -> naive UTC (Manila is UTC+8 all year)."""
    return datetime(day.year, day.month, day.day, hour, minute) - timedelta(hours=8)


# ---------- Interval arithmetic ----------

def test_subtract_cuts_holes_out_of_windows():
    assert slots.subtract([(480, 1020)], [(720, 780)]) == [(480, 720), (780, 1020)]
    assert slots.subtract([(480, 1020)], [(400, 500), (1000, 1100)]) == [(500, 1000)]
    assert slots.subtract([(480, 720)], [(480, 720)]) == []
    assert slots.subtract([(480, 720)], [(800, 900)]) == [(480, 720)]


def test_a_job_pauses_over_lunch():
    day = [(8 * H, 12 * H), (13 * H, 17 * H)]
    assert slots.finish(10 * H, 3 * H, day) == 14 * H  # 10-12, lunch, 1-2
    assert slots.finish(8 * H, 4 * H, day) == 12 * H  # exactly fills the morning
    assert slots.finish(11 * H + 45, 30, day) == 13 * H + 15


def test_a_job_never_starts_in_a_break_or_runs_past_closing():
    day = [(8 * H, 12 * H), (13 * H, 17 * H)]
    assert slots.finish(12 * H, 30, day) is None
    assert slots.finish(12 * H + 45, 15, day) is None
    assert slots.finish(16 * H, 2 * H, day) is None
    assert slots.finish(7 * H, 30, day) is None


def test_touching_jobs_dont_overlap():
    busy = [(8 * H, 10 * H)]
    assert not slots.overlaps(10 * H, 11 * H, busy)  # one ends at 10:00, the next starts at 10:00
    assert slots.overlaps(9 * H + 45, 11 * H, busy)
    assert not slots.overlaps(7 * H, 8 * H, busy)


def test_round_up_to_the_next_quarter_hour():
    assert [slots.round_up(n) for n in (510, 511, 524, 525, 0)] == [510, 525, 525, 525, 0]


# ---------- A small real shop ----------

@pytest.fixture
def shop(ctx):
    """The starter shop (Lift 1, Moto Stand 1, the 16 services) plus Car Bay 1, with three mechanics:
    Jun: Engine, Brakes, Electrical, works the shop's hours.
    Ana: Aircon only, off on Mondays.
    Rico: Motorcycle and Tires, 10:00 to 5:00 on weekdays only."""
    shop = seed.starter_shop(ctx, "ShiroMoto Garage", manila(MON, 7))
    skills = {k.name: k for k in ctx.scalars(select(Skill).where(Skill.shop_id == shop.id))}
    car_bay = ctx.scalar(select(BayType).where(BayType.shop_id == shop.id, BayType.name == "Car bay"))
    ctx.add(Bay(shop_id=shop.id, name="Car Bay 1", bay_type_id=car_bay.id, position=3))

    def mechanic(name, skill_names):
        user = User(shop_id=shop.id, email=f"{name.lower()}@shop.example", name=name, role=MECHANIC,
                    password_hash="x", created_at=utcnow())
        user.skills = [skills[n] for n in skill_names]
        ctx.add(user)
        ctx.flush()
        return user

    shop.jun = mechanic("Jun", ["Engine", "Brakes", "Electrical"])
    ana = mechanic("Ana", ["Aircon"])
    rico = mechanic("Rico", ["Motorcycle", "Tires"])
    for weekday in range(7):
        ctx.add(StaffHours(user_id=ana.id, weekday=weekday, works=weekday != 0, starts=time(8), ends=time(17)))
        ctx.add(StaffHours(user_id=rico.id, weekday=weekday, works=weekday < 5, starts=time(10), ends=time(17)))
    ctx.commit()
    return shop


def service(ctx, shop, name):
    return ctx.scalar(select(Service).where(Service.shop_id == shop.id, Service.name == name))


def need(ctx, shop, *names, vehicle="car"):
    result, problem = slots.need_for([service(ctx, shop, n) for n in names], vehicle)
    assert problem is None, problem
    return result


def times(ctx, shop, names, day, vehicle="car", busy=None, earliest=0):
    """The start times as text ("8:00 AM"), and the reason when there are none."""
    found, reason = slots.free_slots(slots.load_day(ctx, shop, day, busy), need(ctx, shop, *names, vehicle=vehicle),
                                     earliest)
    return [slot_time(slot.start) for slot in found], reason


def add_closure(ctx, shop, day, reason, kind=CLOSED, user=None, hours=None, last=None):
    ctx.add(Closure(shop_id=shop.id, user_id=user.id if user else None, kind=kind, reason=reason, starts_on=day,
                    ends_on=last or day, from_time=hours[0] if hours else None, to_time=hours[1] if hours else None))
    ctx.commit()


# ---------- What a visit needs ----------

def test_services_add_up_and_the_longest_clean_up_counts(ctx, shop):
    both = need(ctx, shop, "Car Oil Change", "Brake Service")
    assert (both.work_minutes, both.buffer_minutes, both.total) == (45 + 90, 15, 150)
    assert both.skill_names == ("Brakes",)


def test_one_visit_uses_one_bay(ctx, shop):
    _, problem = slots.need_for([service(ctx, shop, "Car PMS"), service(ctx, shop, "Wheel Alignment")], "car")
    assert problem == "These services need different bays (alignment bay and car lift). Book them as separate visits."
    _, problem = slots.need_for([service(ctx, shop, "CVT Cleaning")], "car")
    assert problem == "CVT Cleaning isn't for this vehicle."
    assert slots.need_for([], "car") == (None, "Choose at least one service.")


# ---------- One day ----------

def test_a_normal_monday_for_a_car_pms(ctx, shop):
    # 3 h of work + 15 min clean-up = 195 working minutes, and only Jun has Engine.
    found, reason = times(ctx, shop, ["Car PMS"], MON)
    assert reason is None
    # Mornings from 8:00 to 11:45 (the last one pauses over lunch), then 1:00 to 1:45.
    assert found[0] == "8:00 AM" and "11:45 AM" in found
    assert "12:00 PM" not in found and "12:45 PM" not in found  # nobody starts at lunch
    assert found[-1] == "1:45 PM"  # 1:45 + 3 h 15 min = 5:00 PM, closing time
    assert len(found) == 16 + 4


def test_ready_time_skips_lunch(ctx, shop):
    found, _ = slots.free_slots(slots.load_day(ctx, shop, MON), need(ctx, shop, "Car PMS"))
    ten = next(slot for slot in found if slot.start == 10 * H)
    assert ten.ready == 14 * H + 15  # 10-12 (2 h), lunch, 1:00-2:15 (1 h 15 min)
    assert [m.name for m in ten.mechanics] == ["Jun"] and [b.name for b in ten.bays] == ["Lift 1"]


def test_saturday_closes_early(ctx, shop):
    found, _ = times(ctx, shop, ["Car PMS"], SAT)
    assert found[-1] == "10:45 AM"  # 10:45-12:00 (1 h 15 min) + 1:00-3:00 (2 h) = 3 h 15 min
    assert not any(t.endswith("PM") for t in found)


def test_sunday_and_holidays_are_closed(ctx, shop):
    assert times(ctx, shop, ["Car Oil Change"], SUN) == ([], "Closed on Sundays")
    assert times(ctx, shop, ["Car Oil Change"], date(2026, 11, 30)) == ([], "Closed: Bonifacio Day")


def test_a_part_day_closure_cuts_the_afternoon(ctx, shop):
    add_closure(ctx, shop, WED, "Staff training", hours=(time(13), time(17)))
    found, _ = times(ctx, shop, ["Car PMS"], WED)
    assert found == ["8:00 AM", "8:15 AM", "8:30 AM", "8:45 AM"]  # 195 minutes must fit before noon
    quick, _ = times(ctx, shop, ["Car Oil Change"], WED)
    assert quick[-1] == "11:00 AM"
    add_closure(ctx, shop, THU, "Inventory", hours=(time(8), time(12)))
    long_job, reason = times(ctx, shop, ["Car PMS", "Brake Service"], THU)
    assert long_job == [] and reason == "A 4 h 45 min job doesn't fit in this day."


def test_a_mechanic_on_leave(ctx, shop):
    add_closure(ctx, shop, THU, "Vacation", kind=LEAVE, user=shop.jun)
    assert times(ctx, shop, ["Car PMS"], THU) == ([], "No mechanic who does this works on this day.")
    # Part of a day: Jun is at the doctor until 10:00.
    add_closure(ctx, shop, FRI, "Doctor", kind=LEAVE, user=shop.jun, hours=(time(8), time(10)))
    assert times(ctx, shop, ["Car PMS"], FRI)[0][0] == "10:00 AM"


def test_a_mechanics_own_week(ctx, shop):
    # Aircon is Ana's alone, and Ana is off on Mondays.
    assert times(ctx, shop, ["Aircon Cleaning"], MON) == ([], "No mechanic who does this works on this day.")
    assert times(ctx, shop, ["Aircon Cleaning"], TUE)[0][0] == "8:00 AM"
    # Rico starts at 10:00 and doesn't work Saturdays.
    assert times(ctx, shop, ["Motorcycle PMS"], MON, vehicle="moto")[0][0] == "10:00 AM"
    assert times(ctx, shop, ["Motorcycle PMS"], SAT, vehicle="moto")[0] == []


def test_a_skill_nobody_has_and_a_bay_nobody_has(ctx, shop):
    painting = Skill(shop_id=shop.id, name="Painting")
    ctx.add(painting)
    ctx.flush()
    service(ctx, shop, "Car Oil Change").skill_id = painting.id
    ctx.commit()
    assert times(ctx, shop, ["Car Oil Change"], MON) == ([], "No mechanic has the Painting skill.")
    # Wheel Alignment needs an alignment bay, and the starter shop has none.
    assert times(ctx, shop, ["Wheel Alignment"], MON) == ([], "No alignment bay is in use.")
    lift = ctx.scalar(select(Bay).where(Bay.shop_id == shop.id, Bay.name == "Lift 1"))
    lift.is_active = False
    ctx.commit()
    assert times(ctx, shop, ["Car PMS"], MON) == ([], "No car lift is in use.")


def test_a_switched_off_mechanic_gets_no_jobs(ctx, shop):
    shop.jun.is_active = False
    ctx.commit()
    assert times(ctx, shop, ["Car PMS"], MON)[1] == "No mechanic has the Engine skill."


# ---------- Bookings already made (stage 3 fills these in) ----------

def test_a_busy_mechanic_or_bay_blocks_the_time(ctx, shop):
    jun_busy = {("mechanic", shop.jun.id): [(8 * H, 11 * H + 15)]}
    found, _ = times(ctx, shop, ["Car PMS"], MON, busy=jun_busy)
    assert found[0] == "11:15 AM"  # right after his first job
    lift = ctx.scalar(select(Bay).where(Bay.shop_id == shop.id, Bay.name == "Lift 1"))
    found, _ = times(ctx, shop, ["Car PMS"], MON, busy={("bay", lift.id): [(8 * H, 12 * H)]})
    assert found == ["1:00 PM", "1:15 PM", "1:30 PM", "1:45 PM"]  # 11:45 would share the lift until noon
    full = {("bay", lift.id): [(8 * H, 17 * H)]}
    assert times(ctx, shop, ["Car PMS"], MON, busy=full) == ([], "Fully booked.")


def test_a_second_lift_doubles_the_choices(ctx, shop):
    lift = ctx.scalar(select(Bay).where(Bay.shop_id == shop.id, Bay.name == "Lift 1"))
    ctx.add(Bay(shop_id=shop.id, name="Lift 2", bay_type_id=lift.bay_type_id, position=5))
    ctx.commit()
    found, _ = slots.free_slots(slots.load_day(ctx, shop, MON, {("bay", lift.id): [(8 * H, 12 * H)]}),
                                need(ctx, shop, "Car PMS"))
    assert found[0].start == 8 * H and [b.name for b in found[0].bays] == ["Lift 2"]
    assert [b.name for b in found[-1].bays] == ["Lift 1", "Lift 2"]  # in the afternoon both are free


# ---------- Notice and the booking window ----------

def test_minimum_notice_today(ctx, shop):
    now = manila(MON, 9, 10)  # 2 hours' notice -> 11:10 -> the next quarter hour, 11:15
    assert slots.earliest_minute(shop, MON, now) == 11 * H + 15
    assert slots.earliest_minute(shop, TUE, now) == 0
    choice = slots.day_choice(ctx, shop, need(ctx, shop, "Car Oil Change"), MON, now)
    assert slot_time(choice.slots[0].start) == "11:15 AM"


def test_late_in_the_day_nothing_is_left(ctx, shop):
    choice = slots.day_choice(ctx, shop, need(ctx, shop, "Car Oil Change"), MON, manila(MON, 16, 30))
    assert choice.slots == [] and choice.reason == "The times left on this day are too soon to book."


def test_a_days_notice_reaches_into_tomorrow(ctx, shop):
    shop.min_notice_minutes = 24 * 60
    ctx.commit()
    now = manila(MON, 9, 10)
    assert slots.earliest_minute(shop, MON, now) is None
    assert slots.earliest_minute(shop, TUE, now) == 9 * H + 15
    assert slots.day_choice(ctx, shop, need(ctx, shop, "Car Oil Change"), MON, now).reason == \
        "It's too soon to book this day."


def test_the_booking_window(ctx, shop):
    now = manila(MON, 7)
    oil = need(ctx, shop, "Car Oil Change")
    assert slots.day_choice(ctx, shop, oil, MON - timedelta(days=1), now).reason == "This day has passed."
    assert slots.day_choice(ctx, shop, oil, MON + timedelta(days=31), now).reason == "Bookings open 30 days ahead."
    assert slots.day_choice(ctx, shop, oil, MON + timedelta(days=30), now).count > 0


def test_today_is_the_shops_today_not_utcs(ctx, shop):
    # 11:30 PM on Sunday in UTC is already 7:30 AM on Monday in Manila.
    now = datetime(2026, 11, 8, 23, 30)
    assert slots.bookable_days(shop, now)[0] == MON
    assert slots.earliest_minute(shop, MON, now) == 9 * H + 30


def test_a_week_of_choices(ctx, shop):
    week = slots.week_of_choices(ctx, shop, need(ctx, shop, "Car Oil Change"), MON, manila(MON, 7))
    assert [choice.day for choice in week] == [MON, TUE, WED, THU, FRI, SAT, SUN]
    assert week[6].count == 0 and week[6].reason == "Closed on Sundays"
    assert week[5].count < week[0].count  # Saturday is shorter


# ---------- From minutes to a real moment ----------

def test_a_start_becomes_utc(ctx, shop):
    assert slots.start_moment(shop, MON, 8 * H) == datetime(2026, 11, 9, 0, 0)
    assert slots.start_moment(shop, MON, 7 * H) == datetime(2026, 11, 8, 23, 0)  # the day before, in UTC


def test_daylight_saving_moves_utc_but_not_the_shops_clock():
    """Manila has no daylight saving. If the shop were in New York, 9:00 AM would be
    14:00 UTC before 8 March 2026 and 13:00 UTC after it: the shop's clock stays 9:00."""
    elsewhere = Shop(name="Test", timezone="America/New_York")
    assert slots.start_moment(elsewhere, date(2026, 3, 7), 9 * H) == datetime(2026, 3, 7, 14, 0)
    assert slots.start_moment(elsewhere, date(2026, 3, 9), 9 * H) == datetime(2026, 3, 9, 13, 0)
