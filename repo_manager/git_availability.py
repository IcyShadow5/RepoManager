"""Check whether the Git command used by RepoManager can actually start."""
import os
import shutil
import subprocess
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
from dataclasses import dataclass
from .git_environment import git_environment


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@contextmanager
def _launch_error_mode():
    """Keep a bad Windows executable from blocking detection in a system dialog."""
    if os.name != "nt":
        yield
        return
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetThreadErrorMode.argtypes = []
    api.GetThreadErrorMode.restype = wintypes.DWORD
    api.SetThreadErrorMode.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    api.SetThreadErrorMode.restype = wintypes.BOOL
    previous = wintypes.DWORD()
    # Scope SEM_FAILCRITICALERRORS to this probe thread, preserving its flags.
    if not api.SetThreadErrorMode(api.GetThreadErrorMode() | 0x0001, ctypes.byref(previous)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        if not api.SetThreadErrorMode(previous.value, None):
            raise ctypes.WinError(ctypes.get_last_error())


@dataclass(frozen=True)
class GitAvailability:
    status: str
    detail: str = ""

    @property
    def available(self):
        return self.status == "available"


def check_git(*, which=shutil.which, run=subprocess.run):
    """Probe the current process PATH on every call; never cache the result."""
    if which("git") is None:
        return GitAvailability("not_found")
    try:
        with _launch_error_mode():
            result = run(
                ["git", "--version"], capture_output=True, text=True,
                timeout=5, encoding="utf-8", errors="replace",
                env=git_environment(read_only=True),
                creationflags=CREATE_NO_WINDOW)
    except FileNotFoundError:
        return GitAvailability("not_found")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return GitAvailability("launch_failed", str(exc))
    if result.returncode != 0:
        return GitAvailability(
            "unusable", (result.stderr or result.stdout or
                         f"Git exited with code {result.returncode}").strip())
    return GitAvailability("available")
