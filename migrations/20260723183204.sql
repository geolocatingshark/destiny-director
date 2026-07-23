-- Add a nullable free-text `value` column to `auto_post_settings`.
--
-- Backs settings that need a value, not just an on/off flag. The first user is
-- `eververse_image_url` (the default banner appended to the bottom of each Eververse
-- autopost, edited from the autopost-settings webpage). NULL for the plain boolean
-- toggle rows, whose state lives in `enabled`.

-- Modify "auto_post_settings" table
ALTER TABLE `auto_post_settings` ADD COLUMN `value` varchar(512) NULL;
