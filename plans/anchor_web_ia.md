# Anchor web UI — organising around the post, not the tool

**Status: deferred.** The finding in §1 is durable and worth keeping whatever happens to
the layout. The design in §2 was explored, mocked up, and rejected on scaling grounds
(§3); §4 is the direction that objection points at, and is where to pick this up.

Mockups: `plans/anchor_web_ia/feed_hub.html` and `feed_page.html`. Open them straight
from disk — they link the real `dd/anchor/web_static/shared.css`, so they render in the
actual design tokens. They are static HTML with invented numbers; nothing wires up.

---

## 1. The finding: `feed` is already the shared key

This is the part worth remembering. Four surfaces each hold a slice of one feed's state,
and they **already agree on how to name it** — the followable name:

| Surface | Where the key lives |
| --- | --- |
| Autopost toggles | `_Setting.slug` in `dd/anchor/extensions/autopost_settings.py` |
| Mirror log | `run["src_name"]`, resolved via `followable_name(id=run["src_ch_id"])` |
| Statistics | `for feed, src_id in cfg.followables.items()` in `stats_page.py` |
| Compose forms | one page each for the two hand-composed posts (weekly reset, Trials) |

So a per-feed view needs **no schema change and no new query shape** — the joins exist.
Whatever layout wins, that is the axis to build it on.

## 2. What was explored: a feed-centric hub

The six control-panel cards are *tools*. Answering one ordinary operational question —
"did the Xûr post go out, and did everyone get it?" — costs four page visits: the toggle
in autopost settings, the run in mirror logs, the reach in stats, and the data behind it
in the rotation editor's Locations tab. Nothing joins them in the UI.

The mock inverts that:

- **Hub** — one row per feed: autopost on/off, last run's status chip, a reach sparkline.
- **Feed page** — four panels (autopost, source data, recent deliveries, reach), each a
  summary of an existing tool with a link out to it. No new data on the page.

## 3. Why it was rejected: it scales with the wrong number

A flat list grows with the number of feeds, not with how much needs attention.
`FOLLOWABLES` already carries **12** (`ada, twab, prime, nwid, lost_sector, daily_reset,
eververse, weekly_reset, trials, xur, portal_ops, iron_banner`), and beacon has further
command surfaces that could become posts — `distortion`, `emblems_and_cosmetics`,
`free_games`, `nightfall`, `legacy_activities`. Twenty to thirty rows, each with a chip
and a sparkline, is noise: the healthy majority crowds out the one row that matters.

Owner's call, and the right one — recorded so it is not re-proposed as-is.

## 4. Where to pick this up

The objection points at a specific fix: **make the page length track what is wrong, not
how many feeds exist.**

- **Exceptions first.** Show only feeds that are off, failed, partial, or whose reach has
  dropped. Everything healthy collapses to one line ("18 feeds healthy"), expandable.
  A quiet week renders almost nothing, which is the correct amount of UI for a quiet week.
- **Or organise by time, not by feed.** "This week: what posted, what landed, what is
  pending." Page length then tracks recent activity, and the two axes currently tangled
  together get separated — *per-feed* state (the toggle, the reach trend) versus
  *per-instance* state (this Friday's Xûr post and its mirror run). The current UI mixes
  both without naming either.

Either way the feed page from §2 probably survives as the detail view; it is the **hub**
that needs a different shape.

## 5. Open questions, if resumed

- A feed page for something off with no rotation data (Iron Banner between events) is
  nearly empty. Check the thin cases before committing to the layout.
- The rotation editor's tabs do not map onto feeds one-to-one — Planet cycles serves Lost
  Sector, Activities is legacy — so "Source data" is the panel most likely to need
  per-feed special-casing.
- Which feeds would legacy commands actually become? That set decides whether the hub
  needs grouping (by cadence?) on top of exception-filtering.
