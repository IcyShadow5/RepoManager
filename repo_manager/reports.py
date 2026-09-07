"""Timestamped reports and exports with provenance and stable serialization."""

import json
import html
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from . import health, intelligence

SCHEMA_VERSION = 1
EXPORT_KIND = "RepoManager Project Export"

_SECRET_KEY_PARTS = ("token", "secret", "password", "credential", "api_key", "apikey", "private_key")


def _safe(value: Any, key: str = "", *, _seen: set[int] | None = None,
          _depth: int = 0) -> Any:
    """Filter credential-like keys and bound serialization depth/cycles.

    Arbitrary string values are not scanned for embedded credentials.
    """
    if any(part in key.lower() for part in _SECRET_KEY_PARTS):
        return None
    if _depth > 32:
        return "[depth limit]"
    seen = _seen if _seen is not None else set()
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in seen:
            return "[cycle]"
        seen.add(marker)
        try:
            return {
                str(k): _safe(v, str(k), _seen=seen, _depth=_depth + 1)
                for k, v in value.items()
                if not any(part in str(k).lower()
                           for part in _SECRET_KEY_PARTS)
            }
        finally:
            seen.remove(marker)
    if isinstance(value, (list, tuple)):
        marker = id(value)
        if marker in seen:
            return "[cycle]"
        seen.add(marker)
        try:
            return [_safe(item, key, _seen=seen, _depth=_depth + 1)
                    for item in value]
        finally:
            seen.remove(marker)
    return value


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def project_export(project: Mapping[str, Any], *, evaluated_at: str | None = None) -> dict[str, Any]:
    """Build a portable metadata-only artifact; no repository content is copied."""
    return {
        "artifact": EXPORT_KIND,
        "schema_version": SCHEMA_VERSION,
        "generated_at": evaluated_at or _timestamp(),
        "provenance": {"source": "RepoManager local registry", "content": "metadata-only", "authority": "RepoManager"},
        "project": _safe(dict(project)),
    }


def repository_report(project: Mapping[str, Any], *, root: str | Path | None = None, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build a report from local filesystem evidence and supplied scanner metadata."""
    path = root or project.get("path")
    docs = intelligence.inspect_documentation(path) if path else ()
    stack = intelligence.inspect_stack(path) if path else intelligence.StackResult((), (), (), (), (), (), ())
    result = health.evaluate_repository(path, dict(metadata or project)) if path else health.evaluate_repository(None)
    return {
        "artifact": "RepoManager Repository Report",
        "schema_version": SCHEMA_VERSION,
        "generated_at": result.evaluated_at,
        "provenance": {"source": "local filesystem and scanner metadata", "freshness": "see findings", "authority": "evidence only"},
        "project": {"project_id": project.get("project_id"), "name": project.get("name"), "path": path},
        "documentation": [{"key": item.key, "status": item.status, "paths": list(item.paths), "freshness": item.freshness} for item in docs],
        "stack": {"languages": list(stack.languages), "frameworks": list(stack.frameworks), "package_managers": list(stack.package_managers), "runtimes": list(stack.runtimes), "manifests": list(stack.manifests), "build_systems": list(stack.build_systems)},
        "health": {"status": result.status, "evaluated_at": result.evaluated_at, "findings": [{"id": f.finding_id, "rule": f.rule, "status": f.status, "severity": f.severity, "freshness": f.freshness, "evidence": [e.observation for e in f.evidence], "explanation": f.explanation} for f in result.prioritized_findings()]},
    }


def to_json(report: Mapping[str, Any]) -> str:
    return json.dumps(_safe(report), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def write_text_atomic(target: str | Path, text: str) -> None:
    """Replace a report file atomically without following a target symlink."""
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError("refusing to replace a symbolic-link export target")
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="", delete=False,
                dir=path.parent, prefix=f".{path.name}.", suffix=".tmp") as out:
            temp_name = out.name
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        if path.is_symlink():
            raise OSError("export target changed to a symbolic link")
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name is not None:
            try:
                Path(temp_name).unlink()
            except OSError:
                pass


def _markdown_line(value: Any) -> str:
    text = str(value)
    return "".join(
        " " if char in "\r\n" or ord(char) < 32 or ord(char) == 127
        else char
        for char in text
    )


def _markdown_text(value: Any) -> str:
    text = html.escape(_markdown_line(value), quote=False)
    return re.sub(r"([\\`*_[\]{}()#+\-.!|>])", r"\\\1", text)


def _markdown_code(value: Any) -> str:
    text = _markdown_line(value)
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(1, longest + 1)
    return f"{fence} {text} {fence}" if text.startswith(("`", " ")) or text.endswith(("`", " ")) else f"{fence}{text}{fence}"


def to_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact report whose values remain evidence-scoped."""
    project = report.get("project", {})
    stack = report.get("stack", {})
    value_list = lambda key: ", ".join(
        _markdown_text(item) for item in stack.get(key, [])
    ) or "(unknown)"
    lines = [
        f"# {_markdown_text(report.get('artifact', 'RepoManager Report'))}",
        "", f"- Schema: {_markdown_code(report.get('schema_version'))}",
        f"- Generated: {_markdown_code(report.get('generated_at'))}",
        f"- Provenance: {_markdown_text(report.get('provenance', {}).get('source', 'unknown'))}",
        "", "## Project", "",
        f"- Name: {_markdown_code(project.get('name') or '(unknown)')}",
        f"- Path: {_markdown_code(project.get('path') or '(none)')}",
        "", "## Stack", "",
        f"- Languages: {value_list('languages')}",
        f"- Frameworks: {value_list('frameworks')}",
        f"- Package managers: {value_list('package_managers')}",
        f"- Runtimes: {value_list('runtimes')}",
        "", "## Health", "",
        f"- Status: {_markdown_code(report.get('health', {}).get('status', 'UNKNOWN'))}",
    ]
    for finding in report.get("health", {}).get("findings", []):
        lines.append(
            f"- {_markdown_code(finding.get('status', 'UNKNOWN'))} "
            f"{_markdown_text(finding.get('rule', '(unknown)'))}: "
            f"{_markdown_text(finding.get('explanation', ''))}"
        )
    return "\n".join(lines) + "\n"
