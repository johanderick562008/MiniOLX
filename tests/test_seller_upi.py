"""Each seller is paid at their own UPI ID."""
from app.models import User
from tests.conftest import PNG, SHIPPING


def test_upi_id_is_validated_and_normalised(make_user):
    user = make_user("johan", upi=False)
    for bad in ("johan", "@okaxis", "jo han@okaxis", "johan@", "johan@ok axis"):
        assert user.put("/api/auth/me/upi", json={"upi_id": bad}).status_code == 422
    r = user.put("/api/auth/me/upi", json={"upi_id": "  Johan.D@OKAXIS ", "upi_name": " Johan  Derick "})
    assert r.status_code == 200
    assert (r.json()["upi_id"], r.json()["upi_name"]) == ("johan.d@okaxis", "Johan Derick")


def test_upi_settings_need_login_and_csrf(anon, make_user):
    assert anon.put("/api/auth/me/upi", json={"upi_id": "a@okaxis"}).status_code == 401
    user = make_user("johan")
    del user.headers["X-CSRF-Token"]
    assert user.put("/api/auth/me/upi", json={"upi_id": "a@okaxis"}).status_code == 403


def test_cannot_list_without_upi_id(make_user):
    seller = make_user("seller", upi=False)
    assert "Add UPI ID" in seller.get("/products/add").text
    r = seller.post("/api/products", data=dict(name="Pen", description="A good pen for writing",
                    price="10", category="other", condition="new", quantity="1"),
                    files={"image": ("a.png", PNG, "image/png")})
    assert r.status_code == 409 and "UPI ID" in r.json()["detail"]


def test_payment_page_shows_the_sellers_upi(make_user, make_product, place_order):
    seller, buyer = make_user("seller"), make_user("buyer")
    seller.put("/api/auth/me/upi", json={"upi_id": "johan.sells@okicici", "upi_name": "Johan Derick"})
    order = place_order(buyer, make_product(seller))
    html = buyer.get(f"/payment/{order['id']}").text
    assert "johan.sells@okicici" in html and "Johan Derick" in html
    assert "upi://pay?pa=johan.sells@okicici&amp;pn=Johan%20Derick" in html   # & is escaped in HTML
    assert "paying the seller, seller, directly" in html


def test_order_keeps_the_upi_id_it_was_placed_with(make_user, make_product, place_order):
    seller, buyer = make_user("seller"), make_user("buyer")
    order = place_order(buyer, make_product(seller))
    seller.put("/api/auth/me/upi", json={"upi_id": "new.account@ybl"})
    html = buyer.get(f"/payment/{order['id']}").text
    assert "seller@okaxis" in html and "new.account@ybl" not in html      # snapshot


def test_two_sellers_two_upi_ids(make_user, make_product):
    s1, s2, buyer = make_user("seller1"), make_user("seller2"), make_user("buyer")
    for product in (make_product(s1, price="10"), make_product(s2, name="Book", price="300")):
        buyer.post("/api/cart/items", json=dict(product_id=product["id"]))
    orders = buyer.post("/api/orders", json=dict(SHIPPING, expected_total="310.00")).json()["orders"]
    pages = {o["total_amount"]: buyer.get(f"/payment/{o['id']}").text for o in orders}
    assert "seller1@okaxis" in pages["10.00"] and "seller2@okaxis" in pages["300.00"]


def test_upi_checkout_refused_if_seller_has_no_upi(make_user, make_product, test_database):
    seller, buyer = make_user("seller"), make_user("buyer")
    product = make_product(seller)
    with test_database() as db:                      # e.g. a listing from before this feature
        db.query(User).filter_by(username="seller").update({"upi_id": None})
        db.commit()
    buyer.post("/api/cart/items", json=dict(product_id=product["id"]))
    assert "hasn't added a UPI ID" in buyer.get("/checkout").text
    r = buyer.post("/api/orders", json=dict(SHIPPING, expected_total="10.00"))
    assert r.status_code == 409 and "UPI ID" in r.json()["detail"]


def test_seller_sees_which_upi_to_check(make_user, make_product, place_order):
    seller, buyer = make_user("seller"), make_user("buyer")
    order = place_order(buyer, make_product(seller))
    buyer.post("/api/payments", json=dict(order_id=order["id"], transaction_reference="425612345678"))
    assert "(seller@okaxis)" in seller.get("/seller/orders").text


def test_dashboard_and_settings_pages(make_user):
    user = make_user("johan")
    assert "johan@okaxis" in user.get("/dashboard").text
    assert 'value="johan@okaxis"' in user.get("/settings/payment").text
    assert make_user("newbie", upi=False).get("/dashboard").text.count("Add your UPI ID") == 1
