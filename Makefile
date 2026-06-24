deploy-beacon-dev:
	railway environment dev
	railway service beacon
	railway up -d

deploy-anchor-dev:
	railway environment dev
	railway service anchor
	railway up -d

deploy-beacon-prod:
	railway environment production
	railway service beacon
	railway up -d

deploy-anchor-prod:
	railway environment production
	railway service anchor
	railway up -d

remove-last-deploy:
	railway down

run-beacon-local: .env
	uv run python -OOm dd.beacon

run-anchor-local: .env
	uv run python -OOm dd.anchor

destroy-schemas: .env
	uv run python -m dd.common.schemas --destroy-all

create-schemas: .env
	uv run python -m dd.common.schemas --create-all

atlas-migration-plan: .env
	atlas migrate diff --env sqlalchemy

atlas-migration-dry-run:
	@echo "atlas migrate apply -u <MYSQL_URL> --dry-run"
	atlas migrate apply -u ${MYSQL_URL} --dry-run

atlas-migration-apply:
	@echo "atlas migrate apply -u <MYSQL_URL>"
	atlas migrate apply -u ${MYSQL_URL}

lint:
	uv run ruff check dd

format:
	uv run ruff format dd
	uv run ruff check --fix dd

typecheck:
	uv run ty check dd

test: .env
	uv run --env-file .env python -m pytest -m "not discord"

test-unit: .env
	uv run --env-file .env python -m pytest -m "not integration"

coverage: .env
	uv run --env-file .env python -m pytest -m "not discord" --cov=dd --cov-report=term-missing

# All live Discord integration tests (marker `discord`). Opt-in: these hit Discord
# and need a real bot token, so they're excluded from `test`/`coverage`/`check`.
# The bot token comes from .env (DISCORD_TOKEN_BEACON) via --env-file.
test-integration: .env
	uv run --env-file .env python -m pytest -m discord -v

# Just the mirror integration tests (a subset of `test-integration`). Each run
# reuses the dedicated test guild and isolates by sweeping its test channels.
test-mirror-integration: .env
	uv run --env-file .env python -m pytest \
		dd/beacon/tests/test_mirror_integration.py -v

# Every test, including the live Discord integration tests (no marker filter).
# Needs a real bot token in .env (DISCORD_TOKEN_BEACON), same as
# `test-integration`. Use this for a full run before a release.
test-all: .env
	uv run --env-file .env python -m pytest -v

check: lint typecheck test

.env:
	@echo "Please create a .env file with all variables as per beacon.cfg"
	@echo "and .env-example to be able to run this locally. Note that all"
	@echo "variables are required and the example values are not valid but"
	@echo "are there to show the approximate format of values."
	@exit 1

install-termux-deps:
	@echo "If the specific python version for this project is not available"
	@echo "and cannot be upgraded, then consider using the TUR to find it:"
	@echo "https://github.com/termux-user-repository/tur"
	pkg install python uv
