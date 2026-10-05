"""Cooperative cancellation for a read-only scan, without partial commits."""
from contextlib import contextmanager
import threading


class ScanCancelled(Exception):
    pass


class ScanControl:
    def __init__(self):
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._progress = {"phase": "Discovering", "path": "", "directories": 0,
                          "repositories": 0, "inspected": 0}

    def cancel(self):
        self.cancelled.set()

    def check(self):
        if self.cancelled.is_set():
            raise ScanCancelled("Scan cancelled; inventory unchanged")

    def update(self, *, phase=None, path=None, directories=0, repositories=0, inspected=0):
        self.check()
        with self._lock:
            if phase is not None:
                self._progress["phase"] = phase
            if path is not None:
                self._progress["path"] = str(path)
            for key, amount in (("directories", directories), ("repositories", repositories),
                                ("inspected", inspected)):
                self._progress[key] += amount

    def snapshot(self):
        with self._lock:
            return {**self._progress, "cancelling": self.cancelled.is_set()}


_local = threading.local()


@contextmanager
def active_scan(control):
    previous = getattr(_local, "control", None)
    _local.control = control
    try:
        checkpoint()
        yield
    finally:
        _local.control = previous


def current_control():
    return getattr(_local, "control", None)


def checkpoint():
    control = current_control()
    if control is not None:
        control.check()


def progress(**values):
    control = current_control()
    if control is not None:
        control.update(**values)
