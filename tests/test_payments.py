REF = "425612345678"


def pay(buyer, order, reference=REF):
    return buyer.post("/api/payments", json=dict(order_id=order["id"], transaction_reference=reference))


def test_payment_page_shows_upi_qr(make_user, make_product, place_order):
    seller, buyer = make_user("seller"), make_user("buyer")
    order = place_order(buyer, make_product(seller, price="10"))
    html = buyer.get(f"/payment/{order['id']}").text
    assert "upi://pay?pa=seller@okaxis" in html and "am=10.00" in html   # the SELLER's UPI ID
    assert "data:image/svg+xml" in html                    # the QR code
    assert "Learning/Demo Project" in html


def test_i_have_paid_creates_pending_payment_only(make_user, make_product, place_order):
    seller, buyer = make_user("seller"), make_user("buyer")
    order = place_order(buyer, make_product(seller))
    r = pay(buyer, order, "4256 1234 5678")
    assert r.status_code == 201
    assert r.json()["status"] == "pending"
    assert r.json()["transaction_reference"] == REF
    assert buyer.get(f"/api/orders/{order['id']}").json()["status"] == "pending_payment"
    html = buyer.get(f"/payment/{order['id']}").text
    assert "Pending verification" in html and "Payment successful" not in html


def test_payment_rules(make_user, make_product, place_order):
    seller, buyer, other = make_user("seller"), make_user("buyer"), make_user("other")
    product = make_product(seller)
    order = place_order(buyer, product)
    assert pay(buyer, order, "abc").status_code == 422               # bad format
    assert pay(other, order).status_code == 404                      # not your order
    assert pay(buyer, order).status_code == 201
    assert pay(buyer, order, "999912345678").status_code == 409      # already pending
    assert buyer.post(f"/api/orders/{order['id']}/cancel").status_code == 409
    second = place_order(other, product)
    assert pay(other, second).status_code == 409                     # reference reused


def test_seller_verifies_payment(make_user, make_product, place_order):
    seller, buyer = make_user("seller"), make_user("buyer")
    order = place_order(buyer, make_product(seller))
    payment = pay(buyer, order).json()
    r = seller.post(f"/api/payments/{payment['id']}/verify")
    assert r.status_code == 200
    assert r.json()["status"] == "verified" and r.json()["verified_at"] is not None
    order_now = buyer.get(f"/api/orders/{order['id']}").json()
    assert order_now["status"] == "paid" and order_now["payment_status_label"] == "Verified"
    assert seller.post(f"/api/payments/{payment['id']}/verify").status_code == 409


def test_reject_then_resubmit(make_user, make_product, place_order):
    seller, buyer = make_user("seller"), make_user("buyer")
    order = place_order(buyer, make_product(seller))
    payment = pay(buyer, order).json()
    assert seller.post(f"/api/payments/{payment['id']}/reject").json()["status"] == "rejected"
    assert buyer.get(f"/api/orders/{order['id']}").json()["status"] == "pending_payment"
    assert pay(buyer, order).status_code == 201                      # same ref allowed after rejection


def test_only_the_orders_seller_can_verify(make_user, make_product, place_order):
    seller, other_seller, buyer = make_user("seller"), make_user("seller2"), make_user("buyer")
    order = place_order(buyer, make_product(seller))
    payment = pay(buyer, order).json()
    assert other_seller.post(f"/api/payments/{payment['id']}/verify").status_code == 404
    assert other_seller.post(f"/api/payments/{payment['id']}/reject").status_code == 404
    assert buyer.post(f"/api/payments/{payment['id']}/verify").status_code == 404
    assert other_seller.get("/api/seller/orders").json() == []


def test_seller_status_flow(make_user, make_product, place_order):
    seller, buyer = make_user("seller"), make_user("buyer")
    order = place_order(buyer, make_product(seller))
    url = f"/api/seller/orders/{order['id']}/status"
    assert seller.post(url, json={"status": "processing"}).status_code == 409   # not paid yet
    seller.post(f"/api/payments/{pay(buyer, order).json()['id']}/verify")
    assert seller.post(url, json={"status": "completed"}).status_code == 409    # can't skip a step
    assert seller.post(url, json={"status": "processing"}).json()["status"] == "processing"
    assert seller.post(url, json={"status": "completed"}).json()["status"] == "completed"


def test_address_hidden_until_paid(make_user, make_product, place_order):
    seller, buyer = make_user("seller"), make_user("buyer")
    order = place_order(buyer, make_product(seller))
    payment = pay(buyer, order).json()
    assert "Anna Salai" not in seller.get("/seller/orders").text
    seller.post(f"/api/payments/{payment['id']}/verify")
    assert "Anna Salai" in seller.get("/seller/orders?show=all").text
