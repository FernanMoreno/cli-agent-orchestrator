"""Conjunctive launch predicates for frozen, authorized Work material."""

from cli_agent_orchestrator.services.launch_material import LaunchMaterialError


def admit_launch_request(
    material,
    *,
    provider,
    agent,
    model=None,
    allowed_tools=None,
    working_directory=None,
    reuse_terminal_id=None,
    use_worktree=False,
    engine=None,
):
    if provider != material["provider"] or agent != material["profile_name"]:
        raise LaunchMaterialError("scope_agent_not_declared")
    expected_engine = material.get("engine") or (
        "v2" if material["provider"] == "kiro_cli" else None
    )
    if engine is not None and getattr(engine, "value", engine) != expected_engine:
        raise LaunchMaterialError("scope_engine_not_declared")
    if model is not None and model != material["model"]:
        raise LaunchMaterialError("scope_model_not_declared")
    if allowed_tools is not None and sorted(allowed_tools) != sorted(material["allowed_tools"]):
        raise LaunchMaterialError("scope_tools_not_declared")
    root = material["contract"]["resources"]["checkout_root"]
    if working_directory is not None and working_directory != root:
        raise LaunchMaterialError("scope_target_not_declared")
    if reuse_terminal_id is not None or use_worktree:
        # Existing incarnation/child proofs must be explicitly provisioned;
        # a caller flag can never create those proofs.
        raise LaunchMaterialError("scope_target_requires_provision")
    return material
