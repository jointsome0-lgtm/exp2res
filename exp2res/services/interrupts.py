"""§14.14 rule 6 interrupt deferral across the commit-to-report window."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator
import signal
import threading


_DEFERRABLE = hasattr(signal, "pthread_sigmask")
_owned = False
_deferred = False
_restore_mask: set[int] = set()
_pending_on_entry = False


@contextmanager
def interrupt_boundary() -> Iterator[None]:
    """Own the command's SIGINT deferral; release it only after the envelope is out.

    Outside this scope `defer_interrupt` is inert (nothing would unmask).
    Not armed with other threads alive: a signal mask is per-thread, so a
    process-directed SIGINT would still land here as `KeyboardInterrupt`.
    """

    global _owned
    outer = _owned
    _owned = threading.active_count() == 1
    try:
        yield
    finally:
        _owned = outer
        resume_interrupt()


def defer_interrupt() -> None:
    """Hold SIGINT from commit to emitted envelope (§14.14 rule 6).

    The commit-to-report hand-offs are one bytecode wide, so the window is
    removed rather than narrowed; `interrupt_pending` sequences the
    cancellation after the outcome exists. A SIGINT the caller already left
    pending is theirs: neither reported nor consumed here.
    """

    global _deferred, _restore_mask, _pending_on_entry
    if _DEFERRABLE and _owned and not _deferred:
        _pending_on_entry = signal.SIGINT in signal.sigpending()
        _restore_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
        _deferred = True


def interrupt_pending() -> bool:
    """Report whether a deferred SIGINT is waiting, without delivering it."""

    return (
        _deferred
        and not _pending_on_entry
        and signal.SIGINT in signal.sigpending()
    )


def resume_interrupt() -> bool:
    """Release the deferral, reporting whether it held a SIGINT.

    The held signal is consumed, not delivered: the work is already reported,
    so a `KeyboardInterrupt` here would only discard the envelope.
    """

    global _deferred, _restore_mask, _pending_on_entry
    if not _deferred:
        return False
    _deferred = False
    restore, theirs = _restore_mask, _pending_on_entry
    _restore_mask, _pending_on_entry = set(), False
    held = not theirs and signal.SIGINT in signal.sigpending()
    if held:
        signal.sigwait({signal.SIGINT})
    signal.pthread_sigmask(signal.SIG_SETMASK, restore)
    return held
