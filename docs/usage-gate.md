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
- If either cap is **≥ 90%**, the hook exits `2` (blocks the tool call) and tells
  Claude, via stderr, to call `ScheduleWakeup` for the reset time and stop.
- **`ScheduleWakeup` / `TaskUpdate` / `TaskCreate` are allowlisted** — otherwise the
  gate would block the very tool Claude needs to schedule its own pause.
- **Fails open** on any error (network, expired/401 token, changed response shape)
  so it can never wedge a session.

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

- `THRESHOLD=90` — pause at/above this percentage.
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
# cap reaches >= THRESHOLD%. Fires before EVERY tool call, but fetches usage at
# most once per TTL seconds (cached), so the per-step cost is a local file read.
#
# FAILS OPEN on any error (network, expired token, changed response shape) so it
# can never wedge a session.
#
# This file is NOT in version control. Setup + revert: docs/usage-gate.md in the
# Destiny Director repo.

set -u
CACHE="$HOME/.cache/claude-usage.json"
TTL=90          # seconds between live fetches
THRESHOLD=90    # pause at/above this utilization percentage

mkdir -p "$(dirname "$CACHE")"

# The tool being invoked arrives as JSON on stdin.
TOOL=$(python3 -c "import json,sys
try: print(json.load(sys.stdin).get('tool_name',''))
except Exception: print('')" 2>/dev/null)

# ALLOWLIST: never block the tools Claude needs to schedule its own pause / track
# state, or it can't defer and just spins on blocked calls.
case "$TOOL" in
  ScheduleWakeup|TaskUpdate|TaskCreate) exit 0 ;;
esac

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

python3 - "$CACHE" "$THRESHOLD" <<'PY'
import json, sys
cache, thresh = sys.argv[1], float(sys.argv[2])
try:
    d = json.load(open(cache))
except Exception:
    sys.exit(0)  # fail open
for k in ("five_hour", "seven_day"):
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
