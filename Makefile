deploy-dev:
	railway environment dev
	railway service conduction-tines
	railway up -d

deploy-prod:
	railway environment production
	railway service conduction-tines
	railway up -d

run-local: .env
	poetry run honcho start

destroy-schemas: .env
	$(POETRY_CMD) honcho run python -m conduction.schemas --destroy-all

create-schemas: .env
	$(POETRY_CMD) honcho run python -m conduction.schemas --create-all

atlas-migration-plan: .env
	$(POETRY_CMD) honcho run atlas migrate diff --env sqlalchemy

atlas-migration-dry-run:
	@echo "$(POETRY_CMD) honcho run atlas migrate apply -u <MYSQL_URL> --dry-run"
	@$(POETRY_CMD) honcho run atlas migrate apply -u ${MYSQL_URL} --dry-run

atlas-migration-apply:
	@echo "$(POETRY_CMD) honcho run atlas migrate apply -u <MYSQL_URL>"
	@$(POETRY_CMD) honcho run atlas migrate apply -u ${MYSQL_URL}

test: .env
	poetry run honcho run python -m pytest

.env:
	@echo "Please create a .env file with all variables as per conduction.cfg"
	@echo "and .env-example to be able to run this locally. Note that all"
	@echo "variables are required and the example values are not valid but"
	@echo "are there to show the approximate format of values."
	@exit 1
