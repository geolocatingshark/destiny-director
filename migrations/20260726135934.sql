-- Append-only record of each completed mirror operation (create / update / delete).
--
-- Backs `dd.common.schemas.MirrorOperationLog`. The delivery ledger only holds a
-- source's current converged state, so it can't answer "how did each individual
-- operation do?". The drain watcher writes one row here when an operation's fan-out
-- drains, capturing that operation's own final counts + timing (the numbers the retired
-- Discord progress card showed live). In-flight ops read the live ledger; settled ops
-- read this. Pruned alongside the source's delivery rows. No backfill: history begins on
-- the deploy date.

-- Create "mirror_operation_log" table
CREATE TABLE `mirror_operation_log` (
  `id` int NOT NULL AUTO_INCREMENT,
  `src_msg_id` bigint NOT NULL,
  `src_ch_id` bigint NULL,
  `op_type` varchar(8) NOT NULL,
  `version` int NULL,
  `started_at` datetime NOT NULL,
  `finished_at` datetime NOT NULL,
  `total` int NOT NULL,
  `delivered` int NOT NULL,
  `failed` int NOT NULL,
  `cancelled` int NOT NULL,
  `attempts` int NOT NULL,
  `failure_refs` json NULL,
  PRIMARY KEY (`id`),
  INDEX `ix_mirror_operation_log_finished_at` (`finished_at`),
  INDEX `ix_mirror_operation_log_src_msg_id` (`src_msg_id`)
) CHARSET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
