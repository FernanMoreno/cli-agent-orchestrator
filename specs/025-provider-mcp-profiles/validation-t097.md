# T097 — Fail-closed native profile evidence

Date: 2026-10-23. Scope: diagnostic fixture only; no provider runtime or native output change.

The additional T095 profile predicate checked only `status=observed` and `native_agent_matches=True`. A pure reproduction returned true with explicitly false active-receipt, own-root, three-second and original-deadline witnesses. The observer could also finish after its budget while retaining useful observed shapes; those shapes must not qualify as timely acceptance evidence.

A new adversarial regression initially failed64 subcases: missing, false, null and integer-one values for each of four witnesses, on either role in either phase. Three synthetic timing scenarios also failed before correction. The predicate now requires literal true for all four witnesses plus the matching native agent in every diagnostic. The observer reports elapsed time and both timing flags from one finishing timestamp. Late shapes remain available for diagnosis while profile acceptance rejects them.

The corrected fixture initially passed27 synthetic SQLite tests; the final selection after T098 passes31 tests, including all64 witness subcases and finishing before/after each deadline. Existing native/API shape, wrong actor/parent/sibling, receipt, size/type, readonly/privacy and stronger-gate controls remain. Import Linter passed five contracts with zero broken (412files/1834dependencies). Tests use synthetic state only; no native CLI, model, operator credentials or PostgreSQL is needed.

Composition review: only the diagnostic observer→profile-predicate boundary changes. Its neighbors are own-root/receipt identity and runner timing evidence. No API, permission, database schema, message, migration, retry, cancellation or resource lifecycle changes. Original ACK/Unicode/native receipt/restart/cleanup predicates are untouched. Graphify paths for the provider/configuration surface were inspected and source-verified; the new diagnostic fixture is not in the generated graph. Foreign graph artifacts are preserved; no structural refresh is warranted.

Native acceptance remains FAILED/pending as recorded in T094/T096. This correction does not explain either native reply or supply the missing receiver observation. Private historical receipts missing on resumption are not recreated or used as current gates. Only public sanitized historical evidence is available for those runs.

Independent review: Luna verified both reproduced defects and their corrections; Claude read review `326910c97a124064b11703bd7e148fa9` found no blocker after reviewing the encoding guard, all31 tests and the historical/current scope of these documents. Lead independently ran31 tests successfully, style checks and composition5/0; `src/`, `test/`, `web/` and frontend sources are unchanged.
