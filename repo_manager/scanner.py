"""Git repository discovery and metadata collection."""
import logging
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from multiprocessing.pool import ThreadPool
from pathlib import Path

from .projects import (ensure_project_id, is_ignored, project_display_name,
                       repository_default_name, repository_path_key)

GIT_TIMEOUT = 10
MAX_WORKERS = 8
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
    try:
        r = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True, text=True, timeout=GIT_TIMEOUT,
            encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        # Porcelain status uses a leading space as the meaningful index-state
        # column. Only trim the line ending so callers retain Git's columns.
        return (r.returncode, r.stdout.rstrip(), r.stderr.rstrip())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (None, "", str(exc))


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
            current, d = stack.pop()
            try:
                entries = list(os.scandir(current))
            except OSError as exc:
                if root_error is None:
                    root_error = f"scan root incomplete: {exc}"
                continue
            for entry in entries:
                if entry.name == ".git":
                    found.append(str(current))
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


def collect_metadata(path):
    """Gather live git metadata for one repo directory. Returns dict."""
    meta = empty_meta(path)
    p = Path(path)
    if _git(p, "rev-parse", "--git-dir") is None:
        meta["broken"] = True
        return meta
    meta["repository_observed"] = True
    branch = _git(p, "branch", "--show-current")
    meta["branch"] = branch or None  # detached HEAD -> None
    meta["head"] = _git(p, "rev-parse", "HEAD")
    status = _git(p, "status", "--porcelain")
    if status is not None:
        meta["staged"], meta["unstaged"], meta["untracked"] = _parse_porcelain_status(status)
        meta["dirty"] = meta["staged"] + meta["unstaged"] + meta["untracked"]
        meta["status_available"] = True
    upstream_rc, upstream, upstream_error = _git_result(
        p, "rev-parse", "--abbrev-ref", "@{upstream}")
    if upstream_rc == 0 and upstream:
        meta["upstream"] = upstream
        meta["upstream_state"] = "TRACKED"
        ahead = _git(p, "rev-list", "--count", "@{upstream}..HEAD")
        behind = _git(p, "rev-list", "--count", "HEAD..@{upstream}")
        if (ahead is not None and behind is not None
                and ahead.isdigit() and behind.isdigit()):
            meta["ahead"] = int(ahead)
            meta["behind"] = int(behind)
            meta["sync_available"] = True
    elif "no upstream configured" in upstream_error.casefold():
        meta["upstream_state"] = "NONE"
    log = _git(p, "log", "-1", "--format=%cs|%s")
    if log and "|" in log:
        d, m = log.split("|", 1)
        meta["last_commit_date"] = d
        meta["last_commit_msg"] = m[:60]
    remotes = set()
    remote_names = set()
    origin = None
    first = None
    first_name = None
    remote_v = _git(p, "remote", "-v")
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
    worktrees = _worktree_state(p)
    meta["worktrees"] = worktrees if worktrees is not None else []
    meta["worktrees_available"] = worktrees is not None
    roots_raw = _git(p, "rev-list", "--max-parents=0", "HEAD")
    meta["fingerprint"] = {
        "remotes": sorted(remotes),
        "root_commits": sorted(roots_raw.splitlines()) if roots_raw else [],
    }
    return meta


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
            })
        else:
            members = by_new[nk]
            final.append({
                "kind": "move", "category": "ambiguous",
                "old_paths": [m["old_path"] for m in members],
                "new_path": p["new_path"],
                "name": Path(p["new_path"]).name,
                "evidence": ["Matches multiple vanished entries"],
            })
    return sorted(final, key=lambda s: (
        str(s.get("old_path") or s.get("old_paths")[0]).lower(),
        str(s.get("new_path") or "").lower()))


def move_target_identity(path):
    """Re-observe identity evidence stored with an advisory move."""
    metadata = collect_metadata(path)
    if metadata.get("broken"):
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
    # repo must not abort the results of all others
    scanned_paths = list(scanned_paths)
    metas = []
    if scanned_paths:
        with ThreadPool(processes=MAX_WORKERS) as pool:
            futures = [pool.apply_async(collect_metadata, (sp,))
                       for sp in scanned_paths]
            for sp, fut in zip(scanned_paths, futures):
                try:
                    metas.append(fut.get())
                except Exception:
                    log.exception("metadata collection failed for %s", sp)
                    broken_meta = empty_meta(sp)
                    broken_meta["broken"] = True
                    metas.append(broken_meta)

    projects = list(standalone_projects)
    seen = set()
    upgraded_ids = set()
    for old in by_path.values():
        ensure_project_id(old)
    for sp, meta in zip(scanned_paths, metas):
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
                meta.pop("path", None)
                meta.pop("name", None)
                old.update(meta)
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
            old.update(meta)
            old["last_seen"] = now
            projects.append(old)
        else:
            ensure_project_id(meta)
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

    # keep recently-seen vanished projects for PRUNE_DAYS; open suggestions
    # keep their referenced old entries alive until resolved/expired
    cutoff = datetime.now(timezone.utc) - timedelta(days=PRUNE_DAYS)
    for key, old in by_path.items():
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

    projects.sort(key=lambda x: project_display_name(x).lower())
    return projects, problems
