"""Shared logical and physical Git target authorization and mutation locking."""
import os
from pathlib import Path
import threading

from . import projects


def git_target_is_current(project_records, target):
    """True when a preview target still names the same Project and path."""
    target_id = projects.project_id(target)
    target_path = target.get("path")
    for project in project_records:
        same_project = (projects.project_id(project) == target_id
                        if target_id else project is target)
        if same_project:
            return project.get("path") == target_path
    return False


def git_target_snapshot(project):
    """Capture the logical identity and path authorized for a Git mutation."""
    project_id = projects.project_id(project)
    return {
        "project_id": project_id,
        "path": project.get("path"),
        "_record": project if project_id is None else None,
        "repository_marker": repository_marker_identity(project.get("path")),
    }


def repository_marker_identity(path):
    """Stable local identity for the repository metadata directory."""
    if not isinstance(path, str) or not path.strip():
        return None
    marker = Path(path) / ".git"
    try:
        if marker.is_file():
            first = marker.read_text(
                encoding="utf-8", errors="replace").splitlines()[0]
            if not first.casefold().startswith("gitdir:"):
                return None
            git_dir = Path(first.split(":", 1)[1].strip())
            if not git_dir.is_absolute():
                git_dir = marker.parent / git_dir
            git_dir = git_dir.resolve()
            if git_dir.parent.name.casefold() == "worktrees":
                git_dir = git_dir.parent.parent
        elif marker.is_dir():
            git_dir = marker.resolve()
        else:
            return None
        stat = git_dir.stat()
        return (os.path.normcase(str(git_dir)), stat.st_dev, stat.st_ino)
    except (OSError, UnicodeError, IndexError):
        return None


def git_target_is_authorized(project_records, snapshot):
    """Revalidate a mutation target immediately before invoking Git.

    A missing stable ID is intentionally not enough to authorize a stale
    dictionary snapshot: legacy records must still be the same live object.
    """
    path = snapshot.get("path") if isinstance(snapshot, dict) else None
    if not isinstance(path, str) or not path.strip():
        return False
    target_id = snapshot.get("project_id")
    for project in project_records:
        if target_id:
            if projects.project_id(project) == target_id and project.get("path") == path:
                return True
        elif project is snapshot.get("_record") and project.get("path") == path:
            return True
    return False


def git_mutation_target_is_authorized(project_records, snapshot):
    """Require both live Project association and repository identity."""
    marker = snapshot.get("repository_marker") \
        if isinstance(snapshot, dict) else None
    return (marker is not None
            and git_target_is_authorized(project_records, snapshot)
            and repository_marker_identity(snapshot.get("path")) == marker)


class GitMutationGuard:
    """Serialize mutating Git operations per physical repository."""

    def __init__(self):
        self._lock = threading.Lock()
        self._active = set()

    def acquire(self, key):
        with self._lock:
            if key in self._active:
                return False
            self._active.add(key)
            return True

    def release(self, key):
        with self._lock:
            self._active.discard(key)
