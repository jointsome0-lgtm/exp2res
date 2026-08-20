"""Shared writer-side plumbing: held-or-acquired writer authority and transactions."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager, nullcontext
from typing import Any, Callable, ContextManager, Iterator, TypeVar

from exp2res.errors import IdCollisionError, WorkspaceBusyError

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


@contextmanager
def business_transaction(
    connection: sqlite3.Connection,
) -> Iterator[sqlite3.Connection]:
    """`transaction` that reports SQLite contention as §14.14 `workspace_busy`."""

    try:
        with transaction(connection) as held:
            yield held
    except sqlite3.OperationalError as error:
        if is_busy(error):
            raise WorkspaceBusyError() from error
        raise


@contextmanager
def banked_transaction(
    connection: sqlite3.Connection,
    bank: Callable[[], None] | None = None,
    *,
    on_commit_error: Callable[[], None] | None = None,
) -> Iterator[sqlite3.Connection]:
    """`business_transaction` that runs `bank()` once the commit is durable — also
    when the exception lands as `commit()` returns (§14.14 rule 6).
    `on_commit_error()` runs first whenever `commit()` itself raised."""

    # `in_transaction` is false before BEGIN too, so a flag guards the bank.
    commit_reached = False
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        commit_reached = True
        connection.commit()
    except sqlite3.OperationalError as error:
        if commit_reached and on_commit_error is not None:
            on_commit_error()
        connection.rollback()
        if is_busy(error):
            raise WorkspaceBusyError() from error
        raise
    except BaseException:
        if commit_reached and on_commit_error is not None:
            on_commit_error()
        if connection.in_transaction or not commit_reached:
            connection.rollback()
            raise
        if bank is not None:
            bank()
        raise
    if bank is not None:
        bank()


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
