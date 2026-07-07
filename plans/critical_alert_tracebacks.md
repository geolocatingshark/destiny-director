# Restore tracebacks on directly-logged CRITICAL alerts

## Problem
`dd/common/utils.py` `discord_error_logger`: the `level > logging.ERROR` branch drops
`exc_info`, so directly-logged CRITICAL alerts render with no traceback (regressed by the
"make the level flag render overflow as a clean alert" commit). Storm-promoted criticals
are unaffected (they keep the original ERROR's traceback).

## Decision (owner)
- Attach a traceback to critical alerts **only when the exception was really raised**
  (`e.__traceback__ is not None`) — so real failures show a stack, but the CV2-overflow
  proactive notice (a fabricated, never-raised `ValueError`) stays clean.
- Xûr stall: **keep both** the immediate first-failure ERROR (already has a traceback) and
  the sustained-stall owner-pinging CRITICAL, but give the CRITICAL its own traceback.

## Where to look
- `dd/common/utils.py` `discord_error_logger` (~L402): in the `level > logging.ERROR`
  branch keep `str(e)` as the message but pass `exc_info=e` when `e.__traceback__` exists.
- `dd/anchor/extensions/xur.py` L603 & L638: stall `logger.critical(...)` only `%r`-interp
  the exception — add `exc_info=e`.
- Leave criticals with no exception in hand as-is (nothing to attach): mirror failure-ratio
  `dd/beacon/extensions/mirror.py` L551/L937; user-command clash
  `dd/beacon/extensions/user_commands.py` L405.

## Tests / docs
- `dd/common/tests/test_utils.py` L234-249: the existing CV2 test still passes (fabricated
  ValueError → still no traceback). Add a test: a really-raised exception at CRITICAL keeps
  `exc_info`. Update the "no traceback" wording in `utils.py` (~L390-404) and the
  `_alert_cv2_overflow` docstring in `components.py` (~L196).
