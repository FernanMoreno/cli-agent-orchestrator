# T189: native generic-message effect

## Witness preparation

The new pure witness requires an explicit structured MCP success, positive
integer message identity, exact sender/receiver/time, exactly one delivered
inbox row with unchanged Unicode, and one redacted pair-scoped AG-UI event.
Baselines must be empty for the fresh diagnostic identities. The event's
`message_id` identifies the receiver terminal; the structured result's
`message_id` identifies the inbox row. Restart comparison uses the exact copied
row returned by the initial witness and accepts no field changes.

34 witness controls pass. The combined witness, existing Codex native-catalog
contracts and message-observer suite passes66 tests in25.61s, without a database
or model. A review found a false success through additional event fields:
two REDs reproduced the leak before correction. The witness now restricts
event keys to the source-defined envelope, validates scalar metadata and
rejects the exact operation body in all allowed string fields. The shared
generic-message validator and production event contract are unchanged.

The initial test-first run failed collection before the new module existed;
this is separate from the two reproduced witness defects. Read-only Claude
review of the correction found no remaining blockers. Architecture:5 contracts
kept,0 broken. No production permissions, schemas or persistence behavior change.

## Native diagnostic status

The accepted source-bound diagnostic uses pinned Codex0.164.0 app-server MCP
calls, an authenticated production API, verified PostgreSQL and credential-free
`mock_cli` registrations/receiver delivery. It sends no model turn. It checks
unauthenticated HTTP401/no row, a permitted generic message, native-client
excluded-call denial/no extra effect and read-only API restart. Registered
actors remain `mock_cli`: this is not native Codex provider-terminal acceptance.

The selector now points at installed0.165.0; the accepted0.164.0 binary remains
available and is selected explicitly by its previous hash. No installation or
login change was performed. A private syntax admission failure and r1's missing
historical bare-import path were caught before any PostgreSQL/API/native-client
startup or credential staging. r1 is preserved and its operational descriptor
reclosed. r2 passed all25 actual launcher import statements with the exact
canonical virtualenv/PYTHONPATH before fresh archive creation.

r2 exposed a launcher protocol error: the PostgreSQL provisioner is a context
manager, not an iterator. It was reproduced without acquiring a service;
staged credentials were removed and the operational descriptor reclosed. r3
validated all25 imports and bound28 direct helper calls, then created real PG
and two mock actors, but stopped at AG-UI404 because its enablement flag was
missing. r4 enabled AG-UI and reached native MCP, but its auth-disabled child
ignored the token file and the API returned401. Neither failure is relabeled
as acceptance. Both real-service generations retired every borrower, removed
staged credentials and closed their exact PG container/network with originals
preserved. Source/operator hashes remained unchanged.

The absent-versus-present auth-selector behavior was reproduced offline against
`get_local_bearer`; r5 carries the matching API selectors and retains the native
response before waiting for delivery. These are diagnostic configuration fixes,
not production permission changes. Native results were pending until r5; T189/CX-05
and global gates remained open during preparation. T032 stays deferred.

## Actual scoped acceptance

r5 passes in101.572s on base `64aa28da3de4ee64af387b40339f74d2507cc678`,
with498 source pins, explicit0.164.0 binary and the new witness bytes additionally
hash-bound before execution. `structuredContent.success` is the boolean true;
its inbox ID, sender, receiver and creation time match exactly one actual
PostgreSQL delivered row. Its Unicode body and the one redacted AG-UI delivery
event pass an independent reread through the strict witness. No textual fallback
is used. The native catalog contains only `send_message` and real readiness
is observed before the call.

A request without credentials gets401 and creates no row. The native client
rejects an excluded valid `cao_ask_user` request with the previously accepted
exact disabled-tool error; rows and the event count remain unchanged. This is
client denial evidence. The permitted API call uses a fresh private test JWT
with read/write/admin scopes, not proof of per-operation server authorization.

After one API restart, GET and direct PostgreSQL SELECT preserve the exact
accepted row, including ID/time/body/status, without another send. No model turn
is started. Both registered actors and the receiving process are `mock_cli`;
the real Codex app-server executes the MCP call. This accepts native MCP
transport/effect composition, not native Codex provider-terminal/model behavior.

Minimum sampled pre-native admission memory10170MiB exceeds6144MiB. The accepted
app-server75s control budget is unchanged;101.572s includes PG/API/actor setup,
restart and cleanup, not a model completion. Native/API/capture/tmux borrowers
physically stop before targets and the PG owner close. The exact owned
container/network are absent and original containers remain. Staged credentials
are removed; source/operator hashes are unchanged. Operational descriptors are
reclosed and exact executed archives preserved. No login renewal or global
installation occurs. [Sanitized receipt](validation-t189.json).

T189 is complete at FR167/SC151 scope. CX-05 remains open for the native-provider
actor journey, remaining message authority/contracts and final global gates.
No `cao.work.message` acceptance is inferred.

## Composition and limits

Graphify located the orchestration sender/inbox path; actual source confirms
that insertion alone does not emit the delivery event. Delivery calls
`terminal_service.send_input` with sender/orchestration attribution; the
event-log publisher excludes the body and AG-UI maps routing metadata.
The witness's stricter envelope comes from `agui_stream._base`, not an invented
transport schema. Pure tests alone are not proof of PostgreSQL, HTTP or native calls; the r5
receipt independently establishes the bounded real composition above.

The diagnostic must retire native/API/capture/tmux borrowers before dropping
targets and closing its verified PostgreSQL owner. Uncertain retirement holds
the owner for root reconciliation. Historical jobs are untouched. No
`cao.work.message`, per-operation server authorization or global acceptance is
inferred from generic-message/client-catalog results.

Composition verdict: PASS for the bounded native MCP/authenticated API/PostgreSQL/AG-UI/restart and owned-cleanup chain; broader native-provider and Work gates remain open.
