-- Lazy application-emoji store: per-bot cache of Destiny item-icon app emojis.
--
-- Backs `dd.common.emoji_store.AppEmojiStore`. Discord application emojis only render
-- inline in messages posted by the app that owns them, so the composite PK
-- (`app_id`, `name`) scopes each row to one bot's application-emoji store. The
-- (`app_id`, `last_used`) index drives LRU eviction near the 2000/app cap; eviction is
-- safe because a deleted emoji's CDN image persists, so posted messages keep rendering.
-- The (`emoji_id`) index backs a cross-app lookup by rendered emoji id — the beacon
-- mirror uses it to tell an anchor Destiny-item emoji from any other emoji.

-- Create "app_emoji_cache" table
CREATE TABLE `app_emoji_cache` (
  `app_id` bigint NOT NULL,
  `name` varchar(32) NOT NULL,
  `emoji_id` bigint NOT NULL,
  `icon_url` varchar(256) NOT NULL,
  `last_used` datetime NOT NULL,
  PRIMARY KEY (`app_id`, `name`),
  INDEX `ix_app_emoji_lru` (`app_id`, `last_used`),
  INDEX `ix_app_emoji_emoji_id` (`emoji_id`)
) CHARSET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
