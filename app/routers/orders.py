"""JSON API for checkout and the buyer's orders."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_user_csrf
from app.models import User
from app.schemas import CheckoutRequest, OrderOut, PlaceOrderResponse
from app.services import order_service
from app.services.order_service import OrderError

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _run(action, *args):
    try:
        return action(*args)
    except OrderError as exc:
        raise HTTPException(exc.status_code, exc.message)


@router.post("", response_model=PlaceOrderResponse, status_code=status.HTTP_201_CREATED)
def place_order(data: CheckoutRequest, user: User = Depends(require_user_csrf),
                db: Session = Depends(get_db)):
    """Checkout: turns the logged-in user's cart into one order per seller."""
    orders = _run(order_service.place_orders, db, user, data)
    return {"orders": orders}


@router.get("", response_model=list[OrderOut])
def my_orders(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return order_service.list_buyer_orders(db, user)


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _run(order_service.get_buyer_order, db, user, order_id)


@router.post("/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: int, user: User = Depends(require_user_csrf), db: Session = Depends(get_db)):
    return _run(order_service.cancel_order, db, user, order_id)
