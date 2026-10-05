"""Shared scan/live Project reconciliation for both presentations."""
from . import projects


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
        return (projects.project_location_key(project)
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
