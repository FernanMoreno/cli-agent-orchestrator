"""Ephemeral owned-child identity; never a TCP or retirement capability."""

import copy
import importlib
import importlib.util
import pickle
from concurrent.futures import ThreadPoolExecutor
from test.services.test_resource_process_supervisor import api as supervisor_api
from test.services.test_resource_process_supervisor import child

import pytest

pytestmark = pytest.mark.offline_database


def api():
    name = "cli_agent_orchestrator.models.sql_child_subject"
    assert importlib.util.find_spec(name) is not None, "missing verified live child subject"
    return importlib.import_module(name)


@pytest.mark.parametrize("copying", [copy.copy, copy.deepcopy, pickle.dumps])
def test_subject_is_live_only_inside_original_owner_handshake(copying):
    module = api()
    captured = []
    with child() as owner:

        def inspect(subject):
            assert type(subject) is module.VerifiedChildSqlSubject
            subject.check()
            assert subject.binding.process_pid == owner._process.pid
            with pytest.raises(TypeError):
                copying(subject)
            with ThreadPoolExecutor(max_workers=1) as pool:
                with pytest.raises(ValueError):
                    pool.submit(subject.check).result(timeout=10)
            captured.append(subject)

        bound = owner._receive_binding(timeout_s=30, before_ack=inspect)
        assert captured[0].binding is bound
        with pytest.raises(ValueError):
            captured[0].check()


def test_subject_constructor_cannot_mint_positive_identity():
    module = api()
    with pytest.raises(TypeError):
        module.VerifiedChildSqlSubject()


def test_subject_claim_is_one_shot_but_can_still_be_revalidated():
    api()
    with child() as owner:

        def inspect(subject):
            subject.claim()
            subject.check()
            with pytest.raises(ValueError):
                subject.claim()

        owner._receive_binding(timeout_s=30, before_ack=inspect)


@pytest.mark.parametrize("error", [RuntimeError, MemoryError, BaseException])
def test_any_callback_failure_expires_subject_and_prevents_ack(error):
    api()
    captured = []
    original = error("controlled before-ACK failure")
    with child() as owner:

        def failed(subject):
            captured.append(subject)
            raise original

        with pytest.raises(error) as caught:
            owner._receive_binding(timeout_s=30, before_ack=failed)
        assert caught.value is original and owner._binding_failed
        assert owner.observation is None
        with pytest.raises(ValueError):
            captured[0].check()


def test_reentrant_handshake_cannot_grant_an_ack():
    api()
    with child() as owner:

        def reenter(subject):
            with pytest.raises(supervisor_api().ResourceProcessUnproven):
                owner.receive_binding(timeout_s=0)

        with pytest.raises(supervisor_api().ResourceProcessUnproven):
            owner._receive_binding(timeout_s=30, before_ack=reenter)
        assert owner._binding_failed and owner.observation is None


def test_late_recorder_cannot_relabel_a_previously_acknowledged_binding():
    api()
    with child() as owner:
        original = owner.receive_binding(timeout_s=30)
        with pytest.raises(supervisor_api().ResourceProcessUnproven):
            owner._receive_binding(
                timeout_s=0, before_ack=lambda subject: pytest.fail("late record")
            )
        assert owner.receive_binding(timeout_s=0) is original


def test_forked_caller_cannot_revalidate_the_parent_subject():
    import os
    import select
    import signal

    api()
    with child() as owner:

        def inspect(subject):
            reader, writer = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(reader)
                try:
                    with pytest.raises(ValueError):
                        subject.check()
                    os.write(writer, b"denied")
                    os._exit(0)
                except BaseException:
                    os._exit(1)
            os.close(writer)
            pin = os.pidfd_open(pid)
            ready = []
            try:
                ready, _, _ = select.select([reader], [], [], 10)
                assert ready and os.read(reader, 16) == b"denied"
            finally:
                os.close(reader)
                if not ready:
                    signal.pidfd_send_signal(pin, signal.SIGKILL)
                os.close(pin)
                assert os.waitpid(pid, 0) == (pid, 0)
            subject.check()

        owner._receive_binding(timeout_s=30, before_ack=inspect)


def test_expired_subject_cannot_be_revived_by_mutating_its_flags_or_validator():
    api()
    captured = []
    with child() as owner:
        owner._receive_binding(timeout_s=30, before_ack=lambda subject: captured.append(subject))
        subject = captured[0]
        object.__setattr__(subject, "_active", True)
        object.__setattr__(subject, "_validate", lambda candidate: None)
        with pytest.raises(ValueError, match="issued"):
            subject.check()


def test_non_none_callback_result_is_not_an_acknowledgement():
    api()
    with child() as owner:
        with pytest.raises(supervisor_api().ResourceProcessUnproven):
            owner._receive_binding(
                timeout_s=30, before_ack=lambda subject: "untrusted success text"
            )
        assert owner._binding_failed and owner.observation is None


def test_registry_expiration_failure_still_closes_the_channel(monkeypatch):
    module = api()
    captured = []

    def failed_pop(*args):
        raise MemoryError("controlled weak registry failure")

    with child() as owner:
        with monkeypatch.context() as patch:
            patch.setattr(module._ISSUED, "pop", failed_pop)
            with pytest.raises(MemoryError):
                owner._receive_binding(
                    timeout_s=30, before_ack=lambda subject: captured.append(subject)
                )
        assert owner._channel.fileno() == -1 and not owner._receiving
        assert not owner._issued_subjects and owner._subject_token is None
        with pytest.raises(ValueError):
            captured[0].check()
        module._expire_verified_child_sql_subject(captured[0])
