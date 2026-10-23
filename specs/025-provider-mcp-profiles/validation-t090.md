# T090 — Completed receiver native ACK comparison

Date: 2026-10-23. Prepared on HEAD `314a1ce3` with507 unchanged runtime source pins and a separately hashed diagnostic helper. Status: actual comparison observed; managed acceptance FAILED.

T089 passed the sender Unicode check but failed the completed receiver's literal ACK gate. Its223public LAST observations ended with20characters, no exact/whitespace-only ACK and no receipt marker. Those shapes do not prove whether the non-ACK text originated before or after the native store. T090 applies the existing bounded read-only native comparator to that receiver boundary.

The comparator gains a per-call fixed expectation (Unicode marker or ACK), retaining Unicode as the default and rejecting arbitrary expectations. It does not mutate a global marker or change either acceptance predicate. The receiver hook occurs after the expected completed turn and a non-ACK HTTP200 result, at most once per phase, with its own receiver identity, durable receipt and private ownership claim. Capture and SQL share the3s budget within the original75s. Only counts/shapes/equality booleans are retained; reasoning/tools/credentials and raw text are excluded.

All22synthetic controls pass, including default Unicode preservation, literal ACK expectation, native/API equality and divergence, identical native/public nonconforming text, and arbitrary-marker rejection. Root reran60 ACK controls and verified17 unchanged inherited helper ASTs; composition passed five contracts with zero broken. Strict Unicode/ACK/counter/restart/AG-UI/catalog/cleanup gates, prompts, model and6,144MiB admission remain unchanged.

The fresh successful T089 generation receipt `980dcbcb24d9352cdaa6ba125118d983929c66857f46e726cbecb9776e303e82` is reused explicitly while exact HEAD and all507runtime pins remain unchanged. It is not described as a new T090 generation run. The extension only affects the separately pinned diagnostic artifact, unused by generation. T089 executed helper bytes are archived with their original hash; the current parameterized helper is a new version.

Closed candidate SHA `82dc68ec1d94931109862e823937ace22b4aad77c79067de416b63b0464b56bc`; current helper SHA `36917fd04f03907578e7969320ad0812f3e846353db9a473ebcaff339a61d7a6`. Independent read review and unchanged-source/memory/PostgreSQL verification must precede opening the two execution flags. Historical failures remain immutable; diagnostic values cannot supply a successful ACK or settle a turn.


## Actual result and boundary found

Independent read review `fd413a9b2fc9431db8ed627c06f21eef` found no blocker. Root ran six controls against the actual hook condition (exact ACK must not trigger), verified all507runtime pins and the current generation prerequisite, and opened only the two execution flags. Executed SHA `17bc53fdfe554eb40d436794c4c9d1f2ac3b4cd0daad665acafbd0e83ffad820`; executed script/helper bytes preserved and gate reclosed after exit1.

The managed run FAILED receiver_ack_not_observed in131.287s. Crucially, the receiver native comparison was observed: one completed receipt-bound TEXT part (54chars/3lines including receipt); its public native text was exactly ACK (3chars/1line), while the API published20chars/3lines. Exact/NFC/NFKC/compact-native-ACK flags were all true; their API equivalents false, and native_equals_api false. The fresh user/parent, private ownership, durable receipt and native TEXT checks all passed. No plaintext or content digest was exported. The typed native result is already correct; the extra public material originates after that native result. This does not identify its literal extra text, which was not retained.

Observation took13ms, total capture+comparison17.2ms within the original phase and3s budget. Original-run API/capture/tmux/PostgreSQL/private-state cleanup and operator/source/binary/runner preservation all passed. There was no second-phase/API-restart acceptance. T090 completes this bounded comparison, not native-v1/global acceptance; T091 reproduces and corrects the native collapsed-header extraction defect consistent with the observed shape.
