# Destiny Director — Project Rules

Two Discord bots (`hikari-lightbulb` v3) sharing one codebase under `dd/`:

- `dd.beacon` — main bot
- `dd.anchor` — secondary bot
- `dd.common` — shared DB schemas / helpers
- `dd.hmessage`, `dd.sector_accounting` — shared domain code

Python 3.12, fully async (hikari / aiohttp / aiosqlite / asyncmy), SQLAlchemy 2.0,
Atlas for migrations, deployed on Railway.

## Package management — use uv

- Use **uv** only. Never use pip, pip-tools, poetry, or conda.
- Add runtime deps with `uv add <package>`; dev deps with `uv add --dev <package>`.
- `uv.lock` is committed — keep it in sync, never hand-edit `pyproject.toml`
  dependency lists.
- Dependency groups: `dev` (rope, pytest, pytest-asyncio) and `speedups`. Both are
  in `tool.uv.default-groups`.
- Do not create virtualenvs by hand or install packages globally.
- The `Makefile`'s Python targets (`run-*-local`, `*-schemas`, `test`) all use
  `uv run`. The `railway`/`atlas` targets shell out to non-Python CLIs as-is.

## Running things

- Prefix execution with `uv run` — e.g. `uv run python -m pytest`,
  `uv run ruff check`. Don't invoke `python`/`pytest`/`ruff` bare.
- Run a bot locally: `uv run python -OOm dd.beacon` (or `dd.anchor`). Requires a
  populated `.env` (see `.env-example`; all vars are required).
- Deploy via `make deploy-beacon-dev` / `deploy-anchor-prod` etc. (Railway) or via Railway's plugin
- NEVER DEPLOY TO PROD BY ANY MEANS

## Testing

- **pytest** with **pytest-asyncio** (the code is async-first).
- Tests live **inside each package** as `tests/` subdirs, e.g.
  `dd/beacon/tests/test_*.py` — not in a single root `tests/` dir. Follow that
  convention.
- Run: `uv run python -m pytest` (or scope to a path/package).

## Editor

The editor is **Zed** (configured in the user's home `Zed/settings.json`, run over
WSL). VSCode is no longer used — ignore `.vscode/settings.json` and
`pyrightconfig.json`; they're stale. Zed's active Python language servers are
**`ty`** and **`ruff`**; `pyright` and `pylsp` are explicitly **disabled**.

## Linting & formatting — ruff

- **ruff** does both linting and formatting. Line length **88**, double quotes.
- isort: `combine-as-imports` and `force-wrap-aliases` are on — respect them.
- Lint rule set (from Zed's ruff config): `E`, `F`, `W`, `I`, `UP`, `B`, `SIM`
  (pycodestyle, pyflakes, isort, pyupgrade, bugbear, simplify). Write code that
  passes these.
- `format_on_save` is on and runs `source.organizeImports.ruff` →
  `source.fixAll.ruff` → ruff format. **organizeImports removes unused imports on
  save**, so when you add an import, add its usage in the **same edit** or it gets
  stripped before you use it.

## Type checking — ty

- **ty** is the type checker, both in-editor (Zed LSP) and on the CLI (`ty.toml`).
  Use ty/pyrefly-style tools, not mypy or pyright.
- Prefer fixing types over suppressing. When ty genuinely can't model a pattern
  (attrs-generated `__init__`, gspread stubs, dict subclasses), suppress it in
  **`ty.toml` overrides with an explanatory comment** — see the existing per-file
  blocks. Avoid bare inline `# type: ignore`; if you must, include the error code.

## Project layout & config

- All metadata in `pyproject.toml` (PEP 621); build backend is **hatchling**.
  No `setup.py`, `setup.cfg`, or `requirements.txt`.
- Database: SQLAlchemy schemas in `dd/common/schemas.py`. Migrations via **Atlas**
  (`atlas.hcl`, `migrations/`): `make atlas-migration-plan` to diff,
  `make atlas-migration-apply` to apply. `make create-schemas` / `destroy-schemas`
  manage tables directly.
- `.python-version` pins 3.12.

## Conventions

- Async/await throughout — don't introduce blocking I/O in coroutine paths.
- Keep new code matching the surrounding style (naming, comment density, idioms).
- Secrets live in `.env` (gitignored) — never commit them.
