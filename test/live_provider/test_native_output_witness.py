"""ANSI patches, stale markers and byte-range ownership must not fool the observer."""

from test.live_provider.native_output_witness import NativeOutputWitness

import pytest

pytestmark = pytest.mark.offline_database
PREFIX = "T223-progress-"
FIRST = b"\x1b[2J\x1b[H" + (PREFIX + "0001").encode()
PATCH = f"\x1b[1;{len(PREFIX)+3}H55".encode()


def reader(path, mutation=None):
    def request(method, tid, suffix, params):
        assert (method, tid, suffix) == ("GET", "owned", "/output/range")
        raw = path.read_bytes()[params["offset"] : params["offset"] + params["length"]]
        value = dict(
            terminal_id=tid,
            offset=params["offset"],
            length=params["length"],
            data=raw.decode("utf-8", errors="replace"),
        )
        if mutation:
            value[mutation] = "wrong"
        return value

    return request


def test_cursor_patch_is_new_semantic_output_without_a_repeated_raw_literal(tmp_path):
    path = tmp_path / "log"
    path.write_bytes(FIRST)
    witness = NativeOutputWitness(reader(path), "owned", path, len(FIRST))
    path.write_bytes(FIRST + PATCH)
    assert (PREFIX + "0055").encode() not in PATCH
    assert witness.observe(PREFIX + "0055") == len(PATCH)
    assert witness.offset == len(FIRST) + len(PATCH)


def test_old_visible_marker_with_unrelated_new_bytes_is_rejected(tmp_path):
    path = tmp_path / "log"
    path.write_bytes(FIRST)
    witness = NativeOutputWitness(reader(path), "owned", path, len(FIRST))
    with path.open("ab") as out:
        out.write(b"\x1b[?25l")
    with pytest.raises(AssertionError, match="already_visible"):
        witness.observe(PREFIX + "0001")


@pytest.mark.parametrize("mutation", ["terminal_id", "offset", "length", "data"])
def test_api_range_must_match_owned_physical_bytes(tmp_path, mutation):
    path = tmp_path / "log"
    path.write_bytes(FIRST)
    with pytest.raises(AssertionError):
        NativeOutputWitness(reader(path, mutation), "owned", path, len(FIRST))


@pytest.mark.parametrize("change", ["replace", "truncate"])
def test_windowed_utf8_and_replaced_or_truncated_file_fail_closed(tmp_path, change):
    path = tmp_path / "log"
    raw = b" " * 65535 + "é".encode() + FIRST + PATCH
    path.write_bytes(raw)
    witness = NativeOutputWitness(reader(path), "owned", path, 0)
    assert witness.observe(PREFIX + "0055") == len(raw)
    assert "�" not in witness._screen()
    if change == "replace":
        path.rename(tmp_path / "old-log")
        path.write_bytes(raw)
    else:
        path.write_bytes(b"")
    with pytest.raises(AssertionError):
        witness.observe(PREFIX + "0060")


def test_title_escape_containing_a_marker_is_not_visible_progress(tmp_path):
    path = tmp_path / "log"
    path.write_bytes(b"\x1b]0;T223-progress-0055\x07")
    witness = NativeOutputWitness(reader(path), "owned", path, 0)
    assert witness.observe(PREFIX + "0055") is False
