"""Git repository discovery and metadata collection."""
import logging
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from multiprocessing.pool import ThreadPool
from multiprocessing import TimeoutError as PoolTimeout
from pathlib import Path
from .git_environment import git_environment
from .git_observation import run_read_only
from .scan_control import ScanCancelled, active_scan, checkpoint, current_control, progress

from .projects import (counterpart_is_pristine, ensure_project_id, is_ignored,
                        project_display_name, project_id,
                        project_location_key, repository_default_name,
                        repository_path_key, sanitize_pending_move)

GIT_TIMEOUT = 10
MAX_WORKERS = 8
CONTROLLED_SCAN_WORKERS = 4
PRUNE_DAYS = 30
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
SCHEME_RE = re.compile(r"^[a-zA-Z][\w+.-]*://")
SCPLIKE_RE = re.compile(r"^(?:[^@/\s]+@)?([^:/\s]+):([^\s]+)$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+(?::[0-9]{1,5})?$")
_REMOTE_SEGMENT_RE = re.compile(r"^[^/\\?#\s]+$")

log = logging.getLogger(__name__)


def utc_now_iso():
    """UTC timestamp in the registry's stable Z-suffixed format.

    Uses the timezone-aware API (datetime.utcnow is deprecated since
    Python 3.12) while keeping byte-identical output so persisted
    last_seen values keep comparing correctly as plain strings.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_remote(url):
    """Canonical, conservative ``host/path`` form for a Git remote."""
    if not isinstance(url, str) or not url.strip():
        return None
    u = url.strip().rstrip("/")
    if re.match(r"^[A-Za-z]:[\\/]", u) or any(c.isspace() for c in u):
        return None
    authority = path = None
    if SCHEME_RE.match(u):
        scheme, remainder = u.split("://", 1)
        if scheme.lower() not in {"http", "https", "ssh"}:
            return None
        authority, sep, path = remainder.partition("/")
        if not sep:
            return None
        if "@" in authority:
            authority = authority.rsplit("@", 1)[1]
    else:
        # SCP-like syntax requires an explicit user separator. Accepting a
        # bare ``name:value`` turns arbitrary URI schemes into remote hosts.
        if "@" not in u:
            return None
        match = SCPLIKE_RE.match(u)
        if not match:
            return None
        authority, path = match.groups()
    if not authority or not path or not _HOST_RE.fullmatch(authority):
        return None
    if ":" in authority:
        _host, port = authority.rsplit(":", 1)
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            return None
    path = path.removesuffix(".git")
    segments = path.split("/")
    if not segments or any(
            not _REMOTE_SEGMENT_RE.fullmatch(s) or s in {".", ".."}
            or "%" in s for s in segments):
        return None
    if not all(segments) or "?" in path or "#" in path:
        return None
    return f"{authority}/{path}"


def _git(path, *args):
    """Return Git stdout with trailing whitespace removed, or None on failure."""
    returncode, stdout, _stderr = _git_result(path, *args)
    return stdout if returncode == 0 else None


def _git_result(path, *args):
    """Run Git and retain return-code evidence for stateful observations."""
    checkpoint()
    try:
        r = run_read_only(
            ["git", "-C", str(path), *args],
            capture_output=True, text=True, timeout=GIT_TIMEOUT,
            encoding="utf-8", errors="replace",
            env=git_environment(read_only=True),
            creationflags=CREATE_NO_WINDOW,
        )
        # Porcelain status uses a leading space as the meaningful index-state
        # column. Only trim the line ending so callers retain Git's columns.
        return (r.returncode, r.stdout.rstrip(), r.stderr.rstrip())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (None, "", str(exc))
    finally:
        # Finish only this bounded read-only command; never start the next
        # command after cancellation, including timeout/error paths.
        checkpoint()


def scan_root_key(value):
    """Return a canonical comparison key for one configured scan root."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return os.path.normcase(str(Path(value.strip()).resolve()))
    except OSError:
        return os.path.normcase(os.path.abspath(os.path.normpath(value.strip())))


def path_is_under_root(path, root):
    """Return whether a path is the root itself or below that root."""
    path_key = scan_root_key(path)
    root_key = scan_root_key(root)
    if path_key is None or root_key is None:
        return False
    try:
        return os.path.commonpath((path_key, root_key)) == root_key
    except ValueError:
        return False


def _scan_root_problem(root, reason):
    """Build a user-visible, root-scoped scan failure record."""
    return {
        "kind": "scan_root",
        "status": "UNAVAILABLE",
        "complete": False,
        "root": str(root),
        "path": str(root),
        "reason": reason,
    }


def find_repo_dirs_with_status(roots, depth, skip_dirs):
    """Discover repositories and retain completeness evidence per root.

    The ordinary ``find_repo_dirs`` API remains list-shaped for compatibility,
    while this boundary distinguishes a successfully empty root from a root
    that could not be observed. Any directory-walk error makes that root
    incomplete so callers can preserve Projects below it.
    """
    skip = {s.lower() for s in skip_dirs}
    found = []
    problems = []
    seen_roots = set()
    for root in roots:
        checkpoint()
        progress(path=root)
        try:
            root_path = Path(root).resolve()
        except OSError:
            root_path = Path(os.path.abspath(str(root)))
        root_key = scan_root_key(str(root_path))
        if root_key is None or root_key in seen_roots:
            continue
        seen_roots.add(root_key)
        try:
            root_is_dir = root_path.is_dir()
        except OSError as exc:
            problems.append(_scan_root_problem(
                root, f"scan root unavailable: {exc}"))
            continue
        if not root_is_dir:
            problems.append(_scan_root_problem(
                root, "scan root unavailable: directory does not exist"))
            continue

        root_error = None
        stack = [(root_path, 0)]
        while stack:
            checkpoint()
            current, d = stack.pop()
            progress(path=current, directories=1)
            try:
                entries = list(os.scandir(current))
            except OSError as exc:
                if root_error is None:
                    root_error = f"scan root incomplete: {exc}"
                continue
            for entry in entries:
                checkpoint()
                if entry.name == ".git":
                    found.append(str(current))
                    progress(repositories=1)
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError as exc:
                    if root_error is None:
                        root_error = f"scan root incomplete: {exc}"
                    continue
                if not is_dir:
                    continue
                name = entry.name.lower()
                if name in skip or name.startswith("."):
                    continue
                if d < depth:
                    stack.append((Path(entry.path), d + 1))
        if root_error is not None:
            problems.append(_scan_root_problem(root, root_error))

    unique = {}
    for path in found:
        unique.setdefault(os.path.normcase(path), path)
    return sorted(unique.values()), problems


class ScanResult(list):
    """List-compatible repository discoveries with root completeness evidence."""

    def __init__(self, paths, problems=None):
        super().__init__(paths)
        self.problems = list(problems or [])


def find_repo_dirs(roots, depth, skip_dirs):
    """Return a list-compatible ScanResult with per-root discovery problems."""
    found, problems = find_repo_dirs_with_status(roots, depth, skip_dirs)
    return ScanResult(found, problems)


def empty_meta(path):
    """Blank metadata template for one repo directory."""
    p = Path(path)
    return {
        "path": str(p),
        "name": repository_default_name(str(p)),
        "branch": None,
        "head": None,
        "dirty": None,
        "staged": None,
        "unstaged": None,
        "untracked": None,
        "status_available": False,
        "ahead": None,
        "behind": None,
        "upstream": None,
        "upstream_state": "UNKNOWN",
        "sync_available": False,
        "remotes": [],
        "remote_names": [],
        "remote_name": None,
        "remote_reachable": None,
        "repository_observed": False,
        "last_commit_date": None,
        "last_commit_msg": None,
        "remote": None,
        "broken": False,
        "fingerprint": {"remotes": [], "root_commits": []},
        "worktrees": [],
        "worktrees_available": False,
    }


def _parse_porcelain_status(status):
    staged = unstaged = untracked = 0
    for line in (status or "").splitlines():
        if len(line) < 2:
            continue
        index, worktree = line[0], line[1]
        if index == "?" and worktree == "?":
            untracked += 1
        else:
            staged += index != " "
            unstaged += worktree != " "
    return staged, unstaged, untracked


WORKTREE_INSPECTION_UNAVAILABLE = "VERIFICATION_UNAVAILABLE"


def _worktree_state(path):
    """Return authoritative records, or an explicit unavailable result."""
    raw = _git(path, "worktree", "list", "--porcelain")
    if raw is None:
        return None
    records, current = [], None
    for line in raw.splitlines() + [""]:
        if line.startswith("worktree "):
            if current:
                records.append(current)
            current = {"path": line[9:], "branch": None, "head": None,
                       "locked": False, "prunable": False,
                       "current": False}
        elif current is not None and line.startswith("HEAD "):
            current["head"] = line[5:]
        elif current is not None and line.startswith("branch "):
            current["branch"] = line[7:].removeprefix("refs/heads/")
        elif current is not None and line == "locked":
            current["locked"] = True
        elif current is not None and line.startswith("prunable"):
            current["prunable"] = True
        elif not line and current:
            records.append(current)
            current = None
    repo_path = str(Path(path).resolve()).lower()
    for record in records:
        record["current"] = str(Path(record["path"]).resolve()).lower() == repo_path
    return records


def list_worktrees(path):
    """Read-only authoritative Worktree listing, or None if unavailable."""
    return _worktree_state(path)


def worktree_operation_error(returncode, stderr):
    """Map Git's textual failure evidence to a small stable category."""
    text = (stderr or "").lower()
    if "already exists" in text or "is not empty" in text:
        return "PATH_COLLISION"
    if "locked" in text:
        return "LOCKED_WORKTREE"
    if "not a valid" in text or "no such" in text:
        return "TARGET_MISSING"
    return "GIT_FAILURE"


def _run_git(path, *args):
    """Run a bounded Git mutation and retain stdout/stderr evidence."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args], capture_output=True, text=True,
            timeout=GIT_TIMEOUT, encoding="utf-8", errors="replace",
            env=git_environment(),
            creationflags=CREATE_NO_WINDOW)
        return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()
    except subprocess.TimeoutExpired as exc:
        return None, "", str(exc)
    except OSError as exc:
        return None, "", str(exc)


def create_worktree(repository, target_path, branch_or_commit):
    """Create a Worktree only after path and target checks, then verify it."""
    repo = Path(repository)
    target = Path(target_path)
    if not repo.is_dir() or not target_path or not branch_or_commit:
        return {"ok": False, "category": "INVALID_INPUT", "executed": False}
    if target.exists():
        return {"ok": False, "category": "PATH_COLLISION", "executed": False}
    try:
        if target.resolve().is_relative_to(repo.resolve()):
            return {"ok": False, "category": "TARGET_SCOPE", "executed": False}
    except (OSError, ValueError):
        return {"ok": False, "category": "TARGET_SCOPE", "executed": False}
    before_records = list_worktrees(repo)
    if before_records is None:
        return {"ok": False, "category": WORKTREE_INSPECTION_UNAVAILABLE,
                "executed": False, "verified": False}
    before = {item["path"].lower() for item in before_records}
    if not _git(repo, "rev-parse", "--show-toplevel"):
        return {"ok": False, "category": "IDENTITY_MISMATCH", "executed": False}
    expected_head = _git(repo, "rev-parse", branch_or_commit)
    if not expected_head:
        return {"ok": False, "category": "TARGET_MISSING", "executed": False}
    rc, out, err = _run_git(repo, "worktree", "add", str(target), branch_or_commit)
    after = list_worktrees(repo)
    if after is None:
        return {"ok": False, "category": WORKTREE_INSPECTION_UNAVAILABLE,
                "executed": rc is not None, "stdout": out, "stderr": err,
                "verified": False, "before_count": len(before),
                "after_count": None}
    created = next((item for item in after
                    if str(Path(item["path"]).resolve()).lower()
                    == str(target.resolve()).lower()), None)
    verified = (created is not None and target.is_dir()
                and created.get("head") == expected_head)
    return {"ok": rc == 0 and verified, "executed": rc is not None,
            "category": None if rc == 0 and verified else worktree_operation_error(rc, err),
            "stdout": out, "stderr": err, "verified": verified,
            "expected_head": expected_head,
            "before_count": len(before), "after_count": len(after)}


def remove_worktree(repository, target_path, *, confirm=False):
    """Remove an exact Git Worktree without deleting its directory blindly."""
    repo = Path(repository)
    target = Path(target_path)
    if not confirm:
        return {"ok": False, "category": "AUTHORIZATION_FAILURE", "executed": False}
    records = list_worktrees(repo)
    if records is None:
        return {"ok": False, "category": WORKTREE_INSPECTION_UNAVAILABLE,
                "executed": False, "verified": False}
    record = next((item for item in records
                   if str(Path(item["path"]).resolve()).lower() == str(target.resolve()).lower()), None)
    if record is None or record.get("current"):
        return {"ok": False, "category": "TARGET_MISSING", "executed": False}
    try:
        if target.resolve() != Path(record["path"]).resolve():
            return {"ok": False, "category": "IDENTITY_MISMATCH", "executed": False}
    except (OSError, KeyError, TypeError, ValueError):
        return {"ok": False, "category": "IDENTITY_MISMATCH", "executed": False}
    if not target.is_dir():
        return {"ok": False, "category": "TARGET_MISSING", "executed": False}
    status = _git(target, "status", "--porcelain")
    if status is None:
        return {"ok": False, "category": WORKTREE_INSPECTION_UNAVAILABLE,
                "executed": False, "verified": False}
    if status:
        return {"ok": False, "category": "DIRTY_STATE", "executed": False}
    nested = find_repo_dirs([str(target)], depth=2, skip_dirs=[])
    if any(Path(item).resolve() != target.resolve() for item in nested):
        return {"ok": False, "category": "NESTED_REPOSITORY", "executed": False}
    rc, out, err = _run_git(repo, "worktree", "remove", str(target))
    remaining = list_worktrees(repo)
    if remaining is None:
        return {"ok": False, "category": WORKTREE_INSPECTION_UNAVAILABLE,
                "executed": rc is not None, "stdout": out, "stderr": err,
                "verified": False}
    absent = (not target.exists()
              and not any(str(Path(item["path"]).resolve()).lower()
                          == str(target.resolve()).lower()
                          for item in remaining))
    return {"ok": rc == 0 and absent, "executed": rc is not None,
            "category": None if rc == 0 and absent else worktree_operation_error(rc, err),
            "stdout": out, "stderr": err, "verified": absent}


# Technical metadata keys that a positively-observed broken repository may
# legitimately invalidate. Identity/curation keys (path, name, project_id,
# folder_path, status, focus, pinned, added_at) are never included here;
# ``remote_reachable`` is a dead field (always None) and is intentionally
# excluded so observation validity never overwrites it.
TECHNICAL_OBSERVED_FIELDS = frozenset({
    "branch", "head", "dirty", "staged", "unstaged", "untracked",
    "status_available", "ahead", "behind", "upstream", "upstream_state",
    "sync_available", "remotes", "remote_names", "remote_name", "remote",
    "last_commit_date", "last_commit_msg", "broken", "repository_observed",
    "fingerprint", "worktrees", "worktrees_available",
})


def _unborn_repo_is_confirmed(path, cache):
    """Return True only when Git positively proves zero reachable commits.

    Runs at most once per collection and only on a failure path where a
    HEAD/log/roots lookup already failed with a real non-zero Git exit
    (never on timeout/OSError). ``rev-list --all --count`` is
    locale-independent: ``0`` means genuinely unborn/empty, anything else
    (or any failure) means the earlier failure was transient and must be
    treated as unobserved.
    """
    if cache.get("unborn") is not None:
        return cache["unborn"]
    try:
        rc, out, _err = _git_result(path, "rev-list", "--all", "--count")
    except ScanCancelled:
        raise
    except Exception:
        cache["unborn"] = False
        return False
    confirmed = (rc == 0 and (out or "").strip() == "0")
    cache["unborn"] = confirmed
    return confirmed


def repository_marker_evidence(path):
    """Classify local repository-marker evidence without running Git.

    Returns one of ``missing`` (path does not exist), ``not_dir`` (path is
    not a directory), ``absent`` (directory without a RepoManager-valid
    marker), ``valid`` (marker present and plausible), ``invalid``
    (marker present but filesystem-proven corrupt), or ``ambiguous``
    (OSError/unreadable — fail closed toward preserving cached state).

    The marker model mirrors discovery: a ``.git`` directory (normal
    checkout, must contain ``HEAD``) or a ``.git`` file starting with
    ``gitdir:`` (linked worktree). Bare layouts (top-level ``HEAD`` plus
    ``objects`` with no ``.git`` child) are never positively condemned
    here: a failing bare is ``ambiguous``. No Git output or locale text is
    consulted.
    """
    try:
        p = Path(path)
    except Exception:
        return "ambiguous"
    try:
        exists = p.exists()
    except OSError:
        return "ambiguous"
    if not exists:
        return "missing"
    try:
        is_dir = p.is_dir()
    except OSError:
        return "ambiguous"
    if not is_dir:
        return "not_dir"
    dotgit = p / ".git"
    try:
        dot_exists = dotgit.exists()
    except OSError:
        return "ambiguous"
    if not dot_exists:
        try:
            head_is_file = (p / "HEAD").is_file()
            objects_is_dir = (p / "objects").is_dir()
        except OSError:
            return "ambiguous"
        if head_is_file and objects_is_dir:
            return "ambiguous"
        return "absent"
    try:
        dot_is_dir = dotgit.is_dir()
        dot_is_file = dotgit.is_file()
    except OSError:
        return "ambiguous"
    if dot_is_dir and not dot_is_file:
        try:
            head_ok = (dotgit / "HEAD").is_file()
        except OSError:
            return "ambiguous"
        return "valid" if head_ok else "invalid"
    if dot_is_file and not dot_is_dir:
        try:
            content = dotgit.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "ambiguous"
        text = content.strip()
        if not text.lower().startswith("gitdir:"):
            return "invalid"
        target_raw = text[7:].strip()
        if not target_raw:
            return "invalid"
        try:
            target = Path(target_raw)
            if not target.is_absolute():
                target = dotgit.parent / target_raw
            target_exists = target.exists()
        except OSError:
            return "ambiguous"
        return "valid" if target_exists else "invalid"
    return "ambiguous"


def collect_metadata_observation(path):
    """Collect metadata plus transient field-level observation validity.

    Returns ``(metadata, observed)`` where ``observed`` is a frozenset of
    top-level metadata keys whose values were successfully observed during
    this call — including legitimate absences (detached ``branch=None``,
    explicit no-upstream ``NONE``, unborn ``head=None``, empty remotes).
    Failed value observations preserve cached state. ``status_available`` is
    current validity evidence, including failure: cached counts must never
    make an unavailable working-tree observation appear clean.

    The validity set is transient: never persist it to repos.json and never
    expose it in the project schema.
    """
    checkpoint()
    meta = empty_meta(path)
    observed = set()
    p = Path(path)
    state_cache = {}

    git_dir_rc, _git_dir_out, _git_dir_err = _git_result(
        p, "rev-parse", "--git-dir")
    if git_dir_rc is None:
        # Observation infrastructure failed (timeout/OSError/missing
        # executable). This is NOT positive evidence the repository is
        # broken: retain cached values but invalidate working-tree certainty.
        return meta, frozenset({"status_available"})
    if git_dir_rc != 0:
        # A non-zero Git exit alone is NOT proof of a broken repository
        # (permission, safe.directory, transient I/O, translated stderr).
        # Only local positive evidence condemns the location; a plausible
        # marker with failing Git stays UNKNOWN/unobserved and preserves
        # cached state. stderr is diagnostic only, never sole authority.
        marker = repository_marker_evidence(p)
        if marker in ("missing", "not_dir", "absent", "invalid"):
            meta["broken"] = True
            observed.update(TECHNICAL_OBSERVED_FIELDS)
            return meta, frozenset(observed)
        return meta, frozenset({"status_available"})
    meta["repository_observed"] = True
    observed.update({"repository_observed", "broken"})

    # -- BRANCH: rc==0 means observed, even when stdout is empty (detached).
    branch = _git(p, "branch", "--show-current")
    branch_observed = branch is not None
    branch_value = None
    if branch_observed:
        branch_value = branch or None  # detached HEAD -> None (observed)
        meta["branch"] = branch_value
        observed.add("branch")

    # -- HEAD: distinguish genuinely unborn (observed None) from transient.
    head_rc, head_out, _head_err = _git_result(p, "rev-parse", "HEAD")
    if head_rc == 0 and head_out:
        meta["head"] = head_out
        observed.add("head")
    elif (head_rc is not None and head_rc != 0
            and _unborn_repo_is_confirmed(p, state_cache)):
        meta["head"] = None
        observed.add("head")
    # else: unobserved (timeout/OSError with rc None, or transient non-zero).

    # -- STATUS: success (even empty clean output) is observed.
    observed.add("status_available")
    status = _git(p, "status", "--porcelain")
    if status is not None:
        meta["staged"], meta["unstaged"], meta["untracked"] = (
            _parse_porcelain_status(status))
        meta["dirty"] = meta["staged"] + meta["unstaged"] + meta["untracked"]
        meta["status_available"] = True
        observed.update(
            {"dirty", "staged", "unstaged", "untracked", "status_available"})

    # -- UPSTREAM: TRACKED vs explicit NONE vs detached/unborn legit-clear
    # vs generic transient (preserve).
    upstream_rc, upstream, upstream_error = _git_result(
        p, "rev-parse", "--abbrev-ref", "@{upstream}")
    upstream_tracked = False
    upstream_legit_absent = False
    if upstream_rc == 0 and upstream:
        meta["upstream"] = upstream
        meta["upstream_state"] = "TRACKED"
        observed.update({"upstream", "upstream_state"})
        upstream_tracked = True
    elif upstream_rc is not None and upstream_rc != 0:
        if "no upstream configured" in (upstream_error or "").casefold():
            meta["upstream"] = None
            meta["upstream_state"] = "NONE"
            observed.update({"upstream", "upstream_state"})
            upstream_legit_absent = True
        elif branch_observed and branch_value is None:
            # Detached HEAD cannot have an upstream; stale TRACKED must be
            # cleared without relying on locale-fragile stderr text.
            meta["upstream"] = None
            meta["upstream_state"] = "UNKNOWN"
            observed.update({"upstream", "upstream_state"})
            upstream_legit_absent = True
        elif _unborn_repo_is_confirmed(p, state_cache):
            meta["upstream"] = None
            meta["upstream_state"] = "UNKNOWN"
            observed.update({"upstream", "upstream_state"})
            upstream_legit_absent = True
        # else: generic failure -> unobserved, preserve cached upstream facts.

    # -- AHEAD/BEHIND: atomic success updates; legit absence clears;
    # otherwise preserve cached counts.
    if upstream_tracked:
        ahead = _git(p, "rev-list", "--count", "@{upstream}..HEAD")
        behind = _git(p, "rev-list", "--count", "HEAD..@{upstream}")
        if (ahead is not None and behind is not None
                and ahead.strip().isdigit() and behind.strip().isdigit()):
            meta["ahead"] = int(ahead.strip())
            meta["behind"] = int(behind.strip())
            meta["sync_available"] = True
            observed.update({"ahead", "behind", "sync_available"})
    elif upstream_legit_absent:
        meta["ahead"] = None
        meta["behind"] = None
        meta["sync_available"] = False
        observed.update({"ahead", "behind", "sync_available"})

    # -- LAST COMMIT: success observed; confirmed unborn observed None;
    # transient preserves.
    log_rc, log_out, _log_err = _git_result(
        p, "log", "-1", "--format=%cs|%s")
    if log_rc == 0:
        if log_out and "|" in log_out:
            d, m = log_out.split("|", 1)
            meta["last_commit_date"] = d
            meta["last_commit_msg"] = m[:60]
        observed.update({"last_commit_date", "last_commit_msg"})
    elif (log_rc is not None and log_rc != 0
            and _unborn_repo_is_confirmed(p, state_cache)):
        observed.update({"last_commit_date", "last_commit_msg"})

    # -- REMOTE: rc==0 observed even when empty (legit removal clears).
    remotes = set()
    remote_names = set()
    origin = None
    first = None
    first_name = None
    remote_v = _git(p, "remote", "-v")
    remote_observed = remote_v is not None
    if remote_observed:
        if remote_v:
            for line in remote_v.splitlines():
                parts = line.split()
                if len(parts) < 2 or not line.endswith("(fetch)"):
                    continue
                name, url = parts[0], parts[1]
                remote_names.add(name)
                norm = normalize_remote(url)
                if norm:
                    remotes.add(norm)
                if name == "origin" and norm:
                    origin = norm
                if first is None and norm:
                    first = norm
                    first_name = name
        meta["remote"] = origin or first
        meta["remote_name"] = "origin" if origin else first_name
        meta["remotes"] = sorted(remotes)
        meta["remote_names"] = sorted(remote_names)
        observed.update({"remote", "remotes", "remote_names", "remote_name"})
        remote_remotes = sorted(remotes)
    else:
        remote_remotes = None

    # -- WORKTREES: list (even single current-only) observed; None preserves.
    worktrees = _worktree_state(p)
    if worktrees is not None:
        meta["worktrees"] = worktrees
        meta["worktrees_available"] = True
        observed.update({"worktrees", "worktrees_available"})

    # -- FINGERPRINT ROOT COMMITS: success observed; unborn observed [];
    # transient preserves previous roots.
    roots_rc, roots_raw, _roots_err = _git_result(
        p, "rev-list", "--max-parents=0", "HEAD")
    roots_observed = False
    roots_list = None
    if roots_rc == 0:
        roots_list = sorted(roots_raw.splitlines()) if roots_raw else []
        roots_observed = True
    elif (roots_rc is not None and roots_rc != 0
            and _unborn_repo_is_confirmed(p, state_cache)):
        roots_list = []
        roots_observed = True
    if remote_observed or roots_observed:
        fp_remotes = (remote_remotes if remote_observed
                      else sorted(remotes))
        # remote_remotes is None only when unobserved; fall back to default
        # empty here — the apply layer merges with cached fingerprint parts
        # using the observed set, so this placeholder is never persisted
        # over cached state for unobserved parts.
        if fp_remotes is None:
            fp_remotes = []
        fp_roots = roots_list if roots_observed else []
        meta["fingerprint"] = {
            "remotes": fp_remotes,
            "root_commits": fp_roots,
        }
        if roots_observed:
            observed.add("fingerprint")
    return meta, frozenset(observed)


def collect_metadata(path):
    """Gather live git metadata for one repo directory. Returns dict.

    Compatibility wrapper around :func:`collect_metadata_observation`;
    transient observation validity is discarded. Background refresh and
    scan-merge paths must use the observation variant so failed fields
    preserve cached values.
    """
    meta, _observed = collect_metadata_observation(path)
    return meta


def apply_observed_fields(target, meta, observed):
    """Update ``target`` only with successfully observed ``meta`` fields.

    ``observed`` is a set of top-level metadata keys (or None for legacy
    fully-observed records). Identity/curation keys and the dead
    ``remote_reachable`` field are never overwritten here. ``fingerprint``
    merges part-wise: ``remotes`` follows the ``remotes`` observation and
    ``root_commits`` follows the ``fingerprint`` observation.
    """
    protected = {
        "path", "name", "project_id", "folder_path", "status", "focus",
        "pinned", "added_at",
    }
    if observed is None:
        for key, value in meta.items():
            if key in protected or key == "_observed_fields":
                continue
            if key == "fingerprint":
                if isinstance(value, dict):
                    target["fingerprint"] = dict(value)
                continue
            target[key] = value
        return target
    observed_set = set(observed or ())
    for key in observed_set:
        if key in protected or key == "_observed_fields":
            continue
        if key == "fingerprint":
            # Root-commit part observed; remotes part follows "remotes".
            new_fp = meta.get("fingerprint")
            if not isinstance(new_fp, dict):
                continue
            current_fp = target.get("fingerprint")
            if not isinstance(current_fp, dict):
                current_fp = {"remotes": [], "root_commits": []}
            else:
                current_fp = dict(current_fp)
            current_fp["root_commits"] = list(
                new_fp.get("root_commits", []))
            if "remotes" in observed_set:
                current_fp["remotes"] = list(new_fp.get("remotes", []))
            target["fingerprint"] = current_fp
            continue
        if key == "remote_reachable":
            continue
        if key not in meta:
            continue
        target[key] = meta[key]
    # Fingerprint remotes part when only the remote group was observed
    # (roots unobserved, so "fingerprint" absent but "remotes" present).
    if ("remotes" in observed_set and "fingerprint" not in observed_set
            and isinstance(meta.get("fingerprint"), dict)):
        current_fp = target.get("fingerprint")
        if not isinstance(current_fp, dict):
            current_fp = {"remotes": [], "root_commits": []}
        else:
            current_fp = dict(current_fp)
        current_fp["remotes"] = list(meta["fingerprint"].get("remotes", []))
        target["fingerprint"] = current_fp
    return target


def match_move_candidates(stale_entries, fresh_items, suppressed=None):
    """Pure matcher: pair vanished entries with new discoveries.

    stale_entries: registry dicts (path/remote/fingerprint).
    fresh_items:   list of (path, fingerprint-dict-or-None), sorted.
    suppressed:    iterable of (old_path_lower, new_path_lower) pairs.

    Returns deterministic suggestion dicts sorted by (old, new):
      {kind:"move", category: "strong"|"possible"|"ambiguous",
       old_path, new_path|new_paths, name, evidence}
    Lineage evidence NEVER migrates anything — output is advisory only.
    """
    supp_set = set()
    for s in suppressed or []:
        if isinstance(s, dict):
            supp_set.add((str(s.get("old", "")).lower(),
                          str(s.get("new", "")).lower()))
        else:
            o, n = s
            supp_set.add((str(o).lower(), str(n).lower()))

    def evidence_for(entry):
        fp = entry.get("fingerprint") if isinstance(entry, dict) else None
        remotes = set()
        if isinstance(fp, dict):
            remotes = {r.lower() for r in fp.get("remotes", [])
                       if isinstance(r, str)}
        if not remotes and isinstance(entry.get("remote"), str):
            remotes = {entry["remote"].lower()}
        roots = set()
        if isinstance(fp, dict):
            roots = {r for r in fp.get("root_commits", [])
                     if isinstance(r, str)}
        return remotes, roots

    def fresh_evidence(fingerprint):
        if not isinstance(fingerprint, dict):
            return set(), set()
        remotes = {r.lower() for r in fingerprint.get("remotes", [])
                   if isinstance(r, str)}
        return remotes, {r for r in fingerprint.get("root_commits", [])
                         if isinstance(r, str)}

    # Index fresh items by the evidence dimensions that can produce a match.
    # This avoids comparing every stale entry with every fresh entry while
    # preserving the same remote, root-commit, and folder-name matches.
    ordered_fresh = sorted(fresh_items, key=lambda x: x[0].lower())
    by_remote, by_root, by_folder = {}, {}, {}
    for _idx, (new_path, fp) in enumerate(ordered_fresh):
        n_remotes, n_roots = fresh_evidence(fp)
        for r in n_remotes:
            by_remote.setdefault(r, []).append(_idx)
        for rt in n_roots:
            by_root.setdefault(rt, []).append(_idx)
        by_folder.setdefault(Path(new_path).name.lower(), []).append(_idx)

    pairs = []
    for entry in sorted(stale_entries,
                        key=lambda e: str(e.get("path", "")).lower()):
        old_path = entry.get("path")
        if not isinstance(old_path, str):
            continue
        old_remotes, old_roots = evidence_for(entry)
        old_folder = Path(old_path).name.lower()
        candidates = set()
        for r in old_remotes:
            candidates.update(by_remote.get(r, ()))
        for rt in old_roots:
            candidates.update(by_root.get(rt, ()))
        if len(old_folder) >= 5:
            candidates.update(by_folder.get(old_folder, ()))
        for idx in sorted(candidates):
            new_path, fp = ordered_fresh[idx]
            if (old_path.lower(), new_path.lower()) in supp_set:
                continue
            new_remotes, new_roots = fresh_evidence(fp)
            evidence = []
            shared_remote = bool(old_remotes & new_remotes)
            shared_roots = old_roots & new_roots
            same_folder = old_folder == Path(new_path).name.lower()
            folder_eq = same_folder and len(old_folder) >= 5
            if shared_remote:
                evidence.append("Same normalized remote: "
                                + sorted(old_remotes & new_remotes)[0])
            if shared_roots:
                evidence.append(f"Root history matches ({len(shared_roots)})")
            if folder_eq:
                evidence.append("Folder name matches")
            if not evidence:
                continue
            # A shared remote alone can describe forks, mirrors, or copied
            # checkouts. Batch-strength evidence requires history or the same
            # stable folder identity in addition to the remote.
            if shared_roots or (shared_remote and same_folder):
                category = "strong"
            else:
                category = "possible"
            pairs.append({
                "kind": "move",
                "category": category,
                "old_path": old_path,
                "old_project_id": project_id(entry),
                "new_path": new_path,
                "name": entry.get("name") or Path(old_path).name,
                "evidence": evidence,
                "identity": {
                    "remotes": sorted(new_remotes),
                    "root_commits": sorted(new_roots),
                },
            })

    # ambiguity: contested old or new sides collapse into grouped problems
    by_old, by_new = {}, {}
    for p in pairs:
        by_old.setdefault(p["old_path"].lower(), []).append(p)
        by_new.setdefault(p["new_path"].lower(), []).append(p)
    contested = {id(p) for p in pairs
                 if len(by_old[p["old_path"].lower()]) > 1
                 or len(by_new[p["new_path"].lower()]) > 1}

    final, emitted_groups = [], set()
    for p in pairs:
        if id(p) not in contested:
            final.append(p)
            continue
        ok = p["old_path"].lower()
        nk = p["new_path"].lower()
        gkey = ("old", ok) if len(by_old[ok]) > 1 else ("new", nk)
        if gkey in emitted_groups:
            continue
        emitted_groups.add(gkey)
        if gkey[0] == "old":
            members = by_old[ok]
            final.append({
                "kind": "move", "category": "ambiguous",
                "old_path": p["old_path"],
                "new_paths": [m["new_path"] for m in members],
                "name": p["name"],
                "evidence": ["Multiple candidate locations"],
                "candidates": [dict(member) for member in members],
            })
        else:
            members = by_new[nk]
            final.append({
                "kind": "move", "category": "ambiguous",
                "old_paths": [m["old_path"] for m in members],
                "new_path": p["new_path"],
                "name": Path(p["new_path"]).name,
                "evidence": ["Matches multiple vanished entries"],
                "candidates": [dict(member) for member in members],
            })
    return sorted(final, key=lambda s: (
        str(s.get("old_path") or s.get("old_paths")[0]).lower(),
        str(s.get("new_path") or "").lower()))


def annotate_counterpart_provenance(suggestions, created_ids):
    """Attach transient scan-created project_ids to unambiguous suggestions.

    Only unambiguous suggestions and exact transient candidate pairs gain
    ``new_project_id``. Ambiguous groups themselves never gain counterpart
    identity or pending/batch eligibility; manual pair selection is a separate
    explicit approval boundary. Never infer provenance from registry paths.
    """
    for s in suggestions or []:
        if not isinstance(s, dict):
            continue
        if s.get("category") == "ambiguous":
            # Pair provenance remains transient and requires explicit manual
            # approval. Never promote a contested group into pending/batch state.
            annotate_counterpart_provenance(s.get("candidates", []), created_ids)
            continue
        if "new_path" not in s:
            continue
        new_path = s.get("new_path")
        if not isinstance(new_path, str):
            continue
        counterpart_id = (created_ids or {}).get(
            repository_path_key(new_path))
        if counterpart_id is not None:
            s["new_project_id"] = counterpart_id
    return suggestions


def persist_pending_moves(by_path, suggestions, observed_by_key, now_iso):
    """Record durable pending_move relations on stale OLD Projects.

    Only for unambiguous suggestions carrying exact R2.6B counterpart
    provenance whose fresh identity was actually observed. Never overwrites
    a different pending relation (move chains stay unresolved, never
    collapsed). Silent no-ops otherwise; the in-memory suggestion itself is
    unaffected.
    """
    for s in suggestions or []:
        if not isinstance(s, dict):
            continue
        if s.get("category") == "ambiguous" or "new_path" not in s:
            continue
        counterpart_id = s.get("new_project_id")
        new_path = s.get("new_path")
        if (not isinstance(counterpart_id, str)
                or not counterpart_id.strip()
                or not isinstance(new_path, str)):
            continue
        if s.get("category") not in ("strong", "possible"):
            continue
        identity = s.get("identity")
        if not isinstance(identity, dict):
            continue
        id_remotes = identity.get("remotes")
        id_roots = identity.get("root_commits")
        if not isinstance(id_remotes, list) or not isinstance(id_roots, list):
            continue
        old_path = s.get("old_path")
        old_entry = (by_path.get(repository_path_key(old_path))
                     if isinstance(old_path, str) else None)
        if not isinstance(old_entry, dict):
            continue
        existing = old_entry.get("pending_move")
        if isinstance(existing, dict) and existing.get(
                "new_project_id") != counterpart_id:
            log.info("keeping existing pending move for %s; "
                     "not collapsing chain", old_entry.get("path"))
            continue
        fresh_key = repository_path_key(new_path)
        if fresh_key is None or fresh_key not in observed_by_key:
            continue
        fresh_obs = observed_by_key[fresh_key]
        if fresh_obs is not None and ("fingerprint" not in fresh_obs
                                      or "remotes" not in fresh_obs):
            continue
        old_entry["pending_move"] = {
            "new_project_id": counterpart_id,
            "new_path": new_path,
            "identity": {"remotes": list(id_remotes),
                         "root_commits": list(id_roots)},
            "category": s["category"],
            "detected_at": now_iso,
        }


def _pending_rehydration_evidence(pending, old_path, new_path):
    """Rebuild presentation evidence from durable pending state."""
    evidence = []
    roots = pending["identity"]["root_commits"]
    remotes = pending["identity"]["remotes"]
    if roots:
        evidence.append(f"Root history matches ({len(roots)})")
    if remotes:
        evidence.append("Same normalized remote: " + remotes[0])
    old_folder = Path(old_path).name.lower() \
        if isinstance(old_path, str) else ""
    new_folder = Path(new_path).name.lower() \
        if isinstance(new_path, str) else ""
    if old_folder and old_folder == new_folder and len(old_folder) >= 5:
        evidence.append("Folder name matches")
    if not evidence:
        evidence.append("Pending relocation revalidated")
    return evidence


def rehydrate_pending_moves(projects, scanned_keys, meta_by_key,
                            observed_by_key, move_suppressions=None,
                            existing_suggestions=None):
    """Rebuild advisory move suggestions from durable pending_move relations.

    The persisted project_id relation identifies WHICH counterpart;
    fingerprint identity remains a revalidation mechanism, never lookup
    identity. Pairs already covered by an actionable suggestion from this
    same scan are skipped so one relocation is never offered twice.
    Returns ``(suggestions, conflicts)``. Invalid, suppressed, or
    contradicted relations are stripped in place (fail closed); transient
    uncertainty defers silently with the relation retained; curated targets
    yield non-destructive conflict problems without absorption.
    """
    supp_set = set()
    for s in move_suppressions or []:
        if isinstance(s, dict):
            supp_set.add((str(s.get("old", "")).lower(),
                          str(s.get("new", "")).lower()))
        else:
            try:
                old_suppressed, new_suppressed = s
            except (TypeError, ValueError):
                continue
            supp_set.add((str(old_suppressed).lower(),
                          str(new_suppressed).lower()))

    suggestions = []
    conflicts = []
    covered = set()
    for s in existing_suggestions or []:
        if not isinstance(s, dict):
            continue
        if s.get("category") == "ambiguous" or "new_path" not in s:
            continue
        old_covered = s.get("old_path")
        new_covered = s.get("new_path")
        if isinstance(old_covered, str) and isinstance(new_covered, str):
            covered.add((old_covered.lower(), new_covered.lower()))
    for old in list(projects):
        if not isinstance(old, dict) or "pending_move" not in old:
            continue
        pending, reason = sanitize_pending_move(old)
        if pending is None:
            if reason is not None:
                log.warning("dropping malformed pending move on %s: %s",
                            old.get("path"), reason)
                old.pop("pending_move", None)
            continue
        old_path = old.get("path")
        if not isinstance(old_path, str) or not old_path.strip():
            old_path = old.get("folder_path")
        old_key = project_location_key(old)
        new_key = repository_path_key(pending["new_path"])
        if (not isinstance(old_path, str) or not old_path.strip()
                or old_key is None or new_key is None):
            log.warning("dropping pending move with unusable paths on %s",
                        old.get("path"))
            old.pop("pending_move", None)
            continue
        if (old_path.lower(), pending["new_path"].lower()) in supp_set:
            log.info("pending move suppressed for %s", old_path)
            old.pop("pending_move", None)
            continue
        if (old_path.lower(), pending["new_path"].lower()) in covered:
            # This scan's own matcher already offers this exact relocation;
            # keep the durable relation for later without doubling the offer.
            continue
        target = next((p for p in projects
                       if p is not old and isinstance(p, dict)
                       and project_id(p) == pending["new_project_id"]), None)
        if target is None:
            if new_key in scanned_keys:
                log.warning("pending move target %s is gone; "
                            "relation dropped", pending["new_path"])
                old.pop("pending_move", None)
            continue
        if project_location_key(target) != new_key:
            log.warning("pending move target now at a different path; "
                        "relation dropped")
            old.pop("pending_move", None)
            continue
        if old_key in scanned_keys:
            log.warning("pending move source reappeared; relation dropped")
            old.pop("pending_move", None)
            continue
        if not counterpart_is_pristine(target, pending["new_path"]):
            conflicts.append({
                "kind": "move", "category": "ambiguous",
                "old_path": old_path,
                "new_paths": [pending["new_path"]],
                "name": old.get("name") or Path(
                    pending["new_path"]).name,
                "evidence": ["Move target has user-owned changes; kept "
                             "without absorption. Keep Both to dismiss."],
            })
            continue
        live = meta_by_key.get(new_key)
        live_obs = observed_by_key.get(new_key, "missing")
        if live is None or live_obs == "missing" or (
                live_obs is not None
                and ("fingerprint" not in live_obs
                     or "remotes" not in live_obs)):
            continue
        if live.get("broken"):
            continue
        live_fp = live.get("fingerprint")
        if not isinstance(live_fp, dict):
            continue
        live_remotes = live_fp.get("remotes", [])
        live_roots = live_fp.get("root_commits", [])
        if (not isinstance(live_remotes, list)
                or not isinstance(live_roots, list)):
            continue
        live_identity = {
            "remotes": sorted(
                value.casefold() for value in live_remotes
                if isinstance(value, str)),
            "root_commits": sorted(
                value for value in live_roots if isinstance(value, str)),
        }
        if live_identity != pending["identity"]:
            log.warning("pending move identity conflicts with live target; "
                        "relation dropped")
            old.pop("pending_move", None)
            continue
        suggestions.append({
            "kind": "move", "category": pending["category"],
            "old_path": old_path, "new_path": pending["new_path"],
            "name": old.get("name") or Path(pending["new_path"]).name,
            "evidence": _pending_rehydration_evidence(
                pending, old_path, pending["new_path"]),
            "identity": {"remotes": list(pending["identity"]["remotes"]),
                         "root_commits": list(
                             pending["identity"]["root_commits"])},
            "new_project_id": pending["new_project_id"],
        })
    return suggestions, conflicts


def move_target_identity(path):
    """Re-observe identity evidence stored with an advisory move.

    Observation-aware: fingerprint components used for identity comparison
    must have actually been observed. Unobserved (failed) fingerprint
    evidence returns None (identity unavailable) so the move revalidation
    path cancels via identity mismatch instead of authorizing
    ``unobserved [] == legitimately observed []``. Genuinely observed
    empty fingerprints (no remote, unborn) remain valid empty evidence.
    """
    metadata, observed = collect_metadata_observation(path)
    if metadata.get("broken"):
        return None
    observed_set = set(observed or ())
    if "remotes" not in observed_set or "fingerprint" not in observed_set:
        return None
    fingerprint = metadata.get("fingerprint") or {}
    return {
        "remotes": sorted(
            value.casefold() for value in fingerprint.get("remotes", [])
            if isinstance(value, str)),
        "root_commits": sorted(
            value for value in fingerprint.get("root_commits", [])
            if isinstance(value, str)),
    }


def merge_scan(existing_projects, scanned_paths, move_suppressions=None,
               protected_paths=None, failed_roots=None):
    """Merge scan results into the stored project list.

    Return (projects, problems). Retained projects keep their curation;
    new repositories receive defaults. Unreadable known repositories retain
    their records while Git observations become unknown. Invalid records
    are skipped; named folder-only projects remain valid.
    Vanished entries are additionally paired against new discoveries to
    produce advisory move suggestions — never automatic migrations.
    Entries referenced by an open suggestion (protected_paths) are exempt
    from the 30-day prune while the suggestion remains actionable.
    """
    checkpoint()
    failed_root_values = []
    for failed in failed_roots or []:
        if isinstance(failed, dict):
            value = failed.get("root") or failed.get("path")
        else:
            value = failed
        if scan_root_key(value) is not None:
            failed_root_values.append(value)

    def under_failed_root(path):
        return any(path_is_under_root(path, root)
                   for root in failed_root_values)

    by_path = {}
    standalone_projects = []
    standalone_by_folder = {}
    for i, p in enumerate(existing_projects):
        path = p.get("path") if isinstance(p, dict) else None
        folder_path = p.get("folder_path") if isinstance(p, dict) else None
        name = p.get("name") if isinstance(p, dict) else None
        if not isinstance(path, str) or not path.strip():
            # Folder-only Projects are valid registry records but are not
            # repository scan targets. Keep them intact so a repository scan
            # cannot silently discard user-managed Project metadata.
            if (isinstance(folder_path, str) and folder_path.strip()
                    and isinstance(name, str) and name.strip()):
                ensure_project_id(p)
                standalone_projects.append(p)
                standalone_by_folder[repository_path_key(folder_path)] = p
                continue
            log.warning("scan merge skipped registry record %d: no valid path",
                        i)
            continue
        if not isinstance(name, str) or not name.strip():
            log.warning("scan merge skipped registry record %d (%s): "
                        "no valid name", i, path)
            continue
        by_path[repository_path_key(path)] = p

    now = utc_now_iso()
    problems = []

    # parallel metadata collection (I/O-bound subprocess calls); one failing
    # repo must not abort the results of all others. Validity sets are
    # transient and never persisted.
    scanned_paths = list(scanned_paths)
    metas = []
    observed_list = []
    if scanned_paths:
        control = current_control()
        workers = CONTROLLED_SCAN_WORKERS if control else MAX_WORKERS
        progress(phase="Inspecting")

        def inspect_path(path):
            with active_scan(control):
                progress(path=path)
                return collect_metadata_observation(path)

        pool = ThreadPool(processes=workers)
        futures = []
        next_path = iter(scanned_paths)
        try:
            for sp in list(scanned_paths[:workers]):
                checkpoint()
                futures.append((next(next_path), pool.apply_async(inspect_path, (sp,))))
            while futures:
                checkpoint()
                sp, fut = futures[0]
                try:
                    while True:
                        checkpoint()
                        try:
                            result = fut.get(timeout=0.1)
                            break
                        except PoolTimeout:
                            continue
                except ScanCancelled:
                    raise
                except Exception:
                    log.exception("metadata collection failed for %s", sp)
                    broken_meta = empty_meta(sp)
                    broken_meta["broken"] = True
                    metas.append(broken_meta)
                    observed_list.append(frozenset(TECHNICAL_OBSERVED_FIELDS))
                    futures.pop(0)
                    following = next(next_path, None)
                    if following is not None:
                        checkpoint()
                        futures.append((following, pool.apply_async(inspect_path, (following,))))
                    continue
                if isinstance(result, tuple) and len(result) == 2:
                    meta_result, observed_result = result
                    metas.append(meta_result)
                    if observed_result is None:
                        observed_list.append(None)
                    else:
                        observed_list.append(frozenset(observed_result))
                elif isinstance(result, dict):
                    # Legacy mocked collect_metadata shape; treat present
                    # keys as fully observed for backward compatibility.
                    metas.append(result)
                    observed_list.append(None)
                else:
                    log.error("discarding malformed metadata result for %s",
                              sp)
                    broken_meta = empty_meta(sp)
                    broken_meta["broken"] = True
                    metas.append(broken_meta)
                    observed_list.append(frozenset(TECHNICAL_OBSERVED_FIELDS))
                progress(inspected=1)
                futures.pop(0)
                following = next(next_path, None)
                if following is not None:
                    checkpoint()
                    futures.append((following, pool.apply_async(inspect_path, (following,))))
        finally:
            pool.close()
            pool.join()
        checkpoint()

    projects = list(standalone_projects)
    seen = set()
    upgraded_ids = set()
    # R2.6B: transient provenance for scan-created Projects. Maps the new
    # path key to the freshly assigned project_id so an unambiguous move
    # suggestion can later prove its target is the scan-created counterpart
    # (and not a legitimate user-owned Project). Never persisted.
    created_counterpart_ids = {}
    for old in by_path.values():
        ensure_project_id(old)
    for sp, meta, observed in zip(scanned_paths, metas, observed_list):
        checkpoint()
        key = repository_path_key(sp)
        old = by_path.get(key)
        if old is None:
            candidate = standalone_by_folder.get(key)
            if candidate is not None:
                old = candidate
                standalone_projects.remove(candidate)
                standalone_by_folder.pop(key, None)
                upgraded_ids.add(id(candidate))
                old["path"] = sp
                old.pop("folder_path", None)

        if meta["broken"]:
            problems.append({
                "kind": "repository",
                "path": sp,
                "reason": ".git exists but is not a valid repository",
            })
            if old:
                # Retain the logical Project and curation, but replace every
                # Git-derived observation so a prior clean/PASS snapshot cannot
                # be presented as current after Git becomes unavailable.
                # Positively-broken observations carry full technical validity;
                # infrastructure failures (empty observed) preserve instead and
                # never reach this branch with stale invalidation because
                # collect_metadata_observation returns broken=False there.
                meta.pop("path", None)
                meta.pop("name", None)
                if observed is None:
                    old.update(meta)
                else:
                    apply_observed_fields(old, meta, observed)
                old["last_seen"] = now
                projects.append(old)
                seen.add(key)
            continue

        seen.add(key)
        if old:
            ensure_project_id(old)
            if id(old) in upgraded_ids:
                projects.remove(old)
            meta.pop("path", None)  # keep original path identity/casing
            # Scanner refresh must not overwrite curated names or rename note
            # keys. Legacy defaults receive a derived UI label instead.
            meta.pop("name", None)
            if observed is None:
                old.update(meta)
            else:
                apply_observed_fields(old, meta, observed)
            old["last_seen"] = now
            projects.append(old)
        else:
            created_counterpart_ids[key] = ensure_project_id(meta)
            meta.update({
                "added_at": now,
                "last_seen": now,
                "status": "idea",
                "focus": "",
                "pinned": False,
            })
            projects.append(meta)

    # advisory move suggestions BEFORE retention so entries referenced by
    # an open suggestion are shielded from the prune below. Purely
    # informational — nothing here migrates or mutates either side.
    scanned_keys = {repository_path_key(sp) for sp in scanned_paths}
    stale_entries = [p for k, p in by_path.items()
                     if k not in seen and k not in scanned_keys
                     and not under_failed_root(p.get("path"))]
    fresh_items = [(sp, m.get("fingerprint"))
                   for sp, m in zip(scanned_paths, metas)
                   if repository_path_key(sp) not in by_path and not m["broken"]]
    suggestions = match_move_candidates(stale_entries, fresh_items,
                                        move_suppressions)
    # Transient exact-pair proof also supports manual ambiguity review;
    # contested groups themselves remain outside pending/batch approval.
    annotate_counterpart_provenance(suggestions, created_counterpart_ids)
    meta_by_key = {}
    observed_by_key = {}
    for sp, meta, observed in zip(scanned_paths, metas, observed_list):
        key = repository_path_key(sp)
        if key is None:
            continue
        meta_by_key[key] = meta
        observed_by_key[key] = observed
    # R2.6C: persist causal provenance on the stale OLD Project for
    # unambiguous scan-created relations (never reconstructed heuristically).
    persist_pending_moves(by_path, suggestions, observed_by_key, now)
    problems.extend(suggestions)

    protected = {
        key for value in protected_paths or []
        if (key := repository_path_key(value)) is not None
    }
    protected.update(
        repository_path_key(x)
        for s in suggestions
        for x in ([s.get("old_path")] + list(s.get("old_paths") or []))
        if x)
    # R2.6C: an OLD Project carrying a pending relation stays shielded from
    # the prune below even when no suggestion is currently actionable
    # (deferred while a root is offline, for example). Strips performed by
    # the rehydration below only end retention shielding for relations that
    # are no longer pending.
    protected.update(
        key for p in list(projects) + list(by_path.values())
        if isinstance(p, dict) and isinstance(p.get("pending_move"), dict)
        for key in [project_location_key(p)] if key is not None)

    # keep recently-seen vanished projects for PRUNE_DAYS; open suggestions
    # keep their referenced old entries alive until resolved/expired
    cutoff = datetime.now(timezone.utc) - timedelta(days=PRUNE_DAYS)
    for key, old in by_path.items():
        checkpoint()
        try:
            last_seen = datetime.strptime(
                old.get("last_seen"), "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc)
        except (TypeError, ValueError):
            last_seen = None
        if (id(old) not in upgraded_ids and key not in seen
                and (is_ignored(old)
                     or under_failed_root(old.get("path"))
                     or last_seen is None or last_seen >= cutoff
                     or key in protected)):
            projects.append(old)

    # R2.6C: rehydrate AFTER retention so stale OLD Projects are present in
    # the merged list. Consumes the durable project_id relation only; never
    # reconstructs provenance heuristically.
    pending_suggestions, pending_conflicts = rehydrate_pending_moves(
        projects, scanned_keys, meta_by_key, observed_by_key,
        move_suppressions, existing_suggestions=suggestions)
    problems.extend(pending_suggestions)
    problems.extend(pending_conflicts)

    projects.sort(key=lambda x: project_display_name(x).lower())
    checkpoint()
    return projects, problems
