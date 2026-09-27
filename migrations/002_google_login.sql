-- Run ONCE on a database created before "Sign in with Google" was added.
-- (A database created from the new schema.sql already has these changes.)

USE mini_olx;

-- Google-only accounts have no password.
ALTER TABLE users MODIFY password_hash VARCHAR(255) NULL;

-- Google's permanent id for the user. UNIQUE: one Mini OLX account per Google account.
ALTER TABLE users
    ADD COLUMN google_sub VARCHAR(255) NULL AFTER password_hash,
    ADD CONSTRAINT uq_users_google_sub UNIQUE (google_sub);
