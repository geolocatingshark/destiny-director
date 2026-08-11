# Manifest → Postgres: baked, typed, ingested out-of-band

## Status: design proposal (2026-08-11). Assumes the prod Postgres cutover is done.

Replace the in-process Destiny manifest pipeline (download → extract → sqlite +
`LazyTable` + the `item_index` full-table walk) with a typed projection in the
production Postgres, maintained by a scheduled ingest that runs outside every
serving process. Serving processes stop carrying manifest machinery entirely.

Related: supersedes `plans/manifest_backup_in_git.md` (the DB *is* the fallback —
last-good ingest survives deploys and Bungie outages). Unblocks every
beacon/anchor topology option (serverless web, cron feeds, full merge) by
removing the one memory spike they all had to route around.

---

## 1. The shape: a Railway cron service

**One new Railway service, same repo, start command `python -m dd.manifest_ingest`,
cron schedule hourly.** It checks manifest currency and exits; on a version change
it does the full ingest first. No volume, no domain, no serverless toggle.

Why this shape and not the others considered:

| Shape | Verdict |
| --- | --- |
| **Cron service (chosen)** | Resident cost **zero**. Runs ~30 s/hour for the currency check (one Bungie metadata call + one SELECT), ~2–5 min a handful of times a month for real ingests. ≈ **$0.01/mo**. Survives any future bot topology, including anchor ceasing to exist. |
| Subprocess inside anchor | Same GB-hours (spike is transient either way), avoids a new service — but ties ingest to a resident process the other plans are trying to shrink or delete, and dies with it. Keep as the fallback if a fifth service is unwanted. |
| Always-on worker | Pays ~$0.5–1/mo to sleep between hourly ticks. Strictly worse than cron. |
| Serverless service + external wake | Cold-start machinery for a job with no latency requirement. Complexity for nothing. |
| GitHub Actions scheduled ingest | Zero Railway compute, but requires exposing prod Postgres publicly and parking its credentials in GH secrets, and GH cron drifts by up to ~15 min. Rejected on the credential exposure alone. |

Railway cron semantics that fit: UTC, 5-min minimum granularity (hourly is fine),
overlapping runs are **skipped, not stacked** — and the ingest also takes a
`pg_advisory_lock` (the pattern `migrations/env.py` already uses) as belt and
braces.

**The entrypoint must not import the bot.** `dd/manifest_ingest/` imports
aiohttp, zipfile, sqlite3, SQLAlchemy/psycopg — no hikari, no lightbulb, no
extensions. That keeps the hourly fast path small (~50–80 MB for seconds) and
fast to start.

## 2. Schema

Version-pinned rows; exactly one current version; flip is a single transaction.

    manifest_versions
      id            serial PK
      version       text UNIQUE          -- Bungie's version string
      ingested_at   timestamptz
      current       boolean              -- partial unique index: at most one true

    manifest_items                        -- weapons + armor projection
      version_id    int  → manifest_versions
      hash          bigint               -- Bungie's unsigned hash as-is
      name          text
      name_lower    text                 -- indexed (version_id, name_lower)
      icon          text                 -- CDN path; emoji store reads this
      item_type     smallint
      tier_type_name text
      class_type    smallint
      season_number smallint NULL
      collectible_hash bigint NULL
      raw           jsonb                -- escape hatch; see §2a
      PK (version_id, hash)

    manifest_perks(version_id, hash, name, icon)            -- DestinySandboxPerkDefinition
    manifest_item_perks(version_id, item_hash, ord, perk_hash)
    manifest_stats(version_id, hash, name)                  -- DestinyStatDefinition
    manifest_item_stats(version_id, item_hash, stat_hash, value)
    manifest_vendors(version_id, hash, name, ...)           -- + location/destination fields
    manifest_destinations(version_id, hash, name)
    manifest_presentation_edges(version_id, child_hash, parent_hash)
    manifest_equipment_slots(version_id, hash, name)
    manifest_collectibles(version_id, hash, item_hash, parent_node_hashes ...)

Notes:

- **Hashes stay unsigned BIGINT.** `_to_signed_id` is a sqlite storage artifact;
  it dies with the sqlite.
- **Column set = what consumers read today**, nothing speculative. The current
  read surface (from `models.py`, `item_index`, xur/eververse/weekly-reset/
  trials): displayProperties.name/icon, itemType, tierTypeName, classType,
  stats, perks/reusablePlugs, traitIds, collectible → parentNodeHashes → season,
  vendor locations/destinations, seasonNumber.
- Readers resolve `current` **once per operation** (one post construction, one
  HTTP request) and pin that `version_id` for every query in it — a mid-operation
  flip can never mix seasons inside one post. No cache invalidation protocol
  needed; a 60 s per-process memo of the current id is an optional nicety.

### 2a. The `raw` escape hatch — bounded

`raw` holds the full manifest JSON **only for rows already in the projection**
(weapons/armor + touched vendors), not all ~39k items. Prototyping a new field is
a jsonb read; a migration then formalizes it as a column. Phase 0 measures the
real size — if the TOASTed jsonb lands over ~150 MB on disk, strip the known-huge
subtrees nothing plausibly reads (e.g. `investmentStats`, `sockets` internals)
rather than dropping the column. This is DB *disk* (cents), not DB RAM.

## 3. The ingest, step by step

1. GET manifest metadata (one small call) → version string.
2. `SELECT version FROM manifest_versions WHERE current` — equal → **exit 0**.
   This is the hourly fast path: seconds.
3. Download the zip to `/tmp`, extract the world-content sqlite (ephemeral disk;
   nothing persists between runs — deliberately, so there is no cache to go
   stale and no volume to pay for or be pinned to one service by).
4. Walk the allowlisted tables, build typed rows, and in **one transaction**:
   insert the new `manifest_versions` row (`current=false`) → bulk-insert all
   projection rows (psycopg3 COPY / batched executemany) → flip `current`
   old→new → delete versions older than the one just superseded (**keep exactly
   2**: instant rollback is `UPDATE … SET current` back).
5. Failure anywhere → transaction rolls back, old version keeps serving,
   exit non-zero, alert to the alerts channel (plain Discord webhook URL in the
   cron's env — no bot token in this service). Next hourly tick retries.
   Staleness backstop on the serving side: control panel warns when
   `ingested_at` is older than ~8 days.

Peak memory (full-table walk + parse) lives in this process for minutes,
billed ~pennies, and never in a serving process again.

## 4. Serving-side changes

- `item_index` → SQL: autocomplete and light.gg resolution become
  `WHERE version_id = ? AND name_lower LIKE ?` (+ substring fallback) with the
  season/collectible tiebreak as ORDER BY. The in-memory index, its build, and
  its `StartedEvent` warm are deleted. (`pg_trgm` is available headroom if
  substring search ever needs an index; at ~10–20k rows it won't.)
- `LazyTable` call sites → small repository functions (`items_by_hashes`,
  `vendor`, `season_for_collectible`, …) returning the existing `models.py`
  types, hydrated from typed columns with `raw` as fallback.
- Missing hash (Bungie hotfix shipped an item mid-week) → `None`, flowing into
  the same degrade paths that today handle a cold index; the hourly cron bounds
  the window to ≤1 h.
- Emoji store: unchanged — it only ever needed the icon path.

**Deleted from the bots when done:** the download/extract path, `LazyTable`,
`item_index`, prewarm hooks, `_to_signed_id`, the manifest disk footprint, and
the per-deploy ~200 MB re-download on Railway's ephemeral filesystem.

## 5. Phases (independently shippable)

- **Phase 0** — schema (alembic) + ingest service, dual-run. Bots untouched. A
  comparison check (ingested rows vs live `LazyTable` reads for a sample of
  hashes) proves fidelity before anything switches. Measure `raw` size.
- **Phase 1** — `item_index` reads from Postgres. This alone removes the
  resident index and the full-table parse spike from anchor.
- **Phase 2** — port `LazyTable` consumers (xur, eververse, weekly-reset option
  pools, trials, season resolution) to repositories.
- **Phase 3** — delete the in-bot manifest pipeline; close
  `plans/manifest_backup_in_git.md` as superseded.

## 6. Cost accounting (Railway)

| Line | Δ |
| --- | --- |
| New cron service | +≈$0.01/mo (hourly seconds + a few minutes per real ingest) |
| Anchor resident | −(item_index residency + parse spikes); no volume needed |
| DB RAM | ~unchanged — few-MB working set under an already-allocated cache |
| DB disk | +tens of MB (two versions retained); cents |
| Deploy time | improves — no manifest re-download on boot |

The direct saving is modest; the structural saving is that serverless web,
cron-run feeds, and the full merge all stop having a manifest problem.

## 7. Open questions

- **The Pi (`two`).** No cron scheduler there and none wanted on the boot path.
  The test bot already degrades keyless; when real manifest data is needed on
  the Pi, run the same module by hand (`podman run --rm <image> python -m
  dd.manifest_ingest` against the local Postgres). Whether that deserves a
  `dd-ctl` verb is a separate decision — the verb list is deliberately fixed.
- **Alert channel plumbing** — webhook URL vs reusing a bot token in the cron
  env. Webhook proposed (least credential reach for a new service).
- **Localization** — everything above assumes `en` only, as today.
