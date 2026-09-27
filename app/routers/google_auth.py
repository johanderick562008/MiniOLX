"""Browser routes for Sign in with Google. These are full-page redirects
(not fetch calls), because the user has to visit Google's own sign-in page."""
import hmac
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.services import auth_service, google_auth_service
from app.services.auth_service import SESSION_COOKIE
from app.services.google_auth_service import STATE_COOKIE, STATE_MAX_AGE, GoogleAuthError

logger = logging.getLogger("mini_olx")
router = APIRouter(prefix="/auth/google", include_in_schema=False)


def _back_to_login(error_code: str) -> RedirectResponse:
    # Only a short code goes in the URL; the page maps it to fixed text,
    # so nothing an attacker puts in the URL is ever shown to the user.
    response = RedirectResponse(f"/login?error={error_code}", status_code=303)
    response.delete_cookie(STATE_COOKIE, path="/auth/google")
    return response


@router.get("/login")
def google_login():
    if not google_auth_service.is_configured():
        return _back_to_login("google_unavailable")
    google_url, remember = google_auth_service.start_login()
    response = RedirectResponse(google_url, status_code=303)
    response.set_cookie(
        STATE_COOKIE, auth_service.sign_data(remember),
        max_age=STATE_MAX_AGE, httponly=True, samesite="lax",
        secure=settings.COOKIE_SECURE, path="/auth/google",   # only sent to these routes
    )
    return response


@router.get("/callback")
def google_callback(request: Request, db: Session = Depends(get_db)):
    params = request.query_params
    if params.get("error"):                       # e.g. the user clicked Cancel on Google
        return _back_to_login("google_cancelled")

    remembered = auth_service.unsign_data(request.cookies.get(STATE_COOKIE, ""), STATE_MAX_AGE)
    if remembered is None:
        return _back_to_login("google_expired")   # cookie missing, edited or too old
    if not hmac.compare_digest(params.get("state", ""), remembered["state"]):
        logger.warning("Google login: state mismatch")
        return _back_to_login("google_failed")
    if not params.get("code"):
        return _back_to_login("google_failed")

    try:
        user = google_auth_service.complete_login(db, params["code"], remembered)
    except GoogleAuthError as exc:
        logger.warning("Google login refused: %s", exc)
        return _back_to_login(exc.code if exc.code in ("email_taken", "email_unverified") else "google_failed")

    # From here it's exactly like a password login: fresh session, cookie.
    old_token = request.cookies.get(SESSION_COOKIE)
    if old_token:
        auth_service.delete_session(db, old_token)
    token = auth_service.create_session(db, user)
    response = RedirectResponse("/dashboard", status_code=303)
    auth_service.set_session_cookie(response, token)
    response.delete_cookie(STATE_COOKIE, path="/auth/google")
    return response
