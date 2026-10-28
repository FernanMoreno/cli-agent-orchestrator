"""One opt-in native app-server authority case; never requests a model turn."""

import json
from pathlib import Path
from test.live_provider import codex_mcp_catalog as catalog
from test.live_provider import interaction_cli_journey as journey
from test.live_provider.codex_runtime_snapshot import verify_codex_runtime_snapshot
from test.live_provider.supervision_authority import exercise_supervision, require

from jsonschema import validate


def run_case(
    root,
    *,
    binary,
    runtime_manifest,
    runtime_sha256,
    schema_root,
    env,
    overlay,
    owned,
    port,
    caller_id,
    child_id,
    foreign_id,
    observations,
):
    import secrets

    from cli_agent_orchestrator.constants import LOCAL_AGENT_STORE_DIR
    from cli_agent_orchestrator.providers.codex import CodexProvider
    from cli_agent_orchestrator.utils.mcp_readiness import has_ready_signal
    from cli_agent_orchestrator.utils.mcp_tool_policy import TOOL_PROFILES

    admission = verify_codex_runtime_snapshot(
        Path(binary), Path(runtime_manifest), expected_manifest_sha256=runtime_sha256
    )
    require(
        LOCAL_AGENT_STORE_DIR.resolve().is_relative_to(root.resolve()),
        "fresh_private_cao_process_required",
    )
    require(type(port) is int and 1 <= port <= 65535, "invalid_private_api_port")
    memory = next(
        int(line.split()[1]) // 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    require(memory >= 6144, "native_authority_memory_admission")
    observations.assert_ready()
    require(
        (observations.caller_id, observations.child_id, observations.foreign_id)
        == (caller_id, child_id, foreign_id),
        "observation_identity_mismatch",
    )
    response_schema = json.loads((schema_root / "v2/McpServerToolCallResponse.json").read_text())
    work = root / "native-work"
    work.mkdir(mode=0o700)
    ready = root / "supervision-ready.json"
    nonce = secrets.token_hex(16)
    child_env = journey._mcp_child_environment(root, port, caller_id)
    child_env.update(
        CAO_MCP_READY_FILE=str(ready), CAO_MCP_READY_TOKEN=nonce, CAO_MCP_APPS_ENABLED="0"
    )
    metadata = {
        "name": "supervision-authority",
        "description": "Native direct-call authority diagnostic; no model inference.",
        "provider": "codex",
        "codexProfile": "catalog-safe",
        "model": journey.CODEX_INTERACTION_MODEL,
        "mcpServers": {
            catalog.SERVER: {
                "command": "cao-mcp-server",
                "env": child_env,
                "cao_tool_profile": "supervision",
            }
        },
    }
    LOCAL_AGENT_STORE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = LOCAL_AGENT_STORE_DIR / "supervision-authority.md"
    with path.open("x") as out:
        path.chmod(0o600)
        out.write(journey._profile_frontmatter(metadata))
    provider = CodexProvider(
        caller_id,
        "supervision-private",
        "worker",
        "supervision-authority",
        allowed_tools=["@cao-mcp-server"],
    )
    owned_start = len(owned)
    try:
        client = catalog.CatalogClient(
            catalog.native_overrides(provider._build_codex_command(), work, str(binary)),
            root=root,
            work=work,
            env=env,
            overlay=overlay,
            schema_root=schema_root,
            owned=owned,
        )
        client.rpc(
            "initialize",
            {
                "clientInfo": {"name": "caos_supervision_authority", "version": "1"},
                "capabilities": {"explicitGatewayOauth": True},
            },
        )
        client.send({"method": "initialized"})
        thread = client.rpc(
            "thread/start",
            {
                "model": journey.CODEX_INTERACTION_MODEL,
                "cwd": str(work),
                "ephemeral": True,
                "approvalPolicy": "never",
                "sandbox": "read-only",
            },
        )
        thread_id = thread["thread"]["id"]
        names, status = catalog._catalog(client, thread_id)
        require(
            names == TOOL_PROFILES["supervision"] and has_ready_signal(ready, nonce),
            "native_supervision_catalog_mismatch",
        )
        require(
            status.get("resources") == [] and status.get("resourceTemplates") == [],
            "native_supervision_resources_exposed",
        )

        def native_call(tool, args):
            result = catalog._call(client, thread_id, tool, args)
            validate(result, response_schema)
            return result

        proof = exercise_supervision(
            call=native_call,
            offered=names,
            caller_id=caller_id,
            child_id=child_id,
            foreign_id=foreign_id,
            inspect_expected=observations.inspect_expected,
            observe_foreign=observations.observe_foreign,
        )
        observations.verify_native_reads()
        return {
            **proof,
            "catalog_exact": True,
            "catalog_count": len(names),
            "runtime_version": admission["version"],
            "runtime_asset_count": admission["asset_count"],
            "memory_mib": memory,
            "model_turns_requested": 0,
            "api_auth": "disabled_local",
            "target_backend": "mock_cli",
            "target_receipt": "fixture_seeded_postgresql",
        }
    finally:
        for registered in reversed(owned[owned_start:]):
            registered.close()
