"""Single-process lock shared by the classic and Qt presentations."""
import os

from . import store

try:
    import msvcrt
except ImportError:
    msvcrt = None


_handle = None


def acquire():
    """Keep the existing Windows lock file open until process exit."""
    global _handle
    if msvcrt is None or _handle is not None:
        return True
    descriptor = os.open(store.APP_DIR / "repo_manager.lock", os.O_RDWR | os.O_CREAT, 0o600)
    handle = os.fdopen(descriptor, "r+", encoding="ascii")
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return False
    try:
        handle.seek(0)
        handle.write(str(os.getpid()))
        handle.truncate()
        handle.flush()
    except OSError:
        handle.close()
        raise
    _handle = handle
    return True
