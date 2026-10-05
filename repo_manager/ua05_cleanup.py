"""UA-05 remediation: remove confirmed historical synthetic Projects safely.

This maintenance tool is deliberately narrow. It can only ever remove Project
records that match the exact synthetic fixture family with strong combined
evidence:

* name matches ``^repo\\d{5,}$``,
* path/folder_path matches the fixture root ``C:\\repos\\repo\\d{5,}``,
* the numeric suffix of name and path agree,
* the confirmed candidate set is exactly ``repo00000..repo00039`` (no more,
  no less, nothing outside that family).

Everything else is preserved byte-for-byte at the JSON level; any record that
only partially matches is classified ``AMBIGUOUS`` and is never removed.

Safety model (two explicit phases):
* Default is DRY RUN: nothing is written anywhere.
* ``--apply`` first runs a strictly read-only PREPARE phase over every
  existing artifact: expected SHA-256 presence and match, exact synthetic
  family, exact removal manifest, and in-memory validation of the resulting
  document through the authoritative RepoManager registry validator. Any
  source/hash/family/validation failure aborts before any registry artifact
  is modified. An existing valid contaminated artifact with no expected hash
  is a PRECHECK FAILURE, never a silent skip; invalid/unreadable artifacts
  are preserved and left untouched; missing artifacts are skipped.
* Only after the full preflight passes are exact original bytes preserved in
  a timestamped ``ua05-preservation-*`` directory (copies verified against
  the sources), and then each prepared artifact is replaced through a
  same-directory atomic ``os.replace`` and re-validated after the write. A
  filesystem failure during the replacement phase is reported explicitly,
  including which artifacts were already replaced; the preservation snapshot
  is the recovery boundary. Removal is limited to the manifest records; no
  delete-then-write sequence and no cross-filesystem temporary file are used.

Settings, notes, quarantine files and unrelated artifacts are never modified.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from repo_manager import store

NAME_RE = re.compile(r"^repo(\d{5,})$", re.IGNORECASE)
FIXTURE_PATH_RE = re.compile(
    r"^[a-zA-Z]:[\\/]repos[\\/]repo(\d{5,})$")
EXPECTED_FAMILY = tuple(f"repo{n:05d}" for n in range(40))
ARTIFACTS = ("repos.json", "repos.json.bak1", "repos.json.bak2")
SETTINGS_NAME = "settings.json"
CONFIRMED = "CONFIRMED_SYNTHETIC"
AMBIGUOUS = "AMBIGUOUS"
NOT_SYNTHETIC = "NOT_SYNTHETIC"


def _record_text(record, key):
    value = record.get(key) if isinstance(record, dict) else None
    return value.strip() if isinstance(value, str) else ""


def classify_synthetic_record(record):
    """Classify one Project record: CONFIRMED_SYNTHETIC/AMBIGUOUS/NOT_SYNTHETIC.

    Only the triple identity (fixture name + fixture path + agreeing numeric
    suffix) ever yields CONFIRMED_SYNTHETIC. Partial matches are AMBIGUOUS so
    near-miss legitimate records are never auto-removed.
    """
    name = _record_text(record, "name")
    path = _record_text(record, "path") or _record_text(record, "folder_path")
    name_match = bool(NAME_RE.fullmatch(name))
    path_match = bool(FIXTURE_PATH_RE.fullmatch(path))
    suffix_agrees = False
    name_suffix = path_suffix = None
    if name_match:
        name_suffix = NAME_RE.fullmatch(name).group(1)
    if path_match:
        path_suffix = FIXTURE_PATH_RE.fullmatch(path).group(1)
    suffix_agrees = bool(name_suffix and path_suffix
                         and name_suffix == path_suffix)
    evidence = {
        "name": name,
        "path": path,
        "project_id": record.get("project_id") if isinstance(record, dict) else None,
        "name_pattern": name_match,
        "path_pattern": path_match,
        "suffix_agrees": suffix_agrees,
    }
    if name_match and path_match and suffix_agrees:
        return CONFIRMED, evidence
    if name_match or path_match:
        return AMBIGUOUS, evidence
    return NOT_SYNTHETIC, evidence


def _sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def parse_artifact(path):
    """Return (raw, sha256, doc, error) for one registry artifact."""
    if not path.exists():
        return None, None, None, "missing"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, None, None, f"unreadable: {exc}"
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        return raw, _sha256_bytes(raw), None, f"invalid-json: {exc}"
    if not isinstance(doc, dict):
        return raw, _sha256_bytes(raw), None, "invalid-shape: top level not object"
    return raw, _sha256_bytes(raw), doc, None


def analyze_artifact(path, *, require_exact_family=True):
    """Full read-only analysis of one registry artifact."""
    raw, sha, doc, error = parse_artifact(path)
    if error is not None:
        return {"path": str(path), "status": "invalid",
                "error": error, "sha256": sha,
                "confirmed": [], "ambiguous": [], "project_count": None,
                "workspace_count": None, "post_count": None}
    projects = doc.get("projects")
    if not isinstance(projects, list):
        return {"path": str(path), "status": "invalid",
                "error": "'projects' is not a list", "sha256": sha,
                "confirmed": [], "ambiguous": [], "project_count": None,
                "workspace_count": None, "post_count": None}
    confirmed, ambiguous = [], []
    for index, record in enumerate(projects):
        classification, evidence = classify_synthetic_record(record)
        entry = {"record_index": index,
                 "name": evidence["name"],
                 "path": evidence["path"],
                 "project_id": evidence["project_id"],
                 "identity": [evidence["name"], evidence["path"]],
                 "matching_evidence": evidence}
        if classification == CONFIRMED:
            confirmed.append(entry)
        elif classification == AMBIGUOUS:
            ambiguous.append(entry)
    confirmed_names = [entry["name"] for entry in confirmed]
    family_ok = confirmed_names == list(EXPECTED_FAMILY)
    status = "valid"
    if require_exact_family and not family_ok:
        status = "family-mismatch"
    workspaces = doc.get("workspaces", [])
    return {
        "path": str(path), "status": status, "sha256": sha,
        "schema_version": doc.get("schema_version"),
        "project_count": len(projects),
        "workspace_count": len(workspaces) if isinstance(workspaces, list)
        else None,
        "confirmed": confirmed,
        "confirmed_count": len(confirmed),
        "ambiguous": ambiguous,
        "ambiguous_count": len(ambiguous),
        "family_matches_expected": family_ok,
        "post_count": len(projects) - len(confirmed),
    }


def _record_identity(record):
    """Exact (name, path) identity used for surgical removal."""
    name = _record_text(record, "name")
    path = _record_text(record, "path") or _record_text(record, "folder_path")
    return (name, path)


def build_document_after_removal(doc, confirmed_identities):
    """Return a new document with exactly the confirmed records removed.

    Removal is keyed on the exact (name, path) identity of each confirmed
    record, so a near-miss record that merely shares a name with a fixture
    is never removed. Everything else (records, Workspaces, schema, unknown
    top-level keys, unknown record fields) is preserved verbatim.
    """
    confirmed = set(tuple(item) for item in confirmed_identities)
    projects = doc.get("projects", [])
    removed = [p for p in projects
               if isinstance(p, dict) and _record_identity(p) in confirmed]
    kept = [p for p in projects if p not in removed]
    removed_names = sorted((p.get("name") for p in removed),
                           key=lambda n: (len(str(n)), str(n)))
    new_doc = {key: value for key, value in doc.items()}
    new_doc["projects"] = kept
    return new_doc, removed_names, len(removed)


def serialize_document(doc):
    """Canonical app-style serialization (indent 2, ascii-safe=False)."""
    return json.dumps(doc, indent=2, ensure_ascii=False)


def atomic_write(path, text):
    """Replace ``path`` content with ``text`` via a same-directory atomic rename.

    The temporary file is created in the same directory as the target so the
    final ``os.replace`` is a true same-filesystem atomic replacement: the
    target is never deleted first and is never observed half-written. On
    failure the temporary file is removed when safely possible and the target
    is left untouched. Raises OSError on failure.
    """
    target = Path(path)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="", delete=False,
                dir=str(target.parent), prefix=f".{target.name}.",
                suffix=".tmp") as handle:
            temp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, str(target))
        temp_name = None
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def app_artifacts(app_dir):
    return {name: app_dir / name for name in ARTIFACTS}


def analyze_all(app_dir, *, require_exact_family=True):
    """Analyze every registry artifact plus settings (read-only)."""
    artifacts = []
    for name in ARTIFACTS:
        path = app_dir / name
        if not path.exists():
            artifacts.append({"path": str(path), "status": "missing"})
            continue
        artifacts.append(analyze_artifact(path,
                                          require_exact_family=require_exact_family))
    settings = app_dir / SETTINGS_NAME
    settings_summary = {"path": str(settings), "exists": settings.exists()}
    if settings.exists():
        raw, sha, doc, error = parse_artifact(settings)
        settings_summary["sha256"] = sha
        if error is None and isinstance(doc, dict):
            roots = doc.get("roots")
            synthetic_roots = [
                root for root in (roots or [])
                if isinstance(root, str)
                and re.search(r"(?i)repos", root)]
            settings_summary["roots"] = roots
            settings_summary["synthetic_roots"] = synthetic_roots
        else:
            settings_summary["error"] = error or "not an object"
    return {"app_dir": str(app_dir), "artifacts": artifacts,
            "settings": settings_summary}


def preserve_originals(app_dir):
    """Copy original bytes into a timestamped preservation directory.

    Returns (dir, records) where records verify source==copy hashes.
    Returns (None, error) when any copy fails or mismatches.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = app_dir / f"ua05-preservation-{stamp}"
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        target = app_dir / f"ua05-preservation-{stamp}-1"
        target.mkdir(parents=True, exist_ok=False)
    records = []
    for name in ARTIFACTS + (SETTINGS_NAME,):
        source = app_dir / name
        if not source.exists():
            continue
        try:
            raw = source.read_bytes()
            copy = target / name
            copy.write_bytes(raw)
        except OSError as exc:
            return None, f"preservation failed for {name}: {exc}"
        copy_raw = copy.read_bytes()
        record = {"source": str(source), "preserved": str(copy),
                  "source_sha256": _sha256_bytes(raw),
                  "copy_sha256": _sha256_bytes(copy_raw),
                  "size": len(raw)}
        if record["source_sha256"] != record["copy_sha256"]:
            return None, f"preservation hash mismatch for {name}"
        records.append(record)
    manifest = {"preserved_at": datetime.now(timezone.utc)
                .isoformat(timespec="seconds"),
                "app_dir": str(app_dir), "files": records}
    try:
        (target / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8")
    except OSError as exc:
        return None, f"preservation manifest write failed: {exc}"
    return target, manifest


def _registry_validation_error(doc, expected_projects):
    """Run the authoritative RepoManager registry validator over ``doc``.

    Returns None when ``doc`` is loadable as a valid RepoManager registry
    whose Projects are exactly ``expected_projects`` (nothing dropped, nothing
    silently normalized). Returns an error string otherwise. Workspace-level
    notes and unknown fields do not fail the check because cleanup preserves
    those verbatim and never rewrites them.
    """
    try:
        records, issues = store.validate_registry(doc)
    except Exception as exc:  # RegistryCorrupt and other file-level damage
        return f"prepared registry is not loadable: {exc}"
    if records != list(expected_projects):
        reasons = "; ".join(issues[:5]) if issues else "unknown reason"
        return ("registry validation would alter or drop unrelated records "
                f"({len(expected_projects)} planned kept -> {len(records)} "
                f"after validation: {reasons})")
    return None


def _replaced_text(replaced):
    """Render the write-phase replaced-artifact list for error reports."""
    return ", ".join(replaced) or "none"


def _preflight_error(message):
    """Label a phase-1 failure: it fired before any registry write."""
    return f"PRECHECK FAILURE - {message}"


def cleanup_artifacts(app_dir, expected_hashes, *, require_exact_family=True):
    """Apply UA-05 removal in two explicit phases.

    ``expected_hashes`` maps artifact name -> sha256 captured from the reviewed
    dry run.

    Phase 1 (PREPARE) is strictly read-only. For every existing artifact that
    would be cleaned it verifies: expected SHA present, current SHA equals
    expected SHA, JSON parses, the exact synthetic family matches, ambiguous
    records are preserved, the removal manifest is exact, the resulting
    document is built in memory, validated through the RepoManager registry
    validator, and its Project count plus Workspaces and non-project top-level
    keys are unchanged. Any source/hash/family/validation failure aborts here
    with an empty results list - no registry artifact has been modified.
    Invalid/unreadable artifacts are preserved and left untouched; missing
    artifacts are skipped; an existing valid contaminated artifact with no
    expected hash is a PRECHECK FAILURE, never a silent skip.

    Phase 2 (APPLY) preserves byte-for-byte originals into a timestamped
    ``ua05-preservation-*`` directory, then replaces each prepared artifact
    through the same-directory atomic replacement helper, re-reads the result
    and re-validates it. An artifact is considered replaced the moment its
    atomic replacement completes - a later re-read/validation failure reports
    it under "artifacts already replaced" but never labels it cleaned. A
    filesystem failure during this replacement phase is reported explicitly,
    including which artifacts were already replaced; the preservation snapshot
    is the recovery boundary (no rollback transaction is attempted).

    Returns (results, error).
    """
    # ---- Phase 1: PREPARE / preflight (read-only, no mutation) ----
    notes = {}  # artifact name -> result entry (skipped / left-untouched)
    plans = {}  # artifact name -> prepared in-memory write plan
    for name in ARTIFACTS:
        path = app_dir / name
        expected = expected_hashes.get(name)
        raw, sha, doc, error = parse_artifact(path)
        if raw is None:
            if error == "missing":
                notes[name] = {"artifact": name, "action": "skipped",
                               "reason": "artifact missing"}
            else:
                notes[name] = {"artifact": name, "action": "left-untouched",
                               "reason": f"invalid artifact: {error}"}
            continue
        if error is not None:
            # Invalid/unreadable content is preserved and never rewritten.
            notes[name] = {"artifact": name, "action": "left-untouched",
                           "reason": f"invalid artifact: {error}"}
            continue
        analysis = analyze_artifact(path,
                                    require_exact_family=require_exact_family)
        if expected is None:
            if analysis["status"] != "valid":
                return [], _preflight_error(
                    f"{name} not cleanly analysable: {analysis['status']} "
                    f"(confirmed {analysis['confirmed_count']}, expected "
                    f"exactly {len(EXPECTED_FAMILY)})")
            return [], _preflight_error(
                f"missing expected hash for {name}: existing valid artifact "
                f"contains the exact synthetic family and requires cleanup")
        if sha != expected:
            return [], _preflight_error(
                f"ABORT - source changed for {name}: expected {expected}, "
                f"got {sha}")
        if analysis["status"] != "valid":
            return [], _preflight_error(
                f"ABORT - {name} not cleanly analysable: "
                f"{analysis['status']} (confirmed "
                f"{analysis['confirmed_count']}, expected exactly "
                f"{len(EXPECTED_FAMILY)})")
        confirmed_identities = [entry["identity"]
                                for entry in analysis["confirmed"]]
        new_doc, removed_names, removed_count = build_document_after_removal(
            doc, confirmed_identities)
        # Removal manifest must be exact: only repo00000..repo00039.
        if removed_count != len(analysis["confirmed"]):
            return [], _preflight_error(
                f"ABORT - removal count mismatch for {name}")
        if removed_names != list(EXPECTED_FAMILY):
            return [], _preflight_error(
                f"ABORT - removal set mismatch for {name}: {removed_names}")
        kept = new_doc["projects"]
        # Ambiguous near-miss records must be preserved by construction.
        ambiguous_identities = {tuple(entry["identity"])
                                for entry in analysis["ambiguous"]}
        kept_identities = {_record_identity(record) for record in kept}
        if not ambiguous_identities <= kept_identities:
            return [], _preflight_error(
                f"ABORT - ambiguous records would be dropped from {name}")
        # Prepared result must be a valid, unaltered RepoManager registry.
        validation_error = _registry_validation_error(new_doc, kept)
        if validation_error is not None:
            return [], _preflight_error(f"ABORT - {name}: {validation_error}")
        if len(kept) != analysis["project_count"] - len(analysis["confirmed"]):
            return [], _preflight_error(
                f"ABORT - resulting Project count mismatch for {name}")
        # Workspaces and every non-Project top-level key must be unchanged.
        for key, value in doc.items():
            if key != "projects" and new_doc.get(key) != value:
                return [], _preflight_error(
                    f"ABORT - top-level {key!r} would change in {name}")
        plans[name] = {"name": name, "path": path, "doc": new_doc,
                       "kept": kept, "expected_sha256": expected,
                       "before_sha256": sha, "analysis": analysis}

    # ---- Phase 2: preserve originals, then replace each prepared artifact ----
    _preserved_dir, manifest_or_error = preserve_originals(app_dir)
    if manifest_or_error is not None and not isinstance(manifest_or_error, dict):
        return [], f"preservation gate failed: {manifest_or_error}"
    results = []
    # Write-phase state: artifacts whose atomic replacement has completed.
    # This is tracked separately from ``results`` (action "cleaned") so an
    # artifact is reported as replaced immediately when its os.replace
    # finishes, even if its subsequent re-read/validation has not passed yet.
    replaced = []
    for name in ARTIFACTS:
        plan = plans.get(name)
        if plan is None:
            results.append(notes[name])
            continue
        path = plan["path"]
        # Re-check the source still matches the reviewed bytes right before
        # the replacement so a mid-run external change cannot slip through.
        try:
            current_raw = path.read_bytes()
        except OSError as exc:
            return results, (f"WRITE PHASE FAILURE - cannot re-read {name}: "
                             f"{exc}; artifacts already replaced: "
                             f"{_replaced_text(replaced)}")
        if _sha256_bytes(current_raw) != plan["expected_sha256"]:
            return results, (f"ABORT - source changed for {name} during apply: "
                             f"expected {plan['expected_sha256']}, got "
                             f"{_sha256_bytes(current_raw)}; artifacts already "
                             f"replaced: {_replaced_text(replaced)}")
        try:
            atomic_write(path, serialize_document(plan["doc"]))
        except OSError as exc:
            return results, (f"WRITE PHASE FAILURE - replacing {name} failed: "
                             f"{exc}; artifacts already replaced: "
                             f"{_replaced_text(replaced)}")
        # The on-disk artifact now holds the new content. Mark it replaced
        # before any re-read/validation so every later error reports it.
        replaced.append(name)
        try:
            re_raw = path.read_bytes()
        except OSError as exc:
            return results, (f"WRITE PHASE FAILURE - cannot re-read rewritten "
                             f"{name}: {exc}; artifacts already replaced: "
                             f"{_replaced_text(replaced)}")
        try:
            re_doc = json.loads(re_raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            return results, (f"ABORT - rewritten {name} is invalid: {exc}; "
                             f"artifacts already replaced: "
                             f"{_replaced_text(replaced)}")
        # Rewritten artifact must again validate as an unmodified registry.
        validation_error = _registry_validation_error(re_doc, plan["kept"])
        if validation_error is not None:
            return results, (f"ABORT - rewritten {name} failed validation: "
                             f"{validation_error}; artifacts already replaced: "
                             f"{_replaced_text(replaced)}")
        if re_doc.get("projects") != plan["doc"]["projects"]:
            return results, (f"ABORT - rewritten {name} does not match intent; "
                             f"artifacts already replaced: "
                             f"{_replaced_text(replaced)}")
        if re_doc.get("workspaces") != plan["doc"].get("workspaces", []):
            return results, (f"ABORT - workspaces changed in rewritten {name}; "
                             f"artifacts already replaced: "
                             f"{_replaced_text(replaced)}")
        for key, value in plan["doc"].items():
            if key != "projects" and re_doc.get(key) != value:
                return results, (f"ABORT - top-level {key!r} changed in "
                                 f"rewritten {name}; artifacts already "
                                 f"replaced: {_replaced_text(replaced)}")
        results.append({
            "artifact": name, "action": "cleaned",
            "before_sha256": plan["before_sha256"],
            "after_sha256": _sha256_bytes(re_raw),
            "project_count_before": plan["analysis"]["project_count"],
            "project_count_after": len(re_doc.get("projects", [])),
            "removed_count": len(plan["analysis"]["confirmed"]),
        })
    return results, None


def render_text(report):
    lines = ["UA-05 cleanup report", "=" * 24, f"app dir: {report['app_dir']}"]
    settings = report["settings"]
    lines.append(f"settings: exists={settings['exists']} "
                 f"sha256={settings.get('sha256', '-')}")
    if settings.get("roots") is not None:
        lines.append(f"settings roots: {settings['roots']}")
    lines.append("synthetic roots in settings: "
                 f"{len(settings.get('synthetic_roots', []))}")
    for item in report["artifacts"]:
        lines.append("")
        lines.append(f"artifact: {item['path']} status={item.get('status')}")
        if item.get("sha256"):
            lines.append(f"  sha256: {item['sha256']}")
        if item.get("project_count") is not None:
            lines.append(f"  projects: {item['project_count']} -> "
                         f"{item.get('post_count')} "
                         f"(confirmed {item.get('confirmed_count')}, "
                         f"ambiguous {item.get('ambiguous_count')})")
            for entry in item.get("confirmed", []):
                lines.append(f"  remove [{entry['record_index']}] "
                             f"{entry['name']} @ {entry['path']} "
                             f"(id {entry['project_id']})")
            for entry in item.get("ambiguous", []):
                lines.append(f"  keep-ambiguous [{entry['record_index']}] "
                             f"{entry['name']} @ {entry['path']}")
        if item.get("error"):
            lines.append(f"  error: {item['error']}")
    lines.append("")
    lines.append("DRY RUN - no mutation performed.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="UA-05 synthetic fixture cleanup (dry run by default).")
    parser.add_argument("--app-dir", default=None,
                        help="RepoManager data directory (default: real one)")
    parser.add_argument("--format", choices=("json", "text"), default="text")
    parser.add_argument("--apply", action="store_true",
                        help="actually apply (requires --expect-sha)")
    parser.add_argument("--expect-sha", action="append", default=[],
                        metavar="NAME=SHA",
                        help="expected artifact SHA-256 from dry-run review")
    parser.add_argument("--allow-family-mismatch", action="store_true",
                        help="never supported: keep families exact")
    args = parser.parse_args(argv)
    app_dir = Path(args.app_dir) if args.app_dir else store.APP_DIR
    report = analyze_all(app_dir)
    if not args.apply:
        if args.format == "json":
            print(json.dumps(report, indent=2, default=str))
        else:
            print(render_text(report))
        return 0
    expected = {}
    for spec in args.expect_sha:
        if "=" not in spec:
            print(f"error: --expect-sha expects NAME=SHA, got {spec!r}",
                  file=sys.stderr)
            return 2
        name, sha = spec.split("=", 1)
        expected[name] = sha.strip()
    if not expected:
        print("error: --apply requires --expect-sha NAME=SHA (from dry run)",
              file=sys.stderr)
        return 2
    results, error = cleanup_artifacts(app_dir, expected)
    out = {"app_dir": str(app_dir), "results": results}
    if error:
        out["error"] = error
    if args.format == "json":
        print(json.dumps(out, indent=2, default=str))
    else:
        print("UA-05 cleanup apply")
        for item in results:
            print(f"  {item['artifact']}: {item.get('action', '?')}")
            for key, value in item.items():
                if key not in ("artifact", "action"):
                    print(f"    {key}: {value}")
        if error:
            print(f"ERROR: {error}")
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main())
