"""Closed diagnostic cases, independent of native output and API discovery."""

import re
from urllib.parse import urlencode

_CASES = {
    "cross_session": ({"cross_session": True}, ("matching", "other-session"), "authorized", 200),
    "depth_one": ({"depth": 1}, ("matching", "wrong-group"), "authorized", 200),
    "depth_clamp": ({"depth": 999}, ("matching",), "authorized", 200),
    "depth_zero": ({"depth": 0}, (), "invalid", 422),
    "marker_denied": ({"cross_session": True}, (), "denied", None),
}


def discovery_case(variant):
    if variant not in _CASES:
        raise ValueError("unknown_discovery_variant")
    query, labels, mode, status = _CASES[variant]
    return {"query": dict(query), "labels": labels, "mode": mode, "status": status}


def discovery_path(caller, variant=""):
    if not isinstance(caller, str) or re.fullmatch(r"[0-9a-f]{8}", caller) is None:
        raise ValueError("invalid_discovery_caller")
    path = f"/terminals/{caller}/siblings"
    if variant:
        query = discovery_case(variant)["query"]
        path += "?" + urlencode(
            {k: str(v).lower() if isinstance(v, bool) else v for k, v in query.items()}
        )
    return path


def build_discovery_expectation(caller, records, variant):
    case = discovery_case(variant)
    assert len(records) == 3
    labels = {r["metadata"]["fixture"]: r for r in records}
    assert set(labels) == {"matching", "wrong-group", "other-session"}
    expected = [
        {k: labels[label][k] for k in ("id", "group", "metadata")} for label in case["labels"]
    ]
    selected = {row["id"] for row in expected}
    return {
        "schema": "caos.native-discovery-fixture.v2",
        "variant": variant,
        "caller_id": caller,
        "expected": expected,
        "excluded_ids": [caller] + [r["id"] for r in records if r["id"] not in selected],
    }
