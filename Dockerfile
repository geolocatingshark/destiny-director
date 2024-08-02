FROM python:3.11-alpine AS base

RUN apk update
RUN apk add --no-cache git

WORKDIR /app

FROM base AS builder-base

ENV PIP_DEFAULT_TIMEOUT=100 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=1.8.3

RUN pip install "poetry==$POETRY_VERSION"
RUN python -m venv /venv

COPY pyproject.toml poetry.lock ./
RUN . /venv/bin/activate && poetry install --without dev --no-root

FROM builder-base AS builder

COPY . .
RUN . /venv/bin/activate && poetry build

FROM arigaio/atlas:latest-community-alpine AS atlas-base

FROM base AS final

COPY --from=atlas-base /atlas /bin/atlas
COPY --from=builder /venv /venv
COPY --from=builder /app/dist .
COPY docker-entrypoint.sh ./
COPY Procfile ./
COPY migrations ./migrations

RUN . /venv/bin/activate && pip install *.whl
CMD ["sh", "docker-entrypoint.sh"]