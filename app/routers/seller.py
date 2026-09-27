"""JSON API for the seller's sales."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_user_csrf
from app.models import User
from app.schemas import OrderStatusUpdate, SellerOrderOut
from app.services import seller_service
from app.services.seller_service import SellerError

router = APIRouter(prefix="/api/seller", tags=["seller"])


@router.get("/orders", response_model=list[SellerOrderOut])
def my_sales(needs_verification: bool = False, user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """Orders containing your products. ?needs_verification=true for pending payments only."""
    return seller_service.list_orders(db, user, needs_verification)


@router.post("/orders/{order_id}/status", response_model=SellerOrderOut)
def update_status(order_id: int, data: OrderStatusUpdate, user: User = Depends(require_user_csrf),
                  db: Session = Depends(get_db)):
    try:
        seller_service.update_order_status(db, user, order_id, data.status)
    except SellerError as exc:
        raise HTTPException(exc.status_code, exc.message)
    return next(o for o in seller_service.list_orders(db, user) if o.id == order_id)
