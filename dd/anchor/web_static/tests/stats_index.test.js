// Copyright © 2019-present gsfernandes81
//
// This file is part of "dd" henceforth referred to as "destiny-director".
// Licensed under the GNU AGPL v3 or later; see the project LICENSE.

// Unit tests for stats.js::autopostIndex — what the "All feeds" line counts.
// Run with `make test-js` (node --test); no browser, no bundler.

process.env.TZ = "UTC";

const test = require("node:test");
const assert = require("node:assert/strict");

const { autopostIndex } = require("../stats.js");

const reachAt = (series, iso) => {
  const hit = series.find((p) => p[0].toISOString().startsWith(iso));
  return hit ? hit[1] : undefined;
};

test("the all-feeds total sums only live feeds", () => {
  const rows = [
    ["2026-01-01", "xur", "follow", 10],
    ["2026-01-01", "weekly_nightfall", "follow", 5],
  ];

  const ix = autopostIndex(rows, new Set(["xur"]));

  // 10, not 15: the retired feed's followers are not channels our posts reach.
  assert.equal(reachAt(ix.total.reach, "2026-01-01"), 10);
});

test("a retired feed still gets its own series", () => {
  // Excluded from the total, but its own row charts its own history — that is the
  // whole reason the catalog keeps a retired entry.
  const rows = [
    ["2026-01-01", "xur", "follow", 10],
    ["2026-01-01", "weekly_nightfall", "follow", 5],
    ["2026-01-02", "weekly_nightfall", "mirror", 2],
  ];

  const ix = autopostIndex(rows, new Set(["xur"]));

  assert.equal(reachAt(ix.byFeed.get("weekly_nightfall").reach, "2026-01-01"), 5);
  assert.equal(reachAt(ix.byFeed.get("weekly_nightfall").reach, "2026-01-02"), 2);
});

test("retiring a feed leaves no step-down in the all-feeds series", () => {
  // The failure this exists to stop: summing every row ever snapshotted put a permanent
  // cliff in the total on the retirement date, which the trend arrow read as a
  // months-long decline while the live feed was in fact growing.
  const rows = [
    ["2026-01-01", "xur", "follow", 10],
    ["2026-01-01", "weekly_nightfall", "follow", 5],
    // Retired here — no more weekly_nightfall rows are written from this date on.
    ["2026-01-02", "xur", "follow", 11],
  ];

  const ix = autopostIndex(rows, new Set(["xur"]));

  assert.equal(reachAt(ix.total.reach, "2026-01-01"), 10);
  assert.equal(reachAt(ix.total.reach, "2026-01-02"), 11);
});

test("a historical slug the catalog never had is not counted either", () => {
  // `prime`/`nwid` predate the catalog. They are not live, so they are not reach.
  const rows = [
    ["2026-01-01", "xur", "follow", 10],
    ["2026-01-01", "prime", "follow", 99],
  ];

  const ix = autopostIndex(rows, new Set(["xur"]));

  assert.equal(reachAt(ix.total.reach, "2026-01-01"), 10);
  assert.equal(reachAt(ix.byFeed.get("prime").reach, "2026-01-01"), 99);
});

test("an absent live set falls back to counting everything", () => {
  // `load()` tolerates a payload with no `current` (`data.current || []`). Before the
  // total was scoped to live feeds, that path still drew the history; keeping it drawn
  // means a degraded scope rather than a blank chart over data we do hold.
  const rows = [
    ["2026-01-01", "xur", "follow", 10],
    ["2026-01-01", "weekly_nightfall", "follow", 5],
  ];

  assert.equal(reachAt(autopostIndex(rows, new Set()).total.reach, "2026-01-01"), 15);
  assert.equal(reachAt(autopostIndex(rows, undefined).total.reach, "2026-01-01"), 15);
});
