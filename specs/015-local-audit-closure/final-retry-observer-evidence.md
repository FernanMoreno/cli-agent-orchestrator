# Retry transaction observation — 2026-10-07

The T080 owner closure exposed an invalid assumption in the existing durable
retry-authorization test: after the journal call returns, it reads
`in_transaction` from the retained, correctly closed connection. The original
full suites fail there after all authorization-rejection and unchanged-state
assertions pass. The exact case reproduced the ProgrammingError on native
Python 3.12 (one failure, 1.74 seconds).

The test now observes actual `in_transaction` at the owner boundary after the
original inner transaction exits and before the original outer close runs.
Its scoped context manager delegates the saved real closing context; no
connection state or transaction behavior is mocked. The authorization reader
still requires an active transaction; durable-record mismatch still raises
its original ValueError; the failed state and attempt counter remain unchanged.
The observed transaction must have ended, then querying the retained handle
must raise the closed-connection ProgrammingError. Production is unchanged.

All 45 journal/projection neighbors passed on Python 3.12 (5.70 seconds) and
Python 3.14 (7.02 seconds). Independent review found no blocker and reran the
exact final case on Python 3.12 (one pass, 1.22 seconds). The original journal
transaction, ownership and retry authorization contracts remain covered.
Final whole-matrix acceptance is pending.
