# Plan: Remove the `dev_` command prefix from the anchor bot (test env)

> **Status:** Not yet implemented — saved for a later session.
>
> **⚠️ Before you start (re-verify the plan):** This repo is on an active
> feature branch and files change frequently. The line numbers and exact code
> snippets below were accurate when this plan was written but **may have
> drifted**. First run the grep commands in the *Re-verify* section to confirm
> the `dev_` prefix logic still lives where described. **If the code has
> changed, revise this plan accordingly before editing.**

## Context

In a **test environment** the anchor bot (`dd.anchor`) prefixes certain slash
command **group** names with `dev_` so the dev bot can be told apart from the
prod bot when both are in the same test guild. The user wants the *real*
(unprefixed) names in the test env for **all** currently-prefixed groups, and
wants this done by **hardcoding** (no new config flag).

"Test environment" is driven by `cfg.test_env` (`dd/common/cfg.py`), which is
`_test_env("TEST_ENV")`: a `tuple[int, ...]` of guild ids when `TEST_ENV` is set
to a comma-separated list, or an empty tuple `()` (falsy) otherwise. In
**production** `cfg.test_env` is falsy, so the `dev_` prefix was **never applied
there** — therefore this change only affects the test env and leaves prod
behaviour identical.

**Decisions made with the user:**
- Un-prefix **all four** autopost control groups: `xur`, `eververse`,
  `lost_sector`, `gunsmith`.
- **Also** un-prefix the `ddv1` controller group.
- Mechanism: **hardcode** it (do *not* add a `prefix_in_test_env`-style flag).

Because every prefixed group is being un-prefixed, this effectively removes the
`dev_` mechanism from the anchor bot entirely.

**Accepted consequence:** with the prefix gone, the dev bot's `/xur`,
`/eververse`, `/lost_sector`, `/gunsmith`, and `/ddv1` groups will share names
with the prod anchor bot if both run in the same test guild. The user has
accepted this.

## Where the prefix lives (exactly two places — beacon does NOT use this pattern)

1. **`dd/anchor/autopost.py`**, in `make_autopost_control_commands(...)` — builds
   the parent group name. This factory is the **sole** prefixer for the four
   autopost extensions, all of which call it with keyword args:
   - `dd/anchor/extensions/xur.py` → `make_autopost_control_commands(autopost_name="xur", ...)`
   - `dd/anchor/extensions/eververse.py` → `autopost_name="eververse"`
   - `dd/anchor/extensions/lost_sector.py` → `autopost_name="lost_sector"`
   - `dd/anchor/extensions/gunsmith.py` → `autopost_name="gunsmith"`

   Current code (around lines 38-41):
   ```python
   parent_group = lb.Group(
       autopost_name if not cfg.test_env else "dev_" + autopost_name,
       "Commands for Kyber",
   )
   ```

2. **`dd/anchor/extensions/controller.py`**, the `ddv1` controller group (commands
   `all_stop`, `restart`, `info`). Current code (around lines 24-30):
   ```python
   control_group_name = "ddv1"
   if cfg.test_env:
       control_group_name = "dev_ddv1"

   loader = lb.Loader()

   kyber = lb.Group(control_group_name, "Commands for Kyber")
   ```

## Changes

### 1. `dd/anchor/autopost.py` — un-prefix all autopost control groups
Replace the conditional group construction with the bare name:
```python
parent_group = lb.Group(autopost_name, "Commands for Kyber")
```
**Important:** `cfg.test_env` (line ~39) is the **only** use of `cfg` in this
file. After this edit, `from ..common import cfg` (line ~23) becomes unused.
**Remove that import in the same edit** — the repo's format hook strips unused
imports on save, and `ruff` will flag F401 otherwise. (Confirm with
`grep -n "cfg" dd/anchor/autopost.py` after editing: should be empty.)

### 2. `dd/anchor/extensions/controller.py` — un-prefix the `ddv1` group
Delete the `control_group_name` conditional and inline the bare name:
```python
kyber = lb.Group("ddv1", "Commands for Kyber")
```
**Keep** `from ...common import cfg` here — `cfg` is still used elsewhere in the
file (the `info` command prints `cfg.control_discord_server_id`, `cfg.test_env`,
`cfg.followables[...]`, and `loader.command(kyber, guilds=[cfg.control_discord_server_id])`).
Verify with `grep -n "cfg" dd/anchor/extensions/controller.py` (expect several
remaining uses).

## Critical files
- `dd/anchor/autopost.py`
- `dd/anchor/extensions/controller.py`

(No changes needed in the four autopost extension files — they only pass
`autopost_name=`, and the factory change covers them.)

## Re-verify (run these first, before editing)
```bash
# All dev_ prefix sites in anchor (expect exactly the two described above):
grep -rn "dev_" dd/anchor
# Confirm the factory line and its callers:
grep -n "cfg.test_env" dd/anchor/autopost.py
grep -rn "make_autopost_control_commands(" dd/anchor/extensions/*.py
# Confirm cfg usage so you know which import to drop vs keep:
grep -n "cfg" dd/anchor/autopost.py
grep -n "cfg" dd/anchor/extensions/controller.py
```
If anything differs from this plan (renamed factory, new callers, prefix moved,
extra `cfg` use in autopost.py, etc.), **update the plan before proceeding.**

## Project conventions (so a fresh session works correctly)
- Use **uv**: run tools as `uv run ruff check`, `uv run ty check`, `uv run python ...`.
- Two bots share `dd/`: `dd.beacon` (main), `dd.anchor` (secondary), `dd.common` (shared).
- A **format hook runs on edit** and removes unused imports — when you remove the
  last use of an import, remove the import in the same edit (and when adding an
  import, add its usage in the same edit).
- ruff: line length 88, double quotes, isort combine-as-imports.
- Sandbox note: `uv` writes to a cache outside the sandbox; if a `uv run` command
  fails with a read-only-filesystem error, re-run it with the sandbox disabled.

## Verification (after editing)
- `uv run ruff check dd/anchor/autopost.py dd/anchor/extensions/controller.py`
  and `uv run ty check` — both clean (in particular, **no unused `cfg` import**
  left in `autopost.py`).
- Import sanity:
  ```bash
  uv run python -c "import dd.anchor.extensions.xur, dd.anchor.extensions.eververse, dd.anchor.extensions.lost_sector, dd.anchor.extensions.gunsmith, dd.anchor.extensions.controller; print('OK')"
  ```
- Confirm no prefix logic remains: `grep -rn "dev_" dd/anchor` returns nothing.
- (Optional) Group-name check via lightbulb loadable introspection — each
  extension exposes a module-level `loader` whose `loader._loadables` contains
  `_CommandLoadable` objects with a `._command`; for a group that's an
  `lb.Group` with `.name`, for a top-level command the name is
  `._command._command_data.name`. Assert the anchor autopost/controller group
  names are `xur` / `eververse` / `lost_sector` / `gunsmith` / `ddv1` (no
  `dev_`). The factory now ignores `cfg.test_env`, so this holds regardless of
  the `TEST_ENV` value.
- The DB-backed pytest suite (`uv run python -m pytest`) needs a live MySQL and
  is unrelated to this change; it can be skipped if no DB is available.
