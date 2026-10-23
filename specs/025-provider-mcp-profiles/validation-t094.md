# T094 — Current-source native v1, extraction fidelity and non-ACK

Date: 2026-10-23. Source: f16e7ff6682d41cd16b81fd2a11d25a4453002c6. Status: generation PASS; managed acceptance FAILED.

The isolated generation completed in 13.139s with 9479MiB available. Unicode, event/HTTP identity, ledger, source/binary/runner pins, owned cleanup and operator preservation passed. Its immutable private receipt SHA256 is `34f0aab8d3b269a0b7384516b37f1e41958ab0917b7699b6cdd794dd63bc117f`.

The managed run lasted126.853s including preparation/cleanup and failed in the first75s message phase with `receiver_ack_not_observed`. The sender completed with the exact Unicode marker, confirming that T092's sender regression did not recur. The receiver's receipt-linked native completed assistant contained one155-character/3-line TEXT part, including its receipt. After removing only that active protocol receipt, native public text and API LAST were identical:104characters/1line. Neither was ACK, including NFC/NFKC/compact comparisons. The bounded read-only comparison took17.3ms total and retained no raw reply. Native actor profile metadata was not captured in this case; native response nonconformance does not establish why the reply differed from the requested ACK.

This verifies extraction fidelity for the observed reply; it does not pass ACK, the second message, restart, or global acceptance. Do not map the reply to ACK or retrospectively change this receipt. T095 adds actual profile identity/prompt binding; T096 uses a separately pinned canonical explicit profile input already shared with v2. Changing inputs cannot prove the cause of this historical failure.

Minimum admission7503MiB exceeded6144MiB. Original API/capture/tmux/PostgreSQL/private-root cleanup and operator/source/binary/runner/HEAD preservation all passed. Both execution gates were closed and executed bytes archived. No operator login was modified and no historical job was reexecuted.

Availability on resumption: the private T094/T096 directories were no longer present at their former cache paths. The public sanitization and historical hashes remain; the original private receipt is not currently available for a fresh integrity check or gate reuse. See [T096](validation-t096.md).
