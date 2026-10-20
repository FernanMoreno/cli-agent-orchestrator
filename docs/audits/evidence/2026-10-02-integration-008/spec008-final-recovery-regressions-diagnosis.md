# Remaining service15 diagnosis (read-only)

**Two test-contract migrations explain all fifteen recorded failures. No production authority change is justified.**

## Fourteen recovery-decision failures

Source evidence:
- `test/services/test_recovery_decision_intake.py::_seed_run` inserts legacy journal rows without a frozen manifest; `SNAPSHOT` is just `{"source": ...}`. The original `_patched_journal` isolated the DB but did not select explicit legacy approval posture. No prepared plan, private scoped attachment or readable plan ID is provisioned by these fixtures.
- `services/script_runner.py:1749` calls `approval_gate.ensure_plan_approved` after live-run/tier/snapshot/orphan-consent admission checks, before `apply_decisions`, generation CAS, capability issue or spawn. With required approval, `approval_gate.py:148` correctly refuses the unreadable plan ID. The HTTP route maps unavailable proof to 503, so tests expecting invalid-decision 400 cannot reach their intended boundary.
- `settings_service.resolve_workflow_approval_posture` defaults to required; absent, malformed and falsy environment settings cannot disable it. The supported explicit compatibility setting is JSON boolean `workflow.require_approval=false`.

This is not evidence of broken prepared-run credentials or consent revocation: all fourteen stop earlier at the approval check. Do not move decision application ahead of approval, omit approval on resume, manufacture a v2 manifest/approval, broaden private attachment validation, or globally disable approval.

Narrow proposed fixture migration: within this module's existing `_patched_journal`, write a per-test settings JSON with the supported explicit boolean false, point `settings_service.SETTINGS_FILE` at it and remove inherited `CAO_WORKFLOW_REQUIRE_APPROVAL` only via monkeypatch. Document that these historical NULL-manifest rows exercise legacy recovery consent. Retain every existing consent vocabulary/validation/no-write/live/refusal/re-decision/revoke/replay/one-attempt assertion and real SQLite journal transition. Dedicated required/default/malformed/enable-only posture tests must run unchanged alongside it.

Off-workspace proof: `/tmp/cao_recovery_legacy_posture_plugin.py` applies that genuine file-backed posture **only** to the recovery module, after the existing hermetic settings fixture. No gate method, consent method or journal method is substituted. Actual full recovery module: `/tmp/spec008-final-recovery-legacy-proof.log`, **81 passed,5 existing warnings,46.77s**. This includes all failing cases and later cases absent from the original maxfail15 prefix.

## One profile-store failure

`test_profile_store.py:379` originally substitutes `_atomic_publish` with `paused_publish(t,content,encoding)`. Current `utils/atomic_file.py:332` correctly forwards the additive keyword `mode=...`; its owner signature at `:390` accepts keyword-only mode and retains native file-mode preservation. The worker raises TypeError before setting the event, producing the misleading `replace never reached the publish step` assertion and the recorded thread exception. No actual lock/delete interleaving was reached.

Narrow migration: test wrapper accepts `*, mode: int | None = None` and forwards `mode=mode` to the **real** `_atomic_publish`, retaining both events, threads, lock blocking, retained original file, serialised delete and final file-absence assertions. Do not remove the production mode argument or weaken file-permission/atomic locking behavior.

Off-workspace actual copied test with only that wrapper migration: `/tmp/cao_profile_mode_proof.py`; `/tmp/spec008-final-profile-mode-proof.log`: **1 passed,7.15s**.

## Concurrent writer / actual current-source check

The UI implementation agent independently landed exactly the two narrow test migrations while this read-only diagnosis ran. This agent made **no workspace edits**. Current actual-file check without the off-workspace plugin, including dedicated strict posture controls:

`.venv/bin/python -m pytest test/services/test_recovery_decision_intake.py test/services/test_integration_008_approval_posture.py test/services/test_profile_store.py::test_delete_profile_cannot_unlink_while_a_replace_holds_the_lock --no-cov --basetemp=/var/tmp/cao-final-recovery-current-proof -q`

Actual current source: `/tmp/spec008-final-recovery-current-proof.log`, **93 passed,5 existing warnings,55.28s**. This is the real migrated module plus default-required, malformed-settings, explicit-false and enable-only environment controls, with no external proof plugin. Scoped Work classification/owner validation still precedes legacy admission; generation CAS, Work grants, private capability ownership and durable one-shot consent revocation remain production-owned and unchanged. Full service-suite closure remains the controller's broader gate; this report does not extrapolate beyond the captured15 and actual focused proofs.
