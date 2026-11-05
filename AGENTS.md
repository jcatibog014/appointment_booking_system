# AGENTS.md

This file guides AI coding agents (Claude Code, Codex, Cursor, Copilot and others) working in this repository.

## Overview

**ShiroMoto Garage** is a booking system for the owner's future car and motorcycle repair shop, in Python (Flask). It is for this one business, not a general booking tool. The repo is `appointment_booking_system` and the Python package is `shiromoto`. The plan is in `docs/PLAN.md`: 8 stages, **one PR per stage**, and stop for the owner's review after each stage.

It is a learning project for **date/time handling, transactions and concurrency**, so those parts stay easy to read. That means short comments that explain *why*, plain functions that the tests call directly, and no extra libraries unless the owner asks for them. Write the README in simple (A1-level) English.

Python 3.13 is at `%LOCALAPPDATA%\Programs\Python\Python313\python.exe`. Use the virtual environment:

- Install: `.venv\Scripts\python -m pip install -r requirements.txt` (create `.venv` with `python -m venv .venv` first if it's missing).
- First time: `.venv\Scripts\flask --app shiromoto upgrade-db`, then `create-owner you@example.com` (it makes the shop with a starter week, bay types, skills, services and holidays).
- Run: `.venv\Scripts\flask --app shiromoto run --debug --port 5008`, then open http://127.0.0.1:5008. These ports are taken: Swap Meter 5000, Ambon Alert 5001, Ang Gastos! 5002, Sangkap 5003, Gawi 5004, Akda 5005, Hire Wire 5006, Plan-It 5007.
- Test: `.venv\Scripts\python -m pytest`.
  - Every test gets a fresh SQLite file and **never goes online**: an autouse fixture makes any network connection fail.
  - Set `TEST_DATABASE_URL` to an **empty** Neon branch to run the same tests on PostgreSQL. Each test drops and remakes the tables.
  - SQLAlchemy warnings fail the tests (`pytest.ini`).
- Other commands: `set-password <email>`, `purge-demos`.
- Screenshots: `docs/take_screenshots.py` (Playwright + Edge, from Try Demo; its top comment has the steps). Playwright isn't in this `.venv`; borrow `expense_tracker`'s.

## Architecture

- **One business, many sandboxes.** Every row has a `shop_id`.
  - The real shop is the one with `is_demo = False`.
  - Each Try Demo builds a private sample shop (`seed.build`). It is deleted after 2 hours: deleting the shop cascades to everything in it.
  - `shops.current()` is the request's shop: the logged-in person's shop, or else the real one. Every query starts from it.
  - Another shop's id gives 404 (`settings.shop_row`). `tests/test_settings.py::test_another_shops_rows_look_missing` checks this; add new routes to it.
- **Ids are never reused:** `shops` and `users` have `sqlite_autoincrement`. Otherwise SQLite hands a purged demo's ids to the next demo, and an old cookie (a user id) would open the new visitor's demo. PostgreSQL never reuses ids anyway.
- **Roles:** owner, advisor (service advisor), mechanic, customer.
  - Guard views with `auth.roles_required(...)`, `staff_required` or `owner_required`.
  - The demo's **View As** stores a person's id in `session["view_as"]`. `load_user` then sets `g.user` to that person and `g.visitor` to the real visitor. Never trust `view_as` across shops.
  - Staff join through `/join/<token>`. Only a SHA-256 of the token is stored. A link works once, for 7 days.
- **Times:**
  - Moments are naive UTC (`dates.utcnow()`).
  - Opening hours are `time`s on the shop's clock, and closures are `date`s on the shop's calendar.
  - Everyone sees the shop's clock (`shops.timezone`, Asia/Manila). Convert with `dates.at(day, time)` / `to_local`.
  - Every time is on a 15-minute step (`forms.quarter_time`).
  - Service minutes are **working** minutes: breaks don't count, and a job pauses over lunch.
- **Free times (`slots.py`)** work in minutes since midnight on the shop's clock, for one day.
  - The pure functions (`subtract`, `intersect`, `overlaps`, `finish`, `free_slots`) take plain numbers, and the tests call them directly.
  - `load_day()` reads the database once into a `DayPicture`: shop windows minus the break and part-day closures, each mechanic's windows (own hours, leave), and the active bays.
  - Its `busy` argument (`{("bay", id) or ("mechanic", id): [(start, end)]}`) is where stage 3 plugs in the existing bookings.
  - `day_choice()` adds the booking rules: the window from `bookable_days`, and minimum notice from `earliest_minute` (rounded up to the quarter hour).
  - A visit is one bay and one mechanic: `need_for()` refuses services that need different bay types. The work adds up; the clean-up is the longest one, done once.
  - The bay counts as taken over lunch (the car stays on the lift). Touching intervals don't overlap.
  - Every "no times" result has a reason for the person; keep them specific.
- **Booking (`booking.py`) is the only place bookings change.** Each function (`hold`, `confirm`, `move`, `cancel`, `place`) is one transaction: it commits, or rolls back and raises a `BookingError` subclass (`Taken`, `Gone`, `TooLate`, `Changed`) whose text is shown to the person.
  - The guard is `booked_slots`: one row per quarter hour per bay and per mechanic, with UNIQUE(resource_kind, resource_id, slot_start). Holds own cells too, with `expires_at`; expired holds are ignored by `busy_map` and purged at the next `hold`.
  - A lost race (IntegrityError/OperationalError) is retried up to `MAX_ATTEMPTS` with a fresh read.
  - On PostgreSQL, `lock_resources` takes `FOR UPDATE` on the bays and mechanics in id order, and `models.NO_OVERLAP_SQL` adds exclusion constraints (in migration 0002 and in `create_all` via an `after_create` DDL).
  - `confirm` turns a hold's cells into the booking's cells with one UPDATE and checks the count. The form's `key` is `idempotency_key` (unique), so a second press returns the same booking. Ask the database, not the session, whether a hold still exists.
  - `Appointment.version` is SQLAlchemy's `version_id_col`; pages send it back, and a stale one raises `Changed`.
  - `holds`, `appointments`, `shops` and `users` use `sqlite_autoincrement`: SQLite would otherwise reuse a deleted row's id.
  - The race tests (`tests/test_booking.py`) use real threads. Run them on Neon too (`TEST_DATABASE_URL`).
- **Booking pages:** `/b/<ref>` opens with the manage link (`?t=`; only its SHA-256 is stored, and it is remembered in `session["bookings"]`, which survives logging in), for the customer it belongs to, or for staff. Everyone else gets 404. Customers can change a booking until `cancel_cutoff_hours` before it; staff always can. `calendar_file.py` writes the .ics by hand (TZID + VTIMEZONE, escaping, 75-byte folding).
- **The Book page (`views/book.py`)** keeps the first steps as GETs, with every choice in the address. Staff see the same page as "Free Times", with the free mechanics and bays.
- **Money** is whole centavos (int), read with `forms.pesos` and shown with the `peso` filter. Never use floats.
- **Holidays:** `shops.ph_holidays(year)` works out Holy Week from `easter()` and National Heroes Day from `last_monday()`. Eid and Chinese New Year are added by hand.
- **Migrations:** after changing `models.py`, run `upgrade-db`, then `.venv\Scripts\alembic revision --autogenerate --rev-id 00NN -m "what changed"` and check the file.
  - A new NOT NULL column on an existing table needs a `server_default`.
  - `db.upgrade_db()` turns SQLite foreign keys off while migrating, so a batch rebuild can't cascade-delete other tables.
  - `tests/test_migrations.py` checks that the models and migrations agree.
- The page's CSP allows no inline styles or scripts.

## UI rules (the owner's)

- **Look: "Garage night" with a purple accent** (the owner's choice).
  - The charcoal sidebar and public header (`--side-*`) look the same in both themes.
  - Off-white cards; the accent is `#7c3aed` (light) / `#a78bfa` (dark), never orange.
  - Barlow Condensed (600/700, `static/fonts/`) for headings and the name.
  - A faint diamond-plate texture appears only on the login panel and empty states.
  - The logo is a hex nut on a solid tile (`mark()` in `_macros.html`; the nut takes the place's background colour via `--mark-nut`).
  - `tests/test_look.py` checks contrast and that both dark-mode blocks match. Dark mode is written twice in `style.css`: change both together.
- **Neat first:**
  - No decorative emoji. Controls are the same height (`--control-height`) with their bottoms aligned, and a form's main button goes last on the right.
  - Sizes in `rem`; the root font steps up at 1600 / 1920 / 2300px.
  - Layouts use container queries on `.page` (`container: main`).
- **Title Case** for the menu, page titles, section headings and empty-state titles. **Sentence case** for buttons, labels, hints and messages.
- **Preferences and Log out live in Settings** (and Log out in the account menu at the bottom of the sidebar). The sidebar only moves between pages.
- Messages (`flash()`) float as toasts. Pass `"error"` for a problem; that toast stays until closed. Write messages that say what changed.
- **Every page the demo can open must show an example**, for every View As role. `tests/test_demo.py` fails if one goes empty. Give every new feature sample data in `seed.py` in the same PR.
- **Every PR must keep the README's pictures up to date.**
  - Screenshots live in `docs/screenshots/`, and every one is shown in the README. Retake changed pages and add new ones to `docs/take_screenshots.py`. `tests/test_readme.py` fails if they disagree.
  - In a PR description, link pictures with a full `raw.githubusercontent.com` address.
- Commit messages and PR bodies go through files (`git commit -F`, `gh pr create --body-file`): PowerShell 5.1 breaks quotes in arguments.
