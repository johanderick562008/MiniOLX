from fastapi.testclient import TestClient

from app.main import app
from app.services import product_service
from tests.conftest import PNG


def form(**overrides):
    data = dict(name="Mechanical Keyboard", description="Good condition keyboard",
                price="1500", category="electronics", condition="used", quantity="2")
    data.update(overrides)
    return data


def test_create_product_saves_image_with_random_name(make_user):
    seller = make_user("seller")
    r = seller.post("/api/products", data=form(), files={"image": ("my keyboard.png", PNG, "image/png")})
    assert r.status_code == 201
    filename = r.json()["image_url"].rsplit("/", 1)[1]
    assert "keyboard" not in filename and filename.endswith(".png")
    assert (product_service.PRODUCT_UPLOAD_DIR / filename).exists()
    assert r.json()["seller_username"] == "seller"


def test_rejects_non_images_even_with_image_extension(make_user):
    seller = make_user("seller")
    r = seller.post("/api/products", data=form(),
                    files={"image": ("cat.png", b"<html>not an image</html>", "image/png")})
    assert r.status_code == 400
    assert "valid JPG, PNG or WEBP" in r.json()["detail"]


def test_rejects_bad_extension_and_big_files(make_user):
    seller = make_user("seller")
    assert seller.post("/api/products", data=form(),
                       files={"image": ("run.exe", PNG, "image/png")}).status_code == 400
    big = PNG + b"0" * (2 * 1024 * 1024)
    assert seller.post("/api/products", data=form(),
                       files={"image": ("big.png", big, "image/png")}).status_code == 400


def test_validates_price_and_quantity(make_user):
    seller = make_user("seller")
    r = seller.post("/api/products", data=form(price="-5", quantity="0"),
                    files={"image": ("a.png", PNG, "image/png")})
    assert r.status_code == 422
    assert {"price", "quantity"} <= {e["loc"][-1] for e in r.json()["detail"]}


def test_create_requires_login_and_csrf(make_user, anon):
    seller = make_user("seller")
    files = {"image": ("a.png", PNG, "image/png")}
    assert anon.post("/api/products", data=form(), files=files).status_code == 401
    assert TestClient(app, cookies=seller.cookies).post("/api/products", data=form(), files=files).status_code == 403


def test_only_the_owner_can_edit_or_delete(make_user, make_product):
    alice, bob = make_user("alice"), make_user("bob")
    product = make_product(alice)
    assert bob.put(f"/api/products/{product['id']}", data=form()).status_code == 403
    assert bob.delete(f"/api/products/{product['id']}").status_code == 403
    assert bob.get(f"/products/{product['id']}/edit").status_code == 403
    assert alice.put(f"/api/products/{product['id']}", data=form(price="999")).json()["price"] == "999.00"


def test_new_image_replaces_old_file(make_user, make_product):
    seller = make_user("seller")
    product = make_product(seller)
    old = product["image_url"].rsplit("/", 1)[1]
    r = seller.put(f"/api/products/{product['id']}", data=form(),
                   files={"image": ("new.png", PNG, "image/png")})
    new = r.json()["image_url"].rsplit("/", 1)[1]
    assert new != old
    assert not (product_service.PRODUCT_UPLOAD_DIR / old).exists()
    assert (product_service.PRODUCT_UPLOAD_DIR / new).exists()


def test_delete_removes_row_and_image(make_user, make_product):
    seller = make_user("seller")
    product = make_product(seller)
    filename = product["image_url"].rsplit("/", 1)[1]
    assert seller.delete(f"/api/products/{product['id']}").status_code == 204
    assert seller.get(f"/api/products/{product['id']}").status_code == 404
    assert not (product_service.PRODUCT_UPLOAD_DIR / filename).exists()


def test_sold_out_products_are_hidden_from_listing(make_user, make_product):
    seller = make_user("seller")
    product = make_product(seller)
    seller.put(f"/api/products/{product['id']}", data=form(quantity="0"))
    assert seller.get("/api/products").json() == []


def test_search_and_filters(make_user, make_product):
    seller = make_user("seller")
    make_product(seller, name="Mechanical Keyboard", price="1500", category="electronics", condition="used")
    make_product(seller, name="Gaming Mouse", price="800", category="electronics", condition="like_new")
    make_product(seller, name="Keyboard cover", price="150", category="accessories", condition="new")
    make_product(seller, name="Physics Textbook", price="350", category="books", condition="good")

    def names(query):
        return sorted(p["name"] for p in seller.get(f"/api/products{query}").json())

    assert names("?search=keyboard") == ["Keyboard cover", "Mechanical Keyboard"]
    assert names("?search=mechanical%20KEYBOARD") == ["Mechanical Keyboard"]
    assert names("?category=electronics&min_price=500&max_price=1000") == ["Gaming Mouse"]
    assert names("?condition=new") == ["Keyboard cover"]
    assert names("?search=&category=") == names("")          # blank fields are ignored
    prices = [p["price"] for p in seller.get("/api/products?sort=price_asc").json()]
    assert prices == sorted(prices, key=float)


def test_search_treats_percent_literally(make_user, make_product):
    seller = make_user("seller")
    make_product(seller, name="Plain item")
    assert seller.get("/api/products?search=%25").json() == []


def test_bad_filters_are_rejected(anon):
    assert anon.get("/api/products?min_price=500&max_price=100").status_code == 422
    assert anon.get("/api/products?category=cars").status_code == 422
