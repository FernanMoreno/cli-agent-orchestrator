---
type: "query"
date: "2026-09-27T07:55:42.181476+00:00"
question: "Trace T097 from WorkAdmission to launch_bound_work_static_elf"
contributor: "graphify"
outcome: "dead_end"
source_nodes: ["WorkAdmission", "launch_bound_work_static_elf"]
---

# Q: Trace T097 from WorkAdmission to launch_bound_work_static_elf

## Answer

Expanded from original query via graph vocabulary: [work, admission, executable, contract, identity, static, command, process, backend, content, stage, registry]. The directed graph has no path between WorkAdmission and launch_bound_work_static_elf; the matching BFS was too broad (719 nodes). Verified against source: launch_bound_work_static_elf is defined and used only by security tests, with no product caller. WorkAdmission._preflight still receives ProcessRestrictionContract and the Bubblewrap backend remains unregistered and fail-closed. This graph result supports an integration gap but does not decide whether contract evolution is required.

## Outcome

- Signal: dead_end

## Source Nodes

- WorkAdmission
- launch_bound_work_static_elf