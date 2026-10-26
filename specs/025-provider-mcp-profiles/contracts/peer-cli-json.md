# Operator pairing JSON v1

Opt in with `cao peer pair INSTANCE --project PATH --json` or `cao peer accept CHALLENGE --json`.

A command that reaches the operation emits exactly one JSON object on stdout. Result schemas are generated from `models/peer_cli.py`: pair and accept use `schema`, `success: true`, `data`; operation failures use `cao.peer.error.v1`, `success: false`, `error.operation` and `error.code`. Exit is zero for success, one for operational failure. Ordinary Click usage/help handling remains its CLI contract.

Pair data contains the one-use code intentionally, as does its existing human result. Treat it as sensitive: T168 captures it only in private 0600 files and never exports it to task/model output. Accept data contains no code. Failure envelopes contain stable codes (`pairing_failed`, `pairing_declined`, `pairing_aborted`, `pairing_result_invalid`), without exception text or validation inputs. `pairing_result_invalid` means the service may already have created a challenge or grant but its output did not satisfy the machine schema. A failure does not assert rollback or absence of an already-created challenge/grant.

Accept still displays the requesting identity, project and scopes and requires confirmation plus the one-use code. JSON mode sends this review, prompts and confirmation echo to stderr. It does not silently authorize the pairing. The human default remains available.

The paired controller requests JSON explicitly, rejects prose, duplicate members, wrong schemas, extra fields and non-boolean success, and binds the returned destination/project to the expected profiles and pairing. It rechecks both process generations after acceptance. No localized-output fallback is used.

This defines these CLI operations. MCP retains its tool protocol/structured result, HTTP retains status and response models, AG-UI retains its SSE event contract, and provider-native receipts retain their lifecycle meaning. Reuse operation schemas across consumers; do not transplant one transport's envelope into another without a scoped migration.
