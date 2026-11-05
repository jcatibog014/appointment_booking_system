# ShiroMoto Garage

**ShiroMoto Garage** is the booking system for a car and motorcycle repair shop. Customers book a service, like a PMS, an oil change or new brake pads. The shop gives each job a free **bay** and a free **mechanic** with the right skill. Nobody can book the same bay or the same mechanic twice at the same time.

I made this project to practice three skills: **date and time handling** (opening hours, lunch breaks, holidays and leave), **transactions** (a booking is saved all at once, or not at all) and **concurrency** (two people who press "Book" at the same second can't both get the last slot).

It is built in 8 stages, one pull request each. This is **stage 1: the shop's setup**.

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

## What It Can Do (Stage 1)

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
| 2 | Free times: shop hours, mechanic hours, breaks, holidays and leave together |
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
