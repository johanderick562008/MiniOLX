"""Webhooks: requests sent by other servers, not by browsers.

There is no session cookie and no CSRF token here. Instead, every request
must carry a valid HMAC signature made with the webhook secret that only
Razorpay and this server know. Anything else is rejected.
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import payment_service

logger = logging.getLogger("mini_olx")
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/razorpay")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    # The signature covers the exact bytes Razorpay sent, so read the raw
    # body. Parsing the JSON first and re-serialising would change the bytes.
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    if not payment_service.razorpay().verify_webhook_signature(raw_body, signature):
        logger.warning("Rejected webhook with an invalid signature")
        raise HTTPException(400, "Invalid signature.")

    try:
        event = json.loads(raw_body)
    except ValueError:
        raise HTTPException(400, "Body is not JSON.")

    # Database work is blocking code; run it in a worker thread so this
    # async function doesn't freeze the server while it waits for MySQL.
    outcome = await run_in_threadpool(payment_service.handle_razorpay_webhook, db, event)
    logger.info("Razorpay webhook %s: %s", event.get("event"), outcome)

    # Always 200 once the signature is valid: otherwise Razorpay retries
    # the same event for up to 24 hours.
    return {"status": "ok", "outcome": outcome}
