"""Bounded Git observations and explicit, independently approved mutations.

This module knows nothing about Tk or the registry. The caller supplies an
association/physical-identity authorization callback and owns mutation locks.
Git hooks and clean filters remain normal user-authorized local code execution.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit, urlunsplit
from .git_environment import git_environment
from .git_observation import run_read_only

SUCCESS = "SUCCESS"
FAILED = "FAILED"
CANCELLED = "CANCELLED"
UNKNOWN = "OUTCOME_UNKNOWN"
PARTIAL = "PARTIAL_MUTATION"
OUTPUT_LIMIT = 2 * 1024 * 1024
FILE_LIMIT = 5000
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def redact(text):
    """Hide URL user information/query/fragment, not arbitrary embedded secrets."""
    def clean(match):
        raw = match.group(0)
        try:
            parts = urlsplit(raw)
            host = parts.netloc.rsplit("@", 1)[-1]
            return urlunsplit((parts.scheme, host, parts.path, "", ""))
        except ValueError:
            return "[invalid URL]"
    return re.sub(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s]+", clean, str(text))


def display_path(path):
    return str(path).encode("unicode_escape").decode("ascii") if any(
        ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in path) else path


@dataclass(frozen=True)
class Result:
    outcome: str
    output: str


class GitError(Exception):
    def __init__(self, message, *, outcome=FAILED):
        super().__init__(redact(message))
        self.outcome = outcome


@dataclass(frozen=True)
class Change:
    path: str
    index: str
    worktree: str
    original: str | None = None

    @property
    def staged(self):
        return self.index not in (" ", "?")

    @property
    def unstaged(self):
        return self.worktree != " " or self.index == "?"

    @property
    def unsupported(self):
        return "U" in (self.index, self.worktree) or self.index + self.worktree in (
            "AA", "DD")

    @property
    def paths(self):
        return (self.path, self.original) if self.original else (self.path,)

    @property
    def label(self):
        name = display_path(self.path)
        if self.original:
            name = f"{display_path(self.original)} → {name}"
        return f"{self.index}{self.worktree}  {name}"


def parse_status(raw):
    """Parse porcelain v1 -z, whose rename path order is destination/source."""
    tokens = raw.split("\0")
    changes, index = [], 0
    while index < len(tokens) - 1:
        token = tokens[index]
        index += 1
        if len(token) < 4 or token[2] != " ":
            raise GitError("Invalid Git status; no mutation authorized")
        x, y, path = token[0], token[1], token[3:]
        original = None
        if x in "RC" or y in "RC":
            if index >= len(tokens) - 1:
                raise GitError("Incomplete rename status")
            original = tokens[index]
            index += 1
        if not path or (original is not None and not original):
            raise GitError("Empty Git path")
        changes.append(Change(path, x, y, original))
        if len(changes) > FILE_LIMIT:
            raise GitError(f"More than {FILE_LIMIT} changes; use an external Git tool")
    if tokens[-1]:
        raise GitError("Incomplete Git status")
    return tuple(changes)


@dataclass(frozen=True)
class State:
    branch: str | None
    head: str | None
    index_digest: str
    changes: tuple[Change, ...]
    status_digest: str
    in_progress: tuple[str, ...]

    @property
    def commit_key(self):
        return self.branch, self.head, self.index_digest, self.in_progress


@dataclass(frozen=True)
class Remote:
    name: str
    fetch_urls: tuple[str, ...]
    push_urls: tuple[str, ...]
    mirror: bool
    fetch_refspecs: tuple[str, ...] = ()

    def describe(self):
        return (f"{self.name}\n  Fetch: " + ", ".join(map(redact, self.fetch_urls))
                + "\n  Push: " + ", ".join(map(redact, self.push_urls))
                + "\n  Fetch refspecs: " + ", ".join(self.fetch_refspecs)
                + ("\n  Mirror configured (Push disabled)" if self.mirror else ""))


@dataclass(frozen=True)
class NetworkApproval:
    operation: str
    branch: str | None
    head: str | None
    remote: Remote
    destination: str | None
    upstream: tuple[str, str] | None
    set_upstream: bool = False


class Repository:
    def __init__(self, path, *, authorize=lambda: True):
        self.path = str(path)
        self.authorize = authorize

    def run(self, *args, write=False, allowed=(0,), truncate=False):
        if not self.authorize():
            raise GitError("Project association or repository identity changed", outcome=CANCELLED)
        env = git_environment(read_only=not write)
        # Spool to temporary files, never collect unlimited pipe output in RAM.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                runner = subprocess.run if write else run_read_only
                completed = runner(
                    ["git", "--no-pager", "-C", self.path, *args],
                    stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                    env=env, timeout=120 if write else 20,
                    creationflags=CREATE_NO_WINDOW)
            except subprocess.TimeoutExpired:
                raise GitError(
                    "Git timed out; inspect repository state before retrying" if write
                    else "Git observation timed out", outcome=UNKNOWN if write else FAILED)
            except OSError as exc:
                if not write:
                    raise GitError(f"Git observation unavailable: {exc}") from exc
                raise GitError("Git could not be started; check Git installation")
            streams = []
            for stream in (stdout, stderr):
                stream.seek(0)
                data = stream.read(OUTPUT_LIMIT + 1)
                if len(data) > OUTPUT_LIMIT:
                    if not truncate:
                        raise GitError("Git output exceeds the safe viewing limit",
                                       outcome=UNKNOWN if write else FAILED)
                    data = data[:OUTPUT_LIMIT] + b"\n[Output truncated]\n"
                streams.append(data.decode("utf-8", errors="surrogateescape"))
        if completed.returncode not in allowed:
            message = streams[1] or streams[0] or "Git command failed"
            if write:
                message += "\nGit reported failure; re-observe the repository before retrying. Partial index/ref updates may remain."
            raise GitError(message)
        return completed.returncode, streams[0], streams[1]

    def text(self, *args, **kwargs):
        return self.run(*args, **kwargs)[1]

    def state(self):
        branch_rc, branch, _ = self.run("symbolic-ref", "--quiet", "--short", "HEAD", allowed=(0, 1))
        head_rc, head, _ = self.run("rev-parse", "--verify", "HEAD", allowed=(0, 128))
        if head_rc and branch_rc:
            raise GitError("HEAD is unavailable")
        raw = self.text("status", "--porcelain=v1", "-z", "--untracked-files=all")
        changes = parse_status(raw)
        index = self.text("ls-files", "--stage", "-z")
        in_progress = []
        for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer"):
            location = self.text("rev-parse", "--git-path", marker).strip()
            location = Path(location)
            if not location.is_absolute():
                location = Path(self.path) / location
            if location.exists():
                in_progress.append(marker)
        digest = lambda value: hashlib.sha256(value.encode("utf-8", "surrogateescape")).hexdigest()
        return State(branch.strip() if branch_rc == 0 else None,
                     head.strip() if head_rc == 0 else None, digest(index),
                     changes, digest(raw), tuple(in_progress))

    def _check_state(self, approved, *, all_changes=False):
        current = self.state()
        if current.commit_key != approved.commit_key or (
                all_changes and current.status_digest != approved.status_digest):
            raise GitError("Git preview changed; refresh and confirm again", outcome=CANCELLED)
        if current.in_progress or any(change.unsupported for change in current.changes):
            raise GitError("Resolve conflicts or finish the in-progress Git operation externally first")
        return current

    def _check_paths(self, paths, *, mutation=True):
        if not paths:
            raise GitError("Select files first")
        root = Path(self.path).resolve()
        for name in paths:
            p = Path(name)
            if not name or p.is_absolute() or ".." in p.parts or "\0" in name:
                raise GitError("Unsafe file selection")
            # Includes parent junctions/symlinks; never preview/stage outside checkout.
            candidate = root / p
            if candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
                raise GitError("Symlink/external file selection requires an external Git tool")
        # Submodule index entries use mode 160000. Exclude all their descendants.
        for entry in self.text("ls-files", "--stage", "-z").split("\0"):
            if mutation and entry.startswith("160000 "):
                module = entry.split("\t", 1)[1]
                if any(name == module or name.startswith(module + "/") for name in paths):
                    raise GitError("Submodule mutations require an external Git tool")

    def commit(self, approved, message, *, stage_all=False):
        if not message.strip():
            return Result(FAILED, "A commit message is required")
        staged = False
        try:
            current = self._check_state(approved, all_changes=stage_all)
            if stage_all:
                if not current.changes:
                    raise GitError("Nothing to commit")
                self._check_paths(tuple(path for change in current.changes for path in change.paths))
                if "160000 " in self.text("ls-files", "--stage", "-z"):
                    raise GitError("Stage all is disabled for submodule repositories; stage individual ordinary files")
                self.run("add", "-A", write=True)
                staged = True
            elif not any(change.staged for change in current.changes):
                raise GitError("No staged changes; stage files or choose Stage all current changes")
            else:
                self._check_paths(tuple(path for change in current.changes if change.staged for path in change.paths))
            # Recheck the approved branch/HEAD immediately before committing.
            latest = self.state()
            if latest.in_progress or any(change.unsupported for change in latest.changes):
                raise GitError("Git operation/conflict state changed; no commit started", outcome=CANCELLED)
            if (latest.branch, latest.head) != (approved.branch, approved.head) or (
                    not stage_all and latest.commit_key != approved.commit_key):
                raise GitError("Branch/HEAD changed; no commit started", outcome=CANCELLED)
            output = self.text("commit", "-m", message.strip(), write=True)
            return Result(SUCCESS, redact(output.strip()) or "Local commit created; nothing was pushed")
        except GitError as exc:
            if staged and exc.outcome != UNKNOWN:
                return Result(PARTIAL, f"Staging completed, commit did not complete. Index changes remain.\n{exc}")
            return Result(exc.outcome, str(exc))

    def stage(self, approved, changes, *, unstage=False):
        try:
            current = self._check_state(approved, all_changes=True)
            if not changes or any(change not in current.changes for change in changes):
                raise GitError("File selection changed; refresh first", outcome=CANCELLED)
            paths = tuple(dict.fromkeys(path for change in changes for path in change.paths))
            self._check_paths(paths)
            if unstage:
                args = ("restore", "--staged", "--") if current.head else ("rm", "--cached", "--force", "-r", "--")
            else:
                args = ("add", "-A", "--")
            output = self.text("--literal-pathspecs", *args, *paths, write=True)
            return Result(SUCCESS, redact(output.strip()) or ("Files unstaged; working files unchanged" if unstage else "Files staged"))
        except GitError as exc:
            return Result(exc.outcome, str(exc))

    def diff(self, change, *, staged=False):
        self._check_paths(change.paths, mutation=False)
        if change.index == "?":
            path = Path(self.path) / change.path
            if not path.is_file():
                return "Untracked directory or unavailable file"
            with path.open("rb") as stream:
                data = stream.read(OUTPUT_LIMIT + 1)
            if b"\0" in data:
                return "Binary file — preview unavailable"
            text = data[:OUTPUT_LIMIT].decode("utf-8", "replace")
            return text + ("\n[Output truncated]" if len(data) > OUTPUT_LIMIT else "")
        args = ("--cached",) if staged else ()
        return self.text("--literal-pathspecs", "diff", "--ignore-submodules=all", "--no-ext-diff", "--no-textconv",
                         "--no-color", *args, "--", *change.paths, truncate=True) or "No differences in this view"

    def config(self, key):
        rc, out, _ = self.run("config", "--get", key, allowed=(0, 1))
        return out.strip() if rc == 0 else None

    def remotes(self):
        result = []
        for name in self.text("remote").splitlines():
            if not name or name.startswith("-"):
                raise GitError("Unsupported remote name")
            fetch = tuple(self.text("remote", "get-url", "--all", name).splitlines())
            push = tuple(self.text("remote", "get-url", "--push", "--all", name).splitlines())
            rc, refs, _ = self.run("config", "--get-all", f"remote.{name}.fetch", allowed=(0, 1))
            result.append(Remote(name, fetch, push, self.config(f"remote.{name}.mirror") == "true",
                                 tuple(refs.splitlines())))
        return tuple(result)

    def upstream(self, branch):
        if not branch:
            return None
        remote = self.config(f"branch.{branch}.remote")
        merge = self.config(f"branch.{branch}.merge")
        if not remote or not merge or not merge.startswith("refs/heads/") or remote == ".":
            return None
        return remote, merge[len("refs/heads/"):]

    def approve_network(self, operation, remote_name, destination=None, *, set_upstream=False):
        if operation not in ("push", "pull", "fetch"):
            raise GitError("Unsupported network operation")
        state = self.state()
        remote = next((item for item in self.remotes() if item.name == remote_name), None)
        if remote is None:
            raise GitError("Remote unavailable")
        urls = remote.push_urls if operation == "push" else remote.fetch_urls
        if len(urls) != 1:
            raise GitError("Multiple/missing destinations require an external Git tool")
        if operation in ("push", "pull") and (not state.branch or not state.head):
            raise GitError("This action requires an existing commit on an attached branch")
        if operation == "push" and remote.mirror:
            raise GitError("Mirror pushes are not supported")
        if operation in ("fetch", "pull"):
            # Fetch (including Pull's fetch) must never map onto local branches.
            if not remote.fetch_refspecs:
                raise GitError("Non-standard fetch refspecs require an external Git tool")
            for refspec in remote.fetch_refspecs:
                parts = refspec.lstrip("+").split(":")
                if len(parts) != 2 or not parts[1].startswith(f"refs/remotes/{remote.name}/"):
                    raise GitError("Non-standard fetch refspecs require an external Git tool")
                self.run("check-ref-format", "--refspec-pattern", parts[1])
        upstream = self.upstream(state.branch)
        if operation in ("push", "pull"):
            if not destination:
                if upstream and upstream[0] == remote.name:
                    destination = upstream[1]
                elif operation == "push":
                    destination = state.branch
                else:
                    raise GitError("No upstream configured for this remote")
            self.run("check-ref-format", "refs/heads/" + destination)
        return NetworkApproval(operation, state.branch, state.head, remote,
                               destination, upstream, set_upstream)

    def network(self, approved):
        try:
            current = self.approve_network(approved.operation, approved.remote.name,
                                           approved.destination, set_upstream=approved.set_upstream)
            if current != approved:
                raise GitError("Branch, upstream, or remote destination changed; confirm again", outcome=CANCELLED)
            operation = approved.operation
            if operation == "push":
                args = ["-c", "push.followTags=false", "push", "--no-follow-tags", "--recurse-submodules=no"]
                if approved.set_upstream:
                    args.append("--set-upstream")
                args += [approved.remote.name, f"HEAD:refs/heads/{approved.destination}"]
            elif operation == "pull":
                state = self.state()
                if state.in_progress or state.changes:
                    raise GitError("Pull requires a clean working tree with no in-progress operation")
                args = ["-c", "fetch.prune=false", "-c", "fetch.pruneTags=false", "-c", "pull.autostash=false",
                        "pull", "--ff-only", "--no-rebase", "--no-autostash", "--no-recurse-submodules",
                        approved.remote.name, "refs/heads/" + approved.destination]
            else:
                args = ["fetch", "--no-prune", "--no-prune-tags", "--no-recurse-submodules", approved.remote.name]
            final = self.approve_network(approved.operation, approved.remote.name,
                                         approved.destination, set_upstream=approved.set_upstream)
            if final != approved:
                raise GitError("Approved network destination changed; confirm again", outcome=CANCELLED)
            rc, stdout, stderr = self.run(*args, write=True)
            return Result(SUCCESS, redact((stdout + stderr).strip()) or f"{operation.title()} completed")
        except GitError as exc:
            return Result(exc.outcome, str(exc))

    def history(self, *, limit=100):
        limit = min(max(int(limit), 1), 1000)
        if self.state().head is None:
            return "No commits yet"
        return self.text("log", f"--max-count={limit}", "--date=iso-strict", "--no-decorate",
                         "--format=%H%n%ad · %an%n%s%n", truncate=True)

    def commit_details(self, commit_id):
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", commit_id):
            raise GitError("Invalid commit ID")
        return self.text("show", "--ignore-submodules=all", "--no-ext-diff", "--no-textconv", "--no-color",
                         "--format=fuller", "--stat", "--patch", commit_id, "--", truncate=True)
