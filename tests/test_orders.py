from tests.conftest import SHIPPING


def edit_product(seller, product, **changes):
    data = dict(name=product["name"], description=product["description"], price=product["price"],
                category=product["category"], condition=product["condition"],
                quantity=str(product["quantity"]))
    data.update(changes)
    return seller.put(f"/api/products/{product['id']}", data=data)


def test_order_reduces_stock_and_empties_cart(make_user, make_product, place_order):
    seller, buyer = make_user("seller"), make_user("buyer")
    product = make_product(seller, quantity=5)
    order = place_order(buyer, product, quantity=2)
    assert order["status"] == "pending_payment"
    assert order["order_number"] == f"ORD-{1000 + order['id']}"
    assert buyer.get(f"/api/products/{product['id']}").json()["quantity"] == 3
    assert buyer.get("/api/cart").json()["items"] == []


def test_price_snapshot_survives_price_change(make_user, make_product, place_order):
    seller, buyer = make_user("seller"), make_user("buyer")
    product = make_product(seller, price="1500")
    order = place_order(buyer, product)
    edit_product(seller, product, price="1800")
    item = buyer.get(f"/api/orders/{order['id']}").json()["items"][0]
    assert item["price_at_purchase"] == "1500.00"


def test_order_refused_if_prices_changed(make_user, make_product):
    seller, buyer = make_user("seller"), make_user("buyer")
    product = make_product(seller, price="100")
    buyer.post("/api/cart/items", json=dict(product_id=product["id"]))
    edit_product(seller, product, price="150")
    r = buyer.post("/api/orders", json=dict(SHIPPING, expected_total="100.00"))
    assert r.status_code == 409 and "Prices" in r.json()["detail"]


def test_empty_cart_and_double_submit(make_user, make_product, place_order):
    seller, buyer = make_user("seller"), make_user("buyer")
    product = make_product(seller)
    place_order(buyer, product)
    r = buyer.post("/api/orders", json=dict(SHIPPING, expected_total="10.00"))
    assert r.status_code == 400 and r.json()["detail"] == "Your cart is empty."


def test_checkout_validates_delivery_details(make_user, make_product):
    seller, buyer = make_user("seller"), make_user("buyer")
    product = make_product(seller)
    buyer.post("/api/cart/items", json=dict(product_id=product["id"]))
    bad = dict(SHIPPING, phone="12345", pincode="0600", state="Narnia", expected_total="10.00")
    r = buyer.post("/api/orders", json=bad)
    assert r.status_code == 422
    assert {"phone", "pincode", "state"} <= {e["loc"][-1] for e in r.json()["detail"]}


def test_one_order_per_seller(make_user, make_product):
    seller1, seller2, buyer = make_user("seller1"), make_user("seller2"), make_user("buyer")
    pen, book = make_product(seller1, price="10"), make_product(seller2, name="Book", price="300")
    for product in (pen, book):
        buyer.post("/api/cart/items", json=dict(product_id=product["id"]))
    r = buyer.post("/api/orders", json=dict(SHIPPING, expected_total="310.00"))
    assert sorted(o["total_amount"] for o in r.json()["orders"]) == ["10.00", "300.00"]


def test_orders_are_private(make_user, make_product, place_order):
    seller, buyer, other = make_user("seller"), make_user("buyer"), make_user("other")
    order = place_order(buyer, make_product(seller))
    assert other.get(f"/api/orders/{order['id']}").status_code == 404
    assert other.get(f"/orders/{order['id']}").status_code == 404
    assert other.post(f"/api/orders/{order['id']}/cancel").status_code == 404


def test_cancel_restores_stock(make_user, make_product, place_order):
    seller, buyer = make_user("seller"), make_user("buyer")
    product = make_product(seller, quantity=5)
    order = place_order(buyer, product, quantity=2)
    assert buyer.post(f"/api/orders/{order['id']}/cancel").json()["status"] == "cancelled"
    assert buyer.get(f"/api/products/{product['id']}").json()["quantity"] == 5
    assert buyer.post(f"/api/orders/{order['id']}/cancel").status_code == 409


def test_deleting_a_sold_listing_keeps_the_order(make_user, make_product, place_order):
    seller, buyer = make_user("seller"), make_user("buyer")
    product = make_product(seller, name="Hauser Pen", price="10")
    order = place_order(buyer, product)
    assert seller.delete(f"/api/products/{product['id']}").status_code == 204
    item = buyer.get(f"/api/orders/{order['id']}").json()["items"][0]
    assert item["product_id"] is None                      # ON DELETE SET NULL
    assert (item["product_name"], item["price_at_purchase"]) == ("Hauser Pen", "10.00")
