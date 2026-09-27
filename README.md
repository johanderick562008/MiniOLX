# Mini OLX — a learning marketplace

A small OLX-style marketplace built with **FastAPI + MySQL + HTML/CSS/vanilla JavaScript**.

> **Learning/Demo Project.** This is not for production or real commercial use.
> Payments use a **manually verified UPI QR workflow**: the website cannot see
> UPI transactions, so a payment is confirmed only after the seller checks it.

---

## 1. Project overview

Users register, list items for sale with a photo, browse and search listings,
add items to a cart, and check out. Checkout creates an order and shows a UPI
QR code. The buyer pays from any UPI app, enters the transaction reference and
clicks **I Have Paid**, which makes the payment **Pending verification**. The
seller checks their UPI app and clicks **Verify**, which makes the payment
**Verified** and the order **Paid**.

The goal is to understand how a real e-commerce backend works: authentication,
authorization, database relationships, transactions, inventory, and why a
payment can't be trusted just because a user says they paid.

## 2. Features

- Registration and login with bcrypt password hashing and server-side sessions
- **Sign in with Google** (OAuth 2.0 + OpenID Connect, with state, nonce and PKCE)
- CSRF protection on every request that changes data
- Product listings with validated image uploads; edit and delete (owner only)
- Search, category/condition/price filters, sorting, live results
- Shopping cart with stock checks and "can't buy your own product"
- Checkout: one order per seller, price snapshots, stock reserved in a locked transaction
- Buyer can cancel an unpaid order (stock is returned)
- Each seller sets their own UPI ID; the payment page shows the **seller's** UPI ID and a QR for it, with amount and order number pre-filled
- "I Have Paid" → pending; seller verifies or rejects; buyer can resubmit
- Seller sales dashboard; order status flow paid → processing → completed
- Optional **Razorpay test mode** checkout with signature verification, capture and webhooks
- Seed data and 89 automated tests

## 3. Technology stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, Uvicorn, Pydantic |
| Database | MySQL 8, SQLAlchemy 2 ORM, PyMySQL driver |
| Auth | bcrypt, random session tokens in HttpOnly cookies |
| Frontend | Jinja2 templates, HTML5, CSS3, vanilla JavaScript; design from Google Stitch, self-hosted Inter + Plus Jakarta Sans fonts and Material Symbols SVG icons |
| Payments | Manual UPI (`upi://pay` link drawn as a QR with `segno`) |
| Config | python-dotenv (`.env`) |
| Tests | pytest + FastAPI TestClient on in-memory SQLite |

## 4. Architecture

```text
Browser ── HTML pages (GET) ──▶ FastAPI page routes ──▶ Jinja2 templates
   │
   └── fetch() JSON (POST/PUT/DELETE + X-CSRF-Token) ──▶ FastAPI API routes
                                                            │
                                  routers/  (HTTP: validate, status codes)
                                     │
                                  services/ (rules, transactions, locking)
                                     │
                                  models.py (SQLAlchemy) ──▶ MySQL
```

- **Pages** are rendered on the server. Anything that changes data goes through
  the JSON API from `static/js/script.js`, which sends the CSRF token.
- **Routers** deal with HTTP. **Services** hold the business rules, so each rule
  lives in exactly one place.
- **Authorization is always on the server.** Hidden buttons are only cosmetic.

### Authentication: why sessions instead of JWT

Login creates a random token, stores a keyed hash of it in `user_sessions`, and
sets it as an `HttpOnly; SameSite=Lax` cookie. Every request looks the session up.
Unlike a JWT, a session can be revoked instantly: logout deletes the row, so a
stolen cookie stops working. With server-rendered pages that's also simpler,
since the browser sends the cookie automatically.

### Request flow examples

```text
Login:  form → POST /api/auth/login → Pydantic validation → find user →
        bcrypt check → INSERT user_sessions → Set-Cookie → redirect /dashboard

Buy:    product → cart_items → checkout → POST /api/orders
        [transaction: lock cart + products FOR UPDATE → re-check stock →
         INSERT orders + order_items (price snapshot) → reduce stock →
         empty cart → COMMIT]
        → /payment/{id} → UPI QR → pay externally → enter reference →
        POST /api/payments (status = pending) → seller checks UPI app →
        POST /api/payments/{id}/verify → payment verified, order paid
```

## 5. Project structure

```text
mini_olx/
├── app/
│   ├── main.py              app setup, middleware, routers, static files
│   ├── config.py            settings from .env
│   ├── database.py          engine, sessions, get_db dependency
│   ├── models.py            SQLAlchemy models (one per table)
│   ├── schemas.py           Pydantic request/response models and validation
│   ├── dependencies.py      current user/session, CSRF check
│   ├── templating.py        Jinja2 setup, ₹ formatting
│   ├── routers/             auth, products, cart, orders, payments, seller, pages
│   ├── services/            auth, product, cart, order, payment, seller logic
│   ├── templates/           HTML pages (premium theme)
│   ├── themes/classic/      the original design's templates (THEME=classic)
│   ├── icons.py             SVG paths for the icons the templates use
│   ├── static/css, js/      premium style.css, script.js
│   ├── static/fonts/        Inter + Plus Jakarta Sans (with the ₹ glyph)
│   ├── static/classic/      the classic theme's CSS and JS
│   └── uploads/products/    uploaded product images (git-ignored)
├── tests/                   pytest suite
├── schema.sql               MySQL schema (source of truth)
├── seed.py                  demo users and products
├── migrations/              001_razorpay, 002_google_login, 003_seller_upi (.sql)
├── tools/                   send_test_webhook.py
├── requirements.txt         app dependencies
├── requirements-dev.txt     + test dependencies
├── pytest.ini
├── .env.example             copy to .env
└── README.md
```

## 6. MySQL installation (Windows)

1. Download the **MySQL Installer** from dev.mysql.com/downloads and install
   **MySQL Server 8.x** with the defaults (port 3306). Set a root password.
2. Keep "Configure as a Windows Service" ticked so MySQL starts automatically.
3. Use **MySQL 8.x Command Line Client** from the Start menu, or add
   `C:\Program Files\MySQL\MySQL Server 8.x\bin` to your PATH to use `mysql` in cmd.

## 7. Database creation

In the MySQL Command Line Client (logged in as root):

```sql
SOURCE D:/path/to/mini_olx/schema.sql
CREATE USER IF NOT EXISTS 'mini_olx_user'@'localhost' IDENTIFIED BY 'choose_a_password';
GRANT ALL PRIVILEGES ON mini_olx.* TO 'mini_olx_user'@'localhost';
FLUSH PRIVILEGES;
SHOW TABLES;
```

Use forward slashes and **no quotes** in the `SOURCE` path. `schema.sql` is safe
to run again: every statement uses `IF NOT EXISTS`.

Tables: `users`, `user_sessions`, `products`, `cart_items`, `orders`,
`order_items`, `payments`.

## 8. Environment variables

Copy `.env.example` to `.env` in the project root and fill it in:

| Variable | Purpose |
|---|---|
| `DB_HOST`, `DB_PORT` | MySQL address (`localhost`, `3306`) |
| `DB_USER`, `DB_PASSWORD`, `DB_NAME` | the app user from step 7, and `mini_olx` |
| `SECRET_KEY` | long random string: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `UPI_ID`, `UPI_NAME` | fallback only, for orders placed before sellers had UPI IDs (sellers now set their own in **Payment settings**) |
| `SESSION_DAYS` | login lifetime (default 7) |
| `COOKIE_SECURE` | `true` only when served over HTTPS |
| `DB_ECHO` | `true` prints every SQL query to the terminal |
| `THEME` | `premium` (default, the Stitch design) or `classic` (the original design); restart after changing |

No quotes and no spaces around `=`. `.env` is listed in `.gitignore` and must never be committed.

## 9. Installing dependencies

```cmd
cd D:\path\to\mini_olx
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

(PowerShell: `.\venv\Scripts\Activate.ps1`.) For the tests, also run
`pip install -r requirements-dev.txt`.

## 10. Running FastAPI

The app object is `app` in `app/main.py`. Check the setup at:

- http://127.0.0.1:8000/ — the marketplace
- http://127.0.0.1:8000/health/db — database connection check
- http://127.0.0.1:8000/docs — interactive API documentation

## 11. Running Uvicorn

```cmd
uvicorn app.main:app --reload
```

Run it from the project root (the folder that contains `app`). `--reload`
restarts on file changes. Stop with Ctrl + C. `.env` is read only at startup, so
restart after editing it.

Optional demo data:

```cmd
python seed.py
```

This creates `demo_seller` and `demo_buyer` (password `Demo12345`) and nine
products with placeholder images. Running it again skips what already exists.

## 12. Creating accounts

Go to **Create account**. Username: 3–30 letters, numbers or underscores.
Password: at least 8 characters with a letter and a number. Usernames and
emails are unique (case-insensitive). Log in with username or email.

To test buying you need **two accounts**, because you can't buy your own items.
Use a normal window for the seller and an incognito window for the buyer.

### Sign in with Google (optional)

1. In Google Cloud Console open **Google Auth Platform**: fill in **Branding**
   (app name, support email); under **Audience** keep *Testing* and add your
   Gmail as a test user; under **Clients** create a **Web application** client
   with the authorized redirect URI `http://127.0.0.1:8000/auth/google/callback`.
2. Put the client ID and secret in `.env` as `GOOGLE_CLIENT_ID` and
   `GOOGLE_CLIENT_SECRET`, and restart.
3. Databases created before this feature: run `migrations/002_google_login.sql` once.
4. Open the site at **http://127.0.0.1:8000** (not `localhost`: the address must
   match the redirect URI, or the sign-in cookie won't be found).

A first Google sign-in creates an account (username from the email, no
password). Later sign-ins match on Google's permanent `sub` id. If the email
already belongs to a password account, the two are **not** linked
automatically (see Security).

## 13. Adding products

First add your UPI ID under **Payment settings** (dashboard → Add your UPI ID);
listing is blocked until you do, because buyers need somewhere to pay you.
Then **Sell an item** → name, description, price, category, condition, quantity and a
photo (JPG/PNG/WEBP, max 2 MB). The file's bytes are checked, not just its name,
and it's saved under a random filename. Only the owner sees **Edit** and
**Delete**, and the server enforces that too. Setting quantity to 0 marks an
item sold out.

## 14. Testing the cart

As the buyer: open a product, choose a quantity, **Add to cart**. In **Cart**,
use − / + or type a quantity; totals update without reloading. Try exceeding the
stock ("Only N available"), typing 0 (rejected), and **Remove**. If the seller
lowers the stock, the cart marks the item and blocks checkout.

## 15. Testing checkout

**Proceed to checkout** → fill in delivery details (10-digit mobile, 6-digit
pincode, state from the list) → **Place order**. Check that the product's stock
dropped and the cart is empty. Change the product's price as the seller: the
order keeps the old price (snapshot). A cart with two sellers' items becomes two
orders. Unpaid orders can be cancelled, which returns the stock.

## 16. Testing UPI payment

After placing an order you land on `/payment/{order_id}`: QR code, UPI ID,
amount and order number. Scanning opens your UPI app with the amount and a note
like `ORD-1001 Mini OLX` pre-filled. Enter the transaction ID / UTR and click
**I Have Paid**. The status becomes **Pending verification**, never "successful".

UPI won't let you pay your own account. To test with real money, use a ₹1 item
and pay from another account, or simply type a made-up 12-digit reference. That
the site accepts it is exactly why manual verification exists.

## 17. Testing payment verification

As the seller, the **Sales** link shows a red badge with payments to verify.
Compare the amount and reference with your UPI app, then:

- **Verify payment** → payment *Verified*, order *Paid*, the buyer's address appears
- **Reject payment** → payment *Rejected*; the order still awaits payment and the buyer can resubmit

Then move the order along: **Mark as processing** → **Mark as completed**.

### Automated tests

```cmd
pip install -r requirements-dev.txt
pytest
```

89 tests cover authentication, Google sign-in, authorization, products, uploads, search, cart,
orders, stock, price snapshots, payments, verification and Razorpay (with the Razorpay API faked, so they run offline). They use a fresh
in-memory SQLite database per test and never touch your MySQL data.

### Razorpay test mode (optional upgrade)

1. Sign up at dashboard.razorpay.com, switch to **Test Mode**, then
   **Account & Settings → API Keys → Generate Key**.
2. In `.env` set `RAZORPAY_KEY_ID` (rzp_test_...), `RAZORPAY_KEY_SECRET`, and
   `RAZORPAY_WEBHOOK_SECRET` (any long random string you choose).
3. Databases created before this upgrade: run `migrations/001_razorpay.sql` once.
4. At checkout choose **Razorpay (test mode)**, then **Pay with Razorpay**. In the
   window choose UPI and enter `success@razorpay` (or `failure@razorpay`).
5. Webhooks without a public URL: close the Razorpay window without paying,
   then run `python tools/send_test_webhook.py ORD-1005`. The order becomes paid
   through the webhook alone. With a tunnel (e.g. `cloudflared tunnel --url
   http://localhost:8000`) you can register the real webhook URL
   `https://<tunnel>/api/webhooks/razorpay` in the Dashboard (events:
   `payment.captured`, `order.paid`).

Flow: server creates a Razorpay order (amount locked) → checkout.js → browser
sends payment id + signature → server checks the HMAC using **its own** stored
Razorpay order id → asks Razorpay's API for the payment, captures it if only
authorized, checks amount → marks paid. The webhook does the same from
Razorpay's side; both paths are idempotent, so the payment is recorded once.

## 18. Security considerations

| Threat | Protection |
|---|---|
| Stolen password database | bcrypt hashes with per-password salt; plain passwords never stored |
| Stolen session table | only an HMAC (keyed with `SECRET_KEY`) of each token is stored |
| Session theft via XSS | `HttpOnly` cookie; Jinja2 autoescaping; JS uses `textContent` |
| CSRF | per-session token required in `X-CSRF-Token`; `SameSite=Lax` cookie |
| Session fixation | a fresh session is created at every login |
| Username enumeration | same message and similar timing for "no user" and "wrong password" |
| SQL injection | SQLAlchemy bound parameters everywhere; `LIKE` wildcards escaped |
| Acting on others' data | every query is filtered by the logged-in user (owner/buyer/seller) |
| Malicious uploads | extension whitelist, magic-byte check, 2 MB limit, random filenames, `nosniff` header |
| Overselling | `SELECT ... FOR UPDATE` on products inside the order transaction |
| Double submission | cart rows and order rows are locked; duplicates get 400/409 |
| Fake payment success | buyer actions can only create `pending`; only the seller can verify |
| Secrets in code | all secrets in `.env`, which is git-ignored |
| Forged Razorpay success | HMAC signature checked with the Key Secret against our stored order id; status and amount confirmed via Razorpay's API |
| Fake webhooks | HMAC of the raw body with the webhook secret; invalid signatures get 400 |
| Duplicate webhooks | payment recorded once per Razorpay payment id (order row locked) |
| Google login CSRF | random `state` stored in a signed, HttpOnly cookie and compared on return |
| Stolen authorization code | PKCE: the code only works with the verifier kept on our side |
| Replayed / foreign ID tokens | issuer, audience (our client id), expiry and `nonce` all checked |
| Pre-account hijacking | Google sign-in never auto-links to an existing password account with the same email |
| Leaking input in errors | custom 422 handler returns only field and message, never the submitted values |

## 19. Known limitations

- Payments are verified by hand; a buyer could type a reference from another
  payment, so the seller must match the amount and reference in their UPI app.
- No login rate limiting (unlimited password attempts).
- Search uses `LIKE '%word%'`, which scans the table; results are capped at 60 with no pagination.
- Unpaid orders hold stock until the buyer cancels (no automatic expiry).
- Times are shown in UTC.
- Tests run on SQLite, so MySQL-specific behaviour (row locks, CHECK constraints,
  collation) is exercised only when you use the app with MySQL.
- Images are stored on local disk.
- Razorpay: webhooks are processed inline instead of via a job queue; a payment
  that arrives after an order was cancelled is logged, not refunded.

## 20. Future improvements

- Automatic refunds for late Razorpay payments; a job queue for webhooks.
- Email notifications for orders and payment status.
- Product reviews, wishlist, seller ratings, admin dashboard.
- Pagination and MySQL `FULLTEXT` search; Redis caching.
- Login rate limiting; auto-cancel unpaid orders after a time limit.
- Cloud image storage (S3/Cloudinary) and image resizing.
- Docker Compose (app + MySQL), deployment behind HTTPS (`COOKIE_SECURE=true`).
- Alembic database migrations instead of hand-edited `schema.sql`.
- Tests against a real MySQL instance in CI.
