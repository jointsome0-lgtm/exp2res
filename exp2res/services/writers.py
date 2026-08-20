"""Shared writer-side plumbing: held-or-acquired writer authority and transactions."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager, nullcontext
from typing import Any, Callable, ContextManager, Iterator

from exp2res.errors import WorkspaceBusyError


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
