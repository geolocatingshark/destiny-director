#!/usr/bin/env bash
set -e

# Git identities: keys + SSH config live in the gitignored .dev-ssh/ dir, which
# rides along with the bind-mounted repo clone. Wire them into ~/.ssh on start.
if [ -d /workspace/.dev-ssh ]; then
  mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
  chmod 600 /workspace/.dev-ssh/id_ed25519_* 2>/dev/null || true
  [ -f /workspace/.dev-ssh/config ] && ln -sf /workspace/.dev-ssh/config "$HOME/.ssh/config"
  # Push over SSH with the keys above WITHOUT editing the shared .git/config
  # remote (keeps the host on HTTPS): rewrite GitHub HTTPS->SSH in the
  # container's own ~/.gitconfig only.
  git config --global url."git@github.com:".insteadOf "https://github.com/"
fi

# Deps are baked into /home/dev/venv at build time; add the editable project now
# that /workspace is mounted. Best-effort so the container still comes up if the
# clone is absent or offline.
[ -f /workspace/pyproject.toml ] && uv sync --frozen || true

# Keep the container alive; all work happens via `docker exec`.
exec sleep infinity
