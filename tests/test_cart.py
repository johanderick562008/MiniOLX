from fastapi.testclient import TestClient

from app.main import app


def add(client, product, quantity=1):
    return client.post("/api/cart/items", json=dict(product_id=product["id"], quantity=quantity))


def test_add_increase_and_total(make_user, make_product):
    seller, buyer = make_user("seller"), make_user("buyer")
    product = make_product(seller, price="1500", quantity=3)
    add(buyer, product, 2)
    cart = add(buyer, product, 1).json()
    assert cart["item_count"] == 3
    assert len(cart["items"]) == 1               # same product -> one row, bigger quantity
    assert cart["total"] == "4500.00"


def test_cannot_exceed_stock(make_user, make_product):
    seller, buyer = make_user("seller"), make_user("buyer")
    product = make_product(seller, quantity=2)
    add(buyer, product, 2)
    r = add(buyer, product, 1)
    assert r.status_code == 409 and "Only 2 available" in r.json()["detail"]


def test_zero_and_negative_quantities_rejected(make_user, make_product):
    seller, buyer = make_user("seller"), make_user("buyer")
    product = make_product(seller)
    assert add(buyer, product, 0).status_code == 422
    assert add(buyer, product, -1).status_code == 422
    item_id = add(buyer, product).json()["items"][0]["id"]
    assert buyer.put(f"/api/cart/items/{item_id}", json={"quantity": 0}).status_code == 422


def test_cannot_buy_own_product(make_user, make_product):
    seller = make_user("seller")
    product = make_product(seller)
    r = add(seller, product)
    assert r.status_code == 403


def test_update_and_remove(make_user, make_product):
    seller, buyer = make_user("seller"), make_user("buyer")
    product = make_product(seller, price="100", quantity=5)
    item_id = add(buyer, product).json()["items"][0]["id"]
    assert buyer.put(f"/api/cart/items/{item_id}", json={"quantity": 4}).json()["total"] == "400.00"
    assert buyer.delete(f"/api/cart/items/{item_id}").json()["items"] == []


def test_other_users_cannot_touch_my_cart(make_user, make_product):
    seller, buyer, other = make_user("seller"), make_user("buyer"), make_user("other")
    product = make_product(seller)
    item_id = add(buyer, product).json()["items"][0]["id"]
    assert other.put(f"/api/cart/items/{item_id}", json={"quantity": 2}).status_code == 404
    assert other.delete(f"/api/cart/items/{item_id}").status_code == 404


def test_cart_flags_stock_changes(make_user, make_product):
    seller, buyer = make_user("seller"), make_user("buyer")
    product = make_product(seller, quantity=3)
    add(buyer, product, 3)
    seller.put(f"/api/products/{product['id']}", data=dict(
        name=product["name"], description=product["description"], price="10",
        category="other", condition="new", quantity="1"))
    cart = buyer.get("/api/cart").json()
    assert cart["items"][0]["problem"].startswith("Only 1 left")
    assert cart["can_checkout"] is False


def test_cart_requires_login_and_csrf(make_user, make_product, anon):
    seller, buyer = make_user("seller"), make_user("buyer")
    product = make_product(seller)
    assert anon.get("/api/cart").status_code == 401
    no_csrf = TestClient(app, cookies=buyer.cookies)
    assert no_csrf.post("/api/cart/items", json=dict(product_id=product["id"])).status_code == 403
