# T225 — native structured completion observation

**OBSERVED ONLY: no runtime completion acceptance.** [Structured receipt](validation-t225.json). T223 remains open.

The actual pinned Claude2.1.303 CLI delivered SessionStart/Stop JSON through observers installed only in the diagnostic private HOME. No model was asked to emit JSON. The57-tick native response produced1204 bytes of complete assistant text in Stop.last_assistant_message, matching the active receipt and the SessionStart session identity. The unchanged API still returned202/pending: this directly isolates the lost terminal assistant-head boundary.

The inspection passed84.34s under the original180s native/6144MiB admission/5s progress budgets. One primary request offered0 built-ins. Every observed process/wrapper/tmux/home retired; the same held PostgreSQL returned to0borrower connections/0private databases.1884 source pins, the exact executed source archive and native hook payloads remain private.

The observer caps input at1MiB, rejects duplicate/nonfinite/malformed JSON and wrong event/session/Stop types, publishes exclusive atomic0600 records in an owned0700 directory and emits no stdout. SessionStart is required before any user input. Namespace interpreter guards and early StopFailure reporting avoid opaque timeouts.127 controls pass, including private settings/mount preflight, UTF-8/ANSI byte witnesses and existing cancel contracts. The actual callback interpreter was exercised with two closed packets; architecture5/0 and independent read review pass.

This packet is observational evidence, not an authorized task outcome. It does not write PostgreSQL, alter the normal provider verifier, accept a visible nonce, or close Claude cancellation. A separately specified runtime integration must bind a fresh CLI run, native session, exact active generation/receipt, terminal-state witness and the existing durable CAS; negative echo/stale/foreign/interrupted cases remain required. Existing failed runs stay failed. Native MCP/profile/backend/global-capacity acceptance is not inferred.

Native schema reference: [official Claude hook documentation](https://code.claude.com/docs/en/hooks#stop). The actual inspection, not documentation alone, establishes the pinned payload fields recorded in the JSON receipt.
