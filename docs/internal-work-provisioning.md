# Internal launch provisioning

`WorkProvisioning.provision_launch` is an internal service port for recording a durable launch selection. It requires a verified `WorkRepository` and does not execute or dispatch work. The implementation is in `src/cli_agent_orchestrator/services/work_provisioning.py`.

## Principals and selector revisions

The caller must pass two already authenticated `Principal` objects: `admin_context` and `subject`. Both must carry the in-process verification seal and match their durable `work_principals` registration. The administrator must have the admin scope and own the job being provisioned; the subject needs a write or admin scope. IDs, request fields, SQLite rows, or environment values cannot be used to reconstruct either principal.

The logical selector is scoped to `(subject.id, selector)`, so the same selector used for another principal is independent. The service validates the selector and owns the durable provision ID and `ProvisionRef`. Creation requires `expected_revision=0`; replacement requires the exact current revision of an active provision. A replacement keeps the server-generated ID and increments its revision. A stale retry or a retired selection raises `ProvisionConflict` instead of adding another row. The revision check and insert run in the work repository transaction.

## Evidence required before writing

Within that transaction, the service verifies the repository, registered principals, job ownership, and the selected live grant revision and chain. The grant chain must target the same job and subject; the contract provider must be allowed by both the job and grant, and every grant in the chain must cover the contract permissions. The contract must be canonical and have `operation_kind="launch"`.

The frozen delegation snapshot must be authorized and match the job, contract ID, and delivered hash in the contract. The service stores its own canonical contract and hashes along with the adapter version and lease duration. `adapter_version` must be a positive integer; `lease_seconds` must be a positive integer no greater than 3600. Provisioning does not check that an adapter is installed: runtime admission checks the explicit adapter registry and fails closed for an unsupported version before admission.

Only after those checks does the service append the immutable provision history row and its work event. Invalid principals or mixed/unauthorized evidence raise `ProvisionDenied`; stale revisions raise `ProvisionConflict`. The denial tests assert `work_launch_provisions` remains empty after each rejected initial provision, with no partial write.

## Focused tests

Run the forged-principal boundary test:

```bash
.venv/bin/python -m pytest -o addopts= -q test/services/test_work_provisioning.py -k unsealed_admin_or_subject
```

Run the provisioning and adjacent composition/runtime coverage:

```bash
.venv/bin/python -m pytest -o addopts= -q \
  test/services/test_work_provisioning.py \
  test/services/test_work_launch_composition.py \
  test/services/test_work_launch_gateway.py \
  test/services/test_work_launch_runtime.py \
  test/services/test_work_launch_durable_origin.py
```

These commands describe how to run the existing tests; this document change does not report a test result.

## Current boundary

This port has no production caller that supplies both verified principals. It does not add HTTP or MCP provisioning endpoints, lifespan wiring, provider integration, an operator database, or gateway installation/activation. The existing HTTP `/work-launches` route is an admission route through an injected gateway; without that gateway it returns 503, and it does not provision a selection. Creating a durable provision alone therefore does not make a provider or launch path effective. T035 remains open: an authorized operational caller is missing, and activation remains prohibited in this phase.
