# Plan: ShiroMoto Garage

> Progress: Stage 1 (setup) was PR #1 and stage 2 (availability engine) was PR #2, both merged. Stage 3 (booking) was PR #3, merged. **Stages 4–8 are in one PR (#4), a commit per stage** (the owner's choice after stage 3). All 8 stages are built; going live is the owner's step (README, Put It Online). One PR per stage, with a stop for the owner's review after each one.


## Context
The owner wants an appointment booking system to practise **date/time handling, transactions and concurrency**. It shouldn't be a plain booking form: it's for the owner's future auto/moto repair shop, **ShiroMoto Garage**. The owner's choices:
- one business, built for this shop (not a general tool);
- customers can book as a guest (with a manage link) **or** make an account ("My Garage");
- each job needs a free **bay** of the right kind **and** a free **mechanic** with the right skill, and neither can be double-booked;
- full job tracking after check-in;
- feature groups: Staff & schedules, Customer experience, Front desk tools, Insights, Estimates & approval, Vehicle history, Parts & stock, Photos & checklist;
- email + in-app notices, and it must **install on a phone like an app** (PWA + push notifications);
- the "Garage night" look with a **purple** accent instead of orange;
- Render + Neon; **one PR per stage**, with a stop for review after each stage.

## Setup
- Repo: https://github.com/jcatibog014/appointment_booking_system (made by the owner), cloned to `C:\Users\USER\Documents\Workspace\appointment_booking_system`. One branch and PR per stage (`stage-1-setup`, ...). Package `shiromoto`, local port **5008**.
- Stack as Hire Wire / Plan-It: Flask 3, SQLAlchemy 2.0 typed models, Alembic, SQLite locally, Neon Postgres live, pytest, `.venv`, `AGENTS.md` + `CLAUDE.md`. New: `pywebpush` (push), Pillow (photos stored in the DB, like Sangkap).
- Copy from Hire Wire, which is newer than Plan-It:
  - `hirewire/db.py`, including `upgrade_db` with the SQLite foreign-keys fix at :106–134 (Plan-It lacks it);
  - `hirewire/dates.py` (zoneinfo helpers `to_local`, `to_utc`, `day_start_utc`, `week_start`) and `calendar_feed.py` (.ics);
  - from `auth.py`: CSRF `csrf_token`/`check_csrf`, login lockout, `safe_next`, the demo pattern;
  - toasts in `app.js`, `data-watch-changes`, `docs/take_screenshots.py`, `tests/test_readme.py`, `tests/test_look.py`.
- Copy from Plan-It: the app factory with CSP and Render checks (`planit/__init__.py:37`), the "View as" demo switch (`auth.py:298`), `seed.py` builder style, `docs/make_icons.py`, `site.webmanifest`, theme radio in Settings.
- Shop time zone `Asia/Manila`; money is stored as integer centavos (₱).

## People and roles
| Role | Can do |
|---|---|
| Owner | everything: Settings, staff, services, prices, Insights |
| Service advisor (front desk) | calendar, bookings, walk-ins, check-in, estimates, customers, waitlist, parts |
| Mechanic | My jobs today, job status, checklist, photos, findings and parts used |
| Customer | book, reschedule, cancel, approve estimates; with an account also My Garage |

The owner is made with `flask create-owner`. Staff join by an invite link. Customers sign up themselves (or never: the guest manage link `/b/<ref>?t=<token>` does everything). Password reset is by email.

## Data model (main tables)
- **Shop setup:** `shop_settings` (one row: rules such as min notice, max days ahead, cancel cutoff, hold minutes, slot step of 15 min). Also `weekly_hours` (shop and per mechanic: open, close, break) and `closures` (holidays, shop closed, mechanic leave; PH holidays seeded).
- **Bays:** `bay_types` (car lift, moto stand, alignment), `bays`, `skills`, `mechanic_skills`.
- **Services:** `services` (car/moto/both, working minutes, buffer after, bay type, skill, price from).
- **Customers:** `customers` (name, mobile, email, optional `user_id`) and `vehicles` (kind, plate stored normalised and unique, make, model, year, mileage).
- **Bookings:** `appointments` (ref like `SM-7K3Q`, manage token, customer, vehicle, bay, mechanic, `starts_at`/`ends_at` in UTC, status, source online/walk-in/phone, cancel reason, `version` column for optimistic locking, unique `idempotency_key`) and `appointment_services`.
- **`booked_slots`, the double-booking guard:** one row per 15-minute cell per resource, with `UNIQUE(resource_kind, resource_id, slot_start)`. Each row points to an appointment or to a hold (`expires_at`). This works the same on SQLite and Postgres.
- **Holds:** `holds` (5-minute slot hold while the customer fills in details).
- **Waitlist:** `waitlist` (wanted service, date range, times, status, offer expiry).
- **Jobs:** `jobs` (one per appointment: status, mileage in, promised time), `job_events` (timeline), `checklist_items`, `photos` (WebP bytes).
- **Estimates:** `estimate_lines` (labor/part, qty, unit price, status proposed/approved/declined).
- **Parts:** `parts` (stock, low level, price, cost) and `stock_moves`.
- **Reminders:** `service_reminders` (next PMS due by km or date per vehicle).
- **Notices:** `notifications` (outbox for email/push/in-app) and `push_subscriptions`.

## How the practice skills show up
**Date/time** (`shiromoto/dates.py`, `slots.py`, pure functions with `now` passed in so tests are exact):
- times are stored in UTC and shown in Manila time;
- free slots = shop hours ∩ mechanic hours, minus breaks, closures and leave, existing cells, min notice and max days ahead;
- **working-minutes arithmetic**: a 3-hour job that starts at 10:00 with a 12–1 lunch ends at 14:00, and a job can't run past closing;
- other rules: several services add up their time, there's a cancel cutoff, and the .ics file has the correct TZID.

**Transactions** (`booking.py`):
- a booking is one transaction: customer, vehicle, appointment, services and all slot cells for both bay and mechanic, or nothing;
- reschedule frees the old cells and takes the new ones in one transaction;
- cancel frees the cells, then offers the slot to the waitlist;
- an estimate approval and its stock move happen together.

**Concurrency:**
- the UNIQUE cells turn a lost race into an `IntegrityError`, which rolls back and shows "That time was just taken" with fresh times (HTTP 409);
- on Postgres there are also `SELECT … FOR UPDATE` on the bay and mechanic rows, and an **exclusion constraint** (`btree_gist`, `tstzrange &&`) as a second safety net (a Postgres-only migration);
- the 5-minute hold has an expiry, and expired holds are cleaned up lazily and by a timed task;
- an idempotency key on the form stops a double-tap from making 2 bookings;
- the `version` column means two advisors can't overwrite each other's change;
- parts stock uses `UPDATE … SET stock = stock - q WHERE stock >= q`, so stock never goes negative;
- a waitlist offer is claimed with a conditional update, so it's offered once;
- tests: 10 threads race for one slot and exactly 1 wins (SQLite file database, and Neon through `TEST_DATABASE_URL`).

## Look: "Garage night, purple"
- Charcoal sidebar (#16151c), off-white cards, a **purple accent** (around #7c3aed light / #a78bfa dark; the contrast is checked), and bold condensed headings (Barlow Condensed, self-hosted).
- A faint diamond-plate texture only on the login panel and the empty states.
- The login panel takes 1/3 of the screen. Dark mode and Log out go in Settings.
- Few emoji, aligned controls.

## Pages
- **Public:** Home (services and prices, hours, Book now); Book wizard (vehicle → services → day → time → details → confirm, with a hold countdown); Booking page `/b/<ref>` (status timeline, reschedule, cancel, add to calendar, estimate approval, photos and checklist, turn on notifications); Join waitlist; Sign up / Log in; My Garage (vehicles, upcoming, history, PMS due).
- **Staff:**
  - Today (a timeline per bay, with walk-in);
  - Calendar (day and week, by bay or by mechanic);
  - Bookings (filters), Jobs board (status columns), Job page (checklist, photos, estimate, parts, notes, timeline);
  - Customers & Vehicles, Parts, Waitlist, Insights (owner);
  - Settings: shop hours, closures, bays, services, staff (skills and hours), booking rules, notifications, theme, Log out.

## Stages (one PR each, with a review stop after each)
1. **Setup and shop setup.** Scaffold from Hire Wire, then: theme and sidebar, roles, owner command, staff invites, and Settings for hours, closures, bays, services, mechanics and skills, plus a demo skeleton with View As (Owner / Advisor / Mechanic / Customer).
2. **Availability engine.** `dates.py`, `slots.py` and the working-minutes rules, a public services page and a time picker that only shows times. Many edge-case tests: midnight, breaks, holidays, leave, notice, closing time.
3. **Booking without double booking.** Cells, holds, the booking transaction, cancel, reschedule, the idempotency key, Postgres locks and the exclusion constraint, the race tests, the guest manage link, customer accounts and a basic My Garage, and the .ics file.
4. **Front desk.** Today board, calendar day/week, bookings list, walk-ins, check-in and no-show, Customers & Vehicles, and optimistic locking.
5. **Jobs and shop extras.** Job statuses and timeline, check-in checklist and photos, estimates the customer approves line by line, parts and stock with atomic decrement and a low-stock list, and vehicle history with "next PMS due".
6. **Notices, waitlist and phone app.**
   - email through a Gmail app password (outbox; confirmation, the day before, ready for pickup, estimate waiting);
   - a PWA: manifest, `sw.js`, offline page, Install buttons, iPhone steps in Settings;
   - Web Push with VAPID keys (on iPhone it works once the app is installed, iOS 16.4+);
   - waitlist auto-offers;
   - a `/tasks/tick` endpoint behind a secret, called every 15 min by a free GitHub Actions cron. Render's free plan has no cron.
7. **Insights.** Bookings per day, a busiest-hours heatmap, bay use %, no-show and cancel rates with reasons, and revenue by service and by mechanic. Colours are checked with the dataviz validator.
8. **Demo, README and deploy.** Fill a realistic Manila week of sample data so every demo page has examples (`tests/test_demo.py`), app icons, README screenshots (`docs/take_screenshots.py`, `tests/test_readme.py`), `render.yaml` (`shiromoto-garage`, with `upgrade-db` before gunicorn), and step-by-step deploy notes for the owner.

After the stages:
- **Clearer booking words** (PR #5): "slots open", "Choose an available slot", and prices shown as estimates.
- **Simpler pages:**
  - the customer's menu is My Garage, Book, Services and Settings;
  - My Garage is their dashboard and start page (`/` sends a logged-in customer there; the shop's page is also at `/shop`);
  - Today folds its two answer lists into one and leaves the floor to the Calendar;
  - a booking's desk and cancel parts live in its one card;
  - Available Slots (`/desk/free`, `slots.free_time`) lists only the open stretches of 30 minutes or more, and counts them per day;
  - the phone app's service worker fetches styles and scripts from the network first, so a new look shows at once.
- **The owner's view, polished:** a quieter demo bar with an eye icon on View as, themed scrollbars, red (the shop must act) and blue (waiting for the customer) pills, whole rows that light up, hour lines and card-like blocks on the calendar, "No vehicles here" on empty job columns, slots that open the booking page, cancelled bookings struck through, plates as badges, Order buttons for low stock, a draining bar on waitlist offers, whole-number chart lines, a bigger Expected money, Book buttons and a map link on the shop's page.
- **Both sides agree on a new time** (migration 0008, `move_by`): staff propose a new time and the customer accepts or declines it (or staff record a phone answer, or move at once when the customer already agreed). During a job, staff can add a service from the menu to the estimate, and the customer's message lists each item's price.
- **Move requests** (`moves.py`, migration 0007): customers ask for a new time instead of moving the booking. The new time is held while the owner, an advisor or a mechanic decides, and approving moves the booking in one transaction.
- **The Other Service** (`quotes.py`, migration 0006): the customer writes what they need and books a time; staff send the price; the customer confirms or declines it. The answer is a conditional UPDATE on the price the customer saw, and declining shortens or cancels the booking in the same transaction.

Each stage adds `docs/PLAN.md` progress, tests, and sample data for its own features. Each PR retakes the screenshots of pages it changed.

## Verification (every stage)
- `pytest` passes on SQLite. Starting with stage 3, the race tests also run against a Neon `test` branch via `TEST_DATABASE_URL`.
- `flask --app shiromoto run --port 5008`, then click through the stage's pages as each View As role in light and dark mode. Check the phone width (390 px) with Playwright, borrowed from expense_tracker's `.venv`.
- Stage 3 by hand: open two browsers, hold the same 9:00 slot in both and confirm both. One should succeed and the other should see "just taken" with new times.
- Stage 6: install the PWA on Android Chrome and check that a test push arrives.
