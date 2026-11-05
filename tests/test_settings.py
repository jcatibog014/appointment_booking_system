"""The owner's setup: hours, closures, bays, services and staff. Plus everyone's own account."""
from datetime import date, time, timedelta

from sqlalchemy import select

from shiromoto import db, seed
from shiromoto.dates import local_today, utcnow
from shiromoto.models import LEAVE, MECHANIC, Bay, BayType, Closure, Service, Shop, ShopHours, Skill, StaffHours, \
    User

from .conftest import PASSWORD, find, get, is_logged_in, log_in_as, post, week_form


def hours_of(app, shop_id):
    return {row.weekday: row for row in find(app, ShopHours, ShopHours.shop_id == shop_id)}


# ---------- The hub ----------

def test_the_owner_sees_every_setup_page_on_the_hub(owner):
    page = owner.get("/settings").get_data(as_text=True)
    for title in ("Opening Hours", "Closures and Leave", "Bays", "Services and Prices", "Staff", "Appearance",
                  "Log Out"):
        assert title in page
    assert "Open 6 days a week" in page and "16 services offered" in page


def test_everyone_can_change_their_name_and_phone(app, mechanic, mechanic_id):
    response = post(mechanic, "/settings", {"action": "profile", "name": "Jun D.", "phone": "0917 000 1111"})
    assert response.status_code == 302
    assert get(app, User, mechanic_id).name == "Jun D."
    assert post(mechanic, "/settings", {"action": "profile", "name": ""}).status_code == 400


def test_changing_the_password_needs_the_current_one(app, owner, owner_id):
    wrong = post(owner, "/settings", {"action": "password", "current_password": "nope",
                                      "new_password": "brand new pass", "confirm_password": "brand new pass"})
    assert wrong.status_code == 400 and b"isn&#39;t your current password" in wrong.data
    other_device = log_in_as(app.test_client(), owner_id)
    right = post(owner, "/settings", {"action": "password", "current_password": PASSWORD,
                                      "new_password": "brand new pass", "confirm_password": "brand new pass"})
    assert right.status_code == 302
    assert is_logged_in(owner) and not is_logged_in(other_device)


# ---------- Shop details and rules ----------

def test_saving_the_shops_details_and_rules(app, owner, shop_id):
    post(owner, "/settings/shop", {"action": "details", "name": "ShiroMoto Garage", "tagline": "Fast and honest",
                                   "phone": "0917 123 4567", "email": "hi@shiromoto.example",
                                   "address": "Marikina"})
    post(owner, "/settings/shop", {"action": "rules", "min_notice_minutes": "60", "max_days_ahead": "14",
                                   "cancel_cutoff_hours": "24", "hold_minutes": "10"})
    shop = get(app, Shop, shop_id)
    assert (shop.phone, shop.address, shop.tagline) == ("0917 123 4567", "Marikina", "Fast and honest")
    assert (shop.min_notice_minutes, shop.max_days_ahead, shop.cancel_cutoff_hours, shop.hold_minutes) == (60, 14, 24, 10)
    bad = post(owner, "/settings/shop", {"action": "rules", "min_notice_minutes": "60", "max_days_ahead": "999",
                                         "cancel_cutoff_hours": "24", "hold_minutes": "10"})
    assert bad.status_code == 400 and get(app, Shop, shop_id).max_days_ahead == 14


# ---------- Opening hours ----------

def test_saving_a_new_week(app, owner, shop_id):
    response = post(owner, "/settings/hours", week_form({"opens-0": "09:00", "open-6": "1", "opens-6": "09:00",
                                                         "closes-6": "12:00"}))
    assert response.status_code == 302
    week = hours_of(app, shop_id)
    assert week[0].opens == time(9) and week[6].is_open and week[6].closes == time(12)
    assert week[6].break_starts is None


def test_closing_a_day(app, owner, shop_id):
    post(owner, "/settings/hours", week_form({"open-5": None}))
    assert not hours_of(app, shop_id)[5].is_open


def test_hours_must_make_sense(app, owner, shop_id):
    cases = [
        ({"closes-0": "07:00"}, "Closing must be later than the start."),
        ({"opens-1": "08:10"}, "Use a time on the quarter hour"),
        ({"break_ends-2": None}, "Give the break a start and an end"),
        ({"break_starts-3": "16:30", "break_ends-3": "17:30"}, "The break must be inside the opening hours."),
        ({"break_starts-4": "13:00", "break_ends-4": "12:00"}, "The break&#39;s end must be later than the start."),
        ({"opens-0": None}, "Choose a time."),
    ]
    for changes, message in cases:
        response = post(owner, "/settings/hours", week_form(changes))
        assert response.status_code == 400, changes
        assert message in response.get_data(as_text=True), changes
    assert hours_of(app, shop_id)[0].opens == time(8)  # nothing was saved


# ---------- Closures ----------

def test_adding_a_half_day_closure_and_removing_it(app, owner, shop_id):
    day = local_today() + timedelta(days=10)
    response = post(owner, "/settings/closures", {"kind": "closed", "reason": "Staff meeting",
                                                  "starts_on": day.isoformat(), "from_time": "13:00",
                                                  "to_time": "17:00"})
    assert response.status_code == 302
    [closure] = find(app, Closure, Closure.reason == "Staff meeting")
    assert (closure.starts_on, closure.ends_on) == (day, day)  # an empty last day means one day
    assert (closure.from_time, closure.to_time) == (time(13), time(17))
    assert "Staff meeting" in owner.get("/settings/closures").get_data(as_text=True)
    post(owner, f"/settings/closures/{closure.id}/delete")
    assert find(app, Closure, Closure.reason == "Staff meeting") == []


def test_leave_belongs_to_one_person(app, owner, shop_id, mechanic_id):
    day = local_today() + timedelta(days=3)
    post(owner, "/settings/closures", {"kind": "leave", "user_id": mechanic_id, "reason": "Vacation",
                                       "starts_on": day.isoformat(), "ends_on": (day + timedelta(days=2)).isoformat()})
    [leave] = find(app, Closure, Closure.kind == LEAVE)
    assert leave.user_id == mechanic_id and leave.ends_on == day + timedelta(days=2)
    missing = post(owner, "/settings/closures", {"kind": "leave", "reason": "Vacation", "starts_on": day.isoformat()})
    assert missing.status_code == 400 and b"Choose who is on leave." in missing.data


def test_closures_must_make_sense(owner, shop_id):
    day = local_today() + timedelta(days=5)
    cases = [
        ({"ends_on": (day - timedelta(days=1)).isoformat()}, "The last day can&#39;t be before the first day."),
        ({"from_time": "13:00"}, "Give both times for part of a day"),
        ({"from_time": "15:00", "to_time": "14:00"}, "The end must be later than the start."),
        ({"reason": ""}, "Enter a reason."),
    ]
    for changes, message in cases:
        form = {"kind": "closed", "reason": "Closed", "starts_on": day.isoformat(), **changes}
        response = post(owner, "/settings/closures", form)
        assert response.status_code == 400 and message in response.get_data(as_text=True), changes


def test_the_holiday_button_adds_next_years_holidays_once(app, owner, shop_id):
    year = local_today().year + 1
    response = post(owner, "/settings/closures/holidays", {"year": year})
    assert response.status_code == 302  # the starter shop had them already
    assert post(owner, "/settings/closures/holidays", {"year": 1999}).status_code == 400


# ---------- Bays ----------

def test_adding_renaming_and_retiring_a_bay(app, owner, shop_id):
    [lift] = find(app, BayType, BayType.shop_id == shop_id, BayType.name == "Car lift")
    post(owner, "/settings/bays", {"action": "add_bay", "name": "Lift 2", "bay_type_id": lift.id})
    [bay] = find(app, Bay, Bay.name == "Lift 2")
    assert bay.bay_type_id == lift.id and bay.is_active
    duplicate = post(owner, "/settings/bays", {"action": "add_bay", "name": "Lift 2", "bay_type_id": lift.id})
    assert duplicate.status_code == 400 and b"already a bay with this name" in duplicate.data
    post(owner, f"/settings/bays/{bay.id}", {"name": "Lift Two"})
    post(owner, f"/settings/bays/{bay.id}", {"action": "toggle"})
    bay = get(app, Bay, bay.id)
    assert bay.name == "Lift Two" and not bay.is_active


def test_adding_a_kind_of_bay(app, owner, shop_id):
    post(owner, "/settings/bays", {"action": "add_type", "type_name": "Wash bay", "vehicle_kind": "any"})
    assert find(app, BayType, BayType.name == "Wash bay")[0].vehicle_kind == "any"


# ---------- Services ----------

def service_form(app, shop_id, **changes):
    [lift] = find(app, BayType, BayType.shop_id == shop_id, BayType.name == "Car lift")
    [engine] = find(app, Skill, Skill.shop_id == shop_id, Skill.name == "Engine")
    return {"name": "Timing Belt", "category": "Repair", "price_from": "4,500", "vehicle_kind": "car",
            "bay_type_id": lift.id, "work_minutes": "240", "buffer_minutes": "15", "skill_id": engine.id,
            "description": "New timing belt and tensioner.", "is_active": "1", **changes}


def test_adding_and_editing_a_service(app, owner, shop_id):
    assert post(owner, "/settings/services/new", service_form(app, shop_id)).status_code == 302
    [service] = find(app, Service, Service.name == "Timing Belt")
    assert (service.price_from, service.work_minutes, service.buffer_minutes) == (450000, 240, 15)
    post(owner, f"/settings/services/{service.id}", service_form(app, shop_id, price_from="4999.50", is_active=None))
    service = get(app, Service, service.id)
    assert service.price_from == 499950 and not service.is_active
    page = owner.get("/settings/services").get_data(as_text=True)
    assert "Timing Belt" in page and "₱4,999.50" in page and "Hidden" in page
    # Hidden services aren't on the shop's price list.
    assert "Timing Belt" not in owner.get("/").get_data(as_text=True)


def test_a_service_must_make_sense(app, owner, shop_id):
    [stand] = find(app, BayType, BayType.shop_id == shop_id, BayType.name == "Motorcycle stand")
    cases = [
        ({"bay_type_id": stand.id}, "A motorcycle stand can&#39;t take this vehicle."),
        ({"work_minutes": "50"}, "Choose a length in steps of 15 minutes."),
        ({"price_from": "12.345"}, "Write an amount like 1500"),
        ({"name": "Car PMS"}, "There is already a service with this name."),
        ({"name": ""}, "Enter the service&#39;s name."),
    ]
    for changes, message in cases:
        response = post(owner, "/settings/services/new", service_form(app, shop_id, **changes))
        assert response.status_code == 400, changes
        assert message in response.get_data(as_text=True), changes


def test_a_failed_edit_changes_nothing(app, owner, shop_id):
    [pms] = find(app, Service, Service.name == "Car PMS")
    response = post(owner, f"/settings/services/{pms.id}", service_form(app, shop_id, name="Car PMS",
                                                                        work_minutes="50"))
    assert response.status_code == 400
    assert get(app, Service, pms.id).work_minutes == 180


# ---------- Staff ----------

def test_inviting_someone_shows_a_link_once(app, owner, shop_id):
    page = post(owner, "/settings/staff", {"name": "Ana Bautista", "role": "mechanic"}).get_data(as_text=True)
    assert "http://localhost/join/" in page and "Waiting to Join" in page
    again = owner.get("/settings/staff").get_data(as_text=True)
    assert "/join/" not in again and "Ana Bautista" in again


def test_setting_a_mechanics_skills_and_week(app, owner, shop_id, mechanic_id):
    skills = find(app, Skill, Skill.shop_id == shop_id, Skill.name.in_(("Engine", "Brakes")))
    post(owner, f"/settings/staff/{mechanic_id}", {"action": "skills", "skill": [str(s.id) for s in skills]})
    form = {"action": "hours"}
    for day in range(6):
        form.update({f"works-{day}": "1", f"starts-{day}": "10:00", f"ends-{day}": "15:00"})
    form.pop("works-2")  # Wednesday off
    assert post(owner, f"/settings/staff/{mechanic_id}", form).status_code == 302
    with app.app_context():
        jun = db.session().get(User, mechanic_id)
        assert sorted(s.name for s in jun.skills) == ["Brakes", "Engine"]
        hours = {h.weekday: h for h in jun.hours}
        assert hours[0].starts == time(10) and not hours[2].works and not hours[6].works
    post(owner, f"/settings/staff/{mechanic_id}", {"action": "same_as_shop"})
    assert find(app, StaffHours, StaffHours.user_id == mechanic_id) == []


def test_a_mechanics_hours_stay_inside_the_shops(app, owner, shop_id, mechanic_id):
    form = {"action": "hours", "works-0": "1", "starts-0": "07:00", "ends-0": "17:00",
            "works-6": "1", "starts-6": "09:00", "ends-6": "12:00"}
    response = post(owner, f"/settings/staff/{mechanic_id}", form).get_data(as_text=True)
    assert "Keep it inside the shop&#39;s opening hours." in response
    assert "The shop is closed on this day." in response


def test_the_owner_cant_demote_or_lock_themselves(owner, owner_id):
    assert post(owner, f"/settings/staff/{owner_id}", {"action": "role", "role": "mechanic"}).status_code == 403
    assert post(owner, f"/settings/staff/{owner_id}", {"action": "active"}).status_code == 403


def test_changing_a_role(app, owner, mechanic_id):
    post(owner, f"/settings/staff/{mechanic_id}", {"action": "role", "role": "advisor"})
    assert get(app, User, mechanic_id).role == "advisor"


# ---------- Privacy between shops ----------

def test_another_shops_rows_look_missing(app, owner, shop_id):
    """A demo shop sits in the same database. Its ids must never work from the real shop."""
    with app.app_context():
        s = db.session()
        other = seed.build(s, utcnow(), demo=True)
        other.demo_expires_at = utcnow() + timedelta(hours=2)
        s.commit()
        bay = s.scalar(select(Bay).where(Bay.shop_id == other.id))
        service = s.scalar(select(Service).where(Service.shop_id == other.id))
        person = s.scalar(select(User).where(User.shop_id == other.id, User.role == MECHANIC))
        closure = s.scalar(select(Closure).where(Closure.shop_id == other.id))
    assert owner.get(f"/settings/services/{service.id}").status_code == 404
    assert owner.get(f"/settings/staff/{person.id}").status_code == 404
    assert post(owner, f"/settings/bays/{bay.id}", {"name": "Mine now"}).status_code == 404
    assert post(owner, f"/settings/closures/{closure.id}/delete").status_code == 404
    assert post(owner, "/settings/closures", {"kind": "leave", "user_id": person.id, "reason": "x",
                                              "starts_on": date.today().isoformat()}).status_code == 400
    assert get(app, Bay, bay.id).name != "Mine now"

