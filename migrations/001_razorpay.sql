-- Run ONCE on a database created before the Razorpay upgrade.
-- (A database created from the new schema.sql already has this column;
--  running this there fails with "Duplicate column name", which is harmless.)
--
-- Why a separate file? schema.sql uses CREATE TABLE IF NOT EXISTS, which
-- skips tables that already exist, so it can't add a column to them.
-- Changing an existing table needs ALTER TABLE: that's a "migration".

USE mini_olx;

ALTER TABLE orders
    ADD COLUMN gateway_order_id VARCHAR(40) NULL AFTER payment_method,
    ADD CONSTRAINT uq_orders_gateway_order_id UNIQUE (gateway_order_id);
