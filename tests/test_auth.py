"""Logging in, roles, staff invite links, and the command line."""
from datetime import timedelta

from shiromoto import auth, db
from shiromoto.dates import utcnow
from shiromoto.models import ADVISOR, CUSTOMER, MECHANIC, Invite, Shop, User

from .conftest import PASSWORD, find, get, is_logged_in, log_in_as, make_user, post


# ---------- Logging in ----------

def test_the_owner_logs_in_with_email_and_lands_on_the_desk(app, client, owner_id):
    response = post(client, "/login", {"email": " Owner@Shop.example ", "password": PASSWORD})
    assert response.status_code == 302 and response.headers["Location"] == "/desk"
    assert is_logged_in(client)
    assert b"Today" in client.get("/desk").data


def test_a_wrong_password_never_says_whether_the_email_exists(client, owner_id):
    wrong = post(client, "/login", {"email": "owner@shop.example", "password": "nope"})
    unknown = post(client, "/login", {"email": "nobody@shop.example", "password": "nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert b"Wrong email or password." in wrong.data and b"Wrong email or password." in unknown.data


def test_five_wrong_passwords_lock_the_account_for_15_minutes(app, client, owner_id):
    for _ in range(5):
        post(client, "/login", {"email": "owner@shop.example", "password": "nope"})
    response = post(client, "/login", {"email": "owner@shop.example", "password": PASSWORD})
    assert b"Too many failed attempts" in response.data
    assert get(app, User, owner_id).locked_until > utcnow() + timedelta(minutes=14)


def test_login_only_goes_back_to_pages_on_this_site(client, owner_id):
    response = post(client, "/login", {"email": "owner@shop.example", "password": PASSWORD,
                                       "next": "//evil.example/steal"})
    assert response.headers["Location"] == "/desk"


def test_a_switched_off_login_is_logged_out_at_once(app, owner, mechanic_id):
    browser = log_in_as(app.test_client(), mechanic_id)
    assert is_logged_in(browser)
    post(owner, f"/settings/staff/{mechanic_id}", {"action": "active"})
    assert not get(app, User, mechanic_id).is_active
    assert not is_logged_in(browser)
    assert post(app.test_client(), "/login", {"email": "jun@shop.example", "password": PASSWORD}).status_code == 401


def test_every_form_needs_the_csrf_token(client, owner_id):
    response = client.post("/login", data={"email": "owner@shop.example", "password": PASSWORD})
    assert response.status_code == 400 and b"This form has expired" in response.data


def test_logging_out(owner):
    response = post(owner, "/logout")
    assert response.headers["Location"] == "/login"
    assert not is_logged_in(owner)


# ---------- Roles ----------

def test_staff_pages_need_a_login(client, shop_id):
    response = client.get("/desk")
    assert response.status_code == 302 and response.headers["Location"].startswith("/login?next=/desk")


def test_a_mechanic_sees_the_desk_but_not_the_shop_setup(mechanic):
    assert mechanic.get("/desk").status_code == 200
    assert mechanic.get("/settings").status_code == 200
    for page in ("/settings/hours", "/settings/bays", "/settings/services", "/settings/staff", "/settings/shop",
                 "/settings/closures"):
        assert mechanic.get(page).status_code == 403, page
    assert post(mechanic, "/settings/hours", {}).status_code == 403


def test_an_advisor_cant_change_the_setup_either(app, shop_id):
    advisor = log_in_as(app.test_client(), make_user(app, shop_id, "mika@shop.example", ADVISOR))
    assert advisor.get("/desk").status_code == 200
    assert advisor.get("/settings/services").status_code == 403


def test_a_customer_cant_open_the_desk(app, shop_id):
    customer = log_in_as(app.test_client(), make_user(app, shop_id, "carlo@mail.example", CUSTOMER))
    assert customer.get("/desk").status_code == 403
    page = customer.get("/settings").get_data(as_text=True)
    assert "Your Details" in page and "The Shop" not in page
    assert 'class="sidebar"' not in page  # customers get the shop's own header


# ---------- Invite links ----------

def make_invite(app, shop_id, role=MECHANIC, moment=None):
    with app.app_context():
        s = db.session()
        invite, token = auth.make_invite(s, s.get(Shop, shop_id), "Paolo Lim", role, None, moment or utcnow())
        s.commit()
        return invite.id, token


def join_form(**changes):
    return {"name": "Paolo Lim", "email": "paolo@shop.example", "password": "new password 1",
            "confirm_password": "new password 1", **changes}


def test_an_invite_link_makes_a_staff_login_once(app, client, shop_id):
    invite_id, token = make_invite(app, shop_id)
    page = client.get(f"/join/{token}")
    assert page.status_code == 200 and b"Mechanic" in page.data
    response = post(client, f"/join/{token}", join_form())
    assert response.status_code == 302 and response.headers["Location"] == "/desk"
    [paolo] = find(app, User, User.email == "paolo@shop.example")
    assert paolo.role == MECHANIC and paolo.shop_id == shop_id
    assert get(app, Invite, invite_id).used_at is not None
    # Used up: the link can't make a second login.
    assert app.test_client().get(f"/join/{token}").status_code == 404


def test_an_old_or_made_up_invite_link_doesnt_work(app, client, shop_id):
    _, token = make_invite(app, shop_id, moment=utcnow() - timedelta(days=8))
    assert client.get(f"/join/{token}").status_code == 404
    assert client.get("/join/made-up").status_code == 404


def test_only_the_token_hash_is_stored(app, shop_id):
    invite_id, token = make_invite(app, shop_id)
    assert token not in get(app, Invite, invite_id).token_hash


def test_joining_checks_the_form(app, client, shop_id, owner_id):
    _, token = make_invite(app, shop_id)
    response = post(client, f"/join/{token}", join_form(email="owner@shop.example", password="short",
                                                         confirm_password="short"))
    assert response.status_code == 400
    assert b"Use at least 8 characters." in response.data
    response = post(client, f"/join/{token}", join_form(email="owner@shop.example"))
    assert b"already uses this email" in response.data


# ---------- Command line ----------

def test_create_owner_makes_the_shop_with_a_starter_setup(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["create-owner", "Me@Shop.example", "--name", "Hiro Navarro"],
                           input=f"{PASSWORD}\n{PASSWORD}\n")
    assert result.exit_code == 0, result.output
    [shop] = find(app, Shop, Shop.is_demo.is_(False))
    assert shop.name == "ShiroMoto Garage"
    [owner] = find(app, User, User.shop_id == shop.id)
    assert owner.email == "me@shop.example" and owner.role == "owner"
    # A second owner joins the same shop; the same email can't be made twice.
    result = runner.invoke(args=["create-owner", "me@shop.example", "--name", "Again"],
                           input=f"{PASSWORD}\n{PASSWORD}\n")
    assert result.exit_code != 0 and "already has a login" in result.output
    assert len(find(app, Shop)) == 1


def test_set_password_unlocks_and_logs_out_other_devices(app, owner, owner_id):
    result = app.test_cli_runner().invoke(args=["set-password", "owner@shop.example"],
                                          input="another pass\nanother pass\n")
    assert result.exit_code == 0, result.output
    assert not is_logged_in(owner)
    assert post(app.test_client(), "/login", {"email": "owner@shop.example",
                                              "password": "another pass"}).status_code == 302


def test_the_home_page_explains_setup_before_there_is_a_shop(client):
    response = client.get("/")
    assert response.status_code == 503 and b"create-owner" in response.data

