"""SQLAlchemy ORM models. Each class mirrors one table in schema.sql."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CHAR, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    username: Mapped[str] = mapped_column(String(30), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))   # None = Google-only
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    upi_id: Mapped[str | None] = mapped_column(String(100))
    upi_name: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def has_password(self) -> bool:
        return self.password_hash is not None

    @property
    def uses_google(self) -> bool:
        return self.google_sub is not None

    @property
    def accepts_upi(self) -> bool:
        return bool(self.upi_id)

    @property
    def upi_display_name(self) -> str:
        return self.upi_name or self.name

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    products: Mapped[list["Product"]] = relationship(
        back_populates="seller", passive_deletes=True
    )
    cart_items: Mapped[list["CartItem"]] = relationship(
        back_populates="user", passive_deletes=True
    )


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(CHAR(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    user: Mapped[User] = relationship(back_populates="sessions")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    seller_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    category: Mapped[str] = mapped_column(String(20), index=True)
    # quote=True: `condition` is a reserved word in MySQL.
    condition: Mapped[str] = mapped_column("condition", String(20), quote=True)
    quantity: Mapped[int] = mapped_column(default=1)
    image_filename: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    seller: Mapped[User] = relationship(back_populates="products")

    # Read-only helpers. Pydantic's ProductOut reads these like columns.
    @property
    def image_url(self) -> str:
        return f"/uploads/products/{self.image_filename}"

    @property
    def seller_username(self) -> str:
        return self.seller.username

    @property
    def in_stock(self) -> bool:
        return self.quantity > 0

    @property
    def category_label(self) -> str:
        from app.schemas import CATEGORY_LABELS
        return CATEGORY_LABELS.get(self.category, self.category)

    @property
    def condition_label(self) -> str:
        from app.schemas import CONDITION_LABELS
        return CONDITION_LABELS.get(self.condition, self.condition)


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("user_id", "product_id", name="uq_cart_user_product"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="cart_items")
    product: Mapped[Product] = relationship()


ORDER_STATUS_LABELS = {
    "pending_payment": "Awaiting payment",
    "paid": "Paid",
    "processing": "Processing",
    "completed": "Completed",
    "cancelled": "Cancelled",
}


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    shipping_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(15))
    address: Mapped[str] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(100))
    pincode: Mapped[str] = mapped_column(CHAR(6))
    payment_method: Mapped[str] = mapped_column(String(20), default="upi")
    gateway_order_id: Mapped[str | None] = mapped_column(String(40), unique=True)
    payee_upi_id: Mapped[str | None] = mapped_column(String(100))
    payee_name: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="pending_payment", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    buyer: Mapped[User] = relationship()
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderItem.id"
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="Payment.id"
    )

    @property
    def latest_payment(self) -> "Payment | None":
        return self.payments[-1] if self.payments else None

    @property
    def payment_status_label(self) -> str:
        latest = self.latest_payment
        if latest:
            return latest.status_label
        return "Not needed" if self.status == "cancelled" else "Not paid yet"

    @property
    def order_number(self) -> str:
        """Friendly ID shown to people: order 1 is ORD-1001."""
        return f"ORD-{1000 + self.id}"

    @property
    def status_label(self) -> str:
        return ORDER_STATUS_LABELS.get(self.status, self.status)

    @property
    def seller(self) -> User:
        return self.items[0].seller   # every item in an order has the same seller

    @property
    def buyer_username(self) -> str:
        return self.buyer.username


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), index=True
    )
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product_name: Mapped[str] = mapped_column(String(120))
    quantity: Mapped[int]
    price_at_purchase: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product | None] = relationship()
    seller: Mapped[User] = relationship()

    @property
    def line_total(self) -> Decimal:
        return self.price_at_purchase * self.quantity


PAYMENT_STATUS_LABELS = {
    "pending": "Pending verification",
    "verified": "Verified",
    "rejected": "Rejected",
}


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    payment_method: Mapped[str] = mapped_column(String(20))
    transaction_reference: Mapped[str] = mapped_column(String(35), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)

    order: Mapped[Order] = relationship(back_populates="payments")

    @property
    def status_label(self) -> str:
        return PAYMENT_STATUS_LABELS.get(self.status, self.status)

    @property
    def order_number(self) -> str:
        return self.order.order_number
