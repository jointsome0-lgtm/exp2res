"""§14.14 rule 6 interrupt deferral across the commit-to-report window."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator
import signal


_DEFERRABLE = hasattr(signal, "pthread_sigmask")
_owned = False
_deferred = False
_restore_mask: set[int] = set()
_pending_on_entry = False


@contextmanager
def interrupt_boundary() -> Iterator[None]:
    """Own every deferral one command takes, and release it on the way out.

    Blocking a signal is process-global, so the right to do it belongs to
    whoever will report the cancellation. Inside this scope `defer_interrupt`
    holds SIGINT; outside it — a service called from a test, a library,
    another tool — the call is inert and ordinary Ctrl-C semantics stand,
    because nothing there would ever unblock what it masked.

    The release here is the safety net for the paths that never reach the
    classification: whatever the command did, the mask the caller had is the
    mask it gets back.
    """

    global _owned
    outer = _owned
    _owned = True
    try:
        yield
    finally:
        _owned = outer
        resume_interrupt()


def defer_interrupt() -> None:
    """Stop delivering SIGINT until the enclosing boundary releases it.

    Rule 6 owes the envelope every effect the command committed, and each
    hand-off between the commit and that envelope is one bytecode wide: the
    flag set after `commit()` returns, the return that carries the bundle, the
    call that builds the outcome. Narrowing those windows cannot close them —
    a signal delivered in any of them leaves before anything has recorded what
    is now durable. Refusing delivery removes the window instead.

    Cancellation is not lost, only sequenced: `resume_interrupt` reports the
    signal once the outcome exists, and the command still ends cancelled. The
    deferred span reaches from the commit to that classification — a lock
    release, a managed-set removal, one dataclass — and only SIGINT is held,
    so a process that stalls inside it is still signallable.

    A SIGINT the caller had already blocked and left pending is recorded as
    theirs, so this command neither reports it as its own cancellation nor
    consumes it. Where the platform has no signal mask this is all inert, and
    the carried projection on the raised exception remains the report.
    """

    global _deferred, _restore_mask, _pending_on_entry
    if _DEFERRABLE and _owned and not _deferred:
        _pending_on_entry = signal.SIGINT in signal.sigpending()
        _restore_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
        _deferred = True


def resume_interrupt() -> bool:
    """Release the deferral, reporting whether it held a SIGINT.

    Reporting and releasing are one act because they cannot be two: a caller
    that asked first and unblocked later would freeze a classification while
    signals could still arrive behind it, and then discard them. The signal
    this returns is the signal it consumed, and every signal after the
    release is delivered normally — which is what keeps a command
    interruptible while it writes its envelope to a reader that has stopped
    reading.

    The consumed signal is not delivered: it belongs to a command whose
    committed work is already reported, and raising `KeyboardInterrupt` for it
    would only discard that report on its way out.

    The caller's mask is restored exactly, so an embedding that blocks SIGINT
    on its own account gets its policy back unchanged.
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
