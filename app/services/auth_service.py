"""Authentication logic, kept separate from the HTTP layer (routers).

Routers deal with requests and responses; this module deals with users,
passwords and sessions. That split keeps each piece small and testable.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User, UserSession
from app.schemas import RegisterRequest

SESSION_COOKIE = "session"
BCRYPT_MAX_BYTES = 72

# A real bcrypt hash used when a login names an unknown user, so the response
# takes as long as a wrong password would. Otherwise response timing could
# reveal which usernames exist.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt())


class DuplicateUserError(Exception):
    def __init__(self, field: str):
        self.field = field


def utcnow() -> datetime:
    """Current UTC time without tzinfo, matching MySQL DATETIME columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------- passwords ----------

def hash_password(password: str) -> str:
    # gensalt() picks a fresh random salt; it is stored inside the hash string.
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    raw = password.encode("utf-8")
    if len(raw) > BCRYPT_MAX_BYTES:
        return False
    return bcrypt.checkpw(raw, password_hash.encode("ascii"))


# ---------- users ----------

def register_user(db: Session, data: RegisterRequest) -> User:
    if db.scalar(select(User.id).where(User.email == data.email)):
        raise DuplicateUserError("email")
    if db.scalar(select(User.id).where(User.username == data.username)):
        raise DuplicateUserError("username")

    user = User(
        name=data.name,
        email=data.email,
        username=data.username,
        password_hash=hash_password(data.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Two people registered the same name at the same moment: the
        # database's UNIQUE constraint is the final safety net.
        db.rollback()
        raise DuplicateUserError("username or email")
    db.refresh(user)
    return user


def authenticate(db: Session, identifier: str, password: str) -> User | None:
    identifier = identifier.strip()
    if "@" in identifier:
        stmt = select(User).where(User.email == identifier.lower())
    else:
        stmt = select(User).where(User.username == identifier)
    user = db.scalar(stmt)

    if user is None or user.password_hash is None:
        # Unknown user, or a Google-only account with no password: same
        # slow check and same answer, so neither case can be told apart.
        bcrypt.checkpw(b"timing-equaliser", _DUMMY_HASH)
        return None
    return user if verify_password(password, user.password_hash) else None


# ---------- sessions ----------

def _hash_token(token: str) -> str:
    # Keyed with SECRET_KEY: a leaked database alone cannot forge or use tokens.
    return hmac.new(settings.SECRET_KEY.encode(), token.encode(), hashlib.sha256).hexdigest()


def create_session(db: Session, user: User) -> str:
    """Store a new session and return the raw token for the cookie."""
    token = secrets.token_urlsafe(32)
    db.add(UserSession(
        user_id=user.id,
        token_hash=_hash_token(token),
        csrf_token=secrets.token_hex(32),
        expires_at=utcnow() + timedelta(days=settings.SESSION_DAYS),
    ))
    db.commit()
    return token


def get_valid_session(db: Session, token: str) -> UserSession | None:
    session = db.scalar(select(UserSession).where(UserSession.token_hash == _hash_token(token)))
    if session is None:
        return None
    if session.expires_at <= utcnow():
        db.delete(session)
        db.commit()
        return None
    return session


def delete_session(db: Session, token: str) -> None:
    db.execute(delete(UserSession).where(UserSession.token_hash == _hash_token(token)))
    db.commit()


# ---------- signed cookies ----------
# Small data the browser must hand back unchanged (e.g. the OAuth "state").
# The HMAC tag, keyed with SECRET_KEY, makes any edit detectable.

def sign_data(data: dict) -> str:
    import base64
    import json
    payload = base64.urlsafe_b64encode(json.dumps({**data, "iat": int(utcnow().timestamp())}).encode()).decode()
    tag = hmac.new(settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{tag}"


def unsign_data(value: str, max_age_seconds: int) -> dict | None:
    import base64
    import json
    try:
        payload, tag = value.rsplit(".", 1)
        expected = hmac.new(settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, tag):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, TypeError):
        return None
    if utcnow().timestamp() - data.get("iat", 0) > max_age_seconds:
        return None
    return data


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=settings.SESSION_DAYS * 24 * 60 * 60,
        httponly=True,              # JavaScript cannot read it (limits XSS damage)
        samesite="lax",             # not sent on cross-site POSTs
        secure=settings.COOKIE_SECURE,  # HTTPS-only when enabled
        path="/",
    )
