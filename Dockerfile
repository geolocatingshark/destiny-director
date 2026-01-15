# Stage 1: Base (System dependencies)
FROM python:3.12-alpine AS base
RUN apk update && apk add --no-cache git gcc tzdata musl-dev
ENV TZ=Etc/UTC

WORKDIR /app

# Stage 2: Atlas DB Migrations Image
FROM arigaio/atlas:latest-community-alpine AS atlas-base

# Stage 3: UV Helper (Provides the uv binary for build stages)
# Copy the uv binary directly from the official image
# Compile bytecode for faster startup in build stages
FROM base AS uv-helper
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1

# Stage 4: Exporter (Generates requirements.txt)
# --no-emit-project makes the requirements.txt only contain third-party dependencies,
# not the local app itself.
FROM uv-helper AS exporter
COPY pyproject.toml uv.lock ./
RUN uv export --no-dev --no-hashes --no-emit-project --format=requirements-txt --output-file=requirements.txt

# Stage 5: Dependencies
FROM base AS dependencies
COPY --from=exporter /app/requirements.txt .
ENV PIP_DEFAULT_TIMEOUT=100 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
RUN pip install --no-cache-dir -r requirements.txt

# Stage 6: Builder (Builds your application wheel)
FROM uv-helper AS builder
COPY . .
RUN uv build

# Stage 7: Final Base (Combines cached deps + app code)
# Install the application wheel; dependencies are already satisfied by the base layer
FROM dependencies AS final
COPY --from=builder /app/dist/*.whl .
COPY --from=atlas-base /atlas /bin/atlas
RUN pip install --no-cache-dir *.whl

# Stage 8: Target
ARG RAILWAY_SERVICE_NAME
COPY ./migrations ./migrations
COPY ./docker-entrypoint.sh .
CMD ["sh", "docker-entrypoint.sh"]
