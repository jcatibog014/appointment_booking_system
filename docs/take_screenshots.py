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
# {mechanic} is Jun dela Cruz's staff page.
PAGES = [
    ("home.png", "/", "light", DESKTOP, None),
    ("today.png", "/desk", "light", DESKTOP, None),
    ("today-dark.png", "/desk", "dark", DESKTOP, None),
    ("today-mechanic.png", "/desk", "light", DESKTOP, "Jun dela Cruz"),
    ("settings.png", "/settings", "light", DESKTOP, None),
    ("hours.png", "/settings/hours", "light", DESKTOP, None),
    ("closures.png", "/settings/closures", "light", DESKTOP, None),
    ("bays.png", "/settings/bays", "light", DESKTOP, None),
    ("services.png", "/settings/services", "light", DESKTOP, None),
    ("staff.png", "/settings/staff", "light", DESKTOP, None),
    ("mechanic.png", "{mechanic}", "light", DESKTOP, None),
    ("phone.png", "/", "light", PHONE, "Carlo Mendoza"),
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
        for name, address, theme, size, who in PAGES:
            if only and name not in only:
                continue
            page = demo_page(browser, theme, size)
            if "{mechanic}" in address:
                page.goto(f"{BASE}/settings/staff")
                address = page.get_by_role("link", name="Jun dela Cruz").get_attribute("href")
            if who:
                view_as(page, who)
            page.goto(BASE + address)
            save(page, name)
            page.close()
        browser.close()


if __name__ == "__main__":
    main(set(sys.argv[1:]) or None)
