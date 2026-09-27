"""The seller's side of orders: which sales need attention, and moving an
order along once it's paid. Every query is limited to orders that contain
the logged-in seller's items."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import Order, OrderItem, Payment, User
from app.schemas import SELLER_STATUS_FLOW


class SellerError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def sold_by(seller: User):
    """SQL condition: 'this order contains items sold by <seller>'.
    Becomes WHERE EXISTS (SELECT 1 FROM order_items WHERE order_id = orders.id AND seller_id = ?)."""
    return Order.items.any(OrderItem.seller_id == seller.id)


def awaiting_verification():
    """SQL condition: the order has a payment with status 'pending'."""
    return Order.payments.any(Payment.status == "pending")


def list_orders(db: Session, seller: User, needs_verification: bool = False) -> list[Order]:
    stmt = (
        select(Order)
        .where(sold_by(seller))
        .options(
            joinedload(Order.buyer),
            selectinload(Order.items).joinedload(OrderItem.seller),
            selectinload(Order.payments),
        )
        .order_by(Order.created_at.desc(), Order.id.desc())
    )
    if needs_verification:
        stmt = stmt.where(awaiting_verification())
    return list(db.scalars(stmt))


def pending_count(db: Session, seller: User) -> int:
    """Payments waiting for this seller, for the header badge."""
    return db.scalar(
        select(func.count()).select_from(Order).where(sold_by(seller), awaiting_verification())
    ) or 0


def lock_own_order(db: Session, seller: User, order_id: int) -> Order | None:
    """Lock the order row (SELECT ... FOR UPDATE) if it belongs to this seller.

    The order row is the one lock every payment action takes: submitting,
    verifying, rejecting and cancelling all lock it first, so they can never
    run on the same order at the same time."""
    return db.scalar(
        select(Order).where(Order.id == order_id, sold_by(seller)).with_for_update()
    )


def update_order_status(db: Session, seller: User, order_id: int, new_status: str) -> Order:
    try:
        order = lock_own_order(db, seller, order_id)
        if order is None:
            raise SellerError(404, "Order not found.")
        if SELLER_STATUS_FLOW.get(order.status) != new_status:
            raise SellerError(409, f"An order that is '{order.status_label}' can't be marked as {new_status}.")
        order.status = new_status
        db.commit()
    except Exception:
        db.rollback()
        raise
    return order
