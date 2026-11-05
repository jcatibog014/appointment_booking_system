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
    return shop
