import os
import socket

import pytest
from sqlalchemy import select

from shiromoto import auth, create_app, db, seed
from shiromoto.dates import utcnow
from shiromoto.models import MECHANIC, OWNER, Shop, User

# Tests use a fresh SQLite file per test. To check everything on PostgreSQL
# too, set TEST_DATABASE_URL to an EMPTY Neon branch (its tables are dropped
# and made again for every test, so never point it at the live database).
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

CSRF = "test-csrf-token"
PASSWORD = "correct horse"


@pytest.fixture(autouse=True)
def stay_offline(monkeypatch):
    """Tests never go online. Any real network connection fails loudly.
    A Neon test run needs the network, so it's skipped then."""
    if TEST_DATABASE_URL:
        return

    def refuse(*args, **kwargs):
        raise RuntimeError("A test tried to go online. Fake the network call instead.")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "DATABASE": str(tmp_path / "test.sqlite"),
        "DATABASE_URL": TEST_DATABASE_URL,
    })
    with app.app_context():
        if TEST_DATABASE_URL:
            db.drop_tables()
        db.create_tables()
    return app


@pytest.fixture
def ctx(app):
    """An app context, for tests that use db.session() directly."""
    with app.app_context():
        yield db.session()


@pytest.fixture
def client(app):
    client = app.test_client()
    # Every POST needs the session's CSRF token. Put a known one in the session.
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    return client


def post(client, url, data=None, **kwargs):
    """POST a form with the right CSRF token, like a real page would."""
    # Logging in or out starts a new session (and a new token), so set it every time.
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    return client.post(url, data={"csrf_token": CSRF, **(data or {})}, **kwargs)


# ---------- The real shop and its people ----------

@pytest.fixture
def shop_id(app):
    """The real shop, as `flask create-owner` makes it (starter hours, bays and services)."""
    with app.app_context():
        s = db.session()
        shop = seed.starter_shop(s, "ShiroMoto Garage", utcnow())
        s.commit()
        return shop.id


def make_user(app, shop_id, email, role=OWNER, name=None, **fields):
    """Put a user straight into the database. Returns the id."""
    with app.app_context():
        s = db.session()
        user = auth.create_user(s, s.get(Shop, shop_id), email, name or email.split("@")[0].title(), PASSWORD,
                                role, utcnow())
        for field, value in fields.items():
            setattr(user, field, value)
        s.commit()
        return user.id


def log_in_as(client, user_id):
    """Skip the login form: put the user straight into the session."""
    with client.application.app_context():
        version = db.session().get(User, user_id).session_version
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["session_version"] = version
        session["csrf"] = CSRF
    return client


def is_logged_in(client):
    """The real check: load_user() runs on every request and drops a session that
    is no longer valid, so ask after a request what is left in the cookie."""
    client.get("/login")
    with client.session_transaction() as session:
        return "user_id" in session


def get(app, model, row_id):
    with app.app_context():
        return db.session().get(model, row_id)


def find(app, model, *where):
    with app.app_context():
        return db.session().scalars(select(model).where(*where)).all()


@pytest.fixture
def owner_id(app, shop_id):
    return make_user(app, shop_id, "owner@shop.example", OWNER, "Hiro Navarro")


@pytest.fixture
def mechanic_id(app, shop_id):
    return make_user(app, shop_id, "jun@shop.example", MECHANIC, "Jun dela Cruz")


@pytest.fixture
def owner(app, owner_id):
    """A browser logged in as the owner."""
    return log_in_as(app.test_client(), owner_id)


@pytest.fixture
def mechanic(app, mechanic_id):
    return log_in_as(app.test_client(), mechanic_id)


def week_form(changes=None):
    """The opening hours form as the starter week sends it, with changes like {"opens-0": "09:00"}.
    A change to None leaves that field out."""
    data = {}
    for day in range(6):  # Monday to Saturday; Sunday is closed
        data.update({f"open-{day}": "1", f"opens-{day}": "08:00", f"closes-{day}": "15:00" if day == 5 else "17:00",
                     f"break_starts-{day}": "12:00", f"break_ends-{day}": "13:00"})
    for key, value in (changes or {}).items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return data
