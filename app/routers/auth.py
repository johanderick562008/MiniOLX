"""JSON API for registration, login, logout and the current user."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_csrf, require_user_csrf
from app.models import User, UserSession
from app.schemas import LoginRequest, RegisterRequest, UpiSettingsRequest, UserOut
from app.services import auth_service
from app.services.auth_service import SESSION_COOKIE, DuplicateUserError

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    try:
        return auth_service.register_user(db, data)
    except DuplicateUserError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"That {exc.field} is already registered.")


@router.post("/login", response_model=UserOut)
def login(data: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    user = auth_service.authenticate(db, data.identifier, data.password)
    if user is None:
        # Same message for "no such user" and "wrong password".
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect username/email or password.")

    # Discard any session this browser already had, then start a fresh one.
    old_token = request.cookies.get(SESSION_COOKIE)
    if old_token:
        auth_service.delete_session(db, old_token)
    token = auth_service.create_session(db, user)

    auth_service.set_session_cookie(response, token)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    session: UserSession = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    db.delete(session)   # the token stops working immediately
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.put("/me/upi", response_model=UserOut)
def update_upi(data: UpiSettingsRequest, user: User = Depends(require_user_csrf),
               db: Session = Depends(get_db)):
    """Set where buyers pay you when you sell something."""
    user.upi_id = data.upi_id
    user.upi_name = data.upi_name or None
    db.commit()
    db.refresh(user)
    return user
