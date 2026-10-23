# T089 — Native-v1 TEXT versus public LAST

Date: 2026-10-23. Prepared on HEAD `314a1ce3`, with a separately hashed diagnostic artifact outside the 507 unchanged runtime pins. Status: actual generation PASS; managed FAILED at receiver ACK; sender observer untriggered.

The T088 failure retained shapes but not enough information to distinguish native output from extraction. T089 preserves the strict Unicode/ACK gates, original prompts/model/grants, 75-second phase and 6,144-MiB admission floor. The diagnostic does not settle a turn or supply acceptance text.

## Source and implementation

Pinned v1 upstream commit `53d1eabb61e21162157817bf677da0a4ad3332e3` defines legacy `message`/`part` as well as `session_message`; schema coexistence alone is insufficient evidence of the active writer. The legacy writer chain is `packages/opencode/src/session/session.ts` updateMessage/updatePart → SessionV1 events → `packages/core/src/session/projector.ts` MessageUpdated/PartUpdated handlers inserting MessageTable/PartTable. `packages/schema/src/v1/session.ts` defines assistant role, parentID and integer completion time. [Tables](https://github.com/anomalyco/opencode/blob/53d1eabb61e21162157817bf677da0a4ad3332e3/packages/core/src/session/sql.ts), [writer](https://github.com/anomalyco/opencode/blob/53d1eabb61e21162157817bf677da0a4ad3332e3/packages/core/src/session/projector.ts).

CAOS allocates ownership major1 and explicitly launches with OPENCODE_DB under that receipt-owned root's `data/opencode/opencode.db`. The diagnostic checks the exact receipt and marker without scanning databases, rejects symlinks, and never opens operator state. A successful native observation must independently find the fresh receipt-linked user and its completed assistant, which is stronger evidence than merely seeing the configured path.

The read-only SQLite transaction restricts access to message/part; joins TEXT parts through the exact session and assistant parentID; checks UTF-8 lengths before fetching text (16parts/64KiB total); and selects a unique completed assistant containing the standalone current receipt. Reasoning/tool parts and credential tables are excluded. All typed assistant TEXT is compared as native data, including any synthetic/ignored TEXT; it is not presented as a guarantee of human-only final content. Results contain only shapes/equality/normalization booleans and bounded counts, never native text, paths, nonce or content digest. Multiple native parts are reported separately and do not alone establish rendering loss.

The hook runs once when public sender LAST is nonconforming but current metadata already proves the expected completed turn, with at least3.1s remaining. Visible capture is limited to .25s/64KiB, checks the pinned own socket/server before and after, and the complete observation is limited to3s within the original phase deadline. The PG prerequisite remains `result_verified`: accepting `sent` is unnecessary at this completed-result hook. The strict sender gate still decides acceptance independently. An untriggered or unavailable observer remains missing causal evidence.

## Checks and review

Initial eight synthetic controls produced five failures and one error with the unadapted v2 schema query; two unrelated guards already passed. After the v1 query implementation and additional adversarial cases,19tests pass, including parent/session ambiguity, incomplete or reasoning-only answers, byte/total/part limits, view-schema rejection, credential/write/ATTACH denial, owned receipt/symlink checks, read-only bytes, missing nonce, normalization and strict verified-PG phase. Root also reran60 ACK controls and verified17 unchanged inherited helper ASTs plus the same strict ACK-guard bytes. Composition check passed five contracts with zero broken (412files/1,834dependencies).

Graphify contains the provider/service ownership boundary but no live-journey module nodes; source inspection supplies that missing edge. No product module dependency or API schema changes, so the existing foreign Graphify artifacts are preserved. The registered vault index/hot notes were consulted; these temporary diagnostics do not justify a durable vault write yet.

Independent review is required before actual execution, and fresh same-source generation must pass before the managed candidate is unlocked. Historical T085/T088 receipts remain immutable.


## Actual outcome

Claude's final read review `9415f03c673847b18b1196e7df3920cf` found no safety/correctness blocker in the closed sender comparator. Root retained result_verified as a stricter diagnostic prerequisite because the hook requires completed sender metadata; no acceptance rule was relaxed. Fresh current-source generation passed in13.744s at8,898MiB, native exit0 with exact Unicode and complete process/private cleanup; operator/source/binary preserved. Receipt SHA `980dcbcb24d9352cdaa6ba125118d983929c66857f46e726cbecb9776e303e82`.

Managed executed candidate SHA `60fcfee22928a7000d16366ed4ddbb4b0a3124ff0d11c59a5a015293336d8f98`; closed candidate `d2a816df07377384840ff123b7a1379f3d9991d20f62cf2d2db5991ccac71788`. It FAILED in131.939s with receiver_ack_not_observed. Sender Unicode and completed-turn checks passed this time, so the conditional sender native probe never fired; no T088 sender cause was demonstrated. Receiver223LAST observations were completed sequence1/completed1 and HTTP200, ending with a20-character string that was neither literal ACK nor ACK after whitespace trimming, with no receipt marker. Its native text was not observed in T089.

Minimum admission7,298MiB. The original run completed API/capture/tmux/PostgreSQL/private-state cleanup; all operator/source/binary/runner/HEAD checks passed. Executed script and exact helper bytes were archived separately and the gate reclosed. T089 helper SHA `e8a81f0e66e93f4be80b567023dbc40707a6a79112b222098472174d9c3457c8`; the later T090 parameter extension is a distinct artifact and is not retroactively attributed to this run.

T089's sender causal comparison remains open. T090 extends the already reviewed bounded reader to the receiver result actually failing now; it preserves this failed receipt and all acceptance gates. No native-v1/global acceptance follows from a sender-only pass.
