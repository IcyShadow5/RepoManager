"""Pure Project-domain rules used by the RepoManager UI.

This module deliberately owns no Tk widgets, persistence, Git execution, or
launcher behavior. Project records remain dictionary-shaped so the boundary
can evolve without replacing the existing persistence mechanism.
"""
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
import unicodedata
from uuid import uuid4

from . import intelligence, reports


DEFAULT_NOW_LIMIT = 20
PROJECT_ID_FIELD = "project_id"
FOLDER_PATH_FIELD = "folder_path"

# Classification is derived from observable repository context. It is not
# ownership, provenance, trust, or authorization.
CLASS_GIT_REPOSITORY = "git_repository"
CLASS_NON_GIT_LOCATION = "non_git_location"
CLASS_ARCHIVED = "archived"
CLASS_UNKNOWN = "unknown"

CLASSIFICATION_SOURCE_SCANNER = "scanner"
CLASSIFICATION_SOURCE_PROJECT = "project_metadata"
CLASSIFICATION_SOURCE_NONE = "none"


def repository_default_name(path: str) -> str:
    """Return the scanner default name for a repository path.

    ``repository`` is a common checkout-layout directory rather than a useful
    Project label. In that one unambiguous layout, use its parent directory;
    all other repository folder names keep the existing behavior.
    """
    repo = Path(path)
    if repo.name.casefold() == "repository" and repo.parent.name:
        return repo.parent.name
    return repo.name


def _path_basename(path: str) -> str:
    """Return a Windows-friendly basename even when running on another OS."""
    return path.replace("\\", "/").rstrip("/").split("/")[-1]


def _path_parent_basename(path: str) -> str:
    """Return the parent folder name using either path separator."""
    parts = path.replace("\\", "/").rstrip("/").split("/")
    return parts[-2] if len(parts) > 1 else ""


def project_display_name(project: Mapping[str, Any]) -> str:
    """Return a useful label without rewriting persisted Project metadata.

    Legacy scanner records may still store the repository basename. Derive a
    better label only when that stored value is demonstrably the scanner
    default, so user-curated names always win.
    """
    stored = project.get("name")
    name = stored.strip() if isinstance(stored, str) else ""
    path = project.get("path")
    if isinstance(path, str) and path.strip():
        repo_name = _path_basename(path)
        if not name:
            return repository_default_name(path)
        if (repo_name.casefold() == "repository"
                and name.casefold() == "repository"):
            return _path_parent_basename(path) or repository_default_name(path)
    return name


def project_name_suggestion(project: Mapping[str, Any]) -> dict[str, str] | None:
    """Return a deterministic, non-destructive display-name suggestion.

    Suggestions are presentation evidence only. They never alter ``name`` and
    are safe to recompute as remote or manifest observations change.
    """
    stored = project.get("name")
    path = project.get("path") or project.get("folder_path")
    if not isinstance(path, str) or not path.strip():
        return None
    candidates: list[tuple[str, str]] = []
    remote = project.get("remote")
    if isinstance(remote, str) and remote.strip():
        remote_name = remote.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        if remote_name:
            candidates.append((remote_name, "remote"))
    for key in ("manifest_name", "package_name"):
        value = project.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append((value.strip(), "manifest"))
    folder = _path_basename(path)
    if folder.casefold() == "repository":
        folder = _path_parent_basename(path) or folder
    if folder:
        candidates.append((folder, "folder"))
    if not candidates:
        return None
    suggestion, source = candidates[0]
    stored_name = stored.strip() if isinstance(stored, str) else ""
    if suggestion.casefold() == stored_name.casefold():
        return None
    return {"name": suggestion, "source": source}


def project_secondary_identity(project: Mapping[str, Any]) -> str:
    """Return concise location/provider context for duplicate display names."""
    path = project.get("path") or project.get("folder_path")
    if isinstance(path, str) and path.strip():
        return path
    remote = project.get("remote")
    return remote if isinstance(remote, str) else ""


def ensure_project_id(project: dict[str, Any]) -> str:
    """Return a stable logical Project ID, creating it only when absent.

    Existing records are backfilled in memory and may then be persisted by the
    normal registry save path. The ID is deliberately independent of path so a
    repository move does not create a new logical Project.
    """
    value = project.get(PROJECT_ID_FIELD)
    if isinstance(value, str) and value.strip():
        return value
    value = str(uuid4())
    project[PROJECT_ID_FIELD] = value
    return value


def project_id(project: Mapping[str, Any]) -> str | None:
    """Return a valid persisted Project ID, or None for legacy/unassigned data."""
    value = project.get(PROJECT_ID_FIELD)
    return value if isinstance(value, str) and value.strip() else None


def is_ignored(project: Mapping[str, Any]) -> bool:
    """Return whether a Project has been explicitly ignored.

    Ignored is an independent, reversible persistence state rather than a
    lifecycle status. Missing legacy values are active/not ignored.
    """
    return project.get("ignored") is True


def is_repository_backed(project: Mapping[str, Any]) -> bool:
    """Whether the Project currently has an associated repository."""
    return isinstance(project.get("path"), str) and bool(project["path"].strip())


def _repository_evidence_is_observed(project: Mapping[str, Any]) -> bool:
    """Recognize explicit or legacy scanner evidence for a repository."""
    if project.get("repository_observed") is True:
        return True
    # Older registry records predate repository_observed but retain these
    # scanner-owned fields after a successful metadata collection. A bare path
    # must remain insufficient evidence for Git classification.
    return any(key in project for key in (
        "head", "branch", "status_available", "fingerprint",
        "worktrees_available", "upstream_state", "sync_available",
    ))


def derived_classification(project: Mapping[str, Any]) -> dict[str, str]:
    """Describe the safest classification supported by current evidence.

    The result is intentionally derived on demand and is never written to the
    Project record. A valid ``path`` is evidence of an associated Git-backed
    record only when scanner metadata confirms it is not broken. A broken or
    incomplete record remains unknown; path/name conventions never establish
    ownership, origin, trust, or authorization.
    """
    if not isinstance(project, Mapping):
        return {
            "value": CLASS_UNKNOWN,
            "source": CLASSIFICATION_SOURCE_NONE,
            "evidence": "project record is unavailable",
            "confidence": "unknown",
        }
    if project.get("status") == "archived":
        return {
            "value": CLASS_ARCHIVED,
            "source": CLASSIFICATION_SOURCE_PROJECT,
            "evidence": "Project status is archived",
            "confidence": "observed",
        }
    if not is_repository_backed(project):
        if isinstance(project.get(FOLDER_PATH_FIELD), str) and project[FOLDER_PATH_FIELD].strip():
            return {
                "value": CLASS_NON_GIT_LOCATION,
                "source": CLASSIFICATION_SOURCE_PROJECT,
                "evidence": "Project has a folder location but no associated repository path",
                "confidence": "observed",
            }
        return {
            "value": CLASS_UNKNOWN,
            "source": CLASSIFICATION_SOURCE_NONE,
            "evidence": "no repository or folder location is recorded",
            "confidence": "unknown",
        }
    if project.get("broken") is True:
        return {
            "value": CLASS_UNKNOWN,
            "source": CLASSIFICATION_SOURCE_SCANNER,
            "evidence": "scanner marked the associated Git location broken",
            "confidence": "unknown",
        }
    if not _repository_evidence_is_observed(project):
        return {
            "value": CLASS_UNKNOWN,
            "source": CLASSIFICATION_SOURCE_SCANNER,
            "evidence": "no current scanner evidence confirms a Git repository",
            "confidence": "unknown",
        }
    return {
        "value": CLASS_GIT_REPOSITORY,
        "source": CLASSIFICATION_SOURCE_SCANNER,
        "evidence": "scanner metadata contains an associated Git repository",
        "confidence": "observed",
    }


def classification_display(project: Mapping[str, Any]) -> str:
    """Return a concise human-readable label for the derived classification."""
    return derived_classification(project)["value"].replace("_", " ").title()


def classification_summary(project: Mapping[str, Any]) -> str:
    """Return a compact, user-facing explanation of derived classification.

    This deliberately exposes source and evidence while keeping derived
    observations separate from persisted Project curation and authorization.
    """
    result = derived_classification(project)
    return (f"{classification_display(project)} · source: {result['source']} · "
            f"{result['evidence']} · confidence: {result['confidence']}")


def project_folder(project: Mapping[str, Any]) -> str | None:
    """Return the optional local Project folder."""
    value = project.get(FOLDER_PATH_FIELD)
    if isinstance(value, str) and value.strip():
        return value
    path = project.get("path")
    return path if isinstance(path, str) and path.strip() else None


def association_candidates(projects: Iterable[Mapping[str, Any]], current: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return other known valid repository records for explicit reassociation."""
    current_id = project_id(current)
    candidates = []
    for candidate in projects:
        if candidate is current or project_id(candidate) == current_id:
            continue
        if not is_repository_backed(candidate):
            continue
        if candidate.get("broken"):
            continue
        candidates.append(candidate)
    return sorted(candidates, key=lambda p: (
        project_display_name(p).lower(),
        str(p.get("path", "")).lower(),
    ))


def validate_association_target(projects: Iterable[Mapping[str, Any]], current: Mapping[str, Any], target: Mapping[str, Any]) -> tuple[bool, str]:
    """Validate an existing known repository before reassociation."""
    candidates = association_candidates(projects, current)
    if target not in candidates:
        return False, "The selected repository is invalid, broken, or already associated."
    return True, ""


def associate_repository(project: dict[str, Any], target: Mapping[str, Any]) -> dict[str, Any]:
    """Associate one existing repository record without changing Project identity."""
    project["path"] = str(target["path"])
    project.pop(FOLDER_PATH_FIELD, None)
    # ``name`` is user-owned curation. Reassociation changes location, not the
    # logical Project label.
    if not isinstance(project.get("name"), str) or not project["name"].strip():
        project["name"] = project_display_name(target) or str(target["path"])
    for field in (
            "branch", "head", "dirty", "staged", "unstaged", "untracked",
            "ahead", "behind", "upstream", "remotes", "remote_names",
            "remote_name", "remote_reachable", "last_commit_date",
            "last_commit_msg", "remote", "broken", "fingerprint",
            "worktrees", "worktrees_available", "status_available",
            "sync_available"):
        project.pop(field, None)
    return project


def is_visible(project: Mapping[str, Any], filter_text: str) -> bool:
    """Return whether a project matches name, path, or focus text."""
    if not filter_text:
        return True
    haystack = (
        f"{project_display_name(project)} "
        f"{project.get('path', '')} "
        f"{project.get('folder_path', '')} "
        f"{project.get('focus', '')}"
    ).casefold()
    return filter_text.casefold() in haystack


def _text_sort_key(value: Any) -> str:
    """Unicode-aware text ordering suited to case-insensitive Windows UI."""
    return unicodedata.normalize("NFKC", str(value or "")).casefold()


def sorted_projects(
    projects: Iterable[Mapping[str, Any]],
    sort_col: str | None = None,
    sort_desc: bool = False,
) -> list[Mapping[str, Any]]:
    """Return projects in deterministic displayed-value order."""
    items = list(projects)
    if not sort_col:
        return sorted(items, key=lambda p: (
            not p.get("pinned"),
            p.get("status") != "active",
            _text_sort_key(project_display_name(p)),
            _text_sort_key(project_secondary_identity(p)),
            _text_sort_key(project_id(p)),
        ))

    def value(project: Mapping[str, Any]) -> Any:
        if sort_col == "dirty":
            primary = project.get("dirty", 0) or 0
        elif sort_col == "sync":
            primary = (project.get("ahead", 0) or 0) + (
                project.get("behind", 0) or 0)
        elif sort_col == "name":
            primary = _text_sort_key(project_display_name(project))
        elif sort_col == "classification":
            primary = _text_sort_key(classification_display(project))
        elif sort_col == "status":
            primary = _text_sort_key(
                ("Pinned · " if project.get("pinned") else "")
                + str(project.get("status", ""))
            )
        elif sort_col == "worktrees":
            primary = len(project.get("worktrees") or ())
        elif sort_col == "last_commit":
            primary = _text_sort_key(project.get("last_commit_date"))
        elif sort_col == "path":
            primary = _text_sort_key(project_folder(project))
        elif sort_col == "branch":
            primary = _text_sort_key(project.get("branch") or "(none)")
        elif sort_col != "dirty" and sort_col != "sync":
            primary = _text_sort_key(project.get(sort_col))
        return (
            primary,
            _text_sort_key(project_display_name(project)),
            _text_sort_key(project_secondary_identity(project)),
            _text_sort_key(project_id(project)),
        )

    return sorted(items, key=value, reverse=sort_desc)


def working_on_now_rows(
    projects: Iterable[Mapping[str, Any]],
    exists: Callable[[str], bool],
    limit: int = DEFAULT_NOW_LIMIT,
) -> list[Mapping[str, Any]]:
    """Return available explicitly active projects, newest activity first."""
    rows = [
        project for project in projects
        if str(project.get("status", "")) == "active"
        and not is_ignored(project)
        and exists(str(project_folder(project) or ""))
    ]
    rows.sort(
        key=lambda project: project.get("last_commit_date") or "",
        reverse=True,
    )
    return rows[:limit]


def build_repository_report(project: Mapping[str, Any]) -> dict[str, Any]:
    """Create a current local report without mutating Project state."""
    report_project = dict(project)
    report_project["name"] = project_display_name(project)
    return reports.repository_report(report_project)


def build_project_export(project: Mapping[str, Any]) -> dict[str, Any]:
    """Create a metadata-only portable export for one Project."""
    export_project = dict(project)
    export_project["name"] = project_display_name(project)
    return reports.project_export(export_project)


def inspect_project_understanding(project: Mapping[str, Any]) -> dict[str, Any]:
    """Expose objective documentation and stack evidence for consumers."""
    path = project.get("path")
    if not isinstance(path, str) or not path.strip():
        return {"documentation": (), "stack": intelligence.StackResult((), (), (), (), (), (), ())}
    return {"documentation": intelligence.inspect_documentation(path), "stack": intelligence.inspect_stack(path)}


def apply_curation(
    project: dict[str, Any],
    *,
    status: str,
    pinned: bool,
    focus: str,
) -> dict[str, Any]:
    """Apply the existing status/pin/focus edit semantics in place."""
    project["status"] = status or "idea"
    project["pinned"] = bool(pinned)
    project["focus"] = focus.strip()
    return project
