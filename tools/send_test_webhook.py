"""Send a signed, fake 'payment.captured' webhook to your local server.

Real webhooks need a public HTTPS URL, which localhost isn't. This script
lets you watch the webhook path work without that:

  1. Place a Razorpay order, click "Pay with Razorpay", then CLOSE the
     Razorpay window without paying (this creates the Razorpay order).
  2. With uvicorn running, from the project folder:
         python tools/send_test_webhook.py ORD-1005
  3. Refresh the order: it's Paid, even though the browser never reported
     anything. That's what webhooks are for (a buyer who closed the tab).

Try it with a wrong secret (--bad-signature) to see the server reject it.
"""
import argparse
import hashlib
import hmac
import json
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Order  # noqa: E402
from app.services.payment_service import to_paise  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("order_number", help="e.g. ORD-1005")
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/webhooks/razorpay")
    parser.add_argument("--bad-signature", action="store_true", help="sign with the wrong secret")
    args = parser.parse_args()

    if not settings.RAZORPAY_WEBHOOK_SECRET:
        print("Set RAZORPAY_WEBHOOK_SECRET in .env first.")
        return 1

    order_id = int(args.order_number.upper().removeprefix("ORD-")) - 1000
    with SessionLocal() as db:
        order = db.scalar(select(Order).where(Order.id == order_id))
    if order is None or not order.gateway_order_id:
        print("Order not found, or it has no Razorpay order yet (click 'Pay with Razorpay' once).")
        return 1

    event = {
        "entity": "event",
        "event": "payment.captured",
        "contains": ["payment"],
        "created_at": int(time.time()),
        "payload": {"payment": {"entity": {
            "id": f"pay_TEST{secrets.token_hex(6).upper()}",
            "entity": "payment",
            "amount": to_paise(order.total_amount),
            "currency": "INR",
            "status": "captured",
            "order_id": order.gateway_order_id,
        }}},
    }
    body = json.dumps(event).encode()
    secret = "wrong-secret" if args.bad_signature else settings.RAZORPAY_WEBHOOK_SECRET
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    response = httpx.post(args.url, content=body, timeout=10, headers={
        "Content-Type": "application/json", "X-Razorpay-Signature": signature})
    print(response.status_code, response.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
