"""Reusable FastAPI dependencies for "who is making this request?".

FastAPI caches a dependency within one request, so even when several of
these are used together, the session is looked up in MySQL only once.
"""
import hmac

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, UserSession
from app.services.auth_service import SESSION_COOKIE, get_valid_session

CSRF_HEADER = "X-CSRF-Token"


def get_current_session(request: Request, db: Session = Depends(get_db)) -> UserSession | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return get_valid_session(db, token)


def get_current_user_optional(
    session: UserSession | None = Depends(get_current_session),
) -> User | None:
    """For pages that work logged in or out (e.g. the home page)."""
    return session.user if session else None


def get_current_user(user: User | None = Depends(get_current_user_optional)) -> User:
    """For API routes that require login. Responds 401 if not logged in."""
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Log in to continue.")
    return user


def require_csrf(
    request: Request,
    session: UserSession | None = Depends(get_current_session),
) -> UserSession:
    """For every logged-in request that changes data (POST/PUT/DELETE).

    The browser attaches the session cookie to requests automatically, even
    ones triggered by another website. Only our own pages know the CSRF
    token, so requiring it in a header proves the request came from us.
    """
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Log in to continue.")
    sent = request.headers.get(CSRF_HEADER, "")
    if not hmac.compare_digest(sent, session.csrf_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid or missing CSRF token. Reload the page and try again.")
    return session


def require_user_csrf(session: UserSession = Depends(require_csrf)) -> User:
    """Logged in AND carrying a valid CSRF token. Use on every route that
    creates, changes or deletes data."""
    return session.user
