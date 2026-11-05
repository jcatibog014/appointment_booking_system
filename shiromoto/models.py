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

from sqlalchemy import Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Integer, String, Table, Text, \
    Time, UniqueConstraint
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
