# Plan: Record, store & present command-usage statistics (beacon)

## Context

`dd.beacon` exposes a set of user-facing slash commands (`/xur`, `/lost sector`,
`/nightfall`, etc.) but has no visibility into how often each is actually used.
We want to **record** every invocation of the user-facing commands, **store** the
counts durably, and **present** them (a leaderboard of command usage). `/autopost`
and the owner/admin-only commands are explicitly out of scope.

The codebase already has a `/stats` command group (`dd/beacon/extensions/statistics.py`,
owner-only, control-guild) for population/autopost stats — command-usage stats are a
natural new member of that group. There is currently **no** command-invocation
tracking anywhere.

## Design overview

Three pieces:

1. **Record** — a single client-wide lightbulb `PRE_INVOKE` hook intercepts every
   command, filters to the user-facing set, and increments a counter.
2. **Store** — a new `CommandUsage` table in `dd/common/schemas.py`, written via a
   MySQL upsert (`INSERT ... ON DUPLICATE KEY UPDATE count = count + 1`).
3. **Present** — a new owner-only `/stats commands` subcommand rendering a
   leaderboard embed, consistent with the existing `/stats` commands.

### Why a client-wide hook (not per-command edits)

`lb.client_from_app(..., hooks=[...])` applies a hook to **every** command on the
client (confirmed: `dd/anchor/__main__.py:51-55` uses this for `owner_only`;
lightbulb `client.py:111`). One hook = zero per-extension edits and automatic
coverage of future commands. The filter (below) decides what actually gets counted.

The hook runs at `PRE_INVOKE` with `skip_when_failed=True`:
- Runs **after** `CHECKS` pass, so owner-gate / permission rejections are **not**
  counted (verified in `lightbulb/commands/execution.py:_run`).
- `skip_when_failed=True` means it's skipped if any earlier step failed.
- Fires once per leaf-command pipeline → exactly one increment per invocation
  (groups/subgroups don't run their own pipeline).
- The DB write is wrapped in `try/except` and **never** propagates — a stats write
  failure must not break the user's command.

### Filter (which commands count) — denylist

Per the decision, track **all** user-facing slash commands *including* admin-created
custom user-commands, excluding only `/autopost` and the owner/admin command groups.
This is a denylist keyed on the **top-level** command name.

Factor the decision into a pure, testable helper (no DB, no `ctx`):

```python
# top-level command names that are NOT user-facing usage
_EXCLUDED_ROOTS = frozenset({"autopost", "stats", "mirror", "testing", "command"})

def _should_track(qualified_name: str, command_type: h.CommandType) -> bool:
    if command_type is not h.CommandType.SLASH:   # skip message/user context menus
        return False
    return qualified_name.split(" ", 1)[0] not in _EXCLUDED_ROOTS
```

The hook reads `ctx.command_data.qualified_name` (e.g. `"lost sector"`,
`"autopost xur"`) and `ctx.command_data.type`. `split(" ", 1)[0]` reduces any
subcommand to its top-level group so `autopost *`, `stats *`, `mirror *`,
`testing *`, `command *` are all excluded. Custom user-commands have admin-chosen
top-level names that can't collide with the excluded groups (name collision would
be rejected at registration), so they're tracked automatically. Tracked names are
the leaf `qualified_name`, so subcommands are distinguished (`lost sector` vs
`ls today`; `twab` vs `twid` aliases are stored separately).

## Storage model — daily per-command buckets

Bounded growth (commands × days ≈ a few thousand rows/year); supports both all-time
totals (SUM over all dates) and time windows (SUM where `date >= since`).

New model in `dd/common/schemas.py` (follow the existing `Column(...)` style, e.g.
`ServerStatistics` / `MirroredChannel`):

```python
class CommandUsage(Base):
    __tablename__ = "command_usage"
    __mapper_args__ = {"eager_defaults": True}

    command_name = Column("command_name", String(length=128), primary_key=True)
    date = Column("date", Date, primary_key=True)          # daily bucket, UTC
    count = Column("count", BigInteger, nullable=False, default=0)

    @classmethod
    @ensure_session(db_session)
    async def increment(cls, command_name: str, *, session=_UNSET) -> None:
        today = dt.datetime.now(tz=dt.UTC).date()
        stmt = mysql_insert(cls).values(
            command_name=command_name, date=today, count=1
        )
        await session.execute(
            stmt.on_duplicate_key_update(count=cls.count + 1)
        )

    @classmethod
    @ensure_session(db_session)
    async def fetch_totals(
        cls, *, since: dt.date | None = None, session=_UNSET
    ) -> list[tuple[str, int]]:
        q = select(cls.command_name, func.sum(cls.count).label("total"))
        if since is not None:
            q = q.where(cls.date >= since)
        q = q.group_by(cls.command_name).order_by(desc("total"))
        return [(name, int(total)) for name, total in (await session.execute(q)).all()]
```

Imports to add to `schemas.py`: `Date` (to the `sqlalchemy.sql.sqltypes` import),
and `from sqlalchemy.dialects.mysql import insert as mysql_insert`. `func`, `desc`,
`select`, `String`, `BigInteger`, `ensure_session`, `db_session`, `_UNSET`, `dt`
are already imported. (Add import + first usage in the same edit — the formatter
strips unused imports on save.)

Composite PK `(command_name, date)` gives the upsert its conflict target. The
atomic `count = count + 1` at the SQL layer makes concurrent increments race-free —
no in-memory buffering or rollup background task needed.

*Redis (considered, not chosen):* Redis `INCR` + periodic flush would work, but
matches the prior "DB over Redis at this scale" decision — single-replica bots,
trivial DB load, no pub/sub need. Kept as a fallback only.

## Files to change

1. **`dd/common/schemas.py`** — add the `CommandUsage` model + `increment` /
   `fetch_totals` classmethods and the two imports.

2. **`dd/beacon/extensions/statistics.py`** — add:
   - The `_should_track` pure helper and `_EXCLUDED_ROOTS` denylist (above).
   - The `track_command_usage` hook: `@lb.hook(lb.ExecutionSteps.PRE_INVOKE,
     skip_when_failed=True)`, body = `if _should_track(...): try/except around
     await schemas.CommandUsage.increment(qualified_name)` (log + swallow on
     failure). Defined at module level so `__main__` can import it.
   - A new `CommandUsageStatsCommand` registered under the existing
     `stats_command_group` (`name="commands"`, `hooks=[owner_only]`), with an
     optional integer `days` option (`lb.integer(... )`, default unset = all-time;
     when set, `since = today - timedelta(days=days)`). Builds a leaderboard embed
     from `CommandUsage.fetch_totals(since=...)`, mirroring the embed style already
     in this file (`h.Embed(..., color=cfg.embed_default_color)`, numbered list in
     the description). `await ctx.defer()` first, like the sibling commands.

3. **`dd/beacon/__main__.py`** — import `track_command_usage` from the statistics
   extension and pass `hooks=[track_command_usage]` to the existing
   `lb.client_from_app(bot, cfg.test_env or ())` call (line 46-49).

4. **Migration** — run `make atlas-migration-plan` to generate a new
   `migrations/<timestamp>.sql` for the `command_usage` table, then apply.

## Decisions (locked)

- **Storage:** daily per-command buckets.
- **Command set:** all user-facing slash commands *including* admin-created custom
  user-commands; excludes `/autopost` + owner/admin groups (denylist).
- **Visibility:** owner-only `/stats commands` under the existing `/stats` group.

## Verification

- **Lint/type:** `uv run ruff check dd/common/schemas.py dd/beacon` and
  `uv run ty check` (or rely on Zed's ruff+ty) — must pass `E,F,W,I,UP,B,SIM`.
- **Migration:** `make atlas-migration-plan` produces a sane `CREATE TABLE
  command_usage`; review the SQL diff before applying. (Atlas needs the dev MySQL /
  docker; may require running with the Bash sandbox disabled.)
- **Unit test (no DB):** add `dd/beacon/tests/test_command_stats.py` exercising the
  pure `_should_track` helper: `xur`/`lost sector`/`my_custom_cmd` → track;
  `autopost xur`/`stats commands`/`mirror manual_add` → skip; a
  `h.CommandType.MESSAGE` command → skip. No DB needed (DB-touching tests require
  the live MySQL DB).
- **End-to-end (dev):** run `uv run python -OOm dd.beacon` against a populated
  `.env`, invoke `/xur` and a couple of other commands in the test guild, then run
  `/stats commands` and confirm the counts appear. Confirm `/autopost ...` and
  `/stats populations` do **not** increment any counter.
