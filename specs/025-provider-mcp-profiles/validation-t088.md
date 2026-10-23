# T088 — Receiver ACK observation after the T085 failure

Date: 2026-10-23. Source HEAD `f7fc120f282d0c89cb8fb4bd68914c9638ab4c51`. Status: fresh native generation PASS; managed run FAILED before receiver observation. No managed-v1 or global acceptance claimed.

T085 failed with sender completion and 196 receiver LAST HTTP 200 responses, without retaining enough receiver shape evidence to determine why the literal ACK gate failed. The next candidate preserves the mandatory T086 ACK/completion/counter/restart conjunction and every inherited helper; it adds bounded, content-free observations only. No parser, permission, model, per-read bound or phase deadline change is justified yet.

## Fresh generation prerequisite

The closed T085 generation runner was transplanted with only HEAD and output-label substitutions. Root verified exact whole-file equality after those substitutions, 507 current source pins and identical plugin/schema bytes. Only the two T087 live-test files differ from the T085 source pins. Root corrected stale preparation-control filenames and manifest labels before running 30 pure controls; these preparation fixes do not change actual generation predicates. Default operator-source overrides were confirmed absent without printing values.

Actual generation PASS in 13.970 seconds: native exit0, exact Unicode text, completed native event and successful expected-model/auth HTTP observations. Admission8,674MiB, deadline75s, floor6,144MiB, tools/MCP disabled. Owned process stopped; private state removed; operator auth/source/binary/plugin/runner/HEAD unchanged. Receipt SHA `e1ae9487635433599bb11bb7cff499463ae0cf78e9ec3029a4c9d38d92685b57`. Closed runner SHA `abe0873c645f71ee5c82bfb7263ed363fbfce7176dd5e0b97f3403bb86849742`; executed SHA `24edc2fc89bfae91b1651c8c88c8b443d11c0eaba891ba4ee83c03213b786c2e`. Executed bytes preserved and execution reclosed.

This generation result is only the prerequisite for reviewing and unlocking the new managed candidate. It cannot replace the receiver ACK, two-message/restart, AG-UI, catalog/operation or cleanup gates. T085 failure remains immutable and T088 remains open.


## Actual managed diagnostic

The root review verified all 17 inherited function ASTs unchanged, exact ACK-guard bytes, the complete overlay diff, 507 current source pins and the new generation prerequisite. Root reran 60 strict ACK controls and nine observation/privacy/import-order controls. Review caught a helper import before its hash check; the closed candidate was corrected to verify its exact path/hash before dynamic loading and to keep loading behind the execution gate. No acceptance predicate, receiver profile, timeout, permission or provider behavior changed.

Closed runner SHA `dba0deeafd2339a552ff7b4feb952a31e22e36a77086f007a15679bfefdf9f4d`; executed SHA `79f90f3bdbae8dbccd6b508b450e0e643db54026b12ccef7eb8ab2754a2140a9`. Observer helper SHA `049f6c58106fe0e2e4e8aedbc479f200b70fdcf674d05fafba8392ae123f662f`, also verified unchanged after execution. The only opening delta was the two gate flags; executed bytes were retained and the gate closed after exit1.

Managed outcome: FAILED `model_receipt_not_observed` in129.881s, first phase. Sender metadata reached completed/sequence1/completed1 and recovery state verified; one PG message was observed, with discovery and send_message calls completed. However 103 public LAST responses returned HTTP200 with the same261-character shape: Unicode word indicators true, but the literal marker, NFC/NFKC equivalents and whitespace-compacted marker all false. The actual viewport extractor likewise reported312 characters including one receipt line, without the exact/compacted marker. These are shape observations, not captured public plaintext. There is no evidence to identify model noncompliance versus extraction loss. The failure is not explained by whitespace wrapping alone. The unchanged deadline rejected the phase; no Unicode acceptance was granted.

Because the receiver observation occurs after sender acceptance, it never triggered: zero receiver samples, no receiver-ACK conclusion, no second phase or API-restart acceptance. T088 remains open for the intended receiver diagnosis; T085's earlier ACK failure remains separate and unexplained. Do not repeat an unchanged run merely to obtain a green result.

Minimum admission7,167MiB. This failed run itself completed API/capture/tmux/PostgreSQL/private-root cleanup, with no deferred cleanup or failure codes. Operator auth, source, binary, runner and HEAD remained unchanged. This provides current real cleanup evidence for the T087 harness correction; it does not convert the managed run into acceptance. The separate T085 failed-cleanup receipt and recovery remain preserved.

Composition impact: the T088 overlay changes only private diagnostic observations; product source and API schemas remain unchanged. Scoped pure contracts60+9 PASS, source/ownership bindings PASS and cleanup PASS; managed acceptance FAIL, causal diagnosis pending. Further provider changes require evidence at the native-text versus rendered/extracted boundary.
