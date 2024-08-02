data "external_schema" "sqlalchemy" {
  program = [
    "python",
    "destiny-director/common/schemas.py",
    "--print-ddl"
  ]
}

env "sqlalchemy" {
  src = data.external_schema.sqlalchemy.url
  dev = "docker://mysql/8/dev"
  migration {
    dir = "file://migrations"
  }
  format {
    migrate {
      diff = "{{ sql . \"  \" }}"
    }
  }
}