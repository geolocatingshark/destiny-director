# Commit the lightbulb v2→v3 rewrite as a clear, logical series

## Context

The working tree holds the bulk of the **hikari-lightbulb v2 → v3 rewrite** but it
is uncommitted and the index is a messy partial snapshot (49 files staged, 84 more
unstaged, in no coherent grouping). The goal is to land this work as a small,
reviewable series of themed commits so the history reads clearly despite the size
(~2.9k insertions / ~8.9k deletions across the rewrite).

Two complications must be handled first:

1. **Sandbox phantom entries.** `.claude/agents`, `.claude/commands`, `.claude/hooks`,
   `.claude/skills`, `.claude/settings.json` are currently **staged as empty blobs**
   (`e69de29`), and `.mcp.json` is untracked. All appear as `character special (1/3)`
   = `/dev/null` bind-mounts inside the Claude Code sandbox (the known "Sandbox
   phantom dotfiles" issue). Committing them would write garbage. **Decision: unstage
   them, do not re-add, do not touch `.gitignore`** (tracking decision deferred, to be
   done from the host where the real files exist).
2. **Messy index.** Easiest path is a mixed `git reset` to clear staging, then
   re-stage each group with explicit pathspecs (never `git add -A` from the repo root,
   or the phantoms get re-staged).

Note: per-commit green builds are **not** a goal here — mid-rewrite the intermediate
commits won't all run/test standalone. Group for **review clarity**, not bisectability.

> Verify the working-tree state before executing — this plan was captured at a point
> in time and files may have moved on. Re-run `git status` / `file .claude/* .mcp.json`
> to confirm the phantom set and the file groupings below still hold.

## Step 0 — Clear the index, leave phantoms unstaged

```
git reset                       # mixed reset: unstages everything, keeps working tree
```

After this the `.claude/*` empty blobs and `.mcp.json` are simply unstaged/untracked.
**Never** `git add` them. All `git add` calls below use explicit pathspecs so the
phantoms can't sneak back in. (`git add -A <dir>` is used to also stage deletions for
rename detection, but always scoped to a non-root path.)

## Commit series (7 commits)

Each commit = stage the listed pathspecs, then commit with the given message.

### 1. `chore: project tooling and dev config for v3`
- Modified: `.env-example`, `Makefile`, `pyproject.toml`, `README.md`
- New (real, verified non-phantom): `CLAUDE.md`, `ty.toml`
- Removed tracked symlink: `.venv` (it's a `120000` symlink at HEAD; staged delete is
  correct and `.venv` is gitignored)
```
git add .env-example Makefile pyproject.toml README.md CLAUDE.md ty.toml
git rm --cached --quiet .venv 2>/dev/null; git add .venv   # stage the symlink deletion
```

### 2. `feat(common): shared infra for lightbulb v3 extensions`
- New: `dd/common/auth.py`, `bot.py`, `components.py`, `discord_logging.py`,
  `extension_loader.py`, `help.py`, `source.py`
- Modified: `dd/common/cfg.py`, `schemas.py`, `utils.py`, `lost_sector.py`
```
git add dd/common/
```

### 3. `refactor: update hmessage and sector_accounting for v3`
- Modified: `dd/hmessage/embed.py`, `dd/hmessage/message.py`,
  `dd/sector_accounting/__init__.py`, `sector_accounting.py`, `utils.py`, `xur.py`
```
git add dd/hmessage/ dd/sector_accounting/
```

### 4. `refactor(beacon): migrate modules to lightbulb v3 extensions`
- Deletes: `dd/beacon/bot.py`, all `dd/beacon/modules/*.py`
- Adds: all `dd/beacon/extensions/*.py` (incl. `__init__.py` and untracked
  `testing.py`); `guild_count_status.py` is a modify (already lived in extensions/)
- Modified: `dd/beacon/__main__.py`, `nav.py`, `utils.py`
- **Stage delete+add together** so git detects the `modules/x.py → extensions/x.py`
  moves as renames. Exclude tests (next commit).
```
git add -A dd/beacon/__main__.py dd/beacon/nav.py dd/beacon/utils.py \
           dd/beacon/bot.py dd/beacon/modules dd/beacon/extensions
```

### 5. `refactor(anchor): migrate modules to lightbulb v3 extensions`
- Deletes: `dd/anchor/bungie_api.py`, `controller.py`, `eververse.py`, `gunsmith.py`,
  `help.py`, `lost_sector.py`, `posts.py`, `source.py`, `xur.py`
- Adds: `dd/anchor/extensions/*.py`, `dd/anchor/search_json.py`
- Modified: `dd/anchor/__main__.py`, `autopost.py`, `embeds.py`, `utils.py`
```
git add -A dd/anchor/
```

### 6. `test: update and add tests for v3`
- Modified: `dd/beacon/tests/conftest.py`, `test_schemas_mirrored_channel.py`,
  `test_schemas_usercommand.py`, `test_server_statistics.py`
- New: `test_discord_logging.py`, `test_user_commands_autocomplete.py`,
  `test_user_commands_invocation_mapping.py`
```
git add dd/beacon/tests/
```

### 7. `docs: v3 behaviour audit and migration plans`
- `docs/v2_v3_behavior_audit.md`
- `plans/autoposts_state_machine.md`, `cfg_audit_and_cleanup.md`,
  `components_v2_embed_editor.md`, `dev_prefix_removal.md`, `mirror_improvements.md`,
  `user_command_url_latency.md`, `v3_manual_testing_checklist.md`, and this file
  (`plans/commit_v3_rewrite.md`)
```
git add docs/ plans/
```

## Verification

After all 7 commits:

1. **No phantoms committed** — confirm none of the v3 commits contain `.claude/*`
   or `.mcp.json`, and that no empty-blob `e69de29` entries landed:
   ```
   git log --oneline -7 --stat | grep -E '\.claude/|\.mcp\.json' && echo "LEAK!" || echo "clean"
   ```
2. **Working tree is only phantoms left** — `git status` should show only the
   `.claude/*` (modified/char-special) and untracked `.mcp.json` remaining:
   ```
   git status --short
   ```
3. **Renames detected** — sanity-check beacon/anchor moves show as `R`:
   ```
   git show --find-renames --stat HEAD~3   # beacon migration commit
   ```
4. **Tests pass** (requires live MySQL per project env note):
   ```
   uv run python -m pytest
   ```
5. **Lint clean**:
   ```
   uv run ruff check
   ```
6. **Review the series** reads coherently:
   ```
   git log --oneline -7
   ```

## Deferred (out of scope, handle from host)

Whether shared `.claude/` config and `.mcp.json` should be tracked in the repo is left
undecided. They can only be committed correctly **outside the sandbox** (where they're
real files, not `/dev/null` mounts). Do that separately if desired.
