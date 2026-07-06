# Architecture & code patterns

How the Destiny Director codebase is laid out and the patterns to reuse when adding to
it. `CLAUDE.md` carries the short version; this is the deep-dive. Read the relevant
section **before** adding a command, touching the DB layer, or building a message.

## Package layout

Everything lives under `dd/`:

- `dd.beacon` — the main bot. `__main__.py` boots it; `mirror_core.py`, `nav.py`
  (paged-message system), `utils.py`, `help_details.py`, and **`extensions/`** (the
  command modules).
- `dd.anchor` — the "secondary" bot, but substantial: `__main__.py`, an aiohttp **web UI**
  (`web.py` + `web_static/`) for rotation editing, **OpenCV** image generation
  (`cv2_builder.py`, `cv2_nodes.py`, `cv2_raw.py`), `embeds.py`, `search_json.py`, and
  `extensions/` — including the **`bungie_api/`** subpackage (Bungie OAuth + API client).
- `dd.common` — shared infrastructure (far more than schemas): `cfg.py`, `bot.py`,
  `auth.py`, `components.py`, `discord_logging.py`, `extension_loader.py`, `lifecycle.py`,
  `schemas.py`, `utils.py`, plus domain helpers (`rotation_schema.py`, `lost_sector.py`).
- `dd.hmessage` — the `HMessage` message representation (see below).
- `dd.sector_accounting` — Destiny sector/rotation domain data.

**Implicit namespace packages.** There is intentionally no `dd/__init__.py`,
`dd/common/__init__.py`, or `dd/anchor/__init__.py`. Only `beacon`, `hmessage`,
`sector_accounting`, and the `extensions/` subpackages carry `__init__.py`. Don't add the
missing ones to "fix" imports.

`migrations/` (Atlas SQL + `atlas.sum`) sits at the **repo root**, not under `dd/`.
Design context lives in `docs/` (e.g. `v2_v3_behavior_audit.md`, `decisions/`) and
`plans/`.

## Adding a command (lightbulb v3)

Commands are **classes**, not v2 decorators. A command module under
`dd/<bot>/extensions/`:

1. Declares a module-level `loader = lb.Loader()`.
2. Defines `class Foo(lb.SlashCommand, name="…", description="…")` with an
   `@lb.invoke async def invoke(self, ctx: lb.Context)` method.
3. Registers it with `loader.command(Foo)`.
4. Adds listeners with `@loader.listener(h.StartedEvent)`.

The canonical, copy-me example is **`dd/beacon/extensions/template.py`**. Its shape:

```python
import hikari as h
import lightbulb as lb

from dd.hmessage import HMessage
from ...common import cfg
from ..nav import NavigatorView, NavPages

loader = lb.Loader()

class SlashCommand(lb.SlashCommand, name="xur", description="…"):
    @lb.invoke
    async def invoke(self, ctx: lb.Context):
        navigator = NavigatorView(pages=pages)
        await navigator.send(ctx)

loader.command(SlashCommand)
```

A new extension just needs to be a module under `extensions/` that exposes a `loader`.
Extensions are loaded by **`dd/common/extension_loader.py::load_extensions_strict()`**,
which pre-imports each module so a broken extension logs CRITICAL instead of silently
vanishing (lightbulb's default swallows `ImportError`).

Owner-only commands: gate with the `owner_only` hook from `dd/common/auth.py`
(`hooks=[owner_only]` on the command, or via `client_from_app(..., hooks=[owner_only])`),
paired with `owner_check_error_handler`.

## Database access

- Import the session factory: `from dd.common.schemas import db_session`.
- Use it as an async context manager with a transaction:

  ```python
  async with db_session() as session, session.begin():
      ...
  ```

- For helper functions that should accept an optional caller-supplied session, decorate
  with **`@ensure_session(db_session)`** from `dd/common/utils.py` (see the many call
  sites in `schemas.py`).
- **Do not build engines/sessionmakers by hand.** `db_session` is a rebindable
  `_SessionmakerProxy` (`schemas.py`); the test suite swaps its target via
  `configure_test_db()` to point at a throwaway SQLite DB. Hand-rolled engines bypass that
  and can hit the real database.

Schemas are defined in `dd/common/schemas.py`, which also serves as the Atlas DDL source
(`--print-ddl`) and a management CLI (`--create-all` / `--destroy-all`). `--destroy-all`
refuses a non-local DB unless `ALLOW_REMOTE_SCHEMA_DESTROY=1` — never bypass this guard.

## Building messages — `HMessage`

`HMessage` (`from dd.hmessage import HMessage, MultiImageEmbedList`) is a mutable,
mergeable representation of a Discord message (content + embeds + attachments). It's used
throughout for building, mirroring, and announcing. Prefer it over hand-assembling raw
`hikari` embeds. Typical use (from `template.py`):

```python
from dd.common.utils import accumulate

msg = (
    accumulate([HMessage.from_message(m) for m in messages])
    .merge_content_into_embed()
    .merge_attachements_into_embed(default_url=cfg.default_url)
)
```

For **Components V2** posts, use `build_container()` from `dd/common/components.py`.

## Paged messages — `dd/beacon/nav.py`

For multi-page / navigable responses use the nav system rather than rolling your own:
`NavPages` (a date-range-keyed page store), `NavigatorView` (the interactive view), and
`make_navigator_command()` (builds a navigator-backed command). `Pages.from_channel(...)`
pulls messages from a followable channel; see `template.py`.

## Configuration — `dd/common/cfg.py`

`cfg.py` is the single config source. It reads env vars and **validates required ones at
import time** (raises `ValueError` if a required var is missing). It exposes
`cfg.followables`, tokens, colors, alert thresholds, DB URLs, Bungie creds, etc. Because
validation happens at import, running anything that imports `cfg` without a populated
environment (e.g. bare `pytest` without `--env-file .env`) fails fast — this is why tests
go through `make test`.

## Logging

Standard `logging.getLogger(__name__)`. On top of that, `DiscordLogHandler`
(`dd/common/discord_logging.py`, installed via `install_discord_logging()` in
`__main__`) forwards records to a Discord alerts channel, dedupes by signature, and
escalates storms. Deterministic error reference codes live in `dd/common/utils.py`.

## Tests

Tests live inside each package as `tests/` subdirs (including nested ones, e.g.
`dd/anchor/extensions/tests/`, `dd/anchor/extensions/bungie_api/tests/`). Every package
has tests. `conftest.py` exists only in `dd/beacon/tests/` and `dd/anchor/tests/`; its
key fixture is a session-scoped, autouse `_test_db` that repoints the DB layer at a
temp-file SQLite database via `schemas.configure_test_db()`. Set `TEST_USE_MYSQL=1` to run
DB tests against MySQL instead (guarded by `_db_is_local()` / `ALLOW_REMOTE_SCHEMA_DESTROY`
so it can't wipe a remote DB). Run tests with `make test` (see `CLAUDE.md`).
