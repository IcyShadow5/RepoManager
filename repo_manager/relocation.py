"""Confirmed repository relocation transactions shared by desktop presentations."""
import logging
from dataclasses import dataclass
from pathlib import Path

from . import projects, store
from .projects import project_id as _project_id, project_location_key, repository_path_key

log = logging.getLogger("repomanager")

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


def _counterpart_is_disposable(counterpart, new_path):
    """Whether a scan-created new-path occupant may be absorbed.

    A genuinely fresh counterpart carries only scanner defaults. Any
    user-owned curation — status/focus/pin/ignore/launchers/name/moved_from —
    or an owned note file makes it non-disposable, so absorption can never
    discard user state. Fail-closed on unreadable note locations.
    """
    if not isinstance(counterpart, dict):
        return False
    if not projects.counterpart_is_pristine(counterpart, new_path):
        return False
    counterpart_id = _project_id(counterpart)
    counterpart_name = counterpart.get("name")
    try:
        candidates = (
            store.note_path_for(counterpart_name, new_path, counterpart_id),
            store.note_path_for(counterpart_name, new_path),
            store._legacy_note_path_for(counterpart_name, new_path),
        )
    except Exception:
        return False
    for candidate in candidates:
        try:
            if candidate.exists():
                return False
        except OSError:
            return False
    return True


def detach_pending_for_keep_both(projects, suggestion):
    """Remove pending_move relations covered by a Keep Both choice.

    Returns a list of ``(record, previous_pending)`` pairs so the caller
    can restore them if persistence fails. Only records whose location
    matches one of the suggestion's old paths are touched.
    """
    olds = set()
    for candidate in ([suggestion.get("old_path")]
                      + list(suggestion.get("old_paths") or [])):
        if isinstance(candidate, str) and candidate:
            olds.add(candidate.lower())
    detached = []
    for record in projects:
        if not isinstance(record, dict) or "pending_move" not in record:
            continue
        location = record.get("path") or record.get("folder_path")
        if isinstance(location, str) and location.lower() in olds:
            detached.append((record, record.pop("pending_move")))
    return detached


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
    if entry is not None and suggestion.get("old_project_id") is not None and (
            _project_id(entry) != suggestion["old_project_id"]):
        return MoveOutcome(MOVE_STALE_TARGET, ERROR_IDENTITY_MISMATCH,
                           ("approved source Project identity changed",))
    if entry is None:
        return MoveOutcome(MOVE_MISSING_OLD, ERROR_TARGET_MISSING,
                           ("approved old registry entry is missing",))
    new_path_key = repository_path_key(new_path)
    occupant = next((p for p in projects if p is not entry
                     and project_location_key(p) == new_path_key), None)
    expected_counterpart_id = suggestion.get("new_project_id")
    if occupant is not None and (
            not isinstance(expected_counterpart_id, str)
            or not expected_counterpart_id.strip()
            or _project_id(occupant) != expected_counterpart_id):
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

    # R2.6B counterpart absorption: the occupant may only be removed when it
    # is still the exact expected scan-created counterpart AND untouched by
    # user curation. Anything else keeps the generic duplicate protection.
    counterpart_index = None
    counterpart_snapshot = None
    if occupant is not None:
        try:
            counterpart_index = next(
                i for i, p in enumerate(projects) if p is occupant)
        except StopIteration:
            return MoveOutcome(MOVE_DUPLICATE, ERROR_IDENTITY_MISMATCH,
                               ("expected counterpart left the registry",))
        if _project_id(occupant) != expected_counterpart_id:
            return MoveOutcome(MOVE_DUPLICATE, ERROR_IDENTITY_MISMATCH,
                               ("new path occupant is not the expected "
                                "scan-created counterpart",))
        if not _counterpart_is_disposable(occupant, new_path):
            return MoveOutcome(MOVE_DUPLICATE, ERROR_IDENTITY_MISMATCH,
                               ("new path occupant has user-owned state; "
                                "kept without absorption",))
        counterpart_snapshot = dict(occupant)
        del projects[counterpart_index]

    def restore_counterpart():
        if counterpart_snapshot is None:
            return
        snapshot_id = counterpart_snapshot.get("project_id")
        if any(isinstance(p, dict) and p.get("project_id") == snapshot_id
               for p in projects):
            return
        projects.insert(min(counterpart_index, len(projects)),
                        dict(counterpart_snapshot))

    absorbed_evidence = ()
    if counterpart_snapshot is not None:
        absorbed_evidence = (f"absorbed scan-created counterpart "
                             f"{expected_counterpart_id}",)

    snapshot = dict(entry)
    old_name = entry["name"]
    new_name = Path(new_path).name
    entry["moved_from"] = old_path
    entry["path"] = new_path
    entry["name"] = new_name
    entry["last_seen"] = now_iso
    # R2.6C: the relocation is resolved; the durable pending relation ends
    # with the successful state transition (snapshot above still carries it
    # for rollback).
    entry.pop("pending_move", None)

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
            restore_counterpart()
            return MoveOutcome(
                MOVE_ROLLED_BACK_NOTE, ERROR_FILESYSTEM_FAILURE,
                ("stable note migration failed before registry mutation",))
        try:
            save_projects()
        except Exception as save_exc:
            log.exception("registry save failed during confirmed move")
            entry.clear()
            entry.update(snapshot)
            restore_counterpart()
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
                ("note destination collision preserved without overwrite",
                 *absorbed_evidence),
                entry)
        return MoveOutcome(MOVE_OK, None,
                           ("registry and stable note move completed",
                            *absorbed_evidence), entry)

    try:
        save_projects()
    except Exception:
        log.exception("registry save failed during confirmed move")
        entry.clear()
        entry.update(snapshot)
        restore_counterpart()
        return MoveOutcome(MOVE_ROLLED_BACK_REGISTRY,
                           ERROR_PERSISTENCE_FAILURE,
                           ("registry save failed; in-memory entry restored",))
    try:
        result = move_note(old_name, old_path, new_name, new_path)
    except Exception as note_exc:
        log.exception("note migration failed during confirmed move")
        entry.clear()
        entry.update(snapshot)
        restore_counterpart()
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
                           ("note destination collision preserved without overwrite",
                            *absorbed_evidence),
                           entry)
    return MoveOutcome(MOVE_OK, None, ("registry and note move completed",
                                       *absorbed_evidence), entry)


import copy

def approve_candidate(group, index, records):
    candidates = group.get("candidates") or []
    if not isinstance(index, int) or index < 0 or index >= len(candidates):
        raise ValueError("No proven candidate pair available; rescan or keep both")
    candidate = copy.deepcopy(candidates[index])
    old = candidate.get("old_path")
    new = candidate.get("new_path")
    source = next((record for record in records if record.get("path") == old), None)
    if source is None or not projects.project_id(source):
        raise ValueError("Source Project identity is unavailable")
    if not isinstance(candidate.get("identity"), dict):
        raise ValueError("Candidate identity evidence is unavailable; rescan")
    occupant = next((record for record in records if record.get("path") == new), None)
    if occupant is not None and projects.project_id(occupant) != candidate.get("new_project_id"):
        raise ValueError("Candidate has no proven scan-created counterpart; rescan or keep both")
    if candidate.get("old_project_id") != projects.project_id(source):
        raise ValueError("Source Project identity changed since discovery; rescan")
    candidate["manual_approval"] = True
    return candidate


def retire_accepted_pair(suggestions, accepted):
    """Retain unrelated candidate pairs/sources after a contested acceptance."""
    result = []
    for suggestion in suggestions:
        candidates = suggestion.get("candidates")
        if candidates:
            remaining = [pair for pair in candidates
                         if pair.get("old_path") != accepted.get("old_path")]
            if not remaining:
                continue
            updated = dict(suggestion, candidates=remaining)
            # Keep contested survivors manual, including singleton pairs.
            updated.pop("old_path", None)
            updated.pop("new_path", None)
            updated["old_paths"] = list(dict.fromkeys(pair["old_path"] for pair in remaining))
            updated["new_paths"] = list(dict.fromkeys(pair["new_path"] for pair in remaining))
            result.append(updated)
        elif suggestion.get("old_path") == accepted.get("old_path"):
            continue
        else:
            result.append(suggestion)
    return result
