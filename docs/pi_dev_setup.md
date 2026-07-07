# Remote Raspberry Pi 5 dev environment

Develop Destiny Director inside a long-lived Docker container on a Raspberry Pi 5
(`linux/arm64`). The primary workflow is terminal-based: `ssh` into the Pi host →
`docker exec` into the container → run `claude` / git / make. An in-container sshd
(port 2222) additionally lets **Zed remote directly into `/workspace`** (see
[Zed remote / SSH access](#zed-remote--ssh-access)). The container bakes the toolchain
(uv + Node/Claude Code + Railway CLI + Atlas + make); the repo is bind-mounted, so edits
on the host clone and inside the container are the same files.

Files: `Dockerfile.dev`, `docker-entrypoint.dev.sh`, `docker-compose.dev.yml`,
`sshd_config.dev`. Git identity keys live in a gitignored `.dev-ssh/` dir that rides
along with the clone.

## Prerequisites (assumed already done on the Pi)

- Docker + `docker compose` v2 and `git` installed.
- The Pi's own SSH server is enabled and your laptop can `ssh <pi-user>@<pi-ip>`.

## One-time bootstrap

```sh
# 1. Clone (HTTPS is fine for the read-only bootstrap) and check out dev.
git clone https://github.com/gsfernandes81/destiny-director.git
cd destiny-director
git checkout dev

# 2. Create the git identity keys in the gitignored .dev-ssh/ dir.
mkdir -p .dev-ssh && chmod 700 .dev-ssh
ssh-keygen -t ed25519 -f .dev-ssh/id_ed25519_personal -N ""   # -> gsfernandes81
ssh-keygen -t ed25519 -f .dev-ssh/id_ed25519_shark    -N ""   # -> geolocatingshark
chmod 600 .dev-ssh/id_ed25519_personal .dev-ssh/id_ed25519_shark
```

Create `.dev-ssh/config` (used as `~/.ssh/config` inside the container):

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

Register each **public** key with its GitHub account (Settings → SSH keys):
`cat .dev-ssh/id_ed25519_personal.pub` → gsfernandes81, `…_shark.pub` → geolocatingshark.

```sh
# 3. Put the dev .env at the repo root (bind-mounted -> visible in-container).
#    Simplest: scp it from your existing dev box. It must contain every var the
#    bots read at import (Discord tokens, etc.), plus:
#      MYSQL_URL=mysql://kyber:kyber@mysql:3306/kyber
#      RAILWAY_API_TOKEN=<railway account token>   # Railway -> Account -> Tokens
#    (.env is required even for unit tests — make test uses --env-file .env and
#     dd/common/cfg.py reads env at import.)

# 4. Build and start (dev container + mysql).
docker compose -f docker-compose.dev.yml build
docker compose -f docker-compose.dev.yml up -d

# 5. Enter the container over the Pi host's sshd (fish is the interactive shell).
ssh -t <pi-user>@<pi-ip> 'docker exec -it dd-dev fish'
```

Optional laptop `~/.ssh/config` alias so `ssh dd` drops you straight in:

```
Host dd
  HostName <pi-ip>
  User <pi-user>
  RequestTTY yes
  RemoteCommand docker exec -it dd-dev fish
```

**uid note:** the container `dev` user is uid 1000, matching Raspberry Pi OS's default
first user, so bind-mounted files (source + `.dev-ssh/` keys) line up. If your Pi user's
`id -u` ≠ 1000, build with `--build-arg USER_UID=<n> USER_GID=<n>`.

## First run inside the container (`/workspace`, user `dev`)

```sh
# Git remotes + identity (keys are already wired into ~/.ssh by the entrypoint).
git remote set-url origin git@github.com:gsfernandes81/destiny-director.git
git remote add shark git@github.com-shark:geolocatingshark/destiny-director.git
git config user.name  "gsfernandes81"
git config user.email "<your git email>"
ssh -T git@github.com; ssh -T git@github.com-shark   # both should greet their user

# Prove the toolchain.
uv sync                                  # editable project into /home/dev/venv
make test                                # DB-free unit suite (SQLite)
uv run ruff check dd && uv run ty check dd
```

## Claude Code

Node 22 + `@anthropic-ai/claude-code` are baked in; `~/.claude` is a persistent volume,
so login survives rebuilds.

```sh
claude        # then /login: it prints a URL — open on your laptop, paste the code back
```

If Claude Code's Bash sandbox blocks writes to `~/.cache/uv` (breaks uv/ruff/ty/pytest),
relax the sandbox in-container — the container is already an isolation boundary.

## Railway CLI

The Railway CLI is baked into the image (installed from the release tarball — the
`@railway/cli` npm package 404s on arm64). With `RAILWAY_API_TOKEN` in `.env` the container is already
authenticated; verify with `railway whoami`. (Alternative: `railway login --browserless`,
persisted via the `dd-railway` volume.) `make deploy-beacon-dev` / `deploy-anchor-dev`
then run from inside the container.

> **Prod deploys require explicit confirmation each time (see CLAUDE.md). Never deploy
> prod on your own initiative.**

## MySQL / migrations (integration scope)

```sh
docker compose -f docker-compose.dev.yml up -d mysql   # if not already up
make atlas-migration-apply                              # apply against MYSQL_URL
TEST_USE_MYSQL=1 make test-integration                 # integration suite on MySQL
```

Applying migrations needs only the running `mysql` service. Authoring new migrations
(`make atlas-migration-plan`, i.e. `atlas migrate diff`) uses `dev = docker://mysql/8/dev`
in `atlas.hcl`, which spins a throwaway MySQL via Docker — that path needs the mounted
`/var/run/docker.sock` (already in the compose file). Note the non-root `dev` user may
lack permission on the socket; if `atlas migrate diff` fails with a socket permission
error, add `group_add: ["<host docker gid>"]` (from `getent group docker` on the Pi) to
the `dev` service. Applying migrations does not touch the socket. `mysql:8` wants ~1GB+
RAM; keep it stopped when not integration-testing.

## Editing

Terminal-only by default: edit via Claude Code or a terminal editor (`vim`/`nano`/`helix`,
`apt`-installable in-container) inside `docker exec`. No editor is installed on the Pi host.

### Zed remote / SSH access

The container also runs an in-container sshd (port 2222) so **Zed can remote directly into
`/workspace`** (reversing the original terminal-only, no-sshd/no-ports decision). It runs as
the non-root `dev` user, key-only — so only `dev` can log in, preserving the `docker exec`
model and uid-1000 file ownership. Authorized keys are **not hardcoded**: the compose file
bind-mounts the Pi host user's `.ssh` directory (from `DEV_SSH_AUTHORIZED_KEYS` in `.env`,
e.g. `/home/<pi-user>/.ssh/`) read-only, and sshd reads `authorized_keys` from it — so it
authorizes the same keys that already log into the Pi host. Host `2222` publishes the
container's sshd; the host key is persisted in the `dd-ssh-host` volume so Zed's
`known_hosts` stays stable across `make dev-down && make dev-up`.

To use it:

1. Ensure `DEV_SSH_AUTHORIZED_KEYS` is set in `.env` (Pi host user's `.ssh` dir) and that
   its `authorized_keys` lists the public key you'll connect with (Zed's key).
2. `make dev-up` rebuilds the image (with `openssh-server`) and starts sshd on 2222.
3. Locally: `ssh -p 2222 dev@<pi-ip>` should log in as `dev`. For access off-LAN, point a
   **Cloudflare tunnel** at the Pi host's TCP port 2222 (configured from the Cloudflare
   dashboard — out of repo scope; key-based SSH only, pair with Cloudflare Access as you
   see fit).
4. In Zed, add an SSH remote to that host as user `dev` with the matching private key and
   open `/workspace`. Zed uploads its server and connects; SSH sessions inherit the app env
   (`.env` vars + the venv on `PATH`) via `~/.ssh/environment`, so tools resolve as they do
   under `docker exec`.

The old `docker exec -it dd-dev fish` path still works unchanged.
