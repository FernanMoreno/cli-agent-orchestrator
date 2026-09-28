---
type: "explain"
date: "2026-09-22T18:12:48.283365+00:00"
question: "Explain WorkAdmission"
contributor: "graphify"
outcome: "useful"
source_nodes: ["WorkAdmission", "WorkRepository", "WorkAuthority", "WorkScheduler", "WorkReservations"]
---

# Q: Explain WorkAdmission

## Answer

Refresh verification: exact graph label WorkAdmission resolves to src/cli_agent_orchestrator/services/work_admission.py:L65, community Work Admission Dispatch, degree 29. Extracted methods include dispatch_next and _commit_dispatch. Graph uses relationships to WorkRepository, WorkAuthority, WorkScheduler and WorkReservations are tagged INFERRED; constructor references were independently checked at source lines 66 and 74-77. This verifies graph coverage, not universal activation of the pending roadmap.

## Outcome

- Signal: useful

## Source Nodes

- WorkAdmission
- WorkRepository
- WorkAuthority
- WorkScheduler
- WorkReservations