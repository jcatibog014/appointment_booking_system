"""The Book page: vehicle -> services -> day and time, all in the address."""
from datetime import timedelta

from shiromoto.dates import local_today
from shiromoto.models import Service

from .conftest import find, log_in_as, make_user


def service_id(app, shop_id, name):
    return find(app, Service, Service.shop_id == shop_id, Service.name == name)[0].id


def test_step_one_asks_for_the_vehicle(client, shop_id):
    page = client.get("/book").get_data(as_text=True)
    assert "Book a Service" in page and "/book?vehicle=car" in page and "/book?vehicle=moto" in page


def test_step_two_lists_only_that_vehicles_services(client, shop_id):
    page = client.get("/book?vehicle=moto").get_data(as_text=True)
    assert "Motorcycle PMS" in page and "CVT Cleaning" in page
    assert "Car PMS" not in page


def test_step_three_shows_days_and_times(app, client, shop_id):
    pms = service_id(app, shop_id, "Car Oil Change")
    page = client.get(f"/book?vehicle=car&service={pms}").get_data(as_text=True)
    # The starter shop has no mechanics yet, so every day says why it has no times.
    assert "Car Oil Change" in page and "about 45 min" in page and "from ₱1,800" in page
    assert "No mechanic who does this works on this day." in page or "Closed" in page


def test_times_show_once_there_is_a_mechanic(app, client, shop_id):
    make_user(app, shop_id, "jun@shop.example", "mechanic")
    oil = service_id(app, shop_id, "Car Oil Change")
    tomorrow = local_today() + timedelta(days=1)
    # Find a weekday that isn't a holiday in the next two weeks.
    for n in range(14):
        day = tomorrow + timedelta(days=n)
        page = client.get(f"/book?vehicle=car&service={oil}&day={day}").get_data(as_text=True)
        if "Ready around" in page:
            break
    assert "Ready around" in page and "8:00 AM" in page
    assert "Jun" not in page  # customers don't see who or which bay
    # Each day says how many start times are open, in words a customer reads easily.
    assert ' slots<span class="day-count-more"> open</span>' in page and " times<" not in page
    assert "Estimated price" in page


def test_prices_are_called_estimates(app, client, shop_id):
    services = client.get("/book?vehicle=car").get_data(as_text=True)
    assert "Prices are estimates." in services and "ask you before doing any extra work" in services
    assert "Prices are estimates that start from the amount shown." in client.get("/").get_data(as_text=True)


def test_staff_see_who_and_which_bay(app, shop_id, owner_id):
    make_user(app, shop_id, "jun@shop.example", "mechanic", "Jun dela Cruz")
    owner = log_in_as(app.test_client(), owner_id)
    oil = service_id(app, shop_id, "Car Oil Change")
    found = False
    for n in range(1, 15):
        day = local_today() + timedelta(days=n)
        page = owner.get(f"/book?vehicle=car&service={oil}&day={day}").get_data(as_text=True)
        if "Free mechanics" in page:
            found = True
            break
    assert found and "Jun dela Cruz" in page and "Lift 1" in page and "Available Slots" in page
    assert 'class="sidebar"' in page


def test_services_needing_two_kinds_of_bay_are_two_visits(app, client, shop_id):
    pms, alignment = service_id(app, shop_id, "Car PMS"), service_id(app, shop_id, "Wheel Alignment")
    page = client.get(f"/book?vehicle=car&service={pms}&service={alignment}").get_data(as_text=True)
    assert "Book them as separate visits." in page


def test_odd_addresses_dont_break_it(app, client, shop_id):
    cvt = service_id(app, shop_id, "CVT Cleaning")
    assert client.get("/book?vehicle=truck").status_code == 200  # back to step one
    # A motorcycle service on a car is dropped, which leaves nothing chosen: step two.
    page = client.get(f"/book?vehicle=car&service={cvt}&service=abc&service=99999").get_data(as_text=True)
    assert "Choose an available slot" in page
    oil = service_id(app, shop_id, "Car Oil Change")
    for bad in ("day=not-a-day", "day=1999-01-01", "from=2999-01-01"):
        assert client.get(f"/book?vehicle=car&service={oil}&{bad}").status_code == 200


def test_changing_services_keeps_the_choice(app, client, shop_id):
    oil = service_id(app, shop_id, "Car Oil Change")
    page = client.get(f"/book?vehicle=car&service={oil}&edit=1").get_data(as_text=True)
    assert f'value="{oil}" checked' in page


def test_the_day_strip_pages_through_the_booking_window(app, client, shop_id):
    oil = service_id(app, shop_id, "Car Oil Change")
    first = client.get(f"/book?vehicle=car&service={oil}").get_data(as_text=True)
    assert 'aria-label="Later days"' in first and 'aria-label="Earlier days"' not in first
    later = (local_today() + timedelta(days=28)).isoformat()
    last = client.get(f"/book?vehicle=car&service={oil}&from={later}").get_data(as_text=True)
    assert 'aria-label="Earlier days"' in last and 'aria-label="Later days"' not in last
