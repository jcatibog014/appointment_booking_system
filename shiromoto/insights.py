"""Insights for the owner: how busy the shop is, where time is lost, where money comes from.

Everything is for one period (the last 7, 30 or 90 days, on the shop's calendar).
The sums are plain Python over the period's bookings, so the tests can check each
number by hand. Charts are drawn as SVG from here: the page's CSP allows no
inline styles, so positions are attributes and colours are classes (style.css).

Money is "expected": the starting prices of the booked services, plus estimate
lines the customer approved. The shop has no payments in the app.
"""
from dataclasses import dataclass
from datetime import time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from . import shops
from .dates import at, local_today, minutes, to_local
from .formatting import duration, peso
from .models import APPROVED, CANCELLED, CHECKED_IN, LIVE_STATUSES, NO_SHOW, SOURCE_LABELS, WEEKDAYS, Appointment, \
    Bay, BookedSlot, Job

PERIODS = (7, 30, 90)


@dataclass
class Bar:
    label: str
    value: float
    text: str  # the value as people read it: "12", "₱3,500", "64%"
    x: float = 0
    y: float = 0
    width: float = 0
    height: float = 0
    path: str = ""
    labelled: bool = False  # a direct label on this one (the biggest, and the last)


def column_path(x, y, width, height, radius=4):
    """A bar whose top (the data end) is rounded and whose base sits square on the axis."""
    radius = min(radius, width / 2, height)
    base = y + height
    return (f"M{x:.1f},{base:.1f}V{y + radius:.1f}Q{x:.1f},{y:.1f} {x + radius:.1f},{y:.1f}"
            f"H{x + width - radius:.1f}Q{x + width:.1f},{y:.1f} {x + width:.1f},{y + radius:.1f}V{base:.1f}Z")


def row_path(x, y, width, height, radius=4):
    """A horizontal bar: rounded at its right end (the data end), square at the axis."""
    radius = min(radius, height / 2, width)
    end = x + width
    return (f"M{x:.1f},{y:.1f}H{end - radius:.1f}Q{end:.1f},{y:.1f} {end:.1f},{y + radius:.1f}"
            f"V{y + height - radius:.1f}Q{end:.1f},{y + height:.1f} {end - radius:.1f},{y + height:.1f}"
            f"H{x:.1f}Z")


def nice_top(value):
    """A round number at or above value for the axis: 7 -> 8, 23 -> 25, 140 -> 150."""
    if value <= 0:
        return 1
    step = 10 ** (len(str(int(value))) - 1)
    for factor in (1, 2, 2.5, 5, 10):
        if value <= factor * step:
            return factor * step
    return 10 * step


def columns(labels, values, texts, width=640, height=180, gap=2):
    """Vertical bars over time. Returns (bars, ticks): ticks are (y, label) for the grid."""
    top = nice_top(max(values, default=0))
    slot = width / max(len(values), 1)
    bars = []
    biggest = max(range(len(values)), key=lambda i: values[i]) if values else None
    for n, (label, value, text) in enumerate(zip(labels, values, texts)):
        bar_height = height * value / top
        bar = Bar(label=label, value=value, text=text, x=n * slot + gap / 2, y=height - bar_height,
                  width=max(slot - gap, 1), height=bar_height, labelled=n == biggest and value > 0)
        bar.path = column_path(bar.x, bar.y, bar.width, bar.height) if value else ""
        bars.append(bar)
    ticks = [(height - height * f, f"{top * f:g}") for f in (0, .5, 1)]
    return bars, ticks


def rows(labels, values, texts, width=300, row_height=26, gap=2):
    """Horizontal bars, one per name, biggest first. Returns the bars."""
    top = max(values, default=0) or 1
    bars = []
    for n, (label, value, text) in enumerate(zip(labels, values, texts)):
        bar = Bar(label=label, value=value, text=text, x=0, y=n * (row_height + gap * 4), width=width * value / top,
                  height=row_height, labelled=True)
        bar.path = row_path(0, bar.y, bar.width, row_height) if value else ""
        bars.append(bar)
    return bars


# ---------- The numbers ----------

def period_bounds(shop, days, now):
    last = local_today(shop.timezone, now)
    first = last - timedelta(days=days - 1)
    return first, last, at(first, time(0), shop.timezone), at(last + timedelta(days=1), time(0), shop.timezone)


def open_minutes(s, shop, first, last):
    """Minutes the shop was open in the period, without breaks and closed days."""
    week = shops.hours_by_weekday(s, shop)
    closures = shops.closures_between(s, shop, first, last)
    total = 0
    day = first
    while day <= last:
        hours = week[day.weekday()]
        shut = any(c.user_id is None and c.from_time is None and c.starts_on <= day <= c.ends_on for c in closures)
        if hours.is_open and not shut:
            total += minutes(hours.closes) - minutes(hours.opens)
            if hours.break_starts:
                total -= minutes(hours.break_ends) - minutes(hours.break_starts)
        day += timedelta(days=1)
    return total


def build(s, shop, days, now):
    first, last, start, end = period_bounds(shop, days, now)
    visits = s.scalars(select(Appointment).where(
        Appointment.shop_id == shop.id, Appointment.starts_at >= start, Appointment.starts_at < end)
        .options(selectinload(Appointment.lines), selectinload(Appointment.mechanic))).all()
    jobs = {job.appointment_id: job for job in s.scalars(
        select(Job).where(Job.appointment_id.in_([v.id for v in visits])).options(selectinload(Job.lines)))}
    tz = shop.timezone

    made = [v for v in visits if v.status != CANCELLED]
    happened = [v for v in visits if v.status in (CHECKED_IN, NO_SHOW)]
    cancelled = [v for v in visits if v.status == CANCELLED]
    no_shows = [v for v in visits if v.status == NO_SHOW]

    # Bookings per day (everything not cancelled).
    per_day = {first + timedelta(days=n): 0 for n in range(days)}
    for visit in made:
        per_day[to_local(visit.starts_at, tz).date()] += 1
    day_labels = [f"{d:%a} {d.day}" if days <= 7 else f"{d.day} {d:%b}" for d in per_day]
    day_bars, day_ticks = columns(day_labels, list(per_day.values()), [str(v) for v in per_day.values()])

    # Busiest hours: weekday x hour, by start time.
    week = shops.hours_by_weekday(s, shop)
    open_hours = [h for h in range(24) if any(
        row.is_open and minutes(row.opens) <= h * 60 < minutes(row.closes) for row in week.values())]
    heat = {(d, h): 0 for d in range(7) for h in open_hours}
    for visit in made:
        local = to_local(visit.starts_at, tz)
        if (local.weekday(), local.hour) in heat:
            heat[(local.weekday(), local.hour)] += 1
    peak = max(heat.values(), default=0)
    heat_rows = [(WEEKDAYS[d][:3], [(h, heat[(d, h)], step(heat[(d, h)], peak)) for h in open_hours])
                 for d in range(7) if week[d].is_open or any(heat[(d, h)] for h in open_hours)]

    # Bay use: booked quarter hours / open minutes, per bay.
    available = open_minutes(s, shop, first, last)
    used = dict(s.execute(select(BookedSlot.resource_id, func.count()).where(
        BookedSlot.shop_id == shop.id, BookedSlot.resource_kind == "bay", BookedSlot.appointment_id.is_not(None),
        BookedSlot.slot_start >= start, BookedSlot.slot_start < end).group_by(BookedSlot.resource_id)).all())
    bays = s.scalars(select(Bay).where(Bay.shop_id == shop.id, Bay.is_active.is_(True))
                     .order_by(Bay.position)).all()
    use = sorted(((bay.name, min(100.0, 100 * used.get(bay.id, 0) * 15 / available) if available else 0.0)
                  for bay in bays), key=lambda pair: -pair[1])
    bay_bars = rows([name for name, _ in use], [pct for _, pct in use], [f"{pct:.0f}%" for _, pct in use])

    # Money: booked services' starting prices + approved estimate lines, for visits that happened.
    by_service, by_mechanic = {}, {}
    for visit in (v for v in visits if v.status == CHECKED_IN):
        extra = sum(line.amount for line in jobs[visit.id].lines if line.status == APPROVED) if visit.id in jobs else 0
        for line in visit.lines:
            by_service[line.name] = by_service.get(line.name, 0) + line.price_from
        if extra:
            by_service["Approved extra work"] = by_service.get("Approved extra work", 0) + extra
        by_mechanic[visit.mechanic.name] = by_mechanic.get(visit.mechanic.name, 0) + visit.price_from + extra
    services_sorted = sorted(by_service.items(), key=lambda pair: -pair[1])[:8]
    mechanics_sorted = sorted(by_mechanic.items(), key=lambda pair: -pair[1])
    money = sum(by_service.values())

    reasons = {}
    for visit in cancelled:
        reason = visit.cancel_reason.strip() or "No reason given"
        reasons[reason] = reasons.get(reason, 0) + 1
    sources = {key: sum(v.source == key for v in made) for key in SOURCE_LABELS}
    work = sum(v.work_minutes for v in visits if v.status == CHECKED_IN)

    return {
        "first": first, "last": last, "days": days,
        "tiles": {
            "bookings": len(made),
            "money": peso(money),
            "no_show_rate": f"{100 * len(no_shows) / len(happened):.0f}%" if happened else "—",
            "cancel_rate": f"{100 * len(cancelled) / len(visits):.0f}%" if visits else "—",
            "work": duration(work),
            "bay_use": f"{sum(pct for _, pct in use) / len(use):.0f}%" if use else "—",
        },
        "counts": {"no_shows": len(no_shows), "happened": len(happened), "cancelled": len(cancelled),
                   "all": len(visits)},
        "day_bars": day_bars, "day_ticks": day_ticks, "day_every": max(1, days // 10),
        "heat_rows": heat_rows, "heat_hours": open_hours, "peak": peak,
        "bay_bars": bay_bars,
        "service_bars": rows([name for name, _ in services_sorted], [v for _, v in services_sorted],
                             [peso(v) for _, v in services_sorted]),
        "mechanic_bars": rows([name for name, _ in mechanics_sorted], [v for _, v in mechanics_sorted],
                              [peso(v) for _, v in mechanics_sorted]),
        "reasons": sorted(reasons.items(), key=lambda pair: -pair[1]),
        "sources": sources,
    }


def step(value, peak):
    """Which of the 5 heatmap shades a count gets (0 = none: the cell stays empty)."""
    if not value or not peak:
        return 0
    return min(5, 1 + int(4 * value / peak + 0.0001)) if value < peak else 5
