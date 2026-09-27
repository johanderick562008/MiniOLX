"""HTML pages rendered with Jinja2. Data changes go through the JSON API."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session, object_session

from app.database import get_db
from app.dependencies import get_current_session
from app.models import UserSession
from app.schemas import INDIAN_STATES, SORT_LABELS, ProductFilters
from app.services import (cart_service, google_auth_service, order_service, payment_service,
                          product_service, seller_service)
from app.services.order_service import OrderError
from app.templating import templates

router = APIRouter(include_in_schema=False)


def _context(session: UserSession | None, **extra) -> dict:
    return {
        "user": session.user if session else None,
        "csrf_token": session.csrf_token if session else "",
        "cart_count": cart_service.cart_count(session.user) if session else 0,
        "sales_pending": seller_service.pending_count(object_session(session), session.user) if session else 0,
        **extra,
    }


def _error_page(request: Request, session, status_code: int, title: str, message: str):
    return templates.TemplateResponse(
        request, "error.html",
        _context(session, title=title, message=message),
        status_code=status_code,
    )


def _to_login():
    return RedirectResponse("/login", status_code=303)


def _parse_filters(request: Request) -> tuple[ProductFilters, str | None]:
    """Pages show a friendly message for bad filters instead of a 422."""
    try:
        return ProductFilters(**request.query_params), None
    except ValidationError as exc:
        message = exc.errors()[0]["msg"].replace("Value error, ", "")
        return ProductFilters(), f"Filters ignored: {message}"


@router.get("/", response_class=HTMLResponse)
def home(request: Request, session: UserSession | None = Depends(get_current_session),
         db: Session = Depends(get_db)):
    filters, filter_error = _parse_filters(request)
    products = product_service.search_products(db, filters)
    return templates.TemplateResponse(request, "index.html", _context(
        session, products=products, filters=filters, filter_error=filter_error,
        sort_labels=SORT_LABELS, stats=product_service.market_stats(db),
    ))


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, session: UserSession | None = Depends(get_current_session)):
    if session:
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(request, "register.html", _context(
        session, google_enabled=google_auth_service.is_configured()))


# Fixed messages for ?error=<code>. The URL can only pick one of these,
# never supply its own text.
LOGIN_ERRORS = {
    "google_unavailable": "Sign in with Google isn't set up on this server yet.",
    "google_cancelled": "Google sign-in was cancelled.",
    "google_expired": "That sign-in took too long or started in another browser. Please try again.",
    "google_failed": "We couldn't sign you in with Google. Please try again.",
    "email_taken": ("An account with this email already exists. Log in with your username "
                    "and password instead."),
    "email_unverified": "Your Google account's email isn't verified, so we can't use it.",
}


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, session: UserSession | None = Depends(get_current_session)):
    if session:
        return RedirectResponse("/dashboard", status_code=303)
    registered = request.query_params.get("registered") == "1"
    error = LOGIN_ERRORS.get(request.query_params.get("error", ""))
    return templates.TemplateResponse(request, "login.html", _context(
        session, registered=registered, error=error,
        google_enabled=google_auth_service.is_configured()))


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, session: UserSession | None = Depends(get_current_session),
              db: Session = Depends(get_db)):
    if session is None:
        return _to_login()
    my_products = product_service.list_by_seller(db, session.user_id)
    recent_orders = order_service.list_buyer_orders(db, session.user)[:3]
    return templates.TemplateResponse(request, "dashboard.html", _context(
        session, my_products=my_products, recent_orders=recent_orders))


@router.get("/product/{product_id}", response_class=HTMLResponse)
def product_page(product_id: int, request: Request,
                 session: UserSession | None = Depends(get_current_session),
                 db: Session = Depends(get_db)):
    product = product_service.get_product(db, product_id)
    if product is None:
        return _error_page(request, session, 404, "Product not found",
                           "This listing doesn't exist or was removed.")
    is_owner = session is not None and product.seller_id == session.user_id
    return templates.TemplateResponse(request, "product.html", _context(
        session, product=product, is_owner=is_owner,
        more_from_seller=product_service.more_from_seller(db, product.seller_id, product.id),
        seller_live_count=product_service.count_live_by_seller(db, product.seller_id)))


@router.get("/products/add", response_class=HTMLResponse)
def add_product_page(request: Request, session: UserSession | None = Depends(get_current_session)):
    if session is None:
        return _to_login()
    return templates.TemplateResponse(request, "add_product.html", _context(session))


@router.get("/products/{product_id}/edit", response_class=HTMLResponse)
def edit_product_page(product_id: int, request: Request,
                      session: UserSession | None = Depends(get_current_session),
                      db: Session = Depends(get_db)):
    if session is None:
        return _to_login()
    product = product_service.get_product(db, product_id)
    if product is None:
        return _error_page(request, session, 404, "Product not found",
                           "This listing doesn't exist or was removed.")
    if product.seller_id != session.user_id:
        return _error_page(request, session, 403, "Not your listing",
                           "You can only edit products you listed.")
    return templates.TemplateResponse(request, "edit_product.html", _context(session, product=product))


@router.get("/cart", response_class=HTMLResponse)
def cart_page(request: Request, session: UserSession | None = Depends(get_current_session),
              db: Session = Depends(get_db)):
    if session is None:
        return _to_login()
    cart = cart_service.build_cart(db, session.user)
    return templates.TemplateResponse(request, "cart.html", _context(session, cart=cart))


@router.get("/checkout", response_class=HTMLResponse)
def checkout_page(request: Request, session: UserSession | None = Depends(get_current_session),
                  db: Session = Depends(get_db)):
    if session is None:
        return _to_login()
    cart = cart_service.build_cart(db, session.user)
    if not cart.items:
        return RedirectResponse("/cart", status_code=303)
    previous = order_service.last_shipping_details(db, session.user)
    return templates.TemplateResponse(request, "checkout.html", _context(
        session, cart=cart, previous=previous, states=INDIAN_STATES,
        razorpay_available=payment_service.razorpay().is_configured()))


@router.get("/orders", response_class=HTMLResponse)
def orders_page(request: Request, session: UserSession | None = Depends(get_current_session),
                db: Session = Depends(get_db)):
    if session is None:
        return _to_login()
    orders = order_service.list_buyer_orders(db, session.user)
    placed = request.query_params.get("placed")
    return templates.TemplateResponse(request, "orders.html", _context(
        session, orders=orders, placed=placed))


@router.get("/orders/{order_id}", response_class=HTMLResponse)
def order_details_page(order_id: int, request: Request,
                       session: UserSession | None = Depends(get_current_session),
                       db: Session = Depends(get_db)):
    if session is None:
        return _to_login()
    try:
        order = order_service.get_buyer_order(db, session.user, order_id)
    except OrderError:
        return _error_page(request, session, 404, "Order not found",
                           "This order doesn't exist or isn't yours.")
    placed = request.query_params.get("placed") == "1"
    return templates.TemplateResponse(request, "order_details.html", _context(
        session, order=order, placed=placed))


@router.get("/payment/{order_id}", response_class=HTMLResponse)
def payment_page(order_id: int, request: Request,
                 session: UserSession | None = Depends(get_current_session),
                 db: Session = Depends(get_db)):
    if session is None:
        return _to_login()
    try:
        order = payment_service.get_order_for_payment(db, session.user, order_id)
    except payment_service.PaymentError:
        return _error_page(request, session, 404, "Order not found",
                           "This order doesn't exist or isn't yours.")
    method = payment_service.get_payment_method(order.payment_method)
    return templates.TemplateResponse(request, "payment.html", _context(
        session, order=order, payment=order.latest_payment, method=method,
        **method.page_context(order)))


@router.get("/seller/orders", response_class=HTMLResponse)
def seller_orders_page(request: Request, session: UserSession | None = Depends(get_current_session),
                       db: Session = Depends(get_db)):
    if session is None:
        return _to_login()
    show = request.query_params.get("show", "pending")
    orders = seller_service.list_orders(db, session.user, needs_verification=(show == "pending"))
    return templates.TemplateResponse(request, "seller_orders.html", _context(
        session, orders=orders, show=show))


@router.get("/settings/payment", response_class=HTMLResponse)
def payment_settings_page(request: Request, session: UserSession | None = Depends(get_current_session)):
    if session is None:
        return _to_login()
    return templates.TemplateResponse(request, "payment_settings.html", _context(
        session, next_url="/products/add" if request.query_params.get("next") == "sell" else ""))
