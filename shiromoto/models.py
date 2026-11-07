"""The data model: every table is a class here.

One business, many sandboxes. The real shop is ShiroMoto Garage. Every
Try Demo visitor gets a private copy of a sample shop (shops.is_demo) that
is deleted after 2 hours. So every row belongs to a shop (shop_id), and
every query starts from the current shop (shops.current()).

Times:
- moments (a booking's start) are naive UTC datetimes;
- opening hours are plain times on the shop's own clock (Asia/Manila);
- closures are plain dates on the shop's own calendar.
Money is whole centavos (int), so 1,250.50 pesos is 125050. Never floats.
"""
from datetime import date, datetime, time

from sqlalchemy import DDL, Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Integer, LargeBinary, \
    String, Table, Text, Time, UniqueConstraint, event
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from .db import Base

# ---------- Roles ----------

OWNER, ADVISOR, MECHANIC, CUSTOMER = "owner", "advisor", "mechanic", "customer"
ROLES = (OWNER, ADVISOR, MECHANIC, CUSTOMER)
STAFF_ROLES = (OWNER, ADVISOR, MECHANIC)
ROLE_LABELS = {OWNER: "Owner", ADVISOR: "Service advisor", MECHANIC: "Mechanic", CUSTOMER: "Customer"}

# ---------- Vehicles ----------

CAR, MOTO, ANY = "car", "moto", "any"
VEHICLE_KINDS = (CAR, MOTO)
VEHICLE_LABELS = {CAR: "Car", MOTO: "Motorcycle", ANY: "Car or motorcycle"}

# ---------- Closures ----------

HOLIDAY, CLOSED, LEAVE = "holiday", "closed", "leave"
CLOSURE_LABELS = {HOLIDAY: "Holiday", CLOSED: "Shop closed", LEAVE: "Leave"}

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def now_column():
    return mapped_column(DateTime, nullable=False)


# SQLite reuses the id of a deleted row unless told not to (AUTOINCREMENT). Demo shops
# and their people are deleted all the time, and a login cookie holds a user id, so an
# old demo cookie could then match a NEW visitor's user. PostgreSQL never reuses ids.
NEVER_REUSE_IDS = {"sqlite_autoincrement": True}


class Shop(Base):
    __tablename__ = "shops"
    __table_args__ = (NEVER_REUSE_IDS,)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    tagline: Mapped[str] = mapped_column(String(160), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    email: Mapped[str] = mapped_column(String(254), default="")
    address: Mapped[str] = mapped_column(String(240), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Manila")
    # Booking rules (minutes and days, so the arithmetic never needs floats).
    min_notice_minutes: Mapped[int] = mapped_column(Integer, default=120)  # no booking sooner than this
    max_days_ahead: Mapped[int] = mapped_column(Integer, default=30)  # no booking further away than this
    cancel_cutoff_hours: Mapped[int] = mapped_column(Integer, default=12)  # customers can't cancel later than this
    hold_minutes: Mapped[int] = mapped_column(Integer, default=5)  # a chosen time is kept this long
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    demo_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = now_column()

    users: Mapped[list["User"]] = relationship(back_populates="shop", cascade="all, delete-orphan",
                                               passive_deletes=True)


mechanic_skills = Table(
    "mechanic_skills", Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("skill_id", ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    """Everyone who logs in: the owner, staff, and (from stage 3) customers."""
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("shop_id", "email"),
        CheckConstraint("role IN ('owner', 'advisor', 'mechanic', 'customer')", name="role"),
        NEVER_REUSE_IDS,
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(254))
    name: Mapped[str] = mapped_column(String(80))
    phone: Mapped[str] = mapped_column(String(40), default="")
    role: Mapped[str] = mapped_column(String(16))
    password_hash: Mapped[str] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # a staff member who left can't log in
    # Logging in changes nothing here; changing the password adds 1, which logs out old cookies.
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = now_column()

    shop: Mapped[Shop] = relationship(back_populates="users")
    skills: Mapped[list["Skill"]] = relationship(secondary=mechanic_skills, order_by="Skill.name")
    hours: Mapped[list["StaffHours"]] = relationship(cascade="all, delete-orphan", passive_deletes=True,
                                                     order_by="StaffHours.weekday")

    @property
    def first_name(self):
        return self.name.split()[0] if self.name else ""

    @property
    def is_staff(self):
        return self.role in STAFF_ROLES


class Invite(Base):
    """A one-time link that lets a new staff member make their login."""
    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(80))
    # Only a hash of the link's secret is kept, like a password.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = now_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)


class ShopHours(Base):
    """When the shop is open on one day of the week, with one optional break (lunch)."""
    __tablename__ = "shop_hours"
    __table_args__ = (UniqueConstraint("shop_id", "weekday"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)  # 0 = Monday, like date.weekday()
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    opens: Mapped[time | None] = mapped_column(Time)
    closes: Mapped[time | None] = mapped_column(Time)
    break_starts: Mapped[time | None] = mapped_column(Time)
    break_ends: Mapped[time | None] = mapped_column(Time)


class StaffHours(Base):
    """A mechanic's own week. With no rows, a mechanic works whenever the shop is open."""
    __tablename__ = "staff_hours"
    __table_args__ = (UniqueConstraint("user_id", "weekday"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    works: Mapped[bool] = mapped_column(Boolean, default=True)
    starts: Mapped[time | None] = mapped_column(Time)
    ends: Mapped[time | None] = mapped_column(Time)


class Closure(Base):
    """Days (or part of each day) when the shop, or one mechanic, isn't working.

    starts_on..ends_on are whole days on the shop's calendar, both included.
    With from_time and to_time, only that part of each day is closed
    (say 13:00-17:00 for a staff meeting).
    """
    __tablename__ = "closures"

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)  # leave
    kind: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(120))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    from_time: Mapped[time | None] = mapped_column(Time)
    to_time: Mapped[time | None] = mapped_column(Time)

    user: Mapped[User | None] = relationship()


class BayType(Base):
    """A kind of work place: car lift, motorcycle stand, alignment bay."""
    __tablename__ = "bay_types"
    __table_args__ = (UniqueConstraint("shop_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    vehicle_kind: Mapped[str] = mapped_column(String(8), default=ANY)

    bays: Mapped[list["Bay"]] = relationship(back_populates="bay_type", order_by="Bay.position")


class Bay(Base):
    """One place a vehicle is worked on. A bay holds one vehicle at a time."""
    __tablename__ = "bays"
    __table_args__ = (UniqueConstraint("shop_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    bay_type_id: Mapped[int] = mapped_column(ForeignKey("bay_types.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    bay_type: Mapped[BayType] = relationship(back_populates="bays")


class Skill(Base):
    """What a mechanic can do (Engine, Electrical, Aircon...). A service may need one."""
    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("shop_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))


class Service(Base):
    """Something a customer can book: PMS, oil change, tire mounting..."""
    __tablename__ = "services"
    __table_args__ = (
        UniqueConstraint("shop_id", "name"),
        CheckConstraint("work_minutes > 0 AND work_minutes % 15 = 0", name="work_minutes"),
        CheckConstraint("buffer_minutes >= 0 AND buffer_minutes % 15 = 0", name="buffer_minutes"),
        CheckConstraint("price_from >= 0", name="price_from"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(40), default="Maintenance")
    description: Mapped[str] = mapped_column(Text, default="")
    vehicle_kind: Mapped[str] = mapped_column(String(8), default=ANY)
    # Working minutes: the time a mechanic spends on it. Breaks don't count,
    # so a 3-hour job started at 10:00 with a 12-1 lunch ends at 14:00.
    work_minutes: Mapped[int] = mapped_column(Integer)
    # Clean-up after the job, when the bay and mechanic are still busy.
    buffer_minutes: Mapped[int] = mapped_column(Integer, default=0)
    bay_type_id: Mapped[int] = mapped_column(ForeignKey("bay_types.id", ondelete="RESTRICT"), index=True)
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id", ondelete="SET NULL"), index=True)
    price_from: Mapped[int] = mapped_column(Integer, default=0)  # centavos
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # "Do it again after this many months or km, whichever comes first" (PMS, oil). 0 = no reminder.
    again_months: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    again_km: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # The "Other Service" choice: the customer writes what they need, books a time to
    # have it checked (work_minutes), and the shop sends the price after (quotes.py).
    is_other: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    bay_type: Mapped[BayType] = relationship()
    skill: Mapped[Skill | None] = relationship()


# ---------- Customers and vehicles (stage 3) ----------

class Customer(Base):
    """Someone who brings a vehicle in. Guests are matched by their mobile number;
    a customer with a login (users.role = customer) has user_id set."""
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("shop_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(80))
    phone: Mapped[str] = mapped_column(String(40))
    phone_key: Mapped[str] = mapped_column(String(20), index=True)  # digits only: 09175550142
    email: Mapped[str] = mapped_column(String(254), default="")
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")  # staff only
    created_at: Mapped[datetime] = now_column()

    vehicles: Mapped[list["Vehicle"]] = relationship(back_populates="customer", order_by="Vehicle.id")


class Vehicle(Base):
    """A car or motorcycle, known by its plate. One plate is one vehicle in the shop."""
    __tablename__ = "vehicles"
    __table_args__ = (UniqueConstraint("shop_id", "plate_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(8))
    plate: Mapped[str] = mapped_column(String(16))  # as typed: "ABC 1234"
    plate_key: Mapped[str] = mapped_column(String(16))  # for matching: "ABC1234"
    make: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(40), default="")
    year: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = now_column()

    customer: Mapped[Customer] = relationship(back_populates="vehicles")

    @property
    def label(self):
        """Like "Toyota Vios · ABC 1234"."""
        name = " ".join(part for part in (self.make, self.model) if part)
        return f"{name} · {self.plate}" if name else self.plate


# ---------- Bookings (stage 3) ----------

BOOKED, CHECKED_IN, NO_SHOW, CANCELLED = "booked", "checked_in", "no_show", "cancelled"
STATUS_LABELS = {BOOKED: "Booked", CHECKED_IN: "Checked in", NO_SHOW: "No-show", CANCELLED: "Cancelled"}
# A booking in one of these still holds its bay and mechanic. (A no-show gives back
# the rest of its time; a cancelled booking gives back all of it.)
LIVE_STATUSES = (BOOKED, CHECKED_IN)
ONLINE, PHONE, WALK_IN = "online", "phone", "walk_in"

# An Other Service's price (quotes.py): booked -> the shop sends a price -> the customer answers.
TO_PRICE, PRICED, ACCEPTED, DECLINED = "to_price", "priced", "accepted", "declined"
QUOTE_LABELS = {TO_PRICE: "Price to send", PRICED: "Waiting for the customer", ACCEPTED: "Price accepted",
                DECLINED: "Price declined"}
QUOTE_OPEN = (TO_PRICE, PRICED)  # the price isn't agreed yet

# A customer's request for a new time (moves.py): staff approve or decline it.
MOVE_ASKED, MOVE_APPROVED, MOVE_DECLINED, MOVE_WITHDRAWN, MOVE_EXPIRED = \
    "asked", "approved", "declined", "withdrawn", "expired"
MOVE_LABELS = {MOVE_ASKED: "Move requested", MOVE_APPROVED: "Move approved", MOVE_DECLINED: "Move declined",
               MOVE_WITHDRAWN: "Move withdrawn", MOVE_EXPIRED: "Move request ran out"}
SOURCE_LABELS = {ONLINE: "Online", PHONE: "Phone", WALK_IN: "Walk-in"}


class Appointment(Base):
    """One visit: a vehicle, its services, one bay and one mechanic, from starts_at to ends_at (UTC).

    The times include the lunch pause: the bay is taken the whole time.
    `version` goes up on every change. Two people changing the same booking
    can't overwrite each other: the second save fails (StaleDataError)."""
    __tablename__ = "appointments"
    __table_args__ = (CheckConstraint("ends_at > starts_at", name="times"), NEVER_REUSE_IDS)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    ref: Mapped[str] = mapped_column(String(12), unique=True)  # SM-7K3QD, easy to read out on the phone
    token_hash: Mapped[str] = mapped_column(String(64))  # the manage link's secret, hashed
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"), index=True)
    bay_id: Mapped[int] = mapped_column(ForeignKey("bays.id", ondelete="RESTRICT"), index=True)
    mechanic_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    work_minutes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=BOOKED)
    source: Mapped[str] = mapped_column(String(16), default=ONLINE)
    notes: Mapped[str] = mapped_column(Text, default="")
    cancel_reason: Mapped[str] = mapped_column(String(200), default="")
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime)
    mileage_in: Mapped[int | None] = mapped_column(Integer)  # km on the odometer at check-in
    no_show_at: Mapped[datetime | None] = mapped_column(DateTime)
    # The Other Service (quotes.py): what the customer asked for, and the price the shop sent.
    request_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    quote_status: Mapped[str] = mapped_column(String(12), default="", server_default="", index=True)
    quote_price: Mapped[int | None] = mapped_column(Integer)  # centavos
    quote_note: Mapped[str] = mapped_column(String(300), default="", server_default="")
    quoted_at: Mapped[datetime | None] = mapped_column(DateTime)
    quoted_by: Mapped[str] = mapped_column(String(80), default="", server_default="")  # who sent the price
    quote_decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    # A move request (moves.py): the customer asks for a new time, and the shop decides.
    # The new time is held (holds.token = move_hold_token) until then.
    move_status: Mapped[str] = mapped_column(String(12), default="", server_default="", index=True)
    # Who asked for the new time: "customer" (the shop answers) or "shop" (the customer answers).
    move_by: Mapped[str] = mapped_column(String(12), default="customer", server_default="customer")
    move_hold_token: Mapped[str] = mapped_column(String(48), default="", server_default="")
    move_from_at: Mapped[datetime | None] = mapped_column(DateTime)  # the time it had when the customer asked
    move_starts_at: Mapped[datetime | None] = mapped_column(DateTime)  # the time they asked for
    move_ends_at: Mapped[datetime | None] = mapped_column(DateTime)
    move_expires_at: Mapped[datetime | None] = mapped_column(DateTime)  # the held time is given back then
    move_asked_at: Mapped[datetime | None] = mapped_column(DateTime)
    move_note: Mapped[str] = mapped_column(String(300), default="", server_default="")  # the customer's reason
    move_reply: Mapped[str] = mapped_column(String(300), default="", server_default="")  # the shop's answer
    move_decided_by: Mapped[str] = mapped_column(String(80), default="", server_default="")
    move_decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    # The booking form's one-time key: pressing "Book" twice makes one booking.
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = now_column()
    updated_at: Mapped[datetime] = now_column()
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": version}

    customer: Mapped[Customer] = relationship()
    vehicle: Mapped[Vehicle] = relationship()
    bay: Mapped[Bay] = relationship()
    mechanic: Mapped[User] = relationship(foreign_keys=[mechanic_id])
    lines: Mapped[list["AppointmentService"]] = relationship(cascade="all, delete-orphan", passive_deletes=True,
                                                             order_by="AppointmentService.id")

    @property
    def price_from(self):
        return sum(line.price_from for line in self.lines)

    def move_waiting(self, now):
        """True while a move request waits for the shop (and its held time hasn't run out)."""
        return self.move_status == MOVE_ASKED and self.move_expires_at is not None and self.move_expires_at > now

    @property
    def price_open(self):
        """True while an Other Service's price isn't agreed: the total is "from" plus that price."""
        return self.quote_status in QUOTE_OPEN


class AppointmentService(Base):
    """A service on a booking, copied as it was then: a later price change doesn't rewrite old bookings."""
    __tablename__ = "appointment_services"

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id", ondelete="CASCADE"), index=True)
    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(80))
    price_from: Mapped[int] = mapped_column(Integer)
    work_minutes: Mapped[int] = mapped_column(Integer)
    # The Other Service: its price is 0 until the customer accepts the shop's price.
    is_other: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class Hold(Base):
    """A time kept for a few minutes while someone fills in their details.
    Its cells in booked_slots stop anyone else taking the same minutes."""
    __tablename__ = "holds"
    __table_args__ = (NEVER_REUSE_IDS,)  # holds are deleted all the time

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(48), unique=True)
    vehicle_kind: Mapped[str] = mapped_column(String(8))
    service_ids: Mapped[str] = mapped_column(String(200))  # "3,5"
    request_text: Mapped[str] = mapped_column(Text, default="", server_default="")  # for the Other Service
    bay_id: Mapped[int] = mapped_column(ForeignKey("bays.id", ondelete="CASCADE"))
    mechanic_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    starts_at: Mapped[datetime] = mapped_column(DateTime)
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    work_minutes: Mapped[int] = mapped_column(Integer)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = now_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    bay: Mapped[Bay] = relationship()
    mechanic: Mapped[User] = relationship(foreign_keys=[mechanic_id])


class BookedSlot(Base):
    """THE double-booking guard: one row per 15 minutes per bay and per mechanic.

    UNIQUE(resource_kind, resource_id, slot_start) means the database itself
    refuses a second booking of the same bay (or mechanic) for the same
    quarter hour, even when two requests arrive at the same instant. The
    losing request gets an IntegrityError and is rolled back whole."""
    __tablename__ = "booked_slots"
    __table_args__ = (
        UniqueConstraint("resource_kind", "resource_id", "slot_start"),
        CheckConstraint("resource_kind IN ('bay', 'mechanic')", name="resource_kind"),
        CheckConstraint("(appointment_id IS NULL) <> (hold_id IS NULL)", name="one_owner"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    resource_kind: Mapped[str] = mapped_column(String(8))
    resource_id: Mapped[int] = mapped_column(Integer)
    slot_start: Mapped[datetime] = mapped_column(DateTime)  # UTC, on a quarter hour
    appointment_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id", ondelete="CASCADE"),
                                                       index=True)
    hold_id: Mapped[int | None] = mapped_column(ForeignKey("holds.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)  # a hold's cells stop counting after this


# ---------- PostgreSQL's second safety net ----------
# On PostgreSQL an EXCLUSION constraint also refuses two live bookings whose
# time ranges overlap on the same bay or the same mechanic. tsrange is [start, end),
# so a booking ending at 10:00 and one starting at 10:00 may share a bay.
# SQLite has no such constraint; there the booked_slots cells do the job alone.
NO_OVERLAP_SQL = [
    "CREATE EXTENSION IF NOT EXISTS btree_gist",
    "ALTER TABLE appointments ADD CONSTRAINT appointments_bay_no_overlap EXCLUDE USING gist "
    "(bay_id WITH =, tsrange(starts_at, ends_at) WITH &&) WHERE (status IN ('booked', 'checked_in'))",
    "ALTER TABLE appointments ADD CONSTRAINT appointments_mechanic_no_overlap EXCLUDE USING gist "
    "(mechanic_id WITH =, tsrange(starts_at, ends_at) WITH &&) WHERE (status IN ('booked', 'checked_in'))",
]
for _statement in NO_OVERLAP_SQL:
    event.listen(Appointment.__table__, "after_create", DDL(_statement).execute_if(dialect="postgresql"))


# ---------- Jobs: what happens after check-in (stage 5) ----------

CHECKED, INSPECTING, AWAITING, WORKING, WAITING_PARTS, READY, RELEASED = (
    "checked_in", "inspecting", "awaiting_approval", "in_progress", "waiting_parts", "ready", "released")
JOB_LABELS = {CHECKED: "Checked in", INSPECTING: "Inspecting", AWAITING: "Waiting for approval",
              WORKING: "In progress", WAITING_PARTS: "Waiting for parts", READY: "Ready for pickup",
              RELEASED: "Released"}
JOB_ORDER = (CHECKED, INSPECTING, AWAITING, WORKING, WAITING_PARTS, READY, RELEASED)

FUEL_LEVELS = ("Empty", "1/4", "1/2", "3/4", "Full")
CHECK_OK, CHECK_ATTENTION, CHECK_NOT = "ok", "attention", "not_checked"
CHECK_LABELS = {CHECK_OK: "OK", CHECK_ATTENTION: "Needs attention", CHECK_NOT: "Not checked"}
# Looked at and written down when a vehicle comes in, with the customer there.
CHECK_IN_ITEMS = ("Body: scratches and dents", "Windows and mirrors", "Lights", "Tires and wheels",
                  "Warning lights on the dashboard", "Items left in the vehicle")

LABOR, PART = "labor", "part"
PROPOSED, APPROVED, DECLINED = "proposed", "approved", "declined"
LINE_LABELS = {PROPOSED: "Waiting for approval", APPROVED: "Approved", DECLINED: "Declined"}


class Job(Base):
    """The work on one checked-in visit, from check-in to release. `version` stops two
    people moving the same job at once (the second save fails)."""
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("appointment_id"), NEVER_REUSE_IDS)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(24), default=CHECKED)
    fuel_level: Mapped[str] = mapped_column(String(8), default="")
    promised_at: Mapped[datetime | None] = mapped_column(DateTime)  # when we told the customer it'd be ready
    released_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = now_column()
    updated_at: Mapped[datetime] = now_column()
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": version}

    appointment: Mapped["Appointment"] = relationship(backref=backref("job", uselist=False))
    events: Mapped[list["JobEvent"]] = relationship(cascade="all, delete-orphan", passive_deletes=True,
                                                    order_by="JobEvent.at")
    checklist: Mapped[list["ChecklistItem"]] = relationship(cascade="all, delete-orphan", passive_deletes=True,
                                                            order_by="ChecklistItem.position")
    photos: Mapped[list["Photo"]] = relationship(cascade="all, delete-orphan", passive_deletes=True,
                                                 order_by="Photo.id")
    lines: Mapped[list["EstimateLine"]] = relationship(cascade="all, delete-orphan", passive_deletes=True,
                                                       order_by="EstimateLine.id")

    def total(self, status=APPROVED):
        return sum(line.amount for line in self.lines if line.status == status)


class JobEvent(Base):
    """One line on a job's timeline: a status change, a note, a decision. `public` ones
    are shown to the customer on their booking page."""
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str | None] = mapped_column(String(24))
    text: Mapped[str] = mapped_column(String(400), default="")
    public: Mapped[bool] = mapped_column(Boolean, default=True)
    by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    by: Mapped[User | None] = relationship()


class ChecklistItem(Base):
    __tablename__ = "checklist_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(16), default=CHECK_NOT)
    note: Mapped[str] = mapped_column(String(200), default="")


class Photo(Base):
    """A check-in photo, kept in the database (Render's disk forgets files). Always WebP,
    at most 1600 px, with the camera's location and other details removed."""
    __tablename__ = "photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    caption: Mapped[str] = mapped_column(String(120), default="")
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    thumb: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = now_column()


class Part(Base):
    """A part the shop keeps. `stock` only changes through parts.py, which never lets it go below zero."""
    __tablename__ = "parts"
    __table_args__ = (UniqueConstraint("shop_id", "name"), CheckConstraint("stock >= 0", name="stock"))

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    sku: Mapped[str] = mapped_column(String(40), default="")
    stock: Mapped[int] = mapped_column(Integer, default=0)
    low_level: Mapped[int] = mapped_column(Integer, default=2)  # at or below this, it's on the low-stock list
    price: Mapped[int] = mapped_column(Integer, default=0)  # centavos, what the customer pays
    cost: Mapped[int] = mapped_column(Integer, default=0)  # centavos, what the shop paid
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def is_low(self):
        return self.stock <= self.low_level


class StockMove(Base):
    """Every change to a part's stock, with why: received, used on a job, or counted."""
    __tablename__ = "stock_moves"

    id: Mapped[int] = mapped_column(primary_key=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    change: Mapped[int] = mapped_column(Integer)  # +5 received, -1 used
    reason: Mapped[str] = mapped_column(String(120))
    at: Mapped[datetime] = mapped_column(DateTime)
    by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    part: Mapped[Part] = relationship()
    by: Mapped[User | None] = relationship()


class EstimateLine(Base):
    """One thing on a job's estimate: labor or a part. The customer approves or declines each one."""
    __tablename__ = "estimate_lines"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity"), CheckConstraint("unit_price >= 0",
                                                                                         name="unit_price"))

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(8))
    description: Mapped[str] = mapped_column(String(160))
    reason: Mapped[str] = mapped_column(String(300), default="")  # why it's needed, in plain words
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[int] = mapped_column(Integer)  # centavos
    part_id: Mapped[int | None] = mapped_column(ForeignKey("parts.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(12), default=PROPOSED)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    decided_by: Mapped[str] = mapped_column(String(80), default="")  # "the customer" or a staff name
    stock_taken: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = now_column()

    part: Mapped[Part | None] = relationship()

    @property
    def amount(self):
        return self.quantity * self.unit_price


# ---------- Notices, push and the waitlist (stage 6) ----------

EMAIL, PUSH = "email", "push"
PENDING, SENT, FAILED, SKIPPED = "pending", "sent", "failed", "skipped"


class Notification(Base):
    """The outbox: one message to send, by email or push.

    `dedupe_key` is UNIQUE, so a notice can't be queued twice: "the day-before
    reminder for booking 7" is queued once even if the timer runs twice at the
    same moment (the second INSERT fails and is ignored). The timer sends
    pending ones and records what happened."""
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("dedupe_key"), NEVER_REUSE_IDS)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    appointment_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # "confirmed", "day_before", "ready"...
    channel: Mapped[str] = mapped_column(String(8))
    to: Mapped[str] = mapped_column(String(254), default="")  # an email address (push finds its own subscriptions)
    subject: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    link: Mapped[str] = mapped_column(String(400), default="")
    dedupe_key: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(12), default=PENDING, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(300), default="")
    send_after: Mapped[datetime] = mapped_column(DateTime)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = now_column()


class PushSubscription(Base):
    """A phone or browser that asked for push notices: for one booking (a guest) or
    for a person with a login. The endpoint is the browser's own address for it."""
    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("endpoint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    appointment_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(String(600))
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = now_column()


WAITING, OFFERED, TAKEN_UP, EXPIRED, LEFT = "waiting", "offered", "booked", "expired", "left"
WAITLIST_LABELS = {WAITING: "Waiting", OFFERED: "Offered a time", TAKEN_UP: "Booked", EXPIRED: "Ended",
                   LEFT: "Left the list"}


class WaitlistEntry(Base):
    """Someone who wants a time that's full. When a time frees up between first_day and
    last_day, the first person waiting is offered it: the time is held for them for
    30 minutes and they get a link. Claiming an entry is a conditional UPDATE
    (WHERE status = 'waiting'), so one entry is never offered two times at once."""
    __tablename__ = "waitlist"
    __table_args__ = (NEVER_REUSE_IDS,)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(48), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    phone: Mapped[str] = mapped_column(String(40))
    email: Mapped[str] = mapped_column(String(254), default="")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    vehicle_kind: Mapped[str] = mapped_column(String(8))
    service_ids: Mapped[str] = mapped_column(String(200))
    request_text: Mapped[str] = mapped_column(Text, default="", server_default="")  # for the Other Service
    first_day: Mapped[date] = mapped_column(Date)
    last_day: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default=WAITING, index=True)
    hold_token: Mapped[str] = mapped_column(String(48), default="")
    offered_at: Mapped[datetime | None] = mapped_column(DateTime)
    offer_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    offers: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = now_column()
