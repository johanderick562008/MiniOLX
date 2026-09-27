"""Sign in with Google (OAuth 2.0 authorization code flow + OpenID Connect).

The whole dance, and why each step exists:

  1. /auth/google/login
     We send the browser to Google with our client_id and three random
     values, which we also keep in a short-lived signed cookie:
       state          proves the answer that comes back belongs to THIS
                      browser's request (stops login CSRF)
       code_verifier  PKCE: only its hash (code_challenge) goes to Google;
                      a stolen "code" is useless without the original
       nonce          goes INTO the ID token, so an old token can't be replayed

  2. The user signs in on Google's own page. Mini OLX never sees their
     Google password.

  3. /auth/google/callback?code=...&state=...
     We check state, then exchange the one-time code for tokens by calling
     Google directly (server to server, with our client secret).

  4. The ID token says who the user is: sub (permanent Google id), email,
     email_verified, name. We check it's for us, fresh, and carries our nonce.

  5. Find or create the Mini OLX user, then create a normal session.
"""
import base64
import hashlib
import json
import logging
import re
import secrets
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User
from app.services.auth_service import utcnow

logger = logging.getLogger("mini_olx")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
STATE_COOKIE = "google_oauth"
STATE_MAX_AGE = 10 * 60   # the user has 10 minutes to finish signing in


class GoogleAuthError(Exception):
    """Carries a short code; the login page turns it into a friendly message."""
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


def is_configured() -> bool:
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


# ---------- step 1: build the redirect to Google ----------

def start_login() -> tuple[str, dict]:
    """Return (Google URL to redirect to, values to remember in a signed cookie)."""
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",     # identity only; no access to Gmail, Drive...
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",          # always let the user pick an account
    }
    remember = {"state": state, "nonce": nonce, "verifier": code_verifier}
    return f"{AUTH_URL}?{urlencode(params)}", remember


# ---------- steps 3-4: code -> verified identity ----------

def exchange_code(code: str, code_verifier: str) -> dict:
    """Swap the one-time code for tokens, server to server."""
    try:
        response = httpx.post(TOKEN_URL, timeout=10, data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        })
    except httpx.HTTPError as exc:
        logger.warning("Google token endpoint unreachable: %s", exc)
        raise GoogleAuthError("unreachable")
    if response.status_code != 200:
        logger.warning("Google token exchange failed: %s", response.text[:300])
        raise GoogleAuthError("exchange_failed")
    return response.json()


def read_id_token(id_token: str, expected_nonce: str) -> dict:
    """Decode the ID token and check its claims.

    An ID token is a JWT: header.payload.signature, each part base64url.
    We don't verify the signature here, and that is safe ONLY because we got
    the token straight from Google's token endpoint over HTTPS, authenticated
    with our client secret; Google's OpenID Connect docs allow exactly this.
    A token that arrives any other way (from a browser, another app) MUST
    have its signature verified against Google's public keys.
    """
    try:
        payload_part = id_token.split(".")[1]
        payload_part += "=" * (-len(payload_part) % 4)   # restore base64 padding
        claims = json.loads(base64.urlsafe_b64decode(payload_part))
    except (IndexError, ValueError):
        raise GoogleAuthError("bad_token")

    now = utcnow().timestamp()
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise GoogleAuthError("bad_token", "wrong issuer")
    if claims.get("aud") != settings.GOOGLE_CLIENT_ID:
        raise GoogleAuthError("bad_token", "token is for another app")
    if claims.get("exp", 0) < now:
        raise GoogleAuthError("bad_token", "token expired")
    if claims.get("nonce") != expected_nonce:
        raise GoogleAuthError("bad_token", "nonce mismatch")
    if not claims.get("sub"):
        raise GoogleAuthError("bad_token", "no subject")
    if not claims.get("email") or claims.get("email_verified") is not True:
        raise GoogleAuthError("email_unverified")
    return claims


# ---------- step 5: find or create the user ----------

def _unique_username(db: Session, email: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", email.split("@")[0])[:24].strip("_") or "user"
    base = base if len(base) >= 3 else f"{base}_user"
    candidate, n = base, 1
    while db.scalar(select(User.id).where(User.username == candidate)):
        n += 1
        candidate = f"{base}{n}"
    return candidate


def find_or_create_user(db: Session, claims: dict) -> User:
    sub, email = claims["sub"], claims["email"].lower()

    # 1. Signed in with Google before: match on Google's permanent id, not
    #    the email (a Google account's email can change; sub never does).
    user = db.scalar(select(User).where(User.google_sub == sub))
    if user:
        return user

    # 2. A Mini OLX account already uses this email. We do NOT link them
    #    automatically: we never checked that whoever registered that account
    #    owns the email. Otherwise an attacker could register a password
    #    account with YOUR email first, wait for you to "Sign in with Google",
    #    and then log in to your account with their password
    #    ("pre-account hijacking").
    if db.scalar(select(User.id).where(User.email == email)):
        raise GoogleAuthError("email_taken")

    # 3. New user: Google has verified the email, so create the account.
    #    No password: they sign in with Google only.
    user = User(
        name=(claims.get("name") or email.split("@")[0])[:100],
        email=email,
        username=_unique_username(db, email),
        password_hash=None,
        google_sub=sub,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def complete_login(db: Session, code: str, remembered: dict) -> User:
    tokens = exchange_code(code, remembered["verifier"])
    claims = read_id_token(tokens.get("id_token", ""), remembered["nonce"])
    return find_or_create_user(db, claims)
