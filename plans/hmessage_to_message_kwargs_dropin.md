# Stub — make `HMessage.to_message_kwargs` a drop-in for the mirror's `_send_payload`

Status: **deferred / not started.** Investigation stub only.

## Idea

The beacon mirror hand-rolls `mirror_worker._send_payload` to build `channel.send` /
`message.edit` kwargs, and it is *nearly* `HMessage.from_message(msg).to_message_kwargs()`
(`dd/hmessage/message.py`). If we make `to_message_kwargs` a safe drop-in, `_send_payload`
could largely collapse onto it.

The core hardening this needs: **a validation step that drops embeds when a message has
BOTH components and embeds** (Discord treats CV2 components and content/embeds as mutually
exclusive). `to_message_kwargs` today handles it *implicitly* (`if self.components:` sends
components only) — make that an explicit, defensive validation so the invariant is
enforced at construction/emit rather than relied upon.

## Remaining gaps to close before it's a true drop-in (for the mirror's needs)

- **Attachments shape:** `HMessage.from_message` stores `attachments` as **URL strings**
  (`att.url`), whereas `_send_payload` passes `h.Attachment` objects. Both re-host from
  URL, but confirm parity (ordering, spoiler flags, filenames).
- **`role_mentions=True`** — `_send_payload` sets it; `to_message_kwargs` does not.
- **Per-dest role ping** — `_send_payload`/`_cv2_components_for` append the destination's
  spoilered role ping (into the CV2 container, or onto content). `to_message_kwargs` is
  destination-agnostic, so the ping compositing must stay outside it (see the emoji-rewrite
  revision, which already does a per-dest shallow rebuild for the ping).
- **Non-CV2 component drop** — already matched: `from_message` only rebuilds components
  when the source carries `IS_COMPONENTS_V2`, so a plain message with buttons yields
  `components=[]` (buttons dropped), same as `_send_payload`. ✓ (no work needed)

## Why deferred

Out of scope for the emoji-rewrite work. The emoji revision only needs `HMessage` as the
*rewrite surface* (`HMessage.from_message` → rewrite → read fields back in `_send_payload`);
it does **not** require `to_message_kwargs` to replace `_send_payload`. Revisit this as a
separate simplification once the emoji path lands.
