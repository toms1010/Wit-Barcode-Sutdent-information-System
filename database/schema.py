SCHEMA_SQL = """
CREATE DATABASE IF NOT EXISTS `wit_barcode_system` 
CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE `wit_barcode_system`;

CREATE TABLE IF NOT EXISTS `users` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `username` VARCHAR(50) NOT NULL UNIQUE,
    `email` VARCHAR(100) NOT NULL UNIQUE,
    `password_hash` VARCHAR(255) NOT NULL,
    `full_name` VARCHAR(100) NOT NULL,
    `role` ENUM('admin', 'staff', 'viewer') NOT NULL DEFAULT 'staff',
    `is_active` BOOLEAN NOT NULL DEFAULT TRUE,
    `password_changed_at` TIMESTAMP NULL,
    `failed_attempts` INT UNSIGNED NOT NULL DEFAULT 0,
    `locked_until` TIMESTAMP NULL,
    `last_login` TIMESTAMP NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_users_username` (`username`),
    INDEX `idx_users_email` (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `students` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `student_id` VARCHAR(20) NOT NULL UNIQUE,
    `reference_number` VARCHAR(20) NOT NULL UNIQUE,
    `first_name` VARCHAR(50) NOT NULL,
    `middle_name` VARCHAR(50) DEFAULT NULL,
    `last_name` VARCHAR(50) NOT NULL,
    `suffix` VARCHAR(10) DEFAULT NULL,
    `program` VARCHAR(100) NOT NULL,
    `year_level` ENUM('1st Year', '2nd Year', '3rd Year', '4th Year', '5th Year') NOT NULL,
    `section` VARCHAR(20) DEFAULT NULL,
    `date_of_birth` DATE DEFAULT NULL,
    `gender` ENUM('Male', 'Female', 'Other') DEFAULT NULL,
    `address` TEXT DEFAULT NULL,
    `profile_image_path` VARCHAR(255) DEFAULT NULL,
    `enrollment_status` ENUM('active', 'inactive', 'graduated', 'dropped', 'on_leave') NOT NULL DEFAULT 'active',
    `date_enrolled` DATE NOT NULL DEFAULT (CURRENT_DATE),
    `barcode_payload` VARCHAR(100) DEFAULT NULL UNIQUE,
    `created_by` INT UNSIGNED DEFAULT NULL,
    `updated_by` INT UNSIGNED DEFAULT NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_students_student_id` (`student_id`),
    INDEX `idx_students_reference_number` (`reference_number`),
    INDEX `idx_students_program` (`program`),
    INDEX `idx_students_year_level` (`year_level`),
    INDEX `idx_students_enrollment_status` (`enrollment_status`),
    INDEX `idx_students_barcode_payload` (`barcode_payload`),
    CONSTRAINT `fk_students_created_by` FOREIGN KEY (`created_by`) REFERENCES `users`(`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_students_updated_by` FOREIGN KEY (`updated_by`) REFERENCES `users`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `student_contacts` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `student_id` INT UNSIGNED NOT NULL,
    `contact_type` ENUM('phone', 'email', 'emergency_phone', 'emergency_email', 'guardian_phone', 'guardian_email') NOT NULL,
    `contact_value` VARCHAR(100) NOT NULL,
    `is_primary` BOOLEAN NOT NULL DEFAULT FALSE,
    `label` VARCHAR(50) DEFAULT NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_student_contacts_student_id` (`student_id`),
    INDEX `idx_student_contacts_type` (`contact_type`),
    CONSTRAINT `fk_student_contacts_student` FOREIGN KEY (`student_id`) REFERENCES `students`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `courses` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `course_code` VARCHAR(20) NOT NULL UNIQUE,
    `course_name` VARCHAR(100) NOT NULL,
    `description` TEXT DEFAULT NULL,
    `department` VARCHAR(100) DEFAULT NULL,
    `credits` INT UNSIGNED NOT NULL DEFAULT 3,
    `is_active` BOOLEAN NOT NULL DEFAULT TRUE,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_courses_course_code` (`course_code`),
    INDEX `idx_courses_department` (`department`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `subjects` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `subject_code` VARCHAR(20) NOT NULL UNIQUE,
    `subject_name` VARCHAR(100) NOT NULL,
    `course_id` INT UNSIGNED NOT NULL,
    `credits` INT UNSIGNED NOT NULL DEFAULT 3,
    `lecture_hours` INT UNSIGNED NOT NULL DEFAULT 0,
    `lab_hours` INT UNSIGNED NOT NULL DEFAULT 0,
    `description` TEXT DEFAULT NULL,
    `is_active` BOOLEAN NOT NULL DEFAULT TRUE,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_subjects_subject_code` (`subject_code`),
    INDEX `idx_subjects_course_id` (`course_id`),
    CONSTRAINT `fk_subjects_course` FOREIGN KEY (`course_id`) REFERENCES `courses`(`id`) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `enrollments` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `student_id` INT UNSIGNED NOT NULL,
    `academic_year` VARCHAR(20) NOT NULL,
    `semester` ENUM('1st Semester', '2nd Semester', 'Summer', 'Midyear') NOT NULL,
    `enrollment_date` DATE NOT NULL DEFAULT (CURRENT_DATE),
    `status` ENUM('enrolled', 'dropped', 'completed', 'cancelled') NOT NULL DEFAULT 'enrolled',
    `total_credits` INT UNSIGNED NOT NULL DEFAULT 0,
    `created_by` INT UNSIGNED DEFAULT NULL,
    `updated_by` INT UNSIGNED DEFAULT NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_enrollments_student_year_semester` (`student_id`, `academic_year`, `semester`),
    INDEX `idx_enrollments_student_id` (`student_id`),
    INDEX `idx_enrollments_academic_year` (`academic_year`),
    INDEX `idx_enrollments_semester` (`semester`),
    INDEX `idx_enrollments_status` (`status`),
    CONSTRAINT `fk_enrollments_student` FOREIGN KEY (`student_id`) REFERENCES `students`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_enrollments_created_by` FOREIGN KEY (`created_by`) REFERENCES `users`(`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_enrollments_updated_by` FOREIGN KEY (`updated_by`) REFERENCES `users`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `enrollment_subjects` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `enrollment_id` INT UNSIGNED NOT NULL,
    `subject_id` INT UNSIGNED NOT NULL,
    `section` VARCHAR(20) DEFAULT NULL,
    `schedule_time` VARCHAR(50) DEFAULT NULL,
    `schedule_days` VARCHAR(20) DEFAULT NULL,
    `room` VARCHAR(20) DEFAULT NULL,
    `period` INT UNSIGNED DEFAULT NULL,
    `lab_fee` DECIMAL(10,2) DEFAULT NULL,
    `other_fee` DECIMAL(10,2) DEFAULT NULL,
    `grade` VARCHAR(5) DEFAULT NULL,
    `status` ENUM('enrolled', 'dropped', 'completed', 'failed') NOT NULL DEFAULT 'enrolled',
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_enrollment_subjects_enrollment_subject` (`enrollment_id`, `subject_id`),
    INDEX `idx_enrollment_subjects_enrollment_id` (`enrollment_id`),
    INDEX `idx_enrollment_subjects_subject_id` (`subject_id`),
    CONSTRAINT `fk_enrollment_subjects_enrollment` FOREIGN KEY (`enrollment_id`) REFERENCES `enrollments`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_enrollment_subjects_subject` FOREIGN KEY (`subject_id`) REFERENCES `subjects`(`id`) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `barcodes` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `student_id` INT UNSIGNED NOT NULL,
    `barcode_value` VARCHAR(100) NOT NULL UNIQUE,
    `barcode_type` ENUM('CODE39', 'CODE128', 'QR_CODE', 'EAN13') NOT NULL DEFAULT 'CODE128',
    `image_path` VARCHAR(255) DEFAULT NULL,
    `is_active` BOOLEAN NOT NULL DEFAULT TRUE,
    `generated_by` INT UNSIGNED DEFAULT NULL,
    `regenerated_count` INT UNSIGNED NOT NULL DEFAULT 0,
    `last_regenerated_at` TIMESTAMP NULL,
    `regenerated_by` INT UNSIGNED DEFAULT NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_barcodes_student_id` (`student_id`),
    INDEX `idx_barcodes_barcode_value` (`barcode_value`),
    INDEX `idx_barcodes_is_active` (`is_active`),
    CONSTRAINT `fk_barcodes_student` FOREIGN KEY (`student_id`) REFERENCES `students`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_barcodes_generated_by` FOREIGN KEY (`generated_by`) REFERENCES `users`(`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_barcodes_regenerated_by` FOREIGN KEY (`regenerated_by`) REFERENCES `users`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `barcode_scans` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `barcode_id` INT UNSIGNED DEFAULT NULL,
    `scanned_value` VARCHAR(100) NOT NULL,
    `student_id` INT UNSIGNED DEFAULT NULL,
    `scan_status` ENUM('success', 'not_found', 'invalid', 'duplicate', 'error') NOT NULL,
    `confidence_score` DECIMAL(5,4) DEFAULT NULL,
    `detection_bbox` JSON DEFAULT NULL,
    `camera_index` INT UNSIGNED DEFAULT NULL,
    `scanned_by` INT UNSIGNED DEFAULT NULL,
    `scanned_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_barcode_scans_barcode_id` (`barcode_id`),
    INDEX `idx_barcode_scans_student_id` (`student_id`),
    INDEX `idx_barcode_scans_scanned_value` (`scanned_value`),
    INDEX `idx_barcode_scans_scan_status` (`scan_status`),
    INDEX `idx_barcode_scans_scanned_at` (`scanned_at`),
    CONSTRAINT `fk_barcode_scans_barcode` FOREIGN KEY (`barcode_id`) REFERENCES `barcodes`(`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_barcode_scans_student` FOREIGN KEY (`student_id`) REFERENCES `students`(`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_barcode_scans_scanned_by` FOREIGN KEY (`scanned_by`) REFERENCES `users`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `assessment_categories` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `category_code` VARCHAR(30) NOT NULL UNIQUE,
    `category_name` VARCHAR(100) NOT NULL,
    `description` TEXT DEFAULT NULL,
    `is_fee` BOOLEAN NOT NULL DEFAULT TRUE,
    `is_discount` BOOLEAN NOT NULL DEFAULT FALSE,
    `display_order` INT UNSIGNED NOT NULL DEFAULT 0,
    `is_active` BOOLEAN NOT NULL DEFAULT TRUE,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_assessment_categories_category_code` (`category_code`),
    INDEX `idx_assessment_categories_display_order` (`display_order`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `assessments` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `student_id` INT UNSIGNED NOT NULL,
    `enrollment_id` INT UNSIGNED DEFAULT NULL,
    `academic_year` VARCHAR(20) NOT NULL,
    `semester` ENUM('1st Semester', '2nd Semester', 'Summer', 'Midyear') NOT NULL,
    `assessment_date` DATE NOT NULL DEFAULT (CURRENT_DATE),
    `status` ENUM('draft', 'finalized', 'paid', 'partial', 'cancelled') NOT NULL DEFAULT 'draft',
    `total_assessed` DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    `total_discounts` DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    `total_payments` DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    `outstanding_balance` DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    `notes` TEXT DEFAULT NULL,
    `created_by` INT UNSIGNED DEFAULT NULL,
    `updated_by` INT UNSIGNED DEFAULT NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_assessments_student_id` (`student_id`),
    INDEX `idx_assessments_enrollment_id` (`enrollment_id`),
    INDEX `idx_assessments_academic_year` (`academic_year`),
    INDEX `idx_assessments_semester` (`semester`),
    INDEX `idx_assessments_status` (`status`),
    CONSTRAINT `fk_assessments_student` FOREIGN KEY (`student_id`) REFERENCES `students`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_assessments_enrollment` FOREIGN KEY (`enrollment_id`) REFERENCES `enrollments`(`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_assessments_created_by` FOREIGN KEY (`created_by`) REFERENCES `users`(`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_assessments_updated_by` FOREIGN KEY (`updated_by`) REFERENCES `users`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `assessment_items` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `assessment_id` INT UNSIGNED NOT NULL,
    `category_id` INT UNSIGNED NOT NULL,
    `description` VARCHAR(255) DEFAULT NULL,
    `original_amount_text` VARCHAR(50) DEFAULT NULL,
    `amount` DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    `is_verified` BOOLEAN NOT NULL DEFAULT FALSE,
    `verified_by` INT UNSIGNED DEFAULT NULL,
    `verified_at` TIMESTAMP NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_assessment_items_assessment_id` (`assessment_id`),
    INDEX `idx_assessment_items_category_id` (`category_id`),
    INDEX `idx_assessment_items_is_verified` (`is_verified`),
    CONSTRAINT `fk_assessment_items_assessment` FOREIGN KEY (`assessment_id`) REFERENCES `assessments`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_assessment_items_category` FOREIGN KEY (`category_id`) REFERENCES `assessment_categories`(`id`) ON DELETE RESTRICT,
    CONSTRAINT `fk_assessment_items_verified_by` FOREIGN KEY (`verified_by`) REFERENCES `users`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `payments` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `assessment_id` INT UNSIGNED NOT NULL,
    `payment_date` DATE NOT NULL DEFAULT (CURRENT_DATE),
    `amount` DECIMAL(12,2) NOT NULL,
    `payment_method` ENUM('cash', 'check', 'bank_transfer', 'online', 'card', 'other') NOT NULL,
    `reference_number` VARCHAR(100) DEFAULT NULL,
    `received_by` INT UNSIGNED DEFAULT NULL,
    `notes` TEXT DEFAULT NULL,
    `is_voided` BOOLEAN NOT NULL DEFAULT FALSE,
    `voided_by` INT UNSIGNED DEFAULT NULL,
    `voided_at` TIMESTAMP NULL,
    `void_reason` TEXT DEFAULT NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_payments_assessment_id` (`assessment_id`),
    INDEX `idx_payments_payment_date` (`payment_date`),
    INDEX `idx_payments_payment_method` (`payment_method`),
    INDEX `idx_payments_is_voided` (`is_voided`),
    CONSTRAINT `fk_payments_assessment` FOREIGN KEY (`assessment_id`) REFERENCES `assessments`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_payments_received_by` FOREIGN KEY (`received_by`) REFERENCES `users`(`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_payments_voided_by` FOREIGN KEY (`voided_by`) REFERENCES `users`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `payment_schedules` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `assessment_id` INT UNSIGNED NOT NULL,
    `schedule_type` ENUM('down_payment', 'midterm', 'prefinal', 'final', 'custom') NOT NULL,
    `due_date` DATE NOT NULL,
    `amount_due` DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    `amount_paid` DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    `status` ENUM('pending', 'partial', 'paid', 'overdue', 'waived') NOT NULL DEFAULT 'pending',
    `notes` TEXT DEFAULT NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_payment_schedules_assessment_id` (`assessment_id`),
    INDEX `idx_payment_schedules_schedule_type` (`schedule_type`),
    INDEX `idx_payment_schedules_due_date` (`due_date`),
    INDEX `idx_payment_schedules_status` (`status`),
    CONSTRAINT `fk_payment_schedules_assessment` FOREIGN KEY (`assessment_id`) REFERENCES `assessments`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `audit_logs` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `user_id` INT UNSIGNED DEFAULT NULL,
    `action` VARCHAR(50) NOT NULL,
    `table_name` VARCHAR(50) DEFAULT NULL,
    `record_id` INT UNSIGNED DEFAULT NULL,
    `old_values` JSON DEFAULT NULL,
    `new_values` JSON DEFAULT NULL,
    `ip_address` VARCHAR(45) DEFAULT NULL,
    `user_agent` TEXT DEFAULT NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_audit_logs_user_id` (`user_id`),
    INDEX `idx_audit_logs_action` (`action`),
    INDEX `idx_audit_logs_table_name` (`table_name`),
    INDEX `idx_audit_logs_record_id` (`record_id`),
    INDEX `idx_audit_logs_created_at` (`created_at`),
    CONSTRAINT `fk_audit_logs_user` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `system_settings` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `setting_key` VARCHAR(100) NOT NULL UNIQUE,
    `setting_value` TEXT DEFAULT NULL,
    `setting_type` ENUM('string', 'integer', 'float', 'boolean', 'json') NOT NULL DEFAULT 'string',
    `description` TEXT DEFAULT NULL,
    `is_public` BOOLEAN NOT NULL DEFAULT FALSE,
    `updated_by` INT UNSIGNED DEFAULT NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_system_settings_setting_key` (`setting_key`),
    CONSTRAINT `fk_system_settings_updated_by` FOREIGN KEY (`updated_by`) REFERENCES `users`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `yolo_models` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `model_name` VARCHAR(100) NOT NULL,
    `version` VARCHAR(20) NOT NULL,
    `file_path` VARCHAR(255) NOT NULL,
    `model_type` ENUM('detection', 'classification', 'segmentation') NOT NULL DEFAULT 'detection',
    `classes` JSON NOT NULL,
    `training_config` JSON DEFAULT NULL,
    `metrics` JSON DEFAULT NULL,
    `status` ENUM('training', 'candidate', 'active', 'archived', 'failed') NOT NULL DEFAULT 'candidate',
    `trained_by` INT UNSIGNED DEFAULT NULL,
    `activated_by` INT UNSIGNED DEFAULT NULL,
    `activated_at` TIMESTAMP NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_yolo_models_status` (`status`),
    INDEX `idx_yolo_models_model_type` (`model_type`),
    CONSTRAINT `fk_yolo_models_trained_by` FOREIGN KEY (`trained_by`) REFERENCES `users`(`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_yolo_models_activated_by` FOREIGN KEY (`activated_by`) REFERENCES `users`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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

CREATE TABLE IF NOT EXISTS `dataset_images` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `file_path` VARCHAR(255) NOT NULL UNIQUE,
    `file_name` VARCHAR(255) NOT NULL,
    `width` INT UNSIGNED NOT NULL,
    `height` INT UNSIGNED NOT NULL,
    `file_size` BIGINT UNSIGNED NOT NULL,
    `split` ENUM('train', 'val', 'test') NOT NULL,
    `has_annotations` BOOLEAN NOT NULL DEFAULT FALSE,
    `annotation_file` VARCHAR(255) DEFAULT NULL,
    `is_validated` BOOLEAN NOT NULL DEFAULT FALSE,
    `validated_by` INT UNSIGNED DEFAULT NULL,
    `validated_at` TIMESTAMP NULL,
    `notes` TEXT DEFAULT NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_dataset_images_split` (`split`),
    INDEX `idx_dataset_images_is_validated` (`is_validated`),
    CONSTRAINT `fk_dataset_images_validated_by` FOREIGN KEY (`validated_by`) REFERENCES `users`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO `system_settings` (`setting_key`, `setting_value`, `setting_type`, `description`, `is_public`) VALUES
('app_name', 'WIT Student ID & Information System', 'string', 'Application display name', TRUE),
('app_version', '1.0.0', 'string', 'Application version', TRUE),
('barcode_prefix', 'WIT:STUDENT:', 'string', 'Prefix for generated barcode values', TRUE),
('default_academic_year', '2024-2025', 'string', 'Default academic year', TRUE),
('default_semester', '1st Semester', 'string', 'Default semester', TRUE),
('scan_duplicate_suppression_seconds', '5', 'integer', 'Seconds to suppress duplicate scans', FALSE),
('yolo_confidence_threshold', '0.5', 'float', 'YOLO detection confidence threshold', FALSE),
('yolo_iou_threshold', '0.45', 'float', 'YOLO IoU threshold for NMS', FALSE),
('camera_width', '1280', 'integer', 'Camera capture width', FALSE),
('camera_height', '720', 'integer', 'Camera capture height', FALSE),
('max_barcode_generation_batch', '100', 'integer', 'Maximum batch size for barcode generation', FALSE);

INSERT IGNORE INTO `assessment_categories` (`category_code`, `category_name`, `description`, `is_fee`, `is_discount`, `display_order`) VALUES
('tuition_fee', 'Tuition Fee', 'Base tuition fee for enrolled units', TRUE, FALSE, 1),
('miscellaneous_fee', 'Miscellaneous Fee', 'General miscellaneous fees', TRUE, FALSE, 2),
('laboratory_fee', 'Laboratory Fee', 'Fees for laboratory subjects', TRUE, FALSE, 3),
('cda_fee', 'CDA Fee', 'Community Development and Activities fee', TRUE, FALSE, 4),
('diploma_fee', 'Diploma Fee', 'Graduation diploma fee', TRUE, FALSE, 5),
('yearbook_fee', 'Yearbook Fee', 'School yearbook fee', TRUE, FALSE, 6),
('photography_fee', 'Photography Fee', 'ID and graduation photography fee', TRUE, FALSE, 7),
('alumni_fee', 'Alumni Fee', 'Alumni association fee', TRUE, FALSE, 8),
('graduation_fee', 'Graduation Fee', 'Graduation ceremony fee', TRUE, FALSE, 9),
('notarial_fee', 'Notarial Fee', 'Notarial services fee', TRUE, FALSE, 10),
('discount_scholarship', 'Scholarship Discount', 'Scholarship grant discount', FALSE, TRUE, 11),
('discount_sibling', 'Sibling Discount', 'Sibling enrollment discount', FALSE, TRUE, 12),
('discount_employee', 'Employee Dependent Discount', 'Employee dependent discount', FALSE, TRUE, 13),
('discount_other', 'Other Discounts', 'Other approved discounts', FALSE, TRUE, 14),
('payment_cash', 'Cash Payment', 'Cash payment received', FALSE, FALSE, 15),
('payment_check', 'Check Payment', 'Check payment received', FALSE, FALSE, 16),
('payment_bank_transfer', 'Bank Transfer', 'Bank transfer payment received', FALSE, FALSE, 17),
('payment_online', 'Online Payment', 'Online payment received', FALSE, FALSE, 18),
('payment_card', 'Card Payment', 'Credit/Debit card payment', FALSE, FALSE, 19),
('adjustment', 'Adjustment', 'Manual adjustment to assessment', TRUE, FALSE, 20);
"""