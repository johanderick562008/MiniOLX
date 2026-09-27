"""JSON API for payments."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_user_csrf
from app.models import User
from app.schemas import (PaymentOut, PaymentSubmitRequest, RazorpayCheckoutOptions,
                         RazorpayOrderRequest, RazorpayVerifyRequest)
from app.services import payment_service
from app.services.payment_service import PaymentError

router = APIRouter(prefix="/api/payments", tags=["payments"])


def _run(action, *args):
    try:
        return action(*args)
    except PaymentError as exc:
        raise HTTPException(exc.status_code, exc.message)


@router.post("", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
def submit_payment(data: PaymentSubmitRequest, user: User = Depends(require_user_csrf),
                   db: Session = Depends(get_db)):
    """The buyer's "I Have Paid". Creates a payment with status 'pending'."""
    return _run(payment_service.submit_payment, db, user, data.order_id, data.transaction_reference)


# ---------- Razorpay (test mode) ----------
# Declared BEFORE the /{payment_id} routes: FastAPI tries routes in order, and
# "/razorpay/verify" would otherwise match "/{payment_id}/verify" with
# payment_id="razorpay" (and fail with 422, since it isn't a number).

@router.post("/razorpay/order", response_model=RazorpayCheckoutOptions)
def razorpay_order(data: RazorpayOrderRequest, user: User = Depends(require_user_csrf),
                   db: Session = Depends(get_db)):
    """Create (or reuse) the Razorpay order and return checkout.js options."""
    return _run(payment_service.start_razorpay_checkout, db, user, data.order_id)


@router.post("/razorpay/verify", response_model=PaymentOut)
def razorpay_verify(data: RazorpayVerifyRequest, user: User = Depends(require_user_csrf),
                    db: Session = Depends(get_db)):
    """Check the signature from checkout.js, confirm with Razorpay, mark paid."""
    return _run(payment_service.verify_razorpay_checkout, db, user, data.order_id,
                data.razorpay_payment_id, data.razorpay_signature)


@router.get("/{payment_id}", response_model=PaymentOut)
def get_payment(payment_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _run(payment_service.get_payment_for_user, db, user, payment_id)


@router.post("/{payment_id}/verify", response_model=PaymentOut)
def verify_payment(payment_id: int, user: User = Depends(require_user_csrf),
                   db: Session = Depends(get_db)):
    """Seller only: payment -> verified, order -> paid."""
    return _run(payment_service.verify_payment, db, user, payment_id)


@router.post("/{payment_id}/reject", response_model=PaymentOut)
def reject_payment(payment_id: int, user: User = Depends(require_user_csrf),
                   db: Session = Depends(get_db)):
    """Seller only: payment -> rejected; the order still awaits payment."""
    return _run(payment_service.reject_payment, db, user, payment_id)
