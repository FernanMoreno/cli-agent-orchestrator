"""KAS opt-in, compilation and persisted policy identity admission."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Optional

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.models.kiro_engine import KiroEngine
from cli_agent_orchestrator.models.kiro_launch import KiroLaunchRefusedError
from cli_agent_orchestrator.utils.kiro_policy import (
    CompiledKiroPolicy,
    KiroPolicyError,
    compile_kiro_policy,
)


@dataclass(frozen=True)
class LaunchVerdict:
    allowed: bool
    engine: KiroEngine
    mode: str = "lint-gated"
    reason_code: Optional[str] = None
    profile_field: Optional[str] = None
    message: Optional[str] = None
    policy: Optional[CompiledKiroPolicy] = None
    policy_digest: Optional[str] = None


def kas_policy_digest(profile: AgentProfile) -> str:
    """Hash exact private profile and compiled effective rules; never expose bytes."""
    policy = compile_kiro_policy(profile)
    material = {
        "version": 1,
        "profile": profile.model_dump(mode="json"),
        "permissions": policy.permissions.model_dump(mode="json"),
        "visible_tools": policy.visible_tools,
        "denied_tools": policy.denied_tools,
    }
    data = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(data).hexdigest()


def check_kas_launch(
    *,
    engine: KiroEngine,
    profile: Optional[AgentProfile] = None,
    expected_digest: Optional[str] = None,
    require_persisted_proof: bool = False,
) -> LaunchVerdict:
    if engine != KiroEngine.KAS:
        return LaunchVerdict(True, engine)

    def refuse(code: str, message: str, field: Optional[str] = None) -> LaunchVerdict:
        return LaunchVerdict(False, engine, reason_code=code, message=message, profile_field=field)

    if not constants.ENABLE_KAS_LAUNCH:
        return refuse(
            "launch-not-enabled",
            "KAS launch is disabled. Set CAO_ENABLE_KAS_LAUNCH=true to opt in, or use engine 'v2'.",
        )
    if profile is None:
        return refuse(
            "profile-required",
            "KAS requires a validated profile at every launch, restoration and reuse boundary.",
        )
    if require_persisted_proof and not expected_digest:
        return refuse(
            "policy-proof-missing",
            "The existing KAS runtime has no persisted launch-policy proof; reconcile it before reuse.",
        )
    try:
        policy = compile_kiro_policy(profile)
        # Service import is local, keeping provider imports below allocation services.
        from cli_agent_orchestrator.services.kiro_profiles import render_kiro_kas

        render_kiro_kas(profile, [], profile.mcpServers)
        digest = kas_policy_digest(profile)
    except KiroPolicyError as exc:
        return refuse(
            "profile-untranslatable",
            f"KAS profile cannot be translated ({exc.diagnostic.code}). Run cao profile lint <name>.",
        )
    if expected_digest is not None and digest != expected_digest:
        return refuse(
            "policy-drift",
            "KAS policy material differs from the persisted launch proof; reconcile the existing runtime.",
        )
    return LaunchVerdict(True, engine, policy=policy, policy_digest=digest)


def assert_kas_launch_allowed(
    *,
    engine: KiroEngine,
    profile: Optional[AgentProfile] = None,
    expected_digest: Optional[str] = None,
    require_persisted_proof: bool = False,
) -> LaunchVerdict:
    verdict = check_kas_launch(
        engine=engine,
        profile=profile,
        expected_digest=expected_digest,
        require_persisted_proof=require_persisted_proof,
    )
    if not verdict.allowed:
        raise KiroLaunchRefusedError(
            code=verdict.reason_code or "launch-refused",
            message=verdict.message,
            profile_field=verdict.profile_field,
            engine=engine,
        )
    return verdict
