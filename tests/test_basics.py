"""Small pure pieces: money, times, form fields, and the Philippine holiday calendar."""
from datetime import date, datetime, time

from werkzeug.datastructures import MultiDict

from shiromoto import forms, shops
from shiromoto.dates import at, clock, local_today, minutes, to_local, to_utc, week_start
from shiromoto.formatting import duration, peso, pesos_input
from shiromoto.formatting import clock as clock_text


# ---------- Money ----------

def test_peso_shows_whole_pesos_without_cents():
    assert peso(350000) == "₱3,500"
    assert peso(125050) == "₱1,250.50"
    assert peso(5) == "₱0.05"
    assert peso(-2000) == "-₱20"


def test_prices_are_read_as_whole_centavos_never_floats():
    def read(text):
        return forms.pesos(MultiDict({"price": text}), "price")
    assert read("1,250.50") == (125050, None)
    assert read("₱3500") == (350000, None)
    assert read("0.1") == (10, None)  # 10 centavos, not 0.1000000001
    assert read("") == (0, None)
    for wrong in ("12.345", "abc", "-5", "1e3"):
        value, problem = read(wrong)
        assert value is None and problem
    assert pesos_input(125050) == "1250.50" and pesos_input(350000) == "3500"


# ---------- Times ----------

def test_times_read_the_way_people_say_them():
    assert clock_text(time(13, 30)) == "1:30 PM"
    assert clock_text(time(0, 0)) == "12:00 AM"
    assert clock_text(time(12, 0)) == "12:00 PM"
    assert duration(90) == "1 h 30 min" and duration(120) == "2 h" and duration(45) == "45 min"


def test_manila_has_no_daylight_saving_so_it_is_always_utc_plus_8():
    for month in (1, 7):
        moment = to_utc(datetime(2026, month, 5, 8, 0))
        assert moment == datetime(2026, month, 5, 0, 0)
        assert to_local(moment).hour == 8


def test_a_morning_booking_in_manila_is_the_evening_before_in_utc():
    # 7:00 AM on 5 Nov in Manila is still 4 Nov in UTC. Day boundaries must use the shop's clock.
    assert at(date(2026, 11, 5), time(7)) == datetime(2026, 11, 4, 23, 0)


def test_minutes_and_clock_go_both_ways():
    assert minutes(time(8, 30)) == 510
    assert clock(510) == time(8, 30)
    assert week_start(date(2026, 11, 8)) == date(2026, 11, 2)  # Sunday -> its Monday


def test_quarter_hour_times_only():
    def read(text, required=True):
        return forms.quarter_time(MultiDict({"t": text}), "t", required)
    assert read("08:15") == (time(8, 15), None)
    assert read("08:10")[1] == "Use a time on the quarter hour, like 8:00, 8:15 or 8:30."
    assert read("")[1] == "Choose a time."
    assert read("", required=False) == (None, None)
    assert read("25:00")[1]


# ---------- Holidays ----------

def test_easter_moves_every_year():
    assert shops.easter(2026) == date(2026, 4, 5)
    assert shops.easter(2027) == date(2027, 3, 28)
    assert shops.easter(2038) == date(2038, 4, 25)  # about as late as it gets


def test_national_heroes_day_is_the_last_monday_of_august():
    assert shops.last_monday(2026, 8) == date(2026, 8, 31)
    assert shops.last_monday(2027, 8) == date(2027, 8, 30)
    assert shops.last_monday(2026, 12) == date(2026, 12, 28)  # December rolls into the next year


def test_ph_holidays_for_2026():
    days = dict(shops.ph_holidays(2026))
    assert days[date(2026, 4, 2)] == "Maundy Thursday"
    assert days[date(2026, 4, 3)] == "Good Friday"
    assert days[date(2026, 8, 31)] == "National Heroes Day"
    assert days[date(2026, 12, 30)] == "Rizal Day"
    assert list(days) == sorted(days)


def test_adding_holidays_twice_adds_them_once(ctx, shop_id):
    from shiromoto.models import Shop
    shop = ctx.get(Shop, shop_id)
    # The starter shop already has this year's and next year's.
    assert shops.add_ph_holidays(ctx, shop, local_today().year) == 0
    assert shops.add_ph_holidays(ctx, shop, 2030) == len(shops.ph_holidays(2030))
    assert shops.add_ph_holidays(ctx, shop, 2030) == 0
