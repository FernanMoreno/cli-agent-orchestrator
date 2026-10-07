import json

from click.testing import CliRunner

from cli_agent_orchestrator.cli.commands.profile import profile


def test_lint_is_read_only_and_redacts_prompt(tmp_path):
    source = tmp_path / "review.md"
    source.write_text(
        "---\nname: review\ndescription: Review\nengine: kas\nallowedTools: []\n---\nprivate-prompt-sentinel\n"
    )
    result = CliRunner().invoke(profile, ["lint", str(source), "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["generation_safe"] is True
    assert report["kas_visible_tools"] == []
    assert "private-prompt-sentinel" not in result.output
    assert list(tmp_path.iterdir()) == [source]
