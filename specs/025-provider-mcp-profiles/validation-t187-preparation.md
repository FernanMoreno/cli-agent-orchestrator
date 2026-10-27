# T187 — native streaming activity preparation

Status: **native SQLite TEXT growth not observed; T187/T168 remain open**.
The diagnostic reader is not connected to cancellation. No production behavior
or accepted legacy/native_tool predicate changed.

Three serialized model-free controls used source `09f1ab14`, the pinned native
OpenCode 2.0.18 binary, the current production configuration writer and readonly
schema/authorizer. Bubblewrap isolated network/PID namespaces and HOME/XDG;
only a local synthetic SSE provider and dummy credentials were available.
No operator login, real model, PostgreSQL or Docker was used. The native budget
remained 75 seconds and memory admission remained 6144 MiB.

| Attempt | Result | Evidence and limit |
| --- | --- | --- |
| R1 | Timeout 75.564 s; 11 POSTs; no assistant | Context4096 was below the known20000 compaction buffer. The private DB contains a compaction row. This harness configuration failure does not establish native streaming behavior. |
| R2 | Timeout 75.437 s; 10 POSTs; empty assistant | Context65536 removed compaction. The one-request cap rejected further requests. The synthetic canary appears in the session title, demonstrating that the served response was used for title generation. No partial/final assistant TEXT was accepted. |
| R3 | CLI exit0 in9.989 s; 2 POSTs | Reused the successful T119 cap of4. Forty fresh readonly snapshots across two four-second holds show the same unfinished assistant with one TEXT part and zero text bytes. After completion it contains81 bytes, positive completion and finish=stop; the existing strict final reader passes. **No pre-finish TEXT growth was observed.** |

R3 checks the shared monotonic finish flag before and after each snapshot and
rejects observations once any synthetic response has sent finish. This is a
conservative bound; it does not attribute a POST to the primary request. Both
native and outer process groups retired in every attempt. Admission memory was
10454/10179/10456 MiB. Failed receipts and exact executed descriptors remain
private and unchanged. [Redacted receipts](validation-t187-native-store.json)
and the [exact R3 source archive](fixtures/t187/control/native_stream_store_control.py.txt)
preserve the result. The archive is evidence, not a current executable launcher.

The prototype readonly reader binds diagnostic user, active PG receipt hash and
generation, expected agent, native database identity and a unique unfinished
assistant. It exports only identifiers and counts. Independent Luna review
found two draft false positives: an intervening user could redirect attribution,
and capture did not compare the read inode to the ownership inode. Both were
corrected; the intervening-user regression first failed, then the final suite
passed28 tests in13.46 s. The ownership mismatch has positive/negative controls.
Architecture/composition passed5 contracts/0 broken. These are offline contracts,
not proof that this binary persists partial TEXT.

Luna Max agents prepared the control, independently audited selection/identity,
and reviewed the context and before/after finish corrections. Root inspected
their outputs and executed all checks. Claude's previously observed quota refusal
was not retried. Foreign generated Graphify changes were preserved.

Next: separately review a transparent primary HTTP stream observer, reusing the
existing native hook and request-binding infrastructure. It must bind the exact
current request/receipt, observe genuine TEXT deltas without replacing or holding
the original response, and retain bounded buffering/cancellation/error behavior.
Then verify the installed multi-poll controller and only afterward run freshly
source-bound paired native cancellation. This control does not prove that SQLite
always withholds partial text, HTTP liveness, served-model identity, or T168
acceptance; it prevents using an unproved DB signal as cancellation authority.
