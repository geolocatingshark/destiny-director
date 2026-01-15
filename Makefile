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
	. .venv/bin/activate
	python -OOm dd.beacon

run-anchor-local: .env
	. .venv/bin/activate
	python -OOm dd.anchor

destroy-schemas: .env
	. .venv/bin/activate
	python -m dd.common.schemas --destroy-all

create-schemas: .env
	. .venv/bin/activate
	python -m dd.common.schemas --create-all

atlas-migration-plan: .env
	atlas migrate diff --env sqlalchemy

atlas-migration-dry-run:
	@echo "atlas migrate apply -u <MYSQL_URL> --dry-run"
	atlas migrate apply -u ${MYSQL_URL} --dry-run

atlas-migration-apply:
	@echo "atlas migrate apply -u <MYSQL_URL>"
	atlas migrate apply -u ${MYSQL_URL}

test: .env
	poetry run honcho run python -m pytest

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
