-- Modify "mirrored_channel" table
ALTER TABLE `mirrored_channel` ADD COLUMN `legacy_failing_since` datetime NULL AFTER `legacy_error_rate`;
