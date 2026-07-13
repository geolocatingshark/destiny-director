-- Mirror v3: single-worker delivery ledger.
--
-- Replaces the pre-rewrite `mirrored_message` table + the strike columns with the
-- `mirror_delivery` ledger and a per-pair reachability clock. This is the only mirror
-- schema migration prod ever applies (the intermediate mirror-v2 migrations were never
-- released to prod and were collapsed into this one).

-- Create "mirror_delivery" table
CREATE TABLE `mirror_delivery` (
  `src_msg_id` bigint NOT NULL,
  `dest_ch_id` bigint NOT NULL,
  `src_ch_id` bigint NOT NULL,
  `dest_msg_id` bigint NULL,
  `desired_version` int NOT NULL,
  `applied_version` int NOT NULL,
  `deleted` bool NOT NULL,
  `state` varchar(16) NOT NULL,
  `crosspost_state` varchar(16) NOT NULL,
  `attempts` int NOT NULL,
  `due_at` datetime NOT NULL,
  `last_error_ref` varchar(8) NULL,
  `last_error_class` varchar(12) NULL,
  `last_error_msg` varchar(256) NULL,
  `created_at` datetime NOT NULL,
  `finished_at` datetime NULL,
  PRIMARY KEY (`src_msg_id`, `dest_ch_id`),
  INDEX `ix_mirror_delivery_state_due` (`state`, `due_at`),
  INDEX `ix_mirror_delivery_crosspost_due` (`crosspost_state`, `due_at`),
  INDEX `ix_mirror_delivery_created_at` (`created_at`)
) CHARSET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
-- Backfill from mirrored_message as DELIVERED (applied=desired=1), crosspost already
-- resolved (NOT_APPLICABLE). GROUP BY dedupes duplicate (source_msg, dest_ch) pairs;
-- MAX(dest_msg) picks the newest dest message (snowflakes are time-ordered).
INSERT INTO `mirror_delivery`
  (`src_msg_id`, `dest_ch_id`, `src_ch_id`, `dest_msg_id`,
   `desired_version`, `applied_version`, `deleted`, `state`, `crosspost_state`,
   `attempts`, `due_at`, `created_at`, `finished_at`)
SELECT mm.`source_msg`, mm.`dest_ch`, MAX(mm.`src_ch`),
       MAX(mm.`dest_msg`), 1, 1, 0, 'DELIVERED', 'NOT_APPLICABLE', 1,
       MAX(mm.`creation_datetime`), MAX(mm.`creation_datetime`),
       MAX(mm.`creation_datetime`)
FROM `mirrored_message` mm
GROUP BY mm.`source_msg`, mm.`dest_ch`;
-- Drop "mirrored_message" (subsumed by the ledger)
DROP TABLE `mirrored_message`;
-- Strike columns replaced by the reachability sweep; add its per-pair clock.
ALTER TABLE `mirrored_channel` DROP COLUMN `legacy_disable_strikes`, DROP COLUMN `legacy_failing_since`, ADD COLUMN `unreachable_since` datetime NULL AFTER `role_mention_id`;
