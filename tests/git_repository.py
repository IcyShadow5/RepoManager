"""Small, deterministic primitives for real-Git integration tests."""
from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path


GIT_EXE = shutil.which("git")


def canonical_path(value: str | os.PathLike[str]) -> str:
    """Return a filesystem-identity comparison key without rewriting paths."""
    return os.path.normcase(os.path.realpath(os.fspath(value)))


def require_git() -> str:
    if GIT_EXE is None:
        raise unittest.SkipTest("Git executable is not available")
    return GIT_EXE


def git(repository: Path, *args: str) -> str:
    """Run Git against one explicitly supplied temporary repository."""
    result = subprocess.run(
        [require_git(), "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def init_repository(path: Path, *, bare: bool = False) -> Path:
    path.mkdir(parents=True)
    args = ("init", "--bare", "-q") if bare else ("init", "-q")
    git(path, *args)
    git(path, "symbolic-ref", "HEAD", "refs/heads/main")
    if not bare:
        git(path, "config", "user.name", "RepoManager Compatibility Lab")
        git(path, "config", "user.email", "compatibility@example.invalid")
    return path


def create_file(repository: Path, relative_path: str, content: str) -> Path:
    target = repository / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def commit(repository: Path, message: str, *, content: str | None = None) -> str:
    if content is not None:
        create_file(repository, "tracked.txt", content)
    git(repository, "add", "-A")
    git(repository, "commit", "-qm", message)
    return git(repository, "rev-parse", "HEAD")


def create_repository(path: Path) -> Path:
    init_repository(path)
    commit(path, "initial", content="initial\n")
    return path


def create_branch(repository: Path, name: str) -> None:
    git(repository, "branch", name)


def checkout(repository: Path, target: str) -> None:
    git(repository, "checkout", "-q", target)


def detach_head(repository: Path, target: str = "HEAD") -> None:
    git(repository, "checkout", "--detach", "-q", target)


def modify_file(repository: Path, content: str) -> None:
    create_file(repository, "tracked.txt", content)


def stage(repository: Path, relative_path: str = "tracked.txt") -> None:
    git(repository, "add", relative_path)


def delete_file(repository: Path, relative_path: str = "tracked.txt") -> None:
    (repository / relative_path).unlink()


def add_remote(repository: Path, name: str, target: Path) -> None:
    git(repository, "remote", "add", name, str(target.resolve()))


def add_worktree(repository: Path, target: Path, *, branch: str | None = None,
                 detached: bool = False) -> Path:
    if detached:
        git(repository, "worktree", "add", "--detach", "-q", str(target), "HEAD")
    else:
        if branch is None:
            raise ValueError("branch is required for an attached worktree")
        create_branch(repository, branch)
        git(repository, "worktree", "add", "-q", str(target), branch)
    return target
