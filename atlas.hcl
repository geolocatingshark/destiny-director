# Desired schema is the SQLAlchemy models rendered to DDL. We DON'T use Atlas's
# `data "external_schema"` provider here: it's a non-community-only feature, so it
# can't run under the community Atlas binary baked into the dev container. Instead
# `make atlas-migration-plan` writes the DDL to `.atlas/desired.sql` (gitignored)
# first and this env reads that file — which works on BOTH the community and the
# standard Atlas binary.
env "sqlalchemy" {
  src = "file://.atlas/desired.sql"
  # Atlas needs a throwaway "dev" database to normalize the schema and compute the
  # diff. On the local box (with a Docker daemon) this defaults to an ephemeral
  # `docker://` MySQL. The dev container has no usable Docker, so it sets
  # ATLAS_DEV_URL to a dedicated scratch schema on the sibling MySQL service
  # (mysql:3306/atlas_dev) — see docker-compose.dev.yml + docker-entrypoint.dev.sh.
  dev = getenv("ATLAS_DEV_URL") != "" ? getenv("ATLAS_DEV_URL") : "docker://mysql/8/dev"
  migration {
    dir = "file://migrations"
  }
  format {
    migrate {
      diff = "{{ sql . \"  \" }}"
    }
  }
}
