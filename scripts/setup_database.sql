-- Database Setup for WIT Barcode Student Information System
-- Run as root:  sudo mysql < scripts/setup_database.sql
--
-- This script is safe to re-run. It is also the REPAIR script: it never drops
-- the database, so existing student, barcode and assessment records are kept.

CREATE DATABASE IF NOT EXISTS wit_barcode_system
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Create the account if it is missing.
CREATE USER IF NOT EXISTS 'wit_user'@'localhost' IDENTIFIED BY 'wit_password';

-- IMPORTANT: CREATE USER ... IF NOT EXISTS silently does nothing when the
-- account already exists, so a wrong/stale password would persist and produce
-- "1045 (28000): Access denied". ALTER USER always applies the intended
-- password, which is what makes this script able to repair a broken account.
ALTER USER 'wit_user'@'localhost' IDENTIFIED BY 'wit_password';

-- The application may connect over TCP (127.0.0.1) as well as the unix socket,
-- so the account must exist for both hosts.
CREATE USER IF NOT EXISTS 'wit_user'@'127.0.0.1' IDENTIFIED BY 'wit_password';
ALTER USER 'wit_user'@'127.0.0.1' IDENTIFIED BY 'wit_password';

GRANT ALL PRIVILEGES ON wit_barcode_system.* TO 'wit_user'@'localhost';
GRANT ALL PRIVILEGES ON wit_barcode_system.* TO 'wit_user'@'127.0.0.1';

FLUSH PRIVILEGES;

-- ---------------------------------------------------------------------------
-- Verification
-- ---------------------------------------------------------------------------

-- 1. The account must be listed for both hosts.
SELECT User, Host, plugin FROM mysql.user WHERE User = 'wit_user';

-- 2. The database must be present.
SHOW DATABASES LIKE 'wit_barcode_system';

-- 3. Confirm the privileges actually landed.
SHOW GRANTS FOR 'wit_user'@'localhost';

-- Leave the mysql client; then verify as the application account:
--   mysql -u wit_user -p -h 127.0.0.1 wit_barcode_system