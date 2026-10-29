"""Opt-in opencode adapter for the shared native cancellation contract."""

import os
from test.live_provider.native_cancel_adapters import OPENCODE
from test.live_provider.native_concurrent_cancel import run_native_cancel_case

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("CAO_T220_ROOT"), reason="requires a fresh source-pinned descriptor"
)


def test_native_cancel_a_while_b_continues(postgres_service):
    run_native_cancel_case(os.environ["CAO_T220_ROOT"], postgres_service, OPENCODE)
