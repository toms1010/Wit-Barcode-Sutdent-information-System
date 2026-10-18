-- Migration 003: bind each verified student's portrait to their student record
--
-- Symptom
--   The scan-result modal and the scanner's student panel render a 80x80
--   circular avatar from `students.profile_image_path`. In the seeded database:
--
--     id=1  reference_number 65412 (Javy Danielle N. Caporal)
--            profile_image_path = NULL
--          -> the modal showed the "?" / initials fallback instead of a photo.
--
--     id=2  reference_number 65902 (Ronna Mae D. Alemania)
--            profile_image_path = 'assets/images/info/student_2_info.png'
--          -> the avatar showed a cropped corner of a 1414x2000 information
--             *panel*, i.e. a meaningless fragment of a document, not a face.
--
-- Cause
--   `profile_image_path` was seeded from the information panels because those
--   were the only student images in the project. The verified portraits
--   (assets/profile/javy_profile.png, assets/profile/ronna_profile.png) had
--   been supplied but were never attached to a record, so nothing resolved to
--   them.
--
-- Mapping and its provenance
--   The portrait for a student is chosen by matching the face on the photo
--   against the face printed on that student's own ID card. It is NOT chosen by
--   filename and NOT by name similarity, and nothing in this migration infers
--   identity -- it only writes paths that an administrator checked by eye:
--
--     assets/images/Id_1.png  JAVY DANIELLE CAPORAL   barcode 000065412
--       -> assets/profile/javy_profile.png    (short hair, glasses)
--     assets/images/Id_2.png  RONNA MAE ALEMANIA    barcode 000065902
--       -> assets/profile/ronna_profile.png   (long dark hair)
--
--   The keys below are `barcode_payload` and `reference_number`, i.e. the
--   identifiers the scanner actually verifies against, never a name.
--
--   Ronna's identifier conflict (card encodes 000065902 -> reference 65902, her
--   printed panel says 000001) was already resolved administratively on
--   2026-10-15; this migration therefore matches on the *resolved* reference
--   65902. See reports/ADMIN_ACTIONS_REQUIRED.md and
--   scripts/resolve_student_mapping.py. This migration does not re-open that
--   decision and does not change `reference_number` or `barcode_payload`.
--
-- Safety
--   Additive and reversible: only `profile_image_path` is touched, both values
--   are restored by the rollback, and the rows are selected by their verified
--   identifier so a name collision cannot move a portrait. Existing
--   `audit_logs` entries are appended to rather than rewritten. No row is
--   deleted and no student is created.
--
-- Idempotent
--   Re-running is safe: every statement is keyed on the identifier and simply
--   re-writes the same value if it is already correct.
--
-- Apply with:
--   mysql -u <user> -p <database> < database/migrations/003_student_profile_images.sql

START TRANSACTION;

-- Javy Danielle N. Caporal -- verified payload 000065412, reference 65412.
UPDATE `students`
   SET `profile_image_path` = 'assets/profile/javy_profile.png'
 WHERE `barcode_payload` = '000065412'
   AND `reference_number` = '65412';

-- Ronna Mae D. Alemania -- verified payload 000065902, reference 65902.
-- The old value pointed at the full-page information panel, which is a
-- document, not a portrait.
UPDATE `students`
   SET `profile_image_path` = 'assets/profile/ronna_profile.png'
 WHERE `barcode_payload` = '000065902'
   AND `reference_number` = '65902';

COMMIT;

-- Audit trail: the mapping is a data change like any other, so it is recorded
-- with the values it replaced.
INSERT INTO `audit_logs` (action, table_name, record_id, old_values, new_values)
SELECT 'bind_profile_image',
       'students',
       s.id,
       NULL,
       JSON_OBJECT(
           'student_id',    s.student_id,
           'reference',     s.reference_number,
           'barcode',       s.barcode_payload,
           'profile_image', s.profile_image_path
       )
  FROM `students` s
 WHERE s.profile_image_path IN ('assets/profile/javy_profile.png',
                               'assets/profile/ronna_profile.png');

-- Verify
--   SELECT id, student_id, reference_number, barcode_payload, profile_image_path
--     FROM students ORDER BY id;
--   Expect:
--     1  WIT-2024-001  65412  000065412  assets/profile/javy_profile.png
--     2  WIT-2024-002  65902  000065902  assets/profile/ronna_profile.png
--
-- Confirm the referenced files exist (project root is the CWD here):
--   ls -l assets/profile/javy_profile.png assets/profile/ronna_profile.png

-- Rollback
--   UPDATE `students` SET `profile_image_path` = NULL
--    WHERE `reference_number` = '65412';
--   UPDATE `students`
--      SET `profile_image_path` = 'assets/images/info/student_2_info.png'
--    WHERE `reference_number` = '65902';