"""Turning a cart into orders, safely.

Placing an order changes several tables at once: new orders and
order_items rows, lower product stock, an emptied cart. All of it happens
in ONE transaction, so either everything is saved or nothing is.
"""
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import CartItem, Order, OrderItem, Product, User
from app.schemas import CheckoutRequest


class OrderError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def place_orders(db: Session, buyer: User, data: CheckoutRequest) -> list[Order]:
    try:
        orders = _place_orders(db, buyer, data)
        db.commit()   # everything below becomes permanent at once
    except Exception:
        db.rollback() # ...or none of it does
        raise
    return orders


def _check_payment_method(data: CheckoutRequest) -> None:
    from app.services.payment_service import razorpay
    if data.payment_method == "razorpay" and not razorpay().is_configured():
        raise OrderError(400, "Razorpay isn't available. Choose UPI / GPay QR.")


def _place_orders(db: Session, buyer: User, data: CheckoutRequest) -> list[Order]:
    _check_payment_method(data)

    # 1. Lock this buyer's cart rows. If the same checkout is submitted
    #    twice (double click, two tabs), the second request waits here,
    #    then finds an empty cart instead of creating a duplicate order.
    cart_items = list(db.scalars(
        select(CartItem)
        .where(CartItem.user_id == buyer.id)
        .order_by(CartItem.id)
        .with_for_update()
    ))
    if not cart_items:
        raise OrderError(400, "Your cart is empty.")

    # 2. Lock the products being bought (SELECT ... FOR UPDATE). Until we
    #    commit, any other checkout for these products waits, so two buyers
    #    can never both take the last unit. Locking in id order means two
    #    checkouts always lock rows in the same order, avoiding deadlocks.
    product_ids = sorted({item.product_id for item in cart_items})
    products = {
        p.id: p for p in db.scalars(
            select(Product).where(Product.id.in_(product_ids)).order_by(Product.id).with_for_update()
        )
    }

    # 3. Re-check everything against the locked, current data.
    total = Decimal("0")
    for item in cart_items:
        product = products.get(item.product_id)
        if product is None:
            raise OrderError(409, "An item in your cart is no longer available. Review your cart.")
        if product.seller_id == buyer.id:
            raise OrderError(403, "You can't buy your own product.")
        if product.quantity < item.quantity:
            raise OrderError(409, f"Only {product.quantity} of \"{product.name}\" left. Update your cart.")
        total += product.price * item.quantity

    if total != data.expected_total:
        raise OrderError(409, "Prices in your cart changed. Check the new total and place the order again.")

    # 4. One order per seller.
    by_seller: dict[int, list[CartItem]] = defaultdict(list)
    for item in cart_items:
        by_seller[products[item.product_id].seller_id].append(item)

    if data.payment_method == "razorpay":
        from app.services.payment_service import RazorpayPayment
        for items in by_seller.values():
            if sum(products[i.product_id].price * i.quantity for i in items) > RazorpayPayment.MAX_AMOUNT:
                raise OrderError(400, "Razorpay allows at most ₹5,00,000 per payment. Choose UPI / GPay QR.")

    sellers = {s.id: s for s in db.scalars(select(User).where(User.id.in_(list(by_seller))))}
    if data.payment_method == "upi":
        for seller in sellers.values():
            if not seller.accepts_upi:
                raise OrderError(409, f"{seller.username} hasn't added a UPI ID yet, so their items "
                                      "can't be paid by UPI QR. Choose another payment method.")

    orders = []
    for seller_id, items in by_seller.items():
        seller = sellers[seller_id]
        order = Order(
            buyer_id=buyer.id,
            total_amount=sum((products[i.product_id].price * i.quantity for i in items), Decimal("0")),
            shipping_name=data.shipping_name,
            phone=data.phone,
            address=data.address,
            city=data.city,
            state=data.state,
            pincode=data.pincode,
            payment_method=data.payment_method,
            status="pending_payment",
            # Snapshot: the buyer pays the UPI ID shown now, even if the
            # seller changes theirs later.
            payee_upi_id=seller.upi_id if data.payment_method == "upi" else None,
            payee_name=seller.upi_display_name if data.payment_method == "upi" else None,
        )
        for item in items:
            product = products[item.product_id]
            order.items.append(OrderItem(
                product_id=product.id,
                seller_id=seller_id,
                product_name=product.name,          # snapshot
                quantity=item.quantity,
                price_at_purchase=product.price,    # snapshot
            ))
            product.quantity -= item.quantity       # 5. reserve the stock
        db.add(order)
        orders.append(order)

    # 6. Empty the cart.
    for item in cart_items:
        db.delete(item)

    db.flush()   # sends the INSERTs now so the new order ids are known
    return orders


def _order_query():
    return select(Order).options(
        selectinload(Order.items).joinedload(OrderItem.seller),
        selectinload(Order.items).joinedload(OrderItem.product),
        selectinload(Order.payments),
    )


def list_buyer_orders(db: Session, buyer: User) -> list[Order]:
    stmt = _order_query().where(Order.buyer_id == buyer.id).order_by(Order.created_at.desc(), Order.id.desc())
    return list(db.scalars(stmt))


def get_buyer_order(db: Session, buyer: User, order_id: int) -> Order:
    # Filtering by buyer_id: someone else's order is simply "not found".
    order = db.scalar(_order_query().where(Order.id == order_id, Order.buyer_id == buyer.id))
    if order is None:
        raise OrderError(404, "Order not found.")
    return order


def cancel_order(db: Session, buyer: User, order_id: int) -> Order:
    """Buyer cancels an unpaid order: the reserved stock goes back on sale."""
    try:
        order = db.scalar(
            select(Order).where(Order.id == order_id, Order.buyer_id == buyer.id).with_for_update()
        )
        if order is None:
            raise OrderError(404, "Order not found.")
        if order.status != "pending_payment":
            raise OrderError(409, "Only orders awaiting payment can be cancelled.")
        if order.latest_payment and order.latest_payment.status == "pending":
            raise OrderError(409, "Your payment is being verified, so this order can't be cancelled now.")

        product_ids = sorted({i.product_id for i in order.items if i.product_id is not None})
        products = {
            p.id: p for p in db.scalars(
                select(Product).where(Product.id.in_(product_ids)).order_by(Product.id).with_for_update()
            )
        }
        for item in order.items:
            if item.product_id in products:      # skip listings that were deleted
                products[item.product_id].quantity += item.quantity
        order.status = "cancelled"
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_buyer_order(db, buyer, order_id)


def last_shipping_details(db: Session, buyer: User) -> Order | None:
    """The buyer's most recent order, used to pre-fill the checkout form."""
    return db.scalar(
        select(Order).where(Order.buyer_id == buyer.id).order_by(Order.id.desc()).limit(1)
    )
