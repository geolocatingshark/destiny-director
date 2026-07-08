# Adding the "Conquests" section (+ the new-format deltas) to the Weekly Reset post

> **Status: PLANNED, not yet implemented (2026-07-07).** Extends the shipped
> `feat/weekly-reset-autopost` feature. Re-verify symbols + line numbers against the
> tree before executing (grep by name — this module shifts under you).

**Provenance.** Built from (1) a real sample of the *new* post format supplied by the
maintainer (reproduced in the Appendix), (2) a full read of the shipped feature
(`dd/anchor/extensions/weekly_reset.py`, `portal_ops.py`, `bungie_api/`), and (3) **two live
Bungie API probes** run while writing this plan against the **dev** environment
(`railway run --service anchor`, read-only): a public `GetPublicMilestones` probe and an
**authenticated component-204 probe using the live dev OAuth token**. The 204 probe is
decisive and **overturned the first draft's assumption** that Conquests could be
semi-auto-derived — see §1. Every claim below is tied to a concrete symbol or to those
probes.

## 0. The structural decisions

1. **Conquests is a new `build_body` block + one new context field, not a new subsystem.**
   It slots in exactly like every other manual weekly-reset field: a `WeeklyResetContext`
   field, a `build_body` block, and a `set_conquests` selector command. No new endpoint, no
   new fetch.

2. **Conquests is `manual-primary`, not `auto` or `semi` — the API cannot supply the tier
   grouping.** This is the headline finding of the live 204 probe (§1): the new Portal
   "Conquest" activities surface as **untiered `"…: Customize"` entries** with a constant
   `difficultyTier` and no Expert/Master/GM/Ultimate label anywhere in the data. The weekly
   tier→activity assignment shown in the sample post is Portal *presentation* metadata that
   component 204 does not expose. So the team curates this section by hand each week; the
   API's only assist is **autocomplete over the activity-name pool** (which the manifest
   index already makes cheap). A wrong auto-post here is worse than a fast manual one.

3. **The non-Conquests format deltas are string/label work, not data work.** The
   `EVENTS → UPDATES & EVENTS` rename, the Bungie-update link line, the Trials-line
   relocation, and the `VANGUARD ALERTS` header/explainer are edits to `build_body` and a
   couple of new manual fields. They ride along in the same PR but carry near-zero risk.

## 1. Can Conquests come from the API? — two live probes say no

Endpoint abbreviations (as in `weekly_reset_automation.md`): **P204** = GetProfile
component 204 CharacterActivities (OAuth); **PM** = GetPublicMilestones (public);
**MAN** = manifest definitions.

### PM is out (public probe)

`GET /Destiny2/Milestones/` returned **12 milestones, all raids/clan**
(`The Desert Perpetual`, `Salvation's Edge`, `Vow of the Disciple`, `Last Wish`,
`Garden of Salvation`, `Deep Stone Crypt`, `Vault of Glass`, `King's Fall`,
`Root of Nightmares`, `Crota's End`, `Weekly Clan Engrams`, `Purification`), every activity
`difficultyTier: None`. **No Nightfall/Conquests milestone in the public feed.** (Bonus: PM
*does* authoritatively confirm the seasonal raid — `The Desert Perpetual`, challenges=1,
window `2026-07-07..2026-07-14` — a possible future cross-check for the FEATURED RAIDS
rotator, out of scope here.)

### P204 gives the pool but NOT the tiers (authenticated probe — the decisive one)

The live 204 fetch returned **254 distinct available activities / 631 raw per-character
entries**. Findings that kill auto-derivation of Conquests:

- **There is no "Conquest" activity type.** The type distribution is `Story, Mission,
  Strike, Raid, Dungeon, Seasonal Arena, Exotic Mission, Nightmare Hunt, Nightfall, Solo
  Ops, …`. The sample's Conquest activities resolve as ordinary types: `Sunless Cell`,
  `Conductor's Keep`, `Arms Dealer`, `Lightblade` come through as **`Mission`/`Strike`
  "…: Customize"** entries, not a dedicated Conquest bucket.
- **`difficultyTier` is a useless constant.** On the raw entries it is `2` for *everything*
  — Nightfall Advanced, Expert, Master, and every "Customize" op alike. (In the manifest
  *definition* it's `None`.) It cannot discriminate tiers.
- **The new Conquest ops carry no tier label.** Their `selectionScreenDisplayProperties.name`
  is literally `"Customize"` (the Portal "pick your difficulty" screen); no `": Expert"`
  name suffix, no tier modifier. Contrast the **classic** Nightfall, whose
  `selectionScreen` *does* read `Advanced`/`Expert`/`Master` and whose modifiers include
  named `"Expert Modifiers"`/`"Master Modifiers"` — so classic-NF tiers *are* derivable, but
  that is the old playlist and does not match the sample's Conquests.
- **No Grandmaster/GM entries exist right now at all** (GMs open later in a season), and the
  sample's `Ultimate` tier has no representation in the data. `recommendedLight` varies
  (Customize 300–350, Master NF 430) but does not map to clean tier names.

**Net:** component 204 exposes the *pool* of Conquest-eligible activities and their
locations, but the **weekly Expert/Master/GM/Ultimate assignment is not in the API**. That
assignment is what the Conquests section is; therefore it is manual.

### Change table

| # | Change | Best source | Verdict | Conf | Effort |
|---|--------|-------------|---------|------|--------|
| 1 | **CONQUESTS** — the Expert/Master/GM/Ultimate → activity mapping | **MANUAL** (Portal-read; API can't supply tiers) | **manual** | high | M |
| 2 | CONQUESTS — activity-name **autocomplete** to speed manual entry | MAN (`DestinyActivityDefinition` pool, existing index) | assist | high | S |
| 3 | CONQUESTS — per-activity location (if the format wants it) | MAN `DestinyDestinationDefinition` | optional | med | S |
| 4 | `EVENTS` → `UPDATES & EVENTS` header + Bungie update link | MANUAL (new `update_link` field) | manual | high | S |
| 5 | Trials line relocated into UPDATES & EVENTS | computed (existing `trials_active`) | auto | high | S |
| 6 | `VANGUARD ALERTS (Seasonal Tab)` → `VANGUARD ALERTS` + explainer subline | MANUAL (static string) | manual | high | XS |
| 7 | `:Conquests:` server emoji resolves | Kyber emoji dict | deploy | — | XS |

**On locations.** The probe resolves clean location names (`The Moon`, `European Dead Zone`,
`Cosmodrome`, `Savathûn's Throne World`, …) from `destinationHash`, so #3 is *possible*. But
note the sample line `Expert: Sunless Cell, Moon` disagrees with live data (Sunless Cell
resolves to **Dreadnaught**, not Moon), and the `GM` line is plainly a **comma-separated
list of activity names** (`Defiant: EDZ, Arms Dealer, Disgraced, Heist Moon, Scarlet Keep`).
So the section format is best read as *"Tier: activity, activity, …"* — a hand-curated name
list, not a machine "activity, location" join. Treat location resolution as optional polish,
not a requirement.

**Why the GM row here differs from VANGUARD ALERTS' GM line.** The existing GM-Nightfall
derivation (`weekly_reset.py:531-539`) works because that op carries a **`_guaranteed`
reward** marker the code filters on — a signal the untiered Conquest "Customize" entries do
**not** have (probe: `guaranteed=False` on Sunless Cell / Arms Dealer / Lightblade). So the
proven GM path does not generalise to the Conquests tiers.

## 2. Recommended architecture (reuse named symbols)

### 2a. Context field + data shape

Add one ordered, tier-keyed field to `WeeklyResetContext` (`weekly_reset.py:263-366`) with
its `to_dict`/`from_dict` round-trip + the `activity_record` entry (`:725-749`):

```python
# weekly_reset.py — WeeklyResetContext
# Ordered to match the post: Expert, Master, GM, Ultimate. Values are hand-curated activity
# labels; empty tiers are skipped in build_body. (No auto-derivation — see plan §1.)
CONQUEST_TIERS = ("Expert", "Master", "GM", "Ultimate")
conquests: dict[str, list[str]] = field(default_factory=dict)
```

Plain strings, not `WeaponRef` — Conquest rows are un-linked activity labels in the sample.

### 2b. Manual entry (primary), API only as autocomplete

- **`set_conquests` command** — the primary path. Add to the `set_*` family
  (`weekly_reset.py:1910-2083`): a tier Choice (Expert/Master/GM/Ultimate) + a free-text
  value that replaces that tier's list. Model it on `set_crucible` (also a manual multi-line
  list, `:1910-…`), not on the API-derived `set_gm_strike`.
- **Autocomplete assist (optional, cheap):** the manifest index built in `_build_indexes`
  (`weekly_reset.py:1700-1762`) already loads `DestinyActivityDefinition`. Extend it to also
  surface Nightfall/strike/battleground/mission activity **names** (reuse `_classify_activity`
  `:1623-1662` and `_clean_activity_name` `:1677-1697`, stripping the `": Customize"` suffix)
  so `set_conquests` can autocomplete real activity names and avoid typos. This is a
  *suggestion* list only — it does **not** know the tier.
- **No `derive_conquests`, no extra fetch.** The first draft proposed auto-bucketing off
  P204; the live probe proves that's not sound (§1). Do not add it.

### 2c. Rendering

- **`build_body` block** (`weekly_reset.py:595-695`): insert a CONQUESTS block **after** the
  VANGUARD ALERTS block (`:614-627`) to match the sample order. One line per non-empty tier:
  `:Conquests: ┊ {tier}: {", ".join(activities)}`. Header `**CONQUESTS (Seasonal Tab)**`.
- **Emoji:** `:Conquests:` (id `1524120378228740267` per the sample) must exist on Kyber's
  guild or `substitute_user_side_emoji` (`dd/anchor/embeds.py:53-65`) leaves it literal — a
  **deploy prerequisite**, not code.
- **Budget:** the GM tier can list ~5 activities; re-check the 3900-char CV2 budget in
  `validate_post` (`:771-774`) for a fat week (it truncates gracefully, but verify).

### 2d. The non-Conquests deltas

- Rename `**EVENTS**` → `**UPDATES & EVENTS**`; add an optional `update_link: dict | None`
  rendering `:Bungie: ┊ [Update X.Y.Z]({url})` (manual, set via the editor modal alongside
  `events_narrative`, `:1160`).
- Move the Trials reminder line into UPDATES & EVENTS (currently a separate
  `**From Friday - Tuesday**` block, `:675-680`); reuse the computed `trials_active` flag
  (`:571`), just change the emit location.
- `VANGUARD ALERTS (Seasonal Tab)` → `VANGUARD ALERTS`; add the static explainer subline
  `Reward listed is for completing the weekly challenge.` (`:614`).

## 3. Phased rollout by ROI

- **Phase 1 — string deltas (no data risk):** header renames, explainer subline, Bungie
  update-link field, Trials relocation. Extend
  `test_build_body_has_all_sections_and_deeplink` (`test_weekly_reset.py:137-157`) with the
  new markers. Ship-able alone.
- **Phase 2 — Conquests manual slot (the feature):** context field + round-trip +
  `activity_record` + `build_body` block + `set_conquests`. Fully functional by hand; this is
  the whole Conquests deliverable, since §1 rules out auto.
- **Phase 3 — autocomplete polish (optional):** extend `_build_indexes` to offer activity-name
  suggestions in `set_conquests`; optional location resolution if the maintainer wants the
  `activity, location` form.
- **Not doing:** auto-deriving the tier grouping from P204 (probe-disproven), and PM-based
  sourcing (raids only).

## 4. Risks

- **Auto-derivation is a trap — don't reintroduce it.** ⚠️ It's tempting to bucket
  `availableActivities` by difficulty; the live probe shows `difficultyTier` is a constant
  and the Conquest ops are untiered `"Customize"` entries. Any auto-bucketing would silently
  post the wrong tiers. Manual entry is the correct design, not a fallback.
- **Emoji not on the guild** renders `:Conquests:` literally — a visible break. Gate deploy
  on the emoji existing; add a `show`-preview note.
- **CV2 budget** — a 5-activity GM line plus the new section can push a heavy week toward the
  3900-char cap (`validate_post`, `:771-774`). Verify against a sample week.
- **Manual burden.** Conquests adds four tiers the team must fill every week. Mitigate with
  the Phase-3 autocomplete and by pre-filling next week's draft from this week's values
  (the config already carries `last_*` continuity fields, `weekly_reset.py:369-437`).
- **API classic-NF vs new-Conquest confusion.** `selectionScreen` tiers exist for the *old*
  Nightfall but not the new Conquests; a future contributor may wire the wrong one. Document
  the distinction in the `set_conquests` docstring.

**Net:** Conquests is a **manual** section — one context field, one `build_body` block, one
`set_*` selector — because two live probes prove the Bungie API cannot supply the weekly
tier→activity assignment (untiered `"Customize"` entries, constant `difficultyTier`, no
public milestone). The API's only contribution is optional activity-name autocomplete. The
other format deltas are low-risk string work.

**Key files to add/modify:**
- `dd/anchor/extensions/weekly_reset.py` — `WeeklyResetContext.conquests` field + round-trip
  + `activity_record`; `build_body` CONQUESTS block + header/explainer/update-link/Trials
  deltas; `set_conquests` command; optional `_build_indexes` name-pool extension;
  `validate_post` budget re-check.
- `dd/anchor/tests/test_weekly_reset.py` — extend the all-sections marker test; add a
  `set_conquests` round-trip test.
- **Deploy:** upload the `:Conquests:` emoji to the Kyber guild.
- *(No changes needed to `portal_ops.py` or `bungie_api/` — no new derivation.)*

---

### Appendix — the new post format (maintainer sample, 2026-07-07)

New/changed vs. the shipped post: **UPDATES & EVENTS** (was EVENTS; adds a Bungie update
link + Trials-returns line), **VANGUARD ALERTS** (drops "(Seasonal Tab)"; adds the "Reward
listed is for completing the weekly challenge." explainer), and the **new CONQUESTS
(Seasonal Tab)** section:

```
**CONQUESTS (Seasonal Tab)**

<:Conquests:1524120378228740267> ┊ Expert: Sunless Cell, Moon
<:Conquests:1524120378228740267> ┊ Master: Conductor's Keep, Derealize
<:Conquests:1524120378228740267> ┊ GM: Defiant: EDZ, Arms Dealer, Disgraced, Heist Moon, Scarlet Keep
<:Conquests:1524120378228740267> ┊ Ultimate: Lightblade, Operation Seraph's Shield
```

### Appendix — live probe evidence (2026-07-07, dev env via `railway run --service anchor`)

**Public `GET /Destiny2/Milestones/`** → 12 milestones, all raid/clan, `difficultyTier: None`
(names listed in §1). No Nightfall/Conquests milestone → PM rejected.

**Authenticated GetProfile component 204** (dev OAuth token; token rotated on refresh, which
is expected for dev): 254 distinct activities / 631 raw entries.
- Activity types present: `Story(76), Mission(37), Strike(20), Raid(19), Dungeon(17),
  Seasonal Arena(11), Exotic Mission(11), Nightmare Hunt(9), Nightfall(8), Solo Ops(8), …` —
  **no "Conquest" type**.
- Sample Conquest activities resolved as: `Sunless Cell` → `The Sunless Cell: Customize`
  (Mission, **Dreadnaught** — not "Moon"); `Conductor's Keep` → Mission (Earth); `Arms
  Dealer` → Strike/Mission (EDZ); `Lightblade` → Strike (Savathûn's Throne World);
  `Operation Seraph's Shield` → **not in availableActivities**.
- Raw-entry fields on the Conquest "Customize" ops: `difficultyTier=2` (constant),
  `selectionScreen='Customize'`, `guaranteed=False`, no tier suffix/modifier.
- Classic Nightfall entries *do* carry `selectionScreen` = `Advanced`/`Expert`/`Master` with
  named `"Expert Modifiers"`/`"Master Modifiers"` — a tier signal for the **old** playlist
  only, not the new Conquests.

→ The weekly Expert/Master/GM/Ultimate assignment is **not** exposed by the API. Conquests is
manual; the API assists only via activity-name autocomplete.
