-- Daily snapshot of autopost reach: active destination counts per feed.
--
-- Backs `dd.common.schemas.AutopostDailyStat`. One row per (`date`, `feed`, `kind`),
-- where `kind` is "follow" (native Discord channel-follows) or "mirror" (legacy
-- mirrored channels). A daily beacon task snapshots the current `mirrored_channel`
-- reach into this table, so the time series survives later channel removals (a deleted
-- `mirrored_channel` row leaves the historical snapshot intact). The composite PK makes
-- the snapshot idempotent — a same-day re-run overwrites `count` via
-- `ON DUPLICATE KEY UPDATE`. No backfill: the series begins on the deploy date.

-- Create "autopost_daily_stat" table
CREATE TABLE `autopost_daily_stat` (
  `date` date NOT NULL,
  `feed` varchar(32) NOT NULL,
  `kind` varchar(8) NOT NULL,
  `count` bigint NOT NULL,
  PRIMARY KEY (`date`, `feed`, `kind`)
) CHARSET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
