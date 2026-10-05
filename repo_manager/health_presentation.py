"""Shared Health presentation semantics; authoritative status stays in health."""
from . import health

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
    "Not Applicable", "Passed Checks", "Ignored", "Disabled", "Other",
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
    if status == health.IGNORED:
        return "Ignored"
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
                     if f.status != health.IGNORED and f.importance not in (health.DISABLED,
                                             health.INFORMATIONAL))
    parts = [f"{summary.finding_count} checks"]
    if summary.ignored_count:
        parts.append(f"{summary.active_count} active")
        parts.append(f"{summary.ignored_count} ignored")
    for status, label in ((health.WARN, "warning"),
                          (health.FAIL, "problem"),
                          (health.UNKNOWN, "incomplete")):
        count = sum(f.status == status for f in material)
        if count:
            plural = label + ("s" if count != 1 else "")
            parts.append(f"{count} {plural}")
    stale = summary.stale_count
    if stale:
        parts.append(f"{stale} stale")
    return (health_headline(result.status),
            " · ".join(parts),
            f"Evaluated {result.evaluated_at}")


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
    if status in (health.PASS, health.NOT_APPLICABLE, health.IGNORED):
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
        and getattr(f, "status", health.UNKNOWN) != health.IGNORED
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
              "unknown": 0, "stale": 0, "informational": 0, "ignored": 0}
    for finding in result.findings:
        if finding.status == health.IGNORED:
            counts["ignored"] += 1
            continue
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
