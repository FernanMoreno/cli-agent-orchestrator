# PostgreSQL persistence

CAO stores application state in PostgreSQL 18. The `main`, `auth`, and `peer`
targets contain orchestration state, browser authentication, and the local peer
registry respectively. Files such as prompts, logs, and executable snapshots
remain filesystem artifacts.

## Managed installation

The [Docker installer](docker-installation.md) provisions PostgreSQL and private
credentials. See [personal deployment](personal-deployment.md) for initialization,
backup, and restore.

## External PostgreSQL

Provision a separate schema and two roles per target: an owner used for explicit
bootstrap, and an application role used by the server. The owner must be able to
create the target schema and grant application privileges. The application role
must not own the schema or have DDL privileges. Use distinct targets for separate
CAO installations; the peer registry is shared only by the same host/user.

Configuration is read from `$CAO_HOME_DIR/database-config.json`, or the file
selected by `CAO_DATABASE_CONFIG_FILE`. The configuration and password files
must be regular files owned by the runtime user with mode `0600`; symlinks are
rejected. Secret paths are resolved relative to the configuration file.

The following illustrates one target. Supply all three keys (`main`, `auth`,
`peer`), using their actual provisioned roles, databases, schemas, and stable
logical UUIDs:

```json
{
  "version": 1,
  "targets": {
    "main": {
      "host": "postgres.example.internal",
      "port": 5432,
      "database": "cao_main",
      "schema": "cao_main",
      "user": "cao_main_app",
      "store_id": "d3e39268-fdba-44e9-8b89-393423b59966",
      "secret_file": "secrets/main-runtime.json",
      "sslmode": "verify-full",
      "migration_user": "cao_main_owner",
      "migration_secret_file": "secrets/main-owner.json"
    }
  }
}
```

Each secret file contains `{"password": "the-provisioned-password"}`. TLS defaults
to `verify-full`; an isolated local deployment can explicitly select `disable`.
Do not put database passwords in provider prompts or environment variables.

For an initialized personal deployment profile with its three configured targets:

```bash
python scripts/personal_deployment.py --root /path/to/private/profile bootstrap-database
python scripts/personal_deployment.py --root /path/to/private/profile verify-database
```

Bootstrap uses the owner credentials and an advisory lock. Repeating it verifies
the existing component versions and catalog fingerprints. Ordinary startup uses
the application role and rejects schema drift. Keep owner credentials restricted
to maintenance; a runtime configuration can omit the two `migration_*` fields
after bootstrap.

## Migration and recovery

CAO has no importer for legacy file-based stores. The retired
`--import-sqlite`/`import-sqlite` options are rejected before database setup or
filesystem changes. Preserve any old installation separately if it is needed
for archival or rollback; it cannot be migrated by the CAO installer.

Native backups use `pg_dump`/`pg_restore` and a verified manifest. Restore targets
must be distinct from the source. Restored running work is fenced and requires
reconciliation before execution resumes; restoring a bundle does not replay
external actions.

## Tests

See [the PostgreSQL test-service guide](../test/postgres/README.md). Tests use a
pinned disposable service and isolated namespaces. Production credentials and
operator databases are not test fixtures.
