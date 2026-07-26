-- Per-version content snapshot of a mirrored source message.
--
-- Backs `dd.common.schemas.MirrorMessageVersion`. The delivery ledger stores intent,
-- never content, so this is the one place we persist what was actually delivered: the
-- mirror worker snapshots each (`src_msg_id`, `version`) once, as it materializes that
-- version, letting the web mirror-log page re-render every version a follower saw and
-- diff between them. `payload` is a JSON snapshot in Discord's own component/embed shape
-- ("cv2" => {components:[...]}, "classic" => {content, embeds:[...]}); an over-cap post
-- collapses to {truncated:true}. Snapshots are pruned when their source's last delivery
-- row is. No backfill: history begins on the deploy date.

-- Create "mirror_message_version" table
CREATE TABLE `mirror_message_version` (
  `src_msg_id` bigint NOT NULL,
  `version` int NOT NULL,
  `captured_at` datetime NOT NULL,
  `src_guild_id` bigint NULL,
  `kind` varchar(8) NOT NULL,
  `summary` varchar(200) NULL,
  `payload` json NOT NULL,
  PRIMARY KEY (`src_msg_id`, `version`),
  INDEX `ix_mirror_message_version_captured_at` (`captured_at`)
) CHARSET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
