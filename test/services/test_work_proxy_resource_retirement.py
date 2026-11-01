"""A failed parent gate must join its own proxy before positive resource closure."""

from dataclasses import replace
from test.clients.test_work_bubblewrap_process_identity import _identity
from test.security.test_work_bubblewrap_bound_content import _ack_for_stage, _bound_worker
from test.services.test_delivery_admission import create, observe
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services import work_bubblewrap_composition as composition
from cli_agent_orchestrator.services.work_process_effect import (
    begin_native_effect,
    confirm_native_effect_closed,
    observe_native_effect,
    physical_effect_scope,
)


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("held_alive", [False, True])
def test_parent_gate_error_joins_proxy_and_never_overrides_a_live_thread(
    tmp_path, monkeypatch, held_alive, automatic
):
    repository, binding, _, identity = _bound_worker(tmp_path)
    joins = []
    threads = []
    proof = SimpleNamespace(close=lambda: None)
    proxy = SimpleNamespace(_isolation_proof=None, closed=False)
    proxy.create_bound_attempt = lambda **kwargs: SimpleNamespace(
        worker_fd=37, worker_socket_identity=(1, 2)
    )
    proxy.activate_with_isolation_proof = lambda endpoint, value: setattr(
        proxy, "_isolation_proof", value
    )
    proxy.serve = lambda *args: None
    proxy.close = lambda: setattr(proxy, "closed", True)

    class ProxyThread:
        def __init__(self, **kwargs):
            self.alive = False
            self.ident = None
            threads.append(self)

        def start(self):
            self.alive = True
            self.ident = 1

        def is_alive(self):
            return self.alive

        def join(self, timeout):
            assert proxy.closed
            joins.append(timeout)
            self.alive = held_alive

    def release_failed(self, *args, **kwargs):
        assert len(threads) == 1 and threads[0].is_alive()
        raise ValueError("controlled refusal after proxy started")

    def launch_stage(staged, selected_identity, **kwargs):
        begin_native_effect()
        process_identity = _identity()
        ack = replace(
            _ack_for_stage(staged, process_identity),
            open_fds=(0, 1, 2, 3),
            proxy_socket_device=1,
            proxy_socket_inode=2,
        )
        try:
            try:
                kwargs["release_after_ack"](ack, process_identity, lambda: None, lambda: proof)
            except ValueError as error:
                raise composition.WorkBubblewrapParentGateFailed(
                    "controlled native cleanup confirmed", cleanup_confirmed=True
                ) from error
        finally:
            staged.close()

    owner = create()
    assert observe(owner, 2)
    lease = owner.try_reserve("proxy-owner", now=10.0).reservation
    assert lease is not None and lease.start()
    with monkeypatch.context() as patch:
        patch.setattr(composition.threading, "Thread", ProxyThread)
        patch.setattr(composition, "_launch_owned_stage", launch_stage)
        patch.setattr(composition.WorkBubblewrapSetupIntent, "release_if_current", release_failed)
        with physical_effect_scope(lease) as effect:
            observe_native_effect()
            effect.gate()
            with pytest.raises(composition.WorkBubblewrapParentGateFailed):
                try:
                    composition.launch_recorded_bound_work_static_elf(
                        repository,
                        binding.attempt_id,
                        1,
                        expected_attempt_revision=1,
                        command_token=identity.command_token,
                        bwrap_path="/unused-owned-test",
                        bwrap_sha256_digest="0" * 64,
                        mcp_proxy=proxy,
                    )
                except composition.WorkBubblewrapParentGateFailed as error:
                    assert error.cleanup_confirmed is True
                    confirm_native_effect_closed()
                    raise
    assert proxy.closed
    assert joins == [composition._PROXY_THREAD_JOIN_SECONDS]
    lease.finish_after_storage()
    assert owner.snapshot(now=10.0).inflight == int(held_alive)
    if held_alive:
        threads[0].alive = False
        token = next(iter(lease._physical_effects))
        if automatic:
            assert owner.retry_physical_closes().clean
            assert not token.finish()
        else:
            assert token.finish()
        assert owner.snapshot(now=10.0).inflight == 0
