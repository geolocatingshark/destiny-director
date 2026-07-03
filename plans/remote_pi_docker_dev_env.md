# Move dev to a remote Docker container on a Raspberry Pi 5

## Context

Moving development of Destiny Director off the WSL box onto a **Raspberry Pi 5
(ARM64)** running a long-lived **Docker dev container**. The container is a full dev
environment: **git + uv + Node/Claude Code + Railway CLI + Atlas + make**, driven
**terminal-only**: SSH into the **Pi host** (its own sshd) → `docker exec` into the
container → run `claude`. No editor is installed on or tunnelled through the Pi host.

Decisions locked from Q&A:

- **Target:** Raspberry Pi 5, `linux/arm64`. (Pi 4 ≥4GB also works with this plan; Pi 2B+
  is out — 32-bit/1GB can't comfortably run Claude Code + native builds.)
- **Container job:** edit + git + `ruff`/`ty` + DB-free unit tests, **plus** integration
  tests / Atlas migrations (needs a MySQL). **Not** running the bots live (no Discord
  token conflict with the Railway dev deploy to worry about).
- **Base image:** move to **Debian-slim (glibc)** for the new dev image **and** migrate
  the existing **production `Dockerfile` off Alpine** to Debian-slim in the same effort.
- **Workflow:** terminal-only. `ssh` into the **Pi host** (its existing sshd) →
  `docker exec` into the container → run `claude`. Claude Code, git, and the Railway CLI
  all run on the remote, inside the container. **No in-container sshd** and **no editor
  stretch** — the dev container exposes no ports and needs no Pi-host changes beyond a
  normal clone.

Why Debian over Alpine: on `arm64` almost every native dep ships a glibc `manylinux`
wheel, so builds are fast and reliable; on musl many would compile from source. Audit
below shows **only `asyncmy` compiles on arm64**, and **nothing compiles on amd64**.

## Native-dependency wheel audit (cp313, from `uv.lock`)

| Package | amd64 glibc wheel | arm64 glibc wheel | Build on Pi? |
|---|---|---|---|
| `asyncmy` 0.2.11 | yes | **NO** | **Compiles from sdist (arm64 only)** |
| `cryptography` 49 (speedups) | yes (abi3) | yes (abi3) | No — wheel, **no Rust/cargo needed** |
| `hikari[speedups]`, `aiohttp`, `ciso8601`, `pycares`, `orjson`, `brotli`, `backports-zstd`, `regex`, `multidict`, `yarl`, `frozenlist`, `propcache` | yes | yes | No |
| `aiosqlite` | pure-python | pure-python | No |

Consequences: builder stage needs **`build-essential` only** (no Rust, no
`libmariadb` — `asyncmy` is a pure-Cython wire-protocol impl and links only libc). Keep
compilers in a **builder stage discarded from the final image**.

## Deliverables (files to create / change)

1. **Rewrite `Dockerfile`** — Alpine → Debian-slim, `uv sync --frozen` into a venv
   (replaces `uv export` → pip). Affects Railway prod — roll out dev-first.
2. **New `.dockerignore`** — none exists today; the build context currently ships
   `manifest.zip` (~37MB), `kyber-*.sql`, `.venv/`, `.git/`, etc.
3. **New `Dockerfile.dev`** — Debian-slim dev image (uv + node + claude + **railway** +
   atlas; non-root `dev` uid 1000; source is bind-mounted, not baked). No sshd, no ports.
4. **New `docker-entrypoint.dev.sh`** — wire `~/.ssh` from `.dev-ssh/`, `uv sync`, then
   `sleep infinity`.
5. **New `docker-compose.dev.yml`** — `dev` + `mysql` services, named volumes
   (`dd-uv-cache`, `dd-claude`, `dd-railway`, `dd-mysql-data`). No port mapping.
6. **`.gitignore`** — add `/.dev-ssh/` so git identity keys never get committed.
7. **Docs** — add a "Remote Pi dev" section to `README.md` (or a `docs/pi_dev_setup.md`)
   covering host-sshd → `docker exec`, repo-local git keys, and Railway-in-container.

---

## Part A — Migrate the prod `Dockerfile` to Debian-slim

Replace the 8-stage Alpine build. Switch to `uv sync --frozen --no-dev` into a venv (one
tool, no `pip`/`requirements.txt` drift, hash-pinned via the lock; `--no-dev` keeps the
`speedups` group exactly like today). Draft:

```dockerfile
# syntax=docker/dockerfile:1
FROM arigaio/atlas:latest-community AS atlas          # multi-arch static Go binary

FROM python:3.13-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 UV_PROJECT_ENVIRONMENT=/app/.venv
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*                    # only to compile asyncmy on arm64
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project    # deps layer (cached on lock hash)
COPY dd ./dd
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable           # + the project itself

FROM python:3.13-slim-bookworm AS final
ENV TZ=Etc/UTC PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/.venv PATH="/app/.venv/bin:$PATH"
RUN apt-get update && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*                    # runtime only — no compilers
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=atlas /atlas /usr/local/bin/atlas
COPY migrations ./migrations
COPY docker-entrypoint.sh ./
ARG RAILWAY_SERVICE_NAME                              # ARG only — do NOT promote to ENV
CMD ["sh", "docker-entrypoint.sh"]
```

Preserve exactly: `docker-entrypoint.sh` is POSIX-clean (runs under dash); `python`
resolves to the venv, `atlas` to `/usr/local/bin/atlas`, so
`atlas migrate apply -u ${MYSQL_URL} && python -OO -m dd.beacon` is unchanged. Keep
`RAILWAY_SERVICE_NAME` as an `ARG` (baking an empty `ENV` would shadow Railway's runtime
injection and break service selection).

**`.dockerignore`** (new — needed for local `docker build`; `.railwayignore` only covers
Railway uploads):

```
.git
.venv
**/__pycache__
*.pyc
.pytest_cache
.ruff_cache
.ropeproject
htmlcov
.coverage
manifest.zip
manifest/
scratch/
kyber-*.sql
.env
docs/
plans/
```

Do **not** add a bare `*.sql` line — `migrations/*.sql` must ship. Anchor `kyber-*.sql`.

---

## Part B — `Dockerfile.dev` (dev environment image)

Environment-only image: toolchain baked, **app source bind-mounted at runtime**. Venv
lives at `/home/dev/venv` (**outside** `/workspace`) so the bind-mount can't shadow the
pre-built venv. The container runs no network service — access is via `docker exec` from
the host, so it stays non-root without any sshd.

```dockerfile
# syntax=docker/dockerfile:1
FROM arigaio/atlas:latest-community AS atlas

FROM python:3.13-slim-bookworm AS dev
ARG USERNAME=dev USER_UID=1000 USER_GID=1000
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git make openssh-client curl gnupg ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*        # openssh-client: git push over git@github.com
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code @railway/cli   # Claude Code + Railway CLI
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY --from=atlas /atlas /usr/local/bin/atlas
RUN groupadd --gid ${USER_GID} ${USERNAME} \
    && useradd --uid ${USER_UID} --gid ${USER_GID} --shell /bin/bash -m ${USERNAME} \
    && mkdir -p /workspace && chown ${USER_UID}:${USER_GID} /workspace
ENV TZ=Etc/UTC UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 UV_PROJECT_ENVIRONMENT=/home/dev/venv
USER ${USERNAME}
WORKDIR /workspace
COPY --chown=${USER_UID}:${USER_GID} pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/home/dev/.cache/uv,uid=1000,gid=1000 \
    uv sync --frozen --no-install-project             # pre-build deps (dev + speedups)
COPY --chown=${USER_UID}:${USER_GID} docker-entrypoint.dev.sh /home/dev/entrypoint.sh
CMD ["bash", "/home/dev/entrypoint.sh"]
```

`docker-entrypoint.dev.sh` (new):

```sh
#!/usr/bin/env bash
set -e
# Git identities: keys + config live in the gitignored .dev-ssh/ dir (bind-mounted in).
if [ -d /workspace/.dev-ssh ]; then
  mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
  chmod 600 /workspace/.dev-ssh/id_ed25519_* 2>/dev/null || true
  ln -sf /workspace/.dev-ssh/config "$HOME/.ssh/config"   # IdentityFile → /workspace/.dev-ssh/*
fi
# Deps are baked into /home/dev/venv at build time; add the editable project on start.
[ -f /workspace/pyproject.toml ] && uv sync --frozen || true
exec sleep infinity          # keep the container alive; all work happens via `docker exec`
```

## Part B2 — `docker-compose.dev.yml`

```yaml
services:
  dev:
    build: { context: ., dockerfile: Dockerfile.dev }
    image: dd-dev
    container_name: dd-dev
    init: true
    env_file: [.env]                         # MYSQL_URL + RAILWAY_API_TOKEN
    volumes:
      - .:/workspace                         # bind-mount the host clone (uid 1000)
      - dd-uv-cache:/home/dev/.cache/uv       # persist uv cache (fast re-syncs)
      - dd-claude:/home/dev/.claude           # persist Claude Code login/config
      - dd-railway:/home/dev/.config/railway  # persist `railway login` (see Part E2)
      - /var/run/docker.sock:/var/run/docker.sock  # optional: `atlas migrate diff`
    depends_on: [mysql]
  mysql:
    image: mysql:8                           # arm64 image exists
    container_name: dd-mysql
    environment:
      MYSQL_ROOT_PASSWORD: devroot
      MYSQL_DATABASE: kyber
      MYSQL_USER: kyber
      MYSQL_PASSWORD: kyber
    volumes: [dd-mysql-data:/var/lib/mysql]
volumes: { dd-uv-cache: {}, dd-claude: {}, dd-railway: {}, dd-mysql-data: {} }
```

Removed from the original: the `ports: ["2222:2222"]` mapping and the `dd-ssh` volume
(git keys now come from the repo's `.dev-ssh/`), replaced by `dd-railway`. Do **not**
mount a volume over `/home/dev/venv` or all of `/home/dev` (would erase the pre-built
venv).

---

## Part C — One-time Pi 5 bootstrap + access model (Docker already running)

Access is via the **Pi host's own sshd** → `docker exec`. Prereq: the Pi's SSH is enabled
and the laptop's public key is in the **Pi host user's** `~/.ssh/authorized_keys` (normal
Pi SSH setup) — the container exposes no port. Raspberry Pi OS ships `git`; `docker
compose` v2 ships with Docker. The host clone doubles as the bind-mount backing store.

**uid alignment:** the container `dev` user is uid 1000; the Raspberry Pi OS default first
user is also uid 1000, so bind-mounted files (source *and* the `.dev-ssh/` keys) line up
with no permission friction. If the Pi host user's `id -u` ≠ 1000, build with matching
`--build-arg USER_UID=<n> USER_GID=<n>`.

1. On the Pi: `git clone https://github.com/gsfernandes81/destiny-director.git` (HTTPS
   for the read-only bootstrap), `cd` in, check out `dev`.
2. Place git identity keys + the SSH `config` fragment in `.dev-ssh/` (gitignored per
   Deliverable 6; contents in Part D) and `chmod 600` the private keys. These persist with
   the clone — no Docker volume needed for them.
3. Get the dev `.env` onto the Pi (bind-mounted → visible in-container). Simplest is
   `scp` the existing dev `.env` from the WSL box. Set
   `MYSQL_URL=mysql://kyber:kyber@mysql:3306/kyber` (the app reads this via its
   `MYSQL_URL` fallback in `dd/common/cfg.py` — which prefers `MYSQL_PRIVATE_URL` — and
   `atlas` reads it directly) and `RAILWAY_API_TOKEN=<account token>` (Part E2). `.env` is
   required even for unit tests (`make test` uses `--env-file .env`; `cfg.py` reads env at
   import).
4. `docker compose -f docker-compose.dev.yml build`
5. `docker compose -f docker-compose.dev.yml up -d`
6. Enter the container from the laptop, over the **host** sshd:
   `ssh -t <pi-user>@<pi-ip> 'docker exec -it dd-dev bash -l'` — or add an SSH alias so
   `ssh dd` drops you straight in:
   ```
   Host dd
     HostName <pi-ip>
     User <pi-user>
     RequestTTY yes
     RemoteCommand docker exec -it dd-dev bash -l
   ```
   You're now the `dev` user in `/workspace`.

## Part D — First run inside the container (git identities + verify)

Two GitHub identities are in play — `origin` (`git@github.com`, `gsfernandes81`) and
`shark` (`git@github.com-shark`, `geolocatingshark`) — via an SSH host alias. Keys and the
SSH config fragment live in the **gitignored `.dev-ssh/`** dir in the repo (persisted with
the clone; the entrypoint wires them into `~/.ssh`). Generate once and register each
**public** key with its account:

```sh
ssh-keygen -t ed25519 -f .dev-ssh/id_ed25519_personal -N ""   # add pubkey to gsfernandes81
ssh-keygen -t ed25519 -f .dev-ssh/id_ed25519_shark    -N ""   # add pubkey to geolocatingshark
```

`.dev-ssh/config` (symlinked to `~/.ssh/config` by the entrypoint):

```
Host github.com
  HostName github.com
  User git
  IdentityFile /workspace/.dev-ssh/id_ed25519_personal
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
Host github.com-shark
  HostName github.com
  User git
  IdentityFile /workspace/.dev-ssh/id_ed25519_shark
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
```

Then flip `origin` to SSH (`git remote set-url origin git@github.com:...`), add the
`shark` remote, `git config user.name/email`, and verify:

```sh
uv sync                         # editable project into /home/dev/venv
make test                       # DB-free unit suite (SQLite) — proves the toolchain
uv run ruff check dd && uv run ty check dd
ssh -T git@github.com; ssh -T git@github.com-shark   # both identities authenticate
```

No private keys are copied around and none sit in a Docker volume — they live only in the
gitignored `.dev-ssh/` on the Pi clone.

## Part E — Claude Code in the container

Node 22 + `@anthropic-ai/claude-code` are already in the image; `~/.claude` is a persistent
volume, so login survives rebuilds. First run:

```sh
claude            # then /login — it prints a URL; open it in a laptop browser,
                  # paste the code back. (Or set ANTHROPIC_API_KEY / `claude setup-token`.)
```

Notes:
- The repo ships `.claude/` project settings + skills — they arrive with the clone.
- Claude Code's Bash **sandbox** blocks writes to `~/.cache/uv` (a known failure mode for
  `uv`/`ruff`/`ty`/`pytest`). Since the container is already an isolation boundary, relax
  the sandbox in-container (project `.claude/settings`) or ensure the uv-cache volume is
  writable, so `uv run …` works without per-command sandbox toggling.

## Part E2 — Railway CLI in the container

`@railway/cli` is baked into the image (Part B). Keep the container authenticated so
`make deploy-*-dev` and other `railway` targets run in-container:

- **Primary (simplest):** put a Railway **account token** in the bind-mounted `.env` as
  `RAILWAY_API_TOKEN=<token>` (create it in Railway → Account → Tokens). The CLI reads it
  as an env var, so the container is effectively "logged in" with no volume or interactive
  step, and it survives rebuilds because `.env` is bind-mounted.
- **Alternative (interactive):** `railway login --browserless` (the container has no
  browser) prints a pairing URL — open it on the laptop, paste the code. Persist the login
  via the `dd-railway` volume mounted at the CLI config dir (Linux XDG default
  `~/.config/railway`; confirm with `railway whoami` after login and adjust the mount path
  if it doesn't persist).

Verify with `railway whoami`. **Prod deploys still require explicit user confirmation each
time per CLAUDE.md — never deploy prod on initiative.** (The "Railway deploys need sandbox
disabled" caveat is moot in-container, since Part E already relaxes the Claude Code Bash
sandbox there.)

## Part F — Editing (terminal-only)

- Edit via **Claude Code** (`claude`, running in the container) or a **terminal editor**
  (`vim`/`nano`/`helix`, `apt`-installable in-container if wanted) inside `docker exec`.
- No Zed, no sshfs, no container sshd, no exposed ports. The container is reached solely
  through the Pi host's SSH server + `docker exec`.
- The files are bind-mounted, so editing them directly on the Pi host is technically
  possible, but that's out of scope for this terminal-only setup.

## Part G — MySQL / migrations (integration scope)

- Integration tests default to SQLite; set `TEST_USE_MYSQL=1` to hit the `mysql` service.
  Run `make test-integration` / `make coverage` as needed.
- `atlas migrate apply -u $MYSQL_URL` needs only the running `mysql` service.
- `make atlas-migration-plan` (`atlas migrate diff`) uses `dev = "docker://mysql/8/dev"`
  in `atlas.hcl` — it spins a throwaway MySQL **via Docker**, so authoring new migrations
  in-container requires the mounted `docker.sock` (included above). Applying does not.

---

## Verification (end-to-end, safe rollout)

**Dockerfile migration (do before any prod deploy):**
1. `docker build -t dd:deb .` on the amd64 box — must be pure-wheel (no compiler runs).
2. `docker buildx build --platform linux/arm64 -t dd:deb-arm64 .` — exercises the
   `asyncmy` arm64 sdist compile; confirm it succeeds.
3. `docker run --rm dd:deb python -c "import dd, asyncmy, cryptography, hikari; print('ok')"`
   and `docker run --rm dd:deb atlas version`. Confirm no libmariadb link:
   `ldd /app/.venv/lib/python3.13/site-packages/asyncmy/*.so` → only libc/pthread.
4. `make deploy-beacon-dev` + `deploy-anchor-dev` → watch Railway dev logs for
   `atlas migrate apply` success + bot login. **Only after that**, deploy prod — and only
   with explicit user go-ahead (per project rules).

**Dev container (on the Pi 5):**
5. `docker compose -f docker-compose.dev.yml up -d`, then
   `ssh -t <pi-user>@<pi-ip> 'docker exec -it dd-dev bash -l'` lands you in `/workspace`.
6. `make test` (unit) and `uv run ruff check dd && uv run ty check dd` pass.
7. `git push` to `origin` and to `shark` both authenticate using the repo-local
   `.dev-ssh/` keys (the `openssh-client` now in the image).
8. `claude` starts and completes `/login`; edits + tool calls work in `/workspace`.
9. `railway whoami` succeeds; `make deploy-*-dev` runs from the container.
10. Integration path: `docker compose up -d mysql`; `make atlas-migration-apply`;
    `TEST_USE_MYSQL=1 make test-integration`.

## Risks / notes

- The container runs **no network service**; its only ingress is `docker exec` from the
  host, so its attack surface is the **Pi host's SSH config** — harden that normally.
- `.dev-ssh/` holds live private keys on the Pi — keep it gitignored (Deliverable 6),
  `chmod 600`, uid-1000 ownership. `RAILWAY_API_TOKEN` likewise lives in the gitignored
  `.env`.
- **Prod image size** grows Alpine→Debian-slim (~100MB → ~300MB) — acceptable; build time
  is equal-or-faster (the old Alpine `gcc`/`musl-dev` were dead weight; amd64 is pure
  wheels).
- Pin `python:3.13-slim-bookworm` (not bare `-slim`) and consider pinning `uv` / `atlas`
  tags instead of `:latest` for reproducibility.
- `mysql:8` on the Pi wants ~1GB+; fine on a Pi 5, keep it stopped when not integration-
  testing.
- First `docker buildx` arm64 build (or first Pi build) compiles `asyncmy` — a couple
  minutes on a Pi 5; cached thereafter via the uv-cache volume.
