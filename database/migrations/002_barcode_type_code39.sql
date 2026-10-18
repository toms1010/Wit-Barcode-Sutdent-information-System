-- Migration 002: allow CODE39 in barcodes.barcode_type
--
-- Symptom
--   scripts/initialize_database.py inserts BarcodeType.CODE39 ("CODE39") for
--   every barcode copied from the supplied assets, because pyzbar reports the
--   symbology of those images as CODE39. MySQL rejects the insert with
--       1265 (01000): Data truncated for column 'barcode_type' at row 1
--   mysql-connector raises this as a DatabaseUnavailableError in strict mode,
--   which aborts the seed part-way through and leaves the `barcodes` table
--   empty even though `students` was already populated.
--
-- Cause
--   The enum in schema.py omitted 'CODE39' while models/barcode.py has always
--   defined BarcodeType.CODE39.
--
-- Safety
--   Widening an ENUM is additive and non-destructive: existing rows keep their
--   values, no data is rewritten, and the change can be reversed with the
--   companion statement below. Run against a backup first if one is required by
--   local policy.
--
-- Apply with:
--   mysql -u <user> -p <database> < database/migrations/002_barcode_type_code39.sql

ALTER TABLE `barcodes`
  MODIFY COLUMN `barcode_type` ENUM('CODE39', 'CODE128', 'QR_CODE', 'EAN13')
  NOT NULL DEFAULT 'CODE128';

-- Verify
--   SELECT COLUMN_TYPE FROM information_schema.COLUMNS
--    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'barcodes'
--      AND COLUMN_NAME = 'barcode_type';
--   Expect: enum('CODE39','CODE128','QR_CODE','EAN13')
--
-- Rollback (only safe once no CODE39 rows exist)
--   SELECT COUNT(*) FROM `barcodes` WHERE `barcode_type` = 'CODE39';
--   ALTER TABLE `barcodes`
--     MODIFY COLUMN `barcode_type` ENUM('CODE128', 'QR_CODE', 'EAN13')
--     NOT NULL DEFAULT 'CODE128';