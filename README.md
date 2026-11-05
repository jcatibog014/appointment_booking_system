# ShiroMoto Garage

**ShiroMoto Garage** is the booking system for a car and motorcycle repair shop. Customers book a service, like a PMS, an oil change or new brake pads. The shop gives each job a free **bay** and a free **mechanic** with the right skill. Nobody can book the same bay or the same mechanic twice at the same time.

I made this project to practice three skills: **date and time handling** (opening hours, lunch breaks, holidays and leave), **transactions** (a booking is saved all at once, or not at all) and **concurrency** (two people who press "Book" at the same second can't both get the last slot).

It was built in 8 stages: the shop's setup, free times, booking with no double booking, the front desk, jobs, notices and the phone app, insights, and the demo.

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

## Insights (Stage 7)

For the owner, over the last 7, 30 or 90 days:
- **Tiles:** bookings, expected money (the booked services' starting prices plus the extra work customers approved), work done, bay use, the no-show rate and the cancel rate.
- **Bookings per day** (columns), and **busiest hours** (a weekday × hour grid: a stronger colour means busier).
- **Bay use:** booked time as a share of the open hours, without breaks and closed days, per bay.
- **Money by service and by mechanic**, how people booked (online, phone, walk-in), and why bookings were cancelled.
- Every chart has a **Show as a table** view, and a small tip on hover. The colours were checked with a colour validator for both light and dark mode. Dark mode has its own steps: there, a busier hour is lighter.

## Notices, the Waitlist and the Phone App (Stage 6)

**Notices.** Customers get a message when they book, when a booking moves or is cancelled, the evening before (from 5 PM), when there's an estimate to approve, when the vehicle is ready, and a week before a PMS is due. The front desk gets a push notice for each new online booking.
- **Email** goes through Gmail with an app password. **Push** goes to phones that turned it on.
- Every notice goes into an **outbox** first, with a unique key. The same reminder can't be queued twice, even if the timer runs twice at the same moment.
- Before sending, a notice is **claimed** ("pending" becomes "sending"), so two timers never send the same email twice.
- A failed email is tried again 3 times.
- Links in notices are **signed** by the app, so they open the customer's booking without the app keeping their secret link.

**The timer.** Render's free plan has no timer. So a free **GitHub Actions** schedule (`.github/workflows/tick.yml`) calls `/tasks/tick` every 15 minutes with a secret key. Each call clears held times that ran out, makes waitlist offers, queues reminders and sends what is due.

**The waitlist.** On a full day, a customer can **join the waitlist** for a range of days. When a time frees up (a cancellation, a no-show, a moved booking), the first person waiting is offered it:
- the time is **held for 30 minutes**, so nobody else can take it, and they get a link;
- if they don't take it, it goes to the next person (up to 3 offers each).

The offer is claimed with a conditional update, so two timers never offer the same person two times.

**The phone app.** The site installs like an app (a PWA):
- **Install** in Settings on Android, or **Share → Add to Home Screen** on iPhone;
- its own icon, it opens full screen, and an **offline** page when there's no signal;
- **push notifications** on the phone.

On iPhone, push works after installing the app (iOS 16.4 or newer).

## Jobs, Estimates and Parts (Stage 5)

Check-in opens a **job**. It moves along one road, and each step is written on a timeline the customer can read:

```
Checked in → Inspecting → Waiting for approval → In progress → Ready for pickup → Released
                                                    ↕
                                            Waiting for parts
```

- **Check-in checklist:** fuel level, body, glass, lights, tires, warning lights and items left inside, plus **photos**. Every photo is made smaller and saved as WebP, and its hidden camera data (like the GPS position) is removed.
- **Estimates:** the mechanic adds work or parts with a reason in plain words. The customer **approves or declines each line** from their booking link. The shop only does what was approved, and the job can't be "Ready" while a line is waiting.
- **Only one decision counts.** A line changes only while it is still "waiting" (`UPDATE ... WHERE status = 'proposed'`). If the customer and the front desk press at the same moment, the second one is told it was already decided.
- **Parts and stock:** an approved part is taken from stock **in the same transaction** with `stock = stock - n WHERE stock >= n`, so stock never goes below zero. If two jobs want the last brake pads at once, one gets them and the other goes to **Waiting for parts**. When the delivery comes, "The part is here" takes it. Every change is in the part's history, and there is a **low-stock** list.
- **Vehicle history:** every visit, the odometer and the extra work. **Next service due** says "after 6 months or 5,000 km, whichever comes first" (the owner sets this per service). It is also in the customer's My Garage.
- **Jobs board:** every vehicle in the shop, by step. A mechanic sees their own jobs first.

## The Front Desk (Stage 4)

- **Today** shows **the floor**: one row for each bay (or each mechanic), one column for each 15 minutes. You can see the bookings, the times someone is booking right now, lunch, and a red line for "now". Under it is **the queue**, with **Check in** (with the odometer reading) and **No-show** buttons.
- **No-show:** after 15 minutes the front desk can mark a no-show. The rest of that booking's time is freed at once, so a walk-in can have the bay. If the customer comes after all, **They came** takes the time back. But if a walk-in was already booked into it, the database refuses and nothing changes.
- **Walk-ins and callers:** staff book from **right now**. The customers' 2-hour notice is only for online bookings.
- **Calendar:** any day as the timeline (by bay or by mechanic), or a whole week.
- **Bookings:** every booking. Search by booking number, name, phone (written any way) or plate, and filter by status, who booked and dates.
- **Customers:** everyone who booked, with their vehicles, visits, no-shows and the shop's private notes.
- Mechanics see Today (their own jobs) and the Calendar. The owner and service advisors see everything.

## How Double Booking Is Stopped (Stage 3)

A booking takes **cells**: one row for each 15 minutes, for its bay **and** for its mechanic. The database has one rule on those rows: the same bay (or mechanic) and the same 15 minutes may appear only once.

```
booked_slots                       UNIQUE (resource, slot_start)
bay  Lift 1   8:00  ── booking SM-7K3QD
bay  Lift 1   8:15  ── booking SM-7K3QD
mech Jun      8:00  ── booking SM-7K3QD      a second booking of Lift 1 at 8:00
mech Jun      8:15  ── booking SM-7K3QD      is refused BY THE DATABASE
```

**A race.** Two people press 9:00 at the same second:
1. Both see 9:00 free, and both try to write their cells.
2. The first one is saved.
3. The second one breaks the rule. Its whole transaction is undone, so nothing is half saved.
4. It looks at the day again. It takes another bay or mechanic if one is free at 9:00. If not, it says "That time was just taken" and shows the times that are still free.

The tests do this for real: 10 threads press the same time at once. With one lift, exactly 1 wins. With two lifts and two mechanics, exactly 2 win.

**More safety:**
- **Holds.** Pressing a time keeps it for 5 minutes while you type your details (the cells belong to the hold). A hold that runs out stops counting, and the next person can take the time.
- **One booking per press.** The form carries a one-time key, so a double-click or Back + Book makes one booking, not two.
- **One transaction for each change.** Booking, moving and cancelling each save everything or nothing. Moving frees the old cells and takes the new ones together.
- **Nobody overwrites anybody.** Every booking has a version number. If the shop moves your booking while your page is open, your page's old Cancel is refused ("Someone else changed this booking").
- **On PostgreSQL** (the live site) there are two more locks:
  - the request locks the bay and mechanic rows first (`SELECT ... FOR UPDATE`), so the second request waits for the first;
  - an exclusion constraint refuses any two bookings whose times overlap on one bay or one mechanic.

**For customers:**
- Book as a **guest**: you get a booking number (like SM-7K3QD) and a private **link**. The link opens your booking from any phone, so you can move or cancel it. The browser remembers it too.
- **Add to calendar:** an .ics file with Manila time and a reminder the evening before.
- **Move or cancel online** until 12 hours before (the owner sets this). After that, call the shop.
- **An account (My Garage)** keeps your vehicles and bookings together and fills in the form for you. Open a guest booking's link while logged in, then press **Save to My Garage**.

**For staff:**
- **Free Times** now books for a caller or a walk-in.
- **Today** lists the day's bookings. A mechanic sees their own jobs.
- Staff can open any booking, and can move or cancel it at any time.

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

**The Book page** (customers): choose the vehicle, then the services, then a day. The strip shows how many times each day has. Every step is in the web address, so it can be shared.

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
| Service advisor | The front desk: bookings for callers and walk-ins |
| Mechanic | Their jobs (stage 5) and their own week |
| Customer | Book, move and cancel; My Garage |

**Safety**
- Passwords are saved only as hashes. After 5 wrong tries, the account waits 15 minutes.
- An invite link works once, for 7 days. Only a hash of it is saved.
- **Try Demo** makes a private sample shop with 7 bays, 4 mechanics and 14 services. It is yours alone, and it is deleted after 2 hours. **View as** lets you see the shop as the service advisor, a mechanic or a customer.

## Coming Next

| Stage | What it adds |
|---|---|
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
| ![Free times on a phone](docs/screenshots/phone-times.png) Free times on a phone | ![Your details, with the time kept for you](docs/screenshots/book-details.png) Your details (the time is kept) |
| ![A booking's page](docs/screenshots/booking.png) A booking's page | ![A booking, as staff see it](docs/screenshots/booking-staff.png) A booking, as staff see it |
| ![My Garage](docs/screenshots/garage.png) My Garage | ![Make an account](docs/screenshots/signup.png) Make an account |
| ![The calendar, one day by bay](docs/screenshots/calendar.png) Calendar: a day, by bay | ![The calendar, one week](docs/screenshots/calendar-week.png) Calendar: a week |
| ![The calendar by mechanic, in dark mode](docs/screenshots/calendar-dark.png) By mechanic, dark mode | ![Every booking, with search](docs/screenshots/bookings.png) Bookings |
| ![A customer's page](docs/screenshots/customer.png) A customer | ![The Jobs board](docs/screenshots/jobs.png) Jobs board |
| ![A job, with its estimate](docs/screenshots/job.png) A job and its estimate | ![The customer approves the estimate](docs/screenshots/estimate.png) The customer approves |
| ![Parts and stock](docs/screenshots/parts.png) Parts and stock | ![A vehicle's history and next service](docs/screenshots/vehicle.png) A vehicle's history |
| ![Joining the waitlist](docs/screenshots/waitlist-join.png) Join the waitlist | ![The waitlist at the desk](docs/screenshots/waitlist.png) The waitlist, for staff |
| ![The phone app settings on a phone](docs/screenshots/phone-app.png) The phone app | ![Insights](docs/screenshots/insights.png) Insights |
| ![Busiest hours and bay use, in dark mode](docs/screenshots/insights-dark.png) Busiest hours and bay use, dark mode | |

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
