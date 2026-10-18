-- 004_id_cards.sql
--
-- Student ID card generation records.
--
-- The Barcode Generator page used to have no card state at all: it derived
-- "Generated / Missing" purely from whether a row existed in `barcodes`, which
-- says nothing about whether a *card* was ever rendered. This table is the
-- smallest persistence change that lets the ID Card Generator report an honest
-- status and remember where a rendered card was written.
--
-- Deliberately NOT included:
--   * A `printed` status. This application has no reliable way to confirm that
--     a print job reached a printer -- QPrintDialog only reports that the
--     operator accepted the dialog. A `printed` flag would therefore be a
--     claim the database cannot support, so the closest honest state is
--     `print_requested`, recorded when the print dialog is accepted.
--   * Any historical backfill. No row is invented for students who already own
--     a physical card; their status stays `pending` until an operator generates
--     one through this application.
--
-- `custom_fields` holds operator-entered back-side text (an emergency contact
-- that is not yet in `student_contacts`, a signature line label, a validity
-- note) as JSON. It is deliberately separate from `students` so card
-- presentation text can never overwrite student data.

CREATE TABLE IF NOT EXISTS `id_cards` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `student_id` INT UNSIGNED NOT NULL,
    `template_key` VARCHAR(64) NOT NULL DEFAULT 'official_wit',
    `front_image_path` VARCHAR(255) DEFAULT NULL,
    `back_image_path` VARCHAR(255) DEFAULT NULL,
    `pdf_path` VARCHAR(255) DEFAULT NULL,
    `status` ENUM('pending', 'generated', 'print_requested', 'error') NOT NULL DEFAULT 'pending',
    `last_error` VARCHAR(500) DEFAULT NULL,
    `render_count` INT UNSIGNED NOT NULL DEFAULT 0,
    `custom_fields` TEXT DEFAULT NULL,
    `generated_by` INT UNSIGNED DEFAULT NULL,
    `last_generated_at` TIMESTAMP NULL DEFAULT NULL,
    `last_print_requested_at` TIMESTAMP NULL DEFAULT NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_id_cards_student_template` (`student_id`, `template_key`),
    INDEX `idx_id_cards_status` (`status`),
    CONSTRAINT `fk_id_cards_student` FOREIGN KEY (`student_id`) REFERENCES `students`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_id_cards_generated_by` FOREIGN KEY (`generated_by`) REFERENCES `users`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;