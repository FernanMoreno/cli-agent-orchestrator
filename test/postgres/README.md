# PostgreSQL test service

The test suite uses PostgreSQL for all online persistence tests. A missing test
service is an error; PostgreSQL tests do not silently skip.

## Local run

From the repository root, with Docker Engine running:

```bash
uv sync --locked --all-extras --dev
uv run pytest -o addopts= -m "not e2e" test/
```

The session fixture starts the pinned PostgreSQL 18 test image when no owned
service is already running. It creates a private Docker network, publishes the
database only on host loopback, stores the generated administrator password in
a mode-0600 temporary file, and removes the container, network, and files when
pytest exits. The local full-suite profile uses a 3 GiB PostgreSQL tmpfs, a 4
GiB container memory/swap limit, `max_locks_per_transaction=256`, and bounded
WAL retention (`max_wal_size=256MB`, `min_wal_size=64MB`). Every hundred worker
resets, the fixture checks the actual tmpfs and stops the run if less than 128
MiB remains. The separate T097 guest service retains its independently verified
1 GiB profile. The fixture fails with an actionable error if Docker is
unavailable, the daemon is not running, or more than one owned service needs
explicit selection.

An externally managed disposable service can be selected with
`CAO_TEST_POSTGRES_METADATA_FILE`. The metadata and its relative password file
must both be private files owned by the current user and must identify the
pinned, test-owned service. Production database targets and credentials are
not accepted by this path.

The CI test job checks Docker availability and sets
`CAO_TEST_POSTGRES_AUTOSTART=1`; the same fixture provisions the service for
each isolated runner.
