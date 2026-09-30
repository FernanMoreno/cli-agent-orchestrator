# Feature Specification: Personal deployment and remaining acceptance

**Checkout**: main
**Created**: 2026-09-30
**Status**: Implementation authorized by “hazlos todos”

## User Scenarios & Testing

### US1 — Incorporate the accepted upstream candidate (P1)

The operator uses the accepted T085 features in the current checkout while
preserving every current modification. Independent test: a recoverable snapshot,
three-way integration, no conflict markers, and the affected regression gates.

1. Given the dirty checkout, integration preserves later local documentation.
2. Given the accepted candidate, incorporation retains its code and contracts.

### US2 — Use both OpenCode versions and Claude (P1)

The operator launches authorized providers without additional spending.
Independent test: real workflows and provider/backend cells, pinned versions,
recorded receipts and cleanup. Subscription/free models only.

1. V1 retains its accepted behavior; v2 launches through its supported CLI.
2. Claude's renewed subscription login completes the real workflows.
3. Unsupported or unavailable combinations remain visible with exact reasons.

### US3 — Operate a persistent personal service (P1)

The operator runs CAO from this computer, reachable only on localhost. State,
configuration and login records survive server restart. Independent test:
authenticated health check, real provider work, restart, backup and restore.

1. Startup rejects insecure permissions or incomplete configuration.
2. Restart preserves data; expired client credentials can be renewed safely.
3. A consistent backup restores into a separate directory without overwriting
   the active service or leaking credentials into the repository.
4. Recovery records uncertain in-flight work rather than blindly repeating it.

## Requirements

- FR-001: Preserve current changes and record recoverable integration inputs.
- FR-002: Integrate the fixed local upstream object and accepted candidate changes.
- FR-003: Support OpenCode v1/v2 with explicit version-dependent launch/parsing.
- FR-004: Accept Claude real workflows with subscription authentication.
- FR-005: Inventory O03 combinations; exercise authorized available combinations
  on both terminal backends and retain unavailable scenarios as limitations.
- FR-006: Provide a durable localhost deployment, private credentials, reproducible
  startup/shutdown, automatic restart and a consistent backup/restore command.
- FR-007: Register the accepted Docker Work backend explicitly and distinguish
  its static/process contract from interactive model-provider work.
- FR-008: Accept real provider execution and Docker Work in their actual routes;
  never advertise a networked model worker inside a no-network static contract.
- FR-009: Run affected tests, architecture and composition checks; preserve
  evidence separately for current checkout and prior candidate.

## Success Criteria

- SC-001: No current modifications lost and no unresolved integration conflicts.
- SC-002: Each claimed provider/backend scenario has fresh passing evidence.
- SC-003: Personal server is accessible only locally, authenticated, and durable
  across restart; backup restoration preserves a known state record.
- SC-004: No paid API models are used in real-provider acceptance. Codex and
  Claude use subscriptions; the existing Go key authenticates the explicitly
  selected free model. Additional spend remains zero.
- SC-005: Final status distinguishes completed work from unavailable acceptance.

## Assumptions and Edge Cases

One user, current WSL/Linux computer and existing Docker; no public endpoint,
remote publication or additional charges. Sleep/shutdown interrupts availability.
Real quota exhaustion is not induced. No new account/provider is inferred from
“all pending”; credentials and free access constrain the acceptance inventory.
Database backup must include live WAL contents through SQLite's backup API.
Credential renewal does not establish fresh model acceptance by itself.

## Key Entities

Recoverable integration snapshot, versioned provider adapter, acceptance cell,
private deployment configuration, durable CAO home, and restorable backup.

## US3 correction — authenticated browser access

Reproduction: static UI returns 200, but its unauthenticated `/sessions` polling
returns 401 and displays Offline. CLI/TUI acceptance did not cover browser auth.
FR-010: Browser REST, SSE and terminal WebSocket requests must carry the verified
operator bearer using existing server authentication. Browser tab storage only;
no bearer in localStorage, repository or public bootstrap endpoint. An operator
command creates a private fragment link; browser consumes/removes the fragment.
FR-011: Expired/missing credentials show a connection form and can be replaced.
SC-006: Real built browser fetches protected profiles/sessions successfully;
unauthenticated routes still reject access. No authentication bypass or new issuer.
