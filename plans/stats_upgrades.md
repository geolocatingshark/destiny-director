# Stats upgrades — daily autopost tracking + web stats dashboard

Move the stats surface off Discord and onto the anchor web UI, with charts of
**command usage** and **autopost reach** over time at daily / weekly / monthly
resolution. Add a daily snapshot of autopost destination counts so the reach series
has a data source.

## Decisions (confirmed with owner 2026-07-19)

- **Replace, don't duplicate.** Once the web page covers them, remove the Discord
  `/stats` subcommands (`populations`, `server_list`, `autoposts`, `commands`). Keep the
  usage-tracking *hook* — it's the data source, not a report.
- **Web page scope = everything.** Four sections: command usage over time, autopost
  reach over time (+ current totals), server populations, server list.
- **Snapshot-only autopost history.** A daily aggregate snapshot table per
  `(date, feed, kind)`. No per-channel `followed_at` column. The series naturally
  "starts on the date of deploy" (first snapshot row is written on the first daily run).
  Per-channel join dates and precise "new follows/day" are explicitly out of scope.
- **Chunked for a Pro/Opus budget.** The work is split into small, independently
  committable chunks (see the ledger below). **Each chunk lands with `make check` green
  and its own commit**, so a usage wall mid-feature is a clean stopping point — resume at
  the next unchecked box. Aim to keep each chunk well under one 5-hour Opus window.

## What already exists (don't rebuild)

- **`CommandUsage`** (`dd/common/schemas.py:1713`) already stores **daily** buckets
  `(command_name, date, count)`, incremented race-free by the `track_command_usage`
  hook (`dd/beacon/extensions/statistics.py:49`, wired client-wide in
  `dd/beacon/__main__.py:52`). **No schema change for command usage.** Weekly/monthly are
  derived by aggregating the daily rows.
- **`MirroredChannel`** (`dd/common/schemas.py:144`) holds current src→dest pairs with a
  `legacy` flag (legacy = "mirror", non-legacy = "follow") and `count_dests(src_id,
  legacy_only=…)` (line 338). Source for the daily snapshot and the "current totals"
  table — but it has **no history**.
- **`ServerStatistics`** (`dd/common/schemas.py:1612`) — `fetch_server_populations()`,
  `fetch_server_ids()`. Powers the populations + server-list sections.
- **Scheduling**: register a daily job with `@loader.task(lb.uniformtrigger(hours=24,
  wait_first=False), max_failures=-1)` — copy `prune_message_db`
  (`dd/beacon/extensions/mirror.py:1181`).
- **Web UI**: aiohttp, no template engine. Feature modules call `web.register_routes(...)`
  + `web.register_card(Card(...))`; pages are static `web_static/*.html` with
  `<!--__PLACEHOLDER__-->` markers replaced by `html.escape`-d fragments. All routes are
  auth-gated centrally by the web-auth middleware. Copy `autopost_settings.py` +
  `web_static/autopost_settings.html`.

## Chunked execution ledger

Do these in order (each depends on the ones above it). Check the box when the chunk is
committed with `make check` green. The `≈window` column is a rough share of one 5-hour
Opus window on Pro — small on purpose, with headroom for the debug/`make check` tail.

| # | Chunk | Deliverable (one commit, green) | Depends | ≈window |
|---|-------|----------------------------------|---------|---------|
| ☑ 1 | **Snapshot table** | `AutopostDailyStat` model + `record`/`fetch_series` methods + migration + schema tests | — | ~20% |
| ☑ 2 | **Daily snapshot job** | `@loader.task` writing the snapshot + its unit test (data starts accruing) | 1 | ~15% |
| ☑ 3 | **Data endpoint + shell** | `GET /stats/data` JSON + `GET /stats` HTML shell + homepage card. Shipped with the leaderboard / current-totals / populations-summary / server tables already rendered (client-side), so chunks 5–7 shrink to just their charts. | 1 | ~25% |
| ☑ 4 | **Chart harness + command usage** | `charts.js` (DDCharts.lineChart + bucketByResolution) + daily/weekly/monthly toggle; command-usage trend chart wired (single-series accent line). Palette validated (accent pink passes dark checks). | 3 | ~35% |
| ☑ 5 | **Autopost reach chart** | 2-series follow/mirror line (`#autopostsChart`), palette validated (pink + accent-strong blue). Reach aggregated by period-**last** snapshot (stock, not flow). | 4 | ~15% |
| ☑ 6 | **Populations chart** | Log-band column chart (`#populationsChart`) via new `DDCharts.barChart`; summary tiles already done | 4 | ~15% |
| ☐ 7 | ~~Server list section~~ | **Done in chunk 3** (searchable `#serversTable`). No separate work unless we later add server names. | — | — |
| ☐ 8 | **Remove Discord `/stats`** | Delete the four subcommands + dead chart helpers + test cleanup (keep the tracking hook) | 4,5,6 | ~20% |

Natural early stops: after **#2** history is being recorded (the time-sensitive part —
do this first); after **#3** the page exists; after **#4** the harness is reusable so
#5–#7 are cheap repeats; **#8** last, only once the web page fully covers the commands.

### Chunk 1 — `AutopostDailyStat` (data source)

`dd/common/schemas.py`, `CommandUsage`-style (classic `Column`, `eager_defaults`, MySQL
upsert):

```python
class AutopostDailyStat(Base):
    __tablename__ = "autopost_daily_stat"
    __mapper_args__ = {"eager_defaults": True}

    date = Column("date", Date, primary_key=True)                 # UTC daily bucket
    feed = Column("feed", String(length=32), primary_key=True)    # cfg.followables key
    kind = Column("kind", String(length=8), primary_key=True)     # "follow" | "mirror"
    count = Column("count", BigInteger, nullable=False, default=0)
```

Methods (`@ensure_session(db_session)`):
- `record(date, feed, kind, count)` — **snapshot upsert** (overwrite, not increment):
  `mysql_insert(...).on_duplicate_key_update(count=insert().inserted.count)` so a same-day
  re-run corrects the value rather than doubling it. (Contrast `CommandUsage.increment`,
  which is `count = count + 1`.)
- `fetch_series(since: date | None)` — `(date, feed, kind, count)` rows on/after `since`,
  ordered by date, for the charts.

**Migration**: edit the model → `make atlas-migration-plan` → hand-edit the generated
`migrations/<timestamp>.sql` to add the descriptive `--` header comment → `make
atlas-migration-apply`. **Create-table only, no backfill.**

**Tests** (`integration` marker): `record` upsert idempotency (same-day re-run overwrites,
doesn't double); `fetch_series` filtering/ordering. Verify the MySQL
`on_duplicate_key_update` path with `TEST_USE_MYSQL=1`.

### Chunk 2 — daily snapshot task (beacon)

Add to `dd/beacon/extensions/statistics.py` (co-located with the tracking hook) or a small
new `dd/beacon/extensions/stats_snapshot.py`. Mirror `prune_message_db`:

```python
@loader.task(lb.uniformtrigger(hours=24, wait_first=False), max_failures=-1)
async def snapshot_autopost_reach():
    today = dt.datetime.now(tz=dt.UTC).date()
    for feed, src_id in cfg.followables.items():
        mirrors = await MirroredChannel.count_dests(src_id, legacy_only=True)
        follows = await MirroredChannel.count_dests(src_id, legacy_only=False) - mirrors
        await AutopostDailyStat.record(today, feed, "mirror", mirrors)
        await AutopostDailyStat.record(today, feed, "follow", follows)
```

**First confirm `count_dests` semantics** — whether `legacy_only=False` returns *all*
dests or *non-legacy only* — and adjust the follow/mirror split accordingly.
`wait_first=False` also snapshots at boot (fine — idempotent upsert). Unit-test the split
against a seeded `MirroredChannel` fixture.

### Chunk 3 — data endpoint + page shell (anchor)

New extension `dd/anchor/extensions/stats_page.py` + `web_static/stats.{html,css}`,
modelled on `autopost_settings.py`. Both routes sit behind the existing auth middleware.

- `GET /stats/data` → JSON:
  ```jsonc
  {
    "commands":   [{"name": "...", "date": "YYYY-MM-DD", "count": N}, ...],  // CommandUsage.fetch_daily
    "autoposts":  [{"date": "YYYY-MM-DD", "feed": "...", "kind": "follow|mirror", "count": N}, ...],
    "current":    [{"feed": "...", "follows": N, "mirrors": N}, ...],         // MirroredChannel.count_dests now
    "populations":[{"id": "...", "population": N}, ...],                      // ServerStatistics
    "servers":    [{"id": "...", "name": "..."}, ...]                         // fetch_server_ids + names
  }
  ```
  Serve **daily** granularity; the client re-aggregates to weekly/monthly.
- `GET /stats` → render `web_static/stats.html`. **This chunk ships tables only** (raw
  numbers / current totals) so the page is live and green before any chart code.
- `web.register_card(Card("Statistics", "Command & autopost trends", "/stats"))`.
- Thin route test: `/stats/data` returns well-formed JSON for a seeded DB.

### Chunk 4 — chart harness + command-usage section

**Read the `dataviz` skill first** (palette, mark specs, light/dark, accessibility). No
chart lib exists in `web_static/vendor/`; build a **dependency-free inline-SVG** line/area
chart helper in `stats.js` (hand-rolled — matches the repo's minimal-dep posture and stays
inside the auth boundary; uPlot is the fallback if a lib is ever wanted). Include the
daily/weekly/monthly **resolution toggle** (client-side re-bucketing: daily → ISO-week →
calendar-month sums). Wire section 1: command-usage trend (total + top-N) plus the
leaderboard table the old `/stats commands` showed. The harness built here is reused by
chunks 5–6.

### Chunk 5 — autopost reach section

Reuse the harness: stacked series by feed, follow vs mirror, same resolution toggle; plus
the current-totals table (replaces `/stats autoposts`).

### Chunk 6 — populations section

Top-7 bar + log-scale distribution (replaces `/stats populations`).

### Chunk 7 — server-list section

Searchable server table (replaces `/stats server_list`, which was a `.txt` dump).

### Chunk 8 — remove the Discord `/stats` group

In `dd/beacon/extensions/statistics.py`:
- Delete `stats_command_group` + the four subcommand classes (`PopulationsCommand`,
  `ServerListCommand`, `MirrorStatsCommand`, `CommandUsageStatsCommand`) and the
  `loader.command(stats_command_group, …)` registration (line 388).
- Delete the now-dead pure text-chart helpers (`_bar`, `_downsample`, `_sparkline`,
  `_delta`, `_truncate`, `_build_command_chart`, `_build_totals_chart`, ~lines 223-347)
  and update/remove `dd/beacon/tests/test_command_stats.py`.
- **Keep** `track_command_usage`, `_should_track`, `_EXCLUDED_ROOTS`, and the `__main__.py`
  wiring — the live data source. (`"stats"` in `_EXCLUDED_ROOTS` is now moot; can drop.)

## Cross-cutting testing

- `make check` (lint + ty + `pytest -m "not discord"`) green **per chunk** before commit.
- DB tests use the `integration` marker (SQLite default; `TEST_USE_MYSQL=1` for the MySQL
  upsert path).
- Optional JS unit coverage for the daily→weekly→monthly bucketing — see
  `plans/js_unit_tests.md`.

## Rollout

- Branch off `dev`, descriptive name (e.g. `feat/web-stats-dashboard`). Chunks 1–2 (the
  migration + snapshot job) go first so the reach series starts accruing from day one; the
  empty series just renders as empty charts until data builds up.
- Commit per chunk. Ship to `dev` when a coherent set is green.
- **Do not deploy to prod without explicit owner confirmation.**

## Deferred / out of scope

- Per-channel `followed_at` / join-date column and "new follows per day" (owner chose
  snapshot-only).
- Autopost **message volume** over time (this tracks reach = active destinations, not
  messages sent). Could be added later from `mirror_delivery` DELIVERED counts if wanted.
- **Server names** on the populations / server-list sections. `ServerStatistics` stores
  only `(id, population)`, and the id list is the *beacon's* guilds, whose names the
  anchor web process can't resolve without slow per-guild Discord fetches (the reason the
  old `/stats populations` command was slow). The web page shows ids + populations. If
  names are wanted, options are: persist a name column on `ServerStatistics` (refreshed by
  the existing `refresh_server_sizes` task) or a best-effort anchor-cache lookup.
- **`/stats/data` payload note:** there is no separate `servers` array — the server-list
  section derives from `populations` (`[id, population]`, id as a string) client-side.
