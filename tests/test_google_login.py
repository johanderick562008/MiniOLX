"""Sign in with Google. Google itself is faked: we only test OUR side of the
flow (state, nonce, token checks, account matching), so it runs offline."""
import base64
import dataclasses
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import google_auth_service

CLIENT_ID = "test-client.apps.googleusercontent.com"


def make_id_token(**claims) -> str:
    """Build a JWT-shaped string: header.payload.signature (base64url)."""
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'RS256'})}.{part(claims)}.fake-signature"


@pytest.fixture
def google(monkeypatch):
    configured = dataclasses.replace(google_auth_service.settings, GOOGLE_CLIENT_ID=CLIENT_ID,GOOGLE_CLIENT_SECRET="test-secret")
    monkeypatch.setattr(google_auth_service, "settings", configured)
    fake = {"claims": {}, "codes": []}

    def fake_exchange(code, code_verifier):
        fake["codes"].append((code, code_verifier))
        return {"id_token": make_id_token(**fake["claims"])}

    monkeypatch.setattr(google_auth_service, "exchange_code", fake_exchange)
    return fake


def google_sign_in(client, fake, *, sub="google-sub-1", email="johan@gmail.com",email_verified=True, state=None, **overrides):
    """Run the whole browser flow: our /login -> (Google) -> our /callback."""
    r = client.get("/auth/google/login", follow_redirects=False)
    assert r.status_code == 303
    query = parse_qs(urlparse(r.headers["location"]).query)
    fake["claims"] = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": sub,
                      "email": email, "email_verified": email_verified, "name": "Johan Derick",
                      "exp": int(time.time()) + 300, "nonce": query["nonce"][0], **overrides}
    return client.get("/auth/google/callback",
                      params={"code": "one-time-code", "state": state or query["state"][0]},
                      follow_redirects=False)


def test_button_hidden_and_route_safe_when_not_configured(anon):
    assert "Continue with Google" not in anon.get("/login").text
    r = anon.get("/auth/google/login", follow_redirects=False)
    assert r.headers["location"] == "/login?error=google_unavailable"


def test_redirect_to_google_is_well_formed(google, anon):
    assert "Continue with Google" in anon.get("/login").text
    assert "Continue with Google" in anon.get("/register").text
    r = anon.get("/auth/google/login", follow_redirects=False)
    url = urlparse(r.headers["location"])
    query = parse_qs(url.query)
    assert url.netloc == "accounts.google.com"
    assert query["scope"] == ["openid email profile"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["client_id"] == [CLIENT_ID]
    assert "test-secret" not in r.headers["location"]        # the secret never goes to the browser
    assert "google_oauth=" in r.headers["set-cookie"] and "httponly" in r.headers["set-cookie"].lower()


def test_new_google_user_is_created_and_logged_in(google, anon):
    r = google_sign_in(anon, google)
    assert r.status_code == 303 and r.headers["location"] == "/dashboard"
    me = anon.get("/api/auth/me").json()
    assert (me["username"], me["email"], me["name"]) == ("johan", "johan@gmail.com", "Johan Derick")
    assert "Sign-in</dt><dd>Google" in anon.get("/dashboard").text
    code, verifier = google["codes"][0]
    assert code == "one-time-code" and len(verifier) > 40      # PKCE verifier sent with the code


def test_returning_google_user_gets_the_same_account(google, test_database):
    first, second = TestClient(app), TestClient(app)
    google_sign_in(first, google)
    google_sign_in(second, google, email="johan.new@gmail.com")   # email changed on Google's side
    assert first.get("/api/auth/me").json()["id"] == second.get("/api/auth/me").json()["id"]
    from app.models import User
    with test_database() as db:
        assert db.query(User).count() == 1


def test_usernames_stay_unique(google):
    a, b = TestClient(app), TestClient(app)
    google_sign_in(a, google, sub="sub-a", email="johan@gmail.com")
    google_sign_in(b, google, sub="sub-b", email="johan@yahoo.com")
    assert a.get("/api/auth/me").json()["username"] == "johan"
    assert b.get("/api/auth/me").json()["username"] == "johan2"


def test_state_mismatch_is_rejected(google, anon):
    r = google_sign_in(anon, google, state="attacker-state")
    assert r.headers["location"] == "/login?error=google_failed"
    assert anon.get("/api/auth/me").status_code == 401


def test_callback_without_our_cookie_is_rejected(google, anon):
    r = anon.get("/auth/google/callback", params={"code": "x", "state": "y"}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=google_expired"


def test_tampered_cookie_is_rejected(google, anon):
    anon.get("/auth/google/login", follow_redirects=False)
    anon.cookies.set("google_oauth", "eyJzdGF0ZSI6ICJ4In0.forged", path="/auth/google")
    r = anon.get("/auth/google/callback", params={"code": "x", "state": "x"}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=google_expired"


@pytest.mark.parametrize("bad_claims", [
    {"aud": "someone-elses-app"},
    {"iss": "https://evil.example"},
    {"nonce": "replayed-nonce"},
    {"exp": 1000},
])
def test_bad_id_tokens_are_rejected(google, anon, bad_claims):
    r = google_sign_in(anon, google, **bad_claims)
    assert r.headers["location"] == "/login?error=google_failed"
    assert anon.get("/api/auth/me").status_code == 401


def test_unverified_email_is_rejected(google, anon):
    r = google_sign_in(anon, google, email_verified=False)
    assert r.headers["location"] == "/login?error=email_unverified"


def test_existing_password_account_is_not_auto_linked(google, make_user, anon):
    make_user("johan")                                  # registered with johan@example.com
    r = google_sign_in(anon, google, email="johan@example.com")
    assert r.headers["location"] == "/login?error=email_taken"
    assert "already exists" in anon.get("/login?error=email_taken").text
    assert anon.get("/api/auth/me").status_code == 401


def test_google_only_account_has_no_password_login(google, anon):
    google_sign_in(TestClient(app), google)
    for password in ("", "anything123", "None"):
        r = anon.post("/api/auth/login", json={"identifier": "johan", "password": password or "x"})
        assert r.status_code == 401


def test_user_cancelled_on_google(google, anon):
    r = anon.get("/auth/google/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=google_cancelled"


def test_error_codes_never_reflect_raw_text(anon):
    html = anon.get("/login?error=<script>alert(1)</script>").text
    assert "<script>alert(1)</script>" not in html and "alert(1)" not in html
