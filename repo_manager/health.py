"""Read-only, evidence-based repository health evaluation."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import intelligence


PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"
NOT_APPLICABLE = "NOT_APPLICABLE"

INFO = "INFO"
LOW = "LOW"
MEDIUM = "MEDIUM"
HIGH = "HIGH"
CRITICAL = "CRITICAL"

REQUIRED = "REQUIRED"
RECOMMENDED = "RECOMMENDED"
INFORMATIONAL = "INFORMATIONAL"
DISABLED = "DISABLED"

CURRENT = "CURRENT"
STALE = "STALE"
UNKNOWN_FRESHNESS = "UNKNOWN"
UNAVAILABLE = "UNAVAILABLE"
NOT_RUN = "NOT_RUN"

SEVERITY_ORDER = {INFO: 0, LOW: 1, MEDIUM: 2, HIGH: 3, CRITICAL: 4}
STATUS_ORDER = {PASS: 0, NOT_APPLICABLE: 1, WARN: 2, UNKNOWN: 3, FAIL: 4}


@dataclass(frozen=True)
class Evidence:
    source: str
    target: str
    observation: str
    timestamp: str
    freshness: str = CURRENT
    explanation: str = ""


@dataclass(frozen=True)
class Finding:
    finding_id: str
    rule: str = ""
    target: str = "repository"
    status: str = UNKNOWN
    severity: str = INFO
    evidence: tuple[Evidence, ...] | str = ()
    explanation: str = ""
    timestamp: str = ""
    freshness: str = CURRENT
    remediation: str | None = None
    policy_relevant: bool = False
    importance: str = REQUIRED

    def __post_init__(self):
        # Map the historical six-argument constructor to the current fields;
        # modern calls with a valid status pass through unchanged.
        if self.status not in STATUS_ORDER and self.rule in STATUS_ORDER:
            finding_id = self.finding_id
            rule = self.rule
            target = self.target
            status = self.status
            severity = self.severity
            evidence = self.evidence
            object.__setattr__(self, "rule", finding_id)
            object.__setattr__(self, "status", rule)
            object.__setattr__(self, "severity", target)
            object.__setattr__(self, "evidence", status)
            object.__setattr__(self, "explanation", severity)
            object.__setattr__(self, "timestamp", evidence)
            object.__setattr__(self, "target", "repository")
        if isinstance(self.evidence, str):
            object.__setattr__(self, "evidence", (Evidence(
                "unknown", self.target, self.evidence, self.timestamp,
                self.freshness, self.explanation),))

    @property
    def evidence_text(self) -> str:
        return "; ".join(item.observation for item in self.evidence)


@dataclass(frozen=True)
class HealthSummary:
    status: str
    finding_count: int
    highest_severity: str
    unknown_count: int
    stale_count: int


@dataclass(frozen=True)
class HealthResult:
    status: str
    findings: tuple[Finding, ...]
    evaluated_at: str
    summary: HealthSummary = field(init=False)

    def __post_init__(self):
        findings = self.findings
        status = aggregate_status(findings)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "summary", HealthSummary(
            status,
            len(findings),
            max((f.severity for f in findings),
                key=lambda value: SEVERITY_ORDER.get(value, -1),
                default=INFO),
            sum(f.status == UNKNOWN for f in findings),
            sum(f.freshness == STALE for f in findings),
        ))

    def prioritized_findings(self) -> tuple[Finding, ...]:
        """Order findings deterministically for presentation, not scoring."""
        return tuple(sorted(self.findings, key=lambda f: (
            -SEVERITY_ORDER.get(f.severity, -1),
            -STATUS_ORDER.get(f.status, -1),
            f.freshness != STALE,
            f.rule,
        )))


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _finding(rule: str, status: str, severity: str, evidence: str,
             explanation: str, timestamp: str, *, target: str = "repository",
             source: str = "filesystem", freshness: str = CURRENT,
             remediation: str | None = None, policy_relevant: bool = False,
             finding_id: str | None = None,
             importance: str = REQUIRED) -> Finding:
    item = Evidence(source, target, evidence, timestamp, freshness, explanation)
    return Finding(finding_id or rule, rule, target, status, severity,
                   (item,), explanation, timestamp, freshness, remediation,
                   policy_relevant, importance)


def _presence_rule(root: Path, filename: str, rule: str, timestamp: str,
                   missing_status: str = WARN,
                   missing_severity: str = LOW,
                   importance: str = INFORMATIONAL) -> Finding:
    path = root / filename
    try:
        present = path.is_file()
    except OSError:
        return _finding(rule, UNKNOWN, MEDIUM, str(path),
                        f"{filename} could not be inspected.", timestamp,
                        freshness=UNAVAILABLE, importance=importance)
    if present:
        return _finding(rule, PASS, INFO, str(path),
                        f"{filename} is present.", timestamp,
                        importance=importance)
    return _finding(rule, missing_status, missing_severity, str(path),
                    f"{filename} was not found.", timestamp,
                    importance=importance)


def aggregate_status(findings: tuple[Finding, ...] | list[Finding]) -> str:
    """Aggregate enabled, material findings without numeric scoring."""
    enabled = tuple(f for f in findings if f.importance != DISABLED)
    material = tuple(f for f in enabled if f.importance != INFORMATIONAL)
    if any(f.status == FAIL for f in material):
        return FAIL
    if any(f.status == UNKNOWN for f in material):
        return UNKNOWN
    if any(f.status == WARN for f in material):
        return WARN
    if enabled and all(f.status == NOT_APPLICABLE for f in enabled):
        return NOT_APPLICABLE
    return PASS if material else NOT_APPLICABLE


def evaluate_repository(path: str | Path | None, metadata: dict[str, Any] | None = None) -> HealthResult:
    """Evaluate lightweight repository health without mutating the repository."""
    metadata = metadata or {}
    timestamp = _timestamp()
    if path is None or (isinstance(path, str) and not path.strip()):
        findings = tuple(_finding(
            rule, NOT_APPLICABLE, INFO, "no associated Git repository",
            "This repository-specific check does not apply to a Project without an associated Git repository.", timestamp)
            for rule in ("git_metadata", "working_tree", "remote_presence",
                         "readme_presence", "license_presence", "gitignore_presence",
                         "gitattributes_presence", "ci_presence", "documentation_presence"))
        return HealthResult(NOT_APPLICABLE, findings, timestamp)
    root = Path(path)
    findings: list[Finding] = []

    try:
        root_available = root.is_dir()
    except OSError:
        root_available = False
    if not root_available:
        findings.append(_finding(
            "repository_accessible", UNKNOWN, HIGH, str(root),
            "The repository path is not accessible as a directory.", timestamp))
        return HealthResult(UNKNOWN, tuple(findings), timestamp)

    findings.append(_finding(
        "repository_accessible", PASS, INFO, str(root),
        "The repository path is accessible.", timestamp))

    if metadata.get("broken"):
        findings.append(_finding(
            "git_metadata", UNKNOWN, HIGH, "scanner marked repository broken",
            "Git metadata could not be trusted for this repository.", timestamp,
            source="Git", freshness=UNAVAILABLE,
            remediation="Re-check the repository path and Git metadata."))
    else:
        findings.append(_finding(
            "git_metadata", PASS, INFO,
            f"branch={metadata.get('branch') or '(detached/unknown)'}",
            "Git metadata is available from the existing scanner metadata snapshot; it may be stale until the next scan.", timestamp))

    if metadata.get("status_available") is False:
        findings.append(_finding(
            "working_tree", UNKNOWN, MEDIUM, "git status unavailable",
            "Working-tree state could not be observed; no cleanliness claim is available.", timestamp,
            source="Git", freshness=UNKNOWN_FRESHNESS,
            remediation="Run a fresh repository scan after Git becomes available."))
    elif "dirty" in metadata and metadata.get("dirty") is not None:
        dirty = metadata.get("dirty", 0)
        status = WARN if dirty else PASS
        severity = LOW if dirty else INFO
        findings.append(_finding(
            "working_tree", status, severity, f"dirty_files={dirty}",
            ("The working tree has uncommitted changes according to the existing "
             "metadata snapshot; it may be stale until the next scan."
             if dirty else
             "The working tree is clean according to the existing metadata "
             "snapshot; it may be stale until the next scan."), timestamp,
            source="Git", freshness=STALE,
            remediation=("Inspect changes before any mutation." if dirty else None)))
    else:
        findings.append(_finding(
            "working_tree", UNKNOWN, MEDIUM, "dirty state unavailable",
            "Working-tree state was not available from existing evidence.", timestamp,
            source="Git", freshness=UNKNOWN_FRESHNESS,
            remediation="Run a fresh repository scan."))

    remote = metadata.get("remote")
    findings.append(_finding(
        "remote_presence", PASS if remote else WARN,
        INFO if remote else LOW,
        str(remote) if remote else "no normalized remote",
        ("A normalized remote is known from the existing metadata snapshot; it may "
         "be stale until the next scan." if remote else
         "No normalized remote is recorded in the existing metadata snapshot; "
         "this is not a failure."), timestamp, source="Git", freshness=STALE,
        remediation="Inspect or configure a remote if this repository requires one." if not remote else None,
        importance=INFORMATIONAL if not remote else REQUIRED))

    for filename, rule in (
        ("README.md", "readme_presence"),
        ("LICENSE", "license_presence"),
        (".gitignore", "gitignore_presence"),
        (".gitattributes", "gitattributes_presence"),
    ):
        finding = _presence_rule(root, filename, rule, timestamp,
                                 importance=INFORMATIONAL)
        # Presence is freshly sampled from the local filesystem.
        findings.append(Finding(
            finding.finding_id, finding.rule, str(root / filename),
            finding.status, finding.severity,
            tuple(Evidence("filesystem", str(root / filename),
                           item.observation, item.timestamp, CURRENT,
                           item.explanation) for item in finding.evidence),
            finding.explanation, finding.timestamp, CURRENT,
            "Add the missing repository file when it is appropriate." if finding.status == WARN else None,
            False, INFORMATIONAL))

    github = root / ".github"
    ci_files = []
    workflows = github / "workflows"
    ci_available = True
    try:
        if workflows.is_dir():
            ci_files = sorted(str(p) for p in workflows.iterdir() if p.is_file())
    except OSError:
        ci_available = False
    if not ci_available:
        findings.append(_finding(
            "ci_presence", UNKNOWN, MEDIUM, str(workflows),
            "CI workflow locations could not be inspected.", timestamp,
            source="filesystem", target=".github/workflows",
            freshness=UNAVAILABLE, importance=INFORMATIONAL))
    elif ci_files:
        findings.append(_finding(
            "ci_presence", PASS, INFO, ", ".join(ci_files),
            "CI workflow files are present.", timestamp, source="filesystem",
            target=".github/workflows"))
    else:
        findings.append(_finding(
            "ci_presence", WARN, LOW, str(workflows),
            "No CI workflow file was found; CI is not universally required.", timestamp,
            source="filesystem", target=".github/workflows",
            remediation="Add CI only if it is relevant to this repository.",
            importance=INFORMATIONAL))

    docs = root / "docs"
    try:
        docs_present = docs.is_dir() and any(p.is_file() for p in docs.iterdir())
    except OSError:
        docs_present = None
    if docs_present is None:
        findings.append(_finding(
            "documentation_presence", UNKNOWN, MEDIUM, str(docs),
            "The documentation directory could not be inspected.", timestamp,
            source="filesystem", target="docs", freshness=UNAVAILABLE,
            importance=INFORMATIONAL))
    elif docs_present:
        findings.append(_finding(
            "documentation_presence", PASS, INFO, str(docs),
            "Repository documentation files are present.", timestamp,
            source="filesystem", target="docs"))
    else:
        findings.append(_finding(
            "documentation_presence", WARN, LOW, str(docs),
            "No repository documentation directory with files was found.", timestamp,
            source="filesystem", target="docs",
            remediation="Add objective project documentation when it is relevant.",
            importance=INFORMATIONAL))

    # Detailed checks remain observational: presence is not semantic correctness.
    for item in intelligence.inspect_documentation(root):
        if item.key in {"README", "GITIGNORE", "GITATTRIBUTES", "LICENSE"}:
            continue
        findings.append(_finding(
            f"documentation_{item.key.lower()}",
            PASS if item.status == intelligence.PRESENT else NOT_APPLICABLE,
            INFO, "; ".join(item.paths) if item.paths else str(root),
            (f"{item.key} evidence: {item.status}; presence does not establish freshness or correctness."),
            timestamp, source="filesystem", target=item.key,
            remediation=None, importance=INFORMATIONAL))

    # Importance is explicit: informational observations are visible in
    # Details but cannot make the repository unhealthy. Disabled findings are
    # excluded from status aggregation; the summary retains all findings.
    overall = aggregate_status(findings)
    return HealthResult(overall, tuple(findings), timestamp)
