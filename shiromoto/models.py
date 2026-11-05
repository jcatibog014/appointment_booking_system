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

from sqlalchemy import DDL, Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Integer, String, Table, \
    Text, Time, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

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


class AppointmentService(Base):
    """A service on a booking, copied as it was then: a later price change doesn't rewrite old bookings."""
    __tablename__ = "appointment_services"

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id", ondelete="CASCADE"), index=True)
    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(80))
    price_from: Mapped[int] = mapped_column(Integer)
    work_minutes: Mapped[int] = mapped_column(Integer)


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
