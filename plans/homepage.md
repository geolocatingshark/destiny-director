# Plan: Homepage for the Anchor web UI

> **Stub** — to be fleshed out before execution.

## Context

`dd.anchor` serves several web pages from its aiohttp app (`dd/anchor/web.py`) — today the
rotation editor and weekly-reset form, with more likely to follow. There is no single
landing page that ties them together; each page is reached only via its own owner-minted
launcher link.

**Goal:** add a **homepage** that lists/links all bot web pages in one place. A **card-based
layout** is preferred — one card per page/tool.

## Decisions

- **Auth:** reuse the **Discord OAuth** gate from the OAuth branch (see
  `plans/anchor_web_discord_oauth.md`), which will be **merged to `dev`** by the time this
  plan is executed. Do **not** build a separate auth path — depend on the shared middleware.
- **Audience:** the homepage is **owner-only for anchor** — visible only to bot owners /
  team members (the set from `CachedFetchBot.fetch_owner_ids()`), same authorization as the
  rest of the anchor web UI.

## To flesh out

- Route + handler placement in `dd/anchor/web.py` (root `/`?).
- Card design: markup/styling, per-page card content, how new pages register a card.
- How cards are sourced (static list vs. a small registry the feature modules contribute to).
- Interaction with the OAuth middleware once it lands on `dev`.
