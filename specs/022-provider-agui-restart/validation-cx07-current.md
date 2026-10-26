# CX-07 · in-flight HTTP wait recovery · 2026-10-27

**PASS real Codex on418a98536155d7c008c858a3f01e63061a14689d**,50.03s,
exit0, CLI0.164.0/requested gpt-6-luna. [Scoped receipt](validation-cx07-current.json).

Exactly one native MCP wait invocation entered the original worker and had no
successful return when the controller killed/reaped that owned Popen. The same
invocation reached the replacement for the same group with its HTTP wait reduced
from19 to3 seconds. There are exactly two private wait-entry records with
different worker PIDs and one ready return. Both exact receipts were acknowledged
in one ACK; two deliveries are agent_acknowledged. One API restart and three
real AG-UI snapshots passed identity/redaction checks.

All1510source pins, binary/operator auth bytes and metadata, exact model command
and owned container/network absence passed. The execution descriptor was archived
and reclosed by a flag-only change. This closes CX-07 and T172, with production
routes and lifespan off. It does not close another provider, negative catalog/
authorization, Unicode/cancellation, messages or global acceptance.

The earlier CX-06 acceptance remains tied to9d5ba9f8. It did not prove an
interrupted HTTP request. The new case is default-off, Codex-only and requires
`--agui-restart --restart-during-wait`. It leaves the prompt/model,75-second
provider budget,20-second requested wait and45-second readiness limit intact.

The worker records private group/PID/duration entry before awaiting. The
controller verifies one native wait in progress and no return, takes the original
pending AG-UI snapshot, rechecks, kills/reaps only its Popen child and rejects a
return racing with the crash. The replacement must receive the same group's
wait with a smaller remaining budget. Exactly one native wait invocation must
return ready before the original exact-receipt ACK and final snapshot gates pass.
The post-restart snapshot still precedes human answers; criteria are unchanged.

Validation:

- Two observer privacy/ordering RED tests failed before implementation.
- First model-free run:40PASS54.49s. Real PostgreSQL, production API process and
  sockets; the interrupted request produced an actual ConnectionError before
  replacement. The same in-process MCP call recovered both answers and ACKed
  their exact receipts, with no duplicate rows or delivery/ACK events.
- Final regression:143PASS,5warnings,104.85s; owner total114.069s. Adds two-PID,
  decreasing-budget and one-ready-return checks; includes T171, shared HTTP/MCP,
  replay/reconstruction, both live-journey modules and the real process case.
  Borrowers/databases zero and exact owned container absent before completion.
- Architecture:5 contracts kept,0 broken. Black/isort and scoped diff checks pass.
- Claude source review found no blocking correctness/privacy/cleanup issues.
- Private native acceptance adds the three exact boolean predicates to the
  original controller.1 synthetic positive/12 negative controls pass without
  models/services. Its scoped observer preserves only validated private worker
  observations before cleanup; source/binary/operator and PG ownership guards
  remain unchanged. The source-bound native execution above then passed.

The model-free MCP caller is a test driver. Its ConnectionError witness remains
separate from the subsequent native same-invocation/PID-entry proof. Neither
establishes another provider's recovery, full application lifespan or global gates.
