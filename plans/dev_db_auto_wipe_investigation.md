# Investigate: dev MySQL schema gets auto-wiped

> **Status: STUB — not started.** Capture-only tracker for a later agent. Re-verify
> everything against the live state before acting; dev-only, **never touch prod**.

## Problem

The **dev** Railway MySQL database (`railway`) intermittently loses **all** its tables
(0 tables, including `atlas_schema_revisions`). The bots keep pointing at the empty DB
and every DB op fails, e.g.:

```
sqlalchemy.exc.ProgrammingError: (asyncmy...) (1146, "Table 'railway.mirrored_channel' doesn't exist")
```

Observed at least twice on **2026-07-01** (once surfaced via `/autopost enable`).

## Current recovery (works, but manual)

Any bot restart re-runs `atlas migrate apply` at boot and rebuilds the schema — see
`docker-entrypoint.sh` (`atlas migrate apply -u ${MYSQL_URL} && python -OO -m dd.<bot>`
for **both** beacon and anchor). Since deploy-on-push is disabled for dev
(see memory `deploy-remote-is-shark`), recovery is a manual
`railway redeploy -s <bot>` or `make deploy-<bot>-dev`. Confirmed: after the wipe the DB
had 0 tables; a beacon redeploy restored all 9
(`atlas_schema_revisions, auto_post_settings, bungie_credentials, command_usage,
mirrored_channel, mirrored_message, rotation_data, server_statistics, user_command`).

## What we know / ruled out

- It's a **full** wipe (all 9 tables, incl. `atlas_schema_revisions`), not a partial
  app-table drop.
- The app code does not `DROP` tables. Not caused by a redeploy (those *rebuild* via
  atlas). So something external is clearing the DB *between* boots.
- `make destroy-schemas` drops the SQLAlchemy-defined tables but **not**
  `atlas_schema_revisions` (not a SQLAlchemy model), so `destroy-schemas` alone doesn't
  explain a *full* wipe — unless paired with a manual `DROP DATABASE` / recreate.

## Candidate causes to check

1. **Railway MySQL service on dev** — deployment/restart history + **volume
   persistence**. Is the volume ephemeral / does the MySQL service redeploy and lose
   data? Check the service's volume config and recent events in the Railway dashboard.
2. **A manual/scripted reset** — anyone running `make destroy-schemas`, a `DROP
   DATABASE railway; CREATE DATABASE railway;`, or a DB-reset target against dev.
3. **A scheduled job / routine / CI** that resets dev.
4. **The maintainer's own workflow** — they've referred to "emptying the schema on dev";
   confirm whether the wipes are intentional/manual or genuinely automatic.

## First steps for whoever picks this up

- Correlate wipe timestamps with Railway deploy/restart events and any local `make`
  runs. `railway logs -s MySQL -e dev` and the MySQL service's deployment list.
- Inspect the dev **MySQL volume** (persistent vs. ephemeral) — a reprovisioned/ephemeral
  volume is the most likely "automatic" culprit.
- If confirmed automatic, decide mitigation: make the volume persistent, and/or add a
  boot-time guard/healthcheck that re-applies atlas when tables are missing (currently a
  restart is required to notice).

## References

- `docker-entrypoint.sh` (atlas apply per bot); memory `migrations-auto-apply-at-boot`.
- The 1146 error + full-empty confirmation via
  `SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='railway'` (was 0).
