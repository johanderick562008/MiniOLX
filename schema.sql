-- Mini OLX schema. Grows phase by phase.
-- Safe to re-run: every statement uses IF NOT EXISTS.

CREATE DATABASE IF NOT EXISTS mini_olx
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE mini_olx;

-- ---------------------------------------------------------------
-- Phase 2: Authentication
-- ---------------------------------------------------------------

-- One row per registered account.
-- The unique keys are case-insensitive because of the utf8mb4_unicode_ci
-- collation: "Johan" and "johan" count as the same username.
CREATE TABLE IF NOT EXISTS users (
    id            INT           NOT NULL AUTO_INCREMENT,
    name          VARCHAR(100)  NOT NULL,
    email         VARCHAR(255)  NOT NULL,
    username      VARCHAR(30)   NOT NULL,
    password_hash VARCHAR(255)  NULL,       -- bcrypt hash; NULL for Google-only accounts
    google_sub    VARCHAR(255)  NULL,       -- Google's permanent user id ("sub")
    upi_id        VARCHAR(100)  NULL,       -- where buyers pay this user when they sell
    upi_name      VARCHAR(100)  NULL,       -- name shown in the buyer's UPI app
    created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    CONSTRAINT uq_users_email      UNIQUE (email),
    CONSTRAINT uq_users_username   UNIQUE (username),
    CONSTRAINT uq_users_google_sub UNIQUE (google_sub)
) ENGINE=InnoDB;

-- One row per logged-in browser. Logging out deletes the row.
-- token_hash stores a keyed hash of the cookie value, so someone who
-- reads this table still cannot log in as the user.
CREATE TABLE IF NOT EXISTS user_sessions (
    id          INT       NOT NULL AUTO_INCREMENT,
    user_id     INT       NOT NULL,
    token_hash  CHAR(64)  NOT NULL,
    csrf_token  CHAR(64)  NOT NULL,
    created_at  DATETIME  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at  DATETIME  NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_sessions_token_hash UNIQUE (token_hash),
    CONSTRAINT fk_sessions_user FOREIGN KEY (user_id)
        REFERENCES users (id) ON DELETE CASCADE,
    INDEX ix_sessions_user_id (user_id),
    INDEX ix_sessions_expires_at (expires_at)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------
-- Phase 3: Products
-- ---------------------------------------------------------------

-- `condition` is a reserved word in MySQL, so it must be written in backticks.
-- price is DECIMAL, never FLOAT: money needs exact values (0.1 + 0.2 = 0.3).
CREATE TABLE IF NOT EXISTS products (
    id              INT            NOT NULL AUTO_INCREMENT,
    seller_id       INT            NOT NULL,
    name            VARCHAR(120)   NOT NULL,
    description     TEXT           NOT NULL,
    price           DECIMAL(10,2)  NOT NULL,
    category        ENUM('electronics','books','accessories','clothing','other') NOT NULL,
    `condition`     ENUM('new','like_new','good','used') NOT NULL,
    quantity        INT            NOT NULL DEFAULT 1,
    image_filename  VARCHAR(64)    NOT NULL,   -- random name we generated, never the browser's
    created_at      DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    CONSTRAINT fk_products_seller FOREIGN KEY (seller_id)
        REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT chk_products_price    CHECK (price > 0),
    CONSTRAINT chk_products_quantity CHECK (quantity >= 0),
    INDEX ix_products_seller_id  (seller_id),
    INDEX ix_products_category   (category),
    INDEX ix_products_created_at (created_at)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------
-- Phase 5: Shopping cart
-- ---------------------------------------------------------------

-- One row per (user, product). Adding the same product again increases
-- quantity instead of creating a second row; the UNIQUE key enforces that.
-- That same UNIQUE key also serves "all cart rows for user X" lookups,
-- because an index on (user_id, product_id) can be used for user_id alone.
CREATE TABLE IF NOT EXISTS cart_items (
    id          INT       NOT NULL AUTO_INCREMENT,
    user_id     INT       NOT NULL,
    product_id  INT       NOT NULL,
    quantity    INT       NOT NULL,
    created_at  DATETIME  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    CONSTRAINT uq_cart_user_product UNIQUE (user_id, product_id),
    CONSTRAINT fk_cart_user FOREIGN KEY (user_id)
        REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_cart_product FOREIGN KEY (product_id)
        REFERENCES products (id) ON DELETE CASCADE,   -- deleted listing leaves every cart
    CONSTRAINT chk_cart_quantity CHECK (quantity > 0),
    INDEX ix_cart_product_id (product_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------
-- Phase 6: Orders
-- ---------------------------------------------------------------

-- One order = one buyer buying from ONE seller. A cart with items from
-- two sellers becomes two orders at checkout, so each seller later
-- verifies payment only for their own order.
CREATE TABLE IF NOT EXISTS orders (
    id              INT            NOT NULL AUTO_INCREMENT,
    buyer_id        INT            NOT NULL,
    total_amount    DECIMAL(12,2)  NOT NULL,
    shipping_name   VARCHAR(100)   NOT NULL,
    phone           VARCHAR(15)    NOT NULL,
    address         VARCHAR(255)   NOT NULL,
    city            VARCHAR(100)   NOT NULL,
    state           VARCHAR(100)   NOT NULL,
    pincode         CHAR(6)        NOT NULL,
    payment_method  VARCHAR(20)    NOT NULL DEFAULT 'upi',      -- 'upi' or 'razorpay'
    gateway_order_id VARCHAR(40)   NULL,   -- Razorpay's order id (order_...), Razorpay orders only
    payee_upi_id    VARCHAR(100)   NULL,   -- SNAPSHOT of the seller's UPI ID when the order was placed
    payee_name      VARCHAR(100)   NULL,   -- SNAPSHOT of the name shown in the UPI app
    status          ENUM('pending_payment','paid','processing','completed','cancelled')
                                   NOT NULL DEFAULT 'pending_payment',
    created_at      DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    CONSTRAINT fk_orders_buyer FOREIGN KEY (buyer_id) REFERENCES users (id),
    CONSTRAINT chk_orders_total CHECK (total_amount > 0),
    CONSTRAINT uq_orders_gateway_order_id UNIQUE (gateway_order_id),
    INDEX ix_orders_buyer_created (buyer_id, created_at),
    INDEX ix_orders_status (status)
) ENGINE=InnoDB;

-- The lines of an order. product_name and price_at_purchase are SNAPSHOTS:
-- copied from the product when the order is placed, so the order never
-- changes if the seller later edits the price or deletes the listing.
CREATE TABLE IF NOT EXISTS order_items (
    id                 INT            NOT NULL AUTO_INCREMENT,
    order_id           INT            NOT NULL,
    product_id         INT            NULL,       -- NULL once the listing is deleted
    seller_id          INT            NOT NULL,
    product_name       VARCHAR(120)   NOT NULL,
    quantity           INT            NOT NULL,
    price_at_purchase  DECIMAL(10,2)  NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_order_items_order FOREIGN KEY (order_id)
        REFERENCES orders (id) ON DELETE CASCADE,
    CONSTRAINT fk_order_items_product FOREIGN KEY (product_id)
        REFERENCES products (id) ON DELETE SET NULL,
    CONSTRAINT fk_order_items_seller FOREIGN KEY (seller_id) REFERENCES users (id),
    CONSTRAINT chk_order_items_quantity CHECK (quantity > 0),
    CONSTRAINT chk_order_items_price CHECK (price_at_purchase > 0),
    INDEX ix_order_items_order_id (order_id),
    INDEX ix_order_items_product_id (product_id),
    INDEX ix_order_items_seller_id (seller_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------
-- Phase 7: Payments
-- ---------------------------------------------------------------

-- One row per "I Have Paid" claim. An order can have several rows over
-- time (a rejected claim, then a corrected one), but the app allows at
-- most one 'pending' or 'verified' row per order.
-- amount is copied from the order: the amount the buyer was asked to pay.
CREATE TABLE IF NOT EXISTS payments (
    id                     INT            NOT NULL AUTO_INCREMENT,
    order_id               INT            NOT NULL,
    amount                 DECIMAL(12,2)  NOT NULL,
    payment_method         VARCHAR(20)    NOT NULL,
    transaction_reference  VARCHAR(35)    NOT NULL,   -- UPI transaction ID / UTR typed by the buyer
    status                 ENUM('pending','verified','rejected') NOT NULL DEFAULT 'pending',
    created_at             DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    verified_at            DATETIME       NULL,       -- set by the seller in Phase 8
    PRIMARY KEY (id),
    CONSTRAINT fk_payments_order FOREIGN KEY (order_id)
        REFERENCES orders (id) ON DELETE CASCADE,
    CONSTRAINT chk_payments_amount CHECK (amount > 0),
    INDEX ix_payments_order_id (order_id),
    INDEX ix_payments_reference (transaction_reference),
    INDEX ix_payments_status (status)
) ENGINE=InnoDB;
