# asyncmy SQL-injection advisory (CVE-2025-65896) — accepted risk

> **Status: DECIDED — dismiss as not-exploitable.** No code change to the DB layer;
> the resolution is a documented accepted-risk plus a Dependabot `ignore`
> (`.github/dependabot.yml`). Revisit if/when upstream ships a fix.

## The advisory

- **CVE-2025-65896** / **GHSA-qhqw-rrw9-25rm** — CRITICAL.
- "SQL injection via crafted dict keys" in `asyncmy`. Same root cause as PyMySQL's
  **CVE-2024-36039**: the Cython converter escapes dict *values* but not dict *keys*,
  so injection is possible when dict keys are attacker-controlled.
- Affected range: `asyncmy <= 0.2.11`. **0.2.11 is the newest release**, the advisory
  lists `patched: None`, and the upstream issue
  [long2ice/asyncmy#134](https://github.com/long2ice/asyncmy/issues/134) is still open.
  **There is no version to bump to.**

## Why we are not exploitable

- `asyncmy` is used **only** as SQLAlchemy's async MySQL driver
  (`mysql+asyncmy`, wired in `dd/common/cfg.py` / `dd/common/schemas.py`). We never
  call an asyncmy cursor directly.
- SQLAlchemy's `MySQLDialect_asyncmy` uses **`paramstyle = "format"`** — positional
  `%s` parameters passed as a **sequence**, never a dict. The vulnerable dict-key
  escape path (`escape_dict`) is therefore never reached through our stack.
- The only dicts we hand SQLAlchemy are ORM `insert().values(...)` payloads whose
  **keys are column names defined in `schemas.py`**, never user input.

So the practical exploitability against this codebase is nil; the Dependabot alert is
flagging the dependency's presence, not a reachable sink.

## Resolution

1. **`.github/dependabot.yml`** — added, with an `ignore` for `asyncmy` and
   `open-pull-requests-limit: 0`, so Dependabot stops opening security-update PRs for
   a dependency that has no fix. The header documents the CVE and the reasoning.
2. **Dismiss the live alert** in the GitHub Security tab as **"Risk is tolerable"**
   (not exploitable). The `ignore` config suppresses *PRs*, not the *alert* itself, so
   this manual step is required to clear the banner shown on push.

## Re-open when

- Upstream releases a patched `asyncmy` (watch #134 / new PyPI release) — then bump,
  drop the `ignore`, and re-enable the alert. Or
- We ever start calling an asyncmy cursor directly with a dict whose keys are not
  static column names — that would make the sink reachable and this decision invalid.
