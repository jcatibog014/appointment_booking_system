"""Filters for templates: money, times, days and lengths, the way people in the shop say them."""
from .dates import clock as minutes_to_time
from .dates import to_local


def peso(centavos):
    """125050 -> "₱1,250.50"; whole pesos drop the ".00" (350000 -> "₱3,500")."""
    if centavos is None:
        return ""
    sign = "-" if centavos < 0 else ""
    pesos, cents = divmod(abs(int(centavos)), 100)
    return f"{sign}₱{pesos:,}" + (f".{cents:02d}" if cents else "")


def pesos_input(centavos):
    """What a price field shows: 125050 -> "1250.50", 350000 -> "3500"."""
    if centavos is None:
        return ""
    pesos, cents = divmod(int(centavos), 100)
    return f"{pesos}.{cents:02d}" if cents else str(pesos)


def clock(value):
    """time(13, 30) -> "1:30 PM" (the 12-hour clock people use here)."""
    if value is None:
        return ""
    hour = value.hour % 12 or 12
    return f"{hour}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"


def slot_time(start_minutes):
    """Minutes since midnight -> "8:30 AM" (510 -> "8:30 AM")."""
    return clock(minutes_to_time(start_minutes))


def duration(total_minutes):
    """90 -> "1 h 30 min", 120 -> "2 h", 45 -> "45 min"."""
    hours, mins = divmod(int(total_minutes or 0), 60)
    if hours and mins:
        return f"{hours} h {mins} min"
    return f"{hours} h" if hours else f"{mins} min"


def day(value):
    """date(2026, 11, 5) -> "Thu 5 Nov"."""
    return f"{value:%a} {value.day} {value:%b}" if value else ""


def long_day(value):
    """date(2026, 11, 5) -> "Thursday, 5 November 2026"."""
    return f"{value:%A}, {value.day} {value:%B %Y}" if value else ""


def day_range(first, last):
    """"Thu 5 Nov" or "Thu 5 Nov – Fri 6 Nov"."""
    return day(first) if first == last else f"{day(first)} – {day(last)}"


def local_moment(moment, tz_name="Asia/Manila"):
    """Naive UTC -> "Thu 5 Nov, 1:30 PM" on the shop's clock."""
    if moment is None:
        return ""
    local = to_local(moment, tz_name)
    return f"{day(local.date())}, {clock(local.time())}"


def init_app(app):
    app.jinja_env.filters.update(peso=peso, pesos_input=pesos_input, clock=clock, slot_time=slot_time, duration=duration, day=day,
                                 long_day=long_day, day_range=day_range, local_moment=local_moment)
