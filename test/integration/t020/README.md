# T020 local Docker acceptance

The deterministic static ELF in this directory exercises managed `agent_step`
without a model provider. It reads the frozen worker input, sends the authenticated
`cao.work.task_received` call over the private MCP socket, then submits the fixed
`{"value":"t122-deterministic"}` result. Input containing
`T122_OMIT_RESULT` makes it acknowledge and exit without a result, so workflow
state must remain pending. Input containing `T122_FAIL` acknowledges the exact
attempt, emits a diagnostic marker, and exits with code 23 without submitting a
result. Work records that failure only after Docker verifies process exit,
container removal, and attempt-image removal.

Run the real Docker/API YAML and script acceptance suite only by opting in:

```bash
CAO_T020_DOCKER_ACCEPTANCE=1 ./test/integration/t020/run-docker-acceptance.sh
```

The runner reuses T019's local Linux Docker rootfs build and adversarial Docker
backend suite, verifies its immutable image ID, pins this worker source with
`agent-step-worker.sha256`, and reports the compiled ELF digest. Docker Work embeds
the exact staged worker digest in each attempt image and uses T019's private
per-attempt MCP proxy. The process-wide
`WORK_BACKENDS` registry remains empty; the local Docker backend exists only for
this explicit runtime opt-in.

Each acceptance case enters the real FastAPI lifespan through `TestClient`.
Startup composes the opt-in Docker backend, v2 process adapter, workflow origins,
result service, and projector on the same repository, then projects verified
pending results before serving requests. The tests close that server and enter
a second lifespan over the same SQLite and immutable content stores before
resume/recovery. A separate startup composition check is
`test/api/test_workflow_managed_ingress.py::test_managed_workflow_runtime_composition_binds_startup_projector`,
run with:

```bash
uv run pytest -o addopts= -q test/api/test_workflow_managed_ingress.py::test_managed_workflow_runtime_composition_binds_startup_projector
```

The YAML route and script `run_step` route cover authenticated receipt and typed
result handling. The tests compare frozen spec/source hashes, delegated snapshot
IDs, context hashes and exact bytes, workflow binding fingerprints, receiver
receipts, and Work attempt identity before and after restart. The
ACK-without-result worker case must remain `work_pending` after recovery with
the same workflow and Work attempts and without a second dispatch. The runner
also executes T019's Docker sibling isolation probe for the private MCP socket.

The failure case verifies that YAML and script steps project the exact failed
Work attempt, remain failed across restart without creating another binding or
dispatch, and retry only through the admin endpoint's exact run, step, Work
attempt, and generation fences. Replaying the same retry request returns the
same new binding and Work attempt. A second worker failure proves the new
attempt follows the same verified cleanup path.

This acceptance proves behavior only for the local Docker engine/kernel setup on
which it is run. Docker Desktop, WSL2, or this test does not establish support for
any production host, provider, or deployment configuration.
