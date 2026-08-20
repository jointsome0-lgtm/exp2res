"""Shared writer-side plumbing: held-or-acquired writer authority and transactions."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from typing import Any, Callable, ContextManager, Iterable, Iterator, TypeVar

from exp2res.errors import IdCollisionError, WorkspaceBusyError
from exp2res.services.privacy import sorted_paths

T = TypeVar("T")


def held_writer(
    connection: sqlite3.Connection | None,
    open_writer: Callable[..., ContextManager[sqlite3.Connection]],
    *args: Any,
    **kwargs: Any,
) -> ContextManager[sqlite3.Connection]:
    """§8.1: reuse the writer authority a caller already holds, else acquire one.

    `open_writer` is passed by the caller so the name it resolves (and tests
    replace) is the caller's own `writer_database`.
    """

    if connection is not None:
        return nullcontext(connection)
    return open_writer(*args, **kwargs)


def is_busy(error: sqlite3.OperationalError) -> bool:
    text = str(error).lower()
    return "locked" in text or "busy" in text


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE … commit, rolling back on any exit — including a signal."""

    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


@dataclass
class Journal:
    """What one `operation` did so far (§14.14 rule 6): `unlinks` are the durable
    filesystem effects as they happen, `committed` whether COMMIT ended the
    transaction, `residuals` the paths no step proved removed, and `pending`
    the paths a step still in flight has not proven yet."""

    unlinks: list[str] = field(default_factory=list)
    committed: bool = False
    residuals: list[str] = field(default_factory=list)
    pending: tuple[str, ...] = ()

    @property
    def unresolved(self) -> tuple[str, ...]:
        """§14.14 rule 5: residuals plus in-flight paths not already unlinked."""

        unlinked = set(self.unlinks)
        return sorted_paths(
            (*self.residuals, *(path for path in self.pending if path not in unlinked))
        )


class Op:
    """One journaled, cancellable write operation; see `operation`."""

    def __init__(self, connection: sqlite3.Connection, journal: Journal) -> None:
        self.connection = connection
        self.journal = journal

    def _in_flight(
        self, fn: Callable[[], Iterable[str]], pending: tuple[str, ...]
    ) -> None:
        self.journal.pending = pending
        self.journal.residuals.extend(fn())
        self.journal.pending = ()

    def remove(
        self, fn: Callable[..., Iterable[str]], *args: Any, fallback: str, **kwargs: Any
    ) -> None:
        """Run `fn(*args, removed_ledger=journal.unlinks, **kwargs) -> residuals`.

        §13.13 rule 6: a filesystem failure, or an interrupt mid-pass, leaves
        `fallback` residual instead of blocking the operation."""

        try:
            self._in_flight(
                lambda: fn(*args, removed_ledger=self.journal.unlinks, **kwargs),
                (fallback,),
            )
        except OSError:
            self.journal.pending = ()
            self.journal.residuals.append(fallback)

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.connection.execute(sql, params)

    def commit(self) -> None:
        """Durable iff the transaction ended: SIGINT lands between bytecodes, so
        `commit()` either returned or did not (§14.14 rule 6)."""

        if self.journal.committed:
            return
        try:
            self.connection.commit()
        finally:
            self.journal.committed = not self.connection.in_transaction
            if not self.journal.committed:
                self.journal.pending = ()

    def after_commit(
        self, fn: Callable[[], Iterable[str]], *, unproven: Iterable[str] = ()
    ) -> None:
        """Commit, then run `fn() -> residuals`; from the commit until it
        returns, `unproven` paths stay residual (the WAL between commit and
        truncating checkpoint)."""

        self.journal.pending = tuple(unproven)
        self.commit()
        self._in_flight(fn, tuple(unproven))


@contextmanager
def operation(
    writer: ContextManager[sqlite3.Connection],
    *,
    on_rollback: Callable[[], None] | None = None,
) -> Iterator[Op]:
    """BEGIN IMMEDIATE … COMMIT over `writer`'s connection, journaled.

    SQLite contention → `WorkspaceBusyError`. Any `BaseException` — lock
    acquisition, body, commit, post-commit steps, result construction or
    teardown — rolls back unless committed, runs `on_rollback` iff not
    committed, and re-raises with the `Journal` attached as
    `error.operation_journal`; `KeyboardInterrupt` is never swallowed.
    """

    journal = Journal()
    try:
        with writer as connection:
            op = Op(connection, journal)
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield op
                op.commit()
            except BaseException:
                if not journal.committed:
                    if on_rollback is not None:
                        on_rollback()
                    connection.rollback()
                raise
    except BaseException as error:
        raised: BaseException = error
        if isinstance(error, sqlite3.OperationalError) and is_busy(error):
            raised = WorkspaceBusyError()
        raised.operation_journal = journal  # type: ignore[attr-defined]
        if raised is error:
            raise
        raise raised from error


def savepoint(
    connection: sqlite3.Connection, name: str, work: Callable[[], None]
) -> None:
    """Run `work` under SAVEPOINT `name`; an `IdCollisionError` rolls it back and re-raises."""

    connection.execute(f"SAVEPOINT {name}")
    try:
        work()
    except IdCollisionError:
        connection.execute(f"ROLLBACK TO {name}")
        connection.execute(f"RELEASE {name}")
        raise
    connection.execute(f"RELEASE {name}")


def retry_id_collisions(attempt: Callable[[int], T]) -> T:
    """§12 rule 11: three fresh-ID attempts; the last collision is the cause."""

    last_collision: IdCollisionError | None = None
    for index in range(3):
        try:
            return attempt(index)
        except IdCollisionError as error:
            last_collision = error
    raise IdCollisionError() from last_collision
