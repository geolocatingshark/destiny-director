# Plan (stub): show the error reference code in the uncaught-error reply

> **Status: STUB / not a regression (2026-07-07).** The user-facing reply on an
> uncaught command error is **new on the CV2 branch** — prod (`f54683f`) never replied
> to the invoker at all (`_report_uncaught_command_error` was just
> `log_command_failure(exc); return True`). So the missing reference code is a gap in a
> new feature, not a regression. Captured here for a later pass rather than fixed inline.

## The gap

`dd/common/discord_logging.py:_report_uncaught_command_error` now shows the invoker a
uniform ephemeral CV2 error:

```
Something went wrong
`/<name>` hit an unexpected error. It's been logged — please try again.
```

It does **not** include the deterministic reference code. Every alert in the channel is
headed with that code (`_reference_for_record`), and `discord_error_logger` returns it
"shown to the user … so it matches the code on the resulting alert" — but this reply
drops it, so a user can't quote a code that ties their report to the logged alert.

## Proposed fix (small)

The code is `reference_code(identity_for_exc(cause))` where `cause = exc.causes[0]` —
the same identity `log_command_failure` already logs with. Surface it:

- Make `log_command_failure` return the code alongside the name (e.g. `(name, code)`),
  computing `reference_code(identity_for_exc(cause))` once so the reply and the alert
  provably share it. `identity_for_exc` / `reference_code` are already imported here.
- Include it in the CV2 error body, e.g. `… It's been logged (ref: \`{code}\`) — please
  try again.` Keep the message ephemeral and generic (no traceback/detail to the user).

## Verification

- Unit: a fake `ExecutionPipelineFailedException` → `log_command_failure` returns a code
  equal to `reference_code(identity_for_exc(cause))`; the reply body contains that code.
- Manual: trigger an uncaught command error in dev; confirm the ephemeral reply shows a
  `ref:` code that matches the code on the alert-channel post.
- Gate: `uv run ruff check` · `uv run ty check` · `uv run python -m pytest`.
