"""Windows read-only Git boundary: repository helpers cannot start processes."""
from functools import lru_cache
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .git_environment import git_environment
from .windows_process import WindowsProcess


class ObservationUnavailable(OSError):
    pass


@lru_cache(maxsize=4)
def _native_git(executable, modified, size):
    # Git for Windows' PATH cmd/git.exe is a redirector. Resolve its native
    # binary outside every repository; --exec-path does not observe a checkout.
    with tempfile.TemporaryDirectory(prefix="repomanager-git-resolution-") as neutral:
        result = subprocess.run([executable, "--exec-path"], cwd=neutral,
                                capture_output=True, text=True, encoding="utf-8",
                                stdin=subprocess.DEVNULL, timeout=10,
                                env=git_environment(read_only=True),
                                creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise ObservationUnavailable("Native Git executable could not be resolved")
    directory = Path(result.stdout.strip())
    for candidate in (directory / "git.exe", directory.parent.parent / "bin/git.exe"):
        if candidate.is_absolute() and candidate.is_file():
            return str(candidate)
    raise ObservationUnavailable("Read-only observation requires the native Git for Windows executable")


def native_git():
    # Never search the selected repository/current directory for git.exe.
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not os.path.isabs(entry):
            continue
        executable = shutil.which(str(Path(entry) / "git.exe"))
        if executable:
            stat = Path(executable).stat()
            return _native_git(executable, stat.st_mtime_ns, stat.st_size)
    raise ObservationUnavailable("Git executable was not found on PATH")


def run_read_only(command, *, env, timeout, capture_output=False, text=False,
                  encoding="utf-8", errors="strict", stdout=None, stderr=None,
                  stdin=None, creationflags=0):
    """Run only native Git; fail closed on any attempted helper, even exit 0.

    The job is assigned while Git is suspended, with no breakaway permission.
    Git can swallow optional filter failures; successful commands with any
    diagnostics are unavailable rather than treating raw fallback as clean.
    This is an external-helper boundary, not a filesystem/network sandbox.
    """
    if os.name != "nt":
        raise ObservationUnavailable("Read-only Git process isolation requires Windows")
    import _winapi
    import msvcrt

    # Round-trip warnings protect writes, not read-only comparisons. Keep EOL
    # conversion and all other diagnostic/helper rejection unchanged.
    args = [native_git(), "-c", "core.fsmonitor=false", "-c", "core.safecrlf=false", *command[1:]]
    current = _winapi.GetCurrentProcess()
    handles = []
    process = None
    with tempfile.TemporaryFile() as captured_out, tempfile.TemporaryFile() as captured_err, open(os.devnull, "rb") as devnull:
        output, error = (captured_out, captured_err) if capture_output else (stdout, stderr)
        if output is None or error is None:
            raise ObservationUnavailable("Git observation requires captured output")
        try:
            for stream in (devnull, output, error):
                handles.append(_winapi.DuplicateHandle(current, msvcrt.get_osfhandle(stream.fileno()),
                                                       current, 0, True, _winapi.DUPLICATE_SAME_ACCESS))
            # CREATE_NO_WINDOW creates an additional console-host job member.
            # DETACHED_PROCESS with explicit stdio needs no console and keeps
            # one native Git process as the entire authorized process budget.
            process = WindowsProcess(args[0], subprocess.list2cmdline(args),
                                     env=env, cwd=None, creationflags=0x8,
                                     active_process_limit=1, std_handles=handles)
            returncode = process.wait(timeout=timeout)
            if process.process_limit_exceeded:
                raise ObservationUnavailable("Git observation unavailable: an external helper was blocked")
            error.flush()
            error.seek(0)
            if returncode == 0 and error.read(1):
                raise ObservationUnavailable("Git observation emitted diagnostics; metadata is unavailable")
            error.seek(0)
            values = [None, None]
            if capture_output:
                for index, stream in enumerate((output, error)):
                    stream.seek(0)
                    value = stream.read()
                    values[index] = value.decode(encoding, errors) if text else value
            return subprocess.CompletedProcess(args, returncode, *values)
        finally:
            try:
                if process is not None:
                    try:
                        if process.poll() is None:
                            process.terminate()
                            process.wait(timeout=5)
                    finally:
                        process.close()
            finally:
                for handle in handles:
                    _winapi.CloseHandle(handle)
