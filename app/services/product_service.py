"""Product logic: image files on disk + product rows in MySQL."""
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.config import APP_DIR
from app.models import Product, User
from app.schemas import ProductFilters, ProductForm, SortOrder

logger = logging.getLogger("mini_olx")

PRODUCT_UPLOAD_DIR = APP_DIR / "uploads" / "products"
MAX_IMAGE_BYTES = 2 * 1024 * 1024          # 2 MB
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class InvalidImageError(Exception):
    pass


# ---------- images ----------

def _detect_image_type(data: bytes) -> str | None:
    """Identify the file from its first bytes ("magic numbers").

    The browser's filename and Content-Type are just labels the client
    chose; anyone can upload virus.exe renamed to cat.jpg. The bytes
    themselves are what the file really is.
    """
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def save_product_image(upload: UploadFile) -> str:
    """Validate an uploaded image, store it, and return the new filename."""
    if Path(upload.filename or "").suffix.lower() not in ALLOWED_EXTENSIONS:
        raise InvalidImageError("Upload a JPG, PNG or WEBP image.")

    # Read at most one byte past the limit: enough to know it's too big
    # without loading a huge file into memory.
    data = upload.file.read(MAX_IMAGE_BYTES + 1)
    if not data:
        raise InvalidImageError("The image file is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise InvalidImageError("Image must be 2 MB or smaller.")

    extension = _detect_image_type(data)
    if extension is None:
        raise InvalidImageError("That file isn't a valid JPG, PNG or WEBP image.")

    # A random name we control: no path tricks like "../../main.py",
    # no clashes when two sellers both upload "photo.jpg".
    filename = f"{uuid4().hex}{extension}"
    PRODUCT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (PRODUCT_UPLOAD_DIR / filename).write_bytes(data)
    return filename


def delete_product_image(filename: str) -> None:
    try:
        (PRODUCT_UPLOAD_DIR / filename).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete image file %s", filename)


# ---------- queries ----------

MAX_SEARCH_WORDS = 5


def search_products(db: Session, filters: ProductFilters, limit: int = 60) -> list[Product]:
    """In-stock products matching the filters.

    The query is built step by step: each filter that is set adds one more
    WHERE condition. SQLAlchemy turns every value into a bound parameter, so
    whatever a user types is treated as data, never as SQL.
    """
    stmt = (
        select(Product)
        .where(Product.quantity > 0)
        .options(joinedload(Product.seller))   # fetch sellers in the same query
    )

    if filters.search:
        # Every word must appear in the name or the description:
        # "mechanical keyboard" matches "Keyboard (mechanical, used)".
        for word in filters.search.split()[:MAX_SEARCH_WORDS]:
            # autoescape=True makes % and _ literal characters, not LIKE wildcards.
            stmt = stmt.where(or_(
                Product.name.contains(word, autoescape=True),
                Product.description.contains(word, autoescape=True),
            ))
    if filters.category:
        stmt = stmt.where(Product.category == filters.category.value)
    if filters.condition:
        stmt = stmt.where(Product.condition == filters.condition.value)
    if filters.min_price is not None:
        stmt = stmt.where(Product.price >= filters.min_price)
    if filters.max_price is not None:
        stmt = stmt.where(Product.price <= filters.max_price)

    if filters.sort == SortOrder.price_asc:
        stmt = stmt.order_by(Product.price.asc(), Product.id.desc())
    elif filters.sort == SortOrder.price_desc:
        stmt = stmt.order_by(Product.price.desc(), Product.id.desc())
    else:
        stmt = stmt.order_by(Product.created_at.desc(), Product.id.desc())

    return list(db.scalars(stmt.limit(limit)))


def list_available(db: Session, limit: int = 60) -> list[Product]:
    """In-stock products for the marketplace, newest first."""
    return search_products(db, ProductFilters(), limit)


def list_by_seller(db: Session, seller_id: int) -> list[Product]:
    stmt = (
        select(Product)
        .where(Product.seller_id == seller_id)
        .order_by(Product.created_at.desc(), Product.id.desc())
    )
    return list(db.scalars(stmt))


def get_product(db: Session, product_id: int) -> Product | None:
    stmt = select(Product).where(Product.id == product_id).options(joinedload(Product.seller))
    return db.scalar(stmt)


# ---------- changes ----------

def _apply_form(product: Product, data: ProductForm) -> None:
    product.name = data.name
    product.description = data.description
    product.price = data.price
    product.category = data.category.value
    product.condition = data.condition.value
    product.quantity = data.quantity


def create_product(db: Session, seller: User, data: ProductForm, image_filename: str) -> Product:
    product = Product(seller_id=seller.id, image_filename=image_filename)
    _apply_form(product, data)
    db.add(product)
    try:
        db.commit()
    except Exception:
        db.rollback()
        delete_product_image(image_filename)   # don't leave an orphaned file
        raise
    db.refresh(product)   # load created_at/updated_at set by MySQL
    return product


def update_product(db: Session, product: Product, data: ProductForm, new_image: str | None) -> Product:
    old_image = product.image_filename
    _apply_form(product, data)
    if new_image:
        product.image_filename = new_image
    try:
        db.commit()
    except Exception:
        db.rollback()
        if new_image:
            delete_product_image(new_image)
        raise
    # Only remove the old file once the database points at the new one.
    if new_image and new_image != old_image:
        delete_product_image(old_image)
    db.refresh(product)
    return product


def delete_product(db: Session, product: Product) -> None:
    filename = product.image_filename
    db.delete(product)
    db.commit()
    delete_product_image(filename)


# ---------- small real-data helpers for the pages ----------

def market_stats(db: Session) -> dict:
    """Numbers for the homepage card: what's live right now."""
    listings, sellers, newest = db.execute(
        select(func.count(Product.id), func.count(func.distinct(Product.seller_id)), func.max(Product.created_at))
        .where(Product.quantity > 0)
    ).one()
    return {"listings": listings, "sellers": sellers, "newest": newest}


def more_from_seller(db: Session, seller_id: int, exclude_id: int, limit: int = 3) -> list[Product]:
    stmt = (
        select(Product)
        .where(Product.seller_id == seller_id, Product.id != exclude_id, Product.quantity > 0)
        .order_by(Product.created_at.desc(), Product.id.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt))


def count_live_by_seller(db: Session, seller_id: int) -> int:
    return db.scalar(
        select(func.count(Product.id)).where(Product.seller_id == seller_id, Product.quantity > 0)
    ) or 0
