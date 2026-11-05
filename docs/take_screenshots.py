"""Take the README screenshots (docs/screenshots/*.png).

Retake them in every pull request that visibly changes a page, and add a new
one when a page or feature is added: the README shows everything the app can
do. tests/test_readme.py fails if the pictures and the README disagree.

Every picture uses "Try Demo", so only the made-up sample shop and people show.
It uses Microsoft Edge, which Windows already has, through Playwright.
Playwright is only a tool for this script, so it is not in requirements.txt:

    .venv\\Scripts\\python -m pip install playwright
    $env:DATABASE_URL = "sqlite:///C:/Temp/shiromoto-screenshots.sqlite"
    .venv\\Scripts\\flask --app shiromoto upgrade-db
    .venv\\Scripts\\flask --app shiromoto run --port 5008      (keep this one running)
    .venv\\Scripts\\python docs\\take_screenshots.py           (in another terminal)

Every picture is one screen (no full-page shots), so they are all the same size.
Add a line to PAGES when a page is added.
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5008"
OUT = Path(__file__).parent / "screenshots"
DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}

# (file name, address, theme, screen size, who: a demo person's name for View As, or None for the owner).
# {mechanic} is Jun dela Cruz's staff page. "pick:car:Car PMS" ticks services on the Book page
# and presses "Show free times"; "hold:..." does the same, then presses the first time. "garage:first" opens
# the first booking in My Garage.
PAGES = [
    ("home.png", "/", "light", DESKTOP, None),
    ("today.png", "/desk", "light", DESKTOP, None),
    ("today-dark.png", "/desk", "dark", DESKTOP, None),
    ("today-mechanic.png", "/desk", "light", DESKTOP, "Jun dela Cruz"),
    ("calendar.png", "/desk/calendar?by=bay", "light", DESKTOP, None),
    ("calendar-week.png", "/desk/calendar?view=week", "light", DESKTOP, None),
    ("calendar-dark.png", "/desk/calendar?by=mechanic", "dark", DESKTOP, None),
    ("bookings.png", "/desk/bookings", "light", DESKTOP, "Mika Reyes"),
    ("jobs.png", "/desk/jobs", "light", DESKTOP, None),
    ("job.png", "job:NAB 4821", "light", DESKTOP, None),
    ("estimate.png", "estimate:Toyota Vios", "light", DESKTOP, "Carlo Mendoza"),
    ("parts.png", "/desk/parts", "light", DESKTOP, "Mika Reyes"),
    ("vehicle.png", "vehicle:NAB 4821", "light", DESKTOP, None),
    ("customer.png", "customer:Carlo Mendoza", "light", DESKTOP, "Mika Reyes"),
    ("settings.png", "/settings", "light", DESKTOP, None),
    ("hours.png", "/settings/hours", "light", DESKTOP, None),
    ("closures.png", "/settings/closures", "light", DESKTOP, None),
    ("bays.png", "/settings/bays", "light", DESKTOP, None),
    ("services.png", "/settings/services", "light", DESKTOP, None),
    ("staff.png", "/settings/staff", "light", DESKTOP, None),
    ("mechanic.png", "{mechanic}", "light", DESKTOP, None),
    ("book-services.png", "/book?vehicle=car", "light", DESKTOP, "Carlo Mendoza"),
    ("book-times.png", "pick:car:Car PMS", "light", DESKTOP, "Carlo Mendoza"),
    ("free-times.png", "pick:car:Car Oil Change|Brake Service", "light", DESKTOP, None),
    ("free-times-dark.png", "pick:moto:Motorcycle PMS", "dark", DESKTOP, None),
    ("book-details.png", "hold:car:Car Oil Change", "light", DESKTOP, "Carlo Mendoza"),
    ("booking.png", "garage:first", "light", DESKTOP, "Carlo Mendoza"),
    ("booking-staff.png", "today:first", "light", DESKTOP, None),
    ("garage.png", "/garage", "light", DESKTOP, "Carlo Mendoza"),
    ("waitlist-join.png", "waitjoin:car:Car PMS", "light", DESKTOP, "Carlo Mendoza"),
    ("waitlist.png", "/desk/waitlist", "light", DESKTOP, "Mika Reyes"),
    ("phone-app.png", "/settings", "light", PHONE, "Carlo Mendoza"),
    ("phone.png", "/", "light", PHONE, "Carlo Mendoza"),
    ("phone-times.png", "pick:moto:CVT Cleaning", "light", PHONE, "Carlo Mendoza"),
]


def demo_page(browser, theme, size):
    page = browser.new_page(viewport=size, color_scheme=theme)
    page.goto(f"{BASE}/login")
    page.get_by_role("button", name="Try Demo").click()
    page.wait_for_url("**/desk")
    return page


def view_as(page, name):
    label = page.locator("#view-as-person option", has_text=name).inner_text()
    with page.expect_navigation():  # the menu applies itself (app.js)
        page.select_option("#view-as-person", label=label)


def save(page, name):
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    page.screenshot(path=OUT / name)
    print("saved", name)


def main(only=None):
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        login = browser.new_page(viewport=DESKTOP)
        login.goto(f"{BASE}/login")
        if not only or "login.png" in only:
            save(login, "login.png")
        if not only or "signup.png" in only:
            login.goto(f"{BASE}/signup")
            save(login, "signup.png")
        for name, address, theme, size, who in PAGES:
            if only and name not in only:
                continue
            page = demo_page(browser, theme, size)
            if "{mechanic}" in address:
                page.goto(f"{BASE}/settings/staff")
                address = page.get_by_role("link", name="Jun dela Cruz").get_attribute("href")
            if who:
                view_as(page, who)
            if address.startswith(("pick:", "hold:")):
                action, vehicle, names = address.split(":")
                page.goto(f"{BASE}/book?vehicle={vehicle}")
                for service in names.split("|"):
                    page.get_by_role("checkbox", name=service, exact=False).first.check()
                page.get_by_role("button", name="Show free times").click()
                page.wait_for_url("**/book?**service=**")
                if action == "hold":
                    page.locator(".time-chip").first.click()
                    page.wait_for_url("**/book/details/**")
            elif address.startswith("waitjoin:"):
                _, vehicle, wanted = address.split(":")
                page.goto(f"{BASE}/book?vehicle={vehicle}")
                page.get_by_role("checkbox", name=wanted, exact=False).first.check()
                page.get_by_role("button", name="Show free times").click()
                page.wait_for_url("**/book?**service=**")
                page.get_by_role("link", name="Join the waitlist").first.click()
                page.wait_for_url("**/book/waitlist**")
            elif address.startswith("job:"):
                page.goto(f"{BASE}/desk/jobs")
                page.locator(".job-card", has_text=address.split(":", 1)[1]).first.click()
                page.wait_for_url("**/jobs/**")
            elif address.startswith("estimate:"):
                page.goto(f"{BASE}/garage")
                page.locator(".in-shop a.row-title", has_text=address.split(":", 1)[1]).first.click()
                page.wait_for_url("**/b/**")
                page.locator("#estimate").scroll_into_view_if_needed()
            elif address.startswith("vehicle:"):
                page.goto(f"{BASE}/desk/customers?q={address.split(':', 1)[1]}")
                page.locator("a.row-title").first.click()
                page.get_by_role("link", name=address.split(":", 1)[1]).first.click()
                page.wait_for_url("**/desk/vehicles/**")
            elif address.startswith("customer:"):
                page.goto(f"{BASE}/desk/customers")
                page.get_by_role("link", name=address.split(":", 1)[1]).first.click()
                page.wait_for_url("**/desk/customers/**")
            elif address in ("garage:first", "today:first"):
                page.goto(BASE + ("/garage" if address == "garage:first" else "/desk"))
                page.locator("a.row-title[href^='/b/']").first.click()
                page.wait_for_url("**/b/**")
            else:
                page.goto(BASE + address)
            save(page, name)
            page.close()
        browser.close()


if __name__ == "__main__":
    main(set(sys.argv[1:]) or None)
