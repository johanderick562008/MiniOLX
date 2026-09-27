"""Shared test setup.

Tests run against an in-memory SQLite database, created fresh for every
test, so they never touch your real MySQL data. SQLite doesn't support
row locks (FOR UPDATE), so those are tested by reasoning, not here.
"""
import os
import re

# Must be set BEFORE importing the app, which reads settings at import time.
# (python-dotenv never overrides variables that are already set.)
os.environ.update(DB_USER="test", DB_PASSWORD="test", DB_NAME="test",
                  SECRET_KEY="test-secret-key", UPI_ID="test@upi", UPI_NAME="Test Shop")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import product_service  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64       # smallest thing our validator accepts
PASSWORD = "secret123"
SHIPPING = dict(shipping_name="Test Buyer", phone="9876543210", address="12 Anna Salai, T Nagar",
                city="Chennai", state="Tamil Nadu", pincode="600017")


@pytest.fixture(autouse=True)
def test_database(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")   # SQLite ignores FKs unless asked

    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(product_service, "PRODUCT_UPLOAD_DIR", tmp_path)   # uploads go to a temp folder
    yield TestSession
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def anon():
    return TestClient(app)


@pytest.fixture
def make_user():
    """make_user("alice") -> a TestClient logged in as alice, with her CSRF header set."""
    def _make(username: str, upi: bool = True) -> TestClient:
        client = TestClient(app)
        r = client.post("/api/auth/register", json=dict(
            name=f"User {username}", email=f"{username}@example.com", username=username,
            password=PASSWORD, confirm_password=PASSWORD))
        assert r.status_code == 201, r.json()
        r = client.post("/api/auth/login", json={"identifier": username, "password": PASSWORD})
        assert r.status_code == 200, r.json()
        token = re.search(r'name="csrf-token" content="(\w+)"', client.get("/dashboard").text).group(1)
        client.headers["X-CSRF-Token"] = token
        if upi:   # sellers need a UPI ID before they can list items
            r = client.put("/api/auth/me/upi", json={"upi_id": f"{username}@okaxis"})
            assert r.status_code == 200, r.json()
        return client
    return _make


@pytest.fixture
def make_product():
    def _make(seller: TestClient, name="Hauser Pen", price="10", quantity=5, category="other",
              condition="new") -> dict:
        r = seller.post("/api/products", data=dict(
            name=name, description="A good item for testing", price=price,
            category=category, condition=condition, quantity=str(quantity)),
            files={"image": ("photo.png", PNG, "image/png")})
        assert r.status_code == 201, r.json()
        return r.json()
    return _make


@pytest.fixture
def place_order():
    """Add a product to the buyer's cart and check out. Returns the order JSON."""
    def _place(buyer: TestClient, product: dict, quantity: int = 1) -> dict:
        r = buyer.post("/api/cart/items", json=dict(product_id=product["id"], quantity=quantity))
        assert r.status_code == 200, r.json()
        total = buyer.get("/api/cart").json()["total"]
        r = buyer.post("/api/orders", json=dict(SHIPPING, expected_total=total))
        assert r.status_code == 201, r.json()
        return r.json()["orders"][0]
    return _place
