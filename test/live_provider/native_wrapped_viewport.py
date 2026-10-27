"""Strict witness for the pinned diagnostic's known wrapped response shapes."""

import re


def validate_wrapped_viewport(text, *, case, marker, size):
    assert case in {"auth", "quota"} and size == (80, 50)
    assert marker and "\n" not in marker and "CAO-TURN-RECEIPT" not in marker
    rows = text.splitlines()
    replies = [i for i, row in enumerate(rows) if row.startswith("• ") and marker in row]
    assert len(replies) == 1, "partial_reply_missing_or_duplicated"
    pattern = (
        r"^■ unexpected status 401\b" if case == "auth" else r"^■ You['’]ve hit your usage limit\."
    )
    errors = [i for i, row in enumerate(rows) if re.match(pattern, row)]
    assert len(errors) == 1, "native_error_head_missing_or_duplicated"
    reply, error = replies[0], errors[0]
    assert error > reply + 1 and all(not row.strip() for row in rows[reply + 1 : error])
    composers = [i for i in range(error + 1, len(rows)) if re.match(r"^›(?:\s|$)", rows[i])]
    assert composers, "native_composer_missing"
    cell = rows[error : composers[0]]
    while cell and not cell[-1].strip():
        cell.pop()
    assert all(row and not row[0].isspace() for row in cell), "native_cell_boundary_invalid"
    if case == "auth":
        assert len(cell) == 2 and re.fullmatch(r"http://127\.0\.0\.1:\d+/v1/responses", cell[1])
    else:
        assert len(cell) == 3 and cell[1].startswith("visit https://chatgpt.com/settings/usage ")
        assert cell[2].startswith("at ") and cell[2].endswith(".")
    assert not any("Worked for" in row for row in rows[reply:]), "unexpected_native_completion"
    return {
        "partial_reply_observed": True,
        "wrapped_native_error_observed": True,
        "native_finished_marker_absent": True,
        "wrapped_cell_rows": len(cell),
    }
