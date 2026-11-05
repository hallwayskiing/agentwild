"""Who is in charge of a unit right now. Several jobs act on the same heroes (a squad hunts with them, the build crew
walks them to the tribe, the Abyss banks the main character's points); a job that takes a unit's lease is the only one
that moves it, and the others leave it alone until it is released. Leases end by themselves (`secs`) so a job that
dies never freezes a hero."""
import contextlib
import threading
import time

_lock = threading.Lock()
_owner = {}   # unit -> (job, expires)


def take(unit, job, secs=3600):
    """Lease `unit` to `job`. False if another job holds it."""
    with _lock:
        cur = _owner.get(unit)
        if cur and cur[0] != job and cur[1] > time.time():
            return False
        _owner[unit] = (job, time.time() + secs)
        return True


def release(unit, job):
    with _lock:
        if _owner.get(unit, (None,))[0] == job:
            del _owner[unit]


def holder(unit):
    with _lock:
        cur = _owner.get(unit)
        return cur[0] if cur and cur[1] > time.time() else None


def leased(unit, by=None):
    """Is the unit held by a job other than `by` (the caller)?"""
    h = holder(unit)
    return h is not None and h != by


@contextlib.contextmanager
def hold(units, job, secs=3600):
    """Lease all `units` for the block; units already taken elsewhere are skipped (the block gets the ones it won)."""
    got = [u for u in units if take(u, job, secs)]
    try:
        yield got
    finally:
        for u in got:
            release(u, job)
