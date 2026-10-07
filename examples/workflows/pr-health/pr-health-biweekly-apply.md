---
name: pr-health-biweekly-apply
schedule: "0 9 * * 0"
agent_profile: developer
provider: codex
script: ./pr_health_biweekly_guard.py
---

> **COMMENT-ONLY APPLY REQUEST.** Registration does not authorize execution or
> future GitHub writes. Existing operator Work authority and the exact prepared
> plan must authorize this run.

Comments are the only authorized GitHub mutation once the exact plan is approved.

This wording is not the authorization boundary. The default
`workflow.require_approval` posture requires source-only plan preparation,
review and administrator approval with `cao workflow approve <plan_id>`. The
exact `mode=apply` inputs, repository, date, snapshot, finite scope and Work
bindings must match the unexpired plan. A dry-run approval does not authorize
apply. If either prepared identity below is missing or unresolved, stop and
report the review requirement; never infer a standing grant from this template.

Run exactly this command and do not change, omit, or add arguments:

```bash
cao workflow run pr_health --run-id [[run_id_apply]] \
  --input repo=[[repo]] \
  --input as_of=[[as_of]] \
  --input snapshot_id=[[snapshot_id_apply]] \
  --input importance_analysis=true \
  --input importance_provider=claude_code \
  --input importance_agent=reviewer \
  --input mode=apply \
  --prepared-id [[prepared_id_apply]] \
  --expected-plan-id [[expected_plan_id_apply]] \
  --json
```

`cao workflow run` blocks until completion, and `--json` returns the
deterministic full result JSON. Report that complete JSON result, including its
`decisions`, `decisions_file`, `artifact_dir`, and `posted_github_comments`
fields. The command's repository is exactly `[[repo]]`; each target PR number is
selected by the workflow from that repository and live-revalidated before
commenting. Do not add labels, change PR state, submit reviews, write commit
statuses or checks, or make any other GitHub mutation. Do not add any command
arguments. If the run ID already exists, inspect its status instead of creating a
duplicate run — a replayed run re-emits its frozen manifest and posts nothing.
