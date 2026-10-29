"""Opt-in claude adapter for the shared native cancellation contract."""

import os
from test.live_provider.native_cancel_adapters import CLAUDE
from test.live_provider.native_concurrent_cancel import run_native_cancel_case

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("CAO_T223_ROOT"), reason="requires a fresh source-pinned descriptor"
)


def test_native_cancel_a_while_b_continues(postgres_service):
    run_native_cancel_case(os.environ["CAO_T223_ROOT"], postgres_service, CLAUDE)
