# Preview render fixtures — the shared golden corpus

These files freeze what a Discord message preview renders to, so the renderer can be
moved from Python to JavaScript (`plans/preview_renderer_unification.md`) and be
*provably* behaviour-preserving rather than hopefully so.

The corpus is deliberately plain JSON, because **both** sides read it:

- `dd/anchor/tests/test_preview_fixtures.py` renders each case through today's Python
  renderer and asserts it still matches.
- `dd/anchor/web_static/tests/cv2_render.test.js` (from phase 1 on) renders the same
  case through the shared JS renderer and asserts the same result.

## Case shape

```json
{
  "name": "container_with_accent",
  "render": "snapshot" | "authored" | "diff" | "post_spec",
  "kind": "cv2" | "classic",
  "payload": { … },
  "old_kind": "cv2",
  "old_payload": { … },
  "spec": { "body": "…", "image_url": "…", "buttons": [["label", "url"]] },
  "emoji": { "kyber": "https://cdn.discordapp.com/emojis/1.png" },
  "expected_html": "…"
}
```

`render` picks the entry point:

| value | Python entry point | emoji resolution |
|---|---|---|
| `snapshot` | `cv2_render.render_snapshot(payload, kind)` | CDN, from captured `<:name:id>` |
| `authored` | `cv2_html.render_cv2_nodes_html(payload["components"], emoji)` | the guild emoji dict, from typed `:name:` — and **sanitized first** |
| `diff` | `cv2_render.render_diff(payload, kind, old_payload, old_kind)` | CDN |
| `post_spec` | `hybrid_post_core.render_post_spec(PostSpec.cv2(**spec), emoji)` | the guild emoji dict |

`emoji` maps a name to a URL string. Python wraps each in a duck-typed stand-in (the
substituter only reads `.url`); JS accepts the bare-string map directly
(`cv2_model.js` `emojiEntry`).

## The frozen clock

`<t:…:R>` renders a *relative* string off the render-time clock, so the corpus pins
`now` to `NOW_UNIX` in the test module (2026-07-30T17:00:00Z) and the test patches
`_format_ts` to bind it. Every other format letter is absolute and needs no freezing.
The JS renderer already threads `now` as a parameter (`renderMd(content, emoji, now)`),
so it takes the same value explicitly.

## Regenerating

`expected_html` is generated, never hand-written:

```sh
UPDATE_PREVIEW_FIXTURES=1 make test -- dd/anchor/tests/test_preview_fixtures.py
```

**Regenerate only when you mean to change rendered output**, and read the diff — that
diff is the whole point of the corpus. During the port, a case whose behaviour is
deliberately being corrected (the drift items in the plan) gets its expectation updated
in the same commit that corrects it, so the change is visible in review.
