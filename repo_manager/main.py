"""RepoManager — scan, track and launch local git projects."""
import copy
import logging
import logging.handlers
import os
import queue
import shutil
import subprocess
import time
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import agents, health, launchers, processes, projects, providers, reports, scanner, store, theme, workspaces, version

try:
    import msvcrt  # Windows single-instance lock
except ImportError:  # non-Windows: no lock support
    msvcrt = None

REFRESH_MS = 200
QUEUE_DRAIN_MAX_EVENTS = 32
FILTER_DEBOUNCE_MS = 150
NOTE_SAVE_DELAY_MS = 1000
SAVE_DEBOUNCE_MS = 300
TOOLTIP_DELAY_MS = 600
STATUSES = ("idea", "active", "paused", "archived")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
MODIFIER_KEYS = {
    "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
    "Meta_L", "Meta_R", "Caps_Lock", "Num_Lock",
}

log = logging.getLogger("repomanager")

_instance_lock_fp = None

# A stable explicit identity keeps source runs grouped as RepoManager instead
# of inheriting the generic Python launcher identity in the Windows taskbar.
WINDOWS_APP_USER_MODEL_ID = "RepoManager.RepoManager"

# Prefer the canonical application icon; retain the older icon as a fallback
# for source distributions that contain only that asset.
ICON_CANDIDATES = ("appicon.ico", "app.ico")

# Defaults remain user-resizable, while min/max bounds prevent a persisted
# separator drag from making identity or state information disappear.
TABLE_COLUMNS = (
    ("name", "Name", 180, 130, 420, True),
    ("classification", "Class", 120, 100, 220, False),
    ("status", "Status", 90, 75, 150, False),
    ("branch", "Branch", 120, 90, 260, True),
    ("dirty", "Dirty", 55, 50, 90, False),
    ("sync", "±", 60, 55, 90, False),
    ("worktrees", "Trees", 55, 50, 90, False),
    ("last_commit", "Last commit", 100, 85, 150, False),
    ("path", "Path", 340, 220, 760, True),
)
TABLE_COLUMN_ORDER = tuple(item[0] for item in TABLE_COLUMNS)
TABLE_COLUMN_DEFAULTS = {item[0]: item[2] for item in TABLE_COLUMNS}
TABLE_COLUMN_LIMITS = {item[0]: (item[3], item[4])
                       for item in TABLE_COLUMNS}

HELP_TOPICS = {
    "guide": (
        "Start here",
        "Select a Project in the repository table to inspect its state, "
        "Health, Provider evidence and available launchers. Use Filter to "
        "narrow the table, F5 to rescan, and the right-click menu for all "
        "context actions. RepoManager observes before it acts: repository "
        "discovery, Health and Provider checks are read-only."
    ),
    "health": (
        "Health and status",
        "Health summarizes current evidence. PASS means no issue was found "
        "by the checks that ran; WARN means attention is useful; FAIL means a "
        "serious invalid state was observed; UNKNOWN means evidence is "
        "missing or unavailable. Scanner-based evidence can be stale until "
        "the next rescan. Project lifecycle status (Idea, Active, Paused, "
        "Archived) is your curation and is separate from Health."
    ),
    "workspace": (
        "Workspaces and agents",
        "A Workspace is RepoManager metadata that groups Projects for "
        "inspection. It does not coordinate Git changes. An Agent is an "
        "explicitly launched local command for the selected repository. "
        "Ready means both its executable and the selected target were "
        "rechecked. After exit, Target rechecked means RepoManager observed "
        "Git state again; it does not approve the Agent's work."
    ),
    "provider": (
        "Providers and launchers",
        "A Git remote is only a URL. Git host / Provider correspondence is "
        "inferred locally from that URL; online details are a separate "
        "read-only observation. Neither proves ownership, authentication, or "
        "write access. Launchers are detected local start commands and run "
        "only after you explicitly choose one."
    ),
    "shortcuts": (
        "Keyboard and layout",
        "Ctrl+F focuses Filter. F5 rescans. Enter runs the primary launcher "
        "for the focused table. F1 opens this guide. Escape clears Filter or "
        "closes dialogs. Drag table-header separators to resize columns; use "
        "Settings > Appearance to restore safe defaults."
    ),
}


def clamp_column_widths(widths=None):
    """Return complete, integer table widths constrained to safe bounds."""
    widths = widths if isinstance(widths, dict) else {}
    result = {}
    for name in TABLE_COLUMN_ORDER:
        value = widths.get(name, TABLE_COLUMN_DEFAULTS[name])
        if not isinstance(value, int) or isinstance(value, bool):
            value = TABLE_COLUMN_DEFAULTS[name]
        low, high = TABLE_COLUMN_LIMITS[name]
        result[name] = max(low, min(high, value))
    return result


CUSTOM_LAUNCHER_FORM_LAYOUT = {
    "name_label": (0, 0),
    "name_input": (0, 1),
    "executable_label": (1, 0),
    "executable_input": (1, 1),
    "args_label": (2, 0),
    "args_input": (2, 1),
    "args_help": (3, 1),
    "cwd_label": (4, 0),
    "cwd_input": (4, 1),
    "validation": (5, 0),
    "actions": (6, 0),
}


def custom_launcher_form_cells():
    """Return the owned grid cells for the Add Custom Launcher form."""
    return dict(CUSTOM_LAUNCHER_FORM_LAYOUT)


def custom_launcher_validation_message(name, executable, cwd):
    """Return actionable validation feedback, or None for a valid form."""
    if not name or not executable or not cwd:
        return "Name, executable, and working directory are required."
    return None


def custom_launcher_validation_state(message):
    """Return the presentation state for the dialog's validation feedback."""
    return "visible" if isinstance(message, str) and message else "hidden"


def health_headline(status):
    """Human-facing Health headline without weakening the evidence status."""
    return {
        health.PASS: "Healthy — no blocking problem found",
        health.WARN: "Needs attention — review the warnings",
        health.FAIL: "Problems found — action recommended",
        health.UNKNOWN: "Unknown — evidence is incomplete",
        health.NOT_APPLICABLE: "Not applicable to this Project",
    }.get(status, f"{status or 'UNKNOWN'} — review available evidence")


HEALTH_GROUP_ORDER = (
    "Action Needed", "Needs Attention", "Informational",
    "Not Applicable", "Passed Checks", "Disabled", "Other",
)


HEALTH_GROUPS_EXPANDED = {
    "Action Needed", "Needs Attention", "Informational",
}


def health_finding_group(finding):
    """Return the presentation group for one finding.

    Grouping is presentation-only and exhaustive: every finding falls into
    exactly one group, so a future valid status/importance combination is
    never silently dropped from Health Details.
    """
    status = finding.status if hasattr(finding, "status") else None
    importance = (finding.importance
                  if hasattr(finding, "importance") else health.REQUIRED)
    if importance == health.DISABLED:
        return "Disabled"
    if importance == health.INFORMATIONAL:
        return "Informational"
    if status in (health.FAIL, health.UNKNOWN):
        return "Action Needed"
    if status == health.WARN:
        return "Needs Attention"
    if status == health.NOT_APPLICABLE:
        return "Not Applicable"
    if status == health.PASS:
        return "Passed Checks"
    return "Other"


def health_rule_display_name(rule):
    """Presentation label for a raw rule identifier."""
    text = str(rule or "").strip()
    if not text:
        return "Check"
    cleaned = text.replace("_", " ").strip()
    return cleaned[0].upper() + cleaned[1:] if cleaned else "Check"


def health_detail_summary(result):
    """Return the Health Details header lines for a completed result."""
    summary = result.summary
    material = tuple(f for f in result.findings
                     if f.importance not in (health.DISABLED,
                                             health.INFORMATIONAL))
    parts = [f"{summary.finding_count} checks"]
    for status, label in ((health.WARN, "warning"),
                          (health.FAIL, "problem"),
                          (health.UNKNOWN, "incomplete")):
        count = sum(f.status == status for f in material)
        if count:
            plural = label + ("s" if count != 1 else "")
            parts.append(f"{count} {plural}")
    stale = sum(f.freshness == health.STALE for f in result.findings)
    if stale:
        parts.append(f"{stale} stale")
    return (health_headline(result.status),
            " · ".join(parts),
            f"Evaluated {result.evaluated_at}")


# Presentation-only 0-100 Repository Health score. The score refines
# the authoritative Health status; it never overrides it. Penalties are kept
# in a single transparent table so the arithmetic is independently testable.
_HEALTH_STATUS_PENALTIES = {
    health.REQUIRED: {health.WARN: 10, health.UNKNOWN: 18, health.FAIL: 30},
    health.RECOMMENDED: {health.WARN: 7, health.UNKNOWN: 12, health.FAIL: 18},
    health.INFORMATIONAL: {health.WARN: 1, health.UNKNOWN: 2, health.FAIL: 4},
}


_HEALTH_SCORE_BANDS = {
    health.PASS: (80, 100),
    health.WARN: (60, 79),
    health.UNKNOWN: (40, 59),
    health.FAIL: (0, 39),
}


_HEALTH_SCORE_LABELS = {
    health.PASS: "Healthy",
    health.WARN: "Needs attention",
    health.UNKNOWN: "Evidence incomplete",
    health.FAIL: "Problems found",
    health.NOT_APPLICABLE: "Not enough applicable evidence",
}


def health_finding_penalty(finding):
    """Presentation-only point penalty for one finding (0 when none applies).

    PASS, NOT_APPLICABLE and DISABLED findings never cost points. Unknown
    future importance values are treated conservatively like REQUIRED only
    through their status; unknown statuses cost nothing.
    """
    importance = getattr(finding, "importance", health.REQUIRED)
    if importance == health.DISABLED:
        return 0
    status = getattr(finding, "status", health.UNKNOWN)
    if status in (health.PASS, health.NOT_APPLICABLE):
        return 0
    by_status = _HEALTH_STATUS_PENALTIES.get(
        importance, _HEALTH_STATUS_PENALTIES[health.REQUIRED])
    return by_status.get(status, 0)


def health_stale_penalty(findings):
    """Stale-evidence point penalty for a collection of findings.

    Only materially relevant stale findings count (informational and disabled
    findings are not double-punished) and the total is capped at 5 so stale
    evidence can never dominate the score.
    """
    count = sum(
        1 for f in findings
        if getattr(f, "freshness", health.CURRENT) == health.STALE
        and getattr(f, "importance", health.REQUIRED)
        not in (health.DISABLED, health.INFORMATIONAL))
    return min(5, count)


def repository_health_score(result):
    """Return a deterministic 0-100 presentation score for a HealthResult.

    The raw 100 minus per-finding penalties is constrained to the band of the
    authoritative ``result.status`` (PASS 80-100, WARN 60-79, UNKNOWN 40-59,
    FAIL 0-39). Returns ``None`` when there is not enough applicable evidence
    (only NOT_APPLICABLE/DISABLED findings, or an empty result) so the UI can
    show a dash instead of inventing a numeric confidence score.
    """
    if result is None:
        return None
    applicable = tuple(
        f for f in result.findings
        if getattr(f, "importance", health.REQUIRED) != health.DISABLED
        and getattr(f, "status", health.UNKNOWN)
        in (health.PASS, health.WARN, health.FAIL, health.UNKNOWN))
    if not applicable:
        return None
    total = sum(health_finding_penalty(f) for f in applicable)
    raw = 100 - total - health_stale_penalty(result.findings)
    band = _HEALTH_SCORE_BANDS.get(result.status)
    if band is None:
        return None
    return max(band[0], min(band[1], raw))


def repository_health_band_label(result):
    """Restrained language label shown with the score."""
    if result is None:
        return _HEALTH_SCORE_LABELS[health.NOT_APPLICABLE]
    if repository_health_score(result) is None:
        return _HEALTH_SCORE_LABELS[health.NOT_APPLICABLE]
    return _HEALTH_SCORE_LABELS.get(result.status,
                                    health_headline(result.status))


def health_dashboard_counts(result):
    """Counters for the Health dashboard, derived from existing findings.

    Chosen, documented rule: primary counters (passed/warnings/problems/
    unknown) reflect enabled non-informational findings so informational
    observations never masquerade as hard failures; ``informational`` counts
    every enabled informational finding (matching the All-checks Informational
    group); ``stale`` counts every enabled stale finding and stays a muted
    evidence marker, never a failure.
    """
    counts = {"passed": 0, "warnings": 0, "problems": 0,
              "unknown": 0, "stale": 0, "informational": 0}
    for finding in result.findings:
        importance = getattr(finding, "importance", health.REQUIRED)
        if importance == health.DISABLED:
            continue
        if finding.freshness == health.STALE:
            counts["stale"] += 1
        if importance == health.INFORMATIONAL:
            counts["informational"] += 1
            continue
        status = finding.status
        if status == health.PASS:
            counts["passed"] += 1
        elif status == health.WARN:
            counts["warnings"] += 1
        elif status == health.FAIL:
            counts["problems"] += 1
        elif status == health.UNKNOWN:
            counts["unknown"] += 1
    return counts


def package_root():
    """Absolute application source-tree root (the directory holding the package)."""
    return Path(__file__).resolve().parent.parent


def resolve_icon_path(base=None, names=None):
    """Return the canonical application icon file, or ``None`` if none exists.

    ``base`` is the application source-tree root (defaults to
    ``package_root()``). The owner-supplied multi-resolution ``appicon.ico`` is
    canonical; ``app.ico`` is a fallback. Resolution is purely filesystem-based
    so it is deterministic and unit-testable without a display.
    """
    root = Path(base) if base is not None else package_root()
    for name in names or ICON_CANDIDATES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


CONTEXT_MENU_LAYOUT = (
    # Current-work action
    ("command", "Work on this (pin + Active)", "_toggle_working_on_this"),
    "-sep-",
    # Local launchers
    ("command", "Open in Explorer", "open_explorer"),
    ("command", "Open in VS Code", "open_vscode"),
    ("command", "Open Terminal", "open_terminal"),
    ("command", "Open Agent", "open_agent"),
    "-sep-",
    # metadata / curation group
    ("cascade", "Set status", None),
    ("command", "Pin / Unpin", "_toggle_pinned"),
    ("command", "Remove from RepoManager\u2026", "_remove_from_repomanager"),
    "-sep-",
    # read-only information group
    ("command", "Open on GitHub", "open_remote"),
    ("command", "Copy path", "_copy_path"),
    ("command", "Copy GitHub URL", "_copy_remote_url"),
    "-sep-",
    # mutating Git actions (kept separated + last)
    ("command", "Commit & Push\u2026", "git_commit_push"),
    ("command", "Pull", "git_pull"),
)


def working_action_label(status):
    """Return the primary current-work action for a lifecycle status."""
    return "Stop working on this" if status == "active" else "Work on this"


def context_menu_layout(status, *, ignored=False):
    """Return the context menu with a status-aware current-work action."""
    label = working_action_label(status)
    if status != "active":
        label += " (pin + Active)"
    layout = (("command", label, "_toggle_working_on_this"),) \
        + CONTEXT_MENU_LAYOUT[1:]
    if ignored:
        layout = tuple(
            item for item in layout
            if not (isinstance(item, tuple)
                    and item[2] == "_remove_from_repomanager")
        )
    return layout


def persist_project_ignore(project_records, target_id, save_projects):
    """Persist an explicit ignore against the current stable Project identity.

    The live collection remains authoritative. Persistence happens while the
    record is marked ignored, and any failure restores its exact prior state.
    """
    if not isinstance(target_id, str) or not target_id.strip():
        return None
    target = next(
        (project for project in project_records
         if projects.project_id(project) == target_id),
        None,
    )
    if target is None or projects.is_ignored(target):
        return None
    missing = object()
    previous = target.get("ignored", missing)
    target["ignored"] = True
    try:
        save_projects(project_records)
    except Exception:
        if previous is missing:
            target.pop("ignored", None)
        else:
            target["ignored"] = previous
        raise
    return target


def empty_state_text(roots):
    """Compact first-run guidance shown when no Projects are discovered."""
    roots = [r for r in (roots or []) if r]
    if roots:
        where = "Currently scanning:" + "\n" + "\n".join(
            f"  \u2022 {r}" for r in roots)
    else:
        where = "No scan folders are configured yet \u2014 open Settings to " \
                "choose where RepoManager should look."
    return (
        "Nothing here yet.\n"
        "RepoManager scans your folders for Git repositories and keeps a "
        "curated Project for each one.\n"
        f"{where}\n"
        "Press Rescan (F5) to scan now. Right-click any project for shortcuts "
        "and actions."
    )


def is_available(p):
    """True when the associated repository or Project folder exists on disk."""
    path = p.get("path") or p.get("folder_path")
    return bool(path) and os.path.isdir(str(path))


def project_row_id(project):
    """Stable Treeview identity for repository-backed and folder-only Projects."""
    return (project.get("project_id") or project.get("path")
            or project.get("folder_path"))


class AvailabilityCache:
    """Memoize ``os.path.isdir`` per path until ``invalidate()``.

    Availability is a property of the last reconciled repository set, so it is
    cached across table repopulations (filter/sort/refresh) to avoid repeated
    filesystem stats. Call ``invalidate()`` whenever the repository set may
    have changed (scan result applied, confirmed move) so a repository that
    disappeared/moved/appeared is always reflected on the next repopulation.
    """

    def __init__(self, sampler=os.path.isdir):
        self._sampler = sampler
        self._values = {}

    def get(self, path):
        key = str(path)
        if key not in self._values:
            self._values[key] = self._sampler(key)
        return self._values[key]

    def invalidate(self):
        self._values = {}


def reconcile_plan(current, desired):
    """Plan the minimal insert/delete so ordered iids ``current`` become
    ``desired``.

    Returns ``(to_remove, to_insert)`` where ``to_remove`` are ``current``
    iids no longer desired and ``to_insert`` are ``desired`` iids not already
    present, in desired order. Ordering is reconciled separately by moving
    items into place. Pure, Tk-free, and unit testable.
    """
    want = set(desired)
    cur = set(current)
    to_remove = [i for i in current if i not in want]
    to_insert = [i for i in desired if i not in cur]
    return to_remove, to_insert


def reconcile_tree(tree, desired, get_state):
    """Incrementally refresh a ttk.Treeview to ``desired`` (ordered iids).

    Existing rows are reused in place (their values/tags/text refreshed via
    ``get_state(iid) -> kwargs``); only removed rows are deleted and only new
    rows inserted; rows are moved to keep the desired ordering. This avoids
    destroying and recreating every row on each repopulation.
    """
    desired = list(desired)
    current = list(tree.get_children())
    to_remove, to_insert = reconcile_plan(current, desired)
    if to_remove:
        tree.delete(*to_remove)
    inserted = set()
    for iid in to_insert:
        tree.insert("", "end", iid=iid, **get_state(iid))
        inserted.add(iid)
    if tuple(tree.get_children()) != tuple(desired):
        for idx, iid in enumerate(desired):
            if tree.index(iid) != idx:
                tree.move(iid, "", idx)
    for iid in desired:
        if iid not in inserted:
            tree.item(iid, **get_state(iid))


def working_on_now_rows(items, exists=None):
    """Compatibility wrapper for the Project boundary's current-work rule."""
    return projects.working_on_now_rows(
        items, exists or os.path.isdir)


def launcher_candidates_for_project(project, settings):
    """Current launcher candidates for a repository-backed Project."""
    if not projects.is_repository_backed(project):
        return []
    try:
        custom = [item for item in project.get(
            launchers.CUSTOM_LAUNCHERS_FIELD, []) if isinstance(item, dict)]
        return launchers.detect_commands(
            project["path"], settings,
            project_id=projects.project_id(project),
            custom_launchers=custom)
    except Exception:
        log.exception("launcher detection failed for %s",
                      project.get("path"))
        return []


def resolve_primary_for_project(project, settings):
    """Primary launcher command for a project, or None if unavailable."""
    return launchers.select_primary_command(
        launcher_candidates_for_project(project, settings))


def compute_detail_observation(project, settings):
    """Compute local detail observations without accessing Tk or widgets.

    Each local observation is isolated so one failed component cannot leave
    another component's loading state visible forever. Callers must apply the
    returned plain data on the Tk thread.
    """
    observation = {
        "health": None,
        "launchers": (),
        "note": "",
        "health_error": None,
        "note_error": None,
    }
    try:
        observation["health"] = health.evaluate_repository(
            project.get("path"), project)
    except Exception as exc:
        log.exception("health observation failed")
        observation["health_error"] = str(exc)

    try:
        candidates = launcher_candidates_for_project(project, settings)
        observation["launchers"] = tuple(
            candidate.as_dict() if hasattr(candidate, "as_dict")
            else dict(candidate)
            for candidate in candidates)
    except Exception:
        log.exception("launcher observation serialization failed")

    folder = project.get("path") or project.get("folder_path") or ""
    try:
        observation["note"] = store.load_note(
            project.get("name", ""), folder, projects.project_id(project))
    except Exception as exc:
        log.exception("note observation failed")
        observation["note_error"] = str(exc)
    return observation


def available_project_folder(project, *, is_dir=os.path.isdir):
    """Return the current folder target only when it still exists."""
    path = projects.project_folder(project) if project else None
    if not isinstance(path, str) or not path.strip():
        return None
    try:
        return path if is_dir(path) else None
    except OSError:
        return None


def scan_root_key(value):
    """Canonical comparison key for one configured scan root."""
    if not isinstance(value, str) or not value.strip():
        return None
    return os.path.normcase(os.path.abspath(os.path.normpath(value.strip())))


def scan_root_is_available(value, *, is_dir=os.path.isdir):
    key = scan_root_key(value)
    if key is None:
        return False
    try:
        return bool(is_dir(value.strip()))
    except OSError:
        return False


def safe_remote_web_url(remote):
    """Normalize a recognized Git remote to HTTPS, removing user information."""
    if not isinstance(remote, str) or not remote.strip():
        return None
    normalized = scanner.normalize_remote(remote)
    if normalized is None:
        normalized = scanner.normalize_remote("https://" + remote.strip())
    return f"https://{normalized}" if normalized else None


def row_tag(p, available=True):
    """Row color tag; stale dominates, else archived > no-remote > dirty."""
    if not available or p.get("broken") or p.get("status_available") is False:
        return "stale"
    if p.get("status") == "archived":
        return "archived"
    if not p.get("remote"):
        return "noremote"
    if p.get("dirty"):
        return "dirty"
    if p.get("ahead") or p.get("behind"):
        return "sync"
    return ""


def guarded_worker(queue, fn, *, error_kind="error", error_payload=None):
    """Wrap a worker callback with exception reporting.

    `fn` must enqueue its own result or business-failure event as its final
    action. On an unexpected exception, attempt to enqueue an error event;
    emission failures are logged. ``error_payload`` may be a callback that
    receives the user-facing message and returns operation metadata.
    """
    def target():
        try:
            fn()
        except Exception:
            log.exception("background operation failed")
            try:
                message = ("A background operation failed — "
                           "see repo_manager.log for details.")
                payload = (error_payload(message)
                           if callable(error_payload) else error_payload)
                if payload is None:
                    payload = message
                queue.put((error_kind, payload))
            except Exception:
                log.exception("could not emit worker error event")
    return target


def coalesce_worker_errors(messages):
    """Combine queued error messages into one user-facing string."""
    unique = list(dict.fromkeys(messages))
    if not unique:
        return None
    head = "\n".join(unique[:3])
    if len(unique) > 3:
        head += f"\n(+{len(unique) - 3} more — see log)"
    return head


def commit_steps_for(msg, porcelain_now, remote="origin"):
    """Plan commit and push steps from a fresh status check.

    A failed or empty status check authorizes no mutation. The preview is
    only a snapshot: ``git add -A`` stages all changes present when it runs,
    including changes made since the preview or the status re-check.
    """
    if porcelain_now is None or not porcelain_now.strip():
        return []
    return [("add", ("add", "-A")),
            ("commit", ("commit", "-m", msg)),
            ("push", ("push", "-u", remote, "HEAD"))]


def select_push_remote(remote_names, upstream=None):
    """Choose a safe push remote or None when the target is ambiguous."""
    names = [str(name).strip() for name in (remote_names or [])
             if str(name).strip()]
    names = list(dict.fromkeys(names))
    if isinstance(upstream, str) and "/" in upstream:
        tracked = upstream.split("/", 1)[0]
        if tracked in names:
            return tracked
    if "origin" in names:
        return "origin"
    if len(names) == 1:
        return names[0]
    return None


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


def parse_upstream(value):
    """Split ``remote/branch`` while preserving slashes in branch names."""
    if not isinstance(value, str) or "/" not in value:
        return None
    remote, branch = value.strip().split("/", 1)
    return (remote, branch) if remote and branch else None


GIT_SUCCESS = "SUCCESS"
GIT_FAILED = "FAILED"
GIT_CANCELLED = "CANCELLED"
GIT_OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
GIT_PARTIAL = "PARTIAL_MUTATION"


def git_step_outcome(results):
    """Classify sequential Git steps without hiding partial durable mutation.

    ``results`` contains ``(label, returncode, output, evidence)``. A timeout
    or spawn error is uncertain for mutating commands because Git may have
    completed immediately before the client lost the result.
    """
    if not results:
        return GIT_FAILED, "no git steps executed"
    for index, (label, rc, out, evidence) in enumerate(results):
        if evidence == GIT_OUTCOME_UNKNOWN:
            return GIT_OUTCOME_UNKNOWN, f"{label} outcome is unknown: {out}"
        if rc != 0:
            if any(previous_label == "commit" and previous_rc == 0
                   for previous_label, previous_rc, _previous_out, _evidence
                   in results[:index]):
                return GIT_PARTIAL, (
                    f"commit succeeded, {label} failed: {out}")
            return GIT_FAILED, f"{label} failed: {out}"
    return GIT_SUCCESS, results[-1][2]


SCANNER_OBSERVATION_FIELDS = {
    "branch", "head", "dirty", "staged", "unstaged", "untracked",
    "ahead", "behind", "upstream", "remotes", "remote_names", "remote_name",
    "remote_reachable", "last_commit_date", "last_commit_msg", "remote",
    "broken", "repository_observed", "fingerprint", "worktrees", "last_seen",
    "status_available", "upstream_state", "sync_available", "worktrees_available",
}


def reconcile_scan_result(merged, live_projects):
    """Apply scanner observations without overwriting newer Project state."""
    def location_key(project):
        return (project.get("path") or project.get("folder_path")
                or projects.project_id(project))

    def can_use_path_fallback(scanned, current):
        """Allow path compatibility only when one record lacks identity."""
        scanned_id = projects.project_id(scanned)
        current_id = projects.project_id(current)
        return not (scanned_id and current_id and scanned_id != current_id)

    live_by_id = {}
    for project in live_projects:
        project_id = projects.project_id(project)
        if project_id:
            live_by_id.setdefault(project_id, project)
    live_by_row = {}
    for project in live_projects:
        key = location_key(project)
        if key not in live_by_row:
            live_by_row[key] = project
    matched = set()
    result = []
    for scanned in merged:
        current = live_by_id.get(projects.project_id(scanned))
        if current is None:
            candidate = live_by_row.get(location_key(scanned))
            if candidate is not None:
                if can_use_path_fallback(scanned, candidate):
                    current = candidate
                else:
                    # The live collection authoritatively owns this location.
                    # A stale scan row with another identity must neither
                    # inherit its curation nor survive as a duplicate path
                    # that registry validation would later drop ambiguously.
                    continue
        if current is None:
            result.append(scanned)
            continue
        marker = id(current)
        if marker in matched:
            continue
        matched.add(marker)
        if location_key(current) != location_key(scanned):
            result.append(dict(current))
            continue
        combined = dict(scanned)
        combined.update({key: value for key, value in current.items()
                         if key not in SCANNER_OBSERVATION_FIELDS})
        # The live Project is authoritative for explicit ignore/restore state.
        # An in-flight scan may have captured the opposite value (or no value)
        # before the user's later state change; never let that stale payload
        # reactivate or re-ignore the current Project.
        if "ignored" in current:
            combined["ignored"] = current["ignored"]
        else:
            combined.pop("ignored", None)
        result.append(combined)
    result.extend(dict(project) for project in live_projects
                  if id(project) not in matched)
    return result


def evaluate_git_steps(results):
    """Aggregate sequential git step results: [(label, rc, output)].

    Success requires every executed step to succeed; the first failing
    step is named in the message so add/commit failures can no longer be
    masked by a later successful push.
    """
    for label, rc, out in results:
        if rc != 0:
            return False, f"{label} failed: {out}"
    if not results:
        return False, "no git steps executed"
    label, rc, out = results[-1]
    return True, out


def problem_kind(problem):
    """Return the evidence-backed category for a user-visible scan problem."""
    if isinstance(problem, dict) and problem.get("kind") == "scan_root":
        return "scan_root"
    if isinstance(problem, dict) and problem.get("kind") == "repository":
        return "repository"
    # Preserve compatibility with older persisted/in-memory repository
    # problems that predate explicit kinds; move records are handled separately.
    return "repository"


def group_problems(problems):
    """Separate repository validation failures from scan availability issues."""
    groups = {"repository": [], "scan_root": []}
    for problem in problems or []:
        kind = problem_kind(problem)
        groups[kind].append(problem)
    return groups


def problem_display_reason(problem):
    """Return a concise label that does not misclassify scan availability."""
    if problem_kind(problem) == "scan_root":
        return "Scan issue"
    return "Repository problem"


def attention_summary(problems, move_count=0):
    """Build the Attention label without conflating scan and repo failures."""
    problem_groups = group_problems(problems)
    parts = []
    repo_count = len(problem_groups["repository"])
    scan_count = len(problem_groups["scan_root"])
    if repo_count:
        parts.append(
            f"{repo_count} repository problem{'' if repo_count == 1 else 's'}")
    if scan_count:
        parts.append(
            f"{scan_count} scan issue{'' if scan_count == 1 else 's'}")
    if move_count:
        parts.append(f"{move_count} possible move{'' if move_count == 1 else 's'}")
    return "Attention: " + " · ".join(parts) if parts else ""


def group_move_suggestions(suggestions):
    """Split move suggestions into deterministically ordered categories."""
    def key(s):
        return (str(s.get("old_path") or s.get("old_paths", [""])[0]).lower(),
                str(s.get("new_path") or "").lower())
    groups = {"strong": [], "ambiguous": [], "possible": []}
    for s in sorted(suggestions, key=key):
        cat = s.get("category")
        groups[cat if cat in groups else "possible"].append(s)
    return groups


def select_strong_suggestions(suggestions):
    """Only strong matches qualify for batch acceptance."""
    return [s for s in group_move_suggestions(suggestions)["strong"]]


def run_batch_moves(suggestions, projects, now_iso,
                    save_projects, move_note, *, path_exists=None,
                    target_identity=None):
    """Accept every strong suggestion through the same move transaction.

    Deterministic order; each suggestion is independent — one failure does
    not affect the others. Returns (accepted_entries, failures) where
    failures is a list of (suggestion, outcome). ``path_exists`` (a fresh
    filesystem check) is forwarded to the revalidation guard of every move.
    """
    accepted, failures = [], []
    for s in select_strong_suggestions(suggestions):
        kwargs = {"path_exists": path_exists}
        if target_identity is not None:
            kwargs["target_identity"] = target_identity
        outcome, entry = perform_confirmed_move(
            projects, s, now_iso, save_projects, move_note, **kwargs)
        if outcome in ("migrated", "collision"):
            accepted.append((s, entry, outcome))
        else:
            failures.append((s, outcome))
    return accepted, failures


class StatusLine:
    """Single status-bar writer with a hold window for important messages.

    Regular messages are transient as before. Important messages (failures,
    confirmed moves, recovery) resist being overwritten by routine updates
    for a few seconds, so they cannot vanish in the next refresh tick.
    """

    def __init__(self, var, clock=None, hold_ms=6000):
        self._var = var
        self._clock = clock or time.monotonic
        self._hold_ms = hold_ms
        self._hold_until = 0.0

    def set(self, msg, important=False):
        now = self._clock() * 1000.0
        if not important and now < self._hold_until:
            return False  # an important message is still on display
        self._var.set(msg)
        if important:
            self._hold_until = now + self._hold_ms
        else:
            self._hold_until = 0.0
        return True


def filter_superseded_rows(rows, moved_away, result_gen):
    """Drop rows resurrecting working copies moved away after result_gen.

    A scan that started before a user-confirmed move carries the pre-move
    path in its snapshot. Applying its result unguarded would resurrect
    the old row; this filter makes the confirmed move authoritative.
    """
    gone = {p for (g, p) in moved_away if g > result_gen}
    if not gone:
        return rows
    return [r for r in rows
            if str(r.get("path", "")).lower() not in gone]


def perform_confirmed_move(projects, suggestion, now_iso,
                           save_projects, move_note, *,
                           path_exists=None,
                           target_identity=None):
    """Execute a confirmed move only after the approved target is rechecked.

    The suggestion is the user's approval boundary. Immediately before the
    registry mutation, the old location must still be absent and the new
    location must still be present. An optional ``target_identity`` callback
    can provide a current repository identity for the discovered target; when
    supplied, it must match the suggestion's approved ``identity`` value.

    Registry and note changes retain the existing rollback transaction. The
    return value remains the legacy ``(outcome, entry)`` tuple for callers,
    while ``move_outcome(...)`` exposes the same result with a semantic error
    category and evidence for new consumers.
    """
    outcome = _perform_confirmed_move(
        projects, suggestion, now_iso, save_projects, move_note,
        path_exists=path_exists, target_identity=target_identity)
    return outcome.status, outcome.entry


@dataclass(frozen=True)
class MoveOutcome:
    """Concrete semantic result for the current confirmed-move operation."""

    status: str
    error_category: str | None
    evidence: tuple[str, ...]
    entry: dict | None = None


MOVE_OK = "migrated"
MOVE_COLLISION = "collision"
MOVE_MISSING_OLD = "missing_old"
MOVE_DUPLICATE = "duplicate"
MOVE_STALE_TARGET = "stale_target"
MOVE_ROLLED_BACK_REGISTRY = "rolled_back_registry"
MOVE_ROLLED_BACK_NOTE = "rolled_back_note"
MOVE_ROLLBACK_FAILED = "rollback_failed"

ERROR_INVALID_INPUT = "INVALID_INPUT"
ERROR_IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
ERROR_TARGET_MISSING = "TARGET_MISSING"
ERROR_STALE_TARGET = "STALE_TARGET"
ERROR_FILESYSTEM_FAILURE = "FILESYSTEM_FAILURE"
ERROR_PERSISTENCE_FAILURE = "PERSISTENCE_FAILURE"
ERROR_UNKNOWN_FAILURE = "UNKNOWN_FAILURE"


def move_outcome(projects, suggestion, now_iso, save_projects, move_note, *,
                path_exists=None, target_identity=None):
    """Return the structured semantic result for a confirmed move."""
    return _perform_confirmed_move(
        projects, suggestion, now_iso, save_projects, move_note,
        path_exists=path_exists, target_identity=target_identity)


def _perform_confirmed_move(projects, suggestion, now_iso,
                             save_projects, move_note, *,
                             path_exists, target_identity):
    old_path = suggestion.get("old_path")
    new_path = suggestion.get("new_path")
    if not isinstance(old_path, str) or not isinstance(new_path, str):
        return MoveOutcome(MOVE_STALE_TARGET, ERROR_INVALID_INPUT,
                           ("move suggestion lacks valid paths",))
    entry = next((p for p in projects if isinstance(p, dict)
                  and str(p.get("path", "")).lower() == old_path.lower()),
                 None)
    if entry is None:
        return MoveOutcome(MOVE_MISSING_OLD, ERROR_TARGET_MISSING,
                           ("approved old registry entry is missing",))
    if any(p is not entry and str(p.get("path", "")).lower()
           == new_path.lower() for p in projects):
        return MoveOutcome(MOVE_DUPLICATE, ERROR_IDENTITY_MISMATCH,
                           ("new path already has a registry entry",))

    if path_exists is not None:
        try:
            old_exists = bool(path_exists(old_path))
            new_exists = bool(path_exists(new_path))
        except Exception as exc:
            return MoveOutcome(MOVE_STALE_TARGET, ERROR_FILESYSTEM_FAILURE,
                               (f"target revalidation failed: {exc}",))
        if old_exists or not new_exists:
            return MoveOutcome(MOVE_STALE_TARGET, ERROR_STALE_TARGET,
                               (f"old_exists={old_exists}",
                                f"new_exists={new_exists}"))

    approved_identity = suggestion.get("identity")
    if target_identity is not None and approved_identity is not None:
        try:
            current_identity = target_identity(new_path)
        except Exception as exc:
            return MoveOutcome(MOVE_STALE_TARGET, ERROR_FILESYSTEM_FAILURE,
                               (f"identity revalidation failed: {exc}",))
        if current_identity != approved_identity:
            return MoveOutcome(MOVE_STALE_TARGET, ERROR_IDENTITY_MISMATCH,
                               (f"approved_identity={approved_identity}",
                                f"current_identity={current_identity}"))

    snapshot = dict(entry)
    old_name = entry["name"]
    new_name = Path(new_path).name
    entry["moved_from"] = old_path
    entry["path"] = new_path
    entry["name"] = new_name
    entry["last_seen"] = now_iso

    stable_project_id = entry.get("project_id")
    if not isinstance(stable_project_id, str) or not stable_project_id.strip():
        stable_project_id = None
    if stable_project_id:
        try:
            result = move_note(
                old_name, old_path, new_name, new_path, stable_project_id)
        except Exception:
            log.exception("stable note migration failed during confirmed move")
            entry.clear()
            entry.update(snapshot)
            return MoveOutcome(
                MOVE_ROLLED_BACK_NOTE, ERROR_FILESYSTEM_FAILURE,
                ("stable note migration failed before registry mutation",))
        try:
            save_projects()
        except Exception as save_exc:
            log.exception("registry save failed during confirmed move")
            entry.clear()
            entry.update(snapshot)
            if result in ("moved", "collision"):
                return MoveOutcome(
                    MOVE_ROLLBACK_FAILED, ERROR_PERSISTENCE_FAILURE,
                    ("registry save failed after stable note migration; "
                     "note rollback was not available",
                     f"compensation state is uncertain: {save_exc}"), entry)
            return MoveOutcome(
                MOVE_ROLLED_BACK_REGISTRY, ERROR_PERSISTENCE_FAILURE,
                ("registry save failed; stable note was unchanged",))
        if result == "collision":
            return MoveOutcome(
                MOVE_COLLISION, None,
                ("note destination collision preserved without overwrite",),
                entry)
        return MoveOutcome(MOVE_OK, None,
                           ("registry and stable note move completed",), entry)

    try:
        save_projects()
    except Exception:
        log.exception("registry save failed during confirmed move")
        entry.clear()
        entry.update(snapshot)
        return MoveOutcome(MOVE_ROLLED_BACK_REGISTRY,
                           ERROR_PERSISTENCE_FAILURE,
                           ("registry save failed; in-memory entry restored",))
    try:
        result = move_note(old_name, old_path, new_name, new_path)
    except Exception as note_exc:
        log.exception("note migration failed during confirmed move")
        entry.clear()
        entry.update(snapshot)
        try:
            save_projects()
        except Exception as compensation_exc:
            log.critical("could not restore registry after note failure")
            return MoveOutcome(
                MOVE_ROLLBACK_FAILED, ERROR_PERSISTENCE_FAILURE,
                ("note migration failed; registry rollback could not be "
                 "confirmed", f"compensation save failed: {compensation_exc}",
                 f"note failure: {note_exc}"), entry)
        return MoveOutcome(MOVE_ROLLED_BACK_NOTE,
                           ERROR_FILESYSTEM_FAILURE,
                           ("note migration failed; registry rollback "
                            "confirmed",))
    if result == "collision":
        return MoveOutcome(MOVE_COLLISION, None,
                           ("note destination collision preserved without overwrite",),
                           entry)
    return MoveOutcome(MOVE_OK, None, ("registry and note move completed",), entry)


def is_visible(p, flt):
    """Compatibility wrapper for Project visibility rules."""
    return projects.is_visible(p, flt)


def sorted_projects(items, sort_col=None, sort_desc=False):
    """Compatibility wrapper for Project dashboard ordering."""
    return projects.sorted_projects(items, sort_col, sort_desc)


def apply_metadata_refresh(project_records, metadata_records):
    """Apply scanner observations without changing Project identity/curation."""
    by_path = {record["path"]: record for record in metadata_records}
    protected = {
        "path", "name", "project_id", "folder_path", "status", "focus",
        "pinned", "added_at",
    }
    for project in project_records:
        metadata = by_path.get(project.get("path"))
        if metadata is None:
            continue
        project.update({key: value for key, value in metadata.items()
                        if key not in protected})


def enable_dpi_awareness():
    """Crisp rendering at display scaling >100%; best-effort, optional."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (OSError, AttributeError, NameError):
        pass


def set_windows_app_user_model_id():
    """Set the stable Windows shell identity before creating any Tk window."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            WINDOWS_APP_USER_MODEL_ID)
    except (OSError, AttributeError, NameError):
        log.exception("could not set Windows application identity")
        return False
    if result != 0:
        log.error("could not set Windows application identity: HRESULT %#x",
                  result)
        return False
    return True


def thread_excepthook(args):
    """Defensive diagnostics for threads outside the guarded worker paths."""
    if args.exc_type is SystemExit:
        return
    log.critical("unhandled thread exception", exc_info=(
        args.exc_type, args.exc_value, args.exc_traceback))


def _report_tk_callback_exception(_root, exc_type, exc_value, exc_tb):
    """Log Tk callback failures using Tk's bound-method callback contract."""
    log.critical("Tk callback exception", exc_info=(
        exc_type, exc_value, exc_tb))
    try:
        messagebox.showerror(
            "RepoManager",
            "The operation failed and may not have been saved.\n\n"
            f"{exc_value}\n\nSee repo_manager.log for details.",
            parent=_root,
        )
    except Exception:
        log.exception("could not present Tk callback failure")


def setup_logging():
    """Rotating log file; also captures crashes and Tk callback errors."""
    store.ensure_dirs()
    handler = logging.handlers.RotatingFileHandler(
        store.APP_DIR / "repo_manager.log",
        maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[handler])
    sys.excepthook = lambda t, v, tb: log.critical(
        "unhandled exception", exc_info=(t, v, tb))

    tk.Tk.report_callback_exception = _report_tk_callback_exception
    threading.excepthook = thread_excepthook


def acquire_single_instance_lock():
    """Best-effort single-instance guard via an OS-released file lock.

    The handle must stay open for the whole process lifetime: closing the
    file releases the OS lock immediately.
    """
    global _instance_lock_fp
    if msvcrt is None:
        return True
    fp = open(store.APP_DIR / "repo_manager.lock", "w")
    try:
        msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        fp.close()
        return False
    try:
        fp.seek(0)
        fp.write(str(os.getpid()))
        fp.flush()
        _instance_lock_fp = fp
        return True
    except OSError:
        fp.close()
        raise


class RepoManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"RepoManager {version.VERSION}")
        self.geometry("1400x820")
        self.minsize(1040, 640)
        self._apply_icon()

        store.ensure_dirs()
        self.settings = store.load_settings()
        self._settings_report = store.settings_recovery_report()
        self.projects, self._registry_report = store.read_registry()
        self.workspaces = self._registry_report.get("workspaces", [])
        self.agent = agents.new_agent("configured", "Configured agent", self.settings.get("agent_cmd", "opencode"))
        self.runs = []
        self._agent_processes = {}
        self._git_workers = set()
        self._close_after_git = False
        self._close_resume_job = None
        self._close_after_agents = False
        self.provider = providers.GitHubAdapter()
        self._provider_observation = None
        self._provider_gen = 0  # guards stale async provider results
        self._workspace_gen = 0  # guards stale async Workspace results
        self._metadata_gen = 0  # guards stale async metadata results
        self._detail_gen = 0  # guards stale local detail observations
        self._detail_lock = threading.Lock()
        self._detail_pending = None
        self._detail_worker = None
        self._health_result = None
        for project in self.projects:
            projects.ensure_project_id(project)
        self._scan_queue = queue.Queue()
        self._scanning = False
        self._problems = []
        self._move_suggestions = []
        self._scan_gen = 0
        self._moved_away = []  # [(gen, old_path_lower)] awaiting in-flight scans
        self._current = None
        self._loading_detail = False
        self._note_target = None
        self._note_save_job = None
        self._filter_job = None
        self._column_width_save_job = None
        self._tip = None
        self._tip_job = None
        self._tip_row = None
        self._closing = False
        self._after_jobs = set()
        self._avail = AvailabilityCache()
        self._save_job = None
        self._populate_pending = False
        self._active_tree = "main"
        self._sort_col = None
        self._sort_desc = False
        if isinstance(self.settings.get("sort"), list) and self.settings["sort"]:
            self._sort_col, self._sort_desc = self.settings["sort"]

        self.pal = theme.apply(self, self.settings.get("theme", "dark"))

        self._build_ui()
        self._notify_registry_report()
        self._notify_settings_report()
        self._apply_row_colors()
        self._update_heading_marks()
        self._populate_trees()
        self._schedule_after(REFRESH_MS, self._drain_scan_queue)
        self._bind_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if self._registry_blocked():
            self.scan_btn.configure(text="Retry registry (F5)")
        elif self.projects:
            self._refresh_metadata_async()
        else:
            self.start_scan()

    def _apply_icon(self):
        """Set the canonical application icon, trying candidates in order.

        Preferred source is the supplied multi-resolution ``appicon.ico``;
        the older ``app.ico`` is a fallback. Each candidate is applied best
        effort, so a missing or unreadable resource never prevents the window
        from opening (a shrink-wrapped source tree may contain only one of them).
        """
        for icon in ICON_CANDIDATES:
            candidate = package_root() / icon
            if not candidate.is_file():
                continue
            try:
                self.iconbitmap(str(candidate))
                log.info("app icon set: %s", candidate.name)
                return
            except tk.TclError:
                log.warning("could not load app icon %s", candidate.name)

    def _notify_settings_report(self):
        """Tell the user when malformed settings were preserved or blocked."""
        report = self._settings_report
        if report.get("status") != "recovered":
            return
        quarantined = report.get("quarantined")
        if report.get("preservation_failed"):
            message = (
                "settings.json is malformed and could not be preserved.\n"
                "Safe defaults are loaded in memory, but the damaged source "
                "was left untouched and will not be overwritten until it can "
                "be preserved.")
        else:
            message = (
                "settings.json is malformed. Safe defaults are loaded in "
                "memory; the damaged original was preserved at:\n"
                f"{quarantined}\n\n"
                "Review and re-enter any settings that were not recoverable.")
        messagebox.showwarning("RepoManager", message)

    def _notify_registry_report(self):
        """Tell the user once if the registry needed recovery."""
        rep = self._registry_report
        if self._registry_blocked():
            details = (
                "Your project registry (repos.json) could not be safely loaded "
                "or restored.\nScanning and registry changes are paused. "
                "The existing primary will remain untouched while paused.\n\n"
            )
            if rep.get("source"):
                details += f"Projects from backup ({rep['source']}) are available for viewing.\n"
            if rep.get("quarantined"):
                details += f"A copy of the damaged original is at:\n{rep['quarantined']}\n"
            details += (
                "Close any program locking the file or restore a valid registry/backup, "
                "then use Retry registry (F5). You can also close RepoManager safely."
            )
            messagebox.showwarning("RepoManager", details)
            self._status.set("Registry recovery required · Retry registry (F5)",
                             important=True)
            return
        if rep["status"] == "recovered":
            log.warning("registry recovered from %s", rep["source"])
            details = (
                "Your project registry (repos.json) was recovered from "
                f"backup ({rep['source']}).\n"
            )
            if rep.get("quarantined"):
                details += (
                    "The damaged original was preserved at:\n"
                    f"{rep['quarantined']}\n"
                )
            if rep.get("identity_durable") is False:
                details += (
                    "Legacy Project IDs were derived deterministically from "
                    "their recorded paths because the primary registry is "
                    "still unavailable. Identity is stable for this recovery "
                    "but has not been durably written yet.\n"
                )
            messagebox.showinfo("RepoManager", details)

    def _registry_blocked(self):
        report = self.__dict__.get("_registry_report", {})
        return (report.get("status") in {"unavailable", "unrecoverable"}
                or bool(report.get("write_blocked")))

    def _persist_projects(self, records=None, *, workspaces=None):
        """All GUI registry writes require a successfully loaded snapshot."""
        if self._registry_blocked() or self.__dict__.get("_closing", False):
            raise OSError("Registry recovery required; use Retry registry (F5)")
        records = self.projects if records is None else records
        if workspaces is None:
            store.save_projects(records)
        else:
            store.save_projects(records, workspaces=workspaces)

    def _retry_registry(self):
        """Reload before permitting any write from a previously blocked session."""
        self._cancel_project_save()
        try:
            loaded, report = store.read_registry()
        except (OSError, store.RegistryCorrupt) as exc:
            self._registry_report["reasons"] = [str(exc)]
            self._notify_registry_report()
            return False
        if report["status"] == "fresh":
            # A file disappearing after a failed read is not a new installation.
            report = {**report, "status": "unavailable", "write_blocked": True,
                      "reasons": ["Previously unavailable registry is now missing"]}
        self._registry_report = report
        if report["status"] in {"valid", "recovered"}:
            self._clear_detail()
            self.projects = loaded
            self.workspaces = report.get("workspaces", [])
            self._avail.invalidate()
            self._refresh_workspace_list()
            self._populate_trees()
        if self._registry_blocked():
            self._notify_registry_report()
            return False
        self.scan_btn.configure(text="Rescan (F5)")
        self._notify_registry_report()
        self._status.set("Registry reloaded · scanning and saving enabled",
                         important=True)
        return True

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=(10, 4))

        ttk.Label(top, text="RepoManager", font=("", 13, "bold"),
                  foreground=self.pal["accent2"]).grid(
                      row=0, column=0, sticky="w", padx=(0, 18))
        ttk.Label(top, text="Filter", style="Muted.TLabel").grid(
            row=0, column=1, sticky="w")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._debounce_filter())
        self.filter_entry = ttk.Entry(top, textvariable=self.filter_var)
        self.filter_entry.grid(row=0, column=2, sticky="ew", padx=(4, 12))
        top.columnconfigure(2, weight=1, minsize=150)

        self.scan_btn = ttk.Button(top, text="Rescan (F5)",
                                   command=self.start_scan,
                                   style="Primary.TButton")
        self.scan_btn.grid(row=0, column=3)
        ttk.Button(top, text="Settings", command=self.open_settings).grid(
            row=0, column=4, padx=(6, 0))
        ttk.Button(top, text="Help", command=self.open_help).grid(
            row=0, column=5, padx=(6, 0))
        self.theme_btn = ttk.Button(top, text=self._theme_btn_text(),
                                    command=self.toggle_theme)
        self.theme_btn.grid(row=0, column=6, padx=(6, 0))

        self.status_var = tk.StringVar(value=f"{len(self.projects)} projects")
        self._status = StatusLine(self.status_var)
        ttk.Label(top, textvariable=self.status_var,
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=7,
                                              sticky="w", pady=(5, 0))

        self.problems_lbl = ttk.Label(top, text="", style="Muted.TLabel",
                                      cursor="hand2")
        self.problems_lbl.grid(row=1, column=6, sticky="e", pady=(5, 0))
        self.problems_lbl.bind("<Button-1>", lambda e: self._show_problems())

        self._build_context_ui()

        pane = ttk.Panedwindow(self, orient="vertical")
        pane.pack(fill="both", expand=True, padx=10, pady=4)
        self._outer_pane = pane

        # --- Working on now -------------------------------------------------
        now_frame = ttk.Labelframe(pane, text=" Working on now ", padding=6)
        now_cols = ("focus", "dirty", "sync", "last_commit")
        now_heads = ("Focus", "Dirty", "\u00b1", "Last commit")
        now_wrap = ttk.Frame(now_frame)
        now_wrap.pack(fill="both", expand=True)
        self.now_tree = ttk.Treeview(
            now_wrap, columns=now_cols, show="tree headings", height=4,
            selectmode="browse")
        self.now_tree.column("#0", width=200, minwidth=130, stretch=True)
        for c, h in zip(now_cols, now_heads):
            self.now_tree.heading(c, text=h)
            self.now_tree.column(
                c, width=140 if c != "focus" else 420,
                minwidth=70 if c != "focus" else 180,
                stretch=c == "focus", anchor="w")
        now_sb = ttk.Scrollbar(now_wrap, orient="vertical",
                               command=self.now_tree.yview)
        self.now_tree.configure(yscrollcommand=now_sb.set)
        self.now_tree.pack(side="left", fill="both", expand=True)
        now_sb.pack(side="right", fill="y")
        self.now_tree.bind("<Double-1>", self._on_double_click)
        self.now_tree.bind("<Button-1>", lambda e: self._set_active("now"))
        self.now_tree.bind("<FocusIn>", lambda e: self._set_active("now"))
        self.now_tree.bind("<Button-3>", self._show_context_menu)
        self.now_tree.bind("<<TreeviewSelect>>", self._on_now_select)
        pane.add(now_frame, weight=1)

        # --- Dashboard + detail ---------------------------------------------
        lower = ttk.Panedwindow(pane, orient="horizontal")
        pane.add(lower, weight=5)
        self._lower_pane = lower
        cols = TABLE_COLUMN_ORDER
        self._heads = {name: heading for name, heading, *_ in TABLE_COLUMNS}
        tree_wrap = ttk.Frame(lower)
        self.tree = ttk.Treeview(tree_wrap, columns=cols, show="headings",
                                 selectmode="browse")
        widths = clamp_column_widths(self.settings.get("column_widths"))
        for c, h, _default, minimum, _maximum, stretch in TABLE_COLUMNS:
            self.tree.heading(c, text=h,
                              command=lambda c=c: self._sort_by(c))
            self.tree.column(c, width=widths[c], minwidth=minimum,
                             stretch=stretch, anchor="w")
        ys = ttk.Scrollbar(tree_wrap, orient="vertical",
                           command=self.tree.yview)
        xs = ttk.Scrollbar(tree_wrap, orient="horizontal",
                           command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        tree_wrap.rowconfigure(0, weight=1)
        tree_wrap.columnconfigure(0, weight=1)
        # First-run empty-state overlay: shares the tree cell, kept behind the
        # (opaque) tree until no Projects are present, then raised above it.
        self.empty_lbl = ttk.Label(tree_wrap, text="", style="Muted.TLabel",
                                   justify="left", anchor="nw",
                                   wraplength=440)
        self.empty_lbl.grid(row=0, column=0, sticky="nsew",
                            padx=16, pady=16)
        self.empty_lbl.lower()
        lower.add(tree_wrap, weight=1)
        self.tree.bind("<Button-1>", lambda e: self._set_active("main"))
        self.tree.bind("<FocusIn>", lambda e: self._set_active("main"))
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<ButtonRelease-1>", self._on_tree_column_release,
                       add="+")
        for t in (self.now_tree, self.tree):
            t.bind("<Motion>", self._on_tree_motion, add="+")
            t.bind("<Leave>", self._hide_tooltip, add="+")
            t.bind("<MouseWheel>", self._hide_tooltip, add="+")

        detail = ttk.Labelframe(lower, text=" Project ", padding=0,
                                width=420)
        detail.grid_propagate(False)
        self.detail_canvas = tk.Canvas(
            detail, bg=self.pal["panel"], bd=0, highlightthickness=0,
            takefocus=True)
        detail_scroll = ttk.Scrollbar(
            detail, orient="vertical", command=self.detail_canvas.yview)
        self.detail_canvas.configure(yscrollcommand=detail_scroll.set)
        self.detail_canvas.grid(row=0, column=0, sticky="nsew")
        detail_scroll.grid(row=0, column=1, sticky="ns")
        detail.rowconfigure(0, weight=1)
        detail.columnconfigure(0, weight=1)
        self.detail_body = ttk.Frame(
            self.detail_canvas, style="Surface.TFrame", padding=10)
        self._detail_window = self.detail_canvas.create_window(
            (0, 0), window=self.detail_body, anchor="nw")
        self.detail_body.bind("<Configure>", self._sync_detail_scrollregion)
        self.detail_canvas.bind("<Configure>", self._sync_detail_width)
        self._build_detail(self.detail_body)
        lower.add(detail, weight=0)
        self.bind_all("<MouseWheel>", self._on_detail_mousewheel, add="+")
        pane.bind("<Configure>", self._clamp_panes, add="+")
        pane.bind("<ButtonRelease-1>", self._clamp_panes, add="+")
        lower.bind("<Configure>", self._clamp_panes, add="+")
        lower.bind("<ButtonRelease-1>", self._clamp_panes, add="+")

    def _bind_shortcuts(self):
        self.bind_all("<Control-f>", lambda e: self.filter_entry.focus_set())
        self.bind_all("<F5>", lambda e: self.start_scan())
        self.bind_all("<F1>", lambda e: self.open_help())
        for tree in (self.tree, self.now_tree):
            tree.bind("<Return>", self._launch_primary)
            tree.bind("<KP_Enter>", self._launch_primary)
        # Escape clears the filter when the filter box has focus.
        self.filter_entry.bind(
            "<Escape>", lambda e: (self.filter_var.set(""),
                                   self.filter_entry.focus_set()))

    def _sync_detail_scrollregion(self, _event=None):
        self.detail_canvas.configure(
            scrollregion=self.detail_canvas.bbox("all"))

    def _sync_detail_width(self, event):
        """Keep the embedded detail body no wider than the visible viewport."""
        width = max(1, event.width)
        self.detail_canvas.itemconfigure(
            self._detail_window, width=width)
        # A small shared update keeps the fixed-width detail labels responsive
        # without adding Configure handlers to every individual label.
        wraplength = max(1, width - 20)
        for name in (
                "detail_empty", "d_name", "d_project_id", "d_path",
                "d_worktrees", "d_classification", "d_health",
                "provider_status"):
            widget = getattr(self, name, None)
            if widget is not None:
                try:
                    widget.configure(wraplength=wraplength)
                except tk.TclError:
                    pass


    def _on_detail_mousewheel(self, event):
        """Scroll only when the pointer is inside the contextual panel."""
        widget = self.winfo_containing(event.x_root, event.y_root)
        if widget is None or widget is self.d_notes:
            return None
        path = str(widget)
        if (widget is not self.detail_canvas
                and not path.startswith(str(self.detail_body))):
            return None
        direction = -1 if event.delta > 0 else 1
        steps = max(1, min(12, abs(event.delta) // 120))
        self.detail_canvas.yview_scroll(direction * steps * 3, "units")
        return "break"

    def _clamp_panes(self, _event=None, *, initial=False):
        """Keep both primary panes usable after aggressive separator drags."""
        try:
            height = self._outer_pane.winfo_height()
            if height > 0:
                position = self._outer_pane.sashpos(0)
                if initial:
                    position = 130
                target = max(100, min(position, max(100, height - 340)))
                if target != self._outer_pane.sashpos(0):
                    self._outer_pane.sashpos(0, target)

            width = self._lower_pane.winfo_width()
            if width > 0:
                position = self._lower_pane.sashpos(0)
                if initial:
                    position = width - 420
                # The detail Labelframe, Canvas and scrollbar consume a few
                # pixels beyond the nominal detail minimum. Reserve that
                # chrome so the detail pane remains usable after a drag.
                detail_minimum = 360
                maximum = max(400, width - detail_minimum - 6)
                target = max(400, min(position, maximum))
                if target != self._lower_pane.sashpos(0):
                    self._lower_pane.sashpos(0, target)
        except tk.TclError:
            return

    def _on_tree_column_release(self, event):
        if self.tree.identify_region(event.x, event.y) != "separator":
            return
        if self._column_width_save_job:
            try:
                self.after_cancel(self._column_width_save_job)
            except tk.TclError:
                pass
        self._column_width_save_job = self._schedule_after(
            150, self._persist_column_widths)

    def _apply_table_column_widths(self, widths):
        safe = clamp_column_widths(widths)
        for name in TABLE_COLUMN_ORDER:
            minimum, _maximum = TABLE_COLUMN_LIMITS[name]
            self.tree.column(name, width=safe[name], minwidth=minimum)
        return safe

    def _persist_column_widths(self):
        self._column_width_save_job = None
        current = {name: int(self.tree.column(name, "width"))
                   for name in TABLE_COLUMN_ORDER}
        safe = self._apply_table_column_widths(current)
        updated = {**self.settings, "column_widths": safe}
        try:
            store.save_settings(updated)
        except OSError as exc:
            messagebox.showerror("RepoManager", f"Settings were not saved: {exc}", parent=self)
            return
        self.settings.clear()
        self.settings.update(updated)

    def _reset_table_columns(self):
        safe = self._apply_table_column_widths(TABLE_COLUMN_DEFAULTS)
        updated = {**self.settings, "column_widths": safe}
        try:
            store.save_settings(updated)
        except OSError as exc:
            messagebox.showerror("RepoManager", f"Settings were not saved: {exc}", parent=self)
            return
        self.settings.clear()
        self.settings.update(updated)
        self._status.set("Table columns restored to safe defaults")

    def _project_for_keyboard(self, event=None):
        """Project targeted by keyboard Enter: the tree that received the key.

        Keyboard navigation moves a tree's selection without a Button-1
        click, so Enter must follow the key event's widget (the focused
        tree) rather than the last clicked tree (``_active_tree``). The
        same rule applies to both trees; ``_active_tree`` is only a
        fallback when no tree key event is available.
        """
        widget = event.widget if event is not None else None
        if widget is self.tree:
            sel = self.tree.selection()
        elif widget is self.now_tree:
            sel = self.now_tree.selection()
        else:
            focus = self.focus_get()
            if focus is self.tree:
                sel = self.tree.selection()
            elif focus is self.now_tree:
                sel = self.now_tree.selection()
            else:
                sel = None
        if not sel:
            return self._selected_project()
        return next((p for p in self.projects
                     if project_row_id(p) == sel[0]), None)

    def _launch_primary(self, event=None):
        """Enter on a selected row launches its healthy primary command."""
        p = self._project_for_keyboard(event)
        if not p:
            return "break"
        primary = resolve_primary_for_project(p, self.settings)
        if primary is None:
            candidates = launcher_candidates_for_project(p, self.settings)
            if any(candidate.get("healthy", True)
                   for candidate in candidates):
                self._show_more_launchers(candidates)
                self._status.set(
                    f"{projects.project_display_name(p)}: choose a launcher")
                return "break"
            self._status.set(
                f"{projects.project_display_name(p)}: no usable launcher "
                "\u2014 see Launch section")
            return "break"
        self._run_launcher(primary)
        return "break"

    def _refresh_working_action_label(self):
        """Keep the visible current-work action aligned with lifecycle state."""
        button = getattr(self, "d_work_action_btn", None)
        if button is None:
            return
        status = self.d_status.get() if hasattr(self, "d_status") else None
        if not status and self._current:
            status = self._current.get("status")
        button.configure(text=working_action_label(status))

    def _toggle_working_on_this(self):
        """Toggle current work without coupling work state to pin metadata."""
        if not self._current:
            return
        if self._current.get("status") == "active":
            self.d_status.set("paused")
        else:
            self.d_status.set("active")
            self.d_pinned.set(True)
        self._save_detail()

    def _work_on_this(self):
        """Compatibility alias for the shared current-work action."""
        self._toggle_working_on_this()

    # ------------------------------------------------------------- theming
    def _theme_btn_text(self):
        """The button shows the theme a click switches to."""
        dark = self.settings.get("theme", "dark") == "dark"
        return "Ice Light" if dark else "Dark"

    def toggle_theme(self):
        new_name = ("light" if self.settings.get("theme", "dark") == "dark"
                    else "dark")
        updated = {**self.settings, "theme": new_name}
        try:
            store.save_settings(updated)
        except OSError as exc:
            messagebox.showerror("RepoManager", f"Settings were not saved: {exc}", parent=self)
            return
        self.settings.clear()
        self.settings.update(updated)
        self.pal = theme.apply(self, new_name)
        self._apply_row_colors()
        self.theme_btn.configure(text=self._theme_btn_text())
        theme.style_tk_widget(self.d_notes, self.pal, "text")
        self.detail_canvas.configure(bg=self.pal["panel"])
        if self._current:
            self.d_state.configure(
                style=theme.semantic_style(self._current.get("status")))
        if self._health_result:
            self.d_health_status.configure(
                style=theme.semantic_style(self._health_result.status))
        observation = getattr(self, "_provider_observation", None)
        if observation:
            correspondence = providers.provider_correspondence(
                self._current.get("remote") if self._current else None)
            self.provider_status.configure(
                style=theme.semantic_style(
                    providers.provider_presentation_status(
                        correspondence, observation)))
        self._refresh_agent_status()
        if self.problems_lbl.cget("text"):
            self.problems_lbl.configure(style=theme.semantic_style("WARN"))
        help_dialog = getattr(self, "_help_dialog", None)
        if help_dialog is not None and help_dialog.winfo_exists():
            help_dialog.configure(bg=self.pal["bg"])
        self._populate_trees()

    def _apply_row_colors(self):
        p = self.pal
        for tree in (self.tree, self.now_tree):
            tree.tag_configure("dirty", background=p["row_dirty_bg"],
                               foreground=p["row_dirty_fg"])
            tree.tag_configure("noremote", foreground=p["row_norem_fg"])
            tree.tag_configure("sync", foreground=p["row_sync_fg"])
            tree.tag_configure("archived", foreground=p["row_arch_fg"])
            tree.tag_configure("stale", foreground=p["row_arch_fg"],
                               font=("", 9, "italic"))
            tree.tag_configure("won-empty", foreground=p["muted"],
                               font=("", 9, "italic"))

    # ------------------------------------------------------------- sorting
    def _sort_by(self, col):
        previous = (self._sort_col, self._sort_desc)
        if self._sort_col == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col, self._sort_desc = col, False
        updated = {**self.settings,
                   "sort": [self._sort_col, self._sort_desc]}
        try:
            store.save_settings(updated)
        except OSError as exc:
            self._sort_col, self._sort_desc = previous
            messagebox.showerror("RepoManager", f"Settings were not saved: {exc}", parent=self)
            return
        self.settings.clear()
        self.settings.update(updated)
        self._update_heading_marks()
        self._populate_trees()

    def _update_heading_marks(self):
        for c, base in self._heads.items():
            mark = ""
            if c == self._sort_col:
                mark = " \u25bc" if self._sort_desc else " \u25b2"
            self.tree.heading(c, text=base + mark)

    # ------------------------------------------------------------ populate
    def _debounce_filter(self):
        if self._filter_job:
            try:
                self.after_cancel(self._filter_job)
            except tk.TclError:
                pass
            self.__dict__.get("_after_jobs", set()).discard(self._filter_job)
        self._filter_job = self._schedule_after(
            FILTER_DEBOUNCE_MS, self._populate_trees)

    def _main_row_state(self, p):
        """Treeview row kwargs (values+tags) for one project's main-tree row."""
        sync = ""
        if p.get("ahead") or p.get("behind"):
            sync = f"+{p.get('ahead', 0)}/-{p.get('behind', 0)}"
        dirty = (p.get("dirty") if p.get("status_available", True)
                 else "?")
        trees = (len(p.get("worktrees") or [])
                 if p.get("worktrees_available", True) else "?")
        return {"values": (
            projects.project_display_name(p),
            projects.classification_display(p),
            ("Pinned · " if p.get("pinned") else "")
            + str(p.get("status", "")),
            p.get("branch") or "(none)",
            dirty,
            sync,
            trees,
            p.get("last_commit_date") or "",
            p.get("path") or p.get("folder_path") or "(no folder)",
        ), "tags": (row_tag(p, self._avail.get(p.get("path") or p.get("folder_path") or "")),)}

    def _now_row_state(self, p):
        """Treeview row kwargs for one project in the Working-on-now tree."""
        sync = f"+{p.get('ahead', 0)}/-{p.get('behind', 0)}" if (
            p.get("ahead") or p.get("behind")) else ""
        dirty = (p.get("dirty") if p.get("status_available", True)
                 else "?")
        return {"text": ("Pinned · " if p.get("pinned") else "")
                + projects.project_display_name(p),
                "tags": (row_tag(p, self._avail.get(p.get("path") or p.get("folder_path") or "")),),
                "values": (
                    p.get("focus") or "\u2014",
                    dirty,
                    sync,
                    p.get("last_commit_date") or "",
                )}

    def _empty_row_state(self):
        """Treeview row kwargs for the Working-on-now placeholder row."""
        return {"text": "\u2014", "tags": ("won-empty",),
                "values": ("Nothing here yet \u2014 set a Project to Active "
                            "to add it here.",
                            "", "", "")}

    def _populate_trees(self):
        flt = self.filter_var.get().lower().strip()

        prev_main = tuple(self.tree.selection())
        prev_now = tuple(self.now_tree.selection())

        unique = {}
        for p in self.projects:
            if projects.is_ignored(p):
                continue
            unique.setdefault(project_row_id(p), p)
        visible = [p for p in unique.values() if is_visible(p, flt)]
        ordered = sorted_projects(visible, self._sort_col, self._sort_desc)
        if unique:
            self.empty_lbl.lower()
        else:
            self.empty_lbl.configure(
                text=("Registry unavailable — Projects have not been loaded.\n"
                      "Resolve the storage problem, then use Retry registry (F5)."
                      if self._registry_blocked() else
                      empty_state_text(self.settings.get("roots", []))))
            self.empty_lbl.lift()

        # --- main dashboard tree: reuse existing rows where possible -------
        main_by_path = {project_row_id(p): p for p in ordered}
        reconcile_tree(self.tree, [project_row_id(p) for p in ordered],
                       lambda iid: self._main_row_state(main_by_path[iid]))
        if prev_main and prev_main[0] in self.tree.get_children():
            if tuple(self.tree.selection()) != prev_main:
                self.tree.selection_set(prev_main)

        # --- Working on now tree -------------------------------------------
        # Workflow membership is independent from the table search filter.
        now_items = working_on_now_rows(unique.values(), exists=self._avail.get)
        now_desired = ([project_row_id(p) for p in now_items] if now_items
                       else ["__won-empty__"])
        now_by_path = {project_row_id(p): p for p in now_items}
        reconcile_tree(self.now_tree, now_desired, lambda iid: (
            self._empty_row_state() if iid == "__won-empty__"
            else self._now_row_state(now_by_path[iid])))
        if prev_now and prev_now[0] in self.now_tree.get_children():
            self.now_tree.selection_set(prev_now)

        if (prev_main and prev_main[0] not in self.tree.get_children()
                and self._active_tree == "main" and self._current is not None
                and project_row_id(self._current) == prev_main[0]):
            self._clear_detail()

        parts = [f"{len(ordered)} shown / {len(unique)} projects"]
        if self._scanning:
            parts.append("scanning\u2026")
        if self._registry_blocked():
            self._status.set("Registry recovery required · Retry registry (F5)",
                             important=True)
        else:
            self._status.set("   |   ".join(parts))
        if self._problems or self._move_suggestions:
            self.problems_lbl.configure(
                text=attention_summary(self._problems,
                                       len(self._move_suggestions)),
                style=theme.semantic_style("WARN"))
        else:
            self.problems_lbl.configure(text="", style="Muted.TLabel")

    def _update_row(self, p):
        """Refresh a single project's row in both trees without re-sorting."""
        row_id = project_row_id(p)
        if row_id in self.tree.get_children():
            self.tree.item(row_id, **self._main_row_state(p))
        if row_id in self.now_tree.get_children():
            self.now_tree.item(row_id, **self._now_row_state(p))

    def _reconcile_now_tree(self):
        """Reflect membership changes in Working-on-now immediately.

        Called after a status edit changes whether a project belongs in
        the pane (status == active). Reuses the incremental
        reconcile machinery on the Working-on-now tree only — no main-tree
        rebuild, no rescan — so the pane's contents, identity and ordering
        stay consistent with ``_populate_trees``.
        """
        unique = {}
        for p in self.projects:
            unique.setdefault(project_row_id(p), p)
        now_items = working_on_now_rows(unique.values(), exists=self._avail.get)
        now_desired = ([project_row_id(p) for p in now_items] if now_items
                       else ["__won-empty__"])
        now_by_path = {project_row_id(p): p for p in now_items}
        reconcile_tree(self.now_tree, now_desired, lambda iid: (
            self._empty_row_state() if iid == "__won-empty__"
            else self._now_row_state(now_by_path[iid])))
        prev_now = tuple(self.now_tree.selection())
        if prev_now and prev_now[0] in self.now_tree.get_children():
            self.now_tree.selection_set(prev_now)

    # ------------------------------------------------------------- detail
    def _on_select(self, event=None):
        # A virtual event can be emitted while another tree is being mirrored
        # or restored during refresh. Only the focused tree owns the action
        # target; mouse/context paths set the owner before selection changes.
        if self.focus_get() is self.tree:
            self._set_active("main")
        if self._active_tree != "main":
            return
        sel = self.tree.selection()
        if not sel:
            if self._active_tree == "main":
                self._clear_detail()
            return
        self._show_detail(sel[0])

    def _on_now_select(self, event=None):
        if self.focus_get() is self.now_tree:
            self._set_active("now")
        if self._active_tree != "now":
            return
        sel = self.now_tree.selection()
        if not sel:
            if self._active_tree == "now":
                self._clear_detail()
            return
        path = sel[0]
        if path == "__won-empty__":
            if self._active_tree == "now":
                self._clear_detail()
            return
        if path in self.tree.get_children():
            if tuple(self.tree.selection()) != (path,):
                self.tree.selection_set(path)
                self.tree.see(path)
        self._show_detail(path)

    def _show_detail(self, path):
        if self._current and project_row_id(self._current) == path:
            return  # already showing this project — keep in-progress edits
        self._flush_note_save()
        proj = next((p for p in self.projects
                     if project_row_id(p) == path), None)
        self._current = proj
        if not proj:
            self._clear_detail()
            return
        self._render_detail_shell(proj, clear_note=True)
        self._request_detail_observation(proj)
        if hasattr(self, "provider"):
            self._show_provider(proj)
        if hasattr(self, "agent_status"):
            self._refresh_agent_status()

    def _render_detail_shell(self, proj, *, clear_note=False):
        """Render lightweight selection state; expensive observations are pending."""
        self.detail_empty.pack_forget()
        if not self.detail_content.winfo_manager():
            self.detail_content.pack(fill="x")
        self.detail_canvas.yview_moveto(0.0)
        display_name = projects.project_display_name(proj)
        self.d_name.configure(text=display_name)
        self.d_state.configure(
            text=self._state_text(proj),
            style=theme.semantic_style(proj.get("status")))
        self.d_project_id.configure(
            text=f"Project ID: {projects.project_id(proj) or '(none)'}")
        self.d_repository.configure(
            text=f"Repository: {display_name if projects.is_repository_backed(proj) else 'None'}")
        self.d_worktrees.configure(text=self._worktrees_text(proj))
        self.d_path.configure(
            text=proj.get("path") or proj.get("folder_path") or "")
        self.d_classification.configure(
            text=f"Type: {projects.classification_display(proj)}")
        self.d_status.set(proj.get("status") or "idea")
        self.d_pinned.set(bool(proj.get("pinned")))
        self._refresh_working_action_label()
        self.d_focus.delete(0, "end")
        self.d_focus.insert(0, proj.get("focus") or "")
        folder = proj.get("path") or proj.get("folder_path") or ""
        self._note_target = (
            proj.get("name", ""), folder, projects.project_id(proj))
        self._note_loading_gen = getattr(self, "_detail_gen", 0) + 1
        if clear_note:
            self._note_user_edited_gen = None
            self.d_notes.delete("1.0", "end")
            self.d_notes.insert("1.0", "")
        for widget in self.d_launch.winfo_children():
            widget.destroy()
        ttk.Label(
            self.d_launch, text="Loading local launchers…",
            style=theme.semantic_style("IN_PROGRESS")).pack(anchor="w")
        self._health_result = None
        self.d_health_status.configure(
            text="Checking local repository health…",
            style=theme.semantic_style("IN_PROGRESS"))
        self.d_health.configure(text="Local health details are loading…")

    def _request_detail_observation(self, proj):
        """Coalesce local detail work and run it away from Tk."""
        self._detail_gen = getattr(self, "_detail_gen", 0) + 1
        generation = self._detail_gen
        snapshot = copy.deepcopy(proj)
        settings = copy.deepcopy(self.settings)
        request = (generation, snapshot, settings)
        lock = getattr(self, "_detail_lock", None)
        if lock is None:
            self._detail_lock = threading.Lock()
            lock = self._detail_lock
        with lock:
            self._detail_pending = request
            worker = self.__dict__.get("_detail_worker")
            if worker is not None and worker.is_alive():
                return
            worker = threading.Thread(
                target=self._detail_worker_loop, daemon=True)
            self._detail_worker = worker
        try:
            worker.start()
        except RuntimeError:
            with lock:
                if self.__dict__.get("_detail_worker") is worker:
                    self._detail_worker = None
                if self._detail_pending == request:
                    self._detail_pending = None
            log.exception("could not start detail worker")
            # This method runs on Tk's thread, so it is safe to complete the
            # visible error state synchronously when thread creation fails.
            self._apply_detail_observation(
                {"project_id": projects.project_id(snapshot),
                 "path": snapshot.get("path") or snapshot.get("folder_path") or ""},
                {"error": "detail worker could not be started",
                 "health": None, "launchers": (), "note": ""},
                generation)

    def _detail_worker_loop(self):
        """Process only the latest pending detail request."""
        while True:
            lock = self._detail_lock
            with lock:
                request = self._detail_pending
                self._detail_pending = None
            if request is None:
                with lock:
                    if self._detail_pending is None:
                        self._detail_worker = None
                        return
                    continue
            generation, snapshot, settings = request
            target = {
                "project_id": projects.project_id(snapshot),
                "path": snapshot.get("path") or snapshot.get("folder_path") or "",
            }
            try:
                observation = compute_detail_observation(snapshot, settings)
            except Exception as exc:
                log.exception("local detail observation failed")
                observation = {"error": str(exc), "health": None,
                               "launchers": (), "note": ""}
            self._scan_queue.put(("detail", target, observation, generation))

    def _apply_detail_observation(self, target, observation, generation):
        """Apply a worker result only when it still belongs to the selection."""
        if generation != getattr(self, "_detail_gen", 0):
            return
        if not isinstance(observation, dict):
            observation = {"error": "malformed detail observation",
                           "health": None, "launchers": (), "note": ""}
        current = self.__dict__.get("_current")
        if current is None:
            return
        current_target = {
            "project_id": projects.project_id(current),
            "path": current.get("path") or current.get("folder_path") or "",
        }
        if current_target != target:
            return
        self._loading_detail = True
        try:
            if observation.get("error") or observation.get("health_error"):
                self.d_health_status.configure(
                    text="Unknown — local detail observation failed",
                    style=theme.semantic_style("UNKNOWN"))
                self.d_health.configure(text="Local detail could not be evaluated.")
            elif observation.get("health") is not None:
                self._render_health_result(current, observation["health"])

            if projects.is_repository_backed(current):
                self._populate_launchers(
                    current, observation.get("launchers") or ())
            else:
                for widget in self.d_launch.winfo_children():
                    widget.destroy()
                ttk.Label(
                    self.d_launch,
                    text="Unavailable · no associated repository",
                    style=theme.semantic_style("UNAVAILABLE")).pack(anchor="w")

            if (getattr(self, "_note_loading_gen", None) == generation
                    and getattr(self, "_note_user_edited_gen", None) is None
                    and observation.get("note_error") is None):
                self.d_notes.delete("1.0", "end")
                self.d_notes.insert("1.0", observation.get("note") or "")
            if getattr(self, "_note_loading_gen", None) == generation:
                # Whether loaded or failed, this request is no longer pending.
                self._note_loading_gen = None
        finally:
            self._loading_detail = False

    def _clear_detail(self):
        self._detail_gen = getattr(self, "_detail_gen", 0) + 1
        lock = self.__dict__.get("_detail_lock")
        if lock is not None:
            with lock:
                self._detail_pending = None
        self._flush_note_save()
        self._current = None
        self._note_target = None
        self.detail_content.pack_forget()
        if not self.detail_empty.winfo_manager():
            self.detail_empty.pack(fill="x")
        self.d_name.configure(text="")
        self.d_state.configure(text="", style=theme.semantic_style("UNKNOWN"))
        self.d_project_id.configure(text="Project ID: (none)")
        self.d_repository.configure(text="Repository: None")
        self.d_worktrees.configure(text="")
        self.d_classification.configure(text="Classification: Unknown")
        self.d_health_status.configure(
            text="", style=theme.semantic_style("UNKNOWN"))
        self.d_health.configure(text="")
        self.provider_status.configure(
            text="Provider: NOT_RUN", style=theme.semantic_style("UNKNOWN"))
        self._health_result = None
        self.d_path.configure(text="")
        self.d_status.set("")
        self.d_pinned.set(False)
        self._refresh_working_action_label()
        self.d_focus.delete(0, "end")
        self.d_notes.delete("1.0", "end")
        for w in self.d_launch.winfo_children():
            w.destroy()
        if hasattr(self, "agent_status"):
            self._refresh_agent_status()

    def _state_text(self, proj):
        """Short lifecycle badge: capitalized status plus pin state."""
        text = (str(proj.get("status") or "idea").capitalize())
        if proj.get("pinned"):
            text += " \u00b7 pinned"
        return text

    def _worktrees_text(self, proj):
        """One-line summary of already-collected Worktree records."""
        if proj.get("worktrees_available") is False:
            return "Worktrees: unavailable"
        trees = proj.get("worktrees") or []
        linked = [t for t in trees if not t.get("current")]
        if not trees:
            return ""
        if not linked:
            return "Worktree: this checkout only"
        names = " \u00b7 ".join(
            (t.get("branch") or Path(t.get("path", "")).name)
            for t in linked[:4])
        more = f" (+{len(linked) - 4} more)" if len(linked) > 4 else ""
        return f"Worktrees: main + {len(linked)} linked ({names}{more})"

    def _choose_association(self):
        """Choose another known healthy repository for the current Project."""
        if self._registry_blocked() or not self._current:
            return
        candidates = projects.association_candidates(self.projects, self._current)
        if not candidates:
            messagebox.showinfo("RepoManager", "No other valid known repositories are available.")
            return
        dlg = tk.Toplevel(self)
        self._prepare_dialog(
            dlg, "Change associated repository", "620x380")
        ttk.Label(dlg, text="Select an existing valid repository:").pack(anchor="w", padx=12, pady=(12, 6))
        listbox = tk.Listbox(dlg, exportselection=False, height=min(10, len(candidates)))
        theme.style_tk_widget(listbox, self.pal, "list")
        listbox.pack(fill="both", expand=True, padx=12, pady=6)
        for candidate in candidates:
            listbox.insert(
                "end",
                f"{projects.project_display_name(candidate)} — "
                f"{candidate['path']}")

        def apply():
            selection = listbox.curselection()
            if not selection:
                messagebox.showerror("RepoManager", "Select a repository first.", parent=dlg)
                return
            target = candidates[selection[0]]
            valid, reason = projects.validate_association_target(self.projects, self._current, target)
            if not valid:
                messagebox.showerror("RepoManager", reason, parent=dlg)
                return
            projects.associate_repository(self._current, target)
            self._refresh_current_detail()
            self._update_row(self._current)
            self._schedule_project_save()
            dlg.destroy()

        buttons = ttk.Frame(dlg)
        buttons.pack(fill="x", padx=12, pady=12)
        ttk.Button(buttons, text="Cancel", command=dlg.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Save association", command=apply,
                   style="Primary.TButton").pack(side="right")
        dlg.bind("<Return>", lambda _e: apply())
        listbox.focus_set()
        listbox.selection_set(0)

    def _refresh_current_detail(self):
        """Refresh the selected Project without doing local observation in Tk."""
        if not self._current:
            return
        current = self._current
        self._render_detail_shell(current, clear_note=False)
        self._request_detail_observation(current)
        if hasattr(self, "provider"):
            self._show_provider(current)

    def _on_focus_changed(self, event=None):
        if self._registry_blocked() or not self._current or self._loading_detail:
            return
        if event and event.keysym in MODIFIER_KEYS:
            return
        if event and event.keysym == "Escape":
            self.d_focus.delete(0, "end")
            self.d_focus.insert(0, self._current.get("focus") or "")
            return
        if event and event.keysym == "Return":
            self._save_detail()
            return
        self._current["focus"] = self.d_focus.get().strip()

    def _save_detail(self):
        if self._registry_blocked() or self._loading_detail or not self._current:
            return
        projects.apply_curation(
            self._current,
            status=self.d_status.get(),
            pinned=self.d_pinned.get(),
            focus=self.d_focus.get(),
        )
        self._refresh_working_action_label()
        self._update_row(self._current)  # no full re-sort: rows stay put
        self._reconcile_now_tree()
        self._schedule_project_save()

    def _schedule_project_save(self):
        """Debounce full-registry persistence across rapid detail edits."""
        if self._registry_blocked() or self.__dict__.get("_closing", False):
            self._cancel_project_save()
            return
        if self._save_job:
            try:
                self.after_cancel(self._save_job)
            except tk.TclError:
                pass
            self.__dict__.get("_after_jobs", set()).discard(self._save_job)
        self._save_job = self._schedule_after(
            SAVE_DEBOUNCE_MS, self._flush_project_save)

    def _cancel_project_save(self):
        """Drop a pending (not yet executed) debounced registry write."""
        if self._save_job:
            try:
                self.after_cancel(self._save_job)
            except tk.TclError:
                pass
            self.__dict__.get("_after_jobs", set()).discard(self._save_job)
            self._save_job = None

    def _flush_project_save(self):
        """Persist the registry now, cancelling any pending debounced write."""
        self._cancel_project_save()
        if self._registry_blocked() or self.__dict__.get("_closing", False):
            return
        self._persist_projects()

    def _populate_coalesced(self):
        """Run _populate_trees at most once per idle cycle when requested
        multiple times in the same tick (bursty scan/move triggers)."""
        if not self._populate_pending:
            self._populate_pending = True
            self._schedule_after_idle(self._populate_due)

    def _populate_due(self):
        self._populate_pending = False
        self._populate_trees()

    def _schedule_note_save(self, event=None):
        if not self._current:
            return
        self._note_user_edited_gen = getattr(self, "_detail_gen", 0)
        if not self._current:
            return
        self._note_target = (
            self._current["name"], projects.project_folder(self._current) or "",
            projects.project_id(self._current))
        if self._note_save_job:
            try:
                self.after_cancel(self._note_save_job)
            except tk.TclError:
                pass
            self.__dict__.get("_after_jobs", set()).discard(self._note_save_job)
        self._note_save_job = self._schedule_after(
            NOTE_SAVE_DELAY_MS, self._flush_note_save)

    def _flush_note_save(self):
        if self._note_save_job:
            try:
                self.after_cancel(self._note_save_job)
            except tk.TclError:
                pass
            self.__dict__.get("_after_jobs", set()).discard(self._note_save_job)
            self._note_save_job = None
        if not self._note_target:
            return
        name, path, *identity = self._note_target
        store.save_note(
            name, path, self.d_notes.get("1.0", "end-1c"),
            identity[0] if identity else None)

    def _build_detail(self, parent):
        self.detail_empty = ttk.Label(
            parent,
            text="Select a Project to inspect its state, Health and actions.",
            style=theme.semantic_style("UNKNOWN"), wraplength=370,
            justify="left")
        self.detail_empty.pack(fill="x")
        self.detail_content = ttk.Frame(parent, style="Surface.TFrame")
        self.detail_content.pack(fill="x")
        parent = self.detail_content

        self.d_name = ttk.Label(parent, text="", font=("", 12, "bold"),
                                style="Surface.TLabel", wraplength=370,
                                foreground=self.pal["accent2"])
        self.d_name.pack(anchor="w", pady=(0, 2))
        self.d_state = ttk.Label(
            parent, text="", style=theme.semantic_style("UNKNOWN"))
        self.d_state.pack(anchor="w", pady=(0, 6))
        self.d_project_id = ttk.Label(parent, text="Project ID: (none)",
                                      style="SurfaceMuted.TLabel",
                                      wraplength=370)
        self.d_path = ttk.Label(parent, text="",
                                style="SurfaceMuted.TLabel", wraplength=370)
        self.d_path.pack(anchor="w", pady=(0, 4))
        self.d_repository = ttk.Label(
            parent, text="Repository: None", style="SurfaceMuted.TLabel")
        self.d_repository.pack(anchor="w", pady=(0, 2))
        self.d_worktrees = ttk.Label(parent, text="",
                                     style="SurfaceMuted.TLabel",
                                     wraplength=370)
        self.d_worktrees.pack(anchor="w", pady=(0, 2))
        self.d_classification = ttk.Label(parent, text="Classification: Unknown",
                                          style="SurfaceMuted.TLabel",
                                          wraplength=370,
                                          justify="left")
        self.d_classification.pack(anchor="w", pady=(0, 4))
        self.d_technical_details_btn = ttk.Button(
            parent, text="Technical project details…",
            command=self._open_project_technical_details)
        self.d_technical_details_btn.pack(anchor="w", pady=(0, 6))
        self.associate_btn = ttk.Button(parent, text="Change associated repository",
                                        command=self._choose_association)
        self.associate_btn.pack(anchor="w", pady=(0, 8))

        curation = ttk.Labelframe(parent, text=" Curation ", padding=8)
        curation.pack(fill="x", pady=(0, 8))
        row = ttk.Frame(curation, style="Surface.TFrame")
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="Status", style="Surface.TLabel").pack(side="left")
        self.d_status = ttk.Combobox(row, values=list(STATUSES),
                                     state="readonly", width=10)
        self.d_status.pack(side="left", padx=(4, 16))
        self.d_status.bind("<<ComboboxSelected>>",
                           lambda e: self._save_detail())
        self.d_pinned = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Pinned", variable=self.d_pinned,
                        command=self._save_detail,
                        style="Surface.TCheckbutton").pack(side="left")

        frow = ttk.Frame(curation, style="Surface.TFrame")
        frow.pack(fill="x")
        ttk.Label(frow, text="Focus", style="Surface.TLabel").pack(side="left")
        self.d_focus = ttk.Entry(frow)
        self.d_focus.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.d_focus.bind("<KeyRelease>", self._on_focus_changed)
        self.d_focus.bind("<FocusOut>", lambda e: self._save_detail())

        actions = ttk.Labelframe(parent, text=" Project actions ", padding=8)
        actions.pack(fill="x", pady=(0, 8))
        action_row = ttk.Frame(actions, style="Surface.TFrame")
        action_row.pack(fill="x")
        action_row.columnconfigure(0, weight=1)
        action_row.columnconfigure(1, weight=1)
        for index, (label, cmd, style_name) in enumerate((
                (working_action_label(None), self._toggle_working_on_this,
                 "Primary.TButton"),
                ("VS Code", self.open_vscode, "TButton"),
                ("Explorer", self.open_explorer, "TButton"),
                ("Terminal", self.open_terminal, "TButton"))):
            button = ttk.Button(action_row, text=label, command=cmd,
                                style=style_name)
            button.grid(
                row=index // 2, column=index % 2, sticky="ew",
                padx=(0 if index % 2 == 0 else 3,
                      3 if index % 2 == 0 else 0),
                pady=(0 if index < 2 else 6, 0))
            if index == 0:
                self.d_work_action_btn = button

        notes = self.d_notes_section = ttk.Labelframe(
            parent, text=" Notes ", padding=8)
        notes.pack(fill="x", pady=(0, 8))
        note_wrap = ttk.Frame(notes, style="Surface.TFrame")
        note_wrap.pack(fill="x")
        self.d_notes = tk.Text(note_wrap, wrap="word", width=42, height=5,
                               undo=True, padx=6, pady=4)
        theme.style_tk_widget(self.d_notes, self.pal, "text")
        nb = ttk.Scrollbar(note_wrap, command=self.d_notes.yview)
        self.d_notes.configure(yscrollcommand=nb.set)
        self.d_notes.pack(side="left", fill="x", expand=True)
        nb.pack(side="right", fill="y")
        self.d_notes.bind("<KeyRelease>", self._schedule_note_save)

        health_box = self.d_health_section = ttk.Labelframe(
            parent, text=" Repository Health ", padding=8)
        health_box.pack(fill="x", pady=(0, 8))
        health_head = ttk.Frame(health_box, style="Surface.TFrame")
        health_head.pack(fill="x")
        ttk.Button(health_head, text="What does this mean?",
                   style="Link.TButton",
                   command=lambda: self.open_help("health")).pack(side="right")
        self.d_health_status = ttk.Label(
            health_head, text="", style=theme.semantic_style("UNKNOWN"),
            wraplength=220, justify="left")
        self.d_health_status.pack(side="left", fill="x", expand=True)
        self.d_health = ttk.Label(
            health_box, text="", style="SurfaceMuted.TLabel",
            wraplength=370, justify="left")
        self.d_health.pack(anchor="w", fill="x", pady=(6, 0))
        self.d_health_details_btn = ttk.Button(
            health_box, text="View health details",
            command=self._open_health_details)
        self.d_health_details_btn.pack(anchor="w", pady=(6, 0))

        export_box = self.d_export_section = ttk.Labelframe(
            parent, text=" Export ", padding=8)
        export_box.pack(fill="x", pady=(0, 8))
        ttk.Button(export_box, text="Project JSON…",
                   command=self._export_current_project).pack(side="left")
        ttk.Button(export_box, text="Repository report…",
                   command=self._export_current_report).pack(
                       side="left", padx=(6, 0))

        provider_box = self.d_provider_section = ttk.Labelframe(
            parent, text=" Git host / Provider ", padding=8)
        provider_box.pack(fill="x", pady=(0, 8))
        ttk.Button(provider_box, text="About providers",
                   style="Link.TButton",
                   command=lambda: self.open_help("provider")).pack(
                       anchor="e")
        self.provider_status = ttk.Label(
            provider_box, text="Provider: NOT_RUN",
            style=theme.semantic_style("UNKNOWN"), wraplength=370,
            justify="left")
        self.provider_status.pack(anchor="w", fill="x", pady=(2, 0))

        launch_box = self.d_launch_section = ttk.Labelframe(
            parent, text=" Launch ", padding=8)
        launch_box.pack(fill="x", pady=(0, 4))
        self.d_launch = ttk.Frame(launch_box, style="Surface.TFrame")
        self.d_launch.pack(fill="x")

        # Keep the common decision path above notes, provider evidence and
        # exports without discarding any of those secondary surfaces.
        for section in (curation, actions, health_box, launch_box, notes,
                        provider_box, export_box):
            section.pack_forget()
            section.pack(fill="x", pady=(0, 8))
        self.detail_content.pack_forget()

    def _open_project_technical_details(self):
        project = self._current
        if not project:
            return
        name = projects.project_display_name(project)
        location = project.get("path") or project.get("folder_path") or "(none)"
        repository = name if projects.is_repository_backed(project) else "None"
        lines = [
            f"Project: {name}",
            f"Project ID: {projects.project_id(project) or '(none)'}",
            f"Location: {location}",
            f"Repository association: {repository}",
            f"Classification evidence: {projects.classification_summary(project)}",
        ]
        worktrees = self._worktrees_text(project)
        if worktrees:
            lines.append(worktrees)
        self._open_text_dialog(
            "Technical project details", "\n".join(lines), geometry="720x420")

    def _export_current_project(self):
        if not self._current:
            return
        target = filedialog.asksaveasfilename(
            parent=self, title="Export Project metadata", defaultextension=".json",
            filetypes=(("JSON", "*.json"),))
        if not target:
            return
        try:
            reports.write_text_atomic(
                target,
                reports.to_json(projects.build_project_export(self._current)))
            self._status.set(f"Project export written: {target}", important=True)
        except OSError as exc:
            messagebox.showerror("RepoManager", f"Export failed: {exc}", parent=self)

    def _export_current_report(self):
        project = self._current or self._selected_project()
        if not project:
            messagebox.showinfo("RepoManager", "Select a Project first.", parent=self)
            return
        target = filedialog.asksaveasfilename(
            parent=self, title="Export repository report", defaultextension=".md",
            filetypes=(("Markdown", "*.md"), ("JSON", "*.json")))
        if not target:
            return
        try:
            report = projects.build_repository_report(project)
            output = reports.to_json(report) if str(target).lower().endswith(".json") else reports.to_markdown(report)
            reports.write_text_atomic(target, output)
            self._status.set(f"Report written: {target}", important=True)
        except OSError as exc:
            messagebox.showerror("RepoManager", f"Report failed: {exc}", parent=self)

    def _show_health(self, proj):
        """Evaluate and render health for direct callers outside selection."""
        self._render_health_result(
            proj, health.evaluate_repository(proj.get("path"), proj))
        if hasattr(self, "provider"):
            self._show_provider(proj)

    def _render_health_result(self, proj, result):
        """Render a completed plain-data HealthResult on the Tk thread."""
        self._health_result = result
        summary = result.summary
        self.d_health_status.configure(
            text=health_headline(result.status),
            style=theme.semantic_style(result.status))
        enabled = tuple(f for f in result.findings
                        if f.importance != health.DISABLED)
        material = tuple(f for f in enabled
                         if f.importance != health.INFORMATIONAL)
        counts = ", ".join(
            f"{label}: {sum(f.status == status for f in material)}"
            for status, label in ((health.FAIL, "problems"),
                                  (health.UNKNOWN, "incomplete"),
                                  (health.WARN, "recommendations"))
            if any(f.status == status for f in material))
        informational = sum(
            f.importance == health.INFORMATIONAL
            and f.status not in (health.PASS, health.NOT_APPLICABLE)
            for f in enabled)
        lines = [
            f"{summary.finding_count} checks evaluated",
            counts or "No required or recommended action found",
            f"Evaluated {result.evaluated_at} · stale evidence {summary.stale_count}",
        ]
        if informational:
            lines.append(
                f"{informational} informational observation"
                f"{'' if informational == 1 else 's'} in Health details")
        actionable = []
        for finding in result.prioritized_findings():
            if (finding.importance == health.INFORMATIONAL
                    or finding.status in (health.PASS,
                                          health.NOT_APPLICABLE)):
                continue
            prefix = {
                health.FAIL: "Problem",
                health.UNKNOWN: "Incomplete",
                health.WARN: "Recommendation",
            }.get(finding.status, "Notice")
            actionable.append(f"{prefix}: {finding.explanation}")
            if finding.remediation:
                actionable.append(f"Next: {finding.remediation}")
            if len(actionable) >= 4:
                break
        if actionable:
            lines.append("\n" + "\n".join(actionable[:4]))
        else:
            lines.append("\nNo actionable finding in the checks that ran.")
        self.d_health.configure(text="\n".join(lines))

    # The All-checks Treeview colours finding rows by status using the
    # existing palette tokens; group rows stay structural (no status tag).
    _HEALTH_STATUS_TAGS = {
        health.FAIL: "health_fail",
        health.WARN: "health_warn",
        health.PASS: "health_pass",
        health.UNKNOWN: "health_unknown",
        health.NOT_APPLICABLE: "health_not_applicable",
    }

    def _build_health_navigator(self, parent, result):
        """Return (tree, findings_by_item) for one HealthResult."""
        groups = {name: [] for name in HEALTH_GROUP_ORDER}
        for finding in result.findings:
            groups[health_finding_group(finding)].append(finding)
        tree = ttk.Treeview(
            parent, columns=("status", "importance", "freshness"),
            show="tree headings", selectmode="browse")
        tree.heading("#0", text="Check", anchor="w")
        tree.column("#0", width=180, minwidth=150, stretch=True)
        for column, heading, width, minwidth in (
                ("status", "Status", 80, 55),
                ("importance", "Importance", 100, 70),
                ("freshness", "Freshness", 70, 55)):
            tree.heading(column, text=heading)
            tree.column(column, width=width, minwidth=minwidth, anchor="w",
                        stretch=False)
        for tag in set(self._HEALTH_STATUS_TAGS.values()):
            tree.tag_configure(
                tag, foreground=theme.status_fg(self.pal, tag.removeprefix("health_").upper()))
        findings_by_item = {}
        for name in HEALTH_GROUP_ORDER:
            members = groups[name]
            if not members:
                continue
            group_item = tree.insert(
                "", "end", text=f"{name} ({len(members)})",
                open=name in HEALTH_GROUPS_EXPANDED)
            for finding in members:
                status_label = finding.status.replace("_", " ").title()
                importance_label = finding.importance.replace("_", " ").title()
                freshness_label = finding.freshness.replace("_", " ").title()
                status_tag = self._HEALTH_STATUS_TAGS.get(
                    finding.status, "health_unknown")
                item = tree.insert(
                    group_item, "end",
                    text=health_rule_display_name(finding.rule),
                    values=(status_label, importance_label, freshness_label),
                    tags=(status_tag,))
                findings_by_item[item] = finding
        return tree, findings_by_item

    def _health_sync_wrap(self, body):
        """Cheaply update wraplength on registered labels when width changes.

        Called from <Configure>; it never destroys or recreates widgets, so a
        resize cannot trigger a full selected-detail rebuild.
        """
        labels = getattr(body, "_wrap_labels", ())
        if not labels:
            return
        avail = body.winfo_width()
        if avail <= 1:
            return
        wrap = max(240, avail - 8)
        if getattr(body, "_wrap_width", None) == wrap:
            return
        for label in labels:
            try:
                if int(str(label.cget("wraplength"))) != wrap:
                    label.configure(wraplength=wrap)
            except (tk.TclError, ValueError):
                continue
        body._wrap_width = wrap

    def _health_scroll_area(self, parent):
        """Return (canvas, body) vertical scroll area inside ``parent``."""
        canvas = tk.Canvas(parent, bg=self.pal["panel"], bd=0,
                           highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical",
                                  command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        body = ttk.Frame(canvas, style="Surface.TFrame")
        window = canvas.create_window((0, 0), window=body, anchor="nw")

        def sync_width(event):
            canvas.itemconfigure(window, width=max(1, event.width))

        def sync_body(event):
            canvas.configure(scrollregion=(0, 0, event.width, event.height))
            self._health_sync_wrap(body)

        canvas.bind("<Configure>", sync_width)
        body.bind("<Configure>", sync_body)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return canvas, body

    def _health_gauge(self, parent, score, status):
        """Draw the Repository Health ring gauge once (no animation loop).

        The Canvas is a 150x150 square so the complete 270-degree ring drawn
        inside the 12..138 bounding box (plus its stroke width) always fits
        inside the viewport.
        """
        pal = self.pal
        canvas = tk.Canvas(parent, width=150, height=150, bg=pal["bg"],
                           bd=0, highlightthickness=0)
        color = pal["neutral"] if score is None \
            else theme.status_fg(pal, status)
        start, full = 135, 270
        box = (12, 12, 138, 138)
        canvas.create_arc(box, start=start, extent=full, style="arc",
                          outline=pal["border"], width=13)
        extent = int(full * score / 100) if score is not None else 0
        if extent:
            canvas.create_arc(box, start=start, extent=extent, style="arc",
                              outline=color, width=13)
        number = "—" if score is None else str(score)
        canvas.create_text(75, 66, text=number, font=("", 28, "bold"),
                           fill=color)
        canvas.create_text(75, 94, text="/ 100", font=("", 9),
                           fill=pal["muted"])
        canvas._health_score = score
        canvas._health_color = color
        canvas._health_number = number
        return canvas

    def _health_card_label(self, body, card, text, *, fg=None, bold=False,
                           muted=False, size=9, wrap=True):
        """Plain-Tk card label (cards use tk.Frame so colours stay theme-safe)."""
        pal = self.pal
        color = pal["text"] if fg is None else fg
        if muted:
            color = pal["muted"]
        font = ("", size, "bold") if bold else ("", size)
        label = tk.Label(card, text=text, bg=pal["panel2"], fg=color,
                         font=font, anchor="w", justify="left")
        if wrap:
            label.configure(wraplength=max(240, body.winfo_width() - 60)
                            if body.winfo_width() > 1 else 320)
            if not hasattr(body, "_wrap_labels"):
                body._wrap_labels = []
            body._wrap_labels.append(label)
        return label

    def _health_build_overview(self, page, result):
        """Build the immediate Overview: cards + healthy/empty state."""
        canvas, body = self._health_scroll_area(page)
        body._wrap_labels = []
        pad = 6

        actionables = [f for f in result.prioritized_findings()
                       if f.status in (health.FAIL, health.WARN, health.UNKNOWN)
                       and f.importance not in (health.DISABLED,
                                                health.INFORMATIONAL)]
        shown = actionables[:5]
        for finding in shown:
            status = finding.status
            fg = theme.status_fg(self.pal, status)
            card = tk.Frame(body, bg=self.pal["panel2"],
                            highlightthickness=1,
                            highlightbackground=self.pal["border"])
            card.pack(fill="x", pady=(0, pad))
            accent = tk.Frame(card, bg=fg, width=4)
            accent.pack(side="left", fill="y")
            inner = tk.Frame(card, bg=self.pal["panel2"])
            inner.pack(side="left", fill="x", expand=True, padx=(8, 6),
                       pady=6)
            top = tk.Frame(inner, bg=self.pal["panel2"])
            top.pack(fill="x")
            tk.Label(top, text=health_rule_display_name(finding.rule),
                     bg=self.pal["panel2"], fg=self.pal["accent2"],
                     font=("", 10, "bold"), anchor="w").pack(side="left")
            tk.Label(top, text=finding.status, bg=self.pal["panel2"],
                     fg=fg, font=("", 9, "bold"), anchor="e").pack(
                         side="right")
            explanation = self._health_card_label(
                body, inner, finding.explanation or "")
            explanation.pack(anchor="w", fill="x", pady=(4, 0))
            if finding.remediation:
                next_label = self._health_card_label(
                    body, inner, "Next:", bold=True, muted=True)
                next_label.pack(anchor="w", pady=(6, 0))
                remediation = self._health_card_label(
                    body, inner, finding.remediation)
                remediation.pack(anchor="w", fill="x")

        if shown:
            remaining = len(actionables) - len(shown)
            if remaining:
                ttk.Label(
                    body, text=f"+ {remaining} more findings — see All checks",
                    style="Muted.TLabel").pack(anchor="w", pady=(0, 4))
        else:
            ttk.Label(
                body, text="No required action found in the checks that ran.",
                font=("", 11, "bold"), foreground=self.pal["ok"]).pack(
                    anchor="w", pady=(0, pad))

        informational = health_dashboard_counts(result)["informational"]
        if informational:
            ttk.Label(
                body,
                text=f"Informational observations: {informational}",
                style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        if not (shown or informational):
            ttk.Label(
                body, text="No other observations in the checks that ran.",
                style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        return canvas

    def _health_build_all_checks(self, page, result):
        """Build the All-checks surface once and wire its handlers."""
        state = self._health_dialog_state
        split = ttk.Panedwindow(page, orient="horizontal")
        split.pack(fill="both", expand=True)
        navigator_wrap = ttk.Frame(split)
        tree, findings_by_item = self._build_health_navigator(
            navigator_wrap, result)
        navigator_wrap.grid_rowconfigure(0, weight=1)
        navigator_wrap.grid_columnconfigure(0, weight=1)
        navigator_sb = ttk.Scrollbar(navigator_wrap, orient="vertical",
                                     command=tree.yview)
        navigator_hb = ttk.Scrollbar(navigator_wrap, orient="horizontal",
                                     command=tree.xview)
        tree.configure(yscrollcommand=navigator_sb.set,
                       xscrollcommand=navigator_hb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        navigator_sb.grid(row=0, column=1, sticky="ns")
        navigator_hb.grid(row=1, column=0, columnspan=2, sticky="ew")
        split.add(navigator_wrap, weight=3)

        detail_wrap = ttk.Labelframe(split, text=" Selected check ",
                                     padding=6)
        detail_canvas, detail_body = self._health_scroll_area(detail_wrap)
        split.add(detail_wrap, weight=2)

        refs = state["all_checks"] = {
            "tree": tree, "findings_by_item": findings_by_item,
            "detail_body": detail_body, "detail_canvas": detail_canvas,
        }

        def select(_event=None):
            selection = tree.selection()
            finding = findings_by_item.get(selection[0]) \
                if selection else None
            if finding is None:
                return
            if getattr(detail_body, "_rendered_finding", None) is finding:
                return
            self._render_health_finding_detail(detail_body, finding)
            detail_canvas.yview_moveto(0)

        tree.bind("<<TreeviewSelect>>", select)
        tree.focus_set()

        # Default selection follows result.prioritized_findings() with the
        # Prefer: material FAIL/UNKNOWN, material WARN, informational
        # non-PASS, remaining enabled, disabled only as a last resort.
        def priority(finding):
            if finding.importance == health.DISABLED:
                return 4
            if finding.importance == health.INFORMATIONAL:
                return 2 if finding.status not in (health.PASS,
                                                   health.NOT_APPLICABLE) else 3
            if finding.status in (health.FAIL, health.UNKNOWN):
                return 0
            if finding.status == health.WARN:
                return 1
            return 3

        by_finding = {id(finding): item
                      for item, finding in findings_by_item.items()}
        best, best_key = None, None
        for finding in result.prioritized_findings():
            item = by_finding.get(id(finding))
            if item is None:
                continue
            key = priority(finding)
            if best is None or key < best_key:
                best, best_key = item, key
        if best is not None:
            tree.selection_set(best)
        select()

    def _render_health_finding_detail(self, container, finding):
        """Rebuild the selected-finding detail only when the finding changes."""
        for widget in container.winfo_children():
            widget.destroy()
        container._wrap_labels = []
        container._rendered_finding = finding
        container._wrap_width = None
        pad = 8
        avail = container.winfo_width()
        wrap = max(240, avail - 8) if avail > 1 else 320

        def body_label(text, **kwargs):
            label = ttk.Label(container, text=text, wraplength=wrap,
                              justify="left", **kwargs)
            container._wrap_labels.append(label)
            return label

        rows = (
            ("Explanation", finding.explanation),
            ("Next step", finding.remediation),
        )
        for label, text in rows:
            if not text:
                continue
            if label == "Next step":
                ttk.Label(container, text=label,
                          style="SurfaceMuted.TLabel").pack(anchor="w")
                body_label(text).pack(anchor="w", fill="x")
                continue
            body_label(text).pack(anchor="w", fill="x", pady=(0, pad))

        evidence = finding.evidence or ()
        if evidence:
            ttk.Label(container, text="Evidence",
                      style="SurfaceMuted.TLabel",
                      font=("", 9, "bold")).pack(anchor="w", pady=(0, 2))
            evidence_text = "\n".join(
                f"• {item.observation}\n"
                f"  source: {item.source or 'unknown'} · target: "
                f"{item.target or '-'} · freshness: "
                f"{item.freshness.replace('_', ' ').title()}"
                + (f" · at: {item.timestamp}" if item.timestamp else "")
                for item in evidence)
            body_label(evidence_text, style="SurfaceMuted.TLabel").pack(
                anchor="w", fill="x", pady=(0, pad))

        metadata = (
            ("Rule", finding.rule),
            ("Status", finding.status),
            ("Severity", finding.severity),
            ("Importance", finding.importance),
            ("Freshness", finding.freshness),
            ("Target", finding.target),
        )
        ttk.Label(container, text="Technical", style="SurfaceMuted.TLabel",
                  font=("", 9, "bold")).pack(anchor="w", pady=(0, 2))
        for label, value in metadata:
            row = ttk.Frame(container, style="Surface.TFrame")
            row.pack(fill="x")
            ttk.Label(row, text=label, style="SurfaceMuted.TLabel",
                      width=11, anchor="w").pack(side="left")
            ttk.Label(row, text=str(value or "-"), anchor="w").pack(
                side="left", fill="x", expand=True)

    def _open_health_details(self):
        """Open the Repository Health dashboard (Overview first).

        The Overview (score, counters, actionable cards) is built immediately;
        the heavier All-checks Treeview surface is constructed lazily the first
        time the tab is opened and then reused.
        """
        result = self._health_result
        if result is None:
            return
        self._health_dialog_state = {"all_checks": None}
        dlg = tk.Toplevel(self)
        self._prepare_dialog(dlg, "Repository Health details", "860x680")
        pal = self.pal
        outer = ttk.Frame(dlg, padding=12)
        outer.pack(fill="both", expand=True)

        score = repository_health_score(result)
        band_text = repository_health_band_label(result)
        counts = health_dashboard_counts(result)

        title_row = ttk.Frame(outer)
        title_row.pack(fill="x")
        ttk.Label(title_row, text="Repository Health",
                  font=("", 14, "bold"),
                  foreground=pal["accent2"]).pack(side="left")
        ttk.Label(title_row, text=f"Evaluated {result.evaluated_at}",
                  style="Muted.TLabel").pack(side="right")

        score_row = ttk.Frame(outer)
        score_row.pack(fill="x", pady=(8, 0))
        gauge = self._health_gauge(score_row, score, result.status)
        gauge.pack(side="left")
        right = ttk.Frame(score_row)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))
        band_style = theme.semantic_style(result.status) \
            if score is not None else "Muted.TLabel"
        ttk.Label(right, text=band_text, style=band_style,
                  font=("", 13, "bold")).pack(anchor="w", pady=(10, 0))
        ttk.Label(right, text="Repository Health score from the checks that ran",
                  style="Muted.TLabel", wraplength=360,
                  justify="left").pack(anchor="w", pady=(6, 0))

        chip_row = ttk.Frame(outer)
        chip_row.pack(fill="x", pady=(8, 0))
        for label, key, status in (
                ("Passed", "passed", health.PASS),
                ("Warnings", "warnings", health.WARN),
                ("Problems", "problems", health.FAIL),
                ("Unknown", "unknown", health.UNKNOWN)):
            count = counts[key]
            style = theme.semantic_style(status) if count else "Muted.TLabel"
            ttk.Label(chip_row, text=f"{label} {count}",
                      style=style).pack(side="left", padx=(0, 8))
        ttk.Label(chip_row, text=f"Stale {counts['stale']}",
                  style="Muted.TLabel").pack(side="left")

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True, pady=(8, 0))
        overview_page = ttk.Frame(notebook)
        checks_page = ttk.Frame(notebook)
        notebook.add(overview_page, text="Overview")
        notebook.add(checks_page, text="All checks")
        self._health_build_overview(overview_page, result)

        def on_tab(_event=None):
            if notebook.index("current") == 1 \
                    and self._health_dialog_state["all_checks"] is None:
                self._health_build_all_checks(checks_page, result)

        notebook.bind("<<NotebookTabChanged>>", on_tab)

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(6, 0))
        ttk.Button(actions, text="Close", command=dlg.destroy,
                   style="Primary.TButton").pack(side="right")
        return dlg

    def _show_provider(self, proj):
        """Refresh read-only provider evidence for the selected repository.

        The network observation runs on a worker thread and returns through
        the existing _scan_queue, so a slow or missing network never blocks
        the Tk main thread (a GitHub observation can block for the socket
        timeout plus unbounded DNS resolution). Results are discarded unless
        the Project, path, remote, and observation generation still match.
        """
        gen = getattr(self, "_provider_gen", 0) + 1
        self._provider_gen = gen
        project_id = projects.project_id(proj)
        path = proj.get("path") or proj.get("folder_path") or ""
        remote = proj.get("remote")
        target = {"project_id": project_id, "path": path, "remote": remote,
                  "_record": proj}
        correspondence = providers.provider_correspondence(remote)
        if not remote or correspondence is None:
            self._provider_observation = None
            self.provider_status.configure(
                text=("Local correspondence: Unavailable · no usable "
                      "remote URL\nOnline details: Not run\n"
                      "Ownership / access: Not inferred"),
                style=theme.semantic_style("UNKNOWN"))
            return
        if correspondence["provider_id"] != "github":
            self._provider_observation = None
            self.provider_status.configure(
                text="\n".join(self._provider_local_lines(
                    proj, correspondence, online="Not supported for this host")),
                style=theme.semantic_style("AVAILABLE"))
            return
        self._provider_observation = None
        self.provider_status.configure(
            text="\n".join(self._provider_local_lines(
                proj, correspondence, online="Checking GitHub…")),
            style=theme.semantic_style("IN_PROGRESS"))
        q = self._scan_queue

        def work():
            try:
                observation = self.provider.observe(remote)
            except Exception as exc:
                log.exception("provider observation failed")
                observation = providers.ProviderObservation(
                    "github", providers.UNKNOWN,
                    providers.UNKNOWN_FRESHNESS,
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                    evidence=("provider observation failed unexpectedly",),
                    error=str(exc))
            q.put(("provider", target, observation, gen))

        threading.Thread(target=work, daemon=True).start()

    def _provider_local_lines(self, proj, correspondence, *, online):
        """Human summary first; multi-remote evidence remains explicit."""
        label = correspondence["provider_id"].replace("_", " ").title()
        source = proj.get("remote_name") or "selected remote"
        lines = [
            (f"Detected locally from {source}: {label} "
             f"({correspondence['host']})"),
            (f"Repository: {correspondence['namespace']}/"
             f"{correspondence['repository']}"),
        ]
        remotes = list(proj.get("remotes") or ())
        selected = proj.get("remote")
        if selected and selected not in remotes:
            remotes.append(selected)
        matches = providers.provider_correspondences(remotes)
        others = sorted({
            (item["provider_id"].replace("_", " ").title(), item["host"])
            for item in matches
            if item["normalized_remote"].casefold()
            != correspondence["normalized_remote"].casefold()
        })
        if others:
            lines.append("Other detected hosts: " + ", ".join(
                f"{provider} ({host})" for provider, host in others))
        lines.extend((f"Online details: {online}",
                      "Ownership / access: Not inferred"))
        return lines

    def _apply_provider(self, target, observation, gen):
        """Render a queued observation only if its exact target is current.

        Called from the Tk main thread via _drain_scan_queue. A result whose
        generation, Project ID, path, or remote no longer matches is discarded.
        """
        if gen != getattr(self, "_provider_gen", 0):
            return  # a newer observation superseded this result
        if self._current is None:
            return
        structured = isinstance(target, dict)
        path = target.get("path") if structured else target
        target_id = target.get("project_id") if structured else None
        target_remote = target.get("remote") if structured else None
        target_record = target.get("_record") if structured else None
        current_path = (self._current.get("path")
                        or self._current.get("folder_path") or "")
        if current_path != path:
            return
        if (target_id is not None
                and projects.project_id(self._current) != target_id):
            return
        if target_record is not None and self._current is not target_record:
            return
        if structured and self._current.get("remote") != target_remote:
            return
        self._provider_observation = observation
        correspondence = providers.provider_correspondence(
            self._current.get("remote"))
        if correspondence is None:
            return
        if observation.status == providers.AVAILABLE:
            disagreements = providers.compare_local_provider(
                self._current, observation)
            suffix = (f" · disagreement: {', '.join(disagreements)}"
                      if disagreements else "")
            online = (
                f"Observed on GitHub · {observation.visibility.title()} · "
                f"default branch {observation.default_branch or 'unknown'} · "
                f"freshness {observation.freshness.title()}{suffix}")
            text = "\n".join(self._provider_local_lines(
                self._current, correspondence, online=online))
        elif observation.status == providers.NOT_RUN and observation.correspondence:
            text = "\n".join(self._provider_local_lines(
                self._current, correspondence,
                online="Not supported for this host"))
        else:
            online = {
                providers.AUTHENTICATION_REQUIRED: "Unavailable · authentication required",
                providers.NETWORK_FAILURE: "Unavailable · network request failed",
                providers.TIMEOUT: "Unavailable · request timed out",
                providers.NOT_FOUND: "Unavailable · repository not found",
                providers.UNKNOWN: "Unknown · response could not be interpreted",
            }.get(observation.status, "Unavailable")
            text = "\n".join(self._provider_local_lines(
                self._current, correspondence, online=online))
        self.provider_status.configure(
            text=text,
            style=theme.semantic_style(providers.provider_presentation_status(
                correspondence, observation)))

    # -------------------------------------------------------- context menu
    def _build_context_menu(self):
        p = self.pal
        m = tk.Menu(self, tearoff=0, font=("", 9))
        theme.style_tk_widget(m, p, "menu")
        selected = self._selected_project()
        current = selected or getattr(self, "_current", None)
        layout = context_menu_layout(
            current.get("status") if current else None,
            ignored=projects.is_ignored(current) if current else False)
        for item in layout:
            if item == "-sep-":
                m.add_separator()
            elif item[0] == "cascade":
                status_menu = tk.Menu(m, tearoff=0)
                theme.style_tk_widget(status_menu, p, "menu")
                for s in STATUSES:
                    status_menu.add_command(
                        label=s.capitalize(),
                        command=lambda s=s: self._set_status(s))
                m.add_cascade(label=item[1], menu=status_menu)
            else:
                _, label, method_name = item
                m.add_command(label=label, command=getattr(self, method_name))
        self._ctx_menu = m

    def _show_context_menu(self, event):
        tree = event.widget
        row = tree.identify_row(event.y)
        if not row:
            return
        self._set_active("now" if tree is self.now_tree else "main")
        if tuple(tree.selection()) != (row,):
            tree.selection_set(row)
            tree.focus(row)
        tree.focus_set()
        old_menu = getattr(self, "_ctx_menu", None)
        if old_menu is not None:
            try:
                old_menu.destroy()
            except tk.TclError:
                pass
        self._build_context_menu()
        try:
            self._ctx_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._ctx_menu.grab_release()
        return "break"

    def _set_status(self, status):
        if not self._current:
            return
        self.d_status.set(status)
        self._save_detail()

    def _toggle_pinned(self):
        if not self._current:
            return
        self.d_pinned.set(not bool(self.d_pinned.get()))
        self._save_detail()

    def _remove_from_repomanager(self):
        """Confirm and persist an explicit Project ignore by stable identity."""
        selected = self._selected_project()
        target_id = projects.project_id(selected) if selected else None
        if selected is None or target_id is None or projects.is_ignored(selected):
            return None

        dlg = tk.Toplevel(self)
        self._prepare_dialog(dlg, "Remove from RepoManager", "520x250")
        body = ttk.Frame(dlg, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body, text="Remove this project from RepoManager?",
            font=("", 13, "bold"), foreground=self.pal["accent2"],
        ).pack(anchor="w", pady=(0, 12))
        ttk.Label(
            body,
            text=("The repository and its files will not be changed.\n\n"
                  "RepoManager will ignore this project during future scans.\n"
                  "You can restore it later in Settings."),
            wraplength=470, justify="left",
        ).pack(anchor="w", fill="x")

        def remove():
            current_target = next(
                (project for project in self.projects
                 if projects.project_id(project) == target_id),
                None,
            )
            if current_target is None or projects.is_ignored(current_target):
                messagebox.showerror(
                    "RepoManager",
                    "The selected Project is no longer available for removal.",
                    parent=dlg,
                )
                return
            current_detail = self.__dict__.get("_current")
            detail_matches = (
                current_detail is not None
                and projects.project_id(current_detail) == target_id
            )
            if (detail_matches
                    and getattr(self, "_note_user_edited_gen", None)
                    is not None):
                try:
                    self._flush_note_save()
                except OSError as exc:
                    log.exception("Project note save failed before ignore")
                    messagebox.showerror(
                        "RepoManager",
                        f"Project was not removed because its note could not "
                        f"be saved:\n{exc}",
                        parent=dlg,
                    )
                    return
            try:
                ignored = persist_project_ignore(
                    self.projects, target_id,
                    lambda records: self._persist_projects(records),
                )
            except Exception as exc:
                log.exception("Project ignore persistence failed")
                messagebox.showerror(
                    "RepoManager",
                    f"Project was not removed from RepoManager:\n{exc}",
                    parent=dlg,
                )
                self._status.set(
                    "Project was not removed · registry save failed",
                    important=True,
                )
                return
            if ignored is None:
                messagebox.showerror(
                    "RepoManager",
                    "The selected Project is no longer available for removal.",
                    parent=dlg,
                )
                return

            self._cancel_project_save()
            dlg.destroy()
            if detail_matches:
                # The current note was flushed under the unchanged Project ID.
                # Prevent _clear_detail from issuing a redundant second write.
                self._note_target = None
                self._clear_detail()
            self._populate_trees()
            self._status.set(
                "Project removed from RepoManager · restore later in Settings",
                important=True,
            )

        buttons = ttk.Frame(body)
        buttons.pack(side="bottom", fill="x", pady=(18, 0))
        ttk.Button(buttons, text="Remove", command=remove,
                   style="Primary.TButton").pack(side="right")
        ttk.Button(buttons, text="Cancel", command=dlg.destroy).pack(
            side="right", padx=(0, 6))
        dlg.bind("<Return>", lambda _e: remove())
        return dlg

    def _copy_path(self):
        p = self._selected_project()
        if p:
            path = projects.project_folder(p) or ""
            self.clipboard_clear()
            self.clipboard_append(path)
            self._status.set(f"copied: {path}")

    # ---------------------------------------------------------- git actions
    def _track_git_worker(self, done):
        """Keep one Git worker owned by the UI until its result is delivered."""
        workers = self.__dict__.setdefault("_git_workers", set())
        token = object()
        workers.add(token)

        def complete(outcome, out):
            workers.discard(token)
            try:
                done(outcome, out)
            finally:
                self._resume_close_if_ready()

        # The token remains owned until the queued terminal callback runs.
        # This also lets thread-start failures report through the same path.
        complete._git_worker_token = token
        return complete

    def _resume_close_if_ready(self):
        """Resume a deferred close after all tracked Git results are handled."""
        if (not self.__dict__.get("_close_after_git", False)
                or self.__dict__.get("_git_workers", set())
                or self.__dict__.get("_closing", False)
                or self.__dict__.get("_close_resume_job")):
            return
        self._close_after_git = False
        schedule = getattr(self, "_schedule_after_idle", None)
        if schedule is None:
            self._on_close()
            return
        self._close_resume_job = schedule(self._resume_close_callback)
        if (self._close_resume_job is None
                and not self.__dict__.get("_closing", False)):
            self._on_close()

    def _resume_close_callback(self):
        self._close_resume_job = None
        if not self.__dict__.get("_closing", False):
            self._on_close()

    def _git_async(self, proj, done, *args, mutation=False):
        """Run a Git command after target revalidation; report semantic outcome."""
        done = self._track_git_worker(done)
        snapshot = (proj if isinstance(proj, dict) and
                    set(proj) >= {"project_id", "path", "_record"}
                    else git_target_snapshot(proj))
        path = snapshot["path"]
        guard = self.__dict__.setdefault(
            "_git_mutation_guard", GitMutationGuard())
        mutation_key = snapshot.get("repository_marker")
        if mutation and (mutation_key is None
                         or not guard.acquire(mutation_key)):
            self._scan_queue.put((
                "done", (done, GIT_CANCELLED,
                         "Another Git mutation is active or the repository identity is unavailable")))
            return

        def work():
            try:
                authorized = (git_mutation_target_is_authorized(
                    self.projects, snapshot) if mutation else
                    git_target_is_authorized(self.projects, snapshot))
                if not authorized:
                    self._scan_queue.put((
                        "done", (done, GIT_CANCELLED,
                                 "Project association or repository identity changed; no Git write started")))
                    return
                r = subprocess.run(
                    ["git", "-C", path, *args],
                    capture_output=True, text=True, timeout=120,
                    encoding="utf-8", errors="replace",
                    creationflags=CREATE_NO_WINDOW,
                )
                outcome = GIT_SUCCESS if r.returncode == 0 else GIT_FAILED
                out = (r.stdout or r.stderr or "").strip()
            except subprocess.TimeoutExpired as e:
                outcome = GIT_OUTCOME_UNKNOWN if mutation else GIT_FAILED
                out = str(e)
            except OSError as e:
                outcome = GIT_OUTCOME_UNKNOWN if mutation else GIT_FAILED
                out = str(e)
            except Exception:
                log.exception("git action failed unexpectedly")
                outcome, out = GIT_FAILED, "internal error — see repo_manager.log"
            finally:
                if mutation:
                    guard.release(mutation_key)
            self._scan_queue.put(("done", (done, outcome, out)))

        try:
            threading.Thread(target=work, daemon=True).start()
        except (OSError, RuntimeError) as exc:
            if mutation:
                guard.release(mutation_key)
            self._scan_queue.put(("done", (done, GIT_FAILED, str(exc))))

    def _git_report(self, proj, outcome, out, verb):
        name = projects.project_display_name(proj) if proj else "?"
        if outcome == GIT_SUCCESS:
            message = f"{name}: {verb} OK"
        elif outcome == GIT_OUTCOME_UNKNOWN:
            message = f"{name}: {verb} OUTCOME UNKNOWN — do not retry automatically"
        elif outcome == GIT_CANCELLED:
            message = f"{name}: {verb} CANCELLED"
        elif outcome == GIT_PARTIAL:
            message = f"{name}: {verb} PARTIAL — {out[:120]}"
        else:
            message = f"{name}: {verb} FAILED — {out[:120]}"
        self._status.set(message, important=outcome != GIT_SUCCESS)
        if outcome != GIT_SUCCESS:
            title = f"git {verb}"
            messagebox.showwarning(title, out or outcome) if outcome == GIT_PARTIAL else messagebox.showerror(title, out or outcome)
        if (verb in ("commit & push", "pull", "push")
                and outcome in (GIT_SUCCESS, GIT_PARTIAL,
                                GIT_OUTCOME_UNKNOWN)
                and not self.__dict__.get("_close_after_git", False)):
            self._refresh_metadata_async()

    def git_commit_push(self):
        p = self._selected_project()
        if not p or not projects.is_repository_backed(p):
            self._status.set("This action requires an associated Git repository",
                             important=True)
            return
        self._flush_note_save()
        target = git_target_snapshot(p)

        def preview_done(outcome, out):
            if outcome != GIT_SUCCESS:
                self._git_report(p, outcome, out, "status")
                return
            target_project = next((project for project in self.projects
                                  if git_target_is_authorized([project], target)),
                                 None)
            if target_project is None:
                self._git_report(p, GIT_CANCELLED,
                                 "Project association changed while the preview was loading",
                                 "status")
                return
            self._show_commit_preview(target, out.splitlines())

        self._status.set(
            f"{projects.project_display_name(p)}: collecting changes…")
        self._git_async(target, preview_done, "status", "--porcelain")

    def _show_commit_preview(self, p, files):
        if isinstance(p, dict) and set(p) >= {"project_id", "path", "_record"}:
            p = next((project for project in self.projects
                      if git_target_is_authorized([project], p)), None)
        if p is None:
            return
        if not files:
            if messagebox.askyesno(
                    "Commit & Push",
                    f"'{projects.project_display_name(p)}' has no changes "
                    "to commit.\n"
                    "Push existing commits anyway?"):
                self._do_push(p)
            return
        dlg = tk.Toplevel(self)
        self._prepare_dialog(
            dlg, f"Commit & Push — {projects.project_display_name(p)}",
            "580x480")

        ttk.Label(dlg, text=(
            f"Snapshot: {len(files)} changed file(s) in:\n{p['path']}\n"
            "Commit & Push stages all current changes, including changes "
            "made after this preview."), wraplength=550).pack(
                anchor="w", padx=10, pady=(10, 4))
        lst = tk.Listbox(dlg, height=14)
        theme.style_tk_widget(lst, self.pal, "list")
        lst.pack(fill="both", expand=True, padx=10)
        for f in files:
            lst.insert("end", f)

        ttk.Label(dlg, text="Commit message:").pack(anchor="w", padx=10,
                                                    pady=(6, 0))
        msg_entry = ttk.Entry(dlg)
        msg_entry.insert(0, "Update")
        msg_entry.pack(fill="x", padx=10, pady=(0, 8))

        def confirm():
            msg = msg_entry.get().strip() or "Update"
            if not git_target_is_current(self.projects, p):
                messagebox.showerror(
                    "Commit & Push",
                    "The Project association changed after this preview. "
                    "No Git write was started.", parent=dlg)
                return
            dlg.destroy()
            self._do_commit_push(p, msg)

        btns = ttk.Frame(dlg)
        btns.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(btns, text="Commit & Push", command=confirm,
                   style="Primary.TButton").pack(side="right")
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(
            side="right", padx=(0, 6))
        msg_entry.bind("<Return>", lambda e: confirm())
        msg_entry.focus_set()

    def _do_commit_push(self, p, msg):
        if not git_target_is_current(self.projects, p):
            self._git_report(
                p, GIT_CANCELLED, "Project association changed; no Git write started",
                "commit & push")
            return

        target = git_target_snapshot(p)

        def done(outcome, out):
            current = next((project for project in self.projects
                            if git_target_is_authorized([project], target)),
                           None)
            if current is None:
                self._status.set(
                    "Commit & Push completed for a target that is no longer "
                    "associated; inspect the repository directly.",
                    important=True)
                return
            self._git_report(current, outcome, out, "commit & push")

        done = self._track_git_worker(done)

        def work():
            guard = self.__dict__.setdefault(
                "_git_mutation_guard", GitMutationGuard())
            mutation_key = target.get("repository_marker")
            if (mutation_key is None or not guard.acquire(mutation_key)):
                self._scan_queue.put(("done", (done, GIT_CANCELLED,
                                                 "Another Git mutation is active or the repository identity is unavailable")))
                return
            path = target["path"]
            try:
                if not git_mutation_target_is_authorized(
                        self.projects, target):
                    self._scan_queue.put((
                        "done", (done, GIT_CANCELLED,
                                 "Project association or repository identity changed; no Git write started")))
                    return
                try:
                    remotes = subprocess.run(
                        ["git", "-C", path, "remote"],
                        capture_output=True, text=True, timeout=120,
                        encoding="utf-8", errors="replace",
                        creationflags=CREATE_NO_WINDOW,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    self._scan_queue.put(("done", (done, GIT_OUTCOME_UNKNOWN, str(exc))))
                    return
                remote_names = remotes.stdout.splitlines() \
                    if remotes.returncode == 0 else []
                remote = select_push_remote(
                    remote_names, p.get("upstream"))
                if remote is None:
                    self._scan_queue.put((
                        "done", (done, GIT_FAILED,
                                 "No unambiguous Git push remote is configured")))
                    return
                remote_url_result = subprocess.run(
                    ["git", "-C", path, "remote", "get-url", "--push",
                     remote], capture_output=True, text=True, timeout=120,
                    encoding="utf-8", errors="replace",
                    creationflags=CREATE_NO_WINDOW)
                push_url = (remote_url_result.stdout or "").strip()
                if remote_url_result.returncode != 0 or not push_url:
                    self._scan_queue.put((
                        "done", (done, GIT_FAILED,
                                 "The selected push remote has no available push URL")))
                    return
                try:
                    r = subprocess.run(
                        ["git", "-C", path, "status", "--porcelain"],
                        capture_output=True, text=True, timeout=120,
                        encoding="utf-8", errors="replace",
                        creationflags=CREATE_NO_WINDOW,
                    )
                    if r.returncode != 0:
                        self._scan_queue.put((
                            "done", (done, GIT_FAILED,
                                     "Working-tree status could not be rechecked")))
                        return
                    porcelain_now = (r.stdout or "").strip()
                except (OSError, subprocess.TimeoutExpired) as exc:
                    self._scan_queue.put((
                        "done", (done, GIT_OUTCOME_UNKNOWN,
                                 f"Working-tree status recheck failed: {exc}")))
                    return
                steps = commit_steps_for(msg, porcelain_now, remote)
                if not steps:
                    self._scan_queue.put((
                        "done",
                        (done, GIT_FAILED,
                         "Nothing to commit \u2014 working tree clean"),
                    ))
                    return
                results = []
                for label, args in steps:
                    try:
                        if label == "push":
                            current_url = subprocess.run(
                                ["git", "-C", path, "remote", "get-url",
                                 "--push", remote], capture_output=True,
                                text=True, timeout=120, encoding="utf-8",
                                errors="replace",
                                creationflags=CREATE_NO_WINDOW)
                            if (current_url.returncode != 0
                                    or (current_url.stdout or "").strip()
                                    != push_url):
                                results.append((
                                    label, 1,
                                    "push remote changed before execution",
                                    None))
                                break
                        r = subprocess.run(
                            ["git", "-C", path, *args],
                            capture_output=True, text=True, timeout=120,
                            encoding="utf-8", errors="replace",
                            creationflags=CREATE_NO_WINDOW,
                        )
                        results.append(
                            (label, r.returncode,
                             r.stdout or r.stderr or "", None))
                        if r.returncode != 0:
                            break  # prerequisite mutation failed; do not continue
                    except subprocess.TimeoutExpired as e:
                        results.append((label, 1, str(e), GIT_OUTCOME_UNKNOWN))
                        break  # later steps cannot safely execute
                    except OSError as e:
                        results.append((label, 1, str(e), GIT_OUTCOME_UNKNOWN))
                        break
                outcome, out = git_step_outcome(results)
            except Exception:
                log.exception("commit & push failed unexpectedly")
                outcome, out = GIT_FAILED, "internal error — see repo_manager.log"
            finally:
                guard.release(mutation_key)
            self._scan_queue.put(("done", (done, outcome, out)))

        self._status.set(
            f"{projects.project_display_name(p)}: committing & pushing…")
        try:
            threading.Thread(target=work, daemon=True).start()
        except (OSError, RuntimeError) as exc:
            # The callback is already tracked; queue the terminal failure so
            # shutdown and the normal UI reporter observe it exactly once.
            self._scan_queue.put(("done", (done, GIT_FAILED, str(exc))))

    def _do_push(self, p):
        target = git_target_snapshot(p)

        def done(outcome, out):
            current = next((project for project in self.projects
                            if git_target_is_authorized([project], target)),
                           None)
            if current is None:
                self._status.set(
                    "Push completed for a target that is no longer associated; "
                    "inspect the repository directly.", important=True)
                return
            self._git_report(current, outcome, out, "push")

        def remotes_done(outcome, out):
            if outcome != GIT_SUCCESS:
                done(outcome, out)
                return
            if not git_target_is_current(self.projects, target):
                done(GIT_CANCELLED, "Project association changed; no push started")
                return
            current = next((project for project in self.projects
                            if git_target_is_authorized([project], target)), None)
            if current is None:
                done(GIT_CANCELLED, "Project association changed; no push started")
                return
            remote = select_push_remote(out.splitlines(), current.get("upstream"))
            if remote is None:
                done(GIT_FAILED, "No unambiguous Git push remote is configured")
                return

            def url_done(url_outcome, url):
                if url_outcome != GIT_SUCCESS or not url.strip():
                    done(GIT_FAILED,
                         "The selected push remote has no available push URL")
                    return
                if not git_target_is_current(self.projects, target):
                    done(GIT_CANCELLED,
                         "Project association changed; no push started")
                    return
                self._git_async(target, done, "push", "-u", remote, "HEAD",
                                mutation=True)

            self._git_async(target, url_done, "remote", "get-url", "--push",
                            remote)

        self._status.set(f"{projects.project_display_name(p)}: pushing…")
        self._git_async(target, remotes_done, "remote")

    def git_pull(self):
        p = self._selected_project()
        if not p or not projects.is_repository_backed(p):
            self._status.set("This action requires an associated Git repository",
                             important=True)
            return

        target = git_target_snapshot(p)

        def done(outcome, out):
            current = next((project for project in self.projects
                            if git_target_is_authorized([project], target)),
                           None)
            if current is None:
                self._status.set(
                    "Pull completed for a target that is no longer associated; "
                    "inspect the repository directly.", important=True)
                return
            self._git_report(current, outcome, out, "pull")

        def upstream_done(outcome, out):
            if outcome != GIT_SUCCESS:
                done(outcome, out)
                return
            parsed = parse_upstream(out)
            if parsed is None:
                done(GIT_FAILED, "No unambiguous upstream is configured")
                return
            if not git_target_is_current(self.projects, target):
                done(GIT_CANCELLED,
                     "Project association changed; no pull started")
                return
            remote, branch = parsed
            self._git_async(target, done, "pull", "--ff-only", remote, branch,
                            mutation=True)

        self._status.set(f"{projects.project_display_name(p)}: pulling…")
        self._git_async(target, upstream_done, "rev-parse", "--abbrev-ref",
                        "@{upstream}")

    def _web_url(self, proj):
        """Normalized HTTPS URL for a recognized stored remote, or ``None``."""
        return safe_remote_web_url(proj.get("remote"))

    def open_remote(self):
        p = self._selected_project()
        if not p:
            return
        if not p.get("remote"):
            messagebox.showinfo("RepoManager",
                                f"'{projects.project_display_name(p)}' has no "
                                "remote configured.")
            return
        url = self._web_url(p)
        if url is None:
            messagebox.showerror(
                "RepoManager", "The configured remote is not a safe browser URL.",
                parent=self)
            return
        if not webbrowser.open(url):
            self._status.set("Browser could not be opened", important=True)

    def _copy_remote_url(self):
        p = self._selected_project()
        if not p:
            return
        if not p.get("remote"):
            self._status.set(
                f"{projects.project_display_name(p)}: no remote",
                important=True)
            return
        url = self._web_url(p)
        if url is None:
            self._status.set("Remote URL is unavailable or unsafe",
                             important=True)
            return
        self.clipboard_clear()
        self.clipboard_append(url)
        self._status.set(f"copied: {url}")

    # ------------------------------------------------------------- scanning
    def start_scan(self):
        if self.__dict__.get("_closing", False) or self._scanning:
            return
        if self._registry_blocked():
            self._retry_registry()
            return
        self._flush_note_save()
        self._flush_project_save()
        self._scanning = True
        self.scan_btn.state(["disabled"])
        self.scan_btn.configure(text="Scanning…")
        self._status.set("Scanning configured folders")
        self._populate_trees()

        s = dict(self.settings)
        self._scan_gen += 1
        gen = self._scan_gen  # snapshot generation for move-race guard
        snapshot = [dict(p) for p in self.projects]

        def work():
            scan_result = scanner.find_repo_dirs(
                list(s["roots"]), int(s.get("depth", 4)),
                list(s.get("skip_dirs", [])))
            found = list(scan_result)
            root_problems = list(getattr(scan_result, "problems", []))
            merged, problems = scanner.merge_scan(
                snapshot, found,
                move_suppressions=s.get("move_suppressions") or [],
                failed_roots=root_problems)
            self._scan_queue.put((
                "result", merged, list(root_problems) + list(problems), gen))

        threading.Thread(
            target=guarded_worker(
                self._scan_queue, work,
                error_payload=lambda message: {
                    "message": message, "operation": "scan",
                    "generation": gen,
                }),
            daemon=True).start()

    def _refresh_metadata_async(self):
        """Re-collect git metadata without a full disk walk.

        The worker captures logical Project IDs and exact paths. Results are
        applied only to the refresh generation and live association that
        authorized them; reassociation, deletion, or a newer refresh wins.
        """
        if self._registry_blocked() or self._scanning:
            return
        self._scanning = True
        metadata_gen = self.__dict__.get("_metadata_gen", 0) + 1
        self.__dict__["_metadata_gen"] = metadata_gen
        snapshot = [
            (projects.project_id(p), p.get("path"))
            for p in list(self.projects)
            if projects.is_repository_backed(p)
            and isinstance(p.get("path"), str)
        ]
        scan_btn = self.__dict__.get("scan_btn")
        if scan_btn is not None:
            scan_btn.state(["disabled"])
            scan_btn.configure(text="Refreshing…")
        status_line = self.__dict__.get("_status")
        if status_line is not None:
            status_line.set("Refreshing repository metadata")

        def work():
            def collect(item):
                project_id, path = item
                try:
                    metadata = scanner.collect_metadata(path)
                    return project_id, path, metadata, None
                except Exception as exc:
                    log.exception("metadata collection failed for %s", path)
                    return project_id, path, None, exc

            with scanner.ThreadPool(processes=scanner.MAX_WORKERS) as pool:
                results = pool.map(collect, snapshot)
            self._scan_queue.put(("meta", results, metadata_gen))


        threading.Thread(
            target=guarded_worker(
                self._scan_queue, work,
                error_payload=lambda message: {
                    "message": message, "operation": "metadata",
                    "generation": metadata_gen,
                }),
            daemon=True).start()

    def _drain_scan_queue(self):
        if self.__dict__.get("_closing", False):
            # Workers may finish after close; never touch Tk or persist late
            # results once teardown has begun.
            while True:
                try:
                    self._scan_queue.get_nowait()
                except queue.Empty:
                    break
            return
        scan_done = False  # only the tracked scan's own terminal event clears it
        errors = []
        processed = 0
        queue_has_more = False
        try:
            while processed < QUEUE_DRAIN_MAX_EVENTS:
                event = self._scan_queue.get_nowait()
                processed += 1
                if not isinstance(event, (tuple, list)) or len(event) < 2:
                    log.error("discarding malformed background event: %r", event)
                    errors.append("malformed background event")
                    continue
                kind, payload, *rest = event
                if kind in {"result", "meta"} and self._registry_blocked():
                    continue
                if kind == "result":
                    scan_done = True
                    merged, problems = payload, rest[0] if rest else []
                    result_gen = rest[1] if len(rest) > 1 \
                        else self._scan_gen
                    merged = filter_superseded_rows(
                        merged, self._moved_away, result_gen)
                    merged = reconcile_scan_result(merged, self.projects)
                    self._cancel_project_save()
                    self._avail.invalidate()
                    self._persist_projects(merged)
                    self.projects = merged
                    self._problems = [p for p in (problems or [])
                                      if not p.get("kind") == "move"]
                    self._move_suggestions = [p for p in (problems or [])
                                              if p.get("kind") == "move"]
                    if self._current:
                        current_row_id = project_row_id(self._current)
                        self._current = next(
                            (p for p in self.projects
                             if project_row_id(p) == current_row_id),
                            None)
                        if self._current is None:
                            self._clear_detail()
                        else:
                            self._refresh_current_detail()
                elif kind == "meta":
                    results = payload
                    result_gen = rest[0] if rest else None
                    if result_gen is None:
                        log.warning("discarding unvalidated metadata queue event")
                        continue
                    if result_gen != getattr(self, "_metadata_gen", 0):
                        # A superseded worker is not the current operation's
                        # terminal event. Keep scanning/refreshing active
                        # until the newest generation reports completion.
                        continue
                    scan_done = True
                    applied = []
                    for project_id, path, metadata, error in results:
                        if error is not None or metadata is None:
                            errors.append(f"metadata refresh failed for {path}")
                            continue
                        current = next((p for p in self.projects
                                        if projects.project_id(p) == project_id
                                        and p.get("path") == path), None)
                        if current is not None:
                            applied.append(metadata)
                    if applied:
                        updated = [dict(project) for project in self.projects]
                        apply_metadata_refresh(updated, applied)
                        self._persist_projects(updated)
                        # Persist the prospective state first, then update the
                        # existing objects so open detail/UI references remain
                        # valid without ever getting ahead of durable state.
                        apply_metadata_refresh(self.projects, applied)
                        current = self.__dict__.get("_current")
                        current_path = (current.get("path")
                                        if current is not None else None)
                        if (current is not None
                                and any(item.get("path") == current_path
                                        for item in applied)):
                            self._update_row(current)
                            self._refresh_current_detail()
                elif kind == "detail":
                    target, observation, gen = payload, *rest
                    self._apply_detail_observation(target, observation, gen)
                elif kind == "provider":
                    # Best-effort provider evidence; never a scan terminal
                    # event (observe is fully exception-handled off-thread).
                    target, observation, gen = payload, *rest
                    self._apply_provider(target, observation, gen)
                elif kind == "workspace":
                    workspace_id, inspection, gen = payload
                    self._apply_workspace_inspection(
                        workspace_id, inspection, gen)
                elif kind == "done":
                    done, ok, out = payload
                    done(ok, out)
                elif kind == "error":
                    # Tagged worker failures are terminal only for the live
                    # generation. A superseded worker must not re-enable the
                    # UI or make a newer operation appear complete.
                    operation = payload.get("operation") \
                        if isinstance(payload, dict) else None
                    generation = payload.get("generation") \
                        if isinstance(payload, dict) else None
                    if (operation == "scan"
                            and generation != getattr(self, "_scan_gen", 0)):
                        continue
                    if (operation == "metadata"
                            and generation != getattr(self, "_metadata_gen", 0)):
                        continue
                    scan_done = True
                    errors.append(payload.get("message", str(payload))
                                  if isinstance(payload, dict) else payload)
                elif kind == "workspace_error":
                    if (isinstance(payload, dict)
                            and payload.get("generation")
                            != getattr(self, "_workspace_gen", 0)):
                        continue
                    errors.append(payload.get("message", str(payload))
                                  if isinstance(payload, dict) else payload)
                else:
                    log.error("discarding unknown background event: %r", kind)
                    errors.append(f"unknown background event: {kind}")
        except queue.Empty:
            pass
        except Exception as exc:
            log.exception("background event processing failed")
            errors.append(f"background event processing failed: {exc}")
        try:
            queue_has_more = not self._scan_queue.empty()
        except Exception:
            queue_has_more = False
        if scan_done:
            self._scanning = False
            self.scan_btn.state(["!disabled"])
            self.scan_btn.configure(text="Rescan (F5)")
            self._populate_coalesced()
        if errors:
            msg = coalesce_worker_errors(errors)
            status_line = self.__dict__.get("_status")
            if status_line is not None:
                status_line.set("background operation failed — see log", important=True)
            if self.__dict__.get("_closing", False):
                return
            messagebox.showerror("RepoManager", msg)
        self._schedule_after(0 if queue_has_more else REFRESH_MS,
                             self._drain_scan_queue)

    # ---------------------------------------------------------- help/dialogs

    def _apply_dialog_icon(self, dlg):
        icon = resolve_icon_path()
        if icon is None:
            return
        try:
            dlg.iconbitmap(str(icon))
        except tk.TclError:
            pass

    def _prepare_dialog(self, dlg, title, geometry, *, modal=True,
                        on_close=None):
        """Apply shared theme, placement and keyboard behavior to a Toplevel."""
        close = on_close or dlg.destroy
        dlg.title(title)
        dlg.transient(self)
        dlg.configure(bg=self.pal["bg"])
        self._apply_dialog_icon(dlg)
        width, height = (int(part) for part in geometry.split("x", 1))
        self.update_idletasks()
        x = self.winfo_rootx() + max(16, (self.winfo_width() - width) // 2)
        y = self.winfo_rooty() + max(16, (self.winfo_height() - height) // 2)
        x = max(0, min(x, dlg.winfo_screenwidth() - width))
        y = max(0, min(y, dlg.winfo_screenheight() - height))
        dlg.geometry(f"{width}x{height}+{x}+{y}")
        dlg.protocol("WM_DELETE_WINDOW", close)
        dlg.bind("<Escape>", lambda _e: close())
        if modal:
            dlg.grab_set()

    def _open_text_dialog(self, title, content, *, geometry="720x560"):
        dlg = tk.Toplevel(self)
        self._prepare_dialog(dlg, title, geometry)
        body = ttk.Frame(dlg, padding=12)
        body.pack(fill="both", expand=True)
        text = tk.Text(body, wrap="word", padx=10, pady=10,
                       font=("TkDefaultFont", 10), takefocus=True)
        theme.style_tk_widget(text, self.pal, "text")
        scrollbar = ttk.Scrollbar(body, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        text.insert("1.0", content)
        text.configure(state="disabled")
        ttk.Button(dlg, text="Close", command=dlg.destroy,
                   style="Primary.TButton").pack(anchor="e", padx=12,
                                                  pady=(0, 12))
        text.focus_set()
        return dlg

    def open_help(self, topic="guide"):
        """Open layered product guidance without occupying the main layout."""
        existing = getattr(self, "_help_dialog", None)
        keys = list(HELP_TOPICS)
        if existing is not None and existing.winfo_exists():
            existing.notebook.select(keys.index(topic) if topic in keys else 0)
            existing.lift()
            existing.focus_force()
            return existing

        dlg = tk.Toplevel(self)

        def close():
            self._help_dialog = None
            dlg.destroy()

        self._prepare_dialog(
            dlg, "RepoManager Help & Guide", "760x560", modal=False,
            on_close=close)
        self._help_dialog = dlg
        ttk.Label(dlg, text="RepoManager Guide", font=("", 15, "bold"),
                  foreground=self.pal["accent2"]).pack(
                      anchor="w", padx=16, pady=(16, 2))
        ttk.Label(
            dlg, text="Concepts and workflows for RepoManager.",
            style="Muted.TLabel").pack(anchor="w", padx=16, pady=(0, 12))
        notebook = ttk.Notebook(dlg)
        notebook.pack(fill="both", expand=True, padx=16)
        dlg.notebook = notebook
        for key, (heading, copy) in HELP_TOPICS.items():
            page = ttk.Frame(notebook, padding=18)
            notebook.add(page, text=heading)
            ttk.Label(page, text=heading, font=("", 13, "bold"),
                      foreground=self.pal["accent2"]).pack(anchor="w")
            ttk.Label(page, text=copy, wraplength=660, justify="left").pack(
                anchor="w", fill="x", pady=(12, 0))
        notebook.select(keys.index(topic) if topic in keys else 0)
        ttk.Button(dlg, text="Close", command=close,
                   style="Primary.TButton").pack(anchor="e", padx=16,
                                                  pady=14)
        notebook.focus_set()
        return dlg

    # ------------------------------------------------------------- settings

    def open_settings(self):
        dlg = tk.Toplevel(self)
        self._prepare_dialog(dlg, "Settings", "760x580")
        ttk.Label(dlg, text="Settings", font=("", 15, "bold"),
                  foreground=self.pal["accent2"]).pack(
                      anchor="w", padx=16, pady=(16, 2))
        ttk.Label(dlg, text="Configure scanning, appearance, and integrations.",
                  style="Muted.TLabel").pack(
                      anchor="w", padx=16, pady=(0, 12))

        notebook = ttk.Notebook(dlg)
        notebook.pack(fill="both", expand=True, padx=16)

        scanning_tab = ttk.Frame(notebook, padding=14)
        integrations_tab = ttk.Frame(notebook, padding=14)
        appearance_tab = ttk.Frame(notebook, padding=14)
        notebook.add(scanning_tab, text="Scanning")
        notebook.add(integrations_tab, text="Integrations")
        notebook.add(appearance_tab, text="Appearance")

        ttk.Label(scanning_tab, text="Scan folders",
                  font=("", 11, "bold")).pack(anchor="w")
        ttk.Label(
            scanning_tab,
            text="RepoManager discovers Git repositories below these folders. "
                 "Saving starts a new scan.",
            style="Muted.TLabel", wraplength=660, justify="left").pack(
                anchor="w", pady=(2, 8))
        roots_frame = ttk.Frame(scanning_tab)
        roots_frame.pack(fill="both", expand=True)
        roots_list = tk.Listbox(roots_frame, height=8, exportselection=False)
        theme.style_tk_widget(roots_list, self.pal, "list")
        roots_list.grid(row=0, column=0, rowspan=2, sticky="nsew")
        for r in self.settings["roots"]:
            roots_list.insert("end", r)
        roots_frame.columnconfigure(0, weight=1)
        roots_frame.rowconfigure(0, weight=1)
        root_entry = ttk.Entry(roots_frame)
        root_entry.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        root_error = ttk.Label(scanning_tab, text="", style="Muted.TLabel")

        def add_root():
            v = root_entry.get().strip()
            if not v:
                root_error.configure(
                    text="Enter a folder path first.",
                    style=theme.semantic_style("ERROR"))
                return
            if not scan_root_is_available(v):
                root_error.configure(
                    text="Choose an existing accessible folder.",
                    style=theme.semantic_style("ERROR"))
                return
            existing_keys = {
                scan_root_key(root) for root in roots_list.get(0, "end")
            }
            if scan_root_key(v) in existing_keys:
                root_error.configure(
                    text="That scan folder is already listed.",
                    style=theme.semantic_style("WARN"))
                return
            roots_list.insert("end", v)
            root_entry.delete(0, "end")
            root_error.configure(text="", style="Muted.TLabel")

        def del_root():
            for i in reversed(roots_list.curselection()):
                roots_list.delete(i)

        ttk.Button(roots_frame, text="Add", command=add_root).grid(
            row=1, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
        ttk.Button(roots_frame, text="Remove selected",
                   command=del_root).grid(
                       row=0, column=1, sticky="new", padx=(8, 0))
        root_error.pack(anchor="w", fill="x", pady=(8, 0))

        depth_row = ttk.Frame(scanning_tab)
        depth_row.pack(fill="x", pady=(12, 0))
        ttk.Label(depth_row, text="Maximum scan depth").pack(side="left")
        depth = ttk.Spinbox(depth_row, from_=1, to=10, width=5)
        depth.set(self.settings.get("depth", 4))
        depth.pack(side="left", padx=(8, 0))
        ttk.Label(depth_row, text="1–10", style="Muted.TLabel").pack(
            side="left", padx=8)

        ttk.Label(integrations_tab, text="Agent command",
                  font=("", 11, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(
            integrations_tab,
            text="Explicitly launched for the selected repository.",
            style="Muted.TLabel").grid(row=1, column=0, columnspan=2,
                                       sticky="w", pady=(2, 6))
        agent = ttk.Entry(integrations_tab)
        agent.insert(0, self.settings.get("agent_cmd", "opencode"))
        agent.grid(row=2, column=0, columnspan=2, sticky="ew")

        ttk.Label(integrations_tab, text="Godot executable",
                  font=("", 11, "bold")).grid(
                      row=3, column=0, sticky="w", pady=(18, 0))
        ttk.Label(
            integrations_tab,
            text="Optional path used by detected Godot launchers.",
            style="Muted.TLabel").grid(row=4, column=0, columnspan=2,
                                       sticky="w", pady=(2, 6))
        godot = ttk.Entry(integrations_tab)
        godot.insert(0, self.settings.get("godot_exe", ""))
        godot.grid(row=5, column=0, sticky="ew")
        integrations_tab.columnconfigure(0, weight=1)

        def browse_godot():
            f = filedialog.askopenfilename(
                parent=dlg, title="Select Godot executable",
                filetypes=[("Executable", "*.exe"), ("All files", "*.*")])
            if f:
                godot.delete(0, "end")
                godot.insert(0, f)

        ttk.Button(integrations_tab, text="Browse…",
                   command=browse_godot).grid(
                       row=5, column=1, padx=(8, 0))

        ttk.Label(appearance_tab, text="Theme",
                  font=("", 11, "bold")).pack(anchor="w")
        theme_name = ("Dark" if self.settings.get("theme", "dark") == "dark"
                      else "Ice Light")
        ttk.Label(
            appearance_tab,
            text=f"Current: {theme_name}. Switch from the main toolbar to "
                 "review the whole application immediately.",
            style="Muted.TLabel", wraplength=660, justify="left").pack(
                anchor="w", pady=(2, 18))
        ttk.Label(appearance_tab, text="Repository table columns",
                  font=("", 11, "bold")).pack(anchor="w")
        ttk.Label(
            appearance_tab,
            text="Manual widths are saved with safe minimum and maximum "
                 "bounds. Restore the default widths if a layout no longer "
                 "fits your workflow.",
            style="Muted.TLabel", wraplength=660, justify="left").pack(
                anchor="w", pady=(2, 8))
        ttk.Button(appearance_tab, text="Restore safe column defaults now",
                   command=self._reset_table_columns).pack(anchor="w")

        validation = ttk.Label(dlg, text="", style="Muted.TLabel")
        validation.pack(fill="x", padx=16, pady=(8, 0))

        def save_and_close():
            try:
                depth_value = int(depth.get())
            except ValueError:
                depth_value = 0
            if not 1 <= depth_value <= 10:
                validation.configure(
                    text="Scan depth must be a whole number from 1 to 10.",
                    style=theme.semantic_style("ERROR"))
                notebook.select(scanning_tab)
                depth.focus_set()
                return
            agent_value = agent.get().strip()
            if not agent_value:
                validation.configure(
                    text="Agent command cannot be empty.",
                    style=theme.semantic_style("ERROR"))
                notebook.select(integrations_tab)
                agent.focus_set()
                return
            updated = {**self.settings,
                       "roots": list(roots_list.get(0, "end")),
                       "depth": depth_value,
                       "agent_cmd": agent_value,
                       "godot_exe": godot.get().strip()}
            try:
                store.save_settings(updated)
            except OSError as exc:
                validation.configure(
                    text=f"Settings were not saved: {exc}",
                    style=theme.semantic_style("ERROR"))
                return
            self.settings.clear()
            self.settings.update(updated)
            self.agent = agents.new_agent(
                "configured", "Configured agent", agent_value)
            self._refresh_agent_status()
            dlg.destroy()
            self.start_scan()

        btns = ttk.Frame(dlg)
        btns.pack(fill="x", padx=16, pady=(8, 16))
        ttk.Button(btns, text="Save & Rescan", command=save_and_close,
                   style="Primary.TButton").pack(side="right")
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(
            side="right", padx=(0, 6))
        dlg.bind("<Return>", lambda _e: save_and_close())
        root_entry.focus_set()

    # ------------------------------------------------------------ launchers
    def _populate_launchers(self, proj, commands=None):
        """Show actions first and explain unavailable candidates inline."""
        for w in self.d_launch.winfo_children():
            w.destroy()
        if commands is not None:
            cmds = list(commands)
        else:
            try:
                project_id = proj.get("project_id")
                custom = [item for item in proj.get(launchers.CUSTOM_LAUNCHERS_FIELD, [])
                          if isinstance(item, dict)]
                cmds = launchers.detect_commands(proj["path"], self.settings,
                                                 project_id=project_id,
                                                 custom_launchers=custom)
            except Exception:
                log.exception("launcher detection failed for %s", proj.get("path"))
                cmds = []
        primary = launchers.select_primary_command(cmds)
        healthy = [c for c in cmds if c.get("healthy", True)]
        if not cmds:
            empty_message = ttk.Label(
                self.d_launch,
                text="No runnable application or project script was detected.",
                style=theme.semantic_style("UNKNOWN"),
                wraplength=360,
                justify="left")
            empty_message.pack(fill="x", anchor="w")
            empty_actions = ttk.Frame(self.d_launch, style="Surface.TFrame")
            empty_actions.pack(fill="x", pady=(8, 0))
            ttk.Button(
                empty_actions, text="Add Custom Launcher…",
                command=lambda: self._edit_custom_launcher(proj)).pack(
                    anchor="w")
            if not (Path(proj["path"]) / "run.bat").exists():
                ttk.Button(
                    empty_actions, text="Generate run.bat",
                    command=lambda: self._generate_stub(proj)).pack(
                    anchor="w", pady=(6, 0))
            return
        if not healthy:
            ttk.Label(
                self.d_launch, text="Launchers detected, but none is ready:",
                style=theme.semantic_style("WARN")).pack(anchor="w")
        custom_records = [c.get("custom") for c in cmds
                          if c.get("type") == launchers.TYPE_CUSTOM and c.get("custom")]
        action_grid = ttk.Frame(self.d_launch, style="Surface.TFrame")
        action_grid.pack(fill="x", pady=(4, 0))
        action_grid.columnconfigure(0, weight=1)
        action_grid.columnconfigure(1, weight=1)
        ordered = launchers.order_commands(cmds)
        primary = launchers.select_primary_command(ordered)
        if primary:
            primary_button = ttk.Button(
                action_grid, text=f"Run · {launchers.launcher_display_name(primary)}",
                command=lambda c=primary: self._run_launcher(c),
                style="Primary.TButton")
            primary_button.grid(row=0, column=0, columnspan=2, sticky="ew",
                                pady=(0, 6))
        alternatives = [c for c in ordered
                        if c is not primary and c.get("healthy", True)]
        alternative_row = 1 if primary else 0
        for index, c in enumerate(alternatives[:2]):
            ttk.Button(
                action_grid, text=f"Run · {launchers.launcher_display_name(c)}",
                command=lambda c=c: self._run_launcher(c)).grid(
                    row=alternative_row, column=index, sticky="ew",
                    padx=(0 if index == 0 else 3,
                          3 if index == 0 else 0))
        all_row = alternative_row + 1
        ttk.Button(
            action_grid, text=f"All launchers… ({len(cmds)})",
            command=lambda: self._show_more_launchers(cmds)).grid(
                row=all_row, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        unavailable = [c for c in ordered if not c.get("healthy", True)]
        for candidate in unavailable[:3]:
            detail = launchers.launcher_summary(candidate)
            reason_label = ttk.Label(
                self.d_launch,
                text=(f"Unavailable · {detail['name']}: "
                      f"{detail['reason'] or 'reason unavailable'}"),
                style="SurfaceMuted.TLabel", wraplength=360,
                justify="left")
            reason_label.pack(anchor="w", fill="x", pady=(4, 0))
            reason_label.bind(
                "<Configure>",
                lambda event, label=reason_label:
                label.configure(wraplength=max(80, event.width - 4)),
                add="+")
        edit_grid = ttk.Frame(self.d_launch, style="Surface.TFrame")
        edit_grid.pack(fill="x", pady=(8, 0))
        edit_grid.columnconfigure(0, weight=1)
        edit_grid.columnconfigure(1, weight=1)
        for index, record in enumerate(custom_records):
            ttk.Button(
                edit_grid, text=f"Edit {record['name']}…",
                command=lambda r=record: self._edit_custom_launcher(
                    proj, r)).grid(
                        row=index // 2, column=index % 2, sticky="ew",
                        padx=(0 if index % 2 == 0 else 3,
                              3 if index % 2 == 0 else 0),
                        pady=(0 if index < 2 else 6, 0))
        add_row = (len(custom_records) + 1) // 2
        ttk.Button(
            edit_grid, text="Add Custom Launcher…",
            command=lambda: self._edit_custom_launcher(proj)).grid(
                row=add_row, column=0, columnspan=2, sticky="ew", pady=(6, 0))

    def _edit_custom_launcher(self, proj, existing=None):
        """Add or edit one structured, project-owned Custom Launcher."""
        dlg = tk.Toplevel(self)
        dlg.title("Edit Custom Launcher" if existing else "Add Custom Launcher")
        dlg.transient(self)
        fields = {}
        field_vars = {}
        form = ttk.Frame(dlg, padding=12)
        form.pack(fill="both", expand=True)
        field_specs = (
            ("name", "Name", 0),
            ("executable", "Executable", 1),
            ("args_text", "Arguments (one per line)", 2),
            ("cwd", "Working directory", 4),
        )
        for key, label, row in field_specs:
            label_row, label_column = CUSTOM_LAUNCHER_FORM_LAYOUT[f"{key if key != 'args_text' else 'args'}_label"]
            input_row, input_column = CUSTOM_LAUNCHER_FORM_LAYOUT[f"{key if key != 'args_text' else 'args'}_input"]
            ttk.Label(form, text=label).grid(
                row=label_row, column=label_column, sticky="nw",
                padx=(0, 8), pady=5)
            if key == "args_text":
                widget = tk.Text(form, width=52, height=5)
            else:
                variable = tk.StringVar()
                field_vars[key] = variable
                widget = ttk.Entry(form, width=52, textvariable=variable)
            widget.grid(row=input_row, column=input_column,
                        sticky="ew", pady=5)
            fields[key] = widget
        ttk.Label(
            form,
            text="One argument per line. Each line is one literal argument; shell syntax is not parsed.",
            style="SurfaceMuted.TLabel",
            wraplength=420,
            justify="left",
        ).grid(row=CUSTOM_LAUNCHER_FORM_LAYOUT["args_help"][0],
               column=CUSTOM_LAUNCHER_FORM_LAYOUT["args_help"][1],
               sticky="w", pady=(0, 5))
        form.columnconfigure(1, weight=1)
        if existing:
            fields["name"].insert(0, existing.get("name", ""))
            fields["executable"].insert(0, existing.get("executable", ""))
            fields["args_text"].insert("1.0", "\n".join(existing.get("args", [])))
            fields["cwd"].insert(0, existing.get("cwd", ""))
        error = ttk.Label(form, text="", style="Muted.TLabel", wraplength=500)
        error.grid(row=CUSTOM_LAUNCHER_FORM_LAYOUT["validation"][0],
                   column=CUSTOM_LAUNCHER_FORM_LAYOUT["validation"][1],
                   columnspan=2, sticky="w")
        error.grid_remove()

        def clear_error(_event=None, *_trace_args):
            error.configure(text="", style="Muted.TLabel")
            error.grid_remove()

        def show_error(message):
            error.configure(text=message, style=theme.semantic_style("FAIL"))
            error.grid()

        for key, widget in fields.items():
            widget.bind("<KeyRelease>", clear_error, add="+")
            if isinstance(widget, tk.Text):
                widget.bind("<<Modified>>", clear_error, add="+")
            else:
                field_vars[key].trace_add(
                    "write", lambda *_args: clear_error())

        def value(widget):
            return widget.get("1.0", "end-1c") if isinstance(widget, tk.Text) else widget.get()

        def save():
            name = value(fields["name"]).strip()
            executable = value(fields["executable"]).strip()
            cwd = value(fields["cwd"]).strip()
            args = value(fields["args_text"]).splitlines()
            message = custom_launcher_validation_message(name, executable, cwd)
            if message:
                show_error(message)
                return
            record = {"launcher_id": (existing or {}).get("launcher_id") or str(__import__("uuid").uuid4()),
                      "project_id": proj.get("project_id"), "name": name,
                      "executable": executable, "args": args, "cwd": cwd}
            clean = launchers.validate_custom_launcher(record)
            if clean is None:
                show_error("Custom Launcher fields are invalid.")
                return
            launchers_list = [item for item in proj.get(launchers.CUSTOM_LAUNCHERS_FIELD, [])
                              if isinstance(item, dict) and item.get("launcher_id") != clean["launcher_id"]]
            launchers_list.append(clean)
            previous = proj.get(launchers.CUSTOM_LAUNCHERS_FIELD)
            proj[launchers.CUSTOM_LAUNCHERS_FIELD] = launchers_list
            try:
                self._persist_projects(workspaces=self.workspaces)
            except OSError as exc:
                if previous is None:
                    proj.pop(launchers.CUSTOM_LAUNCHERS_FIELD, None)
                else:
                    proj[launchers.CUSTOM_LAUNCHERS_FIELD] = previous
                show_error(f"Custom Launcher was not saved: {exc}")
                return
            clear_error()
            dlg.destroy()
            self._populate_launchers(proj)

        buttons = ttk.Frame(form)
        buttons.grid(row=CUSTOM_LAUNCHER_FORM_LAYOUT["actions"][0],
                     column=CUSTOM_LAUNCHER_FORM_LAYOUT["actions"][1],
                     columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="Save", command=save, style="Primary.TButton").pack(side="right")
        ttk.Button(buttons, text="Cancel", command=dlg.destroy).pack(side="right", padx=6)
        if existing:
            def remove():
                if not messagebox.askyesno("Remove Custom Launcher", f"Remove {existing.get('name', 'this launcher')}?"):
                    return
                previous = proj.get(launchers.CUSTOM_LAUNCHERS_FIELD)
                proj[launchers.CUSTOM_LAUNCHERS_FIELD] = [item for item in proj.get(launchers.CUSTOM_LAUNCHERS_FIELD, [])
                                                          if item.get("launcher_id") != existing.get("launcher_id")]
                try:
                    self._persist_projects(workspaces=self.workspaces)
                except OSError as exc:
                    proj[launchers.CUSTOM_LAUNCHERS_FIELD] = previous
                    show_error(f"Custom Launcher was not removed: {exc}")
                    return
                dlg.destroy()
                self._populate_launchers(proj)
            ttk.Button(buttons, text="Remove", command=remove).pack(side="left")
        return dlg

    def _show_more_launchers(self, commands):
        """Present the complete launcher collection with presentation-only filtering."""
        dlg = tk.Toplevel(self)
        self._prepare_dialog(dlg, "All launchers", "900x500")
        ordered = launchers.order_commands(commands)
        filter_var = tk.StringVar()
        header = ttk.Frame(dlg)
        header.pack(fill="x", padx=12, pady=(12, 4))
        ttk.Label(header, text="All launchers", font=("", 11, "bold")).pack(side="left")
        ttk.Label(header, text="Search").pack(side="left", padx=(24, 5))
        search = ttk.Entry(header, textvariable=filter_var, width=36)
        search.pack(side="left", fill="x", expand=True)
        ttk.Label(dlg, text="Filters name, type, source, command, availability, and reason.",
                  style="SurfaceMuted.TLabel").pack(anchor="w", padx=12)
        frame = ttk.Frame(dlg)
        frame.pack(fill="both", expand=True, padx=12, pady=8)
        tree = ttk.Treeview(
            frame, columns=("name", "type", "command", "cwd", "source", "status"),
            show="headings")
        for col, heading, width in (
                ("name", "Name", 180), ("type", "Type", 100),
                ("command", "Command", 260), ("cwd", "CWD", 200),
                ("source", "Source", 150), ("status", "Availability", 180)):
            tree.heading(col, text=heading)
            tree.column(col, width=width, anchor="w")

        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        xsb = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=sb.set, xscrollcommand=xsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        def populate(*_):
            query = filter_var.get().casefold().strip()
            tree.delete(*tree.get_children())
            for candidate in ordered:
                detail = launchers.launcher_summary(candidate)
                status = ("Available" if detail["available"] else
                          f"Unavailable — {detail['reason'] or 'reason unavailable'}")
                command_text = " ".join(detail["command"])
                searchable = " ".join((detail["name"], detail["type"], command_text,
                                        detail["cwd"], detail["source"], status,
                                        detail["reason"])).casefold()
                if query and query not in searchable:
                    continue
                tree.insert("", "end", values=(detail["name"], detail["type"],
                                                command_text, detail["cwd"],
                                                detail["source"], status))

        filter_var.trace_add("write", populate)
        populate()
        search.focus_set()
        ttk.Button(dlg, text="Close", command=dlg.destroy).pack(pady=(0, 12))

    def _run_launcher(self, cmd):
        try:
            launchers.run_command(cmd, self.settings)
            self._status.set(f"started: {cmd['label']}")
        except (OSError, ValueError) as e:
            log.error("launcher failed: %s", e)
            messagebox.showerror("RepoManager", f"Launch failed:\n{e}")

    def _generate_stub(self, proj):
        target = Path(proj["path"]) / "run.bat"
        if target.exists():
            messagebox.showinfo("RepoManager", f"Not overwriting existing file:\n{target}")
            return
        confirmed = messagebox.askyesno(
            "Generate starter run.bat",
            f"Target: {target}\n\nThis will make the repository dirty/untracked.\n"
            "Creation is skipped if run.bat already exists when checked.\nThe generated file is only a starter template.\n\nContinue?")
        if not confirmed:
            return
        try:
            created = launchers.generate_stub_bat(
                proj["path"], lambda _target: True)
            if not created:
                self._populate_launchers(proj)
                self._status.set(
                    f"run.bat was not created in "
                    f"{projects.project_display_name(proj)}",
                    important=True)
                return
            self._populate_launchers(proj)
            self._status.set(
                f"created run.bat in {projects.project_display_name(proj)}")
        except OSError as e:
            messagebox.showerror("RepoManager",
                                 f"Could not write run.bat:\n{e}")

    # ------------------------------------------------------------- Workspaces
    def _build_context_ui(self):
        """Keep Workspace and Agent capability visible but secondary."""
        frame = ttk.Labelframe(
            self, text=" Context & automation ", padding=7)
        frame.pack(fill="x", padx=10, pady=(0, 6))
        frame.columnconfigure(0, weight=3)
        frame.columnconfigure(1, weight=2)
        self._build_workspace_ui(frame)
        self._build_agent_ui(frame)

    def _build_workspace_ui(self, parent):
        """Expose Workspace metadata in the compact context strip."""
        frame = ttk.Frame(parent, style="Surface.TFrame")
        frame.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        ttk.Label(frame, text="Workspace", style="Surface.TLabel",
                  font=("", 9, "bold")).pack(side="left", padx=(0, 6))
        self.workspace_var = tk.StringVar()
        self.workspace_combo = ttk.Combobox(frame, textvariable=self.workspace_var,
                                            state="readonly", width=20)
        self.workspace_combo.pack(side="left")
        self.workspace_combo.bind("<<ComboboxSelected>>", lambda _e: self._show_workspace())
        ttk.Button(frame, text="New", command=self._new_workspace).pack(
            side="left", padx=(6, 0))
        ttk.Button(frame, text="Remove", command=self._remove_workspace).pack(
            side="left", padx=(4, 0))
        self.workspace_status = ttk.Label(
            frame, text="No Workspace selected", style="SurfaceMuted.TLabel",
            wraplength=330, justify="left")
        self.workspace_status.pack(side="left", padx=10)
        ttk.Button(frame, text="Guide", style="Link.TButton",
                   command=lambda: self.open_help("workspace")).pack(
                       side="right")
        self._refresh_workspace_list()

    def _refresh_workspace_list(self):
        self._workspace_by_name = {w.get("name", ""): w for w in self.workspaces}
        names = list(self._workspace_by_name)
        self.workspace_combo["values"] = names
        if names and self.workspace_var.get() not in names:
            self.workspace_var.set(names[0])
        self._show_workspace()

    def _show_workspace(self):
        workspace = self._workspace_by_name.get(self.workspace_var.get())
        if not workspace:
            self.workspace_status.configure(
                text="No Workspace selected",
                style="SurfaceMuted.TLabel")
            return
        self._workspace_gen += 1
        generation = self._workspace_gen
        workspace_id = workspaces.workspace_id(workspace)
        allowed_ids = {
            projects.project_id(project) for project in self.projects
            if projects.project_id(project) is not None
        }
        snapshot = dict(workspace)
        snapshot["members"] = [
            dict(member) if isinstance(member, dict) else member
            for member in workspace.get("members", [])
        ]
        self.workspace_status.configure(
            text="Checking Workspace members…",
            style=theme.semantic_style("IN_PROGRESS"))

        def inspect():
            result = workspaces.inspect_workspace(
                snapshot, observe=scanner.collect_metadata,
                repository_ids=allowed_ids)
            self._scan_queue.put(
                ("workspace", (workspace_id, result, generation)))

        threading.Thread(
            target=guarded_worker(
                self._scan_queue, inspect, error_kind="workspace_error",
                error_payload=lambda message: {
                    "message": message, "generation": generation,
                }),
            daemon=True).start()

    def _apply_workspace_inspection(self, workspace_id, inspection, generation):
        """Render only the current Workspace generation on the Tk thread."""
        selected = self._workspace_by_name.get(self.workspace_var.get())
        if (generation != self._workspace_gen
                or selected is None
                or workspaces.workspace_id(selected) != workspace_id):
            return
        if not isinstance(inspection, dict):
            inspection = {"status": workspaces.UNKNOWN, "member_count": 0,
                          "members": [], "counts": {}}
        counts = inspection.get("counts")
        counts = counts if isinstance(counts, dict) else {}
        members = inspection.get("members")
        members = members if isinstance(members, list) else []
        dirty = sum(
            isinstance(item, dict)
            and isinstance(item.get("dirty"), int)
            and not isinstance(item.get("dirty"), bool)
            and item.get("dirty") > 0
            for item in members)
        status = inspection.get("status", workspaces.UNKNOWN)
        if status == "READY" and any(
                not isinstance(item, dict)
                or not isinstance(item.get("dirty"), int)
                or isinstance(item.get("dirty"), bool)
                for item in members):
            status = workspaces.UNKNOWN
        member_count = inspection.get("member_count", len(members))
        if not isinstance(member_count, int) or isinstance(member_count, bool):
            member_count = len(members)
        self.workspace_status.configure(
            text=(f"{status} · {member_count} members · "
                  f"valid {counts.get('valid', 0)} · dirty {dirty} · "
                  f"missing {counts.get('missing', 0)} · "
                  f"untracked {counts.get('untracked', 0)} · "
                  f"stale {counts.get('stale', 0)}"),
            style=theme.semantic_style(status))

    def _new_workspace(self):
        dlg = tk.Toplevel(self)
        self._prepare_dialog(dlg, "New Workspace", "500x260")
        ttk.Label(dlg, text="New Workspace", font=("", 14, "bold"),
                  foreground=self.pal["accent2"]).pack(
                      anchor="w", padx=16, pady=(16, 2))
        ttk.Label(
            dlg,
            text="Group Projects for read-only inspection. This creates "
                 "RepoManager metadata only and starts empty.",
            style="Muted.TLabel", wraplength=450, justify="left").pack(
                anchor="w", padx=16, pady=(0, 14))
        ttk.Label(dlg, text="Workspace name").pack(
            anchor="w", padx=16, pady=(0, 4))
        name = ttk.Entry(dlg, width=40)
        name.pack(fill="x", padx=16)
        validation = ttk.Label(dlg, text="", style="Muted.TLabel")
        validation.pack(fill="x", padx=16, pady=(8, 0))

        def save():
            value = name.get().strip()
            if not value:
                validation.configure(
                    text="Enter a Workspace name.",
                    style=theme.semantic_style("ERROR"))
                name.focus_set()
                return
            if value in self._workspace_by_name:
                validation.configure(
                    text="A Workspace with that name already exists.",
                    style=theme.semantic_style("ERROR"))
                name.focus_set()
                return
            updated = [*self.workspaces, workspaces.new_workspace(value)]
            try:
                self._persist_projects(workspaces=updated)
            except OSError as exc:
                validation.configure(
                    text=f"Workspace was not saved: {exc}",
                    style=theme.semantic_style("ERROR"))
                return
            self.workspaces[:] = updated
            self._refresh_workspace_list()
            dlg.destroy()

        buttons = ttk.Frame(dlg)
        buttons.pack(fill="x", padx=16, pady=14)
        ttk.Button(buttons, text="Create Workspace", command=save,
                   style="Primary.TButton").pack(side="right")
        ttk.Button(buttons, text="Cancel", command=dlg.destroy).pack(
            side="right", padx=(0, 6))
        dlg.bind("<Return>", lambda _e: save())
        name.focus_set()

    def _remove_workspace(self):
        name = self.workspace_var.get()
        if not name:
            return
        if not messagebox.askyesno("RepoManager", f"Remove Workspace metadata '{name}'?\nRepositories and Worktrees remain untouched."):
            return
        updated = [w for w in self.workspaces if w.get("name") != name]
        try:
            self._persist_projects(workspaces=updated)
        except OSError as exc:
            messagebox.showerror(
                "RepoManager", f"Workspace was not removed: {exc}", parent=self)
            return
        self.workspaces[:] = updated
        self._refresh_workspace_list()

    # --------------------------------------------------------------- Agents
    def _build_agent_ui(self, parent):
        frame = ttk.Frame(parent, style="Surface.TFrame")
        frame.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        heading = ttk.Frame(frame, style="Surface.TFrame")
        heading.pack(fill="x")
        ttk.Label(heading, text="Agent", style="Surface.TLabel",
                  font=("", 9, "bold")).pack(side="left", padx=(0, 6))
        self.agent_status = ttk.Label(heading, style="SurfaceMuted.TLabel")
        self.agent_status.pack(side="left", fill="x", expand=True)
        self.agent_launch_btn = ttk.Button(
            heading, text="Start Agent", command=self._launch_agent, width=11)
        self.agent_launch_btn.pack(side="left", padx=(6, 4))
        self.agent_stop_btn = ttk.Button(
            heading, text="Stop Agent", command=self._stop_agent, width=11)
        self.agent_stop_btn.pack(side="left")
        self.agent_preflight = ttk.Label(
            frame, text="", style="SurfaceMuted.TLabel", wraplength=420,
            justify="left")
        self.agent_preflight.pack(anchor="w", fill="x", pady=(3, 0))
        self.run_status = ttk.Label(
            frame, text="No Agent run", style="SurfaceMuted.TLabel",
            wraplength=420, justify="left")
        self.run_status.pack(anchor="w", fill="x", pady=(2, 0))
        self._refresh_agent_status()

    def _refresh_agent_status(self):
        target = self._selected_project()
        readiness = agents.agent_readiness(self.agent, target)
        ready = readiness["state"] == agents.READY
        self.agent_status.configure(
            text="Ready" if ready else "Not ready",
            style=theme.semantic_style(readiness["state"]))
        if ready:
            name = projects.project_display_name(target)
            self.agent_preflight.configure(
                text=(f"Agent: {self.agent['display_name']} · Target: {name} · command: "
                      f"{Path(readiness['executable']).name} · "
                      f"cwd: {readiness['cwd']}"))
        else:
            self.agent_preflight.configure(
                text=f"Agent: {self.agent['display_name']} · {readiness['reason']}")
        self.agent_launch_btn.state(["!disabled"] if ready else ["disabled"])
        running = self._latest_active_agent_run()
        self.agent_stop_btn.state(["!disabled"] if running else ["disabled"])

    def _latest_active_agent_run(self):
        for run in reversed(self.runs):
            if run.get("process_state") in (
                    agents.STARTING, agents.RUNNING, agents.STOPPING,
                    agents.UNKNOWN_PROCESS):
                return run
        return None

    def _launch_agent(self):
        target = self._selected_project()
        if not target or not projects.is_repository_backed(target):
            self._status.set("Select a concrete repository target first", important=True)
            return
        self._flush_note_save()
        self._save_detail()
        path = target.get("path")
        if agents.has_running_run(self.runs, self.agent["agent_id"], path):
            self._status.set("An agent is already running for this target", important=True)
            return
        try:
            live_target = {
                "kind": "repository", "path": path,
                "project_id": projects.project_id(target),
                "name": projects.project_display_name(target),
            }
            intent = agents.launch_intent(self.agent, live_target)
        except ValueError as exc:
            self._status.set(str(exc), important=True)
            return
        run = agents.new_run(intent)
        current = next((project for project in self.projects
                        if projects.project_id(project)
                        == live_target["project_id"]), None)
        current_target = ({
            "kind": "repository", "path": current.get("path"),
            "project_id": projects.project_id(current),
            "name": projects.project_display_name(current),
        } if current is not None else {})
        process = agents.start_run(
            run, agent=self.agent, target=current_target)
        self.runs.append(run)
        if process is None:
            self._set_run_status(
                f"Run failed to start: {run.get('failure', '')}",
                agents.FAILED_TO_START)
            self._refresh_agent_status()
            return
        self._agent_processes[run["run_id"]] = process
        self._set_run_status(
            f"Run {run['run_id'][:8]}: {run['process_state']}",
            run['process_state'])
        self._refresh_agent_status()
        self._observe_agent_run(run, process)

    def _stop_agent(self):
        run = self._latest_active_agent_run()
        if run is None:
            return
        process = self._agent_processes.get(run.get("run_id"))
        if process is None:
            run["process_state"] = agents.UNKNOWN_PROCESS
            self._set_run_status("Agent process could not be observed", agents.UNKNOWN)
            self._refresh_agent_status()
            return
        state = agents.cancel_run(run, process)
        label = ("Stop requested · waiting for confirmed exit"
                 if state == agents.STOPPING else f"Agent: {state}")
        self._set_run_status(label, state)
        self._refresh_agent_status()

    def _set_run_status(self, text, state):
        """Show a run-status line with the state colour-token applied."""
        self.run_status.configure(
            text=text, style=theme.semantic_style(state))

    def _observe_agent_run(self, run, process):
        state = agents.observe_run(run, process)
        state_text = {
            agents.RUNNING: "Running",
            agents.STOPPING: "Stop requested · waiting for confirmed exit",
            agents.EXITED: "Exited",
            agents.TERMINATED: "Stopped",
            agents.UNKNOWN_PROCESS: "Process state unknown",
        }.get(state, state)
        self._set_run_status(f"Run {run['run_id'][:8]}: {state_text}", state)
        if state == agents.UNKNOWN_PROCESS:
            self._set_run_status(
                f"Run {run['run_id'][:8]}: process exit is not confirmed",
                agents.UNKNOWN)
            self._refresh_agent_status()
            if self._close_after_agents:
                self._close_after_agents = False
                messagebox.showerror(
                    "RepoManager",
                    "RepoManager stayed open because Agent exit could not be "
                    "confirmed.", parent=self)
            return
        if state in (agents.EXITED, agents.TERMINATED, agents.FAILED_TO_START):
            self._agent_processes.pop(run.get("run_id"), None)
            agents.verify_post_run(run, observe=scanner.collect_metadata)
            verification = run.get('verification', agents.NOT_RUN)
            verification_text = {
                agents.TARGET_RECHECKED: "Target rechecked",
                agents.FAILED: "Target recheck failed",
                agents.UNKNOWN: "Target recheck unknown",
            }.get(verification, verification)
            self._set_run_status(
                f"Run {run['run_id'][:8]}: {state_text} · {verification_text}",
                verification)
            self._refresh_agent_status()
            if self._close_after_agents and self._latest_active_agent_run() is None:
                self._finish_close()
            return
        self._schedule_after(250,
                             lambda: self._observe_agent_run(run, process))

    # ----------------------------------------------------------- selection
    def _set_active(self, which):
        """Remember which tree owns the current interaction ("main" or "now").

        Used by ``_selected_project`` and ``_show_context_menu`` to route
        selection and commands to the tree the user is interacting with.
        """
        self._active_tree = which

    def _selected_project(self):
        """Return the Project owned by the active tree selection.

        FocusIn, mouse selection, and context-menu routing establish the active
        tree, so keyboard navigation and detail buttons share the same owner.
        The other tree is deliberately ignored: its selection may be mirrored or
        stale and must never become an implicit action target.
        """
        if not hasattr(self, "tree") or not hasattr(self, "now_tree"):
            return None
        tree = self.now_tree if self._active_tree == "now" else self.tree
        sel = tree.selection()
        if not sel or sel[0] == "__won-empty__":
            return None
        return next((p for p in self.projects
                     if project_row_id(p) == sel[0]), None)

    def _launch(self, cmd):
        try:
            subprocess.Popen(cmd, close_fds=True,
                             creationflags=CREATE_NO_WINDOW)
        except OSError as e:
            log.error("launch failed: %s %s", e, cmd)
            messagebox.showerror(
                "RepoManager",
                f"Launch failed:\n{e}\n\n{' '.join(map(str, cmd))}")

    def open_explorer(self):
        p = self._selected_project()
        path = available_project_folder(p)
        if path is None:
            self._status.set("Project folder is unavailable", important=True)
            return
        self._launch(["explorer.exe", path])

    def open_vscode(self):
        p = self._selected_project()
        path = available_project_folder(p)
        if path is None:
            self._status.set("Project folder is unavailable", important=True)
            return
        exe = shutil.which("code")
        if not exe:
            candidate = Path.home() / ("AppData/Local/Programs/"
                                       "Microsoft VS Code/bin/code.cmd")
            exe = str(candidate) if candidate.exists() else None
        if not exe:
            messagebox.showerror("RepoManager", "VS Code not found on PATH.")
            return
        argument = path
        if os.name == "nt" and Path(exe).suffix.casefold() in processes.BATCH_SUFFIXES:
            # code.cmd forwards quoted arguments to the native CLI parser.
            argument += "\\" * (len(path) - len(path.rstrip("\\")))
        try:
            processes.spawn_structured(
                exe, (argument,), cwd=path, popen=subprocess.Popen,
                creationflags=CREATE_NO_WINDOW)
        except (OSError, ValueError) as e:
            log.error("launch failed: %s %s", e, [exe, path])
            messagebox.showerror(
                "RepoManager", f"Launch failed:\n{e}\n\n{exe} {path}")

    def open_terminal(self):
        p = self._selected_project()
        path = available_project_folder(p)
        if path is None:
            self._status.set("Project folder is unavailable", important=True)
            return
        self._launch(["wt.exe", "-d", path])

    def open_agent(self):
        """Compatibility action routed through the structured Agent contract."""
        self._launch_agent()

    # ------------------------------------------------- close / tooltips
    def _schedule_after(self, delay, callback):
        """Schedule a callback tracked so shutdown can cancel it."""
        if self.__dict__.get("_closing", False):
            return None
        def tracked_callback():
            self.__dict__.get("_after_jobs", set()).discard(job)
            if not self.__dict__.get("_closing", False):
                callback()
        try:
            job = self.after(delay, tracked_callback)
        except tk.TclError:
            return None
        jobs = self.__dict__.get("_after_jobs")
        if jobs is not None:
            jobs.add(job)
        return job

    def _schedule_after_idle(self, callback):
        """Schedule an idle callback tracked by the shutdown guard."""
        if self.__dict__.get("_closing", False):
            return None
        def tracked_callback():
            self.__dict__.get("_after_jobs", set()).discard(job)
            if not self.__dict__.get("_closing", False):
                callback()
        try:
            job = self.after_idle(tracked_callback)
        except tk.TclError:
            return None
        jobs = self.__dict__.get("_after_jobs")
        if jobs is not None:
            jobs.add(job)
        return job

    def _cancel_after_jobs(self):
        """Cancel tracked callbacks before destroying Tk widgets."""
        for job in tuple(self.__dict__.get("_after_jobs", ())):
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        self.__dict__.get("_after_jobs", set()).clear()

    def destroy(self):
        """Make direct Tk teardown safe for tests and exceptional callers."""
        self.__dict__["_closing"] = True
        self._cancel_after_jobs()
        try:
            self._cancel_tip_job()
        except (AttributeError, tk.TclError):
            pass
        try:
            self._cancel_project_save()
        except (AttributeError, tk.TclError):
            pass
        try:
            # Let ttk deliver already-queued virtual theme events while the
            # target widgets still exist. Tracked application callbacks are
            # inert because ``_closing`` is already true.
            self.update_idletasks()
        except tk.TclError:
            pass
        super().destroy()

    def _confirm_stop_agents_and_close(self):
        """Offer the two close-window choices for running Agent processes."""
        dlg = tk.Toplevel(self)
        self._prepare_dialog(
            dlg, "Agent is still running", "520x230", modal=True)
        result = {"stop": False}
        ttk.Label(
            dlg, text="An Agent is still running", font=("", 13, "bold"),
            foreground=self.pal["accent2"]).pack(
                anchor="w", padx=16, pady=(16, 6))
        ttk.Label(
            dlg,
            text=("RepoManager will not leave an Agent detached. Stop requests "
                  "are confirmed by observing process exit before the window "
                  "closes."),
            wraplength=480, justify="left").pack(
                anchor="w", padx=16, pady=(0, 16))
        buttons = ttk.Frame(dlg)
        buttons.pack(fill="x", padx=16, pady=(0, 16))

        def choose_stop():
            result["stop"] = True
            dlg.destroy()

        ttk.Button(
            buttons, text="Stop Agent and close", command=choose_stop,
            style="Primary.TButton").pack(side="right")
        ttk.Button(
            buttons, text="Cancel closing", command=dlg.destroy).pack(
                side="right", padx=(0, 6))
        dlg.wait_window()
        return result["stop"]

    def _on_close(self):
        """Apply the Git/Agent close policy, then persist and close."""
        if self.__dict__.get("_closing", False):
            return
        if self.__dict__.get("_git_workers", set()):
            self._close_after_git = True
            status_line = self.__dict__.get("_status")
            if status_line is not None:
                status_line.set(
                    "Git operation still running; window will close after its "
                    "terminal result is received.",
                    important=True)
            return
        active = [run for run in self.runs if run.get("process_state") in (
            agents.STARTING, agents.RUNNING, agents.STOPPING,
            agents.UNKNOWN_PROCESS)]
        if active:
            if self._close_after_agents:
                return
            if not self._confirm_stop_agents_and_close():
                return
            self._close_after_agents = True
            stop_failed = False
            for run in active:
                process = self._agent_processes.get(run.get("run_id"))
                if process is None:
                    run["process_state"] = agents.UNKNOWN_PROCESS
                    stop_failed = True
                    continue
                if agents.cancel_run(run, process) == agents.UNKNOWN_PROCESS:
                    stop_failed = True
                    continue
                self._observe_agent_run(run, process)
            if stop_failed:
                self._close_after_agents = False
                messagebox.showerror(
                    "RepoManager",
                    "RepoManager stayed open because Agent exit could not be "
                    "confirmed.", parent=self)
                return
            if self._latest_active_agent_run() is not None:
                self._status.set(
                    "Stopping Agent · window will close after confirmed exit",
                    important=True)
                return
        self._finish_close()

    def _finish_close(self):
        """Persist pending edits and destroy only after Agent exit."""
        if self.__dict__.get("_closing", False):
            return
        try:
            self._flush_note_save()
            self._save_detail()
            self._flush_project_save()
        except (OSError, store.RegistryCorrupt) as exc:
            log.exception("close blocked because pending data could not be saved")
            messagebox.showerror(
                "RepoManager",
                "RepoManager stayed open because pending data could not be "
                f"saved.\n\n{exc}\n\nResolve the storage problem and close again.",
                parent=self,
            )
            return
        self._closing = True
        self._cancel_after_jobs()
        self._cancel_tip_job()
        self._cancel_project_save()
        self.destroy()

    def _on_double_click(self, event):
        if event.widget.identify_row(event.y):
            return self._launch_primary(event)
        return None

    def _show_problems(self):
        if not self._problems and not self._move_suggestions:
            return
        dlg = tk.Toplevel(self)
        self._prepare_dialog(dlg, "Problems & possible moves", "780x560")
        ttk.Button(dlg, text="Close (dismiss \u2014 suggestions reappear "
                             "on next scan)",
                   command=dlg.destroy).pack(pady=(8, 4))
        outer = ttk.Frame(dlg)
        outer.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        canvas = tk.Canvas(outer, bg=self.pal["bg"],
                           highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        self._problems_body = tk.Frame(canvas, bg=self.pal["bg"])
        self._problems_body.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self._problems_body,
                             anchor="nw", width=700)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        dlg.bind("<MouseWheel>", lambda e: canvas.yview_scroll(
            -1 * (e.delta // 120), "units"))
        self._render_problems_body()

    def _render_problems_body(self):
        body = getattr(self, "_problems_body", None)
        if body is None or not body.winfo_exists():
            return
        for w in body.winfo_children():
            w.destroy()
        wrap = 620
        problem_groups = group_problems(self._problems)
        if problem_groups["repository"]:
            ttk.Label(body, text="Repository problems",
                      style=theme.semantic_style("ERROR")).pack(
                          anchor="w", pady=(2, 4))
            lst = tk.Listbox(
                body, height=min(5, len(problem_groups["repository"])))
            theme.style_tk_widget(lst, self.pal, "list")
            lst.pack(fill="x")
            for prob in problem_groups["repository"]:
                lst.insert("end",
                           f"{prob['path']}  \u2014  {prob['reason']}")

        if problem_groups["scan_root"]:
            ttk.Label(body, text="Scan issues",
                      style=theme.semantic_style("WARN")).pack(
                          anchor="w", pady=(12, 4))
            lst = tk.Listbox(
                body, height=min(5, len(problem_groups["scan_root"])))
            theme.style_tk_widget(lst, self.pal, "list")
            lst.pack(fill="x")
            for prob in problem_groups["scan_root"]:
                lst.insert("end",
                           f"{prob['path']}  \u2014  {prob['reason']}")

        if self._move_suggestions:
            groups = group_move_suggestions(self._move_suggestions)
            strong, ambiguous = (groups["strong"], groups["ambiguous"])
            ttk.Label(body, text="Possible moves",
                      style=theme.semantic_style("WARN")).pack(
                          anchor="w", pady=(12, 4))
            if strong:
                n = len(strong)
                ttk.Button(body, text=f"Accept {n} strong move"
                                      f"{'' if n == 1 else 's'}",
                           command=self._accept_moves_batch).pack(
                    anchor="w", pady=(0, 6))
            for section, items in (("Strong matches", strong),
                                   ("Ambiguous \u2014 choose manually",
                                    ambiguous),
                                   ("Other matches", groups["possible"])):
                if not items:
                    continue
                section_style = (theme.semantic_style("WARN")
                                 if section.startswith("Ambiguous")
                                 else theme.semantic_style("UNKNOWN"))
                ttk.Label(body, text=section,
                          style=section_style).pack(anchor="w", pady=(8, 2))
                for sug in items:
                    row = tk.Frame(body, bg=self.pal["panel"], bd=1,
                                   relief="solid")
                    row.pack(fill="x", pady=3)
                    head = f"{sug['name']}  ({sug['category']})"
                    old = sug.get("old_path") or \
                        " / ".join(sug.get("old_paths") or [])
                    new = sug.get("new_path") or \
                        " / ".join(sug.get("new_paths") or [])
                    detail = (f"was: {old}\nnow: {new}\n"
                              + "\n".join(sug.get("evidence") or []))
                    tk.Label(row, text=f"{head}\n{detail}",
                             bg=self.pal["panel"], fg=self.pal["text"],
                             anchor="w", justify="left",
                             wraplength=wrap).pack(
                        side="left", fill="both", expand=True,
                        padx=8, pady=6)
                    btns = tk.Frame(row, bg=self.pal["panel"])
                    btns.pack(side="right", padx=6)
                    is_amb = sug["category"] == "ambiguous" \
                        or "old_paths" in sug
                    if not is_amb:
                        ttk.Button(btns, text="Use new path",
                                   command=lambda s=sug:
                                   self._accept_move(s)).pack(
                            pady=(4, 2), fill="x")
                    ttk.Button(btns, text="Keep both",
                               command=lambda s=sug:
                               self._keep_both_move(s)).pack(fill="x")

    def _accept_moves_batch(self):
        """Batch-accept all strong suggestions via the same transaction."""
        now_iso = scanner.utc_now_iso()
        accepted, failures = run_batch_moves(
            list(self._move_suggestions), self.projects, now_iso,
            save_projects=self._persist_projects,
            move_note=store.move_note,
            path_exists=os.path.exists,
            target_identity=scanner.move_target_identity)
        consumed = {id(s) for s, _entry, _outcome in accepted}
        self._move_suggestions = [s for s in self._move_suggestions
                                  if id(s) not in consumed]
        for s, entry, outcome in accepted:
            self._scan_gen += 1
            self._moved_away.append(
                (self._scan_gen, s["old_path"].lower()))
            log.info("batch confirmed move: %s -> %s",
                     s["old_path"], entry["path"])
        lines = [f"Accepted: {len(accepted)}"]
        if failures:
            lines.append(f"Failed/skipped: {len(failures)}")
            for s, outcome in failures[:5]:
                names = s.get("name") or Path(
                    s.get("old_path", "?")).name
                lines.append(f"  \u2022 {names} \u2014 {outcome}")
        uncertain = any(outcome == MOVE_ROLLBACK_FAILED
                        for _s, outcome in failures)
        messagebox.showinfo("RepoManager", "\n".join(lines))
        self._avail.invalidate()
        self._populate_coalesced()
        if uncertain:
            messagebox.showerror(
                "RepoManager",
                "At least one move rollback could not be confirmed. "
                "Durable registry state may still contain the new path; "
                "inspect the registry and note files before retrying.",
                parent=self)
        else:
            self._refresh_metadata_async()
        self._render_problems_body()

    def _accept_move(self, suggestion, dialog=None):
        """Confirmed move: re-path the OLD entry; migrate curation + notes."""
        self._flush_note_save()
        now_iso = scanner.utc_now_iso()
        outcome, entry = perform_confirmed_move(
            self.projects, suggestion, now_iso,
            save_projects=self._persist_projects,
            move_note=store.move_note,
            path_exists=os.path.exists,
            target_identity=scanner.move_target_identity)
        if outcome == "missing_old":
            messagebox.showerror("RepoManager", "Old entry no longer found.")
        elif outcome == "duplicate":
            messagebox.showerror(
                "RepoManager",
                "The new path already has its own registry entry.\n"
                "Resolve the duplicate manually.")
        elif outcome == "stale_target":
            messagebox.showerror(
                "RepoManager",
                "The target changed since the scan — the move was cancelled.\n"
                "The old path is present again or the new path is missing.")
        elif outcome == "rolled_back_registry":
            messagebox.showerror(
                "RepoManager",
                "Saving the registry failed \u2014 the move was cancelled "
                "and nothing was changed.")
        elif outcome == "rolled_back_note":
            messagebox.showerror(
                "RepoManager",
                "Moving the project note failed \u2014 the move was rolled "
                "back. Registry and note are unchanged at the old location.")
        elif outcome == MOVE_ROLLBACK_FAILED:
            messagebox.showerror(
                "RepoManager",
                "The move could not be fully rolled back. Registry or note "
                "state may have changed. "
                "Inspect the registry and note files before retrying.",
                parent=self)
            log.critical(
                "confirmed move rollback is unconfirmed for %s -> %s",
                suggestion.get("old_path"), suggestion.get("new_path"))
            self._status.set(
                "move rollback unconfirmed; inspect durable state before retrying",
                important=True)
        elif outcome in (MOVE_OK, MOVE_COLLISION):
            if outcome == MOVE_COLLISION:
                messagebox.showwarning(
                    "RepoManager",
                    "Both locations had notes. The old note was kept next "
                    "to the existing one with a '-moved' suffix.")
            old_path = suggestion["old_path"]
            self._scan_gen += 1
            self._moved_away.append((self._scan_gen, old_path.lower()))
            self._move_suggestions = [s for s in self._move_suggestions
                                      if s is not suggestion]
            log.info("confirmed move: %s -> %s",
                     old_path, entry["path"])
            self._status.set(
                f"moved: {projects.project_display_name(entry)}",
                important=True)
        self._avail.invalidate()
        self._populate_coalesced()
        self._render_problems_body()

    def _keep_both_move(self, suggestion):
        """Keep both entries; suppress this exact pairing going forward."""
        olds = [suggestion.get("old_path")] + \
            list(suggestion.get("old_paths") or [])
        news = [suggestion.get("new_path")] + \
            list(suggestion.get("new_paths") or [])
        supp = list(self.settings.get("move_suppressions", []))
        for o in olds:
            for n in news:
                if o and n:
                    pair = {"old": o.lower(), "new": n.lower()}
                    if pair not in supp:
                        supp.append(pair)
        updated = {**self.settings, "move_suppressions": supp}
        try:
            store.save_settings(updated)
        except OSError as exc:
            messagebox.showerror(
                "RepoManager", f"Move choice was not saved: {exc}", parent=self)
            return
        self.settings.clear()
        self.settings.update(updated)
        self._move_suggestions = [s for s in self._move_suggestions
                                  if s is not suggestion]
        self._status.set("kept both repositories")
        self._render_problems_body()
        self._populate_coalesced()

    def _cancel_tip_job(self):
        if self._tip_job:
            try:
                self.after_cancel(self._tip_job)
            except tk.TclError:
                pass
            self.__dict__.get("_after_jobs", set()).discard(self._tip_job)
            self._tip_job = None

    def _hide_tooltip(self, _event=None):
        self._cancel_tip_job()
        self._tip_row = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None

    def _on_tree_motion(self, event):
        row = event.widget.identify_row(event.y)
        if row == self._tip_row:
            return
        self._hide_tooltip()
        self._tip_row = row
        if row:
            self._tip_job = self._schedule_after(
                TOOLTIP_DELAY_MS, lambda: self._show_tooltip(row))

    def _show_tooltip(self, row):
        self._tip_job = None
        proj = next((p for p in self.projects
                     if project_row_id(p) == row), None)
        if not proj:
            return
        path = projects.project_folder(proj) or ""
        lines = [path]
        if not self._avail.get(path):
            lines.append(f"Not found since: "
                         f"{proj.get('last_seen') or 'unknown'}")
        if proj.get("last_commit_msg"):
            lines.append(f"{proj.get('last_commit_date') or ''}  "
                         f"{proj['last_commit_msg']}")
        if proj.get("branch"):
            lines.append(f"branch: {proj['branch']}")
        if proj.get("remote"):
            lines.append(
                f"remote: {self._web_url(proj) or '(unsafe/unavailable)'}")
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
        self._tip = tw = tk.Toplevel(self)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{self.winfo_pointerx() + 14}"
                       f"+{self.winfo_pointery() + 20}")
        tk.Label(tw, text="\n".join(lines), justify="left", anchor="w",
                 bg=self.pal["panel2"], fg=self.pal["text"],
                 relief="solid", borderwidth=1, padx=8, pady=4,
                 wraplength=540).pack()


def main():
    enable_dpi_awareness()
    setup_logging()
    set_windows_app_user_model_id()
    try:
        acquired = acquire_single_instance_lock()
    except OSError as exc:
        log.exception("single-instance guard unavailable")
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "RepoManager",
            "RepoManager could not establish its application-data lock.\n\n"
            f"{exc}\n\nCheck access to %LOCALAPPDATA%\\RepoManager.")
        root.destroy()
        return
    if not acquired:
        log.warning("second instance blocked")
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "RepoManager",
            "RepoManager is already running.\n"
            "Check your taskbar for the existing window.")
        root.destroy()
        return
    log.info("starting RepoManager")
    app = RepoManagerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
