# Message Content Intent verification

Record of Destiny Director's (beacon, app ID `986338748813934642`) October 2026
re-authorisation for Discord's privileged **Message Content** intent. Reuse it for the
next review.

- [`form.md`](form.md) — the review form's questions and our answers, as filled in on
  25 October 2026.
- [`screenshots/`](screenshots) — the use-case screenshots for the form's
  "screenshots and/or videos" question (see below).

## Facts the answers rely on

Check these against the code before re-using an answer. If one has changed, update the
answer and [`PRIVACY.md`](../../PRIVACY.md) together.

- Only beacon asks for a privileged intent, and only Message Content:
  `intents=h.Intents.ALL_UNPRIVILEGED | h.Intents.MESSAGE_CONTENT`
  (`dd/beacon/__main__.py`). Anchor uses `ALL_UNPRIVILEGED` alone. Neither bot asks for
  **Server Members** or **Presence**. The form lists all three because it is generic;
  only Message Content was ticked.
- Message content is acted on in two places only:
  - **mirror** (`dd/beacon/extensions/mirror.py`): announcement channels in Kyber's
    server and the control server (`MirroredChannel` sources).
  - **nav-page feeds** (`dd/beacon/nav.py`, `extensions/free_games.py`,
    `extensions/twab.py`): the same team channels.

  Every other message is filtered out by channel ID, or by crosspost reference in
  `mirror_tracing.py`, without its content being looked at. Hikari's in-memory message
  cache (`max_messages=100`) is the only place other servers' content can sit, briefly.
- Stored content is limited to `mirror_message_version`: snapshots of each delivered
  announcement version. They are pruned after `mirror_retention_days` (14), **except**
  the latest delivered post per destination channel, which is kept until a newer post
  replaces it. That exception is why "30 days or less" is answered **No**.
- The source posts are written by the Kyber's Corner team and mostly published through
  the companion bot ("DD v1"), as in screenshot 1.
- Storage is Railway Postgres. Railway staff state that all volume data is encrypted
  at rest; the details are in Railway's Trust Center.

## Screenshots

| File | Shows |
| --- | --- |
| [`1-source-post-in-kybers-server.png`](screenshots/1-source-post-in-kybers-server.png) | The source: the weekly reset post, published by the companion bot DD v1 in the `#weekly-reset` announcement channel of Kyber's server. |
| [`2-mirrored-copy-in-following-server.png`](screenshots/2-mirrored-copy-in-following-server.png) | The copy Destiny Director delivered a minute later to a following server (our test server's Weekly Reset thread). |
| [`3-autopost-enable-disable.png`](screenshots/3-autopost-enable-disable.png) | The opt-in and opt-out: `/autopost weekly_reset` with Disable, then Enable again (newest is at the bottom). |
| [`4-on-demand-weekly-reset-command.png`](screenshots/4-on-demand-weekly-reset-command.png) | The on-demand command: `/weekly reset` replying with the latest post from the source channel, with week navigation. |

Paste a link to the screenshots folder in the form, from the prod repo once this is
merged to `main`:
<https://github.com/geolocatingshark/destiny-director/tree/main/docs/message-content-intent-verification/screenshots>

## Links

- Privacy Policy: <https://github.com/geolocatingshark/destiny-director/blob/main/PRIVACY.md>
- Terms of Service: <https://github.com/geolocatingshark/destiny-director/blob/main/TOS.md>
