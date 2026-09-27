"""Payments.

The order system never talks to a payment provider directly. It asks
get_payment_method(order.payment_method) for an object that knows how that
provider works:

  ManualUPIPayment  buyer pays by QR, types a reference, seller verifies by hand
  RazorpayPayment   Razorpay checkout; verified automatically by signature,
                    the Razorpay API and webhooks

Adding RazorpayPayment didn't require changing how orders are created.
"""
import hashlib
import hmac
import logging
from abc import ABC, abstractmethod
from decimal import Decimal
from urllib.parse import quote, urlencode

import httpx
import segno
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models import Order, OrderItem, Payment, User
from app.services.auth_service import utcnow
from app.services.seller_service import lock_own_order, sold_by

logger = logging.getLogger("mini_olx")


class PaymentError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class PaymentMethod(ABC):
    """What every payment method must provide. Each method then adds the
    steps its own flow needs (a form submission, a gateway call, ...)."""
    code: str
    label: str

    @abstractmethod
    def page_context(self, order: Order) -> dict:
        """What the payment page needs to show for this order."""


class ManualUPIPayment(PaymentMethod):
    """The buyer pays by scanning a UPI QR in their own app, then types the
    transaction reference here. The website CANNOT see whether money
    actually moved, so every payment starts as 'pending' until a person
    checks their bank/UPI app and verifies it (Phase 8)."""

    code = "upi"
    label = "UPI / GPay QR"

    @staticmethod
    def payee(order: Order) -> tuple[str, str, bool]:
        """(UPI ID, name, is_seller) the buyer should pay.

        Normally the seller's UPI ID, saved on the order when it was placed.
        Orders placed before sellers had UPI IDs fall back to the site-wide
        UPI_ID in .env."""
        if order.payee_upi_id:
            return order.payee_upi_id, order.payee_name or order.seller.name, True
        return settings.UPI_ID, settings.UPI_NAME, False

    def upi_link(self, order: Order) -> str:
        # The standard UPI deep-link format that every UPI app understands.
        upi_id, name, _ = self.payee(order)
        params = {
            "pa": upi_id,                             # payee address (the seller's UPI ID)
            "pn": name,                               # payee name
            "am": f"{order.total_amount:.2f}",        # amount, pre-filled in the app
            "cu": "INR",
            "tn": f"{order.order_number} Mini OLX",   # note, so you can match the payment
        }
        return "upi://pay?" + urlencode(params, quote_via=quote, safe="@")

    def page_context(self, order: Order) -> dict:
        link = self.upi_link(order)
        qr = segno.make(link, error="m")
        upi_id, name, is_seller = self.payee(order)
        return {
            "upi_id": upi_id,
            "upi_name": name,
            "paying_seller": is_seller,
            "upi_link": link,
            "qr_data_uri": qr.svg_data_uri(scale=6, border=2, dark="#1B2430"),
            "upi_id_is_placeholder": upi_id in ("", "example@upi"),
        }

    def submit(self, db: Session, order: Order, transaction_reference: str) -> Payment:
        """Record the buyer's "I Have Paid" claim."""
        payment = Payment(
            amount=order.total_amount,
            payment_method=self.code,
            transaction_reference=transaction_reference,
            status="pending",            # never "verified" from the buyer's click
        )
        order.payments.append(payment)
        return payment


def to_paise(amount: Decimal) -> int:
    """Razorpay counts money in paise (integers): Rs 10.50 -> 1050."""
    return int((amount * 100).to_integral_value())


class RazorpayPayment(PaymentMethod):
    """Razorpay Standard Checkout in TEST mode (no real money moves).

    1. Server creates a Razorpay order for the exact amount   (create_gateway_order)
    2. Browser opens checkout.js with that order id; buyer pays
    3. Browser sends payment_id + signature -> server checks it (verify_checkout_signature)
       and asks Razorpay's API for the payment's real status   (confirm_payment)
    4. Razorpay also calls our webhook, which works even if the
       buyer closed the tab                                    (verify_webhook_signature)
    """

    code = "razorpay"
    label = "Razorpay (card, UPI, netbanking)"
    API_URL = "https://api.razorpay.com/v1"
    MAX_AMOUNT = Decimal("500000")   # Razorpay's single-payment limit

    @staticmethod
    def is_configured() -> bool:
        return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)

    def page_context(self, order: Order) -> dict:
        return {"razorpay_ready": self.is_configured()}

    # ---- talking to Razorpay ----

    def _request(self, method: str, path: str, json: dict | None = None) -> dict:
        """One authenticated call to the Razorpay API (HTTP Basic auth with
        Key ID + Key Secret). The secret never leaves the server."""
        try:
            response = httpx.request(
                method, f"{self.API_URL}{path}", json=json, timeout=10,
                auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
            )
        except httpx.HTTPError as exc:
            logger.warning("Razorpay unreachable: %s", exc)
            raise PaymentError(502, "Couldn't reach Razorpay. Try again in a moment.")
        if response.status_code >= 400:
            try:
                description = response.json()["error"]["description"]
            except (ValueError, KeyError, TypeError):
                description = f"HTTP {response.status_code}"
            logger.warning("Razorpay API error on %s %s: %s", method, path, description)
            raise PaymentError(502, f"Razorpay error: {description}")
        return response.json()

    def create_gateway_order(self, order: Order) -> str:
        data = self._request("POST", "/orders", {
            "amount": to_paise(order.total_amount),
            "currency": "INR",
            "receipt": order.order_number,
            "notes": {"mini_olx_order_id": str(order.id)},
        })
        return data["id"]

    def confirm_payment(self, payment_id: str, gateway_order_id: str, amount_paise: int) -> dict:
        """Ask Razorpay what really happened, and capture if needed.

        'authorized' = the bank approved it, but the money isn't yours yet.
        'captured'   = the money is yours. Only then is the order paid."""
        payment = self._request("GET", f"/payments/{payment_id}")
        if payment.get("order_id") != gateway_order_id or payment.get("amount") != amount_paise:
            raise PaymentError(409, "This payment doesn't match the order.")
        if payment.get("status") == "authorized":
            payment = self._request("POST", f"/payments/{payment_id}/capture",
                                    {"amount": amount_paise, "currency": "INR"})
        if payment.get("status") != "captured":
            raise PaymentError(409, f"Payment is '{payment.get('status')}', not completed.")
        return payment

    # ---- signatures (HMAC-SHA256) ----

    @staticmethod
    def verify_checkout_signature(gateway_order_id: str, payment_id: str, signature: str) -> bool:
        # Razorpay signs "order_id|payment_id" with your Key Secret. Only Razorpay
        # and your server know that secret, so a matching signature proves the
        # browser didn't invent these values.
        expected = hmac.new(settings.RAZORPAY_KEY_SECRET.encode(),
                            f"{gateway_order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
        # Webhooks are signed over the EXACT bytes received, with the webhook
        # secret (a different secret from the Key Secret).
        if not settings.RAZORPAY_WEBHOOK_SECRET:
            return False
        expected = hmac.new(settings.RAZORPAY_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


PAYMENT_METHODS: dict[str, PaymentMethod] = {
    ManualUPIPayment.code: ManualUPIPayment(),
    RazorpayPayment.code: RazorpayPayment(),
}


def get_payment_method(code: str) -> PaymentMethod:
    return PAYMENT_METHODS[code]


def razorpay() -> RazorpayPayment:
    return PAYMENT_METHODS["razorpay"]


# ---------- queries ----------

def _order_query():
    return select(Order).options(
        selectinload(Order.items).joinedload(OrderItem.seller),
        selectinload(Order.payments),
    )


def get_order_for_payment(db: Session, buyer: User, order_id: int) -> Order:
    order = db.scalar(_order_query().where(Order.id == order_id, Order.buyer_id == buyer.id))
    if order is None:
        raise PaymentError(404, "Order not found.")
    return order


def get_payment_for_user(db: Session, user: User, payment_id: int) -> Payment:
    """The buyer who paid and the seller being paid can both see a payment."""
    payment = db.scalar(
        select(Payment)
        .join(Payment.order)
        .where(Payment.id == payment_id, or_(Order.buyer_id == user.id, sold_by(user)))
    )
    if payment is None:
        raise PaymentError(404, "Payment not found.")
    return payment


# ---------- "I Have Paid" ----------

def submit_payment(db: Session, buyer: User, order_id: int, transaction_reference: str) -> Payment:
    try:
        # Lock the order row so two quick "I Have Paid" clicks can't both
        # pass the checks below and create two pending payments.
        order = db.scalar(
            _order_query().where(Order.id == order_id, Order.buyer_id == buyer.id).with_for_update()
        )
        if order is None:
            raise PaymentError(404, "Order not found.")
        if order.status == "cancelled":
            raise PaymentError(409, "This order was cancelled.")
        if order.status != "pending_payment":
            raise PaymentError(409, "This order is already paid.")
        if order.payment_method != ManualUPIPayment.code:
            raise PaymentError(409, "This order is paid through Razorpay, not the UPI QR.")

        latest = order.latest_payment
        if latest and latest.status == "pending":
            raise PaymentError(409, "A payment for this order is already waiting for verification.")

        # One real UPI transaction can only pay for one order.
        reused = db.scalar(
            select(Payment.id).where(
                Payment.transaction_reference == transaction_reference,
                Payment.status.in_(["pending", "verified"]),
            )
        )
        if reused:
            raise PaymentError(409, "This transaction reference has already been used.")

        payment = PAYMENT_METHODS[ManualUPIPayment.code].submit(db, order, transaction_reference)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(payment)
    return payment


# ---------- seller verification (manual UPI) ----------

def _lock_for_review(db: Session, seller: User, payment_id: int) -> tuple[Payment, Order]:
    order_id = db.scalar(select(Payment.order_id).where(Payment.id == payment_id))
    order = lock_own_order(db, seller, order_id) if order_id else None
    if order is None:
        # Not found, or it's another seller's payment: same answer either way.
        raise PaymentError(404, "Payment not found.")
    payment = db.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
    if payment.status != "pending":
        raise PaymentError(409, f"This payment is already {payment.status_label.lower()}.")
    return payment, order


def verify_payment(db: Session, seller: User, payment_id: int) -> Payment:
    """The seller saw the money in their UPI app. Payment -> verified, order -> paid,
    both in one transaction."""
    try:
        payment, order = _lock_for_review(db, seller, payment_id)
        if order.status != "pending_payment":
            raise PaymentError(409, "This order is no longer awaiting payment.")
        payment.status = "verified"
        payment.verified_at = utcnow()
        order.status = "paid"
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(payment)
    return payment


def reject_payment(db: Session, seller: User, payment_id: int) -> Payment:
    """No matching money arrived. The order stays 'pending_payment', so the
    buyer can submit a corrected reference or cancel."""
    try:
        payment, _order = _lock_for_review(db, seller, payment_id)
        payment.status = "rejected"
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(payment)
    return payment


# ---------- Razorpay flow ----------

def _lock_buyer_order(db: Session, buyer: User, order_id: int) -> Order:
    order = db.scalar(
        _order_query().where(Order.id == order_id, Order.buyer_id == buyer.id).with_for_update()
    )
    if order is None:
        raise PaymentError(404, "Order not found.")
    return order


def start_razorpay_checkout(db: Session, buyer: User, order_id: int) -> dict:
    """Step 1: make sure a Razorpay order exists for this order, and return
    the options checkout.js needs. Retries reuse the same Razorpay order."""
    gateway = razorpay()
    if not gateway.is_configured():
        raise PaymentError(503, "Razorpay isn't set up. Add the test keys to .env.")
    try:
        order = _lock_buyer_order(db, buyer, order_id)
        if order.payment_method != gateway.code:
            raise PaymentError(409, "This order uses the UPI QR payment.")
        if order.status != "pending_payment":
            raise PaymentError(409, "This order doesn't need payment.")
        if not order.gateway_order_id:
            order.gateway_order_id = gateway.create_gateway_order(order)
            db.commit()
        else:
            db.rollback()   # nothing changed; release the lock
    except Exception:
        db.rollback()
        raise
    return {
        "key_id": settings.RAZORPAY_KEY_ID,            # public; the secret stays here
        "razorpay_order_id": order.gateway_order_id,
        "amount": to_paise(order.total_amount),
        "currency": "INR",
        "name": "Mini OLX (test mode)",
        "description": order.order_number,
        "prefill": {"name": order.shipping_name, "email": buyer.email, "contact": f"+91{order.phone}"},
    }


def record_razorpay_payment(db: Session, gateway_order_id: str, payment_id: str,
                            amount_paise: int) -> Payment:
    """Mark the order paid for a CONFIRMED Razorpay payment.

    Called from two places that can race each other: the browser's verify
    request and the webhook. Both lock the order row, and the second one finds
    the payment already recorded and does nothing (idempotent)."""
    try:
        order = db.scalar(
            _order_query().where(Order.gateway_order_id == gateway_order_id).with_for_update()
        )
        if order is None:
            raise PaymentError(404, "No order matches this Razorpay order.")

        for existing in order.payments:
            if existing.transaction_reference == payment_id:
                db.rollback()
                return existing                      # already recorded

        if amount_paise != to_paise(order.total_amount):
            raise PaymentError(409, "Paid amount doesn't match the order total.")
        if order.status != "pending_payment":
            # e.g. the buyer cancelled, then paid in an old tab. Real money
            # would need a refund; in test mode we log it.
            logger.warning("Razorpay payment %s for %s arrived while order is '%s': refund needed",
                           payment_id, order.order_number, order.status)
            raise PaymentError(409, "This order was cancelled or already paid.")

        payment = Payment(
            amount=order.total_amount,
            payment_method=RazorpayPayment.code,
            transaction_reference=payment_id,        # pay_...
            status="verified",                       # verified by Razorpay, not a person
            verified_at=utcnow(),
        )
        order.payments.append(payment)
        order.status = "paid"
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(payment)
    return payment


def verify_razorpay_checkout(db: Session, buyer: User, order_id: int, payment_id: str,
                             signature: str) -> Payment:
    """Step 3: the browser reports success. Trust nothing it sent until checked."""
    gateway = razorpay()
    order = db.scalar(select(Order).where(Order.id == order_id, Order.buyer_id == buyer.id))
    if order is None or not order.gateway_order_id:
        raise PaymentError(404, "Order not found.")

    # Use the Razorpay order id from OUR database, not the one the browser sent.
    if not gateway.verify_checkout_signature(order.gateway_order_id, payment_id, signature):
        logger.warning("Bad Razorpay signature for %s", order.order_number)
        raise PaymentError(400, "Payment signature is invalid.")

    amount = to_paise(order.total_amount)
    gateway.confirm_payment(payment_id, order.gateway_order_id, amount)
    return record_razorpay_payment(db, order.gateway_order_id, payment_id, amount)


def handle_razorpay_webhook(db: Session, event: dict) -> str:
    """Step 4: Razorpay tells us a payment was captured. Returns what happened,
    for logging. Never raises for business problems: Razorpay would keep
    retrying an event we will never accept."""
    if event.get("event") not in ("payment.captured", "order.paid"):
        return "ignored"
    try:
        entity = event["payload"]["payment"]["entity"]
        record_razorpay_payment(db, entity["order_id"], entity["id"], int(entity["amount"]))
        return "recorded"
    except (KeyError, TypeError, ValueError):
        logger.warning("Malformed Razorpay webhook: %s", event.get("event"))
        return "malformed"
    except PaymentError as exc:
        logger.warning("Razorpay webhook not applied: %s", exc.message)
        return "rejected"
