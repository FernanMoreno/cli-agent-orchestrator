"""Pure standard-library sanitization for single-line log values."""

import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_C0_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # keep \t (0x09), drop \n/\r via escape
# Unicode line/paragraph separators that some markdown
# renderers and structured-log parsers honour as line breaks. Stripping
# these defeats forged-line audit-log injection. Replacement matches
# the existing \r/\n policy: literal ``\\n``.
_UNICODE_LINEBREAK_RE = re.compile("[\u0085\u2028\u2029]")
# Strip lone surrogates U+D800-U+DFFF.
_LONE_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def _sanitize_for_log(s: str, max_len: int = 200) -> str:
    """Make a string safe for a single-line audit/log entry.

    Strips ANSI, drops C0 controls (preserving tabs), escapes ``\\n``/``\\r`` to
    their literal two-char sequences, truncates with an ellipsis suffix.
    Exported for the audit log to reuse.
    """
    out = _ANSI_RE.sub("", s or "")
    out = out.replace("\r", "\\r").replace("\n", "\\n")
    # Strip Unicode line separators (NEL, LS, PS) that
    # would otherwise smuggle line breaks past the \r/\n escape above.
    # VT/FF are already covered by the C0 strip below; this regex only
    # has to handle U+0085, U+2028, U+2029.
    out = _UNICODE_LINEBREAK_RE.sub("\\\\n", out)
    out = _LONE_SURROGATE_RE.sub("", out)  # lone surrogates
    out = _C0_RE.sub("", out)
    if len(out) > max_len:
        out = out[: max_len - 1] + "…"
    return out
