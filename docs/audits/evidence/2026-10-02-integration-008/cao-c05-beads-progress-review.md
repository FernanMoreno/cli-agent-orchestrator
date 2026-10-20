# Independent final Beads progress delta review

Verdict: PASS for the reviewed owner-scoped progress and verified-close delta on current source. No product edits by reviewer. Root corrected two independently reproduced Important issues before this verdict.

## Concrete boundary findings and disposition

1. Rebinding configured workspace alias `repo` to a different root/revision/device/inode with identical task ID/content incorrectly credited old accepted Work and exposed its attempts as progress for the new task. Actual SQLite/private scoped Work/artifact proof reproduced true instead of false in `test_same_workspace_alias_rebind_cannot_credit_old_task_scope`; `/tmp/cao-c05-beads-progress-review-current.log`. Root fixed `services/beads_assignment_service.py:128`–144 to compare durable workspace_identity before any assignment/attempt publication. Current independent case passes and asserts no assignment or attempt metadata.

2. The same rebind let `api/beads_routes.py:233` close the new repository's task using the old historical accepted Work. Actual route, shared SQLite metadata journal, accepted private result and fake external adapter reproduced no refusal, `/tmp/cao-c05-beads-close-valid-review.log`. Root now passes actual owner binding's workspace identity and frozen task DTO privately to `services/beads_service.py:194`; workspace mismatch refuses before journal claim/CLI, task content mismatch refuses before claim even with current valid CAS hash. New independent tests prove both 409 and zero external effects. Historical binding GET retains its separate owner-scoped meaning.

Root also corrected externally closed status semantics: accepted Work is retained when the actual frozen task content is unchanged. External status/material hash/current Work projection flag alone do not represent new requirements; task title/description/priority/labels/dependencies/etc still compare. Pending preparation/admission retains full material CAS and does not launch closed tasks.

## Evidence

Owned test-only file: `test/api/test_integration_008_beads_work_review.py` (existing 7 boundary cases plus 6 delta cases).

- Final requested complete current gate: `.venv/bin/python -m pytest test/api/test_integration_008_beads_work_review.py --no-cov -p no:cacheprovider -q`: **13 passed**, 19.13s; `/tmp/cao-c05-beads-progress-review-complete13.log`. Earlier current gate: 12 passed in16.48s before adding the final edited-task close case (`/tmp/cao-c05-beads-progress-review-final.log`).
- `.venv/bin/python -m pytest test/api/test_integration_008_beads_work_review.py -k verified_close_refuses_edited --no-cov -p no:cacheprovider -q`: **1 passed**, 12 deselected, 6.05s; `/tmp/cao-c05-beads-edited-close-review-final.log`. Same frozen product source; final combined 13-case run above also passes.
- `project-composition-check "$(cat .ai/project-name)"`: **5 contracts kept, 0 broken**, `/tmp/cao-c05-beads-progress-review-composition.log`.
- Black formatted owned reviewer file; scoped product `git diff --check` clean.

New delta cases prove authentic owner READ sees actual accepted Work and its terminal/attempt mapping while an authentic foreign READ owner sees none; projection leaves the durable binding unchanged; body drift removes credit; normal verified external close retains accepted Work and unchanged current response DTO hash; supported 257-item actual API metadata list remains usable (source cap4096 agrees with bd's bounded list); workspace rebind hides old mapping and refuses close; title edit with fresh current hash refuses close before adapter effect.

The previously delivered configured actual RSA/JWT READ/WRITE and foreign-owner API checks remain included in the final 13-case gate. `describe_tasks` independently rejects unverified principals and filters binding owner in SQLite. API annotation is limited to verified caller or actual loopback socket trusted local principal; no forwarded-header identity substitution. The private expected identity/content fields are not present in the extra-forbid public close request schema.

## Source composition review

`describe_tasks` uses historical coordinator status without calling the mutating assignment status/complete path; attempts are bounded to 64, the 65th flags truncation, and UI describes the first 64 rather than claiming exhaustive/latest history. `task_dto` hash remains the canonical current external DTO hash; presentation fields do not feed a second assignment engine or grant authority. Epic external-closed counts and actual accepted-Work counts remain separate. UI run/terminal display introduces no write capability; existing fleet epoch/unmount reset owns late-response cleanup, reviewed in prior Beads UI tests.

No real external bd/provider was launched. Tests use actual SQLite/origin/private scoped approvals/admission/artifacts/coordinator proofs; only the external task CLI adapter is fake. Initial close reviewer async fixture nesting and a missing adapter return were invalid fixture failures, explicitly corrected and not counted as product RED evidence. The projection cap increase landed before the 257-case execution, so that case is preservation proof, not claimed RED-to-GREEN.
