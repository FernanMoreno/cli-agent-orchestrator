"""Opt-in claude adapter for the shared native cancellation contract."""

import json
import os
from pathlib import Path
from test.live_provider.native_cancel_adapters import claude_adapter_for_manifest
from test.live_provider.native_concurrent_cancel import run_native_cancel_case

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("CAO_T223_ROOT"), reason="requires a fresh source-pinned descriptor"
)


def test_native_cancel_a_while_b_continues(postgres_service):
    root = Path(os.environ["CAO_T223_ROOT"])
    manifest = json.loads((root / "manifest.json").read_text())
    adapter = claude_adapter_for_manifest(manifest)
    run_native_cancel_case(root, postgres_service, adapter)
