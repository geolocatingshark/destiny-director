# Dockerfile / deploy-speed optimization

## Context

Investigated whether Railway deploys could be faster by changing how the Docker image
installs dependencies. Conclusion: **the current `requirements.txt` export is fine — it is
not the bottleneck.** Two other levers matter, but each carries a portability risk that needs
a real build to validate before committing. Deferring for a focused later session.

This is exploratory/optional. Nothing here is urgent; deploys work today.

## Current build (`Dockerfile`)

8-stage multi-stage build on `python:3.12-alpine`:
1. `base` — alpine + `apk add git gcc tzdata musl-dev`
2. `atlas-base` — pulls the Atlas migration binary
3. `uv-helper` — base + `uv` binary, `UV_COMPILE_BYTECODE=1`
4. `exporter` — `uv export --no-dev --no-emit-project` → `requirements.txt` (from only
   `pyproject.toml` + `uv.lock`)
5. `dependencies` — `pip install -r requirements.txt`  ← the heavy layer
6. `builder` — `uv build` → app wheel
7. `final` — `FROM dependencies` + `pip install *.whl` + copy Atlas binary
8. `target` — copy `migrations/`, `docker-entrypoint.sh`; `CMD sh docker-entrypoint.sh`

The dep layer (5) is keyed on only `pyproject.toml`/`uv.lock`, so **code-only deploys cache-hit
it and skip install entirely** — that part is already correct.

## Findings (why this is mostly fine as-is)

- **Railway layer-caches the Dockerfile build.** For the common code-only deploy, the
  `pip install` layer is reused, so pip-vs-uv install speed is irrelevant there. The cost is
  stages 6–8 (`uv build` + `pip install *.whl` + small copies).
- The `requirements.txt` export vs a cached `uv sync` dep layer give **the same caching**;
  swapping one for the other is not itself a speed win.

## Levers (ranked by real impact), if revisiting

1. **Base image `alpine` → `slim` (`python:3.12-slim`)** — biggest win, only on
   *dependency-change* deploys. Alpine/musl forces source compilation for several deps
   (notably `asyncmy` (Cython); also `cffi`/`regex`/`aiohttp` when no musllinux wheel exists),
   which is why the Dockerfile installs `gcc`/`musl-dev`. Debian/glibc `slim` pulls prebuilt
   **manylinux** wheels for almost everything, dropping the compile step *and* the
   `gcc`/`musl-dev` apk install.
   - **Risk to validate:** image is larger than alpine; must re-verify `asyncmy` +
     `cryptography` (speedups group) build/run on glibc.

2. **`pip` → `uv sync` / `uv pip install`** — moderate win on dependency-change deploys
   (parallel downloads, faster installs), and it **removes the `pip install` usage that
   contradicts the CLAUDE.md "use uv only, never pip" rule.** Modern uv Docker pattern:
   `uv sync --frozen --no-install-project` for the cached dep layer, then copy code and
   `uv sync --frozen`. Same caching as today, faster installer.

## Suggested approach when picked up

- Switch base to `python:3.12-slim`; drop `gcc`/`musl-dev` from the apt step (keep `git`,
  `tzdata`); replace `apk` with `apt-get` accordingly.
- Replace the export→pip flow with the uv-sync two-step (cached deps, then project), per the
  official uv Docker guide. Keep `UV_COMPILE_BYTECODE=1`.
- **Benchmark a real build both ways** (cold and warm cache) before committing — the whole
  point is measured improvement, not theoretical.
- Keep the Atlas binary copy and `docker-entrypoint.sh` flow unchanged.

## Out of scope / decided

- **`setuptools` runtime dep — KEEP it, do not remove.** Verified unused at *runtime* on all
  platforms, but on Termux/Android (hard constraint — see memory) deps are compiled from sdist,
  and a present `setuptools` can be required when building with `--no-build-isolation`. The
  cleanup saves nothing meaningful and risks the Android build path. Not worth it.
