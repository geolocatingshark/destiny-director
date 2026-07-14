# Claude usage-limit gate (PreToolUse hook)

A per-developer Claude Code hook that **pauses agentic execution when the Claude
subscription usage cap gets close to its limit**, so a long autonomous run stops
itself instead of dying mid-task at the limit. It is a personal workflow tool,
not part of the Destiny Director application.

## What lives where (important)

Only **this doc is committed**. The moving parts are **user-level** (in `~/.claude/`)
and are deliberately *not* in the repo — so the hook is never forced on teammates,
CI, or other checkouts. If you move to a new machine (or wipe `~/.claude`), this
doc is how you recreate it.

| Piece | Path | Committed? |
|---|---|---|
| Hook script | `~/.claude/hooks/usage-gate.sh` | No (user home) |
| Hook registration | `~/.claude/settings.json` → `hooks.PreToolUse` | No (user home) |
| Usage cache | `~/.cache/claude-usage.json` | No (regenerated) |
| This doc | `docs/usage-gate.md` | **Yes** |

## How it works

- Registered as a **`PreToolUse`** hook with `matcher: "*"`, so the harness runs it
  **before every tool call** — i.e. before every step of agentic execution. The
  harness enforces it; it does not depend on the model remembering to check.
- It **evaluates every step but only fetches every ~90s** (a TTL cache), so the
  per-step cost is a local file read, not a network round-trip.
- Data source: `GET https://api.anthropic.com/api/oauth/usage` with the Claude
  OAuth token from `~/.claude/.credentials.json` (`claudeAiOauth.accessToken`).
  The response gives `five_hour.utilization` and `seven_day.utilization` as 0–100
  percentages plus `resets_at` timestamps. (The short cap is a **rolling 5-hour**
  window, not "daily"; the long one is the **7-day** weekly cap.)
- Each cap has its own threshold: **5-hour ≥ 90%** or **7-day ≥ 95%**. When a cap
  reaches its threshold the hook exits `2` (blocks the tool call) and tells Claude,
  via stderr, to call `ScheduleWakeup` for the reset time and stop. (The 7-day
  window is deliberately allowed closer to the limit — it resets slowly, so pausing
  a full week of work at 90% is too conservative.)
- **`ScheduleWakeup` / `TaskUpdate` / `TaskCreate` are allowlisted** — otherwise the
  gate would block the very tool Claude needs to schedule its own pause.
- **Per-session bypass (user-confirmed).** A user can authorise a single session to
  ignore the gate — see the section below. The grant is keyed to the harness
  `session_id`, so it covers exactly that session and no other; it disappears when
  the session ends.
- **Fails open** on any error (network, expired/401 token, changed response shape)
  so it can never wedge a session.

## Per-session bypass

Sometimes a run is worth pushing past the gate (a migration mid-flight, a release
you want finished this evening). The gate supports a bypass that is scoped to **one
session** and requires the **user's explicit go-ahead** — it is never Claude's call
to make unprompted.

- **The grant is a file named for the session id** under
  `~/.cache/claude-usage-bypass/<session_id>`. While it exists, that session's tool
  calls are never blocked. A different session with a fuller cap is still gated
  normally — the grant does not leak.
- **How the user confirms.** When (and only when) the user explicitly asks to bypass
  the gate for the session, Claude runs a Bash command carrying the
  `USAGE_GATE_BYPASS` token. The hook recognises it, records the grant for the
  current `session_id`, and — crucially — lets that command through **even if
  already over the cap**, so the hatch works exactly when it's needed:

  ```bash
  echo "USAGE_GATE_BYPASS: authorise this Claude session to bypass the usage gate (user-approved)."
  ```

- **To revoke** (restore normal thresholds for the session):

  ```bash
  echo "USAGE_GATE_BYPASS_REVOKE: restore normal usage-gate thresholds for this session."
  ```

- **Trade-off:** bypassing re-accepts the original risk — the run can still hard-stop
  at the *real* subscription cap mid-task. The gate only defers that; bypass removes
  the guard rail for that one session. The stderr note on authorising says as much.

The behavioural directive ("only on the user's explicit say-so") lives in the
developer's Claude memory, not in `CLAUDE.md`, since the whole hook is a personal
workflow tool and not forced on teammates or CI.

### `/usage-bypass` slash command

The bypass is fronted by a **user-level slash command** so the user triggers it
explicitly — typing `/usage-bypass` *is* the confirmation. Like the hook it is
user-level (`~/.claude/commands/usage-bypass.md`) and **not committed**, so this doc
is how to recreate it.

- `/usage-bypass` → authorise the bypass for the current session.
- `/usage-bypass revoke` → clear it, restoring normal thresholds.

This works from **any client attached to this box** — a terminal session, or a
web/mobile client remote-controlling the same server-mode Claude. All spawned
sessions run on this filesystem, so they read the same `~/.claude/commands/` and the
same hook, and each carries its own `session_id` (so the grant stays scoped to the
one session you drove). It does **not** apply to a cloud sandbox that merely clones
the repo — that has neither `~/.claude/commands/` nor the hook.

Recreate `~/.claude/commands/usage-bypass.md`:

```markdown
---
description: Authorise (or revoke) a usage-gate bypass for THIS Claude session
argument-hint: "[revoke]"
---

The user is explicitly authorising a usage-gate bypass for the current session by
invoking this command. Invoking it IS the required user confirmation — act on it
directly, do not ask again.

Argument: "$ARGUMENTS"

- If the argument is `revoke`, run exactly this Bash command:
  `echo "USAGE_GATE_BYPASS_REVOKE: restore normal usage-gate thresholds for this session (user-approved)."`
  Then tell the user the bypass is revoked and the normal thresholds (5-hour ≥ 90%,
  7-day ≥ 95%) apply to this session again.

- Otherwise (no argument), run exactly this Bash command:
  `echo "USAGE_GATE_BYPASS: authorise this Claude session to bypass the usage gate (user-approved)."`
  Then tell the user this session will no longer be paused by the usage gate until it
  ends — and note the trade-off: the run can still hard-stop at the *real* subscription
  cap mid-task.

Mechanism: the PreToolUse usage-gate hook (`~/.claude/hooks/usage-gate.sh`) intercepts
that echo, records/clears a grant file keyed to this session's `session_id`
(`~/.cache/claude-usage-bypass/<session_id>`), and lets the command through even when
already over the cap. It is scoped to this one session only. See docs/usage-gate.md.
```

### Gotcha worth remembering

The endpoint is documented as `claude.ai/api/oauth/usage`, but that host is behind
a Cloudflare managed challenge and returns HTML/`403` to non-browser clients. Use
the **`api.anthropic.com`** host, which serves the same endpoint without a challenge.

## Setup / reinstall instructions

1. Create the hook script:

   ```bash
   mkdir -p ~/.claude/hooks
   # Paste the script from the "Hook script" section below into:
   #   ~/.claude/hooks/usage-gate.sh
   chmod +x ~/.claude/hooks/usage-gate.sh
   ```

2. Register it in `~/.claude/settings.json` (merge — keep existing keys like `theme`):

   ```bash
   python3 - <<'PY'
   import json, pathlib
   p = pathlib.Path.home() / ".claude" / "settings.json"
   d = json.loads(p.read_text()) if p.exists() else {}
   d.setdefault("hooks", {})["PreToolUse"] = [
       {"matcher": "*", "hooks": [
           {"type": "command", "command": "$HOME/.claude/hooks/usage-gate.sh"}
       ]}
   ]
   p.write_text(json.dumps(d, indent=2) + "\n")
   PY
   ```

3. Verify:

   ```bash
   echo '{"tool_name":"Bash"}' | ~/.claude/hooks/usage-gate.sh; echo "exit=$?"   # expect 0
   cat ~/.cache/claude-usage.json                                                # usage JSON
   ```

   A fresh Claude Code session picks up `~/.claude/settings.json` on start.

### Tuning knobs (top of the script)

- `THRESHOLD_FIVE_HOUR=90` / `THRESHOLD_SEVEN_DAY=95` — per-window pause
  thresholds (pause at/above this percentage).
- `TTL=90` — seconds between live fetches (raise to fetch less often; evaluation
  every step is free either way).
- Allowlist `case` line — tools that are never blocked.

## Revert / uninstall instructions

1. Remove the hook registration (leaves other settings intact):

   ```bash
   python3 - <<'PY'
   import json, pathlib
   p = pathlib.Path.home() / ".claude" / "settings.json"
   d = json.loads(p.read_text())
   hooks = d.get("hooks", {})
   hooks.pop("PreToolUse", None)
   if not hooks:
       d.pop("hooks", None)
   p.write_text(json.dumps(d, indent=2) + "\n")
   PY
   ```

2. Delete the script and cache:

   ```bash
   rm -f ~/.claude/hooks/usage-gate.sh ~/.cache/claude-usage.json
   rm -f ~/.claude/commands/usage-bypass.md
   rm -rf ~/.cache/claude-usage-bypass
   rmdir ~/.claude/hooks 2>/dev/null || true
   ```

3. Start a new Claude Code session (settings are read at session start). To disable
   temporarily without uninstalling, just `chmod -x ~/.claude/hooks/usage-gate.sh`
   and it fails open, or remove the `PreToolUse` block per step 1.

## Caveats

- **Undocumented endpoint.** `/api/oauth/usage` is not a published API — field names
  or the host could change. The hook fails open by design so a shape change degrades
  to "no gate," never to a stuck session.
- **Token expiry.** Claude Code refreshes `claudeAiOauth.accessToken` in place; the
  hook re-reads the file each fetch. An expired token → 401 → fail open. Re-`/login`
  if the gate silently stops working.
- **Not a daily cap.** The short window is rolling 5-hour, not calendar-daily.

## Hook script

Full contents of `~/.claude/hooks/usage-gate.sh` (kept here because the script is
not committed):

```bash
#!/usr/bin/env bash
# usage-gate.sh — Claude Code PreToolUse hook.
#
# Pauses agentic execution when the 5-hour or 7-day Claude subscription usage
# cap reaches >= its per-window threshold. Fires before EVERY tool call, but
# fetches usage at most once per TTL seconds (cached), so the per-step cost is a
# local file read.
#
# FAILS OPEN on any error (network, expired token, changed response shape) so it
# can never wedge a session.
#
# This file is NOT in version control. Setup + revert: docs/usage-gate.md in the
# Destiny Director repo.

set -u
CACHE="$HOME/.cache/claude-usage.json"
TTL=90          # seconds between live fetches
# Per-window thresholds: pause at/above this utilization percentage.
THRESHOLD_FIVE_HOUR=90
THRESHOLD_SEVEN_DAY=95

BYPASS_DIR="$HOME/.cache/claude-usage-bypass"
mkdir -p "$(dirname "$CACHE")" "$BYPASS_DIR"

# The hook payload arrives as JSON on stdin. Parse it once into: TOOL (for the
# allowlist), SESSION (to scope a bypass to exactly one session), and ACTION (a
# user-issued authorise/revoke request, spotted by a token in a Bash command).
INPUT=$(cat)
{ read -r TOOL; read -r SESSION; read -r ACTION; } < <(printf '%s' "$INPUT" | python3 -c "
import json, sys
try: d = json.load(sys.stdin)
except Exception: d = {}
tool = d.get('tool_name', '') or ''
sid = d.get('session_id', '') or ''
ti = d.get('tool_input', {}) or {}
cmd = ti.get('command', '') if isinstance(ti, dict) else ''
if tool == 'Bash' and 'USAGE_GATE_BYPASS_REVOKE' in cmd:
    action = 'revoke'
elif tool == 'Bash' and 'USAGE_GATE_BYPASS' in cmd:
    action = 'authorize'
else:
    action = 'none'
print(tool); print(sid); print(action)
" 2>/dev/null)

# ALLOWLIST: never block the tools Claude needs to schedule its own pause / track
# state, or it can't defer and just spins on blocked calls.
case "$TOOL" in
  ScheduleWakeup|TaskUpdate|TaskCreate) exit 0 ;;
esac

# PER-SESSION BYPASS. A user may authorise THIS session to ignore the gate. The
# grant is a file named for the session id, so it covers exactly one session and
# never leaks to another. Claude issues the authorise/revoke request ONLY on the
# user's explicit say-so (see docs/usage-gate.md), by running a Bash command that
# carries the USAGE_GATE_BYPASS token; this hook turns that into the grant and
# lets the command through even when already over the cap (so the hatch works
# precisely when it's needed).
GRANT="$BYPASS_DIR/${SESSION:-__nosession__}"
case "$ACTION" in
  authorize)
    [ -n "$SESSION" ] && : > "$GRANT"
    echo "USAGE GATE: bypass AUTHORISED for this session; the gate will not block it until the session ends. Note the run can still hard-stop at the real subscription cap." >&2
    exit 0 ;;
  revoke)
    [ -n "$SESSION" ] && rm -f "$GRANT"
    echo "USAGE GATE: bypass REVOKED for this session; normal thresholds apply again." >&2
    exit 0 ;;
esac

# Already-authorised session → never block.
[ -n "$SESSION" ] && [ -f "$GRANT" ] && exit 0

# Refresh the cache only when missing or older than TTL
# ("check every step, fetch every few steps").
now=$(date +%s)
mtime=$(stat -c %Y "$CACHE" 2>/dev/null || echo 0)
if [ ! -f "$CACHE" ] || [ $((now - mtime)) -ge "$TTL" ]; then
  TOKEN=$(python3 -c "import json;print(json.load(open('$HOME/.claude/.credentials.json'))['claudeAiOauth']['accessToken'])" 2>/dev/null) || exit 0
  [ -n "$TOKEN" ] || exit 0
  # NOTE: the api.anthropic.com host serves this endpoint; claude.ai is behind a
  # Cloudflare challenge and returns HTML/403 to non-browser clients.
  curl -sf --max-time 10 -H "Authorization: Bearer $TOKEN" \
       https://api.anthropic.com/api/oauth/usage -o "$CACHE.tmp" 2>/dev/null \
    && mv "$CACHE.tmp" "$CACHE" || { rm -f "$CACHE.tmp" 2>/dev/null; exit 0; }
fi

python3 - "$CACHE" "$THRESHOLD_FIVE_HOUR" "$THRESHOLD_SEVEN_DAY" <<'PY'
import json, sys
cache = sys.argv[1]
thresholds = {"five_hour": float(sys.argv[2]), "seven_day": float(sys.argv[3])}
try:
    d = json.load(open(cache))
except Exception:
    sys.exit(0)  # fail open
for k, thresh in thresholds.items():
    blk = d.get(k) or {}
    u = blk.get("utilization")
    if u is not None and u >= thresh:
        sys.stderr.write(
            f"USAGE GATE: {k} at {u:.0f}% (>= {thresh:.0f}%). "
            f"Do NOT retry tools or start new work. Call ScheduleWakeup with "
            f"delaySeconds set to reach {blk.get('resets_at', 'the reset time')}, "
            f"tell the user you are paused until reset, then stop.\n"
        )
        sys.exit(2)  # exit 2 => block the tool call; stderr is shown to Claude
sys.exit(0)
PY
```
