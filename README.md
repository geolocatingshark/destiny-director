# Destiny Director

Setting up the dev environment:

1. Install [uv](https://docs.astral.sh/uv/) (the only supported package manager).
2. Run `uv sync` in the root of the git clone to create the virtualenv and install
   dependencies (Python 3.12, pinned in `.python-version`).
3. [Optional] Set up `.env` with the environment variables referenced in
   `dd/common/cfg.py` & `.env-example`.
4. [Optional] Run `uv run pre-commit install` to enable the lint/format/type-check
   hooks (`.pre-commit-config.yaml`) on each commit.

Quality gates (lint, type-check, tests) run via the `Makefile`:

```
make lint        # ruff check
make format      # ruff format + ruff check --fix
make typecheck   # ty check
make check       # lint + typecheck + test (the full gate)
```

Running a bot locally:

```
make run-beacon-local   # main bot
make run-anchor-local   # secondary bot
```

(Both run `uv run python -OOm dd.<bot>` and require a populated `.env`.)

Running tests locally:

```
make test
```

Running code locally with docker:

```
docker build -t anchor .
docker run --env-file=.env anchor
```

Deploying code to [railway](https://railway.app/):

Make sure you have the [railway cli](https://docs.railway.app/develop/cli) installed and
are logged in. Use these to deploy to the dev instance on railway:

```
make deploy-beacon-dev
make deploy-anchor-dev
```

**CAUTION** use these to deploy to the production instance on railway:

```
make deploy-beacon-prod
make deploy-anchor-prod
```
