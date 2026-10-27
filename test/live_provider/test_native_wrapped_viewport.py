"""Owned narrow-viewport witness contracts; no process, model or database."""

from pathlib import Path
from test.live_provider.native_wrapped_viewport import validate_wrapped_viewport

import pytest

pytestmark = pytest.mark.offline_database
FIXTURES = Path(__file__).parents[1] / "providers/fixtures"


@pytest.mark.parametrize("case", ["auth", "quota"])
@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "wrong_size",
        "missing_canary",
        "indented_head",
        "unwrapped",
        "wrong_continuation",
        "finished",
        "duplicate",
        "missing_composer",
        "wrong_case",
        "blank_body",
        "receipt_canary",
    ],
)
def test_narrow_witness_requires_the_actual_wrapped_failure(case, mutation):
    text = (FIXTURES / f"codex_0164_wrapped_{case}.txt").read_text()
    marker = "CAO-PARTIAL-T178-20261027" if case == "auth" else "CAO-PARTIAL-QUOTA-T179"
    size = (80, 50)
    requested = case
    if mutation == "wrong_size":
        size = (220, 50)
    elif mutation == "missing_canary":
        text = text.replace(marker, "different")
    elif mutation == "indented_head":
        text = text.replace("■ ", "  ■ ", 1)
    elif mutation == "unwrapped":
        name = "partial_auth_error" if case == "auth" else "partial_quota"
        text = (FIXTURES / f"codex_0164_{name}.txt").read_text()
        marker = "CAO synthetic partial response" if case == "auth" else "CAO-PARTIAL-QUOTA-T179"
    elif mutation == "wrong_continuation":
        text = (
            text.replace("http://127.0.0.1:1/v1/responses", "bad continuation")
            if case == "auth"
            else text.replace("visit https://chatgpt.com/settings/usage", "bad continuation")
        )
    elif mutation == "finished":
        text = text.replace("› Ask Codex", "Worked for 1s\n› Ask Codex")
    elif mutation == "duplicate":
        head = next(line for line in text.splitlines() if line.startswith("■ "))
        text = text.replace("› Ask Codex", head + "\n› Ask Codex")
    elif mutation == "missing_composer":
        text = text.replace("› Ask Codex", "Ask Codex")
    elif mutation == "wrong_case":
        requested = "quota" if case == "auth" else "auth"
    elif mutation == "blank_body":
        rows = text.splitlines()
        index = next(i for i, line in enumerate(rows) if line.startswith("■ "))
        rows.insert(index + 1, "")
        text = "\n".join(rows)
    elif mutation == "receipt_canary":
        marker = "CAO-TURN-RECEIPT-" + "a" * 32
    if mutation is None:
        result = validate_wrapped_viewport(text, case=requested, marker=marker, size=size)
        assert result["wrapped_cell_rows"] == (2 if case == "auth" else 3)
        assert result["partial_reply_observed"]
    else:
        with pytest.raises(AssertionError):
            validate_wrapped_viewport(text, case=requested, marker=marker, size=size)
