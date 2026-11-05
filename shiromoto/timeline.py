"""The desk's timeline: one row per bay (or per mechanic), one column per 15 minutes.

The page's CSP allows no inline styles, so a block can't say style="left: 40%".
Instead the grid has one column per quarter hour, and a block gets two classes,
gs-N (start column) and ge-N (end column); style.css has a rule for each N.
Column 1 is the row's name, so 8:00 (the opening) is column 2.
"""
from dataclasses import dataclass, field
from datetime import time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from . import shops, slots
from .dates import at, minutes, to_local
from .models import LIVE_STATUSES, NO_SHOW, Appointment, Bay, Hold

MAX_COLUMNS = 96  # a whole day of quarter hours; style.css has gs-/ge- rules up to 98


@dataclass
class Block:
    start: int  # minutes since midnight, on the shop's clock
    end: int
    kind: str  # "booking", "hold" or "break"
    appointment: object = None
    gs: int = 0
    ge: int = 0


@dataclass
class Row:
    id: int
    name: str
    detail: str
    blocks: list = field(default_factory=list)


@dataclass
class Board:
    day: object
    opens: int
    closes: int
    closed: str | None
    rows: list
    hours: list  # (label, gs, ge) for the header
    last: int  # the last column line, so every row has the same columns
    now_column: int | None = None


def column(minute, opens):
    return (minute - opens) // slots.STEP + 2


def place(block, opens, closes):
    """Clip a block to the opening hours and give it its columns. None if it's outside them."""
    start, end = max(block.start, opens), min(block.end, closes)
    if end <= start:
        return None
    block.gs, block.ge = column(start, opens), column(slots.round_up(end), opens)
    return block


def local_minutes(moment, tz):
    local = to_local(moment, tz)
    return local.hour * 60 + local.minute


def day_items(s, shop, day, now):
    """The day's bookings (live and no-shows) and live holds."""
    first = at(day, time(0), shop.timezone)
    last = first + timedelta(days=1)
    bookings = s.scalars(select(Appointment).where(
        Appointment.shop_id == shop.id, Appointment.status.in_((*LIVE_STATUSES, NO_SHOW)),
        Appointment.starts_at < last, Appointment.ends_at > first)
        .options(selectinload(Appointment.lines), selectinload(Appointment.customer),
                 selectinload(Appointment.vehicle)).order_by(Appointment.starts_at)).all()
    holds = s.scalars(select(Hold).where(Hold.shop_id == shop.id, Hold.expires_at > now, Hold.starts_at < last,
                                         Hold.ends_at > first)).all()
    return bookings, holds


def build(s, shop, day, now, by="bay"):
    """The timeline for one day, by bay or by mechanic."""
    hours = shops.hours_by_weekday(s, shop)[day.weekday()]
    closures = [c for c in shops.closures_between(s, shop, day, day) if c.user_id is None]
    whole_day = next((c for c in closures if c.from_time is None), None)
    if not hours.is_open or whole_day:
        reason = f"Closed: {whole_day.reason}" if whole_day else f"Closed on {day:%A}s"
        return Board(day=day, opens=0, closes=0, closed=reason, rows=[], hours=[], last=2)
    opens, closes = minutes(hours.opens), minutes(hours.closes)
    breaks = [(minutes(hours.break_starts), minutes(hours.break_ends))] if hours.break_starts else []
    breaks += [(minutes(c.from_time), minutes(c.to_time)) for c in closures if c.from_time]

    if by == "mechanic":
        people = shops.mechanics(s, shop)
        rows = [Row(id=m.id, name=m.name, detail=", ".join(k.name for k in m.skills)) for m in people]
        key = "mechanic_id"
    else:
        bays = s.scalars(select(Bay).where(Bay.shop_id == shop.id, Bay.is_active.is_(True))
                         .order_by(Bay.position, Bay.name)).all()
        rows = [Row(id=b.id, name=b.name, detail=b.bay_type.name) for b in bays]
        key = "bay_id"
    by_id = {row.id: row for row in rows}
    bookings, holds = day_items(s, shop, day, now)
    for row in rows:
        for start, end in breaks:
            block = place(Block(start=start, end=end, kind="break"), opens, closes)
            if block:
                row.blocks.append(block)
    for item in bookings:
        row = by_id.get(getattr(item, key))
        block = row and place(Block(start=local_minutes(item.starts_at, shop.timezone),
                                    end=local_minutes(item.ends_at, shop.timezone), kind="booking",
                                    appointment=item), opens, closes)
        if block:
            row.blocks.append(block)
    for held in holds:
        row = by_id.get(getattr(held, key))
        block = row and place(Block(start=local_minutes(held.starts_at, shop.timezone),
                                    end=local_minutes(held.ends_at, shop.timezone), kind="hold"), opens, closes)
        if block:
            row.blocks.append(block)
    labels = []
    for hour in range(-(-opens // 60), closes // 60 + 1):
        minute = hour * 60
        if opens <= minute < closes:
            labels.append((slots_label(minute), column(minute, opens), min(column(minute + 60, opens),
                                                                          column(closes, opens))))
    local_now = to_local(now, shop.timezone)
    now_column = None
    if local_now.date() == day:
        minute = local_now.hour * 60 + local_now.minute
        if opens <= minute < closes:
            now_column = column(minute - minute % slots.STEP, opens)
    return Board(day=day, opens=opens, closes=closes, closed=None, rows=rows, hours=labels,
                 last=column(closes, opens), now_column=now_column)


def slots_label(minute):
    """600 -> "10 AM", 780 -> "1 PM": the hour marks on the timeline."""
    hour = minute // 60
    return f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"

