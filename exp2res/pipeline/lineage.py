"""Pure retained-row planning for §13.3 correction lineages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
import sqlite3

from exp2res.domain.models import EvidenceItem, RawLog
from exp2res.errors import IntegrityFailureError, SelectorNotFoundError
from exp2res.llm.fact_extractor import (
    DisplacedSupportDescriptor,
    FactExtractorInput,
)
from exp2res.storage.repository import hydrate_evidence_item, hydrate_raw_log


def _id_key(value: str) -> bytes:
    return value.encode("utf-8")


def _log_order(log: RawLog) -> tuple[object, bytes]:
    return (log.recorded_at.astimezone(timezone.utc), _id_key(log.id))


@dataclass(frozen=True)
class LineageContext:
    """One deterministic Stage 3 planning unit and its lookup closure."""

    root_id: str
    member_ids: tuple[str, ...]
    effective_logs: tuple[RawLog, ...]
    evidence_items: tuple[EvidenceItem, ...]
    displaced_support_items: tuple[DisplacedSupportDescriptor, ...]
    item_to_log_id: dict[str, str]
    item_to_strength: dict[str, str]
    effective_log_by_id: dict[str, RawLog]

    def extractor_input(self) -> FactExtractorInput:
        return FactExtractorInput(
            raw_logs=list(self.effective_logs),
            evidence_items=list(self.evidence_items),
            displaced_support_items=list(self.displaced_support_items),
        )


def _snapshot_roots(logs_by_id: dict[str, RawLog]) -> dict[str, str]:
    """Resolve every root with repository.lineage_root's fail-closed walk."""

    roots: dict[str, str] = {}
    for raw_log_id in logs_by_id:
        if raw_log_id in roots:
            continue
        current = raw_log_id
        path: list[str] = []
        seen: set[str] = set()
        while current not in roots:
            if current in seen:
                raise IntegrityFailureError()
            seen.add(current)
            path.append(current)
            log = logs_by_id.get(current)
            if log is None:
                raise IntegrityFailureError()
            if log.corrects_log_id is None:
                root = current
                break
            current = log.corrects_log_id
        else:
            root = roots[current]
        for member in path:
            roots[member] = root
    return roots


def plan_lineages(
    connection: sqlite3.Connection, *, log_id: str | None
) -> tuple[LineageContext, ...]:
    """Plan every lineage, or exactly the lineage containing ``log_id``."""

    raw_rows = connection.execute("SELECT * FROM raw_logs").fetchall()
    logs = tuple(hydrate_raw_log(row) for row in raw_rows)
    logs_by_id = {log.id: log for log in logs}
    if len(logs_by_id) != len(logs):
        raise IntegrityFailureError()
    if log_id is not None and log_id not in logs_by_id:
        raise SelectorNotFoundError()

    roots = _snapshot_roots(logs_by_id)
    displaced_ids = {
        log.corrects_log_id
        for log in logs
        if log.corrects_log_id is not None
    }

    evidence_rows = connection.execute("SELECT * FROM evidence_items").fetchall()
    all_items = tuple(hydrate_evidence_item(row) for row in evidence_rows)
    items_by_log: dict[str, list[EvidenceItem]] = {raw_id: [] for raw_id in logs_by_id}
    for item in all_items:
        if item.raw_log_id not in items_by_log:
            raise IntegrityFailureError()
        items_by_log[item.raw_log_id].append(item)

    members_by_root: dict[str, list[RawLog]] = {}
    for log in logs:
        members_by_root.setdefault(roots[log.id], []).append(log)

    contexts: list[LineageContext] = []
    for root_id, unsorted_members in members_by_root.items():
        root = logs_by_id[root_id]
        members = [root] + sorted(
            (member for member in unsorted_members if member.id != root_id),
            key=_log_order,
        )
        effective_logs = tuple(
            member for member in members if member.id not in displaced_ids
        )
        effective_ids = {log.id for log in effective_logs}
        effective_items = sorted(
            (
                item
                for raw_id in effective_ids
                for item in items_by_log[raw_id]
            ),
            key=lambda item: _id_key(item.id),
        )
        displaced_items = sorted(
            (
                item
                for member in members
                if member.id in displaced_ids
                for item in items_by_log[member.id]
                if item.strength != "manual_claim"
            ),
            key=lambda item: _id_key(item.id),
        )
        descriptors = tuple(
            DisplacedSupportDescriptor(
                id=item.id,
                raw_log_id=item.raw_log_id,
                strength=item.strength,
                uri=item.uri,
                path=item.path,
            )
            for item in displaced_items
        )
        selectable = (*effective_items, *displaced_items)
        if len({item.id for item in selectable}) != len(selectable):
            raise IntegrityFailureError()
        contexts.append(
            LineageContext(
                root_id=root_id,
                member_ids=tuple(member.id for member in members),
                effective_logs=effective_logs,
                evidence_items=tuple(effective_items),
                displaced_support_items=descriptors,
                item_to_log_id={item.id: item.raw_log_id for item in selectable},
                item_to_strength={item.id: item.strength for item in selectable},
                effective_log_by_id={log.id: log for log in effective_logs},
            )
        )

    contexts.sort(key=lambda context: _log_order(logs_by_id[context.root_id]))
    if log_id is not None:
        selected_root = roots[log_id]
        return tuple(context for context in contexts if context.root_id == selected_root)
    return tuple(contexts)
