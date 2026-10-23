# T098 — Native JSON row limits measured in bytes

Date: 2026-10-23. Scope: private native-v1 diagnostic fixture only.

Independent Luna review reproduced a metadata row above1MiB in UTF-8 bytes being accepted because SQLite `length(TEXT)` counts characters. The declared1MiB row bound therefore did not match the implemented limit for multibyte JSON. The separate agent-string limit already used BLOB length correctly.

Three new synthetic regressions failed before correction: CJK padding in user metadata, completed-assistant metadata and the TEXT-part JSON container. Each container has fewer than1048576 characters but more than1048576 UTF-8 bytes. All JSON row guards now use `length(CAST(... AS BLOB))<=1048576`, keeping the same limit and existing text/part/identity/time guards. The helper also requires UTF-8 SQLite encoding before measuring rows: a UTF-16 synthetic database first passed incorrectly, then failed closed after the encoding guard was added. This prevents database-encoding bytes from being mistaken for UTF-8 evidence. Oversized rows are excluded from the receipt-bound selection; no model input/output is changed.

The final31-test synthetic selection passes, including the three new byte-cap regressions,64-byte accepted/65-byte rejected agent metadata, multibyte agent overflow, and mismatched-agent diagnostic minimization. T097's64 adversarial binding/timing cases and three timing scenarios also pass. No private/operator database or real model is used.

Composition check:412files/1834dependencies, five Import Linter contracts kept, zero broken. Composition review covers SQLite query bounds, own-root/receipt identity and diagnostic serialization. No production module, API, schema, grants, message contract, cancellation, transaction or cleanup behavior changes. Verdict: PASS for these offline diagnostic corrections; broader native acceptance remains pending. Existing authorizer tests establish configured denies; this change adds no claim of exhaustive authorizer instrumentation.

Independent review: Luna verified both reproduced defects and their corrections; Claude read review `326910c97a124064b11703bd7e148fa9` found no blocker after reviewing the encoding guard, all31 tests and the historical/current scope of these documents. Lead independently ran31 tests successfully, style checks and composition5/0; `src/`, `test/`, `web/` and frontend sources are unchanged.
