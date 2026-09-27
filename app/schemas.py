"""Pydantic schemas: the shape of data coming in (requests) and going out (responses).

FastAPI validates every request body against these before your route code
runs. Invalid data never reaches the database; the client gets a 422 error.
"""
import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,30}$")


class RegisterRequest(BaseModel):
    name: str = Field(max_length=100)
    email: EmailStr
    username: str
    password: str
    confirm_password: str

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())  # trim and collapse inner spaces
        if len(value) < 2:
            raise ValueError("Enter your full name.")
        return value

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str) -> str:
        if len(value) > 255:
            raise ValueError("Email is too long.")
        return value.lower()

    @field_validator("username")
    @classmethod
    def check_username(cls, value: str) -> str:
        value = value.strip()
        if not USERNAME_RE.fullmatch(value):
            raise ValueError("Use 3-30 letters, numbers or underscores.")
        return value

    @field_validator("password")
    @classmethod
    def check_password(cls, value: str) -> str:
        # Passwords are never stripped: spaces are allowed characters.
        if len(value) < 8:
            raise ValueError("Use at least 8 characters.")
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Use at most 72 characters.")  # bcrypt's input limit
        if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
            raise ValueError("Include at least one letter and one number.")
        return value

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=255)  # username or email
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    """What the API returns about a user. Note: no password_hash."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    username: str
    created_at: datetime
    upi_id: str | None = None
    upi_name: str | None = None


# ---------------------------------------------------------------
# Phase 3: Products
# ---------------------------------------------------------------
from decimal import Decimal  # noqa: E402
from enum import Enum  # noqa: E402


class Category(str, Enum):
    electronics = "electronics"
    books = "books"
    accessories = "accessories"
    clothing = "clothing"
    other = "other"


class Condition(str, Enum):
    new = "new"
    like_new = "like_new"
    good = "good"
    used = "used"


# How each stored value is shown to people.
CATEGORY_LABELS = {
    "electronics": "Electronics",
    "books": "Books",
    "accessories": "Accessories",
    "clothing": "Clothing",
    "other": "Other",
}
CONDITION_LABELS = {"new": "New", "like_new": "Like New", "good": "Good", "used": "Used"}


class ProductForm(BaseModel):
    """Fields of the Add Product form (sent as multipart form data, not JSON,
    because it travels together with an image file)."""
    name: str = Field(max_length=120)
    description: str = Field(max_length=2000)
    price: Decimal = Field(gt=0, le=Decimal("1000000"), max_digits=10, decimal_places=2)
    category: Category
    condition: Condition
    quantity: int = Field(ge=1, le=999)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 3:
            raise ValueError("Use at least 3 characters.")
        return value

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 10:
            raise ValueError("Describe the item in at least 10 characters.")
        return value


class ProductUpdateForm(ProductForm):
    """Editing allows quantity 0, which marks the item as sold out."""
    quantity: int = Field(ge=0, le=999)


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    seller_id: int
    seller_username: str
    name: str
    description: str
    price: Decimal
    category: str
    category_label: str
    condition: str
    condition_label: str
    quantity: int
    image_url: str
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------
# Phase 4: Search and filters
# ---------------------------------------------------------------

class SortOrder(str, Enum):
    newest = "newest"
    price_asc = "price_asc"
    price_desc = "price_desc"


SORT_LABELS = {"newest": "Newest first", "price_asc": "Price: low to high", "price_desc": "Price: high to low"}


class ProductFilters(BaseModel):
    """Query-string filters, e.g. /api/products?search=keyboard&max_price=3000

    Every field is optional; a missing or blank field means "don't filter on this".
    """
    search: str | None = Field(None, max_length=100)
    category: Category | None = None
    condition: Condition | None = None
    min_price: Decimal | None = Field(None, ge=0, le=Decimal("1000000"))
    max_price: Decimal | None = Field(None, ge=0, le=Decimal("1000000"))
    sort: SortOrder | None = None

    @field_validator("*", mode="before")
    @classmethod
    def blank_means_none(cls, value):
        # An empty search box or "All categories" arrives as "".
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("search")
    @classmethod
    def clean_search(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value else None

    @model_validator(mode="after")
    def check_price_range(self):
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise ValueError("Minimum price can't be more than the maximum price.")
        return self

    @property
    def is_active(self) -> bool:
        return any([self.search, self.category, self.condition,
                    self.min_price is not None, self.max_price is not None])


# ---------------------------------------------------------------
# Phase 5: Shopping cart
# ---------------------------------------------------------------

MAX_CART_QUANTITY = 99


class AddToCartRequest(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(1, ge=1, le=MAX_CART_QUANTITY)
    # Buy Now: make sure the item is in the cart, but don't add more on top
    # if it's already there.
    buy_now: bool = False


class UpdateCartItemRequest(BaseModel):
    # ge=1 rejects 0 and negative numbers with a 422 before any code runs.
    # To take an item out, the client calls DELETE instead.
    quantity: int = Field(ge=1, le=MAX_CART_QUANTITY)


class CartItemOut(BaseModel):
    id: int
    product_id: int
    seller_username: str
    seller_accepts_upi: bool
    name: str
    image_url: str
    unit_price: Decimal        # the product's CURRENT price
    quantity: int
    available: int             # the product's current stock
    line_total: Decimal
    problem: str | None        # e.g. "Only 1 left" -- blocks checkout


class CartOut(BaseModel):
    items: list[CartItemOut]
    item_count: int            # total units, shown on the header badge
    subtotal: Decimal
    total: Decimal
    can_checkout: bool


# ---------------------------------------------------------------
# Phase 6: Checkout and orders
# ---------------------------------------------------------------
from typing import Literal  # noqa: E402

INDIAN_STATES = [
    "Andaman and Nicobar Islands", "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar",
    "Chandigarh", "Chhattisgarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Goa",
    "Gujarat", "Haryana", "Himachal Pradesh", "Jammu and Kashmir", "Jharkhand", "Karnataka",
    "Kerala", "Ladakh", "Lakshadweep", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya",
    "Mizoram", "Nagaland", "Odisha", "Puducherry", "Punjab", "Rajasthan", "Sikkim",
    "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
]


def _collapse(value: str) -> str:
    return " ".join(value.split())


class CheckoutRequest(BaseModel):
    shipping_name: str = Field(max_length=100)
    phone: str = Field(max_length=20)
    address: str = Field(max_length=255)
    city: str = Field(max_length=100)
    state: str
    pincode: str
    payment_method: Literal["upi", "razorpay"] = "upi"
    # The total the buyer saw on the checkout page. If prices changed since
    # then, the order is refused instead of charging a different amount.
    expected_total: Decimal = Field(gt=0)

    @field_validator("shipping_name", "address", "city")
    @classmethod
    def required_text(cls, value: str, info) -> str:
        value = _collapse(value)
        minimum = 5 if info.field_name == "address" else 2
        if len(value) < minimum:
            raise ValueError(f"Enter at least {minimum} characters.")
        return value

    @field_validator("phone")
    @classmethod
    def indian_mobile(cls, value: str) -> str:
        digits = re.sub(r"[\s-]", "", value)
        if digits.startswith("+91"):
            digits = digits[3:]
        elif len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        if not re.fullmatch(r"[6-9]\d{9}", digits):
            raise ValueError("Enter a 10-digit Indian mobile number.")
        return digits

    @field_validator("state")
    @classmethod
    def known_state(cls, value: str) -> str:
        if value not in INDIAN_STATES:
            raise ValueError("Choose a state from the list.")
        return value

    @field_validator("pincode")
    @classmethod
    def six_digit_pincode(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[1-9]\d{5}", value):
            raise ValueError("Enter a 6-digit pincode.")
        return value


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int | None
    product_name: str
    quantity: int
    price_at_purchase: Decimal
    line_total: Decimal


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: str
    status: str
    status_label: str
    payment_status_label: str
    total_amount: Decimal
    payment_method: str
    shipping_name: str
    phone: str
    address: str
    city: str
    state: str
    pincode: str
    created_at: datetime
    items: list[OrderItemOut]


class PlaceOrderResponse(BaseModel):
    orders: list[OrderOut]


# ---------------------------------------------------------------
# Phase 7: Payments
# ---------------------------------------------------------------

class PaymentSubmitRequest(BaseModel):
    order_id: int = Field(gt=0)
    transaction_reference: str

    @field_validator("transaction_reference")
    @classmethod
    def clean_reference(cls, value: str) -> str:
        value = re.sub(r"\s", "", value).upper()
        if not re.fullmatch(r"[A-Z0-9]{8,35}", value):
            raise ValueError("Enter the transaction ID / UTR from your UPI app (8-35 letters or numbers).")
        return value


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    order_number: str
    amount: Decimal
    payment_method: str
    transaction_reference: str
    status: str
    status_label: str
    created_at: datetime
    verified_at: datetime | None


# ---------------------------------------------------------------
# Phase 8: Seller order management
# ---------------------------------------------------------------

# What a seller may move an order to, from each status.
SELLER_STATUS_FLOW = {"paid": "processing", "processing": "completed"}


class OrderStatusUpdate(BaseModel):
    status: Literal["processing", "completed"]


class SellerOrderOut(OrderOut):
    buyer_username: str
    latest_payment: PaymentOut | None


# ---------------------------------------------------------------
# Upgrade: Razorpay (test mode)
# ---------------------------------------------------------------

class RazorpayOrderRequest(BaseModel):
    order_id: int = Field(gt=0)


class RazorpayCheckoutOptions(BaseModel):
    """Everything checkout.js needs to open the payment window.
    Contains the public Key ID only, never the Key Secret."""
    key_id: str
    razorpay_order_id: str
    amount: int            # paise
    currency: str
    name: str
    description: str
    prefill: dict[str, str]


class RazorpayVerifyRequest(BaseModel):
    """What checkout.js hands the browser after a successful payment.
    Untrusted until the signature is checked on the server."""
    order_id: int = Field(gt=0)
    razorpay_payment_id: str = Field(pattern=r"^pay_[A-Za-z0-9]{6,30}$")
    razorpay_order_id: str = Field(max_length=40)
    razorpay_signature: str = Field(pattern=r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------
# Seller UPI details
# ---------------------------------------------------------------

# A UPI ID (VPA) looks like  name@bank : letters, digits, dot, dash or
# underscore before the @, and a bank/app handle after it.
UPI_ID_RE = re.compile(r"^[a-z0-9._-]{2,60}@[a-z][a-z0-9]{1,30}$")


class UpiSettingsRequest(BaseModel):
    upi_id: str
    upi_name: str = Field("", max_length=100)

    @field_validator("upi_id")
    @classmethod
    def valid_upi_id(cls, value: str) -> str:
        value = value.strip().lower()
        if not UPI_ID_RE.fullmatch(value):
            raise ValueError("Enter a UPI ID like yourname@okaxis or 9876543210@ybl.")
        return value

    @field_validator("upi_name")
    @classmethod
    def clean_upi_name(cls, value: str) -> str:
        return " ".join(value.split())
