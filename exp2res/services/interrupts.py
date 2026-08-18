"""§14.14 rule 6 interrupt deferral across the commit-to-report window."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator
import signal


_DEFERRABLE = hasattr(signal, "pthread_sigmask")
_owned = False
_deferred = False


@contextmanager
def interrupt_boundary() -> Iterator[None]:
    """Own every deferral one command takes, and release it on the way out.

    Blocking a signal is process-global, so the right to do it belongs to
    whoever will report the cancellation. Inside this scope `defer_interrupt`
    holds SIGINT; outside it — a service called from a test, a library,
    another tool — the call is inert and ordinary Ctrl-C semantics stand,
    because nothing there would ever unblock what it masked.
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

    Cancellation is not lost, only sequenced: the boundary reports the signal
    once the envelope exists, and the command still ends cancelled. The
    deferred span reaches from the commit to that envelope — a lock release, a
    managed-set removal, one dataclass — and only SIGINT is held, so a process
    that stalls inside it is still signallable.

    Where the platform has no signal mask this is inert, and the carried
    projection on the raised exception remains the report.
    """

    global _deferred
    if _DEFERRABLE and _owned and not _deferred:
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
        _deferred = True


def interrupt_pending() -> bool:
    """Report whether a deferred SIGINT is waiting, without delivering it.

    The §14.14 boundary asks this to classify an outcome that is already
    built, and keeps the deferral armed until that envelope is out: delivering
    the signal to decide the classification would lose the report it decided.
    """

    return _deferred and signal.SIGINT in signal.sigpending()


def resume_interrupt() -> bool:
    """Resume delivery, reporting whether a SIGINT arrived while deferred.

    The pending signal is consumed rather than delivered: it belongs to a
    command whose committed work is already reported, so nothing is left for a
    `KeyboardInterrupt` to unwind and raising one would only discard the
    envelope on its way out.

    This resets rather than unwinds a depth, because the command boundary is
    the single caller and a deferral that outlived its command would silently
    make the next one uninterruptible.
    """

    global _deferred
    if not _deferred:
        return False
    _deferred = False
    observed = signal.SIGINT in signal.sigpending()
    if observed:
        signal.sigwait({signal.SIGINT})
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT})
    return observed
