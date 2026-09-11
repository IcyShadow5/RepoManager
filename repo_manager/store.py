"""JSON persistence for RepoManager."""
import copy
import hashlib
import json
import logging
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path

from .workspaces import ensure_workspace_id, validate_workspace


def _sanitize_custom_launchers(record, index):
    """Keep valid structured custom launchers and report malformed entries."""
    launchers = record.get("custom_launchers")
    if launchers is None:
        return record, []
    if not isinstance(launchers, list):
        return {key: value for key, value in record.items()
                if key != "custom_launchers"}, [f"[{index}] invalid 'custom_launchers'"]
    valid = []
    issues = []
    for item_index, item in enumerate(launchers):
        if not isinstance(item, dict):
            issues.append(f"[{index}] custom launcher [{item_index}] is not an object")
            continue
        required = ("project_id", "name", "executable", "cwd")
        if (not all(isinstance(item.get(key), str) and item[key].strip()
                    for key in required)
                or not isinstance(item.get("args", []), list)
                or not all(isinstance(arg, str) for arg in item.get("args", []))):
            issues.append(f"[{index}] custom launcher [{item_index}] is malformed")
            continue
        owner_id = record.get("project_id")
        if (isinstance(owner_id, str) and owner_id.strip()
                and item["project_id"] != owner_id):
            issues.append(
                f"[{index}] custom launcher [{item_index}] belongs to a different project")
            continue
        valid.append(dict(item))
    cleaned = dict(record)
    cleaned["custom_launchers"] = valid
    return cleaned, issues

APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "RepoManager"
REPOS_FILE = APP_DIR / "repos.json"
SETTINGS_FILE = APP_DIR / "settings.json"
NOTES_DIR = APP_DIR / "notes"

SCHEMA_VERSION = 2
MAX_REGISTRY_BYTES = 16 * 1024 * 1024
MAX_SETTINGS_BYTES = 1 * 1024 * 1024
MAX_SETTINGS_QUARANTINES = 5
MAX_PROJECTS = 100_000
MAX_WORKSPACES = 10_000
MAX_WORKSPACE_MEMBERS = 10_000
_PROJECT_STATUSES = {"idea", "active", "paused", "archived"}
_COUNT_FIELDS = ("dirty", "staged", "unstaged", "untracked", "ahead", "behind")

DEFAULT_SETTINGS = {
    "roots": [],
    "depth": 4,
    "skip_dirs": [
        "node_modules", ".venv", "venv", "__pycache__", ".next",
        "dist", "build", ".godot", "AppData", ".cache", ".mono",
        "Intermediate", "Binaries", "Saved", "DerivedDataCache",
        "site-packages",
    ],
    "agent_cmd": "opencode",
    "move_suppressions": [],
}

log = logging.getLogger(__name__)
_REGISTRY_SAVE_LOCK = threading.RLock()
_SETTINGS_REPORT = {"status": "fresh", "quarantined": None,
                    "reasons": [], "preservation_failed": False}
_SETTINGS_QUARANTINED_KEY = None
_RECOVERED_WORKSPACE_CACHE = {}


class RegistryCorrupt(Exception):
    """File-level registry corruption (whole file unusable as-is)."""


def ensure_dirs():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    NOTES_DIR.mkdir(parents=True, exist_ok=True)


def settings_recovery_report():
    """Return a copy of the latest settings load/recovery evidence."""
    return copy.deepcopy(_SETTINGS_REPORT)


def _registry_cache_key():
    """Return a stable in-process key for the active registry path."""
    return os.path.normcase(os.path.abspath(str(REPOS_FILE)))


def _cache_recovered_workspaces(workspaces):
    """Retain recovered Workspace metadata while a primary is unavailable."""
    _RECOVERED_WORKSPACE_CACHE[_registry_cache_key()] = copy.deepcopy(
        list(workspaces or []))


def _cached_recovered_workspaces():
    """Return the last known Workspace state for a missing primary."""
    return copy.deepcopy(_RECOVERED_WORKSPACE_CACHE.get(_registry_cache_key(), []))


def _cache_committed_workspaces(workspaces):
    """Refresh derived recovery state without reversing a durable save."""
    try:
        _cache_recovered_workspaces(workspaces)
    except Exception:
        log.exception(
            "registry committed but recovered Workspace cache update failed")


def _quarantine_settings(raw):
    """Preserve malformed settings bytes under a bounded sidecar name."""
    global _SETTINGS_QUARANTINED_KEY
    digest = hashlib.sha256(raw).hexdigest()
    quarantine_key = (
        os.path.normcase(os.path.abspath(str(SETTINGS_FILE))), digest)
    previous = _SETTINGS_REPORT.get("quarantined")
    if (_SETTINGS_QUARANTINED_KEY == quarantine_key and previous
            and Path(previous).exists()):
        return Path(previous)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = Path(str(SETTINGS_FILE) + f".corrupt-{stamp}")
    n = 0
    while target.exists():
        n += 1
        target = Path(str(SETTINGS_FILE) + f".corrupt-{stamp}-{n}")
    try:
        ensure_dirs()
        target.write_bytes(raw)
    except OSError as exc:
        log.error("could not quarantine corrupt settings: %s", exc)
        return None
    try:
        candidates = sorted(
            Path(APP_DIR).glob("settings.json.corrupt-*"),
            key=lambda path: path.stat().st_mtime)
    except OSError:
        candidates = []
    for stale in candidates[:-MAX_SETTINGS_QUARANTINES]:
        try:
            stale.unlink()
        except OSError:
            log.warning("could not trim old settings quarantine %s", stale)
    _SETTINGS_QUARANTINED_KEY = quarantine_key
    return target


def _load_settings_payload():
    """Read settings while preserving malformed source bytes and evidence."""
    global _SETTINGS_REPORT
    try:
        raw = SETTINGS_FILE.read_bytes()
    except FileNotFoundError:
        _SETTINGS_REPORT = {"status": "fresh", "quarantined": None,
                            "reasons": [], "preservation_failed": False}
        return {}
    except OSError as exc:
        _SETTINGS_REPORT = {
            "status": "unavailable", "quarantined": None,
            "reasons": [f"unreadable: {exc}"],
            "preservation_failed": False,
        }
        return {}
    try:
        if len(raw) > MAX_SETTINGS_BYTES:
            raise ValueError("settings exceeds size limit")
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("settings top level is not an object")
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        quarantined = _quarantine_settings(raw)
        _SETTINGS_REPORT = {
            "status": "recovered", "quarantined": str(quarantined)
            if quarantined else None, "reasons": [str(exc)],
            "preservation_failed": quarantined is None,
        }
        log.warning("settings recovered with safe defaults: %s", exc)
        return {}
    _SETTINGS_REPORT = {"status": "valid", "quarantined": None,
                        "reasons": [], "preservation_failed": False}
    return parsed


def _preserve_settings_before_write():
    """Quarantine malformed existing settings before replacing them."""
    try:
        raw = SETTINGS_FILE.read_bytes()
    except FileNotFoundError:
        return
    except OSError:
        raise
    try:
        if len(raw) > MAX_SETTINGS_BYTES:
            raise ValueError("settings exceeds size limit")
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("settings top level is not an object")
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        quarantined = _quarantine_settings(raw)
        if quarantined is None:
            raise OSError(
                "malformed settings could not be preserved before replacement"
            ) from exc
        return quarantined
    return None


def _write_json(path, data):
    ensure_dirs()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def sanitize_fingerprint(value):
    """Normalize a record's fingerprint evidence; None if unusable.

    Unknown inner keys are preserved for forward compatibility. Malformed
    remotes/root_commits are reset to empty lists, never fatal.
    """
    if not isinstance(value, dict):
        return None
    out = dict(value)
    for key in ("remotes", "root_commits"):
        val = value.get(key)
        out[key] = [x for x in val if isinstance(x, str)] \
            if isinstance(val, list) else []
    return out


def _canonical_registry_path(value):
    return os.path.normcase(os.path.abspath(os.path.normpath(value)))


def _reject_duplicate_project_paths(projects):
    """Refuse to persist state that Registry validation would later drop."""
    seen = {}
    for index, record in enumerate(projects):
        if not isinstance(record, dict):
            continue
        path = record.get("path")
        if not isinstance(path, str) or not path.strip():
            path = record.get("folder_path")
        if not isinstance(path, str) or not path.strip():
            continue
        key = _canonical_registry_path(path)
        if key in seen:
            raise RegistryCorrupt(
                "refusing to save duplicate Project path at records "
                f"[{seen[key]}] and [{index}]"
            )
        seen[key] = index


def _sanitize_operational_fields(record, index):
    cleaned = dict(record)
    issues = []
    project_id = cleaned.get("project_id")
    if project_id is not None and (
            not isinstance(project_id, str) or not project_id.strip()):
        cleaned.pop("project_id", None)
        issues.append(f"[{index}] invalid 'project_id'; regenerated")
    if cleaned.get("status", "idea") not in _PROJECT_STATUSES:
        cleaned["status"] = "idea"
        issues.append(f"[{index}] invalid 'status'; reset")
    pinned = cleaned.get("pinned")
    if pinned is not None and not isinstance(pinned, bool):
        cleaned["pinned"] = False
        issues.append(f"[{index}] invalid 'pinned'; reset")
    broken = cleaned.get("broken")
    if broken is not None and not isinstance(broken, bool):
        cleaned.pop("broken", None)
        issues.append(f"[{index}] invalid 'broken'; ignored")
    if "ignored" in cleaned and not isinstance(cleaned["ignored"], bool):
        # An uncertain exclusion decision must fail closed. Keep the record
        # retained and explicitly report the malformed source, but never turn
        # it into an active Project by resetting it to False.
        cleaned["ignored"] = True
        issues.append(f"[{index}] invalid 'ignored'; preserved as ignored")
    for field in _COUNT_FIELDS:
        value = cleaned.get(field)
        if value is not None and (
                not isinstance(value, int) or isinstance(value, bool)
                or value < 0):
            cleaned[field] = None
            issues.append(f"[{index}] invalid '{field}'; reset to unknown")
    return cleaned, issues


def _validated_workspaces(data, issues=None):
    valid = []
    seen_ids = set()
    for index, workspace in enumerate(data.get("workspaces", [])):
        if not isinstance(workspace, dict):
            if issues is not None:
                issues.append(f"workspace [{index}] is not an object")
            continue
        workspace_issues = validate_workspace(workspace)
        workspace_key = workspace.get("workspace_id")
        if isinstance(workspace_key, str) and workspace_key.strip():
            workspace_key = workspace_key.strip()
            if workspace_key in seen_ids:
                workspace_issues.append("duplicate workspace_id")
        if workspace_issues:
            if issues is not None:
                issues.extend(
                    f"workspace [{index}] {reason}"
                    for reason in workspace_issues)
            continue
        seen_ids.add(workspace_key)
        valid.append(dict(workspace))
    return valid


def validate_registry(data):
    """Validate a parsed repos.json payload.

    Return (project_records, issues). Projects need a non-empty name and
    at least one valid path or folder_path; folder-only projects are valid.
    Duplicate identities are rejected, operational fields are sanitized,
    and unknown fields are preserved. Issues describe rejected projects or
    workspaces and sanitized fields, not a count of dropped records.
    Raise RegistryCorrupt for invalid structure, schema, or count limits.
    """
    if not isinstance(data, dict):
        raise RegistryCorrupt("top level is not an object")
    if "schema_version" in data and data["schema_version"] != SCHEMA_VERSION:
        raise RegistryCorrupt(f"unsupported schema_version {data['schema_version']!r}")
    projects = data.get("projects")
    if not isinstance(projects, list):
        raise RegistryCorrupt("'projects' is missing or not a list")
    if len(projects) > MAX_PROJECTS:
        raise RegistryCorrupt("'projects' exceeds count limit")
    workspaces = data.get("workspaces", [])
    if not isinstance(workspaces, list):
        raise RegistryCorrupt("'workspaces' is not a list")
    if len(workspaces) > MAX_WORKSPACES:
        raise RegistryCorrupt("'workspaces' exceeds count limit")
    for workspace in workspaces:
        if (isinstance(workspace, dict)
                and isinstance(workspace.get("members"), list)
                and len(workspace["members"]) > MAX_WORKSPACE_MEMBERS):
            raise RegistryCorrupt("Workspace members exceed count limit")
    records = []
    issues = []
    seen_ids = set()
    seen_paths = set()
    for i, rec in enumerate(projects):
        if not isinstance(rec, dict):
            issues.append(f"[{i}] record is not an object")
            continue
        path = rec.get("path")
        folder_path = rec.get("folder_path")
        name = rec.get("name")
        if path is not None and (not isinstance(path, str) or not path.strip()):
            issues.append(f"[{i}] invalid 'path'")
            continue
        if folder_path is not None and (not isinstance(folder_path, str) or not folder_path.strip()):
            issues.append(f"[{i}] invalid 'folder_path'")
            continue
        if (not isinstance(path, str) or not path.strip()) and (not isinstance(folder_path, str) or not folder_path.strip()):
            issues.append(f"[{i}] missing Project folder or repository 'path'")
            continue
        if not isinstance(name, str) or not name.strip():
            issues.append(f"[{i}] missing or invalid 'name'")
            continue
        rec, operational_issues = _sanitize_operational_fields(rec, i)
        issues.extend(operational_issues)
        project_id = rec.get("project_id")
        if isinstance(project_id, str) and project_id.strip():
            project_id = project_id.strip()
            if project_id in seen_ids:
                issues.append(f"[{i}] duplicate 'project_id'; record dropped")
                continue
        target_path = path if isinstance(path, str) and path.strip() else folder_path
        canonical_path = _canonical_registry_path(target_path)
        if canonical_path in seen_paths:
            issues.append(f"[{i}] duplicate Project path; record dropped")
            continue
        rec, launcher_issues = _sanitize_custom_launchers(rec, i)
        issues.extend(launcher_issues)
        if "fingerprint" in rec:
            cleaned = sanitize_fingerprint(rec["fingerprint"])
            if cleaned is None:
                log.warning("record [%d] fingerprint malformed; dropped", i)
                rec = {k: v for k, v in rec.items()
                       if k != "fingerprint"}
            else:
                rec = {**rec, "fingerprint": cleaned}
        if "moved_from" in rec and not isinstance(rec["moved_from"], str):
            issues.append(f"[{i}] invalid 'moved_from'; ignored")
            rec = {k: v for k, v in rec.items() if k != "moved_from"}
        records.append(rec)
        if isinstance(project_id, str):
            seen_ids.add(project_id)
        seen_paths.add(canonical_path)
    _validated_workspaces(data, issues)
    return records, issues


def _backup_paths():
    return [Path(str(REPOS_FILE) + ".bak1"), Path(str(REPOS_FILE) + ".bak2")]


def _rotate_backups():
    """bak2 <- bak1 <- copy of the committed primary.

    Called immediately after the authoritative commit (os.replace of the
    primary) so the newest backup generation mirrors the latest
    successfully committed Registry; older generations still preserve
    earlier valid states. The primary is copied (never renamed away) and
    rotation is best effort: a degraded backup never rolls back or
    falsely invalidates the commit that already replaced the primary.
    """
    b1, b2 = _backup_paths()
    primary = Path(REPOS_FILE)
    try:
        if b1.exists():
            os.replace(b1, b2)
    except OSError as e:
        log.warning("backup rotation degraded (bak2): %s", e)
    try:
        if primary.exists():
            shutil.copyfile(primary, b1)
    except OSError as e:
        log.warning("backup rotation degraded (bak1): %s", e)


def _quarantine(raw):
    """Preserve corrupt bytes in a timestamped sidecar, avoiding observed names."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = Path(str(REPOS_FILE) + f".corrupt-{stamp}")
    n = 0
    while target.exists():
        n += 1
        target = Path(str(REPOS_FILE) + f".corrupt-{stamp}-{n}")
    try:
        ensure_dirs()
        target.write_bytes(raw)
        log.warning("corrupt registry quarantined at %s", target)
        return target
    except OSError as e:
        log.error("could not quarantine corrupt registry: %s", e)
        return None


def _load_backup():
    """First valid backup as (records, reasons, source-name, workspaces), or None."""
    for cand in _backup_paths():
        try:
            raw = cand.read_bytes()
            if len(raw) > MAX_REGISTRY_BYTES:
                raise RegistryCorrupt("registry exceeds size limit")
            parsed = json.loads(raw.decode("utf-8"))
            records, reasons = validate_registry(parsed)
        except FileNotFoundError:
            continue
        except (ValueError, UnicodeDecodeError, RegistryCorrupt, OSError,
                RecursionError):
            log.warning("backup %s is invalid; skipped", cand.name)
            continue
        workspaces = _validated_workspaces(parsed)
        for workspace in workspaces:
            ensure_workspace_id(workspace)
        return records, reasons, cand.name, workspaces
    return None


def _derived_project_id(record):
    """Derive a repeatable legacy ID when UUID persistence is unavailable."""
    location = record.get("path") or record.get("folder_path") or ""
    canonical = (_canonical_registry_path(location)
                 if isinstance(location, str) and location
                 else str(location))
    # Derive identity from the recorded project location. Git observations can
    # change between recovery reads even when that location stays unchanged.
    digest = hashlib.sha256(
        ("legacy-path:" + canonical).encode("utf-8")
    ).hexdigest()
    return "legacy-" + digest


def _backfill_and_persist(records, *, establish_primary=True, workspaces=None,
                          force_persist=False):
    """Assign stable legacy IDs and persist the complete recovered registry."""
    changed = False
    for record in records:
        if not isinstance(record.get("project_id"), str) or not record["project_id"].strip():
            record["project_id"] = _derived_project_id(record)
            changed = True
    persisted = False
    if establish_primary and (changed or force_persist):
        save_projects(records, workspaces=workspaces)
        persisted = True
    return {"identity_derived": changed, "persisted": persisted}


def _recovery_backfill(records, workspaces):
    """Backfill recovery IDs without hiding an unsuccessful primary repair."""
    try:
        result = _backfill_and_persist(
            records, workspaces=workspaces, force_persist=True)
    except (OSError, RegistryCorrupt) as exc:
        log.error("could not establish recovered registry primary: %s", exc)
        result = {
            "identity_derived": any(
                isinstance(record.get("project_id"), str)
                and record["project_id"].startswith("legacy-")
                for record in records),
            "persisted": False,
            "persistence_error": str(exc),
        }
    return result


def read_registry():
    """Load the project and Workspace registry with corruption handling.

    Returns (projects, report). The compatibility field 'dropped' counts
    reported validation issues, including field sanitization.
    report.status is one of:
    fresh | valid | recovered | unrecoverable | unavailable.
    write_blocked means the caller must reload before scanning or saving.
    A transiently unreadable file (lock/sync placeholder) falls back to
    backups without quarantining anything — the original stays untouched.
    """
    empty_report = {"status": "fresh", "source": None, "quarantined": None,
                    "dropped": 0, "reasons": [], "workspaces": [],
                    "identity_durable": True}
    primary = Path(REPOS_FILE)
    try:
        raw = primary.read_bytes()
    except FileNotFoundError:
        # Defense-in-depth: a missing primary (external deletion, or a state
        # left by a pre-atomic-save crash) recovers from the newest valid
        # backup instead of silently starting fresh and losing user data.
        got = _load_backup()
        if got is not None:
            records, reasons, src, recovered_workspaces = got
            _cache_recovered_workspaces(recovered_workspaces)
            log.warning("registry primary missing; recovered from %s", src)
            backfill = _recovery_backfill(records, recovered_workspaces)
            report = {"status": "recovered", "source": src,
                      "quarantined": None, "dropped": len(reasons),
                      "reasons": reasons, "workspaces": recovered_workspaces,
                      "identity_durable": backfill["persisted"],
                      "identity_source": ("derived" if
                                          backfill["identity_derived"]
                                          else "backup")}
            if backfill.get("persistence_error"):
                report["persistence_error"] = backfill["persistence_error"]
                report["write_blocked"] = True
            return records, report
        return [], empty_report
    except OSError as exc:
        log.warning("registry file unreadable (%s); trying backups", exc)
        got = _load_backup()
        if got is None:
            log.error("registry unreadable and no valid backup")
            return [], {"status": "unavailable", "source": None,
                        "quarantined": None, "dropped": 0, "workspaces": [],
                        "write_blocked": True,
                        "reasons": [f"unreadable: {exc}"]}
        records, reasons, src, recovered_workspaces = got
        _cache_recovered_workspaces(recovered_workspaces)
        backfill = _backfill_and_persist(
            records, establish_primary=False, workspaces=recovered_workspaces)
        return records, {"status": "recovered", "source": src,
                         "write_blocked": True,
                         "quarantined": None, "dropped": len(reasons),
                         "reasons": [f"unreadable: {exc}", *reasons],
                         "workspaces": recovered_workspaces,
                         "identity_durable": not backfill["identity_derived"],
                         "identity_source": ("derived" if
                                             backfill["identity_derived"]
                                             else "backup")}
    try:
        if len(raw) > MAX_REGISTRY_BYTES:
            raise RegistryCorrupt("registry exceeds size limit")
        parsed = json.loads(raw.decode("utf-8"))
        records, reasons = validate_registry(parsed)
        persisted_workspaces = _validated_workspaces(parsed)
        for workspace in persisted_workspaces:
            ensure_workspace_id(workspace)
        if reasons:
            log.warning("registry load reported %d validation issue(s): %s",
                        len(reasons), "; ".join(reasons))
        _cache_recovered_workspaces(persisted_workspaces)
        _backfill_and_persist(records, workspaces=persisted_workspaces)
        return records, {"status": "valid", "source": "primary",
                         "quarantined": None, "dropped": len(reasons),
                         "reasons": reasons, "workspaces": persisted_workspaces,
                         "identity_durable": True, "identity_source": "primary"}
    except (ValueError, UnicodeDecodeError, RegistryCorrupt,
            RecursionError) as exc:
        qpath = _quarantine(raw)
        detail = str(exc)

    got = _load_backup()
    if got is None:
        log.critical("registry invalid and no valid backup (%s); "
                     "reload required", detail)
        return [], {"status": "unrecoverable", "source": None,
                    "quarantined": str(qpath) if qpath else None,
                    "dropped": 0, "reasons": [detail], "workspaces": [],
                    "write_blocked": True}
    records, reasons, src, recovered_workspaces = got
    _cache_recovered_workspaces(recovered_workspaces)
    if reasons:
        log.warning("recovered registry reported %d validation issue(s): %s",
                    len(reasons), "; ".join(reasons))
    log.warning("registry recovered from %s (primary was corrupt: %s)",
                src, detail)
    backfill = _recovery_backfill(records, recovered_workspaces)
    report = {"status": "recovered", "source": src,
              "quarantined": str(qpath) if qpath else None,
              "dropped": len(reasons), "reasons": reasons,
              "workspaces": recovered_workspaces,
              "identity_durable": backfill["persisted"],
              "identity_source": ("derived" if
                                  backfill["identity_derived"]
                                  else "backup")}
    if backfill.get("persistence_error"):
        report["persistence_error"] = backfill["persistence_error"]
        report["write_blocked"] = True
    return records, report


def _sanitize_settings(stored):
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    if isinstance(stored, dict):
        settings.update(stored)
    if not isinstance(settings["depth"], int) or isinstance(
            settings["depth"], bool) or not 1 <= settings["depth"] <= 10:
        settings["depth"] = DEFAULT_SETTINGS["depth"]
    for key in ("roots", "skip_dirs"):
        val = settings.get(key)
        if not isinstance(val, list) or not all(
                isinstance(v, str) and v.strip() for v in val):
            settings[key] = list(DEFAULT_SETTINGS[key])
    if settings.get("theme", "dark") not in {"dark", "light"}:
        settings["theme"] = "dark"
    sort = settings.get("sort")
    if (sort is not None
            and (not isinstance(sort, list) or len(sort) != 2
                 or not isinstance(sort[0], str)
                 or not isinstance(sort[1], bool))):
        settings.pop("sort", None)
    widths = settings.get("column_widths")
    if widths is not None:
        if not isinstance(widths, dict):
            settings["column_widths"] = {}
        else:
            settings["column_widths"] = {
                key: value for key, value in widths.items()
                if isinstance(key, str) and isinstance(value, int)
                and not isinstance(value, bool)
            }
    supp = settings.get("move_suppressions")
    if not isinstance(supp, list):
        settings["move_suppressions"] = []
    else:
        settings["move_suppressions"] = [
            {"old": s["old"], "new": s["new"]} for s in supp
            if isinstance(s, dict) and isinstance(s.get("old"), str)
            and isinstance(s.get("new"), str)]
    agent_cmd = settings.get("agent_cmd")
    if (not isinstance(agent_cmd, str) or not agent_cmd.strip()
            or len(agent_cmd) > 4096
            or any(ord(char) < 32 for char in agent_cmd)):
        settings["agent_cmd"] = DEFAULT_SETTINGS["agent_cmd"]
    if not settings["roots"]:
        home = Path.home()
        settings["roots"] = [
            str(home / "Desktop"),
            str(home / "Documents"),
            str(home / "Downloads"),
        ]
    return settings


def load_settings():
    return _sanitize_settings(_load_settings_payload())


def save_settings(settings):
    global _SETTINGS_REPORT
    _preserve_settings_before_write()
    _write_json(SETTINGS_FILE, _sanitize_settings(settings))
    _SETTINGS_REPORT = {"status": "valid", "quarantined": None,
                        "reasons": [], "preservation_failed": False}


def load_projects():
    """Compatibility wrapper: projects only, no corruption handling report."""
    projects, report = read_registry()
    if report["status"] == "unavailable":
        raise OSError("Registry unavailable; reload before using project state")
    return projects


def load_workspaces():
    """Load valid Workspace metadata from the shared registry."""
    _projects, report = read_registry()
    if report["status"] == "unavailable":
        raise OSError("Registry unavailable; reload before using Workspace state")
    return report.get("workspaces", [])


def save_workspaces(workspaces, projects=None):
    """Persist Workspace metadata with the caller's current Project state.

    Legacy callers may omit ``projects`` and retain the former disk-read
    behavior. The GUI passes its live list so a pending curation debounce
    cannot be replaced temporarily by an older registry snapshot.
    """
    if projects is None:
        projects, report = read_registry()
        if report.get("write_blocked"):
            raise OSError("Registry recovery required before saving Workspaces")
    save_projects(projects, workspaces=workspaces)


def _persisted_workspaces():
    """Read workspaces from the primary, or the recovery cache if absent.

    This raw read avoids recursing through read_registry, whose recovery
    can call save_projects. An unreadable or malformed primary aborts the
    save; treating either as an empty list would erase Workspace metadata.
    """
    try:
        data = json.loads(Path(REPOS_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _cached_recovered_workspaces()
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise RegistryCorrupt(
            "cannot preserve workspaces from malformed primary") from exc
    if not isinstance(data, dict):
        raise RegistryCorrupt(
            "cannot preserve workspaces from malformed primary")
    if "workspaces" not in data:
        return []
    value = data["workspaces"]
    if not isinstance(value, list):
        raise RegistryCorrupt(
            "cannot preserve workspaces from malformed primary")
    return list(value)


def save_projects(projects, workspaces=None):
    """Replace the registry using a flushed temporary file and os.replace.

    Calls within this process are serialized. The new file is written,
    fsynced, and replaces the primary atomically; backup rotation follows
    the commit so the newest backup generation mirrors the latest
    committed Registry. Failures before replacement leave an existing
    primary and its backups in place; post-commit backup maintenance is
    best effort. This does not promise durability across every filesystem
    or power failure.

    When ``workspaces`` is None, preserve workspaces from the primary or,
    if it is absent, the last recovery cache. Pass ``workspaces=[]`` to clear
    them explicitly.
    """
    with _REGISTRY_SAVE_LOCK:
        _reject_duplicate_project_paths(projects)
        ensure_dirs()
        data = {"schema_version": SCHEMA_VERSION, "projects": projects}
        if workspaces is not None:
            data["workspaces"] = list(workspaces)
        else:
            data["workspaces"] = _persisted_workspaces()
        try:
            current = json.loads(REPOS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError, RecursionError):
            current = None
        if current == data and _load_backup() is not None:
            _cache_committed_workspaces(data["workspaces"])
            return
        tmp = REPOS_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, REPOS_FILE)
        # Rotation follows the authoritative commit so bak1 always mirrors
        # the latest committed Registry; a later recovery from bak1 can then
        # never resurrect a state that the caller already replaced. Post-
        # commit maintenance is best effort: a backup failure must neither
        # roll back nor falsely invalidate the commit above.
        try:
            _rotate_backups()
        except Exception:
            log.exception("registry committed but backup rotation failed")
        _cache_committed_workspaces(data["workspaces"])


def _note_slug(name):
    return "".join(
        c if c.isalnum() or c in "-_." else "-" for c in str(name)
    ).strip("-.") or "project"


def _legacy_note_path_for(name, path):
    """Six-hex note path retained for compatibility with older registries."""
    h = hashlib.sha1(str(path).lower().encode()).hexdigest()[:6]
    return NOTES_DIR / f"{_note_slug(name)}-{h}.md"


def note_path_for(name, path, project_id=None):
    """Deterministic markdown note file for a stable Project identity.

    Current application calls provide ``project_id`` so renames, moves, and
    reassociation cannot change the note key. The path-based form remains for
    legacy callers but uses the full SHA-1 digest rather than the historical
    six-hex truncation.
    """
    if isinstance(project_id, str) and project_id.strip():
        h = hashlib.sha256(
            ("project-id:" + project_id.strip()).encode("utf-8")
        ).hexdigest()
        return NOTES_DIR / f"project-{h}.md"
    h = hashlib.sha1(str(path).lower().encode()).hexdigest()
    return NOTES_DIR / f"{_note_slug(name)}-{h}.md"


def load_note(name, path, project_id=None):
    current = note_path_for(name, path, project_id)
    legacy = _legacy_note_path_for(name, path)
    for candidate in (current, legacy):
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return ""


_NOTE_SAVE_LOCK = threading.RLock()


def save_note(name, path, text, project_id=None):
    p = note_path_for(name, path, project_id)
    with _NOTE_SAVE_LOCK:
        ensure_dirs()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)


def move_note(old_name, old_path, new_name, new_path, project_id=None):
    """Re-key a note after a user-confirmed move.

    Returns one of: "absent" (nothing to move), "moved", "collision"
    (destination existed; source preserved beside it with a suffix).
    Detected destination collisions are routed to a separate note file.
    The existence checks do not reserve names against concurrent writers.
    """
    stable = note_path_for(old_name, old_path, project_id) \
        if project_id else None
    if stable is not None and stable.exists():
        return "absent"
    src = next((candidate for candidate in (
        note_path_for(old_name, old_path),
        _legacy_note_path_for(old_name, old_path),
    ) if candidate.exists()), None)
    if src is None:
        return "absent"
    dst = note_path_for(new_name, new_path, project_id)
    ensure_dirs()
    legacy_dst = _legacy_note_path_for(new_name, new_path)
    if dst.exists() or legacy_dst.exists():
        stem = dst.stem + "-moved-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        sidecar = dst.with_name(stem + ".md")
        n = 0
        while sidecar.exists():
            n += 1
            sidecar = dst.with_name(f"{stem}-{n}.md")
        os.replace(src, sidecar)
        log.warning("note collision on move: kept as %s", sidecar.name)
        return "collision"
    os.replace(src, dst)
    return "moved"
