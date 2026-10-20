# Last three service failures: diagnosis, selective fixes, acceptance

Outcome: PASS. Source change is restricted to coordinator deadline initialization; test migration uses genuine RSA verification. No principal, private-plan, immutable-binding, owner or deadline-integrity checks relaxed.

## Initial focused evidence

Current three requested IDs are4parameter cases (origin/grant history +operations +delegation). Focused run3passed/1failed `/tmp/spec008-last-three-focused.log`: native history and operations passed without full-suite contention; bearerless delegation consistently401. This alone did not justify treating the intermittent native cases as stale.

## Real product timing defect behind native/operations failures

`WorkWorkflowPlans.start` stores second-resolution workflow_run.started_at before private attachment/atomic callbacks. `WorkCoordinator.start.attach` previously calculated deadline from a later time.time() after driver initialization. `_row` correctly rejects deadline beyond stored started_at+frozen policy budget+1second. Honest attachment taking>1s violates this guard, so a valid controller can fail before any revoke or next Work effect.

Deterministic actual SQLite/private prepared fixture delayed actual driver.enable completion by1.2s; normal verified owner status then raised coordinator_private_deadline_integrity. No run fields or proofs were forged. RED `/tmp/spec008-last-three-deadline-native.log` and repository RED `/tmp/spec008-last-three-deadline-red.log`.

Root's retained failed native origin case confirms identical cause:
- `/var/tmp/cao-spec008-services-final-parallel/popen-gw1/memory-metadata1379/cli-agent-orchestrator.db` opened read-only.
- Actual grant paths point exactly to `.../test_actual_native_accepted_hi0`.
- Durable workflow_run state failed; actual owned Python error was `RuntimeError: coordinator context refused` before revocation.
- Actual deadline exceeded durable started_at+approved deadline_seconds by **1.0091543197631836s**, enough for the preserved guard to refuse the coordinator context API.

Minimal fix in `services/work_coordinator.py`: read workflow_run.started_at inside the SAME existing atomic attach transaction; parse its UTC timestamp; initialize deadline=durable start epoch+frozen policy deadline_seconds. No grace increase, new clock tolerance, retry, policy rewrite, archive migration or generation change. Existing private deadline validation remains unchanged.

New `test/services/test_integration_008_coordinator_deadline.py` proves delayed actual start succeeds with exact timestamp-anchored deadline, authentic foreign owner refuses, a fresh competing driver cannot claim an active lease, and malicious+2s deadline extension still fails integrity.

## Delegation401: genuine fixture migration

Old `test_work_delegation.py` replaced API get_current_principal and FastAPI auth scope dependencies, then sent no bearer. Current `_run_step_scopes` deliberately invokes actual auth.get_current_scopes and `_optional_run_step_principal` authenticates actual bearer; the old overrides do not authorize that route. Missing bearer401 is correct.

Migrated only this test: actual2048-bit RSA key, configured issuer/audience/JWKS URI, real signed RS256 bearer, offline JWKS key provider returning actual public key; original token decoding/signature/claims/scope validation stays active. Removed manufactured request principal and dependency authorization overrides. An unauthenticated request now explicitly proves401/no dispatch, followed by authenticated200. Forged caller_id remains body metadata; actual server-verified principal is forwarded and no Work item/child binding/task receipt created. Product auth/API source unchanged.

## Changed paths

- src/cli_agent_orchestrator/services/work_coordinator.py (trusted start timestamp anchor only)
- test/services/test_integration_008_coordinator_deadline.py (new deterministic regression)
- test/services/test_work_delegation.py (genuine RSA bearer fixture)

No product edits outside coordinator, provider launch, external service/credential, commit, docs/Graphify/frontend edit. Retained root diagnostic databases were read-only.

## Fresh gates

Focused initial green2cases13.40s: `/tmp/spec008-last-three-green-first.log`.

Full affected suites (coordinator, deterministic deadline, native continuation review, operations, delegation):

`TMPDIR=/var/tmp .venv/bin/python -m pytest test/services/test_integration_008_coordinator.py test/services/test_integration_008_coordinator_deadline.py test/services/test_integration_008_continuation_review.py test/services/test_integration_008_operations.py test/services/test_work_delegation.py --basetemp=/var/tmp/cao-spec008-last-three-final --no-cov -p no:cacheprovider -q`

**51 passed in111.19s**, `/tmp/spec008-last-three-final.log`. Actual private-FD subprocess/accepted-byte/native continuation/revoke/current-owner/lease/fence cases remain executable, not proof factories. No skip/xfail added.

`project-composition-check "$(cat .ai/project-name)"`: **5 kept,0broken**, `/tmp/spec008-last-three-composition.log`. Owned3file isort check, Black check and scoped whitespace check passed. Source now frozen and ownership released to root for its Linux/Graphify snapshot and final broader gates.
