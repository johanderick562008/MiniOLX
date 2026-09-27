"""Cart logic. Every rule is checked here on the server; the buttons in the
browser are only a convenience and can be bypassed."""
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models import CartItem, Product, User
from app.schemas import MAX_CART_QUANTITY, CartItemOut, CartOut


class CartError(Exception):
    """A rule was broken. Carries the HTTP status the router should send."""
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _check_can_buy(product: Product, user: User, quantity: int) -> None:
    if product.seller_id == user.id:
        raise CartError(403, "You can't buy your own product.")
    if product.quantity <= 0:
        raise CartError(409, "This item is sold out.")
    if quantity > product.quantity:
        raise CartError(409, f"Only {product.quantity} available.")
    if quantity > MAX_CART_QUANTITY:
        raise CartError(409, f"You can add at most {MAX_CART_QUANTITY} of one item.")


def _problem(item: CartItem) -> str | None:
    """Stock can change after something was added (another buyer, or the
    seller edits the listing), so every cart view re-checks it."""
    available = item.product.quantity
    if available <= 0:
        return "Sold out. Remove it to continue."
    if item.quantity > available:
        return f"Only {available} left. Lower the quantity to continue."
    return None


def _user_items(db: Session, user: User) -> list[CartItem]:
    stmt = (
        select(CartItem)
        .where(CartItem.user_id == user.id)
        .options(joinedload(CartItem.product).joinedload(Product.seller))
        .order_by(CartItem.created_at, CartItem.id)
    )
    return list(db.scalars(stmt))


def _get_own_item(db: Session, user: User, item_id: int) -> CartItem:
    # Filtering by user_id means another user's cart row simply isn't found:
    # User A can never read, change or delete User B's cart.
    item = db.scalar(
        select(CartItem)
        .where(CartItem.id == item_id, CartItem.user_id == user.id)
        .options(joinedload(CartItem.product))
    )
    if item is None:
        raise CartError(404, "Cart item not found.")
    return item


def build_cart(db: Session, user: User) -> CartOut:
    items_out = []
    for item in _user_items(db, user):
        price = item.product.price
        items_out.append(CartItemOut(
            id=item.id,
            product_id=item.product_id,
            seller_username=item.product.seller.username,
            seller_accepts_upi=item.product.seller.accepts_upi,
            name=item.product.name,
            image_url=item.product.image_url,
            unit_price=price,
            quantity=item.quantity,
            available=item.product.quantity,
            line_total=price * item.quantity,
            problem=_problem(item),
        ))
    subtotal = sum((i.line_total for i in items_out), Decimal("0"))
    return CartOut(
        items=items_out,
        item_count=sum(i.quantity for i in items_out),
        subtotal=subtotal,
        total=subtotal,   # no delivery fee or tax in version 1
        can_checkout=bool(items_out) and all(i.problem is None for i in items_out),
    )


def cart_count(user: User) -> int:
    """Units in the cart, for the header badge."""
    return sum(item.quantity for item in user.cart_items)


def add_item(db: Session, user: User, product_id: int, quantity: int, buy_now: bool = False) -> None:
    product = db.get(Product, product_id)
    if product is None:
        raise CartError(404, "Product not found.")

    existing = db.scalar(
        select(CartItem).where(CartItem.user_id == user.id, CartItem.product_id == product_id)
    )
    if existing and buy_now:
        _check_can_buy(product, user, existing.quantity)
        return   # already in the cart: Buy Now just goes to the cart

    new_quantity = quantity + (existing.quantity if existing else 0)
    _check_can_buy(product, user, new_quantity)

    if existing:
        existing.quantity = new_quantity
    else:
        db.add(CartItem(user_id=user.id, product_id=product_id, quantity=quantity))
    try:
        db.commit()
    except IntegrityError:
        # Two tabs added the same product at the same instant; the UNIQUE
        # key rejected the second row.
        db.rollback()
        raise CartError(409, "Your cart changed in another tab. Try again.")


def update_item(db: Session, user: User, item_id: int, quantity: int) -> None:
    item = _get_own_item(db, user, item_id)
    _check_can_buy(item.product, user, quantity)
    item.quantity = quantity
    db.commit()


def remove_item(db: Session, user: User, item_id: int) -> None:
    item = _get_own_item(db, user, item_id)
    db.delete(item)
    db.commit()
