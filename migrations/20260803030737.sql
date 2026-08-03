-- Create "cv2_draft" table
CREATE TABLE `cv2_draft` (
  `id` varchar(36) NOT NULL,
  `created_by` bigint NOT NULL,
  `action` varchar(8) NOT NULL,
  `nodes` json NOT NULL,
  `guild_id` bigint NULL,
  `target_channel_id` bigint NULL,
  `target_message_id` bigint NULL,
  `published_message_id` bigint NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  INDEX `ix_cv2_draft_created_at` (`created_at`)
) CHARSET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
