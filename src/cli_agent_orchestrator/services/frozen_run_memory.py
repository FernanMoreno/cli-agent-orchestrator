"""Resolve CAO memory once per run and freeze it (issue #583 Bolt 2, unit ``memory-resolve-once``).

FR-9's actual guarantee. Two units built the parts and left them inert: ``manifest-freeze`` wrote an
empty ``FrozenMemoryRecord`` and named this unit as its filler, and ``terminal-frozen-memory`` added
the optional pre-resolved parameter on the injection path with nothing to supply it. This module is
what resolves, records, and supplies — so that altering CAO memory after a failure cannot change what
a resumed run sees.

THE ORDER IS PERSIST-THEN-USE, AND IT IS THE WHOLE CORRECTNESS ARGUMENT. The record is written into
``manifest_json`` BEFORE the block is handed to a terminal. The two crash outcomes are not symmetric:

* crash AFTER persisting — the manifest records memory that may never have been used. An over-record:
  harmless, and detectable, because the run has a memory record and no terminal.
* crash AFTER using and before persisting — the run SAW memory that nothing recorded. That is FR-9's
  own Fail criterion, and it fails SILENTLY, because an empty memory record is indistinguishable from
  a run that never created a terminal. Worse, a later replay reads that empty record and falls through
  to LIVE memory, which is the exact drift the feature exists to prevent.

Only one of the two breaks the requirement, so the ordering is forced rather than preferred. It is the
same discipline the envelope already applies with redact-then-bound and hash-then-redact.

A FAILED PERSIST INJECTS NOTHING. That is not a separate policy — handing over a block whose record did
not persist IS the use-then-persist failure, reached by another route. The run proceeds with no memory:
a visible, explicable difference rather than an unrecorded one.

THE ONCE-PER-RUN FLAG IS THE RECORD'S PRESENCE, NOT ITS CONTENT. A run that legitimately resolved no
memories records ``""``, and ``""`` is a RESOLVED result — ``terminal_service`` discriminates on
``is None`` for exactly this reason. Were the flag "content is truthy", such a run would re-resolve at
every terminal and again on every resume, each time reading the live store. The flag is durable
(a column, not process state) because a resume can happen in a fresh process, and process-local
bookkeeping would forget and re-resolve — reintroducing the requirement's Fail criterion through
bookkeeping rather than logic.

THE RUN IS INJECTED WITH WHAT WAS STORED, not with what was resolved. The stored copy is redacted and
may be truncated, so it and the raw resolution are not always the same bytes — and only one of them can
be injected. Injecting the stored copy makes the original run and every replay see BYTE-IDENTICAL
context, which is FR-9 met strictly. Two consequences, both accepted deliberately:

* a workflow-driven terminal's memory block is REDACTED, where a non-workflow terminal's is not. That
  is a real behaviour difference, documented in ``docs/workflows.md``; it is also a security
  improvement, since a secret sitting in curated memory stops reaching the agent's context here.
* when the bound bites, the agent sees less memory than the live path would have given it, and
  ``memory.truncated`` records that it happened.

The alternative — full block on the first run, stored copy on a replay — diverges only on runs where
redaction or truncation bit, which is to say on runs with a lot of memory, which is to say it would be
found in production rather than in a test.

WHAT THIS MODULE DOES NOT DO. It does not hash, redact, bound or truncate (``execution_manifest`` owns
all four, and hashes BEFORE redacting so the hash identifies what was resolved). It does not decide
whether memory reaches an agent at all — the operator's kill switch governs that, and
``terminal_service`` honours it on the frozen path too. It does not resolve eagerly: a run that creates
no terminal resolves nothing, because an unused block is sensitive text kept for no reason.
"""

import logging
import re
from typing import Optional, Protocol

from cli_agent_orchestrator.services import execution_manifest, workflow_journal
from cli_agent_orchestrator.services.secret_gate import scan_for_secrets

logger = logging.getLogger(__name__)

#: What ``source`` records for a block resolved through the ordinary curated path. A fixed token
#: rather than free text, because it is compared across a resume and read by ``frozen-context-proof``.
MEMORY_SOURCE_CURATED = "cao-memory:curated"
_SNAPSHOT_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


class FrozenSnapshotMemoryUnavailable(RuntimeError):
    """An explicit snapshot reference could not safely yield terminal content."""


class AuthorizedSnapshotReader(Protocol):
    """Trusted capability that has already bound authority to a snapshot read."""

    def read_authorized(self, snapshot_id: str) -> str:
        """Return exact authorized content, including an authorized empty string."""


class WorkOrderSnapshotReader:
    """Read only the snapshot fixed in one currently authorized work-order binding.

    This is a server-composition capability, not a caller callback or authority
    grant.  It revalidates the stored order in the same read transaction that
    loads its snapshot, so a principal cannot substitute a snapshot belonging
    to another job, contract, or binding destination.
    """

    def __init__(self, contracts, binding):
        from cli_agent_orchestrator.models.work_contract import WorkContractBinding
        from cli_agent_orchestrator.services.work_contract import WorkContracts

        if type(contracts) is not WorkContracts or type(binding) is not WorkContractBinding:
            raise TypeError("durable work-order binding required")
        self._contracts = contracts
        self._binding = binding

    def read_authorized(self, snapshot_id: str) -> str:
        """Return UTF-8 bytes only after revalidating this exact durable order."""
        if not _is_snapshot_id(snapshot_id):
            raise FrozenSnapshotMemoryUnavailable("authorized frozen snapshot is unavailable")
        try:
            from cli_agent_orchestrator.services.delegation_snapshot import DelegationSnapshots

            with self._contracts.repository.read_snapshot() as connection:
                current = self._contracts._revalidate_order(
                    connection,
                    self._binding.attempt_id,
                    generation=self._binding.generation,
                )
                if current != self._binding or current.contract.snapshot.id != snapshot_id:
                    raise FrozenSnapshotMemoryUnavailable(
                        "authorized frozen snapshot is unavailable"
                    )
                snapshot = DelegationSnapshots._load_authorized(
                    connection,
                    connection.execute(
                        "SELECT * FROM work_delegation_snapshots WHERE id=?", (snapshot_id,)
                    ).fetchone(),
                )
                if (
                    snapshot.job_id != current.job_id
                    or snapshot.id != current.contract.snapshot.id
                    or snapshot.delivered_hash != current.contract.snapshot.delivered_hash
                ):
                    raise FrozenSnapshotMemoryUnavailable(
                        "authorized frozen snapshot is unavailable"
                    )
                return snapshot.content.decode("utf-8")
        except FrozenSnapshotMemoryUnavailable:
            raise
        except Exception:
            raise FrozenSnapshotMemoryUnavailable(
                "authorized frozen snapshot is unavailable"
            ) from None


def _is_snapshot_id(value: object) -> bool:
    return (
        type(value) is str and bool(_SNAPSHOT_ID.fullmatch(value)) and not scan_for_secrets(value)
    )


def resolve_frozen_memory(
    legacy_memory: str | None,
    *,
    snapshot_id: str | None = None,
    snapshot_reader: AuthorizedSnapshotReader | None = None,
) -> str | None:
    """Choose legacy content or an explicitly authorized snapshot, never a fallback mix."""
    if snapshot_id is None:
        return legacy_memory
    if not _is_snapshot_id(snapshot_id) or snapshot_reader is None:
        raise FrozenSnapshotMemoryUnavailable("authorized frozen snapshot is unavailable")
    try:
        reader = getattr(snapshot_reader, "read_authorized", None)
        if not callable(reader):
            raise TypeError("authorized snapshot reader required")
        content = reader(snapshot_id)
    except Exception:
        raise FrozenSnapshotMemoryUnavailable("authorized frozen snapshot is unavailable") from None
    if type(content) is not str:
        raise FrozenSnapshotMemoryUnavailable("authorized frozen snapshot is unavailable")
    return content


def _resolve_live(terminal_id: str, task_description: str) -> str:
    """Resolve memory the same way the live injection path does.

    Deliberately the SAME call ``terminal_service.inject_memory_context`` makes, including the 200
    character task-description slice: this unit changes WHEN memory is resolved, never WHAT
    resolution means. A separate resolution path here would make a replayed run's context differ from
    a live one for reasons unrelated to freezing.
    """
    from cli_agent_orchestrator.services.memory_service import MemoryService

    return MemoryService().get_curated_memory_context(
        terminal_id, task_description=task_description[:200]
    )


def frozen_memory_for(
    run_id: Optional[str],
    terminal_id: str,
    task_description: str = "",
    *,
    snapshot_id: str | None = None,
    snapshot_reader: AuthorizedSnapshotReader | None = None,
) -> Optional[str]:
    """The memory block this run's terminals must be given, or ``None`` for "resolve live".

    ``None`` means the caller should pass nothing to ``send_input`` and let today's live path run —
    which is the correct answer for a non-workflow terminal, for a YAML run, or for a run with no
    readable manifest. ``""`` means a manifest existed but its memory block could not be persisted;
    the caller must supply that empty block to prevent the terminal's live-memory fallback.

    The legacy route is TOTAL: it never raises, and a freeze fault returns the explicit empty
    block rather than unrecorded live memory.  An explicit ``snapshot_id`` is different: a
    missing, invalid or unauthorized selection raises ``FrozenSnapshotMemoryUnavailable`` so
    it cannot be silently reclassified as legacy/live content.
    """
    if snapshot_id is not None:
        # An explicit snapshot selection is not legacy data.  It either resolves
        # through a trusted, order-bound port or prevents terminal injection;
        # it must never become a live-memory fallback.
        return resolve_frozen_memory(
            None,
            snapshot_id=snapshot_id,
            snapshot_reader=snapshot_reader,
        )

    if not run_id:
        # Not a workflow terminal. Today's behaviour, untouched — C-1.
        return None

    try:
        row = workflow_journal.get_run(run_id)
    except Exception as e:  # noqa: BLE001 — a journal fault must not fail the step
        logger.warning("frozen memory: could not read run '%s' (resolving live): %s", run_id, e)
        return None

    if row is None or row.manifest_json is None:
        # A YAML run never freezes a manifest, and a script run whose freeze failed has none either.
        # Both mean "there is nothing frozen to honour", which is not the same as "no memory".
        return None

    envelope = execution_manifest.parse(row.manifest_json)
    if envelope is None:
        logger.warning(
            "frozen memory: run '%s' has an unreadable manifest (resolving live)", run_id
        )
        return None

    while True:
        if envelope.memory.source:
            # ``source`` is the durable resolved flag. Content may legitimately be "", and the
            # hash is always populated even before the lazy fill, so neither can identify a fill.
            return envelope.memory.content

        resolved = _resolve_live(terminal_id, task_description)
        filled = execution_manifest.with_memory(
            envelope, content=resolved, source=MEMORY_SOURCE_CURATED
        )

        try:
            persisted = workflow_journal.compare_and_set_run_manifest(
                run_id, row.manifest_json, execution_manifest.serialise(filled)
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "frozen memory: could not record the resolved block for run '%s'; the run proceeds "
                "with NO memory rather than using memory the manifest does not record: %s",
                run_id,
                e,
            )
            return ""

        if persisted:
            # The STORED copy — redacted, possibly truncated — so this run and every replay sees
            # the same bytes. Not `resolved`, which is the raw resolution.
            return filled.memory.content

        try:
            row = workflow_journal.get_run(run_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "frozen memory: could not reload run '%s' after a competing fill; "
                "the run proceeds with NO memory: %s",
                run_id,
                e,
            )
            return ""

        if row is None or row.manifest_json is None:
            logger.warning(
                "frozen memory: run '%s' disappeared after a competing fill; "
                "the run proceeds with NO memory",
                run_id,
            )
            return ""

        envelope = execution_manifest.parse(row.manifest_json)
        if envelope is None:
            logger.warning("frozen memory: run '%s' has an unreadable manifest", run_id)
            return ""
