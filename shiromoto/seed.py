"""Starting data: the real shop's first setup, and the demo's sample shop.

starter_shop() runs once, from `flask create-owner`. It gives the real shop
a normal week, the usual bay types, skills and a list of common services
with prices, so the owner edits a list instead of typing one from nothing.

build(demo=True) makes a whole sample shop for one Try Demo visitor, with
staff, bays and closures. Every page the demo can open must show an
example: add sample data here with every new feature (tests/test_demo.py).
"""
import secrets
from datetime import time, timedelta

from sqlalchemy import delete, select
from werkzeug.security import generate_password_hash

from . import shops
from .dates import local_today, to_local
from .models import ADVISOR, CAR, CLOSED, CUSTOMER, LEAVE, MECHANIC, MOTO, OWNER, Bay, BayType, Closure, \
    Service, Shop, Skill, StaffHours, User

BAY_TYPES = [("Car lift", CAR), ("Car bay", CAR), ("Alignment bay", CAR), ("Motorcycle stand", MOTO)]
SKILLS = ["Engine", "Brakes", "Electrical", "Aircon", "Tires", "Motorcycle"]

OTHER_TEXT = "Not on the list? Tell us what you need. We check it, send you the price, and you decide."

# (name, category, vehicle, work minutes, buffer minutes, bay type, skill, price in pesos, description)
SERVICES = [
    ("Car PMS", "Maintenance", CAR, 180, 15, "Car lift", "Engine", 3500,
     "Preventive maintenance: oil and filter, fluids, brakes, belts, lights and a 30-point check."),
    ("Car Oil Change", "Maintenance", CAR, 45, 15, "Car lift", None, 1800,
     "Engine oil and oil filter, with a quick check of fluids and tires."),
    ("Brake Service", "Repair", CAR, 90, 15, "Car lift", "Brakes", 2500,
     "New brake pads, cleaned calipers and a brake fluid check."),
    ("Aircon Cleaning", "Repair", CAR, 120, 15, "Car bay", "Aircon", 3000,
     "Evaporator and blower cleaning, new cabin filter and a cold-air check."),
    ("Engine Diagnostics", "Diagnostics", CAR, 60, 0, "Car bay", "Electrical", 1000,
     "Computer scan for warning lights, with a written list of what we found."),
    ("Battery Check and Replacement", "Repair", CAR, 30, 0, "Car bay", "Electrical", 500,
     "Battery and charging test. A new battery is priced separately."),
    ("Wheel Alignment", "Tires", CAR, 60, 0, "Alignment bay", "Tires", 1200,
     "Computerised four-wheel alignment for straight, even driving."),
    ("Tire Mounting and Balancing", "Tires", CAR, 60, 0, "Car bay", "Tires", 800,
     "Mounting and balancing for four tires. Tires are priced separately."),
    ("Motorcycle PMS", "Maintenance", MOTO, 90, 15, "Motorcycle stand", "Motorcycle", 1200,
     "Oil, spark plug, air filter, chain or belt, brakes and a full safety check."),
    ("Motorcycle Oil Change", "Maintenance", MOTO, 30, 0, "Motorcycle stand", None, 450,
     "Engine oil (and gear oil for scooters) with a quick safety check."),
    ("CVT Cleaning", "Maintenance", MOTO, 60, 15, "Motorcycle stand", "Motorcycle", 650,
     "For scooters: cleaned belt, rollers and clutch for smooth pick-up."),
    ("Motorcycle Brake Pads", "Repair", MOTO, 45, 0, "Motorcycle stand", "Brakes", 500,
     "New front or rear pads and a brake check."),
    ("Motorcycle Tire Change", "Tires", MOTO, 30, 0, "Motorcycle stand", "Tires", 300,
     "One tire mounted and checked. Tires are priced separately."),
    ("Motorcycle Electrical Check", "Diagnostics", MOTO, 60, 0, "Motorcycle stand", "Electrical", 600,
     "Lights, horn, charging and starter checks, with a written list of what we found."),
    # The Other Service (quotes.py): the customer writes what they need; the price comes after the booking.
    ("Other Car Service", "Other", CAR, 60, 0, "Car lift", None, 0, OTHER_TEXT),
    ("Other Motorcycle Service", "Other", MOTO, 60, 0, "Motorcycle stand", None, 0, OTHER_TEXT),
]
OTHER = {"Other Car Service", "Other Motorcycle Service"}


# Do it again after (months, km), whichever comes first: for the "next PMS due" reminder.
AGAIN = {"Car PMS": (6, 5000), "Car Oil Change": (4, 5000), "Motorcycle PMS": (4, 3000),
         "Motorcycle Oil Change": (2, 1500), "CVT Cleaning": (6, 6000), "Aircon Cleaning": (12, 0)}


def add_catalog(s, shop):
    """Bay types, skills and services. Returns ({bay type name: BayType}, {skill name: Skill})."""
    bay_types = {name: BayType(shop_id=shop.id, name=name, vehicle_kind=kind) for name, kind in BAY_TYPES}
    skills = {name: Skill(shop_id=shop.id, name=name) for name in SKILLS}
    s.add_all([*bay_types.values(), *skills.values()])
    s.flush()
    for position, (name, category, kind, work, buffer, bay_type, skill, pesos, text) in enumerate(SERVICES):
        s.add(Service(shop_id=shop.id, name=name, category=category, vehicle_kind=kind, work_minutes=work,
                      buffer_minutes=buffer, bay_type_id=bay_types[bay_type].id,
                      skill_id=skills[skill].id if skill else None, price_from=pesos * 100, description=text,
                      position=position, again_months=AGAIN.get(name, (0, 0))[0], again_km=AGAIN.get(name, (0, 0))[1],
                      is_other=name in OTHER))
    return bay_types, skills


def add_bays(s, shop, bay_types, names):
    """names: [(bay name, bay type name)]."""
    for position, (name, bay_type) in enumerate(names):
        s.add(Bay(shop_id=shop.id, name=name, bay_type_id=bay_types[bay_type].id, position=position))


def new_shop(s, name, moment, demo=False):
    shop = Shop(name=name, tagline="Car and motorcycle care, booked in a minute.", timezone="Asia/Manila",
                phone="0917 555 0142", email="hello@shiromoto.example",
                address="12 Mabini Street, Barangay San Roque, Marikina City", is_demo=demo, created_at=moment)
    s.add(shop)
    s.flush()
    shops.add_starting_hours(s, shop)
    return shop


def starter_shop(s, name, moment):
    """The real shop, the first time the owner is made. The caller commits."""
    shop = new_shop(s, name, moment)
    shop.phone = shop.email = shop.address = ""  # the owner types the real ones in Settings
    bay_types, _ = add_catalog(s, shop)
    add_bays(s, shop, bay_types, [("Lift 1", "Car lift"), ("Moto Stand 1", "Motorcycle stand")])
    year = local_today(shop.timezone, moment).year
    shops.add_ph_holidays(s, shop, year)
    shops.add_ph_holidays(s, shop, year + 1)
    return shop


# ---------- The demo's sample shop ----------

# (short name, full name, role, skills)
PEOPLE = [
    ("hiro", "Hiro Navarro", OWNER, []),
    ("mika", "Mika Reyes", ADVISOR, []),
    ("jun", "Jun dela Cruz", MECHANIC, ["Engine", "Brakes", "Electrical"]),
    ("ana", "Ana Bautista", MECHANIC, ["Aircon", "Electrical", "Engine"]),
    ("paolo", "Paolo Lim", MECHANIC, ["Motorcycle", "Tires", "Brakes"]),
    ("rico", "Rico Santos", MECHANIC, ["Tires", "Motorcycle", "Electrical"]),
    ("carlo", "Carlo Mendoza", CUSTOMER, []),
]


def build(s, moment, demo=True):
    """A whole sample shop. Returns it. The caller commits."""
    shop = new_shop(s, "ShiroMoto Garage", moment, demo=demo)
    bay_types, skills = add_catalog(s, shop)
    add_bays(s, shop, bay_types, [
        ("Lift 1", "Car lift"), ("Lift 2", "Car lift"), ("Car Bay 1", "Car bay"), ("Alignment Bay", "Alignment bay"),
        ("Moto Stand 1", "Motorcycle stand"), ("Moto Stand 2", "Motorcycle stand"),
        ("Moto Stand 3", "Motorcycle stand"),
    ])
    # Nobody knows these passwords: demo people are only opened with Try Demo and View As.
    password = generate_password_hash(secrets.token_urlsafe(24))
    people = {}
    for short, name, role, skill_names in PEOPLE:
        person = User(shop_id=shop.id, email=f"{short}@shiromoto.example", name=name, role=role,
                      password_hash=password, phone=f"0917 555 {1000 + len(people):04d}",
                      created_at=moment - timedelta(days=90))
        person.skills = [skills[n] for n in skill_names]
        s.add(person)
        people[short] = person
    s.flush()

    # Ana is off on Mondays; Rico works 10 to 5 on weekdays only. The rest follow the shop.
    for weekday in range(7):
        s.add(StaffHours(user_id=people["ana"].id, weekday=weekday, works=weekday in (1, 2, 3, 4, 5),
                         starts=time(8), ends=time(15) if weekday == 5 else time(17)))
        s.add(StaffHours(user_id=people["rico"].id, weekday=weekday, works=weekday < 5,
                         starts=time(10), ends=time(17)))

    today = local_today(shop.timezone, moment)
    shops.add_ph_holidays(s, shop, today.year)
    shops.add_ph_holidays(s, shop, today.year + 1)
    s.add(Closure(shop_id=shop.id, user_id=people["jun"].id, kind=LEAVE, reason="Family trip to Baguio",
                  starts_on=today + timedelta(days=3), ends_on=today + timedelta(days=4)))
    s.add(Closure(shop_id=shop.id, kind=CLOSED, reason="Staff training on hybrid cars",
                  starts_on=today + timedelta(days=8), ends_on=today + timedelta(days=8),
                  from_time=time(13), to_time=time(17)))
    # A past leave too, so the list shows both upcoming and past rows.
    s.add(Closure(shop_id=shop.id, user_id=people["paolo"].id, kind=LEAVE, reason="Sick leave",
                  starts_on=today - timedelta(days=6), ends_on=today - timedelta(days=6)))
    s.flush()
    add_bookings(s, shop, people, today, moment)
    return shop


# (name, phone, [(kind, plate, make, model, year)]); Carlo has a login (PEOPLE above).
CUSTOMERS = [
    ("Carlo Mendoza", "0917 555 1006", [(CAR, "NAB 4821", "Toyota", "Vios", 2019),
                                        (MOTO, "123 QWE", "Honda", "Click 125i", 2022)]),
    ("Liza Ramos", "0918 222 3401", [(CAR, "DAF 9022", "Mitsubishi", "Mirage G4", 2020)]),
    ("Ben Tan", "0919 444 7810", [(CAR, "NCE 1188", "Ford", "Ranger", 2018)]),
    ("Joy Villanueva", "0927 333 5120", [(MOTO, "456 RTY", "Yamaha", "NMAX", 2021)]),
    ("Mark Aquino", "0905 111 2290", [(CAR, "ZXA 7745", "Honda", "City", 2016)]),
]

# (customer, plate, services, days from today, start hour:minute, source, note)
BOOKINGS = [
    ("Liza Ramos", "DAF 9022", ["Car PMS"], 0, (8, 0), "online", "Due for the 30,000 km service."),
    ("Joy Villanueva", "456 RTY", ["CVT Cleaning"], 0, (9, 0), "walk_in", ""),
    ("Ben Tan", "NCE 1188", ["Brake Service"], 0, (13, 0), "phone", "Squeaking when braking."),
    ("Carlo Mendoza", "NAB 4821", ["Car Oil Change"], 1, (10, 0), "online", ""),
    ("Mark Aquino", "ZXA 7745", ["Aircon Cleaning"], 2, (8, 30), "phone", "Aircon smells musty."),
    ("Liza Ramos", "DAF 9022", ["Wheel Alignment"], 5, (14, 0), "online", "Pulls to the left."),
    ("Ben Tan", "NCE 1188", ["Engine Diagnostics"], 6, (9, 0), "phone", "Check engine light is on."),
]


# Each sample vehicle's odometer today; a visit N days ago read 45 km less per day.
ODOMETER_TODAY = {"NAB 4821": 41900, "123 QWE": 5400, "DAF 9022": 30200, "NCE 1188": 63400, "456 RTY": 9200,
                  "ZXA 7745": 88400}


def km_on(plate, days_back):
    return ODOMETER_TODAY[plate] - 45 * days_back


def add_bookings(s, shop, people, today, moment):
    """Sample bookings from today on, one past visit and one cancelled one, all placed
    with the real slot finder so they never overlap. A day the shop is closed moves
    the booking to the next open day."""
    from . import booking
    from .models import CANCELLED, CHECKED_IN, NO_SHOW, PRICED, BookedSlot, Customer, Service, Vehicle

    def arrived(appointment, km):
        appointment.status, appointment.mileage_in = CHECKED_IN, km
        appointment.checked_in_at = appointment.starts_at - timedelta(minutes=5)

    services = {sv.name: sv for sv in s.scalars(select(Service).where(Service.shop_id == shop.id))}
    customers, vehicles = {}, {}
    for name, phone, owned in CUSTOMERS:
        user = people["carlo"] if name == "Carlo Mendoza" else None
        customer = Customer(shop_id=shop.id, user_id=user.id if user else None, name=name, phone=phone,
                            phone_key=booking.phone_key(phone), email=user.email if user else "",
                            created_at=moment - timedelta(days=60))
        s.add(customer)
        s.flush()
        customers[name] = customer
        for kind, plate, make, model, year in owned:
            vehicle = Vehicle(shop_id=shop.id, customer_id=customer.id, kind=kind, plate=plate,
                              plate_key=booking.plate_key(plate), make=make, model=model, year=year,
                              created_at=customer.created_at)
            s.add(vehicle)
            vehicles[plate] = vehicle
    s.flush()

    def book(name, plate, names, offset, start, source="online", notes="", status="booked", request_text=""):
        # A closed day moves the booking to the next open day (or, in the past, the one before).
        step = -1 if offset < 0 else 1
        for extra in range(10):
            placed = booking.place(s, shop, [services[n] for n in names], vehicles[plate], customers[name],
                                   today + timedelta(days=offset + step * extra), start[0] * 60 + start[1], moment,
                                   source=source, notes=notes, status=status, request_text=request_text)
            if placed is not None:
                return placed
        return None

    live = {}
    for name, plate, names, offset, start, source, notes in BOOKINGS:
        placed = book(name, plate, names, offset, start, source, notes)
        if placed is not None and placed.starts_at <= moment:  # today's that have started are here already
            arrived(placed, km_on(plate, 0))
            live[plate] = placed
    # The Other Service: Carlo asked for something that isn't on the list, and the shop sent
    # a price (View As Carlo: confirm or decline it). Joy's request still needs a price (Today).
    asked = book("Carlo Mendoza", "123 QWE", ["Motorcycle PMS", "Other Motorcycle Service"], 3, (10, 0),
                 request_text="Rattle from the front fork over bumps.")
    if asked is not None:
        asked.quote_status, asked.quote_price = PRICED, 85000
        asked.quote_note = "Front fork oil and new seals. Your booking time stays the same."
        asked.quoted_by, asked.quoted_at = people["mika"].name, moment - timedelta(hours=2)
    book("Joy Villanueva", "456 RTY", ["Other Motorcycle Service"], 2, (13, 0),
         request_text="The horn stopped working, and the turn signals blink too fast.")
    # Carlo's car came in on the last open day and is still here, waiting for his
    # approval: the customer's View As shows an estimate to approve.
    waiting = book("Carlo Mendoza", "NAB 4821", ["Brake Service"], -1, (13, 0), source="online",
                   notes="Grinding noise from the front.")
    if waiting is not None:
        arrived(waiting, km_on("NAB 4821", (today - to_local(waiting.starts_at, shop.timezone).date()).days))
        live["waiting"] = waiting
    # Earlier visits (the customer pages and the Bookings list show history), one no-show,
    # and one Carlo cancelled.
    for n, (name, plate, names, offset, start) in enumerate((
            ("Carlo Mendoza", "NAB 4821", ["Car PMS"], -40, (8, 0)),
            ("Liza Ramos", "DAF 9022", ["Car Oil Change"], -21, (9, 0)),
            ("Ben Tan", "NCE 1188", ["Tire Mounting and Balancing"], -12, (13, 0)),
            ("Joy Villanueva", "456 RTY", ["Motorcycle Oil Change"], -9, (10, 0)),
            ("Carlo Mendoza", "123 QWE", ["Motorcycle Brake Pads"], -5, (14, 0)))):
        past = book(name, plate, names, offset, start, source="phone" if n % 2 else "online")
        if past is not None:
            past.created_at = past.updated_at = past.starts_at - timedelta(days=3)
            arrived(past, km_on(plate, (today - to_local(past.starts_at, shop.timezone).date()).days))
            live[f"past-{past.id}"] = past
    missed = book("Mark Aquino", "ZXA 7745", ["Engine Diagnostics"], -7, (15, 0), source="phone")
    if missed is not None:
        s.execute(delete(BookedSlot).where(BookedSlot.appointment_id == missed.id))
        missed.status, missed.no_show_at = NO_SHOW, missed.starts_at + timedelta(minutes=20)
    cancelled = book("Carlo Mendoza", "NAB 4821", ["Battery Check and Replacement"], 4, (15, 0), status=CANCELLED)
    if cancelled is not None:
        cancelled.cancel_reason, cancelled.cancelled_at = "Fixed it myself", moment - timedelta(days=1)
    s.flush()
    def book_on(name, plate, names, day, start, source, status):
        return booking.place(s, shop, [services[n] for n in names], vehicles[plate], customers[name], day,
                             start[0] * 60 + start[1], moment, source=source, status=status)

    add_history(s, shop, today, book_on, arrived, live)
    add_jobs(s, shop, people, live, moment)
    # Two people on the waitlist: the timer offers them the next free time.
    from .models import WaitlistEntry
    for name, phone, plate_kind, wanted, days in (("Rosa Garcia", "0917 888 2301", CAR, "Aircon Cleaning", (1, 5)),
                                                   ("Dan Cruz", "0920 777 4512", MOTO, "CVT Cleaning", (2, 4))):
        s.add(WaitlistEntry(shop_id=shop.id, token=secrets.token_urlsafe(18), name=name, phone=phone,
                            vehicle_kind=plate_kind, service_ids=str(services[wanted].id),
                            first_day=today + timedelta(days=days[0]), last_day=today + timedelta(days=days[1]),
                            created_at=moment - timedelta(hours=days[0] * 5)))
    s.flush()


# (name, code, stock, low at, price, cost) in pesos
PARTS = [
    ("Engine oil 10W-40, 1 L", "OIL-1040", 24, 8, 420, 290),
    ("Oil filter (Toyota Vios)", "OF-VIOS", 6, 2, 380, 240),
    ("Front brake pads (Toyota Vios)", "BP-VIOS-F", 4, 2, 2200, 1450),
    ("Brake fluid DOT 4, 500 ml", "BF-DOT4", 1, 3, 450, 260),
    ("Air filter (Mitsubishi Mirage)", "AF-MIRAGE", 2, 1, 650, 400),
    ("Wiper blade 22 in", "WB-22", 3, 2, 550, 310),
    ("Spark plug (Honda Click 125i)", "SP-CLICK", 0, 2, 320, 180),
    ("CVT belt (Yamaha NMAX)", "CVT-NMAX", 1, 1, 1650, 1050),
]


# Four weeks of everyday visits, so Insights has something to show: (customer, plate, service).
EVERYDAY = [
    ("Liza Ramos", "DAF 9022", "Car Oil Change"), ("Ben Tan", "NCE 1188", "Brake Service"),
    ("Mark Aquino", "ZXA 7745", "Car PMS"), ("Joy Villanueva", "456 RTY", "Motorcycle PMS"),
    ("Carlo Mendoza", "123 QWE", "CVT Cleaning"), ("Liza Ramos", "DAF 9022", "Wheel Alignment"),
    ("Ben Tan", "NCE 1188", "Engine Diagnostics"), ("Joy Villanueva", "456 RTY", "Motorcycle Tire Change"),
    ("Mark Aquino", "ZXA 7745", "Battery Check and Replacement"), ("Carlo Mendoza", "NAB 4821", "Aircon Cleaning"),
]
HISTORY_DAYS = 28


def add_history(s, shop, today, book_on, arrived, live):
    """Two to four visits on each open day of the last four weeks, at busy morning and
    after-lunch hours, placed by the real slot finder. A fixed random seed makes every
    demo the same. About 1 in 12 is a no-show and 1 in 10 was cancelled."""
    import random

    from .models import CANCELLED, NO_SHOW, BookedSlot
    pick = random.Random(20261105)
    reasons = ["Car sold", "Changed my plans", "Fixed it at home", "Found an earlier time"]
    for back in range(2, HISTORY_DAYS + 1):
        for _ in range(pick.choice((2, 3, 3, 4))):
            name, plate, service = pick.choice(EVERYDAY)
            start = pick.choice(((8, 0), (8, 30), (9, 0), (10, 0), (13, 0), (13, 30), (14, 0), (15, 0)))
            roll = pick.random()
            status = CANCELLED if roll < .1 else "booked"
            visit = book_on(name, plate, [service], today - timedelta(days=back), start,
                            pick.choice(("online", "online", "phone", "walk_in")), status)
            if visit is None:
                continue  # closed, or that time wasn't free: the day is busy enough
            visit.created_at = visit.updated_at = visit.starts_at - timedelta(days=pick.randint(1, 6))
            if status == CANCELLED:
                visit.cancel_reason, visit.cancelled_at = pick.choice(reasons), visit.created_at + timedelta(hours=5)
            elif roll < .18:
                s.execute(delete(BookedSlot).where(BookedSlot.appointment_id == visit.id,
                                                   BookedSlot.slot_start > visit.starts_at))
                visit.status, visit.no_show_at = NO_SHOW, visit.starts_at + timedelta(minutes=20)
            else:
                arrived(visit, km_on(plate, back))
                live[f"past-{visit.id}"] = visit
    s.flush()


def add_jobs(s, shop, people, live, moment):
    """Parts in stock, and a job for every checked-in sample visit, at different steps."""
    from . import jobs
    from .models import (APPROVED, AWAITING, CHECK_ATTENTION, CHECK_OK, DECLINED, INSPECTING, LABOR, PART, READY,
                         RELEASED, WORKING, EstimateLine, JobEvent, Part, StockMove)

    parts = {}
    for name, code, stock, low, price, cost in PARTS:
        part = Part(shop_id=shop.id, name=name, sku=code, stock=stock, low_level=low, price=price * 100,
                    cost=cost * 100)
        s.add(part)
        parts[code] = part
    s.flush()
    used = {}  # part id -> how many the sample jobs took, so the delivery adds up to today's stock

    def line(job, kind, text, qty, price, status, part=None, reason="", who="the customer"):
        item = EstimateLine(job_id=job.id, kind=kind, description=text, quantity=qty, unit_price=price * 100,
                            part_id=part.id if part else None, status=status, reason=reason,
                            decided_at=job.created_at + timedelta(minutes=50) if status != "proposed" else None,
                            decided_by=who if status != "proposed" else "", stock_taken=bool(part)
                            and status == APPROVED, created_at=job.created_at + timedelta(minutes=30))
        s.add(item)
        if part and status == APPROVED:
            used[part.id] = used.get(part.id, 0) + qty
            s.add(StockMove(part_id=part.id, job_id=job.id, change=-qty, reason=f"Used on job #{job.id}",
                            at=item.created_at + timedelta(minutes=25)))
        return item

    def step(job, status, minutes_after, text=None):
        job.status = status
        job.events.append(JobEvent(at=job.created_at + timedelta(minutes=minutes_after), status=status,
                                   text=text or jobs.PUBLIC_TEXT[status], by_id=job.appointment.mechanic_id))

    for key, visit in live.items():
        job = jobs.open_job(s, visit, visit.checked_in_at, people["mika"], fuel="1/2")
        for item in job.checklist:
            item.state = CHECK_OK
        if key == "waiting":
            job.checklist[0].state, job.checklist[0].note = CHECK_ATTENTION, "Small scratch on the rear bumper"
            step(job, INSPECTING, 20)
            step(job, AWAITING, 45, "Front rotors are worn and the brake fluid is dark. " + jobs.PUBLIC_TEXT[AWAITING])
            line(job, PART, "Front brake pads (Toyota Vios)", 1, 2200, APPROVED, parts["BP-VIOS-F"],
                 "The pads are down to 2 mm.")
            line(job, LABOR, "Resurface the front rotors", 1, 1200, "proposed",
                 reason="They have grooves, so new pads would wear fast and squeak.")
            line(job, PART, "Brake fluid DOT 4, 500 ml", 1, 450, "proposed", parts["BF-DOT4"],
                 reason="The fluid is dark and should be changed every 2 years.")
            job.events.append(JobEvent(at=job.created_at + timedelta(minutes=55),
                                       text="Customer asked us to call before 5 PM.", public=False,
                                       by_id=people["mika"].id))
        elif key == "DAF 9022":
            step(job, WORKING, 25)
            line(job, PART, "Air filter (Mitsubishi Mirage)", 1, 650, APPROVED, parts["AF-MIRAGE"],
                 "The filter is clogged with dust.")
            line(job, PART, "Wiper blade 22 in", 2, 550, DECLINED, parts["WB-22"], "The rubber is cracked.")
        elif key == "456 RTY":
            step(job, WORKING, 10)
            step(job, READY, 55)
        elif key.startswith("past-"):
            step(job, WORKING, 15)
            if visit.vehicle.kind == "car" and "PMS" in visit.lines[0].name:
                line(job, PART, "Engine oil 10W-40, 1 L", 4, 420, APPROVED, parts["OIL-1040"], "For the oil change.")
            step(job, READY, 150)
            step(job, RELEASED, 190)
            job.released_at = job.created_at + timedelta(minutes=190)
        else:
            step(job, INSPECTING, 15)
    for part in parts.values():
        s.add(StockMove(part_id=part.id, change=part.stock + used.get(part.id, 0), reason="Delivery from the supplier",
                        at=moment - timedelta(days=45), by_id=people["mika"].id))
    s.flush()
