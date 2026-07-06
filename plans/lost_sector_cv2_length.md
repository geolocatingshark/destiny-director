# Plan (STUB): fit Lost Sector content within the Components V2 4000-char cap

> **Status: STUB (2026-07-06).** Captured while fixing the dev `/ls` breakage. The
> navigator side is fixed (converted pages are now truncated — see
> `dd/beacon/nav.py:_capped_container_from_embeds`). This plan is about the *native*
> Lost Sector post itself, which is also at risk. Related:
> [[cv2_char_cap_alerts]] (`plans/cv2_char_cap_alerts.md`).

## The "strange" part: embeds and CV2 do NOT share length limits

A post that was legal as an embed can be illegal as Components V2:

| | Embed | Components V2 |
|---|---|---|
| Total displayable text | **6000** | **4000** |
| Single text field | description **4096** | one text display, counts to the 4000 total |

So a Lost Sector post whose embed description was ~4000–4096 chars was *fine* as an
embed but is already over the CV2 cap — before the converter adds its markdown
(`## [title](url)`, `-# footer`, `**field**`, masked links), which pushes it further
over. That plus custom-emoji cost (each `<:name:id>` is ~24–30 chars, and details add
several per sector) is why the flattened page overflows. Discord also counts in **UTF-16
code units**, so a page at Python-`len` 4000 can still be rejected.

## Current state / risks

- **Navigator (fixed):** `_capped_container_from_embeds` trims converted history pages
  to `_CV2_TEXT_BUDGET` (3900) with a "… (truncated)" note; `send`/`_edit` catch any
  residual `ClientHTTPResponseError` and show a fallback.
- **Native post (`dd/common/lost_sector.py:format_post`) still rough:** it truncates to
  `description[:4000]` (lost_sector.py:194-201), which
  1. cuts the **tail**, silently dropping the "Rewards / View more / Support Us" footer
     on detail-heavy days;
  2. uses Python `len()` (not UTF-16), so it can still emit a page Discord rejects;
  3. truncates mid-token (can split an `<:emoji:id>` mention).

## Options to genuinely fit LS content

1. **Budget-aware building in `format_post`** — assemble in priority order (header →
   sectors → footer), appending details only while under a UTF-16 budget with margin,
   so the footer/links always survive. Preferred.
2. **Reduce what counts toward 4000:**
   - swap some custom emoji for unicode glyphs (1–2 chars vs ~26);
   - condense/de-duplicate the champions & shields lines from `format_data`;
   - cap `format_data` to the first N sectors or a one-line compact form on long days.
3. **Share the cap logic** — a common `fit_cv2_text(text, budget)` used by both
   `format_post` and the navigator, counting UTF-16 units and cutting on a safe
   boundary with a visible note.

## Verification
- Unit: feed `format_post` a many-sector, details-enabled rotation; assert the built
  container's text ≤ 4000 (UTF-16) AND the Rewards/links footer is still present.
- Dev: enable LS details, drive `/ls` across history + lookahead; confirm every page
  renders (truncated where needed) with the footer intact and no 400/429.
