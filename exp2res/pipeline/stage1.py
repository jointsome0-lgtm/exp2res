"""§13.1 atomic raw-capture and evidence persistence."""

from __future__ import annotations

import sqlite3
from typing import Callable

from exp2res.domain.models import EvidenceItem, RawLog
from exp2res.errors import WorkspaceBusyError
from exp2res.storage.repository import insert_evidence_item, insert_raw_log
from exp2res.storage.workspace import DEFAULT_BUSY_TIMEOUT_MS, writer_database


FailureHook = Callable[[], None]
CommittedHook = Callable[[BaseException], None]


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

    `on_committed` receives anything raised once the pair is durable, so the
    caller can record what §14.14 rule 6 owes the envelope. Releasing the lock
    and closing the connection are the longest part of that window, and they
    happen after the commit that made the rows permanent — an interrupt landing
    there would otherwise leave as a bare cancellation naming nothing.
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
                connection.commit()
                committed = True
            except sqlite3.OperationalError as error:
                connection.rollback()
                if "locked" in str(error).lower() or "busy" in str(error).lower():
                    raise WorkspaceBusyError() from error
                raise
            except BaseException:
                if not committed:
                    connection.rollback()
                raise
    except BaseException as error:
        if committed and on_committed is not None:
            on_committed(error)
        raise
