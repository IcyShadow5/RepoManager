"""Structured process launching, including Windows batch-file shims.

Normal executables are started directly with ``shell=False``.  Windows batch
files cannot be passed directly to CreateProcess, so they use an explicit
``cmd.exe`` boundary whose executable and arguments are supplied through
quoted environment-variable expansions.  This keeps paths containing spaces,
Unicode, and command metacharacters out of the command program itself.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Callable, Iterable, Mapping


BATCH_SUFFIXES = {".bat", ".cmd"}
_EXECUTABLE_ENV = "REPOMANAGER_EXECUTABLE"
_ARG_ENV_PREFIX = "REPOMANAGER_ARG_"


def _system_cmd_executable() -> str:
    """Resolve the Windows command processor without trusting ``COMSPEC``."""
    if os.name == "nt":
        try:
            import ctypes

            buffer = ctypes.create_unicode_buffer(32768)
            length = ctypes.windll.kernel32.GetSystemDirectoryW(
                buffer, len(buffer))
            if 0 < length < len(buffer):
                candidate = Path(buffer.value) / "cmd.exe"
                if candidate.is_file():
                    return str(candidate)
        except (AttributeError, OSError, ValueError):
            pass
    found = shutil.which("cmd.exe")
    if found:
        return str(found)
    raise OSError("Windows command processor is unavailable")


def resolve_executable(
    executable: str,
    *,
    which: Callable[[str], str | None] | None = None,
) -> str | None:
    """Resolve an executable name or explicit file path without running it."""
    if not isinstance(executable, str) or not executable.strip():
        return None
    value = executable.strip()
    explicit = Path(value)
    try:
        if explicit.is_file():
            return str(explicit)
    except OSError:
        return None
    try:
        found = (which or shutil.which)(value)
    except (OSError, TypeError):
        return None
    return str(found) if found else None


def _batch_environment(
    executable: str,
    args: Iterable[str],
    base: Mapping[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    """Build a cmd program whose untrusted values stay in quoted variables."""
    values = [str(arg) for arg in args]
    for value in (executable, *values):
        if any(character in value for character in ('"', "\r", "\n")):
            raise ValueError(
                "Windows batch executable and arguments cannot contain "
                "quotes or line breaks"
            )
    env = dict(base or os.environ)
    env[_EXECUTABLE_ENV] = executable
    command = f'"%{_EXECUTABLE_ENV}%"'
    for index, value in enumerate(values):
        name = f"{_ARG_ENV_PREFIX}{index}"
        env[name] = value
        command += f' "%{name}%"'
    return command, env


def structured_invocation(
    executable: str,
    args: Iterable[str] = (),
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> tuple[list[str], dict[str, str] | None]:
    """Return argv/env for a direct executable or explicit Windows shim."""
    resolved = resolve_executable(executable)
    if resolved is None:
        raise OSError(f"executable not found: {executable}")
    values = [str(arg) for arg in args]
    active_platform = os.name if platform is None else platform
    if active_platform == "nt" and Path(resolved).suffix.casefold() in BATCH_SUFFIXES:
        command, env = _batch_environment(resolved, values, environ)
        return [_system_cmd_executable(), "/d", "/v:off", "/s", "/c",
                command], env
    return [resolved, *values], None


def spawn_structured(
    executable: str,
    args: Iterable[str] = (),
    *,
    cwd: str,
    popen: Callable[..., Any] = subprocess.Popen,
    creationflags: int = 0,
    start_new_session: bool = False,
) -> Any:
    """Revalidate and start a structured command with ``shell=False``."""
    try:
        valid_cwd = Path(cwd).is_dir()
    except (OSError, TypeError):
        valid_cwd = False
    if not valid_cwd:
        raise OSError(f"working directory not found: {cwd}")
    argv, env = structured_invocation(executable, args)
    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "shell": False,
        "creationflags": creationflags,
    }
    if env is not None:
        kwargs["env"] = env
        # ``Popen(list)`` applies C-runtime quoting to the final /c program;
        # cmd.exe does not understand its backslash-escaped quotes.  The
        # program below contains only fixed switches and variable names, so
        # pass that trusted boundary verbatim while keeping all caller values
        # in the environment.
        command_line = (subprocess.list2cmdline(argv[:5])
                        + ' "' + argv[5] + '"')
        kwargs["executable"] = argv[0]
        argv_for_popen: str | list[str] = command_line
    else:
        argv_for_popen = argv
    if start_new_session:
        kwargs["start_new_session"] = True
    return popen(argv_for_popen, **kwargs)
