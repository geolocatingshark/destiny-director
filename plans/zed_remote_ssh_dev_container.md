# Plan: Zed-remote SSH access to the Pi dev container

## Context

The dev container (`dd-dev`, defined by `Dockerfile.dev` + `docker-compose.dev.yml` +
`docker-entrypoint.dev.sh`) was **deliberately** built with no in-container sshd and no
exposed ports — access is `docker exec` reached through the Pi *host's* own sshd (see
`plans/remote_pi_docker_dev_env.md`, which explicitly rejected an in-container sshd and a
`2222:2222` mapping). Terminal-only, no editor tunnelling.

The user now wants to **reverse that decision** so **Zed can remote directly into the
container**:

1. The container's sshd must accept the **same authorized keys as `/home/gavin/.ssh/`**
   (i.e. the Pi host user's `authorized_keys`).
2. Zed connects over SSH straight into the container.
3. The user will set up a Cloudflare tunnel from the dashboard pointing a chosen URL at
   **host port 2222**, which must map to the container's guest sshd.

**Approach (confirmed with user):** run a **non-root sshd as the existing `dev` user** on
unprivileged port 2222 (only `dev` can log in — preserves the current `docker exec` model
and uid-1000 ownership exactly), **inject the app env** (`.env` vars + venv on PATH) into
SSH sessions so Zed terminals work like `docker exec`, and **update the docs**.

**Authorized-keys source (per user):** no hardcoded default anywhere in code/config.
Instead a new `DEV_SSH_AUTHORIZED_KEYS` var in `.env` holds the Pi host user's `.ssh`
**directory** (`/home/gavin/.ssh/`); compose interpolates it as the source of a read-only
volume and sshd reads `authorized_keys` from inside it. Compose auto-reads the project
`.env` for interpolation, so the var is available for the volume source path.

## Changes

### 1. New file: `sshd_config.dev` (repo root)

Static sshd config, copied into the image. Non-root, key-only, port 2222:

```
# In-container sshd for Zed-remote / direct SSH. Runs as the non-root `dev` user
# on an unprivileged port; only `dev` can authenticate (non-root sshd only serves
# its own account). Host 2222 -> here (see docker-compose.dev.yml + Cloudflare).
Port 2222
ListenAddress 0.0.0.0

# Persisted in the dd-ssh-host volume so Zed's known_hosts survives rebuilds.
HostKey /home/dev/.ssh-host/ssh_host_ed25519_key
PidFile /home/dev/.ssh-host/sshd.pid

# Key-only auth; the Pi host user's .ssh dir is bind-mounted read-only at .host-ssh.
AuthorizedKeysFile /home/dev/.host-ssh/authorized_keys
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
UsePAM no                 # PAM needs root; non-root sshd must disable it

# Let SSH/Zed sessions inherit the env the entrypoint writes (~/.ssh/environment).
PermitUserEnvironment yes

Subsystem sftp internal-sftp
StrictModes yes
```

### 2. `Dockerfile.dev`

- Add **`openssh-server`** to the existing apt install line (keep `openssh-client`).
- Extend the pre-create `mkdir` (currently `/home/dev/.cache/uv /home/dev/.claude
  /home/dev/.config/railway`) to also create **`/home/dev/.ssh-host`** (host-key volume
  mountpoint, dev-owned). `/home/dev/.host-ssh` needs no pre-create — it's a **directory**
  bind mount, so it inherits the mounted `.ssh` dir's ownership (Pi `gavin`, uid 1000 ==
  container `dev`, mode 700), which satisfies `StrictModes yes`.
- `COPY --chown=${USER_UID}:${USER_GID} sshd_config.dev /home/dev/sshd_config`.
- Add `EXPOSE 2222` (documentation only).

### 3. `docker-entrypoint.dev.sh`

Keep the existing `.dev-ssh` git-key wiring and `uv sync`. Before the final exec:

- Ensure `~/.ssh-host` exists (700); generate the host key once if absent:
  `ssh-keygen -t ed25519 -f /home/dev/.ssh-host/ssh_host_ed25519_key -N "" -C dd-dev-host`.
- Write the container env for SSH sessions: `mkdir -p ~/.ssh && chmod 700 ~/.ssh`, then
  dump `env` (filtering noise like `PWD/SHLVL/_/HOME/OLDPWD/HOSTNAME`) to
  `~/.ssh/environment`, **prepending the venv** so tools resolve
  (`PATH=/home/dev/venv/bin:$PATH`); `chmod 600 ~/.ssh/environment`. (One value per line,
  no quotes — matches `PermitUserEnvironment` format; existing single-line vars like
  `FOLLOWABLES` are fine.)
- Replace `exec sleep infinity` with **`exec /usr/sbin/sshd -D -e -f /home/dev/sshd_config`**
  — sshd becomes PID 1, keeps the container alive, and serves SSH (`-e` -> `docker logs`).

### 4. `docker-compose.dev.yml` (`dev` service)

- Add a port mapping (replaces the "No ports" comment):
  ```yaml
  ports:
    - "2222:2222"   # host 2222 -> container sshd; Cloudflare tunnel points here
  ```
- Add two volumes:
  ```yaml
  - ${DEV_SSH_AUTHORIZED_KEYS}:/home/dev/.host-ssh:ro   # Pi host user's .ssh dir (from .env)
  - dd-ssh-host:/home/dev/.ssh-host                     # persist host key -> stable known_hosts
  ```
  **No default** — the source path comes solely from `DEV_SSH_AUTHORIZED_KEYS` in `.env`.
  If it's unset, compose warns and sshd finds no keys (no login), which is the intended
  fail-safe rather than a baked-in path.
- Add `dd-ssh-host:` to the top-level `volumes:` list.

### 5. `.env-example`

Document the new var so the template stays complete:
```
# Pi dev container: the Pi HOST user's .ssh directory, bind-mounted read-only so the
# in-container sshd (Zed-remote, port 2222) authorizes the same keys. Its authorized_keys
# must list the pubkey(s) that will connect (e.g. Zed's).
DEV_SSH_AUTHORIZED_KEYS=/home/gavin/.ssh/
```
(`cfg.py` ignores unknown env vars, so this is compose-only — it does not affect the bots.)

### 6. `docs/pi_dev_setup.md` (+ note in `plans/remote_pi_docker_dev_env.md`)

- Update the "terminal-only / no editor tunnelled" framing (`docs/pi_dev_setup.md:1-10`,
  `:139-143`) to add a **"Zed remote / SSH access"** subsection: the container now runs an
  sshd on port 2222 (login user `dev`, key-only, keys sourced from the Pi host's
  `/home/gavin/.ssh/authorized_keys`); expose it to Zed via the Cloudflare tunnel the user
  configures to host `:2222`.
- Add a one-line note in `plans/remote_pi_docker_dev_env.md` recording that the
  no-sshd/no-ports decision was intentionally reversed to enable Zed-remote, with the date.

`make dev-up` (already `--build`) rebuilds and restarts — no Makefile change required;
`DEV_SSH_AUTHORIZED_KEYS` is read from `.env` at compose interpolation time.

### On `.dev-ssh` (kept, orthogonal)

`.dev-ssh/` stays as-is: it holds the **outbound** git-push identities (two GitHub
accounts, personal + shark) wired into `~/.ssh` by the entrypoint. That is a distinct
concern from **inbound** login auth (the host `authorized_keys` above), so they are not
merged — merging would entangle GitHub push identity with container login. The entrypoint
keeps writing the app env to `~/.ssh/environment`, which coexists with the `.dev-ssh`
config symlink. No `.dev-ssh` change is required for elegance here.

## Key design notes

- **Non-root sshd** started by `dev` can only authenticate `dev` — exactly the desired
  scope, and it preserves the `docker exec` default-user and uid-1000 file ownership.
  `UsePAM no` is required (PAM needs root).
- **`StrictModes yes` passes** because the `authorized_keys` file is owned by uid 1000
  (Pi `gavin` == container `dev`) and its parent `/home/dev/.host-ssh` is pre-created
  dev-owned 700; all path components are owned by root or `dev`.
- **Host key in a named volume** (`dd-ssh-host`) keeps the server key stable across
  rebuilds, so Zed/known_hosts doesn't complain after `make dev-down && make dev-up`
  (only `make dev-down-volumes` resets it).

## Verification

1. `make dev-up` → `docker logs dd-dev` shows `Server listening on 0.0.0.0 port 2222`.
2. From the Pi host, with a key whose pubkey is in `/home/gavin/.ssh/authorized_keys`:
   `ssh -p 2222 dev@localhost` → logs in as `dev`; check env injected:
   `echo "$MYSQL_URL"; which uv; python --version` all resolve.
3. `ssh -p 2222 dev@localhost 'cd /workspace && make test'` runs the unit suite (proves
   env + venv + PATH reach non-interactive/exec sessions, i.e. what Zed uses).
4. Confirm the old path still works: `docker exec -it dd-dev fish` (unchanged, as `dev`).
5. Host-key stability: `make dev-down && make dev-up`, reconnect — no known_hosts warning.
6. Zed: add a remote `ssh dev@<cloudflare-host>` (or `dev@<pi-ip> -p 2222` locally); Zed
   uploads its server and opens `/workspace`. (Cloudflare tunnel host→:2222 is set up by
   the user from the dashboard — out of repo scope.)

## Steps required from you (the user)

1. **`.env` on the Pi:** add `DEV_SSH_AUTHORIZED_KEYS=/home/gavin/.ssh/` (the code change
   only ships the `.env-example` line).
2. **authorized_keys:** ensure `/home/gavin/.ssh/authorized_keys` contains the **public**
   key you'll connect with (e.g. the key Zed uses). Same file your Pi host login already
   uses — nothing new if that key already connects to the Pi.
3. **Rebuild:** run `make dev-up` on the Pi (rebuilds the image with `openssh-server` and
   starts sshd on 2222).
4. **Cloudflare:** in the dashboard, point your chosen URL at the Pi host's **TCP port
   2222** (the container port is published there). Key-based SSH only — pair it with
   Cloudflare Access as you see fit.
5. **Zed:** add an SSH remote to that URL as user `dev`, configured to use the matching
   private key; open `/workspace`.
