-- Add the covering index for the derived auto-disable sweep
-- (MirroredChannel.disable_failing_mirrors): state-then-pair-then-finished_at, so
-- both the DELIVERED last-success anchor scan and the FAILED streak scan are served
-- without a full-table scan.
--
-- Ships as its own migration (not folded into 20260710235540, the create-table
-- migration) because that one had already been applied to environments — editing an
-- applied migration would diverge the file/checksum from the live schema.
CREATE INDEX `ix_mirror_delivery_state_pair_finished` ON `mirror_delivery` (`state`, `src_ch_id`, `dest_ch_id`, `finished_at`);
