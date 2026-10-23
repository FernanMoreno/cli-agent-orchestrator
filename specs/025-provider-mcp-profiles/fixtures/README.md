# Native-v1 diagnostic fixture

This reader is not imported by CAOS product code. Reviewed private acceptance launchers bind its exact SHA before using it on the current run-owned major1 database and durable receipt. It exports booleans/counts only; it never supplies acceptance text. Limits and scope are described in FR071–FR073 and validation-t089.md/validation-t090.md.

Run synthetic controls from the repository root, without native services or operator data:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. python specs/025-provider-mcp-profiles/fixtures/test_native_v1_probe.py
```

The tests create only temporary synthetic SQLite tables. An actual native diagnostic additionally requires reviewed source/binary/ownership/generation bindings, a private run, PostgreSQL identity verification and the original memory/deadline/acceptance gates. No default operator-database location is opened by this fixture.
