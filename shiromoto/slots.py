"""Free times: when can a job start, with which mechanic, in which bay?

Everything here works in MINUTES SINCE MIDNIGHT on the shop's clock, for one
day at a time: 8:30 AM is 510. Whole numbers are easy to add, compare and
test, and they never meet a time zone. Only at the very end does a start
become a real moment (dates.at turns a day + a time into UTC).

A job needs:
- a mechanic who has every skill it needs and is working (shop hours ∩ their
  own hours, minus the break, part-day closures and their leave);
- a bay of the right kind that is free for the whole job.

Work time is WORKING time. A job pauses over the lunch break, so a 3-hour job
that starts at 10:00 with lunch 12:00-13:00 ends at 14:00. The bay stays
taken over lunch (the car is still on the lift). A job must finish the same
day: a car is never booked to stay on a lift overnight.

The pure functions (subtract, finish, free_slots...) take plain numbers and
lists, so tests can call them directly. load_day() reads the database once
and builds the DayPicture they work on.
"""
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select

from . import shops
from .dates import at, clock, local_today, minutes, to_local
from .formatting import duration
from .models import ANY, Bay

STEP = 15  # a job can start every quarter hour


# ---------- Intervals: (start, end) in minutes, end not included ----------

def subtract(windows, cuts):
    """windows minus cuts. [(480, 1020)] minus [(720, 780)] -> [(480, 720), (780, 1020)]."""
    result = []
    for start, end in windows:
        pieces = [(start, end)]
        for cut_start, cut_end in cuts:
            next_pieces = []
            for a, b in pieces:
                if cut_end <= a or b <= cut_start:  # no overlap: keep it whole
                    next_pieces.append((a, b))
                    continue
                if a < cut_start:
                    next_pieces.append((a, cut_start))
                if cut_end < b:
                    next_pieces.append((cut_end, b))
            pieces = next_pieces
        result.extend(pieces)
    return result


def intersect(windows, start, end):
    """The parts of windows between start and end."""
    return [(max(a, start), min(b, end)) for a, b in windows if max(a, start) < min(b, end)]


def overlaps(start, end, busy):
    """True if (start, end) touches any busy interval. Touching ends don't count:
    a job ending at 10:00 and one starting at 10:00 can share a bay."""
    return any(start < busy_end and busy_start < end for busy_start, busy_end in busy)


def finish(start, needed, windows):
    """When a job that starts at start and needs `needed` working minutes ends,
    using only the minutes inside windows (sorted, not overlapping).
    None if it can't start there or doesn't fit before the last window ends."""
    if not any(a <= start < b for a, b in windows):
        return None  # never start a job in a break or after hours
    left = needed
    for a, b in windows:
        if b <= start:
            continue
        begin = max(a, start)
        if b - begin >= left:
            return begin + left
        left -= b - begin
    return None


def round_up(value, step=STEP):
    """510 stays 510; 511 becomes 525. The next quarter hour on or after value."""
    return -(-value // step) * step


# ---------- What a booking needs ----------

@dataclass(frozen=True)
class Need:
    work_minutes: int
    buffer_minutes: int
    bay_type_id: int
    bay_type_name: str
    skill_ids: frozenset
    skill_names: tuple
    vehicle_kind: str

    @property
    def total(self):
        """The working minutes the bay and the mechanic are busy: the work, then the clean-up."""
        return self.work_minutes + self.buffer_minutes


def need_for(services, vehicle_kind):
    """What one visit with these services needs. Returns (Need, None) or (None, problem).

    The work adds up; the clean-up happens once, at the end, so the longest
    clean-up counts. All the services must fit the same kind of bay, because a
    visit uses one bay: a car isn't moved from the lift to the alignment bay
    halfway through (book those as two visits)."""
    if not services:
        return None, "Choose at least one service."
    wrong = [s.name for s in services if s.vehicle_kind not in (vehicle_kind, ANY)]
    if wrong:
        return None, f"{wrong[0]} isn't for this vehicle."
    bay_types = {s.bay_type_id for s in services}
    if len(bay_types) > 1:
        names = sorted({s.bay_type.name.lower() for s in services})
        return None, (f"These services need different bays ({' and '.join(names)}). "
                      "Book them as separate visits.")
    skills = {s.skill.id: s.skill.name for s in services if s.skill}
    first = services[0]
    return Need(work_minutes=sum(s.work_minutes for s in services),
                buffer_minutes=max(s.buffer_minutes for s in services),
                bay_type_id=first.bay_type_id, bay_type_name=first.bay_type.name,
                skill_ids=frozenset(skills), skill_names=tuple(sorted(skills.values())),
                vehicle_kind=vehicle_kind), None


# ---------- One day, as the slot finder sees it ----------

@dataclass
class Crew:
    """A mechanic on one day."""
    id: int
    name: str
    skill_ids: frozenset
    windows: list  # when they can work, breaks already cut out
    busy: list = field(default_factory=list)  # jobs already booked (stage 3)
    off: str | None = None  # why they don't work at all today


@dataclass
class Place:
    """A bay on one day."""
    id: int
    name: str
    bay_type_id: int
    busy: list = field(default_factory=list)


@dataclass
class DayPicture:
    day: object
    closed: str | None  # why the whole shop is shut, or None
    opens: int
    closes: int
    crew: list
    bays: list


@dataclass
class Option:
    """One mechanic who can do the job at a start time, and the bays free for it."""
    mechanic: Crew
    bays: list  # every bay of the right kind that is free from the start to `end`
    end: int  # when the work and clean-up are done


@dataclass
class Slot:
    start: int
    options: list

    @property
    def ready(self):
        """The earliest the vehicle can be ready, over every option."""
        return min(option.end for option in self.options)

    @property
    def mechanics(self):
        return [option.mechanic for option in self.options]

    @property
    def bays(self):
        """Every bay that is free for at least one option, in the shop's order."""
        seen = {}
        for option in self.options:
            for bay in option.bays:
                seen.setdefault(bay.id, bay)
        return list(seen.values())


def mechanic_windows(base, own_hours, leave):
    """A mechanic's working windows from the shop's (base) windows, their own
    hours for this weekday (or None) and their leave on this day.
    Returns (windows, why they're off or None)."""
    if own_hours is not None:
        if not own_hours.works:
            return [], "Day off"
        base = intersect(base, minutes(own_hours.starts), minutes(own_hours.ends))
    for closure in leave:
        if closure.from_time is None:
            return [], closure.reason
        base = subtract(base, [(minutes(closure.from_time), minutes(closure.to_time))])
    return base, None


def load_day(s, shop, day, busy=None):
    """Read everything about one day from the database.

    busy: {("bay", id): [(start, end)], ("mechanic", id): [...]} in minutes.
    Stage 2 has no bookings yet; stage 3 fills this from booked_slots."""
    busy = busy or {}
    hours = shops.hours_by_weekday(s, shop)[day.weekday()]
    closures = shops.closures_between(s, shop, day, day)
    shop_wide = [c for c in closures if c.user_id is None]
    closed = None
    if not hours.is_open:
        closed = f"Closed on {day:%A}s"
    elif any(c.from_time is None for c in shop_wide):
        closed = "Closed: " + next(c.reason for c in shop_wide if c.from_time is None)
    opens = minutes(hours.opens) if hours.is_open else 0
    closes = minutes(hours.closes) if hours.is_open else 0
    base = [] if closed else [(opens, closes)]
    cuts = [(minutes(c.from_time), minutes(c.to_time)) for c in shop_wide if c.from_time]
    if hours.is_open and hours.break_starts:
        cuts.append((minutes(hours.break_starts), minutes(hours.break_ends)))
    base = subtract(base, sorted(cuts))

    crew = []
    for mechanic in shops.mechanics(s, shop):
        own = next((h for h in mechanic.hours if h.weekday == day.weekday()), None)
        leave = [c for c in closures if c.user_id == mechanic.id]
        windows, off = mechanic_windows(base, own, leave)
        crew.append(Crew(id=mechanic.id, name=mechanic.name, skill_ids=frozenset(k.id for k in mechanic.skills),
                         windows=sorted(windows), busy=busy.get(("mechanic", mechanic.id), []),
                         off="Shop closed" if closed else off))
    bays = [Place(id=bay.id, name=bay.name, bay_type_id=bay.bay_type_id, busy=busy.get(("bay", bay.id), []))
            for bay in s.scalars(select(Bay).where(Bay.shop_id == shop.id, Bay.is_active.is_(True))
                                 .order_by(Bay.position, Bay.name))]
    return DayPicture(day=day, closed=closed, opens=opens, closes=closes, crew=crew, bays=bays)


def free_slots(picture, need, earliest=0):
    """Every start time that works, from `earliest` (minutes) on.
    Returns (slots, reason): reason says why there are none (or is None)."""
    if picture.closed:
        return [], picture.closed
    bays = [bay for bay in picture.bays if bay.bay_type_id == need.bay_type_id]
    if not bays:
        return [], f"No {need.bay_type_name.lower()} is in use."
    able = [m for m in picture.crew if need.skill_ids <= m.skill_ids]
    if not able:
        return [], f"No mechanic has the {' and '.join(need.skill_names)} skill{'s' * (len(need.skill_names) > 1)}."
    working = [m for m in able if m.windows]
    if not working:
        return [], "No mechanic who does this works on this day."
    slots = []
    for start in range(round_up(picture.opens), picture.closes, STEP):
        if start < earliest:
            continue
        options = []
        for mechanic in working:
            end = finish(start, need.total, mechanic.windows)
            if end is None or overlaps(start, end, mechanic.busy):
                continue
            free = [b for b in bays if not overlaps(start, end, b.busy)]
            if free:
                options.append(Option(mechanic=mechanic, bays=free, end=end))
        if options:
            slots.append(Slot(start=start, options=options))
    if slots:
        return slots, None
    if earliest and free_slots(picture, need)[0]:
        return [], "The times left on this day are too soon to book."
    longest = max(sum(b - a for a, b in m.windows) for m in working)
    if longest < need.total:
        return [], f"A {duration(need.total)} job doesn't fit in this day."
    return [], "Fully booked."


# ---------- The booking window: which days, and from what time ----------

def earliest_minute(shop, day, now, notice=None):
    """The first minute on `day` that respects the minimum notice (the shop's, or
    `notice` minutes: staff booking a walk-in use 0, "from now on").
    0 means the whole day is fine; None means the whole day is too soon."""
    notice = shop.min_notice_minutes if notice is None else notice
    soonest = to_local(now + timedelta(minutes=notice), shop.timezone)
    if soonest.date() < day:
        return 0
    if soonest.date() > day:
        return None
    return round_up(soonest.hour * 60 + soonest.minute + (soonest.second > 0 or soonest.microsecond > 0))


def bookable_days(shop, now):
    """(first day, last day) customers may book, on the shop's calendar."""
    today = local_today(shop.timezone, now)
    return today, today + timedelta(days=shop.max_days_ahead)


@dataclass
class DayChoice:
    day: object
    slots: list
    reason: str | None

    @property
    def count(self):
        return len(self.slots)


def day_choice(s, shop, need, day, now, busy=None, notice=None):
    """The free times on one day, with the booking rules applied."""
    first, last = bookable_days(shop, now)
    if day < first:
        return DayChoice(day, [], "This day has passed.")
    if day > last:
        return DayChoice(day, [], f"Bookings open {shop.max_days_ahead} days ahead.")
    earliest = earliest_minute(shop, day, now, notice)
    if earliest is None:
        return DayChoice(day, [], "It's too soon to book this day.")
    slots, reason = free_slots(load_day(s, shop, day, busy), need, earliest)
    return DayChoice(day, slots, reason)


def week_of_choices(s, shop, need, start_day, now, count=7, busy_for=None, notice=None):
    """`count` days of choices. busy_for(day) gives that day's taken intervals (booking.busy_map)."""
    days = [start_day + timedelta(days=n) for n in range(count)]
    return [day_choice(s, shop, need, day, now, busy_for(day) if busy_for else None, notice) for day in days]


def start_moment(shop, day, start):
    """A slot's start as naive UTC, for the database."""
    return at(day, clock(start), shop.timezone)


