"""Pure Workspace composition rules.

A Workspace is RepoManager-owned metadata that groups concrete repository or
Git Worktree targets. Live branch, HEAD, and dirty state are always observed
from Git; this module only defines composition and derived reporting.
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

VALID = "VALID"
MISSING = "MISSING"
UNTRACKED = "UNTRACKED"
STALE = "STALE"
AMBIGUOUS = "AMBIGUOUS"
UNAVAILABLE = "UNAVAILABLE"
UNKNOWN = "UNKNOWN"

WORKSPACE_ID_FIELD = "workspace_id"


def ensure_workspace_id(workspace: dict[str, Any]) -> str:
    """Return the durable Workspace ID, creating one independently of paths."""
    value = workspace.get(WORKSPACE_ID_FIELD)
    if isinstance(value, str) and value.strip():
        return value
    value = str(uuid4())
    workspace[WORKSPACE_ID_FIELD] = value
    return value


def workspace_id(workspace: Mapping[str, Any]) -> str | None:
    value = workspace.get(WORKSPACE_ID_FIELD)
    return value if isinstance(value, str) and value.strip() else None


def new_workspace(name: str, *, description: str = "", project_id: str | None = None) -> dict[str, Any]:
    """Create metadata only; no repository or filesystem operation is done."""
    workspace = {
        WORKSPACE_ID_FIELD: str(uuid4()),
        "name": name.strip(),
        "description": description.strip(),
        "members": [],
    }
    if project_id:
        workspace["project_id"] = project_id
    return workspace


def add_member(
    workspace: dict[str, Any],
    repository_id: str,
    path: str,
    *,
    branch: str | None = None,
    head: str | None = None,
    label: str = "",
    role: str = "",
) -> dict[str, Any]:
    """Add one explicit target, rejecting duplicate target paths."""
    if not isinstance(repository_id, str) or not repository_id.strip():
        raise ValueError("repository_id is required")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path is required")
    key = str(Path(path).resolve()).casefold()
    if any(str(Path(m.get("path", "")).resolve()).casefold() == key
           for m in workspace.setdefault("members", []) if isinstance(m, dict)):
        raise ValueError("workspace member path is already present")
    member = {"repository_id": repository_id, "path": path}
    if branch:
        member["expected_branch"] = branch
    if head:
        member["expected_head"] = head
    if label.strip():
        member["label"] = label.strip()
    if role.strip():
        member["role"] = role.strip()
    workspace["members"].append(member)
    return member


def remove_member(workspace: dict[str, Any], path: str) -> bool:
    """Remove metadata for an exact target path; never touches the target."""
    key = str(Path(path).resolve()).casefold()
    members = workspace.setdefault("members", [])
    before = len(members)
    workspace["members"] = [
        m for m in members
        if not isinstance(m, dict)
        or str(Path(m.get("path", "")).resolve()).casefold() != key
    ]
    return len(workspace["members"]) != before


def inspect_member(
    member: Mapping[str, Any],
    *,
    observe: Callable[[str], Mapping[str, Any] | None],
    exists: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Classify one member using an authoritative observation callback.

    ``observe`` should return scanner metadata. No callback result is treated
    as authorization; missing and unavailable states remain explicit.
    """
    path = member.get("path")
    if not isinstance(path, str) or not path.strip():
        return {"state": AMBIGUOUS, "evidence": ["member has no valid path"], "member": dict(member)}
    exists = exists or (lambda value: Path(value).is_dir())
    try:
        if not exists(path):
            return {"state": MISSING, "evidence": ["target directory does not exist"], "member": dict(member)}
        observed = observe(path)
    except Exception as exc:
        return {"state": UNAVAILABLE, "evidence": [f"observation failed: {exc}"], "member": dict(member)}
    if not isinstance(observed, Mapping) or observed.get("broken"):
        return {"state": UNAVAILABLE, "evidence": ["Git metadata is unavailable"], "member": dict(member)}
    status_available = observed.get("status_available", True)
    dirty = observed.get("dirty")
    if (status_available is False
            or not isinstance(dirty, int)
            or isinstance(dirty, bool)
            or dirty < 0):
        return {
            "state": UNAVAILABLE,
            "evidence": ["Git working-tree status is unavailable"],
            "member": dict(member),
        }

    evidence = ["Git metadata observed"]
    state = VALID
    expected_branch = member.get("expected_branch")
    expected_head = member.get("expected_head")
    if expected_branch and observed.get("branch") != expected_branch:
        state = STALE
        evidence.append(f"expected branch {expected_branch!r}, observed {observed.get('branch')!r}")
    if expected_head and observed.get("head") != expected_head:
        state = STALE
        evidence.append("expected HEAD differs from observed HEAD")
    result = {
        "state": state,
        "path": path,
        "repository_id": member.get("repository_id"),
        "label": member.get("label") or Path(path).name,
        "branch": observed.get("branch"),
        "head": observed.get("head"),
        "dirty": dirty,
        "worktree": observed.get("worktrees") or [],
        "freshness": "CURRENT",
        "evidence": evidence,
        "member": dict(member),
    }
    return result


def inspect_workspace(
    workspace: Mapping[str, Any],
    *,
    observe: Callable[[str], Mapping[str, Any] | None],
    exists: Callable[[str], bool] | None = None,
    repository_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Return per-member evidence plus transparent aggregate counts."""
    members = workspace.get("members")
    if not isinstance(members, list):
        members = []
    allowed = set(repository_ids) if repository_ids is not None else None
    results = []
    for member in members:
        if not isinstance(member, Mapping):
            results.append({"state": AMBIGUOUS,
                            "evidence": ["member is not an object"]})
        elif (allowed is not None
              and member.get("repository_id") not in allowed):
            results.append({
                "state": UNTRACKED, "member": dict(member),
                "repository_id": member.get("repository_id"),
                "path": member.get("path"),
                "evidence": ["repository_id is not present in the Project registry"],
            })
        else:
            results.append(inspect_member(
                member, observe=observe, exists=exists))
    counts = {state.lower(): sum(r.get("state") == state for r in results)
              for state in (VALID, MISSING, UNTRACKED, STALE, AMBIGUOUS,
                            UNAVAILABLE, UNKNOWN)}
    return {
        "workspace_id": workspace_id(workspace),
        "name": workspace.get("name", ""),
        "member_count": len(results),
        "members": results,
        "counts": counts,
        "status": _aggregate_status(results),
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _aggregate_status(results: Iterable[Mapping[str, Any]]) -> str:
    states = {r.get("state") for r in results}
    if MISSING in states or UNTRACKED in states or AMBIGUOUS in states:
        return "BLOCKED"
    if UNAVAILABLE in states or UNKNOWN in states:
        return "UNKNOWN"
    if STALE in states:
        return "STALE"
    return "READY"


def validate_workspace(workspace: Mapping[str, Any]) -> list[str]:
    """Validate persisted Workspace metadata without dropping unknown fields."""
    issues = []
    if not isinstance(workspace_id(workspace), str):
        issues.append("missing workspace_id")
    if not isinstance(workspace.get("name"), str) or not workspace["name"].strip():
        issues.append("missing workspace name")
    if not isinstance(workspace.get("members"), list):
        issues.append("members is not a list")
    else:
        for index, member in enumerate(workspace["members"]):
            if not isinstance(member, Mapping):
                issues.append(f"member [{index}] is not an object")
                continue
            if not isinstance(member.get("repository_id"), str) or not member["repository_id"].strip():
                issues.append(f"member [{index}] missing repository_id")
            if not isinstance(member.get("path"), str) or not member["path"].strip():
                issues.append(f"member [{index}] missing path")
    return issues
