"""§13.1 atomic raw-capture and evidence persistence."""

from __future__ import annotations

import sqlite3
from typing import Callable

from exp2res.domain.models import EvidenceItem, RawLog
from exp2res.errors import WorkspaceBusyError
from exp2res.services.interrupts import defer_interrupt
from exp2res.storage.repository import insert_evidence_item, insert_raw_log
from exp2res.storage.workspace import DEFAULT_BUSY_TIMEOUT_MS, writer_database


FailureHook = Callable[[], None]
CommittedHook = Callable[[BaseException], None]


def _capture_is_durable(connection, raw_log_id: str) -> bool | None:
    """Answer from the database whether the pair survived, or None if unknown.

    `commit()` can raise after SQLite has already made the transaction
    durable, so the flag an assignment sets afterwards is not the question
    §14.14 rule 6 asks — the stored row is. Unknown is not committed: an
    identity nothing can prove is never named.
    """

    try:
        if connection.in_transaction:
            return False
        row = connection.execute(
            "SELECT 1 FROM raw_logs WHERE id = ?", (raw_log_id,)
        ).fetchone()
        return row is not None
    except Exception:
        return None


def persist_manual_capture(
    workspace,
    *,
    raw_log: RawLog,
    evidence_items: tuple[EvidenceItem, ...],
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    after_raw_insert: FailureHook | None = None,
    on_committed: CommittedHook | None = None,
) -> None:
    """Commit one validated RawLog and its ordered evidence bundle or neither.

    Delivery of SIGINT is deferred from before the commit, so the rows cannot
    become durable in a window where nothing has recorded them; the §14.14
    boundary resumes delivery once the envelope exists. `on_committed` receives
    anything else raised after that point — a managed-cleanup failure, a lock
    the platform could not release, a commit that failed after durability — so
    the caller can still record what rule 6 owes the envelope.
    """
    committed = False
    try:
        with writer_database(workspace, timeout_ms=timeout_ms) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                insert_raw_log(connection, raw_log)
                if after_raw_insert is not None:
                    after_raw_insert()
                for evidence_item in evidence_items:
                    insert_evidence_item(connection, evidence_item)
                defer_interrupt()
                connection.commit()
                committed = True
            except BaseException as error:
                durable = _capture_is_durable(connection, raw_log.id)
                committed = bool(durable)
                if durable is False:
                    connection.rollback()
                if isinstance(error, sqlite3.OperationalError) and (
                    "locked" in str(error).lower() or "busy" in str(error).lower()
                ):
                    raise WorkspaceBusyError() from error
                raise
    except BaseException as error:
        if committed and on_committed is not None:
            on_committed(error)
        raise
