# Peer-key authentication convergence — 2026-10-07

The pre-T078 Python 3.11 matrix exposed a real signature boundary: a regression
registered an all-zero Ed25519 public key and sent an all-zero signature. The
API occasionally advanced past signature authentication and returned project
mismatch (400), rather than signature rejection (403). Two focused invocations
passed and the third reproduced the failure. The original full attempts are
retained as interrupted diagnostic history, not final acceptance.

## Reproduction and scope

Actual `verify_peer_signature` probes with 1,000 different nonces accepted
250/269/260/264/259 all-zero-key signatures on Python 3.10/3.11/3.12/3.13/3.14.
Independent review also reproduced identity-point public-key forgery in 100/100
requests. Generated valid keys rejected wrong-key signatures and accepted
correctly signed requests. The gap therefore concerned admitted weak public
encodings, including imported/persisted grants; honest generated CAO keys
remain compatible. A fixture-only repair would leave this demonstrated gap.

Before production changes, the new regression file produced **21 failed,
2 passed**: weak-key admission, forged verification and an existing persisted
weak-key grant all failed their denial assertions. The passing controls used
real generated keys with both public sign bits.

## Narrow correction

`peer_public_key_bytes` is shared by pairing admission and signature
verification. It masks only the public sign bit for comparison, rejects
noncanonical Y encodings and the seven published masked-Y small-order/alias
values, then returns the original bytes to cryptography. Existing weak grants
therefore fail closed without a schema migration. No new dependency or
signature primitive was introduced. The published reference is
[libsodium 1.0.20-RELEASE](https://github.com/jedisct1/libsodium/blob/1.0.20-RELEASE/src/libsodium/crypto_core/ed25519/ref10/ed25519_ref10.c#L998);
[point-validation documentation](https://doc.libsodium.org/advanced/point-arithmetic)
distinguishes full subgroup validation. This encoding guard does **not** claim
full subgroup/on-curve validation; cryptography still verifies signatures.

The separate API invalid-signature regression pins a real generated public
key and signs the exact request material using a different generated key. Its
403/scope_denied assertions remain unchanged.

## Executable checks and review

- New weak-key tests plus existing signature-auth tests: **28 passed in 2.38 s**.
- API scopes/routes, authentication, registry, recovery and lifecycle: **179
  passed in 13.54 s**; the default selection deselected the integration case.
- That two-process integration case was then explicitly selected with
  `-m 'not e2e'`: **1 passed in 32.32 s**, exercising valid paired traffic.
- Independent reviewer reran the **23 new tests successfully** and found no
  blocker in admission/verifier/error handling/import direction.
- Post-fix probes on **each Python 3.10–3.14**: all-zero-key signatures
  **0/1,000** accepted; wrong-key signatures **0/1,000** accepted; correctly
  signed generated-key requests **1,000/1,000** accepted.
- Staged Git secret scanning after these source changes passed with zero leaks.

Graphify located the verifier and peer/auth consumers before the change;
source inspection verified enrollment, grant persistence, request verification
and process-registry paths. The import direction already exists and no
cryptographic key file is accessed merely by importing the shared helper.
The final frozen full matrix and joint coverage gate remain separately
required in [matrix evidence](final-python-matrix-evidence.md).

Private logs and probes remain under `/home/felni/cao015-final/` and
`/home/felni/cao015-final-python/`; no credentials or raw provider logs are
published.
