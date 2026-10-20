"""Consistency, bounds and input isolation of the native policy envelope."""

import base64
import copy
import json

import frontmatter
import pytest

from cli_agent_orchestrator.utils.opencode_policy import (
    MAX_ENCODED_POLICY,
    NATIVE_POLICY_PREFIX,
    apply_native_profiles,
    parse_native_agent,
    render_native_agent,
    validate_native_binding,
)

pytestmark = pytest.mark.offline_database


@pytest.fixture
def native():
    agent = frontmatter.Post("Revisión Unicode: ñ, 中文, 🐾.", description="Reviewer")
    config = {
        "mcp": {"cao": {"type": "local", "command": ["/stable/cao-mcp-server"], "enabled": True}},
        "agent": {"reviewer": {"tools": {"cao*": True}}},
        "tools": {"cao*": False},
    }
    return agent, config


def rewrite_envelope(text, change):
    post = frontmatter.loads(text)
    first, _, body = post.content.partition("\n")
    payload = json.loads(base64.urlsafe_b64decode(first[len(NATIVE_POLICY_PREFIX) : -4]))
    change(payload)
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    post.content = f"{NATIVE_POLICY_PREFIX}{encoded} -->\n{body}"
    return frontmatter.dumps(post)


@pytest.mark.parametrize("profile", ["interaction", "collaboration"])
def test_ready_round_trip_preserves_payload_and_private_source(native, profile):
    agent, config = native
    before = copy.deepcopy(config)
    text = render_native_agent(agent, "reviewer", {"cao": profile}, source_config=config)
    bare, policy = parse_native_agent(text, "reviewer")
    validate_native_binding(bare, policy, config)
    private = apply_native_profiles(config, policy)
    assert bare.content == agent.content
    assert bare.metadata == agent.metadata
    assert private["mcp"]["cao"]["cao_tool_profile"] == profile
    assert private["mcp"]["cao"]["command"][1:] == [
        "-m",
        "cli_agent_orchestrator.mcp_server.server",
    ]
    assert config == before
    with pytest.raises(TypeError):
        policy.profiles["cao"] = "interaction"


def test_pending_is_parseable_but_not_launchable(native):
    agent, config = native
    text = render_native_agent(agent, "reviewer", {"cao": "interaction"})
    bare, policy = parse_native_agent(text, "reviewer")
    assert policy.state == "pending"
    with pytest.raises(ValueError, match="native MCP policy"):
        validate_native_binding(bare, policy, config)


@pytest.mark.parametrize(
    "change",
    [
        lambda x: x.update(version=2),
        lambda x: x.update(version=True),
        lambda x: x.update(agent_id="other"),
        lambda x: x.update(state="unknown"),
        lambda x: x.update(profiles={"cao": "unknown"}),
        lambda x: x.update(profiles={"cao": None}),
        lambda x: x.update(profiles=[]),
        lambda x: x.update(profiles={"": "interaction"}),
        lambda x: x.update(binding="ñ" * 64),
        lambda x: x.update(binding=None),
        lambda x: x.update(extra=True),
        lambda x: x.pop("state"),
    ],
)
def test_malformed_ready_metadata_never_becomes_legacy(native, change):
    agent, config = native
    text = render_native_agent(agent, "reviewer", {"cao": "interaction"}, source_config=config)
    with pytest.raises(ValueError):
        parse_native_agent(rewrite_envelope(text, change), "reviewer")


@pytest.mark.parametrize("mutation", ["body", "metadata", "server", "grant", "missing", "disabled"])
def test_changed_bound_inputs_are_rejected(native, mutation):
    agent, config = native
    text = render_native_agent(agent, "reviewer", {"cao": "interaction"}, source_config=config)
    bare, policy = parse_native_agent(text, "reviewer")
    if mutation == "body":
        bare.content = "Different prompt"
    elif mutation == "metadata":
        bare["permission"] = {"*": "allow"}
    elif mutation == "server":
        config["mcp"]["cao"]["command"] = ["other-server"]
    elif mutation == "grant":
        config["agent"]["reviewer"]["tools"] = {}
    elif mutation == "missing":
        config["mcp"].clear()
    else:
        config["mcp"]["cao"]["enabled"] = False
    with pytest.raises(ValueError, match="native MCP policy"):
        validate_native_binding(bare, policy, config)


def test_unrelated_agent_and_global_tools_do_not_invalidate_binding(native):
    agent, config = native
    text = render_native_agent(agent, "reviewer", {"cao": "interaction"}, source_config=config)
    bare, policy = parse_native_agent(text, "reviewer")
    config["agent"]["other"] = {"tools": {"other*": True}}
    config["tools"]["other*"] = False
    config["mcp"]["other"] = {"command": ["other-server"]}
    validate_native_binding(bare, policy, config)


@pytest.mark.parametrize(
    "body",
    [
        NATIVE_POLICY_PREFIX + "not-base64 -->\nReview.",
        NATIVE_POLICY_PREFIX + "a" * (MAX_ENCODED_POLICY + 1) + " -->\nReview.",
        "Before\n" + NATIVE_POLICY_PREFIX + "broken -->",
        NATIVE_POLICY_PREFIX + "broken\nReview.",
    ],
)
def test_reserved_prefix_never_silently_falls_back_to_legacy(body):
    with pytest.raises(ValueError):
        parse_native_agent(frontmatter.dumps(frontmatter.Post(body)), "reviewer")


def test_duplicate_json_keys_are_rejected():
    encoded = base64.urlsafe_b64encode(b'{"version":1,"version":2}').decode()
    text = frontmatter.dumps(frontmatter.Post(f"{NATIVE_POLICY_PREFIX}{encoded} -->"))
    with pytest.raises(ValueError):
        parse_native_agent(text, "reviewer")


def test_render_refuses_reserved_prompt_content(native):
    agent, _ = native
    agent.content += "\n" + NATIVE_POLICY_PREFIX
    with pytest.raises(ValueError, match="reserved"):
        render_native_agent(agent, "reviewer", {})


def test_legacy_and_empty_managed_profile_remain_distinct(native):
    agent, config = native
    bare, policy = parse_native_agent(frontmatter.dumps(agent), "reviewer")
    assert policy is None
    text = render_native_agent(bare, "reviewer", {}, source_config=config)
    managed, policy = parse_native_agent(text, "reviewer")
    assert policy is not None and policy.profiles == {}
    validate_native_binding(managed, policy, config)


def test_binding_does_not_depend_on_yaml_formatting(native, monkeypatch):
    agent, config = native
    text = render_native_agent(agent, "reviewer", {"cao": "interaction"}, source_config=config)
    bare, policy = parse_native_agent(text, "reviewer")
    original = frontmatter.dumps
    monkeypatch.setattr(
        frontmatter, "dumps", lambda post: original(post, default_flow_style=True, sort_keys=False)
    )
    validate_native_binding(bare, policy, config)


def test_windows_newlines_preserve_policy_payload_semantics(native):
    agent, config = native
    agent.content += "\nSecond line."
    text = render_native_agent(agent, "reviewer", {"cao": "interaction"}, source_config=config)
    bare, policy = parse_native_agent(text.replace("\n", "\r\n"), "reviewer")
    validate_native_binding(bare, policy, config)
    assert bare.content == agent.content
