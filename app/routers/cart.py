"""JSON API for the shopping cart. Every route acts on the logged-in
user's own cart; there is no way to name another user's cart."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_user_csrf
from app.models import User
from app.schemas import AddToCartRequest, CartOut, UpdateCartItemRequest
from app.services import cart_service
from app.services.cart_service import CartError

router = APIRouter(prefix="/api/cart", tags=["cart"])


def _run(action, *args) -> None:
    """Turn a CartError from the service into an HTTP error response."""
    try:
        action(*args)
    except CartError as exc:
        raise HTTPException(exc.status_code, exc.message)


# Every change returns the whole updated cart, so the page can redraw
# itself from one response instead of making a second request.

@router.get("", response_model=CartOut)
def get_cart(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return cart_service.build_cart(db, user)


@router.post("/items", response_model=CartOut)
def add_to_cart(data: AddToCartRequest, user: User = Depends(require_user_csrf),
                db: Session = Depends(get_db)):
    _run(cart_service.add_item, db, user, data.product_id, data.quantity, data.buy_now)
    return cart_service.build_cart(db, user)


@router.put("/items/{item_id}", response_model=CartOut)
def update_cart_item(item_id: int, data: UpdateCartItemRequest,
                     user: User = Depends(require_user_csrf), db: Session = Depends(get_db)):
    _run(cart_service.update_item, db, user, item_id, data.quantity)
    return cart_service.build_cart(db, user)


@router.delete("/items/{item_id}", response_model=CartOut)
def remove_cart_item(item_id: int, user: User = Depends(require_user_csrf),
                     db: Session = Depends(get_db)):
    _run(cart_service.remove_item, db, user, item_id)
    return cart_service.build_cart(db, user)
