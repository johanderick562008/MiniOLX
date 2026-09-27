import re

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import PASSWORD


def register(client, **overrides):
    data = dict(name="Johan D", email="johan@example.com", username="johan",password=PASSWORD, confirm_password=PASSWORD)
    data.update(overrides)
    return client.post("/api/auth/register", json=data)


def test_register_stores_a_hash_not_the_password(anon, test_database):
    assert register(anon).status_code == 201
    from app.models import User
    with test_database() as db:
        user = db.query(User).one()
    assert user.password_hash != PASSWORD
    assert user.password_hash.startswith("$2b$")


def test_register_rejects_duplicates(anon):
    register(anon)
    assert register(anon, email="other@example.com").status_code == 409   # same username
    assert register(anon, username="other").status_code == 409            # same email


def test_register_validates_input(anon):
    r = register(anon, email="not-an-email", username="a b", password="short", confirm_password="short")
    assert r.status_code == 422
    fields = {e["loc"][-1] for e in r.json()["detail"]}
    assert {"email", "username", "password"} <= fields


def test_register_rejects_mismatched_passwords(anon):
    r = register(anon, confirm_password="different123")
    assert r.status_code == 422
    assert "do not match" in r.json()["detail"][0]["msg"]


def test_validation_errors_never_echo_the_password(anon):
    r = register(anon, confirm_password="different123")
    assert PASSWORD not in r.text


def test_login_with_username_or_email(anon):
    register(anon)
    assert anon.post("/api/auth/login", json={"identifier": "johan", "password": PASSWORD}).status_code == 200
    assert anon.post("/api/auth/login", json={"identifier": "JOHAN@example.com", "password": PASSWORD}).status_code == 200


def test_login_failure_gives_one_generic_message(anon):
    register(anon)
    wrong_password = anon.post("/api/auth/login", json={"identifier": "johan", "password": "wrong1234"})
    no_such_user = anon.post("/api/auth/login", json={"identifier": "ghost", "password": "wrong1234"})
    assert wrong_password.status_code == no_such_user.status_code == 401
    assert wrong_password.json() == no_such_user.json()


def test_session_cookie_is_httponly(anon):
    register(anon)
    r = anon.post("/api/auth/login", json={"identifier": "johan", "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_me_requires_login(anon, make_user):
    assert anon.get("/api/auth/me").status_code == 401
    assert make_user("alice").get("/api/auth/me").json()["username"] == "alice"


def test_logout_needs_csrf_and_ends_the_session(make_user):
    alice = make_user("alice")
    without_token = TestClient(app, cookies=alice.cookies)
    assert without_token.post("/api/auth/logout").status_code == 403
    assert alice.post("/api/auth/logout").status_code == 204
    assert alice.get("/api/auth/me").status_code == 401


def test_dashboard_redirects_when_logged_out(anon):
    r = anon.get("/dashboard", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_pages_escape_user_input(make_user, anon):
    client = TestClient(app)
    client.post("/api/auth/register", json=dict(
        name="<script>alert(1)</script>", email="x@example.com", username="xss_user",
        password=PASSWORD, confirm_password=PASSWORD))
    client.post("/api/auth/login", json={"identifier": "xss_user", "password": PASSWORD})
    html = client.get("/dashboard").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert re.search(r'name="csrf-token"', html)


def test_site_icons_are_linked_and_served(anon):
    html = anon.get("/").text
    assert 'rel="icon"' in html and "icons/favicon.svg" in html and 'rel="apple-touch-icon"' in html
    r = anon.get("/favicon.ico")
    assert r.status_code == 200 and r.headers["content-type"] == "image/x-icon"
    assert anon.get("/static/icons/favicon.svg").status_code == 200
    assert anon.get("/static/site.webmanifest").json()["theme_color"] == "#0f5e4c"
