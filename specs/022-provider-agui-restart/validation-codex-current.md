# Codex current interaction diagnostic · 2026-10-26

## Current scoped acceptance · 2026-10-27

**PASS on9d5ba9f80d25ef2ef91661fe65806014911d9663** after T171.
Codex CLI0.164.0, requested gpt-6-luna, completed in50.23s, exit0.
Two questions/two human answers, one ready wait, one ACK containing both exact
returned question/version/receipt identities, one API restart and three real
AG-UI snapshots. Both deliveries are agent_acknowledged. All1509source pins,
binary/operator auth byte/stat preservation and exact owned container/network
absence passed. The executed descriptor was archived and reclosed flag-only.
[Machine-readable scoped receipt](validation-codex-current.json).

This closes CX-06, with production routes and lifespan off. It does not close
full application startup, negative native catalog/authorization, messages,
Unicode/cancellation, CX-07 or global acceptance. The native receipt does not
count internal reconnections or prove interruption of an in-flight HTTP wait.
An external read-only0.25s sampler observed non-200/failed health-query windows
of0.677s and15.866s, and the actual native wait argument20. These are sampled
health availability, not exact socket-downtime measurements or proof of which
reconnect branches executed. The failed runs below retain their own sources.

## Preserved first failure

The real native run on `74a2ca9b910a1d574f09f78ba11f06699e4d110d`
failed acceptance. Codex CLI 0.164.0, requested `gpt-6-luna`, exited zero in
49.18 seconds. Two questions and two human answers persisted; one controlled
API worker restart and three real AG-UI snapshots preserved identity and
redaction. All seven waits returned `unavailable`; no ACK was attempted.
The final delivery states were empty. Exit zero does not establish acceptance.

The private controller verified all 1508 source pins, binary, operator auth
bytes/metadata, model command, exact owned container/network absence and
cleanup. The executed descriptor was archived and reclosed by a flag-only
change. A separate interpreter admission rejection occurred before PostgreSQL
or model launch; its failed receipt is retained independently.

The original sanitized receipt omitted the fixed error detail that distinguishes
HTTP errors, connection failures and unexpected exceptions. Its raw transcript
was removed by the successful cleanup. Seven `unavailable` results therefore
do not identify the cause. Zero successful wait observations also does not
prove the service was never entered.

Two serial model-free diagnostics on the same source used real PostgreSQL:

- Original, freshly reconstructed and observed services returned `pending`,
  then the observed fresh service returned `ready` after the human answer.
- The production API worker returned HTTP 200 `ready` before and after a real
  process restart, with the same persisted group and capability.

Both owners closed; exact container absence was independently checked. These
diagnostics exclude neither an in-flight transport interruption nor a timing
failure in the original native run. They are not substitute native acceptance.

T083–T085 add only a closed diagnostic enum to the existing sanitized MCP
receipt. They preserve error semantics, deadlines, retries and all acceptance
predicates. The RED run produced 13 missing-field failures; the intermediate
run passed 86 tests and exposed one existing exact-output expectation requiring
the new nullable field. Final verification: 88 offline tests passed in30.08s,
including production mapper → MCP wire → provider event → final receipt privacy
contracts; composition kept all5 architecture contracts. Black/isort and diff
checks passed; independent Claude review returned GO. No global gate or full application-lifespan acceptance
is claimed; the controlled API worker uses production routes with lifespan off.

## Source-bound retry on7079a7a8

The actual retry failed in45.5s, Codex exit0. Its three wait calls all classified
as `connection_error`, confirming the MCP caller could not connect to the API.
Two questions and human answers persisted; one restart and three AG-UI snapshots
preserved identity/redaction. No ACK was called. This establishes a transport
failure, not an HTTP503/service exception. The exact unreachable-window duration
and actual wait argument were not retained, so they remain unproven.

The owner verified all1508source pins, binary, operator auth bytes/metadata,
model command, cleanup and exact own container/network absence. The executed
descriptor is archived and the execution copy was reclosed by a flag-only
change. T171 inSpec025 addresses bounded explicit-wait reconnection, keeping
native acceptance open until a new source-bound run meets every predicate.
