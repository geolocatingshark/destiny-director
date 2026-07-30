#!/usr/bin/env bash
# Launch a beacon/anchor DEV bot inside the dd-dev container for spin-up-to-test work.
# Two isolation guarantees, so a throwaway test run can never harm anything else:
#
#   * DB isolation — the bot runs against $DEVBOT_SCHEMA (default kyber_devbot), NOT the
#     shared `kyber` DB, so `make create/destroy-schemas` and the integration tests can't
#     clobber it (and vice-versa). The schema is created + granted to the app user on the
#     first launch (needs root — the mysql image grants the app user only its own DB).
#   * OOM isolation — relies on the dd-dev container memory cap (docker-compose.dev.yml
#     mem_limit) to confine any OOM kill to THIS container's cgroup; `choom` then biases
#     the bot to be the FIRST victim within it, ahead of the Claude session. The make
#     targets refuse to launch unless the cap is in place (see `_require-mem-cap`).
#
# No jemalloc in the dev image (prod-only), so RAM won't mirror prod exactly — fine for
# functional spin-up-to-test, not for a RAM-measurement instance.
#
# Usage: ./docker-run-devbot.sh <beacon|anchor>   (normally via `make run-*-devbot` /
# `make devbot-up`). Env vars are ambient in dd-dev via the compose `env_file: [.env]`.
set -euo pipefail

bot="${1:-}"
case "$bot" in
  beacon | anchor) ;;
  *) echo "usage: $0 <beacon|anchor>" >&2; exit 2 ;;
esac

: "${MYSQL_URL:?MYSQL_URL not set — run inside dd-dev where env_file loads .env}"
schema="${DEVBOT_SCHEMA:-kyber_devbot}"

# The bot connects with the SAME creds/host as MYSQL_URL, only the database swapped — the
# most faithful mirror of prod's (non-root) connection, just against a separate schema.
devbot_url="${MYSQL_URL%/*}/${schema}"
app_user="${MYSQL_URL#*//}"; app_user="${app_user%%:*}"

# Host/port for the admin (root) provisioning connection, parsed from MYSQL_URL so a
# non-default host still works. Port defaults to 3306 when the URL omits it.
hostport="${MYSQL_URL#*@}"; hostport="${hostport%%/*}"
db_host="${hostport%%:*}"; db_port="${hostport##*:}"
[ "$db_port" = "$db_host" ] && db_port=3306

# Provision the schema as root (mirrors docker-entrypoint.dev.sh's atlas_dev step). Fatal
# on failure — unlike that best-effort step, the bot can't run without its schema — but
# bounded-retry first so a not-yet-ready MySQL doesn't spuriously fail the launch.
echo "devbot: provisioning schema '$schema' on ${db_host}:${db_port} (grant -> ${app_user})"
DEVBOT_SCHEMA="$schema" \
DEVBOT_APP_USER="$app_user" \
DEVBOT_DB_HOST="$db_host" \
DEVBOT_DB_PORT="$db_port" \
DEVBOT_DB_ADMIN_USER="${DEVBOT_DB_ADMIN_USER:-root}" \
DEVBOT_DB_ADMIN_PASSWORD="${DEVBOT_DB_ADMIN_PASSWORD:-${MYSQL_ROOT_PASSWORD:-devroot}}" \
"${VIRTUAL_ENV:-/home/dev/venv}/bin/python" - <<'PY'
import asyncio
import os

import asyncmy

schema = os.environ["DEVBOT_SCHEMA"]
user = os.environ["DEVBOT_APP_USER"]


async def main() -> None:
    last = None
    for _ in range(15):
        try:
            conn = await asyncmy.connect(
                host=os.environ["DEVBOT_DB_HOST"],
                port=int(os.environ["DEVBOT_DB_PORT"]),
                user=os.environ["DEVBOT_DB_ADMIN_USER"],
                password=os.environ["DEVBOT_DB_ADMIN_PASSWORD"],
            )
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE IF NOT EXISTS `{schema}`")
                await cur.execute(f"GRANT ALL PRIVILEGES ON `{schema}`.* TO '{user}'@'%'")
                await cur.execute("FLUSH PRIVILEGES")
            conn.close()
            return
        except Exception as exc:  # noqa: BLE001 — retry any conn-time failure
            last = exc
            await asyncio.sleep(2)
    raise SystemExit(f"could not provision `{schema}` — is the mysql service up? ({last})")


asyncio.run(main())
PY

# Apply migrations to the devbot schema (mirrors the prod entrypoint's pre-flight). Atlas
# defaults to file://migrations relative to cwd, which make runs at the repo root.
echo "devbot: applying migrations to '$schema'"
atlas migrate apply -u "$devbot_url"

# Override BOTH url vars so cfg.py selects the devbot schema regardless of which it prefers
# (it prefers MYSQL_PRIVATE_URL when set). choom biases the bot up the OOM-kill list so it,
# not the Claude session, is reaped first if the container cap is ever hit.
export MYSQL_URL="$devbot_url" MYSQL_PRIVATE_URL="$devbot_url"
oom_adj="${DEVBOT_OOM_SCORE_ADJ:-800}"
echo "devbot: starting $bot against '$schema' (oom_score_adj=$oom_adj)"
exec choom -n "$oom_adj" uv run python -OOm "dd.$bot"
