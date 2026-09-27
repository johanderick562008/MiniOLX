"""Fill the database with demo users and products.

Run from the project folder (with the venv active), AFTER schema.sql:
    python seed.py

Safe to run more than once: existing users and products are skipped.
"""
import struct
import sys
import zlib
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.database import SessionLocal
from app.models import Product, User
from app.services.auth_service import hash_password
from app.services.product_service import PRODUCT_UPLOAD_DIR

DEMO_PASSWORD = "Demo12345"

USERS = [
    {"name": "Demo Seller", "email": "seller@demo.local", "username": "demo_seller",
     "upi_id": "demo.seller@upi"},   
    {"name": "Demo Buyer", "email": "buyer@demo.local", "username": "demo_buyer"},
]

# (name, description, price, category, condition, quantity, colour)
PRODUCTS = [
    ("Mechanical Keyboard", "Blue-switch mechanical keyboard, all keys working. Good condition.", "1500", "electronics", "good", 2, (52, 94, 168)),
    ("Gaming Mouse", "RGB gaming mouse with adjustable DPI. Barely used.", "800", "electronics", "like_new", 3, (178, 60, 60)),
    ("Headphones", "Over-ear wired headphones with a detachable cable.", "1200", "electronics", "used", 1, (40, 40, 48)),
    ("Scientific Calculator", "Casio-style scientific calculator, perfect for engineering exams.", "450", "electronics", "good", 4, (90, 110, 120)),
    ("Laptop Backpack", "Water-resistant backpack that fits a 15.6 inch laptop.", "900", "accessories", "like_new", 2, (30, 110, 80)),
    ("24 inch Monitor", "Full HD IPS monitor with HDMI. No dead pixels.", "6500", "electronics", "used", 1, (20, 20, 24)),
    ("USB Hub", "4-port USB 3.0 hub with a short cable.", "350", "accessories", "new", 6, (200, 200, 205)),
    ("Compact Keyboard", "Wireless compact keyboard, includes the receiver.", "700", "electronics", "good", 2, (232, 163, 23)),
    ("Programming Books (set of 3)", "Python, data structures and web development books. Light pencil notes.", "600", "books", "used", 1, (120, 70, 150)),
]


def placeholder_png(colour: tuple[int, int, int], width: int = 640, height: int = 480) -> bytes:
    """Build a simple two-tone PNG by hand, so seeding needs no image library.
    A PNG is a signature plus chunks: IHDR (size), IDAT (compressed pixels), IEND."""
    light = tuple(min(255, c + 60) for c in colour)
    rows = []
    for y in range(height):
        rgb = bytes(colour if y < height * 0.72 else light)
        rows.append(b"\x00" + rgb * width)   # 0 = no filter for this row

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)   # 8-bit RGB
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def get_or_create_user(db, data: dict) -> tuple[User, bool]:
    user = db.scalar(select(User).where(User.username == data["username"]))
    if user:
        return user, False
    user = User(**data, password_hash=hash_password(DEMO_PASSWORD))
    db.add(user)
    db.flush()
    return user, True


def main() -> int:
    PRODUCT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    written_files = []
    try:
        users = {}
        for data in USERS:
            user, created = get_or_create_user(db, data)
            users[data["username"]] = user
            print(f"{'Created' if created else 'Exists '} user  {user.username}")

        seller = users["demo_seller"]
        for name, description, price, category, condition, quantity, colour in PRODUCTS:
            exists = db.scalar(select(Product.id).where(Product.seller_id == seller.id, Product.name == name))
            if exists:
                print(f"Exists  product {name}")
                continue
            filename = f"{uuid4().hex}.png"
            (PRODUCT_UPLOAD_DIR / filename).write_bytes(placeholder_png(colour))
            written_files.append(filename)
            db.add(Product(
                seller_id=seller.id, name=name, description=description, price=Decimal(price),
                category=category, condition=condition, quantity=quantity, image_filename=filename,
            ))
            print(f"Created product {name}")

        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        for filename in written_files:            # don't leave orphaned images
            (PRODUCT_UPLOAD_DIR / filename).unlink(missing_ok=True)
        print(f"\nDatabase error: {exc.__class__.__name__}: {exc.orig if hasattr(exc, 'orig') else exc}")
        print("Is MySQL running, is .env correct, and did you run schema.sql first?")
        return 1
    finally:
        db.close()

    print(f"\nDone. Log in as demo_seller or demo_buyer with password: {DEMO_PASSWORD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
