# Apply the default accent colour to cv2 builder containers (and keep it editable)

## Goal

When a user adds a **Container** in the interactive Components-V2 builder
(`/post components` → add "Container"), it currently appears with **no accent colour**
— a plain grey block — until they open its Edit modal and type a hex. Every *other*
container-producing path already defaults to the brand colour. Seed new builder
containers with the same default so they look right immediately, while leaving the
existing modal free to change or clear it.

## Background (as-is)

- A "cv2 container" is a raw Components-V2 node dict, minted by `make_container()` in
  `dd/anchor/cv2_nodes.py`:
  ```python
  def make_container() -> Node:
      return {"type": CONTAINER, "components": []}   # no accent_color
  ```
  It's registered as the "container" constructor (`_ADD_CONSTRUCTORS`) and invoked when
  the user picks "Container" from the add menu (`cv2_builder.py`).

- Every **other** container path already applies `cfg.embed_default_color` when no
  colour is set:
  - `build_container()` — `dd/common/components.py`
  - `embeds_to_container()` — `dd/common/components.py`
  - Direct `ContainerComponentBuilder(accent_color=cfg.embed_default_color)` in
    `weekly_reset.py`, `xur.py`, `eververse.py`, `ada.py`, `portal_ops.py`,
    `lost_sector.py`, `testing.py`.

  So `make_container` is the lone outlier.

- Colour lives on the node under `accent_color` (int RGB). The builder container is
  **already editable** in-Discord via its Edit modal, driven by `container_fields` /
  `mutate_container` (`cv2_nodes.py`): the modal pre-fills from
  `node.get("accent_color")` and blank clears it. There is no web-UI or per-guild
  colour config — colour is a single global (`cfg.embed_default_color`,
  env `EMBED_DEFAULT_COLOR`).

## Plan

1. **Seed the default in `make_container`.** Return
   `{"type": CONTAINER, "components": [], "accent_color": int(cfg.embed_default_color)}`,
   matching `build_container` / `embeds_to_container`. Import `cfg` from
   `...common` in `cv2_nodes.py`.

2. **"Also make it editable" — confirm, no new code.** The Edit modal already exposes
   the colour: with a default seeded, `container_fields` pre-fills the default hex,
   `mutate_container` lets the user change it (valid hex) or clear it (blank →
   `pop("accent_color")`). So a seeded container is both defaulted *and* editable — the
   two halves of the task. Add a test asserting the modal round-trip still works on a
   defaulted container.

## Notes / non-goals

- **Do not** touch the synthetic root wrapper in `resolve_path`
  (`{"type": CONTAINER, "components": nodes}`) — that's a path-navigation stand-in, not
  a posted container.
- No per-guild / per-feed theming — colour stays the one global default, consistent
  with the rest of the codebase.

## Verification

- New/updated tests in `dd/anchor/tests/test_cv2_nodes.py`:
  - `make_container()["accent_color"] == int(cfg.embed_default_color)`.
  - existing `test_container_color_and_spoiler` still passes (change → clear → invalid).
- `make test` / `make lint` / `make typecheck` green.
