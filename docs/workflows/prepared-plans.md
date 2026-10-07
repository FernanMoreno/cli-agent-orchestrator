# Prepared workflow plans

Script approval is required when settings are absent or malformed. An explicit
`"workflow": {"require_approval": false}` in settings preserves legacy starts;
an environment value can enable approval and cannot disable it.

Start with existing Work jobs, grants, origin authorizations, executable pins and
an authorized delegation snapshot. Approval never creates these resources.
`memory: "off"` requires an authorized empty snapshot; `exact-snapshot` freezes
the complete authorized snapshot. A profile using credential-bearing MCP
environment values cannot currently be prepared: trusted secret-reference
material support is required before those credentials can be frozen safely.

The Python workflow declares a literal `SCOPE` with finite targets, permitted
agent profiles and memory mode. YAML workflows supply the same literal declaration
through `--scope`. Target mappings and binding selections are JSON objects:

```json
{"repo": "/absolute/existing/checkout"}
```

```json
{"repo": {"developer": "existing-seed-step-id"}}
```

Discover available seeds owned by the authenticated caller, prepare the exact
source and inputs, and review the public digest envelope:

```sh
cao workflow seeds my-workflow
cao workflow plan my-workflow --targets targets.json --bindings bindings.json --json
cao workflow review-plan PREPARED_ID
```

An administrator approves the returned `plan_id` through the existing workflow
approval command or `POST /workflows/plans/approve`. The owner then runs the same
prepared identity; it can be reused for multiple independent runs:

```sh
cao workflow run my-workflow --prepared-id PREPARED_ID --expected-plan-id PLAN_ID
```

Operator provisioning uses `cao workflow provision my-workflow --request request.json`
or `POST /workflows/my-workflow/provisions`. The request includes the existing
job/grant revision, complete contract and delivery, workflow/receiver subject and
authorization references, source hash and expected revision. An optional
`subject_token` authenticates the existing subject; it never creates a principal
or grant and must be handled as a credential. Provisioning requires an admin
caller and retains the existing Work validators. MCP exposes preparation, review
and discovery, without approval or authority creation.

A scoped runtime step may derive its prompt within the approved frozen source,
inputs, profile, target and step-count bounds. It names `target_key`; omission is
accepted only for one unambiguous declared target for that profile. The server
creates the exact immutable Work delivery before admission. A repeated step
with a conflicting prompt or scope is refused. Grants, authorized snapshots,
physical checkout identity, profiles and executable dependencies are checked
again before execution and resume. Existing provisioned child worktrees retain
their approved HEAD pin: a later commit requires a newly prepared plan. Ignore
nested worktree directories in the parent Git checkout to keep its baseline
representable; preparation refuses an unavailable or unstable baseline.

Private source and typed inputs remain in owner-only snapshot storage. Public
review returns digests, not private source, paths or inputs. Runs retaining
immutable Work audit references cannot be deleted; deletion returns HTTP 409.
An unused prepared plan may be deleted with `DELETE /workflows/plans/PREPARED_ID`.
