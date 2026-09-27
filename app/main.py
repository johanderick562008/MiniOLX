"""Application entry point. Run with: uvicorn app.main:app --reload"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import APP_DIR
from app.database import engine, get_db
from app.routers import auth, cart, google_auth, orders, pages, payments, products, seller, webhooks
from app.services.product_service import PRODUCT_UPLOAD_DIR

logger = logging.getLogger("mini_olx")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once at startup: warn early if MySQL is unreachable,
    # but still start so you can see the error at /health/db.
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Connected to MySQL.")
    except SQLAlchemyError as exc:
        logger.warning("Could not connect to MySQL at startup: %s", exc)
    yield
    engine.dispose()  # runs once at shutdown: close pooled connections


app = FastAPI(
    title="Mini OLX (Learning Project)",
    description="A small marketplace built to learn FastAPI + MySQL.",
    lifespan=lifespan,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # Browsers must trust our Content-Type (e.g. image/png) instead of
    # guessing from the bytes, so an uploaded file can't be run as HTML.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    # FastAPI's default 422 body echoes back everything the client sent,
    # including passwords. Return only where the problem is and what it is.
    errors = [{"loc": list(err["loc"]), "msg": err["msg"]} for err in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


PRODUCT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=APP_DIR / "uploads"), name="uploads")

app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(google_auth.router)
app.include_router(products.router)
app.include_router(cart.router)
app.include_router(orders.router)
app.include_router(payments.router)
app.include_router(seller.router)
app.include_router(webhooks.router)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    # Browsers and bookmark tools ask for /favicon.ico at the site root even
    # when the page links its icons elsewhere, so serve it from here too.
    return FileResponse(APP_DIR / "static" / "favicon.ico", media_type="image/x-icon",
                        headers={"Cache-Control": "public, max-age=604800"})


@app.get("/health/db", tags=["health"])
def database_health(db: Session = Depends(get_db)):
    try:
        mysql_version = db.execute(text("SELECT VERSION()")).scalar()
    except SQLAlchemyError:
        logger.exception("Database health check failed")
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"status": "ok", "database": "connected", "mysql_version": mysql_version}
