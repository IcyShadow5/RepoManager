"""Explicit external-agent launch and observation boundary.

This module deliberately does not interpret agent output, manage sessions, or
orchestrate work. It records one user-approved process launch and re-observes
the concrete target afterward.
"""
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
from typing import Any, Callable, Mapping
from uuid import uuid4

from . import processes

AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"
UNKNOWN = "UNKNOWN"
INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
READY = "READY"
NOT_READY = "NOT_READY"

NOT_STARTED = "NOT_STARTED"
STARTING = "STARTING"
RUNNING = "RUNNING"
STOPPING = "STOPPING"
EXITED = "EXITED"
TERMINATED = "TERMINATED"
FAILED_TO_START = "FAILED_TO_START"
UNKNOWN_PROCESS = "UNKNOWN"

NOT_RUN = "NOT_RUN"
PENDING = "PENDING"
TARGET_RECHECKED = "TARGET_RECHECKED"
VERIFIED = TARGET_RECHECKED  # compatibility name; no correctness claim
PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
FAILED = "FAILED"
CONTRADICTED = "CONTRADICTED"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_agent(agent_id: str, display_name: str, executable: str,
              *, args: list[str] | None = None) -> dict[str, Any]:
    return {"agent_id": agent_id, "display_name": display_name.strip(),
            "executable": executable, "args": list(args or [])}


def agent_availability(agent: Mapping[str, Any], *, which: Callable[[str], str | None] = shutil.which) -> str:
    executable = agent.get("executable")
    if not isinstance(executable, str) or not executable.strip():
        return INVALID_CONFIGURATION
    try:
        return AVAILABLE if processes.resolve_executable(executable, which=which) else UNAVAILABLE
    except (OSError, TypeError):
        return UNKNOWN


def validate_target(path: str, *, is_dir: Callable[[str], bool] | None = None) -> tuple[bool, str]:
    if not isinstance(path, str) or not path.strip():
        return False, "target working directory is missing"
    checker = is_dir or (lambda value: Path(value).is_dir())
    try:
        if not checker(path):
            return False, "target working directory is missing"
    except OSError as exc:
        return False, f"target working directory unavailable: {exc}"
    return True, ""


def agent_readiness(agent: Mapping[str, Any], target: Mapping[str, Any] | None) -> dict[str, str]:
    """Combine executable and selected-target evidence for truthful UI state."""
    availability = agent_availability(agent)
    executable = agent.get("executable") if isinstance(agent, Mapping) else None
    resolved = processes.resolve_executable(executable) if availability == AVAILABLE else None
    if availability != AVAILABLE:
        reason = {
            INVALID_CONFIGURATION: "agent command is not configured",
            UNAVAILABLE: "agent executable was not found",
            UNKNOWN: "agent executable could not be checked",
        }.get(availability, "agent executable is unavailable")
        return {"state": NOT_READY, "availability": availability,
                "reason": reason, "executable": str(executable or ""), "cwd": ""}
    path = target.get("path") if isinstance(target, Mapping) else None
    valid, reason = validate_target(path)
    if not valid:
        return {"state": NOT_READY, "availability": availability,
                "reason": reason, "executable": str(resolved or executable),
                "cwd": str(path or "")}
    return {"state": READY, "availability": availability,
            "reason": "executable and target are available now",
            "executable": str(resolved), "cwd": str(path)}


def launch_intent(agent: Mapping[str, Any], target: Mapping[str, Any], *, user_action: str = "explicit") -> dict[str, Any]:
    """Build a structured invocation without shell interpolation."""
    readiness = agent_readiness(agent, target)
    if readiness["state"] != READY:
        raise ValueError(readiness["reason"])
    path = target.get("path")
    configured_args = [str(arg) for arg in agent.get("args", [])]
    args = [readiness["executable"], *configured_args]
    return {"agent_id": agent["agent_id"], "target": dict(target),
            "cwd": str(path), "argv": args, "launch_time": utc_now(),
            "user_action": user_action,
            "agent_config": {"executable": agent.get("executable"),
                             "args": configured_args}}


def new_run(intent: Mapping[str, Any]) -> dict[str, Any]:
    return {"run_id": str(uuid4()), "agent_id": intent["agent_id"],
            "target": dict(intent["target"]), "cwd": intent["cwd"],
            "argv": list(intent["argv"]), "start_time": intent["launch_time"],
            "process_state": NOT_STARTED, "verification": NOT_RUN,
            "exit_code": None, "end_time": None, "claim": None,
            "agent_config": dict(intent.get("agent_config") or {})}


def has_running_run(runs: list[Mapping[str, Any]], agent_id: str, target_path: str) -> bool:
    return any(r.get("agent_id") == agent_id and r.get("cwd") == target_path
               and r.get("process_state") in (
                   STARTING, RUNNING, STOPPING, UNKNOWN_PROCESS)
               for r in runs)


def _same_launch_contract(run: Mapping[str, Any], agent: Mapping[str, Any] | None,
                          target: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Revalidate identity, configuration, executable, argv, and cwd."""
    valid, reason = validate_target(run["cwd"])
    if not valid:
        return False, reason
    argv = run.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
        return False, "agent arguments are invalid"
    if processes.resolve_executable(argv[0]) is None:
        return False, "agent executable is no longer available"
    if agent is not None:
        snapshot = run.get("agent_config") or {}
        if (agent.get("agent_id") != run.get("agent_id")
                or agent.get("executable") != snapshot.get("executable")
                or [str(arg) for arg in agent.get("args", [])] != snapshot.get("args")):
            return False, "agent configuration changed before launch"
        readiness = agent_readiness(agent, target or run.get("target"))
        if readiness["state"] != READY:
            return False, readiness["reason"]
        if readiness["executable"] != argv[0]:
            return False, "agent executable changed before launch"
    if target is not None:
        snapshot_target = run.get("target") or {}
        if (target.get("path") != snapshot_target.get("path")
                or target.get("project_id") != snapshot_target.get("project_id")):
            return False, "Project identity or target changed before launch"
    return True, ""


def start_run(run: dict[str, Any], *, agent: Mapping[str, Any] | None = None,
              target: Mapping[str, Any] | None = None,
              popen: Callable[..., Any] = subprocess.Popen) -> Any:
    """Revalidate and start exactly the structured argv in its directory."""
    valid, reason = _same_launch_contract(run, agent, target)
    if not valid:
        run["process_state"] = FAILED_TO_START
        run["failure"] = reason
        return None
    run["process_state"] = STARTING
    try:
        process = processes.spawn_structured(
            run["argv"][0], run["argv"][1:], cwd=run["cwd"], popen=popen,
            start_new_session=True)
    except (OSError, ValueError) as exc:
        run["process_state"] = FAILED_TO_START
        run["failure"] = str(exc)
        return None
    run["pid"] = getattr(process, "pid", None)
    run["process_state"] = RUNNING
    return process


def observe_run(run: dict[str, Any], process: Any) -> str:
    """Update process state from the external process, never semantic status."""
    if run.get("process_state") not in (STARTING, RUNNING, STOPPING):
        return run.get("process_state", UNKNOWN_PROCESS)
    try:
        code = process.poll()
    except (OSError, AttributeError):
        run["process_state"] = UNKNOWN_PROCESS
        return UNKNOWN_PROCESS
    if code is None:
        run["process_state"] = (STOPPING if run.get("termination_requested")
                                else RUNNING)
    else:
        run["exit_code"] = code
        run["end_time"] = utc_now()
        run["process_state"] = (TERMINATED if run.get("termination_requested")
                                else EXITED)
    return run["process_state"]


def cancel_run(run: dict[str, Any], process: Any) -> str:
    """Request termination only; never revert target changes."""
    if not run.get("termination_requested"):
        try:
            process.terminate()
        except (OSError, AttributeError):
            run["process_state"] = UNKNOWN_PROCESS
            return UNKNOWN_PROCESS
        run["termination_requested"] = True
    run["process_state"] = STOPPING
    return observe_run(run, process)


def verify_post_run(run: dict[str, Any], *, observe: Callable[[str], Mapping[str, Any] | None]) -> dict[str, Any]:
    """Record post-run Git metadata without judging the agent's changes."""
    try:
        observed = observe(run["cwd"])
    except Exception as exc:
        run["verification"] = UNKNOWN
        run["verification_evidence"] = [f"observation failed: {exc}"]
        return run
    if not isinstance(observed, Mapping) or observed.get("broken"):
        run["verification"] = FAILED
        run["verification_evidence"] = ["target Git metadata unavailable after run"]
        return run
    run["post_run"] = {
        "branch": observed.get("branch"), "head": observed.get("head"),
        "dirty": observed.get("dirty", 0),
        "worktrees": observed.get("worktrees") or [],
        "observed_at": utc_now(),
    }
    run["verification"] = TARGET_RECHECKED
    run["verification_evidence"] = ["post-run Git metadata observed"]
    return run
