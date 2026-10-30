"""OpenCode v2 returns source-bound native text only after its receipt witness."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from cli_agent_orchestrator.providers.base import OutputExtractionError
from cli_agent_orchestrator.providers.opencode_cli import OpenCodeCliProvider
from cli_agent_orchestrator.utils.opencode_ownership import PrivateConfigOwnership
from cli_agent_orchestrator.utils.opencode_v2_result_binding import write_v2_result_binding

TERMINAL_ID = "t120-terminal"
AGENT = "storage_probe"
SOURCE = "---\ndescription: Storage probe\n---\nReturn exact text.\n"
TOKEN = "CAO-TURN-RECEIPT-" + "a" * 32
TEXT = "Validado ñ, 中文."
RENDERED = "Validado\n ñ, 中文.\n" + TOKEN


def _replace_native_user_text(database: Path, text: str) -> None:
    """Change only the synthetic upstream OpenCode store, never CAO metadata."""
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "UPDATE session_message SET data=? WHERE id='user-1'",
            (json.dumps({"text": text}, ensure_ascii=False),),
        )


def _provider_and_store(tmp_path: Path, *, parts: list[str], restored=False, agent_id: str = AGENT):
    cao_home = tmp_path / "cao"
    owner = PrivateConfigOwnership(cao_home, TERMINAL_ID)
    root = owner.allocate(2)
    config_path = root / "opencode.json"
    config_path.write_text(
        json.dumps({"agents": {agent_id: {"system": "Return exact text.", "permissions": {}}}}),
        encoding="utf-8",
    )
    config_path.chmod(0o600)
    write_v2_result_binding(owner, (agent_id, SOURCE), config_path)

    data = root / "data"
    data.mkdir(mode=0o700)
    opencode = data / "opencode"
    opencode.mkdir(mode=0o700)
    database = opencode / "opencode.db"
    with closing(sqlite3.connect(database)) as db:
        db.executescript("""CREATE TABLE session_v2 (
                   id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                   slug TEXT NOT NULL, directory TEXT NOT NULL,
                   version TEXT NOT NULL, cost REAL NOT NULL DEFAULT 0,
                   time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL
               );
               CREATE TABLE session_message (
                   id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                   type TEXT NOT NULL, seq INTEGER NOT NULL,
                   time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL,
                   data TEXT NOT NULL
               );
               CREATE UNIQUE INDEX session_message_session_seq_idx
                   ON session_message(session_id,seq);
               INSERT INTO session_v2 VALUES
                   ('session-1','project-1','slug','/workspace','2.0.18',0,1,1);""")
        db.execute(
            "INSERT INTO session_message VALUES(?,?,?,?,?,?,?)",
            (
                "user-1",
                "session-1",
                "user",
                5,
                5,
                5,
                json.dumps({"text": "Current prompt " + TOKEN}, ensure_ascii=False),
            ),
        )
        db.execute(
            "INSERT INTO session_message VALUES(?,?,?,?,?,?,?)",
            (
                "assistant-1",
                "session-1",
                "assistant",
                8,
                8,
                8,
                json.dumps(
                    {
                        "agent": agent_id,
                        "content": [{"type": "text", "text": part} for part in parts],
                        "time": {"created": 6, "completed": 7},
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        db.commit()
    database.chmod(0o600)

    provider = OpenCodeCliProvider(TERMINAL_ID, "session", "window", agent_id)
    provider.restore_runtime_variant("opencode-v2")
    if restored:
        provider._private_config_ownership = PrivateConfigOwnership(cao_home, TERMINAL_ID)
        provider.restore_private_config_ownership()
        provider.restore_turn_receipt_state(
            {
                "generation": "b" * 32,
                "receipt_sha256": hashlib.sha256(TOKEN.encode("utf-8")).hexdigest(),
                "phase": "sent",
            }
        )
    else:
        provider._private_config_ownership = owner
        provider._pending_turn_generation = "b" * 32
        provider._pending_turn_receipt = TOKEN
        provider._pending_turn_receipt_sha256 = hashlib.sha256(TOKEN.encode("utf-8")).hexdigest()
    return provider, owner, root, database


def _unprofiled_provider(tmp_path: Path, *, restored=False):
    cao_home = tmp_path / "cao"
    owner = PrivateConfigOwnership(cao_home, TERMINAL_ID)
    root = owner.allocate(2)
    config_path = root / "opencode.json"
    config_path.write_text(json.dumps({"agents": {}, "mcp": {}}), encoding="utf-8")
    config_path.chmod(0o600)
    write_v2_result_binding(owner, None, config_path)

    provider = OpenCodeCliProvider(TERMINAL_ID, "session", "window")
    provider.restore_runtime_variant("opencode-v2")
    if restored:
        provider._private_config_ownership = PrivateConfigOwnership(cao_home, TERMINAL_ID)
        provider.restore_private_config_ownership()
        provider.restore_turn_receipt_state(
            {
                "generation": "b" * 32,
                "receipt_sha256": hashlib.sha256(TOKEN.encode("utf-8")).hexdigest(),
                "phase": "sent",
            }
        )
    else:
        provider._private_config_ownership = owner
        provider._pending_turn_generation = "b" * 32
        provider._pending_turn_receipt = TOKEN
        provider._pending_turn_receipt_sha256 = hashlib.sha256(TOKEN.encode("utf-8")).hexdigest()
    return provider, root


def test_current_v2_returns_exact_native_unicode_for_split_rendered_rows(tmp_path):
    provider, _owner, _root, _database = _provider_and_store(
        tmp_path,
        parts=[TEXT[:9], TEXT[9:] + "\n" + TOKEN],
    )

    canonical = provider.canonicalize_turn_receipt_result(RENDERED)

    assert canonical == TEXT + "\n" + TOKEN
    assert canonical.splitlines()[0] == TEXT


def test_restored_profile_alias_normalizes_to_the_private_agent_id(tmp_path):
    private_agent = "team__reviewer"
    provider, _owner, _root, _database = _provider_and_store(
        tmp_path,
        parts=[TEXT + "\n" + TOKEN],
        restored=True,
        agent_id=private_agent,
    )
    provider._agent_profile = "team/reviewer"

    assert provider.canonicalize_turn_receipt_result(RENDERED) == TEXT + "\n" + TOKEN


def test_restored_wrong_profile_alias_still_fails_closed(tmp_path):
    provider, _owner, _root, _database = _provider_and_store(
        tmp_path,
        parts=[TEXT + "\n" + TOKEN],
        restored=True,
        agent_id="team__reviewer",
    )
    provider._agent_profile = "other/reviewer"

    with pytest.raises(OutputExtractionError):
        provider.canonicalize_turn_receipt_result(RENDERED)


def test_restored_v2_uses_bound_agent_root_without_recovering_plaintext_nonce(tmp_path):
    provider, _owner, _root, _database = _provider_and_store(
        tmp_path,
        parts=[TEXT + "\n" + TOKEN],
        restored=True,
    )

    assert provider._pending_turn_receipt is None
    assert provider._native_agent_source is None
    assert provider.canonicalize_turn_receipt_result(RENDERED) == TEXT + "\n" + TOKEN


def test_restored_native_binding_uses_private_agent_when_metadata_is_empty(tmp_path):
    provider, _owner, _root, _database = _provider_and_store(
        tmp_path,
        parts=[TEXT + "\n" + TOKEN],
        restored=True,
    )
    provider._agent_profile = ""

    assert provider.canonicalize_turn_receipt_result(RENDERED) == TEXT + "\n" + TOKEN


def test_hard_native_line_feed_is_preserved_not_normalized(tmp_path):
    hard_text = "first logical line\nsecond logical line"
    provider, _owner, _root, _database = _provider_and_store(
        tmp_path,
        parts=["first logical line\n", "second logical line\n" + TOKEN],
    )

    canonical = provider.canonicalize_turn_receipt_result(
        "first logical line second logical line\n" + TOKEN
    )

    assert canonical == hard_text + "\n" + TOKEN
    assert "\n" in canonical


@pytest.mark.parametrize(
    "rendered",
    [
        "content without a receipt",
        TOKEN + "\nsecond token\n" + TOKEN,
        "foreign receipt\n" + "CAO-TURN-RECEIPT-" + "c" * 32,
    ],
)
def test_missing_duplicate_or_wrong_rendered_nonce_fails_closed(tmp_path, rendered):
    provider, _owner, _root, _database = _provider_and_store(
        tmp_path,
        parts=[TEXT + "\n" + TOKEN],
    )

    with pytest.raises(OutputExtractionError):
        provider.canonicalize_turn_receipt_result(rendered)


def test_native_text_that_does_not_match_rendered_content_is_not_substituted(tmp_path):
    provider, _owner, _root, _database = _provider_and_store(
        tmp_path,
        parts=["different answer\n" + TOKEN],
    )

    with pytest.raises(OutputExtractionError):
        provider.canonicalize_turn_receipt_result(RENDERED)


@pytest.mark.parametrize(
    "native,rendered",
    [
        ("The task is **succeeded**.", "The task is succeeded."),
        ("Estado __ñ, 中文__.", "Estado ñ, 中文."),
        ("Task `owned_id` is **succeeded**.", "Task owned_id is succeeded."),
        ("Literal `**stars**` and `&amp;`.", "Literal **stars** and &amp;."),
        ("Literal ``a`b``.", "Literal a`b."),
        ("**`a`**", "a"),
        ("A **bold** answer.\n\nAnother **part**.", "A bold answer.\nAnother part."),
    ],
)
def test_bound_v2_strong_emphasis_preserves_exact_native_text(tmp_path, native, rendered):
    provider, _owner, _root, _database = _provider_and_store(
        tmp_path,
        parts=[native[:8], native[8:] + "\n" + TOKEN],
    )
    assert (
        provider.canonicalize_turn_receipt_result(rendered + "\n" + TOKEN) == native + "\n" + TOKEN
    )


@pytest.mark.parametrize(
    "native,rendered",
    [
        ("The task is **succeeded**.", "The task is failed."),
        ("2 ** 3", "2 3"),
        ("**unclosed", "unclosed"),
        (r"\*\*literal\*\*", "literal"),
        ("`**literal**`", "literal"),
        ("    **literal**", "literal"),
        ("```\n**literal**\n```", "literal"),
        ("# **heading**", "heading"),
        ("[label](https://example.invalid) **bold**", "label bold"),
        ("**bold** &amp; literal", "bold & literal"),
        ("<b>**bold**</b>", "bold"),
        ("***nested***", "nested"),
        ("**outer __inner__**", "outer inner"),
        ("[unused]: https://example.invalid\n\n**bold**", "bold"),
        ("**bold**  \nhard break", "bold hard break"),
        ("**bold** &#65;", "bold A"),
        ("**bold**\x00", "bold�"),
        ("**succeeded**", "succeedeD"),
        ("`  code  ` **bold**", "code bold"),
        ("`line\nfold` **bold**", "line fold bold"),
        ("`&#65;` **bold**", "A bold"),
        (r"`\*` **bold**", "* bold"),
        ("`literal` **bold**", "literaL bold"),
        ("`a **bold**", "a bold"),
        ("    `code`", "code"),
    ],
)
def test_v2_strong_projection_does_not_erase_literal_or_unsupported_content(
    tmp_path, native, rendered
):
    provider, _owner, _root, _database = _provider_and_store(
        tmp_path,
        parts=[native + "\n" + TOKEN],
    )
    with pytest.raises(OutputExtractionError):
        provider.canonicalize_turn_receipt_result(rendered + "\n" + TOKEN)


def test_v2_strong_projection_rejects_receipt_between_paragraphs(tmp_path):
    native = "**first**\n" + TOKEN + "\nlast"
    provider, *_ = _provider_and_store(tmp_path, parts=[native])
    with pytest.raises(OutputExtractionError):
        provider.canonicalize_turn_receipt_result("first\nlast\n" + TOKEN)


def test_v2_strong_projection_parser_failure_stays_generic(tmp_path, monkeypatch):
    import markdown_it

    provider, *_ = _provider_and_store(tmp_path, parts=["**bold**\n" + TOKEN])

    def fail(*args, **kwargs):
        raise ValueError("private parser detail")

    monkeypatch.setattr(markdown_it.MarkdownIt, "parse", fail)
    with pytest.raises(OutputExtractionError) as error:
        provider.canonicalize_turn_receipt_result("bold\n" + TOKEN)
    assert str(error.value) == "OpenCode v2 canonical result is unavailable"


def test_v2_strong_projection_stops_before_and_after_parse(monkeypatch):
    import markdown_it

    from cli_agent_orchestrator.providers import opencode_cli

    parse = markdown_it.MarkdownIt.parse
    calls = []

    def watched(self, *args, **kwargs):
        calls.append(True)
        return parse(self, *args, **kwargs)

    monkeypatch.setattr(markdown_it.MarkdownIt, "parse", watched)
    monkeypatch.setattr(opencode_cli.time, "monotonic", lambda: 2.0)
    assert opencode_cli._inline_render_signature("**bold**", stop=1.0) is None
    assert calls == []
    ticks = iter([0.0, 2.0])
    monkeypatch.setattr(opencode_cli.time, "monotonic", lambda: next(ticks))
    assert opencode_cli._inline_render_signature("**bold**", stop=1.0) is None
    assert calls == [True]


def test_v1_remains_identity_and_never_reads_v2_database(tmp_path):
    provider = OpenCodeCliProvider(TERMINAL_ID, "session", "window", AGENT)
    provider.restore_runtime_variant("opencode-v1")

    assert provider.canonicalize_turn_receipt_result(RENDERED) == RENDERED


@pytest.mark.parametrize("restored", [False, True])
def test_unprofiled_v2_uses_only_its_private_rendered_mode(tmp_path, restored):
    provider, _root = _unprofiled_provider(tmp_path, restored=restored)

    assert provider.canonicalize_turn_receipt_result(RENDERED) == RENDERED


def test_empty_profile_metadata_does_not_bypass_missing_private_mode(tmp_path):
    provider, _owner, root, _database = _provider_and_store(
        tmp_path,
        parts=[TEXT + "\n" + TOKEN],
    )
    (root / ".cao-v2-result-binding.json").unlink()
    provider._agent_profile = ""
    provider._native_agent_source = None

    with pytest.raises(OutputExtractionError):
        provider.canonicalize_turn_receipt_result(RENDERED)


def test_nonempty_profile_metadata_cannot_select_private_rendered_mode(tmp_path):
    provider, _root = _unprofiled_provider(tmp_path)
    provider._agent_profile = AGENT

    with pytest.raises(OutputExtractionError):
        provider.canonicalize_turn_receipt_result(RENDERED)
