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
from .dates import local_today
from .models import ADVISOR, CAR, CLOSED, CUSTOMER, LEAVE, MECHANIC, MOTO, OWNER, Bay, BayType, Closure, \
    Service, Shop, Skill, StaffHours, User

BAY_TYPES = [("Car lift", CAR), ("Car bay", CAR), ("Alignment bay", CAR), ("Motorcycle stand", MOTO)]
SKILLS = ["Engine", "Brakes", "Electrical", "Aircon", "Tires", "Motorcycle"]

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
]


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
                      position=position))
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
    ("Carlo Mendoza", "123 QWE", ["Motorcycle PMS"], 3, (10, 0), "online", ""),
    ("Liza Ramos", "DAF 9022", ["Wheel Alignment"], 5, (14, 0), "online", "Pulls to the left."),
    ("Ben Tan", "NCE 1188", ["Engine Diagnostics"], 6, (9, 0), "phone", "Check engine light is on."),
]


def add_bookings(s, shop, people, today, moment):
    """Sample bookings from today on, one past visit and one cancelled one, all placed
    with the real slot finder so they never overlap. A day the shop is closed moves
    the booking to the next open day."""
    from . import booking
    from .models import CANCELLED, CHECKED_IN, NO_SHOW, BookedSlot, Customer, Service, Vehicle

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

    def book(name, plate, names, offset, start, source="online", notes="", status="booked"):
        for extra in range(10):  # the next day the shop is open, if that day is closed
            placed = booking.place(s, shop, [services[n] for n in names], vehicles[plate], customers[name],
                                   today + timedelta(days=offset + extra), start[0] * 60 + start[1], moment,
                                   source=source, notes=notes, status=status)
            if placed is not None:
                return placed
        return None

    for name, plate, names, offset, start, source, notes in BOOKINGS:
        placed = book(name, plate, names, offset, start, source, notes)
        if placed is not None and placed.starts_at <= moment:  # today's that have started are here already
            arrived(placed, 21300 + placed.id * 137)
    # Earlier visits (the customer pages and the Bookings list show history), one no-show,
    # and one Carlo cancelled.
    for name, plate, names, offset, start, km in (
            ("Carlo Mendoza", "NAB 4821", ["Car PMS"], -40, (8, 0), 38250),
            ("Liza Ramos", "DAF 9022", ["Car Oil Change"], -21, (9, 0), 27910),
            ("Ben Tan", "NCE 1188", ["Tire Mounting and Balancing"], -12, (13, 0), 61200),
            ("Joy Villanueva", "456 RTY", ["Motorcycle Oil Change"], -9, (10, 0), 8400),
            ("Carlo Mendoza", "123 QWE", ["Motorcycle Brake Pads"], -5, (14, 0), 5120)):
        past = book(name, plate, names, offset, start, source="phone" if km % 2 else "online")
        if past is not None:
            past.created_at = past.updated_at = past.starts_at - timedelta(days=3)
            arrived(past, km)
    missed = book("Mark Aquino", "ZXA 7745", ["Engine Diagnostics"], -7, (15, 0), source="phone")
    if missed is not None:
        s.execute(delete(BookedSlot).where(BookedSlot.appointment_id == missed.id))
        missed.status, missed.no_show_at = NO_SHOW, missed.starts_at + timedelta(minutes=20)
    cancelled = book("Carlo Mendoza", "NAB 4821", ["Battery Check and Replacement"], 4, (15, 0), status=CANCELLED)
    if cancelled is not None:
        cancelled.cancel_reason, cancelled.cancelled_at = "Fixed it myself", moment - timedelta(days=1)
    s.flush()
