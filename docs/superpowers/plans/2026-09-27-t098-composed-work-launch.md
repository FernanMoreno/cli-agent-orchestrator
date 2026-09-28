# T098 Composed Work Launch Implementation Plan

> Execute inline in this session under the user's instruction to finish the open audit task. Do not commit. Every implementation step starts with a failing test.

**Goal:** Compose durable launch admission and dispatch with the Bubblewrap Work backend and process supervisor while keeping the backend unregistered until T097/C08 host acceptance.

**Architecture:** Keep the HTTP gateway as admission-only. A server-owned registered dispatcher reads the persisted delivery through `WorkAdmission`, applies its reservation and authority fences, then calls one process-execution capability for the selected Work backend. The capability stages only exact immutable executable content, persists setup ACK and process identities before GO, and records uncertain cleanup for reconciliation; it never exposes Tmux/Herdr fallback.

**Tech Stack:** Python 3.12, SQLite, Bubblewrap 0.13 policy, Linux Landlock/seccomp, pytest.

**Spec:** `specs/001-verifiable-orchestration/spec.md`, acceptance scenario US2.11 and T098 in `specs/001-verifiable-orchestration/tasks.md`.

## Global Constraints

- Keep `src/cli_agent_orchestrator/backends/work_registry.py` at `WORK_BACKENDS = {}` until T097/C08 host acceptance is complete.
- Bubblewrap and supervisor failures reject before worker effects; neither Tmux nor Herdr may substitute for a Work process boundary.
- Bind launch, dispatch, setup ACK, process identity, GO, termination and cleanup to the live item, attempt, generation, revision and contract.
- Revalidate authority at GO and immediately before every protected effect; uncertain cleanup remains reconcilable and is never redelivered automatically.
- Only exact immutable executable content in the contract mapping may execute. Dynamic loaders invoked as commands, unbound shebang interpreters, memfd execution and unlisted FDs fail closed in v1.
- Scratch artifacts and WSL2 tests are code evidence only, not host acceptance. Do not activate T019/T097/Tmux/Herdr or use the operator database.

---

### Task 1: Specify the end-to-end dispatcher boundary

**Files:**

- Modify `specs/001-verifiable-orchestration/spec.md` and `plan.md`.
- Create `docs/superpowers/plans/2026-09-27-t098-composed-work-launch.md`.
- Test in `test/integration/t098/test_work_launch_dispatch.py`.

- [x] Write an SQLite-backed integration test that admits through `DurableLaunchGateway`, observes a queued durable receipt, dispatches only through the registered Work adapter, and proves the test worker receives the exact item/attempt/generation/revision/contract binding.
- [x] Add negative scenarios for changed authority before effect, missing backend, Tmux/Herdr substitution, cleanup uncertainty, restart, and duplicate dispatch. Pre-GO rejection leaves no worker effect; uncertainty reconciles without automatic redelivery.
- [x] Run the focused integration module and confirm the new dispatch and process-execution behavior fails for the missing production bridge, not because of fixture setup.

### Task 2: Add a single protected Work process-effect capability

**Files:**

- Modify `src/cli_agent_orchestrator/services/work_admission.py` and `src/cli_agent_orchestrator/backends/work_backend.py`.
- Modify `src/cli_agent_orchestrator/services/work_launch.py` and add a focused service module only if the execution protocol cannot stay small.
- Test in `test/services/test_work_launch.py` and `test/services/test_work_admission.py`.

- [x] Make production `DurableLaunchGateway` process-only through the V2 process-effect capability; keep the explicit V1 compatibility adapter outside this gateway.
- [x] Revalidate exact admitted binding and resource reservations immediately before process setup and each protected effect.
- [x] Reject missing, expired, replaced or mismatched capabilities before any process backend call; verify a Tmux/Herdr backend cannot substitute terminal effects.

### Task 3: Confine executable mappings and supervise the Bubblewrap process tree

**Files:**

- Modify `src/cli_agent_orchestrator/services/work_process_supervisor.py`, `work_process_landlock.py`, `work_process_seccomp.py`, `work_bubblewrap_composition.py`, and `work_bubblewrap_setup_intent.py` only where the RED tests require.
- Modify `src/cli_agent_orchestrator/backends/bubblewrap_backend.py` without registering it.
- Test in `test/services/test_work_process_supervisor.py` and `test/security/test_work_bubblewrap_adversarial.py`.

- [x] Permit only the immutable executable mapping, deny writable executable mounts, and run adversarial coverage for loader invocation, rejected shebang content, `execveat`, memfd, inherited FDs, fork and double-fork behavior. The composed scratch worker re-executes its own mapping from a double-fork descendant and proves every other tested entrypoint is denied.
- [x] Persist setup ACK and exact process identities before GO, revalidate current authority at GO, and route process-tree termination/recovery through `WorkProcessSupervisor`.
- [x] Prove cleanup uncertainty stays attached to the original attempt and restart reconciliation never retries its launch.

### Task 4: Connect the internal dispatcher and exercise composed failures

**Files:**

- Modify `src/cli_agent_orchestrator/services/work_launch_runtime.py`, `work_launch_gateway.py`, and `src/cli_agent_orchestrator/api/main.py` only for server-owned dispatcher wiring; keep public request and receipt semantics admission-only.
- Add `test/integration/t098/` coverage using a temporary SQLite database and scratch worker artifacts.
- Update `specs/001-verifiable-orchestration/tasks.md` with evidence and remaining T097/C08 host gates.

- [x] Drive admission → registered dispatch → pinned scratch Bubblewrap setup → supervisor → worker with success, authority revocation before GO, duplicate/restart, descendant-exec adversaries, and uncertain cleanup cases. The scratch fixture overrides `preflight_work` and wraps the result only to capture stdout; the real bound process capability, Bubblewrap, Landlock, seccomp and supervisor run. This does not change the production registry or count as host acceptance.
- [x] Verify pre-GO failures leave no worker effect and never fall through to Tmux/Herdr; verify uncertain process/effect states block redelivery.
- [x] Run focused tests, architecture checks, composition review, and final diff review. Keep host-gated T097 acceptance unchecked and backend unregistered.
