-- Modify "mirrored_channel" table
ALTER TABLE `mirrored_channel` RENAME COLUMN `legacy_error_rate` TO `legacy_disable_strikes`;
