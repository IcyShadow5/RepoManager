"""Read-only diagnostics for UA-05 synthetic Project contamination.

This module deliberately does not import the persistence recovery paths.  It
only reads registry/config/log artifacts and source text, then returns a
JSON-serializable report.  In particular, it never calls ``ensure_dirs``,
``read_registry``, ``save_*``, quarantine logic, a scanner, Git, or Tk.

Run from the repository root with::

    python -m repo_manager.ua05_diagnostics

Use ``--format json`` for machine-readable output.  The default application
folder is the same location used by RepoManager, but it is only inspected.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable


REPORT_VERSION = "ua05-diagnostic/v1"
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_SOURCE_FILE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_FILES = 10_000
MAX_LOG_MATCHES = 100
MAX_SOURCE_MATCHES = 250

_SYNTHETIC_NAME_RE = re.compile(r"^repo\d{5,}$", re.IGNORECASE)
_SYNTHETIC_PATH_RE = re.compile(
    r"^(?:[a-z]:[\\/]repos[\\/]|/repos/)", re.IGNORECASE)
_SYNTHETIC_SOURCE_RE = re.compile(r"\brepo\d{5,}\b", re.IGNORECASE)
_WINDOWS_REPOS_SOURCE_RE = re.compile(r"C:\\\\repos", re.IGNORECASE)
_FORMATTED_REPO_SOURCE_RE = re.compile(
    r"(?:repo|prefix).*\{[^\n}]*:\s*0?5d", re.IGNORECASE)
_ISOLATION_MARKER_RE = re.compile(
    r"(?:_mk_project|repo000|C:\\\\repos|TemporaryDirectory|"
    r"_IsolatedStorePaths|store\.(?:APP_DIR|REPOS_FILE|SETTINGS_FILE|NOTES_DIR))",
    re.IGNORECASE,
)
_PERSISTENCE_APIS = (
    "save_projects", "save_settings", "save_note", "save_workspaces",
    "read_registry", "load_projects", "load_settings", "load_workspaces",
)
_ISOLATED_PATH_RE = re.compile(
    r"(?:_IsolatedStorePaths|APP_DIR|REPOS_FILE|SETTINGS_FILE|NOTES_DIR|"
    r"patch\.multiple\(\s*store|patch\.object\(\s*store)",
    re.IGNORECASE,
)
_MOCKED_PERSISTENCE_RE = re.compile(
    r"(?:patch|mock\.patch)[^\n]*(?:save_projects|save_settings|"
    r"save_note|save_workspaces|read_registry|load_projects|load_settings)",
    re.IGNORECASE,
)


def default_app_dir() -> Path:
    """Return RepoManager's configured data directory without creating it."""
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "RepoManager"


def default_source_root() -> Path:
    """Return the checkout root containing this package."""
    return Path(__file__).resolve().parent.parent


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_stat(path: Path) -> dict[str, Any]:
    """Describe one path without following a content-specific operation."""
    result: dict[str, Any] = {"path": str(path), "exists": False}
    try:
        stat = path.stat()
    except FileNotFoundError:
        return result
    except OSError as exc:
        result.update({"exists": True, "status": "unreadable", "error": str(exc)})
        return result
    result.update({
        "exists": True,
        "status": "file" if path.is_file() else "directory" if path.is_dir() else "other",
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    })
    return result


def _read_limited(path: Path, limit: int) -> tuple[bytes | None, str | None]:
    """Read a bounded file, returning bytes or a non-fatal diagnostic error."""
    try:
        size = path.stat().st_size
        if size > limit:
            return None, f"file exceeds diagnostic limit ({size} > {limit} bytes)"
        return path.read_bytes(), None
    except FileNotFoundError:
        return None, "file does not exist"
    except OSError as exc:
        return None, str(exc)


def _artifact(path: Path, *, parse_json: bool = False) -> dict[str, Any]:
    result = _safe_stat(path)
    if not result["exists"] or result.get("status") != "file":
        return result
    raw, error = _read_limited(path, MAX_ARTIFACT_BYTES)
    if error:
        result.update({"status": "unreadable", "error": error})
        return result
    assert raw is not None
    result["sha256"] = hashlib.sha256(raw).hexdigest()
    if parse_json:
        try:
            result["json"] = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            result.update({"json_status": "invalid", "json_error": str(exc)})
        else:
            result["json_status"] = "valid"
    return result


def _artifact_paths(app_dir: Path, stem: str) -> list[Path]:
    """Return primary, backups, and quarantine artifacts in stable order."""
    primary = app_dir / stem
    backups = [app_dir / f"{stem}.bak1", app_dir / f"{stem}.bak2"]
    try:
        quarantined = sorted(
            app_dir.glob(f"{stem}.corrupt-*"), key=lambda item: str(item).casefold())
    except OSError:
        quarantined = []
    return [primary, *backups, *quarantined]


def _record_evidence(record: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    name = record.get("name")
    path = record.get("path") or record.get("folder_path")
    name_text = name.strip() if isinstance(name, str) else ""
    path_text = path.strip() if isinstance(path, str) else ""
    name_match = bool(name_text and _SYNTHETIC_NAME_RE.fullmatch(name_text))
    path_match = bool(path_text and _SYNTHETIC_PATH_RE.match(path_text))
    if not (name_match or path_match):
        return None
    return {
        "index": index,
        "name": name_text or None,
        "path": path_text or None,
        "project_id": record.get("project_id"),
        "name_pattern": name_match,
        "path_pattern": path_match,
    }


def _registry_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    """Summarize one parsed registry artifact without normalizing or saving it."""
    summary: dict[str, Any] = {
        key: artifact.get(key)
        for key in ("path", "exists", "status", "size", "mtime_ns", "sha256",
                    "json_status", "json_error")
        if key in artifact
    }
    parsed = artifact.get("json")
    if not isinstance(parsed, dict):
        if artifact.get("exists") and artifact.get("json_status") == "valid":
            summary["shape"] = "valid-json-non-object"
        return summary
    records = parsed.get("projects")
    summary["schema_version"] = parsed.get("schema_version")
    summary["projects_type"] = type(records).__name__
    summary["project_count"] = len(records) if isinstance(records, list) else None
    summary["workspace_count"] = (
        len(parsed["workspaces"]) if isinstance(parsed.get("workspaces"), list)
        else None
    )
    synthetic = []
    if isinstance(records, list):
        for index, record in enumerate(records):
            evidence = _record_evidence(record, index)
            if evidence is not None:
                synthetic.append(evidence)
    summary["synthetic_records"] = synthetic
    return summary


def _settings_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    summary = _registry_summary(artifact)
    parsed = artifact.get("json")
    if not isinstance(parsed, dict):
        return summary
    roots = parsed.get("roots")
    skip_dirs = parsed.get("skip_dirs")
    summary["roots"] = roots if isinstance(roots, list) else None
    summary["skip_dirs"] = skip_dirs if isinstance(skip_dirs, list) else None
    summary["synthetic_roots"] = [
        root for root in (roots or [])
        if isinstance(root, str)
        and (_SYNTHETIC_PATH_RE.match(root.strip())
             or "\\repos" in root.casefold())
    ]
    summary["move_suppressions_count"] = (
        len(parsed["move_suppressions"])
        if isinstance(parsed.get("move_suppressions"), list) else None
    )
    return summary


def _source_files(root: Path) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    errors: list[str] = []
    excluded = {
        ".git", "__pycache__", ".build-venv", "build", "dist",
        ".venv", "venv", "node_modules", ".tox", ".pytest_cache",
        ".mypy_cache", ".ruff_cache",
    }
    excluded_casefold = {name.casefold() for name in excluded}
    try:
        for current, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(
                (name for name in dirs if name.casefold() not in excluded_casefold),
                key=str.casefold)
            for name in sorted(names, key=str.casefold):
                path = Path(current) / name
                if path.suffix.casefold() not in {".py", ".md", ".txt", ".rst"}:
                    continue
                files.append(path)
                if len(files) >= MAX_SOURCE_FILES:
                    return files, errors + [
                        f"source file limit reached ({MAX_SOURCE_FILES})"]
    except OSError as exc:
        errors.append(f"source walk failed: {exc}")
    return files, errors


def _source_provenance(root: Path) -> dict[str, Any]:
    files, walk_errors = _source_files(root)
    test_hits: list[dict[str, Any]] = []
    production_hits: list[dict[str, Any]] = []
    isolation_hits: list[dict[str, Any]] = []
    isolation_review: list[dict[str, Any]] = []
    read_errors: list[str] = []
    diagnostic_path = Path(__file__).resolve()
    for path in files:
        # Do not let this helper's own detector regexes manufacture a
        # production provenance hit while the checkout is being inspected.
        if path.resolve() == diagnostic_path:
            continue
        raw, error = _read_limited(path, MAX_SOURCE_FILE_BYTES)
        if error:
            if error != "file does not exist":
                read_errors.append(f"{path}: {error}")
            continue
        assert raw is not None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        relative = str(path.relative_to(root))
        relative_posix = relative.replace("\\", "/")
        is_test = relative_posix.startswith("tests/")
        is_production = relative_posix.startswith("repo_manager/")
        persistence_reference = any(
            f"store.{api}" in text for api in _PERSISTENCE_APIS)
        if is_test and persistence_reference:
            isolated = bool(_ISOLATED_PATH_RE.search(text))
            mocked = bool(_MOCKED_PERSISTENCE_RE.search(text))
            if isolated:
                isolation_status = "path-isolated"
            elif mocked:
                isolation_status = "persistence-mocked"
            else:
                isolation_status = "manual-review"
            isolation_review.append({
                "file": relative,
                "status": isolation_status,
                "path_isolation_evidence": isolated,
                "mock_evidence": mocked,
            })
        for line_number, line in enumerate(text.splitlines(), 1):
            # Only concrete synthetic-name/path patterns are provenance.
            # Generic store.APP_DIR and persistence references are tracked
            # separately as isolation evidence and must never imply that
            # production code generates synthetic projects.
            provenance = []
            if (_SYNTHETIC_SOURCE_RE.search(line)
                    or _WINDOWS_REPOS_SOURCE_RE.search(line)
                    or _FORMATTED_REPO_SOURCE_RE.search(line)):
                provenance.append("synthetic-name-or-path-fixture")
            if _ISOLATION_MARKER_RE.search(line):
                isolation_hits.append({
                    "file": relative, "line": line_number,
                    "text": line.strip()[:300],
                })
            if not provenance:
                continue
            hit = {"file": relative, "line": line_number,
                   "text": line.strip()[:300], "markers": provenance}
            if is_test:
                target = test_hits
            elif is_production:
                target = production_hits
            else:
                # Keep the report focused on executable test/production
                # provenance rather than matching prose in docs or tooling.
                continue
            if len(target) < MAX_SOURCE_MATCHES:
                target.append(hit)
    return {
        "root": str(root),
        "files_considered": len(files),
        "test_hits": test_hits,
        "production_hits": production_hits,
        "isolation_hits": isolation_hits[:MAX_SOURCE_MATCHES],
        "isolation_review": isolation_review[:MAX_SOURCE_MATCHES],
        "walk_errors": walk_errors,
        "read_errors": read_errors[:MAX_SOURCE_MATCHES],
    }


def _log_matches(path: Path) -> dict[str, Any]:
    result = _safe_stat(path)
    result["matches"] = []
    if not result.get("exists") or result.get("status") != "file":
        return result
    raw, error = _read_limited(path, MAX_ARTIFACT_BYTES)
    if error:
        result.update({"status": "unreadable", "error": error})
        return result
    assert raw is not None
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception as exc:  # defensive; bytes.decode(errors=replace) is total
        result["error"] = str(exc)
        return result
    for line_number, line in enumerate(text.splitlines(), 1):
        if (_SYNTHETIC_SOURCE_RE.search(line)
                or _WINDOWS_REPOS_SOURCE_RE.search(line)):
            if len(result["matches"]) >= MAX_LOG_MATCHES:
                break
            result["matches"].append({
                "line": line_number, "text": line.strip()[:500],
            })
    result["match_count"] = len(result["matches"])
    return result


def _conclusions(
    registry_summaries: Iterable[dict[str, Any]],
    settings_summaries: Iterable[dict[str, Any]],
    provenance: dict[str, Any],
    logs: dict[str, Any],
) -> list[dict[str, Any]]:
    registries = list(registry_summaries)
    synthetic = [
        record for artifact in registries
        for record in artifact.get("synthetic_records", [])
    ]
    primary = registries[0] if registries else {}
    primary_synthetic = primary.get("synthetic_records", [])
    test_hits = provenance.get("test_hits", [])
    production_hits = provenance.get("production_hits", [])
    settings_synthetic = [
        root for artifact in settings_summaries
        for root in artifact.get("synthetic_roots", [])
    ]
    log_match_count = logs.get("match_count", 0)
    isolation_review = provenance.get("isolation_review", [])
    isolation_manual_review = [
        item for item in isolation_review if item.get("status") == "manual-review"
    ]
    if primary_synthetic:
        persisted_status = "present"
        persisted_confidence = "high"
    elif primary.get("json_status") == "valid":
        persisted_status = "absent"
        persisted_confidence = "high"
    else:
        persisted_status = "indeterminate"
        persisted_confidence = "low"
    if production_hits:
        scanner_status = "production-source-match"
        scanner_confidence = "medium"
    elif test_hits:
        scanner_status = "no-production-match-test-fixture-present"
        scanner_confidence = "high"
    else:
        scanner_status = "no-source-match"
        scanner_confidence = "medium"
    return [
        {
            "code": "persisted_synthetic_records",
            "status": persisted_status,
            "confidence": persisted_confidence,
            "evidence": primary_synthetic or synthetic[:20],
        },
        {
            "code": "configured_synthetic_roots",
            "status": "present" if settings_synthetic else "absent-or-unreadable",
            "confidence": "high" if settings_synthetic else "medium",
            "evidence": settings_synthetic,
        },
        {
            "code": "current_source_provenance",
            "status": scanner_status,
            "confidence": scanner_confidence,
            "evidence": (production_hits or test_hits)[:20],
        },
        {
            "code": "historical_log_provenance",
            "status": "matches-found" if log_match_count else "no-matches-found",
            "confidence": "medium",
            "evidence": logs.get("matches", [])[:20],
        },
        {
            "code": "test_isolation_review",
            "status": ("manual-review-required" if isolation_manual_review
                       else "static-isolation-evidence" if isolation_review
                       else "no-persistence-test-references"),
            "confidence": "medium",
            "evidence": (isolation_manual_review or isolation_review)[:20],
        },
    ]


def build_report(
    app_dir: str | Path | None = None,
    *,
    source_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build a bounded UA-05 report using read-only filesystem operations."""
    app = Path(app_dir) if app_dir is not None else default_app_dir()
    source = Path(source_root) if source_root is not None else default_source_root()
    registry_artifacts = [
        _artifact(path, parse_json=True) for path in _artifact_paths(app, "repos.json")
    ]
    settings_artifacts = [
        _artifact(path, parse_json=True) for path in _artifact_paths(app, "settings.json")
    ]
    registry_summaries = [_registry_summary(item) for item in registry_artifacts]
    settings_summaries = [_settings_summary(item) for item in settings_artifacts]
    log_artifact = _log_matches(app / "repo_manager.log")
    provenance = _source_provenance(source)
    return {
        "report_version": REPORT_VERSION,
        "generated_at": _iso_now(),
        "read_only": True,
        "app_dir": str(app),
        "source_root": str(source),
        "artifacts": {
            "registry": registry_summaries,
            "settings": settings_summaries,
            "log": log_artifact,
        },
        "source_provenance": provenance,
        "conclusions": _conclusions(
            registry_summaries, settings_summaries, provenance, log_artifact),
        "limitations": [
            "A current registry proves what is persisted, not which process wrote it.",
            "A source fixture proves current provenance, not historical execution.",
            "No repository scan or Git command is performed, so scanner reachability is assessed statically only.",
            "The report does not clean, quarantine, rewrite, or otherwise mutate any artifact.",
        ],
    }


def render_text(report: dict[str, Any]) -> str:
    """Render a concise human-readable report without dumping full JSON."""
    lines = [
        "RepoManager UA-05 read-only diagnostic",
        f"Report: {report.get('report_version')} · generated {report.get('generated_at')}",
        f"Application data: {report.get('app_dir')}",
        f"Source root: {report.get('source_root')}",
        "",
        "Conclusions:",
    ]
    for item in report.get("conclusions", []):
        lines.append(
            f"- {item.get('code')}: {item.get('status')} "
            f"(confidence {item.get('confidence')})"
        )
    registry = report.get("artifacts", {}).get("registry", [])
    lines.append("\nRegistry artifacts:")
    for item in registry:
        path = item.get("path")
        status = item.get("status", "missing")
        count = item.get("project_count")
        synthetic = len(item.get("synthetic_records", []))
        suffix = f", {count} projects, {synthetic} synthetic matches" \
            if count is not None else ""
        lines.append(f"- {path}: {status}{suffix}")
    settings = report.get("artifacts", {}).get("settings", [])
    lines.append("\nSettings artifacts:")
    for item in settings:
        lines.append(
            f"- {item.get('path')}: {item.get('status', 'missing')}"
            f" ({len(item.get('synthetic_roots', []))} synthetic-root matches)"
        )
    provenance = report.get("source_provenance", {})
    lines.extend(("\nSource provenance:",
                  f"- test hits: {len(provenance.get('test_hits', []))}",
                  f"- production hits: {len(provenance.get('production_hits', []))}",
                  f"- isolation markers: {len(provenance.get('isolation_hits', []))}",
                  f"- persistence isolation reviews: {len(provenance.get('isolation_review', []))}",
                  f"- manual-review candidates: {sum(item.get('status') == 'manual-review' for item in provenance.get('isolation_review', []))}"))
    log = report.get("artifacts", {}).get("log", {})
    lines.append(f"- log synthetic matches: {log.get('match_count', 0)}")
    lines.extend(("", "No files were changed by this diagnostic."))
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only RepoManager UA-05 registry/config provenance diagnostic.")
    parser.add_argument(
        "--app-dir", type=Path, default=None,
        help="RepoManager data directory (default: %%LOCALAPPDATA%%/RepoManager).")
    parser.add_argument(
        "--source-root", type=Path, default=None,
        help="Checkout/source root to inspect for fixture provenance.")
    parser.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="Output format (default: text).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = build_report(args.app_dir, source_root=args.source_root)
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
