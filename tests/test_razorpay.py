"""Razorpay tests. The Razorpay API is replaced by a fake, so these run
offline and check OUR logic: signatures, amounts, idempotency, webhooks."""
import dataclasses
import hashlib
import hmac
import json

import pytest

from app.services import payment_service
from tests.conftest import SHIPPING

KEY_SECRET = "test_key_secret"
WEBHOOK_SECRET = "test_webhook_secret"


class FakeRazorpay:
    """Stands in for api.razorpay.com. Records calls; answers like Razorpay."""
    def __init__(self):
        self.calls = []
        self.orders = {}
        self.payments = {}

    def __call__(self, method, path, json=None):
        self.calls.append((method, path))
        if method == "POST" and path == "/orders":
            order_id = f"order_TEST{len(self.orders) + 1}"
            self.orders[order_id] = json["amount"]
            return {"id": order_id, "amount": json["amount"], "status": "created"}
        if method == "GET" and path.startswith("/payments/"):
            return self.payments[path.split("/")[2]]
        if method == "POST" and path.endswith("/capture"):
            payment = self.payments[path.split("/")[2]]
            payment["status"] = "captured"
            return payment
        raise AssertionError(f"unexpected call {method} {path}")

    def pay(self, gateway_order_id, payment_id="pay_ABC123DEF456", status="captured", amount=None):
        self.payments[payment_id] = {"id": payment_id, "order_id": gateway_order_id, "status": status,
                                     "amount": amount if amount is not None else self.orders[gateway_order_id]}
        signature = hmac.new(KEY_SECRET.encode(), f"{gateway_order_id}|{payment_id}".encode(),
                             hashlib.sha256).hexdigest()
        return {"razorpay_payment_id": payment_id, "razorpay_order_id": gateway_order_id,
                "razorpay_signature": signature}


@pytest.fixture
def fake_razorpay(monkeypatch):
    configured = dataclasses.replace(payment_service.settings, RAZORPAY_KEY_ID="rzp_test_KEY",
                                     RAZORPAY_KEY_SECRET=KEY_SECRET, RAZORPAY_WEBHOOK_SECRET=WEBHOOK_SECRET)
    monkeypatch.setattr(payment_service, "settings", configured)
    fake = FakeRazorpay()
    monkeypatch.setattr(payment_service.RazorpayPayment, "_request", lambda self, *a, **k: fake(*a, **k))
    return fake


@pytest.fixture
def razorpay_order(make_user, make_product):
    """A buyer with an unpaid Razorpay order for ₹10."""
    def _make(price="10"):
        seller, buyer = make_user("seller"), make_user("buyer")
        product = make_product(seller, price=price)
        buyer.post("/api/cart/items", json=dict(product_id=product["id"]))
        total = buyer.get("/api/cart").json()["total"]
        r = buyer.post("/api/orders", json=dict(SHIPPING, expected_total=total, payment_method="razorpay"))
        assert r.status_code == 201, r.json()
        return seller, buyer, r.json()["orders"][0]
    return _make


def start(buyer, order):
    r = buyer.post("/api/payments/razorpay/order", json={"order_id": order["id"]})
    assert r.status_code == 200, r.json()
    return r.json()


def webhook(client, event, secret=WEBHOOK_SECRET):
    body = json.dumps(event).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/api/webhooks/razorpay", content=body,
                       headers={"Content-Type": "application/json", "X-Razorpay-Signature": signature})


def captured_event(gateway_order_id, amount, payment_id="pay_HOOK123456"):
    return {"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": payment_id, "order_id": gateway_order_id, "amount": amount, "status": "captured"}}}}


def test_razorpay_hidden_when_not_configured(make_user, make_product):
    seller, buyer = make_user("seller"), make_user("buyer")
    product = make_product(seller)
    buyer.post("/api/cart/items", json=dict(product_id=product["id"]))
    assert "Razorpay (test mode)" not in buyer.get("/checkout").text
    r = buyer.post("/api/orders", json=dict(SHIPPING, expected_total="10.00", payment_method="razorpay"))
    assert r.status_code == 400


def test_checkout_options_never_contain_the_secret(fake_razorpay, razorpay_order):
    _, buyer, order = razorpay_order()
    options = start(buyer, order)
    assert options["key_id"] == "rzp_test_KEY" and options["amount"] == 1000   # paise
    assert KEY_SECRET not in json.dumps(options)
    assert start(buyer, order)["razorpay_order_id"] == options["razorpay_order_id"]   # reused
    assert fake_razorpay.calls.count(("POST", "/orders")) == 1


def test_verified_checkout_marks_order_paid(fake_razorpay, razorpay_order):
    _, buyer, order = razorpay_order()
    result = fake_razorpay.pay(start(buyer, order)["razorpay_order_id"])
    r = buyer.post("/api/payments/razorpay/verify", json={"order_id": order["id"], **result})
    assert r.status_code == 200 and r.json()["status"] == "verified"
    assert buyer.get(f"/api/orders/{order['id']}").json()["status"] == "paid"


def test_forged_signature_is_rejected(fake_razorpay, razorpay_order):
    _, buyer, order = razorpay_order()
    result = fake_razorpay.pay(start(buyer, order)["razorpay_order_id"])
    result["razorpay_signature"] = "0" * 64
    r = buyer.post("/api/payments/razorpay/verify", json={"order_id": order["id"], **result})
    assert r.status_code == 400
    assert buyer.get(f"/api/orders/{order['id']}").json()["status"] == "pending_payment"


def test_signature_uses_our_order_id_not_the_browsers(fake_razorpay, razorpay_order):
    """A valid signature for some OTHER Razorpay order must not pay this one."""
    _, buyer, order = razorpay_order()
    start(buyer, order)
    fake_razorpay.orders["order_OTHER"] = 1000
    result = fake_razorpay.pay("order_OTHER")
    r = buyer.post("/api/payments/razorpay/verify", json={"order_id": order["id"], **result})
    assert r.status_code == 400


def test_authorized_payment_gets_captured(fake_razorpay, razorpay_order):
    _, buyer, order = razorpay_order()
    result = fake_razorpay.pay(start(buyer, order)["razorpay_order_id"], status="authorized")
    buyer.post("/api/payments/razorpay/verify", json={"order_id": order["id"], **result})
    assert ("POST", f"/payments/{result['razorpay_payment_id']}/capture") in fake_razorpay.calls
    assert buyer.get(f"/api/orders/{order['id']}").json()["status"] == "paid"


def test_failed_payment_does_not_pay(fake_razorpay, razorpay_order):
    _, buyer, order = razorpay_order()
    result = fake_razorpay.pay(start(buyer, order)["razorpay_order_id"], status="failed")
    r = buyer.post("/api/payments/razorpay/verify", json={"order_id": order["id"], **result})
    assert r.status_code == 409
    assert buyer.get(f"/api/orders/{order['id']}").json()["status"] == "pending_payment"


def test_webhook_marks_paid_when_tab_was_closed(fake_razorpay, razorpay_order, anon):
    _, buyer, order = razorpay_order()
    gateway_id = start(buyer, order)["razorpay_order_id"]
    r = webhook(anon, captured_event(gateway_id, 1000))
    assert r.status_code == 200 and r.json()["outcome"] == "recorded"
    assert buyer.get(f"/api/orders/{order['id']}").json()["status"] == "paid"


def test_webhook_and_browser_together_record_one_payment(fake_razorpay, razorpay_order, anon,
                                                         test_database):
    _, buyer, order = razorpay_order()
    gateway_id = start(buyer, order)["razorpay_order_id"]
    result = fake_razorpay.pay(gateway_id, payment_id="pay_SAME123456")
    buyer.post("/api/payments/razorpay/verify", json={"order_id": order["id"], **result})
    for _ in range(2):   # Razorpay may deliver the same event more than once
        webhook(anon, captured_event(gateway_id, 1000, payment_id="pay_SAME123456"))

    from app.models import Payment
    with test_database() as db:
        assert db.query(Payment).filter_by(order_id=order["id"]).count() == 1
    assert buyer.get(f"/api/orders/{order['id']}").json()["status"] == "paid"


def test_webhook_rejects_bad_signature_and_wrong_amount(fake_razorpay, razorpay_order, anon):
    _, buyer, order = razorpay_order()
    gateway_id = start(buyer, order)["razorpay_order_id"]
    assert webhook(anon, captured_event(gateway_id, 1000), secret="wrong").status_code == 400
    r = webhook(anon, captured_event(gateway_id, 1))           # ₹0.01 instead of ₹10
    assert r.status_code == 200 and r.json()["outcome"] == "rejected"
    assert buyer.get(f"/api/orders/{order['id']}").json()["status"] == "pending_payment"


def test_webhook_ignores_other_events(fake_razorpay, anon):
    r = webhook(anon, {"event": "refund.created", "payload": {}})
    assert r.json()["outcome"] == "ignored"


def test_manual_upi_form_refused_for_razorpay_orders(fake_razorpay, razorpay_order):
    _, buyer, order = razorpay_order()
    r = buyer.post("/api/payments", json=dict(order_id=order["id"], transaction_reference="425612345678"))
    assert r.status_code == 409


def test_other_buyer_cannot_start_or_verify(fake_razorpay, razorpay_order, make_user):
    _, buyer, order = razorpay_order()
    result = fake_razorpay.pay(start(buyer, order)["razorpay_order_id"])
    other = make_user("other")
    assert other.post("/api/payments/razorpay/order", json={"order_id": order["id"]}).status_code == 404
    assert other.post("/api/payments/razorpay/verify", json={"order_id": order["id"], **result}).status_code == 404


def test_payment_page_loads_razorpay_checkout(fake_razorpay, razorpay_order):
    _, buyer, order = razorpay_order()
    html = buyer.get(f"/payment/{order['id']}").text
    assert "https://checkout.razorpay.com/v1/checkout.js" in html
    assert 'id="rzp-pay-btn"' in html and "success@razorpay" in html
    assert "Scan &amp; Pay" not in html            # no UPI QR for Razorpay orders
    assert KEY_SECRET not in html
