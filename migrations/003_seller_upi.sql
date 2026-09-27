-- Run ONCE on a database created before sellers had their own UPI IDs.
-- (A database created from the new schema.sql already has these columns.)

USE mini_olx;

-- Where buyers pay each seller.
ALTER TABLE users
    ADD COLUMN upi_id   VARCHAR(100) NULL AFTER google_sub,
    ADD COLUMN upi_name VARCHAR(100) NULL AFTER upi_id;

-- Snapshot on each order: the UPI ID the buyer was asked to pay.
ALTER TABLE orders
    ADD COLUMN payee_upi_id VARCHAR(100) NULL AFTER gateway_order_id,
    ADD COLUMN payee_name   VARCHAR(100) NULL AFTER payee_upi_id;
