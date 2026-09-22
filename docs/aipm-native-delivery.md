# AIPM native-delivery hardening

This fork carries the CAO-side portion of a governed native-delegation
integration. It is based on upstream commit
`c282ce52752bbeb3dcd78927d0cc06b9f0498461` and preserves the normal CAO
defaults for every caller.

## What changes in CAO

An API caller can set `prompt_redelivery: false` for deferred initial delivery
or `run-step`. CAO then sends the prompt once and retains an unconfirmed
terminal for the caller to reconcile; it never silently pastes the same work a
second time. The choice is part of the idempotency fingerprint.

The patch also carries the caller terminal ID through session creation, records
an explicit post-send in-flight status boundary for synchronous steps, and
uses rendered terminal history for shell readiness. These changes prevent a
stale ready/idle screen from being mistaken for the result of a new request.

For OpenCode, startup uses a provider-approved visible TUI frame and a
plain-shell launch path. The tmux and herdr backends expose that plain-shell
transport contract, and tmux retries brief session/window discovery races
before treating an otherwise healthy child as missing.

## Intentional boundary

This repository does not contain the AIPM control plane. Its authorization
ledger, provider allowlist, native-child tree limits, durable sibling mailbox,
batch wait barrier, cleanup/reconciliation policy, and web console are kept in
the separate `aipm_cao` integration. CAO remains a general orchestrator; the
integration is responsible for deciding which callers are allowed to opt into
the stricter delivery mode.

## Provider discovery and operation preflight

`GET /agents/providers` returns the existing `name`, `binary`, and `installed`
fields plus an additive `capabilities` object for every production adapter
registered in this CAO checkout. The object describes only operations of that
adapter, such as `native_children`, `sibling_messages`, terminal status probes,
and `durable_turn_receipts` when a provider needs receipt-verified completion.

It deliberately does **not** include `compatible_with`, `incompatible_with`,
or any other provider-pair routing field. AIPM (or another caller) may
preflight the operation it is about to request, but its job allowlist remains
the authority for which selected providers can collaborate. Write coordination
continues to come from task/resource leases, not a claim that two CLI brands
are inherently incompatible.

The accompanying unit tests cover the changed OpenCode readiness path, tmux
shell readiness, and propagation of the no-redelivery option into a synchronous
agent step.
