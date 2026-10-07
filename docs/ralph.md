# Bounded Work continuations

Ralph uses the existing approved workflow executor and Work ledger. It does not create a principal, grant, DelegationSnapshot, or approval. The selected provider/profile must already have an authorized workflow provision seed for the exact generated template source.

1. Publish the fixed source with `cao ralph template --provider PROVIDER --agent PROFILE --name NAME`. The default memory policy is `exact-snapshot`; `--memory off` requires an existing authorized empty snapshot.
2. Discover current workflow provision seeds with the workflow seed discovery command/API. An operator can explicitly provision the new template through the existing workflow provision facade using real job, grant, receiver, contract, and snapshot references.
3. Create a preparation JSON file, then run `cao ralph prepare request.json`:

```json
{
  "workflow_name": "NAME",
  "task": {"description": "Perform the reviewed task"},
  "criteria": [{"kind": "output_equals", "path": ["answer"], "value": 42}],
  "target_mappings": {"repo": "/actual/authorized/checkout"},
  "binding_selections": {"repo": {"PROFILE": "existing-seed-id"}},
  "min_iterations": 1,
  "max_iterations": 8,
  "deadline_seconds": 3600,
  "stall_limit": 3,
  "correction_budget": 4
}
```

4. Review the returned prepared plan through the workflow plan review interface. An administrator approves the exact `plan_id` using the existing workflow approval command or web review interface.
5. Start with `cao ralph start PREPARED_ID --plan PLAN_ID --run-id RUN_ID`. The web Ralph panel exposes the same prepare, review, approve, and start sequence.
6. Inspect with `cao ralph status RUN_ID`; submit bounded next-iteration feedback with `cao ralph feedback RUN_ID --request-id UNIQUE_ID --text TEXT`. Replaying a request ID with different text refuses. Feedback never pastes into an uncertain active turn.
7. `cao ralph resume RUN_ID` uses the same fenced driver as automatic continuation and refuses unresolved Work. `cao ralph stop RUN_ID` fences future steps and terminates the owned controller process. A pending worker remains visibly `stopping` until actual Work cleanup proves cessation. `cao ralph complete RUN_ID` verifies the accepted artifact and current authorized criteria; it cannot manufacture success.

Only an authenticated accepted Work result advances an iteration. The result projection and durable wakeup commit together; one leased driver owns generation changes and process allocation. Restart requires a currently verified configured principal matching the original owner. Missing credentials, changed approved source/profile/context/checkout identity, revoked authority, or unresolved process identity pauses execution. A worker commit can change the approved checkout HEAD and require a new preparation; the controller does not silently approve a replacement plan.

Completion criteria support exact accepted-output values and bounded relative file hash/content checks. File reads require the original `fs_read` tool/path grant and permitted profile tool; they reject symlink traversal and unstable or oversized files. Read-only status verifies historical accepted-result evidence. Fresh file/authority validation remains a write-authorized completion operation.
