"""§13.1 atomic raw-capture and evidence persistence."""

from __future__ import annotations

import sqlite3
from typing import Callable

from exp2res.domain.models import EvidenceItem, RawLog
from exp2res.errors import WorkspaceBusyError
from exp2res.storage.repository import insert_evidence_item, insert_raw_log
from exp2res.storage.workspace import DEFAULT_BUSY_TIMEOUT_MS, writer_database


FailureHook = Callable[[], None]


def persist_manual_capture(
    workspace,
    *,
    raw_log: RawLog,
    evidence_item: EvidenceItem,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    after_raw_insert: FailureHook | None = None,
) -> None:
    """Commit exactly one validated RawLog/EvidenceItem pair or neither."""
    with writer_database(workspace, timeout_ms=timeout_ms) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            insert_raw_log(connection, raw_log)
            if after_raw_insert is not None:
                after_raw_insert()
            insert_evidence_item(connection, evidence_item)
            connection.commit()
        except sqlite3.OperationalError as error:
            connection.rollback()
            if "locked" in str(error).lower() or "busy" in str(error).lower():
                raise WorkspaceBusyError() from error
            raise
        except BaseException:
            connection.rollback()
            raise
