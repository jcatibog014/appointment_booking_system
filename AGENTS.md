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
- Other commands: `set-password <email>`, `purge-demos`, `tick` (the timer once), `make-vapid-keys`.
- Deploy: `render.yaml` (service `shiromoto-garage`, `upgrade-db` before gunicorn). `tests/test_deploy.py` checks that every `os.environ.get` setting in `create_app` is listed there and that secrets use `sync: false`. Icons: `docs/make_icons.py`.
- The full test run takes ~2.5 min on SQLite (each demo builds four weeks of history); far longer on Neon.
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
- **The front desk** (`views/desk.py`, `timeline.py`): statuses are booked → checked in, or no-show, or cancelled (`LIVE_STATUSES` still hold time).
  - `booking.check_in` works from 2 h before the start until the end.
  - `mark_no_show` works 15 min after the start and deletes the cells from this quarter hour on. `undo_no_show` re-adds them, and raises `Taken` if a walk-in got them.
  - Staff holds use `notice=0` (from now on, never the past).
  - The timeline is a CSS grid with one column per quarter hour. Blocks use the generated `gs-N`/`ge-N` classes (no inline styles under the CSP; rules 1–98 at the end of `style.css`).
  - Desk macros are imported `with context` (they use `shop`).
  - Migration 0003 recreates the PostgreSQL exclusion constraints to count only live statuses.
- **Jobs (`jobs.py`)** start at check-in (`booking.check_in` calls `open_job` in the same transaction) and only move along `MOVES`. Always call `check_version` BEFORE changing a job: reading a relationship autoflushes and bumps the version.
  - `decide` is a conditional UPDATE (`WHERE status = 'proposed'`), so a line is decided once.
  - An approved part calls `take_from_stock` (`UPDATE ... WHERE stock >= n`) in the same transaction. If it's short, the job goes to Waiting for parts, and `take_now` retries.
  - `parts.stock` also has a CHECK (>= 0). `receive` changes stock and writes a `StockMove`.
  - Photos (`photos.py`, Pillow) are re-encoded to WebP (max 1600 px, plus a thumbnail) without EXIF, and stored in the DB. `/photos/<id>` is for staff or whoever can open the booking.
  - `next_due` uses `Service.again_months`/`again_km`. `add_months` clamps the day (31 Jan + 1 month = 28/29 Feb).
- **Notices (`notices.py`)** are queued into `notifications`, with a UNIQUE `dedupe_key` (inserted in a savepoint, so a duplicate is skipped). `send_pending` claims each row (pending → sending) before sending.
  - Email uses `smtplib.SMTP_SSL` with a Gmail app password (`MAIL_USERNAME`/`MAIL_PASSWORD`). Push uses `pywebpush.webpush` with `VAPID_*` (`flask make-vapid-keys`). Both are looked up at call time, so tests fake them.
  - Demo shops' notices are always `skipped`.
  - Links in notices are `notices.booking_link` (itsdangerous, 45 days). `open_booking` accepts `?s=`.
  - Queue notices after the action commits, with `notices.safely(...)`, so a notice problem never undoes a booking.
- **The timer (`tick.py`):** `POST /tasks/tick` with `X-Tick-Key` (CSRF-free; 404 without `TICK_KEY`), or `flask tick`. GitHub Actions calls it every 15 min (`.github/workflows/tick.yml`, secrets `TICK_URL` and `TICK_KEY`).
- **Move requests (`moves.py`):** a customer's POST to `/b/<ref>/move/confirm` asks (`moves.ask`); staff's moves at once (`booking.move`). The asked-for time is a `Hold` (token in `appointments.move_hold_token`, up to 24 h). `approve` turns the hold's cells into the booking's in one transaction. Answers are claimed with `UPDATE ... WHERE move_status = 'asked'`. `booking.cancel` and `booking.move` call `moves.drop_request`. The timer runs `moves.expire`. The session doesn't expire objects on commit, so refresh after a Core UPDATE.
- **Other Service (`quotes.py`):** a `Service` with `is_other`. The customer's text travels as `?other=` through the Book page, then in `holds.request_text` and `waitlist.request_text`, onto `appointments.request_text`. `quote_status` goes to_price → priced → accepted or declined. Its line's `price_from` stays 0 until accepted. Answers use `UPDATE ... WHERE quote_status = 'priced' AND quote_price = :shown`. `decline` removes the line and shortens the booking (or cancels it if nothing is left) in one transaction.
- **Waitlist (`waitlist.py`):** entries are claimed with `UPDATE ... WHERE status = 'waiting'`, then the offer is a 30-minute `booking.hold(minutes=...)`. `expire` gives unused offers back. `booked_from_offer` runs when that hold is confirmed.
- **Phone app:** `/sw.js` is served from the root (`views/app_pages.py`). Change `VERSION` in `static/sw.js` when a cached file changes. Icons are drawn by `docs/make_icons.py` (Pillow). `/push/subscribe` takes JSON with the `X-CSRF-Token` header (the token is also in a meta tag).
- **Insights (`insights.py`, owner only):** plain Python sums over the period's bookings. The tests check each number by hand (`tests/test_insights.py`).
  - Charts are SVG built from `columns()`/`rows()`: rounded data ends, a square base, 2 px gaps, one series each (no legend; the title names it), a direct label on the biggest bar, and a "Show as a table" for each. Hover tips come from `data-tip` via `setUpChartTips()`.
  - Colours: `--chart` and `--heat-1..5` (+ `--heat-ink-N`) in all three theme blocks. They were checked with the dataviz validator (bars: lightness band and 3:1; heatmap: one hue, monotone), with text contrast of 4.5:1 or more on every shade. The dark heatmap runs the other way (busier = lighter).
  - The demo has four weeks of everyday visits (`seed.add_history`, a fixed random seed) so Insights shows real shapes.
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
