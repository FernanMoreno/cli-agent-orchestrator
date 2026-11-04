# Persistent admission policy control (T022; validated opt-in)

The deployment selector `CAO_ADMISSION_POLICY_STORE` is `file` by default; `postgres` selects versioned policy in the main PostgreSQL store. It does not grant resources. Trusted composition resolves the base profile UUID from the configured profile home. Different profile homes intentionally have different identities; API, Bridge and Root must share the intended profile and main store to share policy. Request DTOs cannot select either identity or database target. Process-specific loader scope IDs govern file retention only.

| Operation | Request | Result |
| --- | --- | --- |
| GET `/admission/policy` | none | persisted record or null; separately applied revision or null |
| PUT `/admission/policy` | expected_revision + policy | appended active record |
| POST `/admission/policy/revoke` | expected_revision | appended inactive record, preserving prior values |
| POST `/admission/policy/seed` | expected_revision=0 | explicit seed from configured private file; no path input |
| GET `/admission/policy/history` | limit 1..100 (default50), optional before_revision | descending keyset entries + next_before_revision |

All operations require a verified JWT administrator and emit no-store. DTO validation is strict, unknown fields rejected; fixed errors do not echo input, paths or storage exceptions. Conflicts409, absent revocation404, invalid DTO422, unavailable control503. Mutations have no automatic retry after uncertain commits. Runtime SQL is SELECT-only; owner writes verify configured namespace/table ownership and CAS under a per-profile transaction lock. Schema history is an independent component; previous component migrations remain unchanged.

The normalized policy has exactly nine settings: memory_per_delivery_bytes, cpu_per_delivery_millicores, memory_reserve_percent, cpu_reserve_percent, max_sample_age_s, postgres_reserve_percent, postgres_statement_timeout_ms, sample_interval_s, healthy_samples_before_growth. Percentages range0..99; integer quantities and durations are positive; durations finite and sample interval shorter than freshness. SQL integer quantities use signed64-bit representation. No aggregate worker ceiling is added. PUT replaces the complete normalized policy; omitted values use documented domain defaults, never inherit prior settings.

Records include canonical profile UUID, positive durable revision, active, normalized policy, hashed actor principal and aware database timestamp. Persisted does not mean applied. The monitor reports an applied DB revision only after publishing a valid observation under it and the current generation; lifecycle invalidation clears that claim. Source/read failure, missing or revoked DB policy disables admission without file fallback. Changes during sampling invalidate publication, including a revision change with unchanged values. Cross-process changes are observed on the next read/recheck; there is no synchronous distributed revocation fence or NOTIFY claim. Existing sample freshness bounds still apply.

Root pre-SQL startup uses an explicitly separate private bootstrap file and independent revision cache, after signed activation verification. Once funded SQL exists, the monitor reads the current policy through its existing original control session. Bootstrap never counts as a persisted/applied DB revision. A refused bound session remains an authority failure. Runtime API startup may remain unknown with no DB row so an administrator can create revision1.

`AdmissionPolicyClient` uses caller-owned HTTP transport and token supplier, the same DTOs, fixed typed errors, and no credential/file management. Productive resource authority, model quotas, Root issuer funding, physical M1/M2/M3 and Studio UI acceptance remain separate.

A failed mutation still invalidates the local published generation until the next valid cycle. Only pure owner-target configuration rejection before SQL, or a pre-yield schema refusal with positively closed native prefix handles, is a proven non-write refusal. Unreturned opens, uncertain closes/commits and other unproven failures retain the generic uncertainty latch; reads may inspect state but never reset it or retry a mutation automatically. Bootstrap validation proves the signed envelope and current-policy rechecks; it does not attest published capacity.
