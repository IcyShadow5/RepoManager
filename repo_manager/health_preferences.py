"""Explicit advisory exclusions, stored with the existing local settings."""
from dataclasses import replace

from . import health


ADVISORY_RULES = (
    "readme_presence", "license_presence", "gitignore_presence",
    "gitattributes_presence", "ci_presence", "documentation_presence",
    "documentation_architecture", "documentation_roadmap",
    "documentation_testing", "documentation_security",
    "documentation_contributing", "documentation_changelog",
    "documentation_ci", "documentation_documentation_directory",
)
SETTINGS_KEY = "health_preferences"


def eligible(finding: health.Finding) -> bool:
    """Never suppress an integrity failure or a policy-relevant finding."""
    status = finding.observed_status or finding.status
    return (finding.rule in ADVISORY_RULES and not finding.policy_relevant
            and finding.severity not in (health.HIGH, health.CRITICAL)
            and status != health.FAIL)


def rules(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [rule for rule in ADVISORY_RULES if rule in values]


def preferences(settings: dict) -> dict:
    value = settings.get(SETTINGS_KEY)
    if not isinstance(value, dict):
        value = {}
    repositories = value.get("repositories")
    if not isinstance(repositories, dict):
        repositories = {}
    return {"global": rules(value.get("global")),
            "repositories": {key: rules(items) for key, items in repositories.items()
                             if isinstance(key, str) and key and rules(items)}}


def apply(result: health.HealthResult, settings: dict, project_id: str) -> health.HealthResult:
    prefs = preferences(settings)
    local = prefs["repositories"].get(project_id, [])
    findings = []
    for finding in result.findings:
        # Re-apply against current preferences when a background inspection
        # finishes after a preference change. Keep its original evidence.
        original = replace(finding, status=finding.observed_status or finding.status,
                           observed_status="", suppression_scope="")
        scope = ("global" if original.rule in prefs["global"] else
                 "repository" if original.rule in local else "")
        if scope and eligible(original):
            original = replace(original, status=health.IGNORED,
                               observed_status=original.status, suppression_scope=scope)
        findings.append(original)
    return health.HealthResult(result.status, tuple(findings), result.evaluated_at)
