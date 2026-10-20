# Current registered MCP catalog — 2026-10-21

Runtime inventory: **79 operations**, with optional MCP Apps disabled in this observed catalog. Seven immutable profiles are classified below. Every future/unlisted/plugin name defaults to deny in a selected profile. This table describes surface permission, not a replacement for operation authority.

| Operation | interaction | collaboration | coordination | supervision | knowledge | knowledge_writer | elastic_worker |
| --- | --- | --- | --- | --- | --- | --- | --- |
| answer_user_prompt | deny | deny | deny | deny | deny | deny | deny |
| assign | deny | deny | deny | deny | deny | deny | deny |
| assign_elastic | deny | deny | deny | deny | deny | deny | deny |
| assign_local_cao_task | deny | deny | allow | deny | deny | deny | deny |
| beads_capabilities | deny | deny | deny | deny | deny | deny | deny |
| cancel_local_cao_task | deny | deny | allow | deny | deny | deny | deny |
| cancel_terminal_turn | deny | deny | deny | allow | deny | deny | deny |
| cao_acknowledge_user_answers | allow | allow | allow | allow | allow | allow | allow |
| cao_answer_interaction | deny | deny | deny | deny | deny | deny | deny |
| cao_ask_user | allow | allow | allow | allow | allow | allow | allow |
| cao_escalate_interaction | deny | allow | allow | allow | allow | allow | allow |
| cao_get_interaction | deny | allow | allow | allow | allow | allow | allow |
| cao_list_interactions | deny | allow | allow | allow | allow | allow | allow |
| cao_wait_for_user_answers | allow | allow | allow | allow | allow | allow | allow |
| close_verified_bead | deny | deny | deny | deny | deny | deny | deny |
| complete_assignment | deny | deny | deny | deny | deny | deny | allow |
| create_epic | deny | deny | deny | deny | deny | deny | deny |
| decide_work | deny | deny | deny | deny | deny | deny | deny |
| decompose_beads | deny | deny | deny | deny | deny | deny | deny |
| delete_terminal | deny | deny | deny | deny | deny | deny | deny |
| emit_ui | deny | deny | deny | deny | deny | deny | deny |
| find_profiles | deny | deny | deny | deny | deny | deny | deny |
| get_bead | deny | deny | deny | deny | deny | deny | deny |
| get_epic_status | deny | deny | deny | deny | deny | deny | deny |
| get_handoff_result | deny | deny | deny | deny | deny | deny | deny |
| get_local_cao_task | deny | deny | allow | deny | deny | deny | deny |
| get_ready_beads | deny | deny | deny | deny | deny | deny | deny |
| get_work_events | deny | deny | deny | deny | deny | deny | deny |
| get_work_item | deny | deny | deny | deny | deny | deny | deny |
| handoff | deny | deny | deny | deny | deny | deny | deny |
| inspect_bead_assignment | deny | deny | deny | deny | deny | deny | deny |
| inspect_beads_operation | deny | deny | deny | deny | deny | deny | deny |
| inspect_terminal_turn | deny | deny | deny | allow | deny | deny | deny |
| join_native_child | deny | deny | deny | deny | deny | deny | deny |
| knowledge_instructions | deny | deny | deny | deny | deny | deny | deny |
| knowledge_read | deny | deny | deny | deny | deny | deny | deny |
| list_beads | deny | deny | deny | deny | deny | deny | deny |
| list_local_cao_peers | deny | deny | allow | deny | deny | deny | deny |
| list_native_children | deny | deny | deny | deny | deny | deny | deny |
| list_outcomes | deny | deny | deny | deny | deny | deny | deny |
| list_siblings | deny | allow | allow | allow | allow | allow | allow |
| load_skill | deny | deny | deny | deny | allow | allow | deny |
| memory_forget | deny | deny | deny | deny | deny | deny | deny |
| memory_recall | deny | deny | deny | deny | allow | allow | deny |
| memory_store | deny | deny | deny | deny | deny | allow | deny |
| mutate_bead | deny | deny | deny | deny | deny | deny | deny |
| prepare_bead_assignment | deny | deny | deny | deny | deny | deny | deny |
| ralph_complete | deny | deny | deny | deny | deny | deny | deny |
| ralph_feedback | deny | deny | deny | deny | deny | deny | deny |
| ralph_start | deny | deny | deny | deny | deny | deny | deny |
| ralph_status | deny | deny | deny | deny | deny | deny | deny |
| ralph_stop | deny | deny | deny | deny | deny | deny | deny |
| report_outcome | deny | deny | deny | deny | deny | allow | deny |
| revoke_work_decision | deny | deny | deny | deny | deny | deny | deny |
| send_message | deny | allow | allow | allow | allow | allow | allow |
| start_bead_assignment | deny | deny | deny | deny | deny | deny | deny |
| store_lesson | deny | deny | deny | deny | deny | deny | deny |
| unassign_bead | deny | deny | deny | deny | deny | deny | deny |
| update_metadata | deny | deny | deny | deny | deny | deny | deny |
| verify_terminal_turn | deny | deny | deny | allow | deny | deny | deny |
| work_launch | deny | deny | deny | deny | deny | deny | deny |
| workflow_cancel | deny | deny | deny | deny | deny | deny | deny |
| workflow_create | deny | deny | deny | deny | deny | deny | deny |
| workflow_events | deny | deny | deny | deny | deny | deny | deny |
| workflow_get | deny | deny | deny | deny | deny | deny | deny |
| workflow_list | deny | deny | deny | deny | deny | deny | deny |
| workflow_plan_approval | deny | deny | deny | deny | deny | deny | deny |
| workflow_prepare | deny | deny | deny | deny | deny | deny | deny |
| workflow_result | deny | deny | deny | deny | deny | deny | deny |
| workflow_resume | deny | deny | deny | deny | deny | deny | deny |
| workflow_return | deny | deny | deny | deny | deny | deny | deny |
| workflow_review | deny | deny | deny | deny | deny | deny | deny |
| workflow_run | deny | deny | deny | deny | deny | deny | deny |
| workflow_seeds | deny | deny | deny | deny | deny | deny | deny |
| workflow_start | deny | deny | deny | deny | deny | deny | deny |
| workflow_status | deny | deny | deny | deny | deny | deny | deny |
| workflow_update | deny | deny | deny | deny | deny | deny | deny |
| workflow_validate | deny | deny | deny | deny | deny | deny | deny |
| workflow_wait | deny | deny | deny | deny | deny | deny | deny |

## Operation authority and audit limits

- Interaction and recovery retain the existing terminal/capability/group authority; human-answer operations remain denied by every profile.
- Generic messaging/discovery retain their existing terminal/session/API checks. This inventory does not establish the separate external `cao.work.message` contract or current-source persistence/restart acceptance.
- Coordination exposes only the four local peer operations. Their shared helper still checks `_tool_denied_reason("assign", local_only=True)`, a resolved calling terminal and a project working directory; API pairing/task ownership remain independent. General `assign`, `handoff` and `assign_elastic` remain denied.
- Supervision additionally checks authenticated current target metadata for self or direct assigned-child ownership before inspection/action dispatch. Existing server-selector permission, generation fences and downstream Work ownership checks remain. Unbound operator behavior is preserved.
- Knowledge exposes `load_skill` and `memory_recall`; only `knowledge_writer` adds `memory_store` and `report_outcome`. Existing scope/context, memory-disabled and learning-disabled service behavior remains. Neither profile exposes memory deletion, lessons, Beads/Ralph/Work/workflow mutation or generic UI/terminal deletion.
- `complete_assignment` is exposed only by `elastic_worker`; it still requires configured worker/broker/release context and successful delivery before release. Catalog selection alone cannot configure a worker.

Actual catalog/direct-call policy and transport tests cover every registered name and every expanded profile. Negative authority tests cover target identity/ownership, coarse denial ordering, local project context and unconfigured workers. No permitted real operation was dispatched during this inventory; live provider, PostgreSQL and concurrency acceptance remain separate.

Private `t036-catalog-audit.json` records each operation’s source/line, observed guard/delegation calls and profile decisions. Guard-call discovery is source evidence, not an automatic proof of transitive backend authorization. [T036 validation](validation-t036.md) records executable coverage and remaining limits.
