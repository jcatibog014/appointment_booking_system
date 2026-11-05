# ShiroMoto Garage

**ShiroMoto Garage** is the booking system for a car and motorcycle repair shop. Customers book a service, like a PMS, an oil change or new brake pads. The shop gives each job a free **bay** and a free **mechanic** with the right skill. Nobody can book the same bay or the same mechanic twice at the same time.

I made this project to practice three skills: **date and time handling** (opening hours, lunch breaks, holidays and leave), **transactions** (a booking is saved all at once, or not at all) and **concurrency** (two people who press "Book" at the same second can't both get the last slot).

It is built in 8 stages, one pull request each. Stage 1 set up the shop. This is **stage 2: free times**.

![The desk's Today page](docs/screenshots/today.png)

## The Shop's Pieces

```
Service (Car PMS, 3 h)  ──needs──►  a kind of bay (Car lift)  +  a skill (Engine)
                                          │                          │
                                     Lift 1, Lift 2            Jun, Ana (mechanics)
```

- A **bay** holds one vehicle at a time: a car lift, a car bay, an alignment bay or a motorcycle stand.
- A **mechanic** has skills (Engine, Brakes, Electrical, Aircon, Tires, Motorcycle) and working hours.
- A **service** says which kind of bay and which skill it needs, how long the work takes, and the clean-up time after it.
- Work time skips the lunch break. A 3-hour job that starts at 10:00, with lunch from 12:00 to 1:00, ends at 2:00.

## How Free Times Are Found (Stage 2)

A time is free when **a mechanic with every skill** is working for the whole job **and a bay of the right kind** is empty for the whole job.

```
Shop hours        8:00 ─────────── 12:00   lunch   1:00 ──────────── 5:00
Jun's hours       8:00 ─────────── 12:00           1:00 ──────────── 5:00
Car PMS (3 h 15 min with clean-up), starting 10:00:
                        10:00 ════ 12:00 (pause)   1:00 ═══ 2:15  ready
```

- Every time is in **minutes since midnight** on the shop's clock (8:30 AM is 510). The finder adds, subtracts and compares plain numbers. Only the final start becomes a real UTC moment.
- **Work time skips breaks.** A job never starts during lunch, and it must finish before closing on the same day.
- **Closures:** Sundays, holidays and whole-day closures give no times. A part-day closure (1:00 to 5:00 PM) cuts the afternoon.
- **People:** a mechanic's own hours, days off and leave (a whole day, or only part of it) count too. Ana is off on Mondays, so on Mondays there are no Aircon times.
- **Booking rules:** the minimum notice (2 hours: at 9:10 AM the first time is 11:15 AM), the furthest day (30 days ahead), and "today" means **Manila's** today. At 11:30 PM in UTC, Manila is already the next morning.
- **Several services in one visit** add up their work time. The clean-up happens once, at the end. They must use the same kind of bay: a car is not moved from the lift to the alignment bay halfway through.
- When a day has no times, the page says why: "Closed: Bonifacio Day", "No mechanic who does this works on this day", "The times left on this day are too soon to book"...

**The Book page** (customers): choose the vehicle, then the services, then a day. The strip shows how many times each day has. Every step is in the web address, so it can be shared. Online booking itself comes in stage 3. Until then the page shows the shop's phone number.

**Free Times** (staff, in the menu): the same page, plus which mechanics and which bays are free at each time.

## What It Could Already Do (Stage 1)

**For customers**
- The shop's page: services and prices for cars and for motorcycles, opening hours, today's status ("Open 8:00 AM to 5:00 PM" or "Closed: Bonifacio Day") and the closures in the next 30 days.

**For the owner (Settings)**
- **Shop details and booking rules:** the earliest and latest booking, the online cancel cut-off, and how long a chosen time is kept.
- **Opening hours:** each day of the week, with a lunch break. Times use 15-minute steps.
- **Closures and leave:** shop closures, part-day closures (for example 1:00 to 5:00 PM for training), and a mechanic's leave. **Add PH holidays** adds the Philippine holidays for a year. Holy Week is worked out for each year, because Easter moves.
- **Bays:** add, rename, or take a bay out of use. Add new kinds of bay.
- **Services and prices:** time, clean-up, bay, skill and price. Prices are saved as whole centavos, never as decimals.
- **Staff:** invite a service advisor or mechanic with a one-time link, change roles, set skills and working hours, and switch off a login.

**For staff**
- **Today:** the shop's hours, who is working (or on leave), the bays, and the closures in the next 14 days. The owner also sees a setup checklist.

**Roles**

| Role | Can do |
|---|---|
| Owner | Everything, including Settings for the shop |
| Service advisor | The front desk (bookings come in stage 3) |
| Mechanic | Their jobs (stage 5) and their own week |
| Customer | Book, change and cancel (stage 3) |

**Safety**
- Passwords are saved only as hashes. After 5 wrong tries, the account waits 15 minutes.
- An invite link works once, for 7 days. Only a hash of it is saved.
- **Try Demo** makes a private sample shop with 7 bays, 4 mechanics and 14 services. It is yours alone, and it is deleted after 2 hours. **View as** lets you see the shop as the service advisor, a mechanic or a customer.

## Coming Next

| Stage | What it adds |
|---|---|
| 3 | Booking with no double booking: holds, transactions, races, cancel and reschedule |
| 4 | The front desk: day and week calendar, walk-ins, check-in |
| 5 | Jobs: status, checklist and photos, estimates the customer approves, parts and stock |
| 6 | Email, reminders, waitlist, and an app for your phone |
| 7 | Insights: busy hours, bay use, no-shows and money |
| 8 | Demo, pictures and going online |

## Pictures

| | |
|---|---|
| ![The shop's page](docs/screenshots/home.png) The shop's page | ![The login page](docs/screenshots/login.png) Log in |
| ![Today, as a mechanic](docs/screenshots/today-mechanic.png) Today, as a mechanic | ![Today in dark mode](docs/screenshots/today-dark.png) Dark mode |
| ![Settings](docs/screenshots/settings.png) Settings | ![Opening hours](docs/screenshots/hours.png) Opening hours |
| ![Closures and leave](docs/screenshots/closures.png) Closures and leave | ![Bays](docs/screenshots/bays.png) Bays |
| ![Services and prices](docs/screenshots/services.png) Services and prices | ![Staff](docs/screenshots/staff.png) Staff |
| ![A mechanic's skills and hours](docs/screenshots/mechanic.png) A mechanic's skills and hours | ![The shop's page on a phone](docs/screenshots/phone.png) On a phone |
| ![Book: choose services](docs/screenshots/book-services.png) Book: choose services | ![Book: free times](docs/screenshots/book-times.png) Book: free times |
| ![Free Times for staff](docs/screenshots/free-times.png) Free Times, for staff | ![Free Times in dark mode](docs/screenshots/free-times-dark.png) Free Times, dark mode |
| ![Free times on a phone](docs/screenshots/phone-times.png) Free times on a phone | |

All people in these pictures are made up. They come from Try Demo.

## Tools

| Tool | What it is | What it does here |
|---|---|---|
| Python 3.13 | Language | The whole server |
| Flask | Web framework | Pages and forms |
| SQLAlchemy | Database library | The tables, as Python classes |
| Alembic | Migration tool | Changes the tables and keeps the data |
| SQLite | Database | Saves data on my computer and in tests |
| PostgreSQL (Neon) | Database | Saves data on the live site |
| zoneinfo, tzdata | Python's time zone tools | Manila time for every booking |
| pytest | Test tool | Checks that everything works, without going online |
| Playwright | Browser tool | Takes the pictures for this page |
| Barlow Condensed | Font | Headings and the name. The two small files are inside the app. |
| HTML, CSS, JavaScript | Languages | The pages. They also work without JavaScript. |

## Run It on Your Computer

You need Python 3.13. Use these commands in PowerShell, in this folder.

1. Make the virtual environment and install the tools:

   ```
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```

2. Make the tables:

   ```
   .venv\Scripts\flask --app shiromoto upgrade-db
   ```

3. Make the shop and your owner login:

   ```
   .venv\Scripts\flask --app shiromoto create-owner you@example.com
   ```

4. Start the site, then open http://127.0.0.1:5008 and log in (or press Try Demo):

   ```
   .venv\Scripts\flask --app shiromoto run --debug --port 5008
   ```

Run the tests with:

```
.venv\Scripts\python -m pytest
```

## Forgot Your Password?

The owner can set a new one from the command line:

```
.venv\Scripts\flask --app shiromoto set-password you@example.com
```
