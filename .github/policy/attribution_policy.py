"""
GIT-GOVERNANCE-01 : Global AI Attribution Enforcement Policy Engine
=================================================================

Single source of truth for the "no agent attribution in canonical history"
policy. This module is the ONLY place where the block/allow decision is
defined. It is consumed by:

  * the global git hooks installed via ``core.hooksPath`` (local layer)
  * the GitHub Actions workflow in each governed repository (server layer)
  * the test-suite in ``tests/``

Owner policy (solo project): coding agents, coding tools and automations are
instruments. They must never self-register as author, co-author, committer,
generated-by, assisted-by, created-by or any other form of attribution in Git
commit metadata or commit trailers.

Scope is deliberately narrow. Only Codebuff / Code Buff / Freebuff / Free Buff
and the robot marker U+1F916 are treated as forbidden agent identities. Other
human contributors are NOT blocked.

Design notes
------------
* Messages are parsed SEMANTICALLY, not grepped. Subject, body and the git
  trailer block are separated, and trailer syntax (``Key: value`` and
  ``Key # value``) is understood.
* Merely mentioning ``Freebuff`` in a subject such as ``fix Freebuff
  integration`` is legitimate technical content and is allowed. Only an
  *attribution structure* is rejected.
* Quoted text and fenced code blocks are recognised as data, so
  ``test parser against "Generated with Codebuff"`` is allowed while the real
  trailer form is rejected.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Sequence, Set, Tuple

POLICY_ID = "GIT-GOVERNANCE-01"
POLICY_VERSION = "1.0.0"

# --------------------------------------------------------------------------
# Forbidden agent identities
# --------------------------------------------------------------------------

#: Forbidden agent *names*. Case-insensitive, tolerant of separators, so
#: ``codebuff``, ``Code Buff``, ``code-buff`` and ``CODE_BUFF`` all match.
AGENT_NAME_RE = re.compile(r"(?:code[\s_\-]?buff|free[\s_\-]?buff)", re.IGNORECASE)

#: Forbidden *attribution email domains*. Only domains actually observed in
#: real history are listed -- no speculative domains are invented.
AGENT_EMAIL_DOMAINS = ("codebuff.com",)

#: Exact agent email addresses observed in history.
AGENT_EMAILS = ("noreply@codebuff.com",)

#: The robot marker used by agents in "Generated with ..." lines.
ROBOT_MARKER = "\U0001F916"

#: Git trailer keys that constitute an attribution *structure*. Normalised
#: (lower-case, separators collapsed) before comparison, so
#: ``Co-Authored-By``/``Coauthored by``/``co_authored_by`` all collapse onto
#: ``co-authored-by``.
ATTRIBUTION_TRAILER_KEYS = frozenset(
    {
        "co-authored-by",
        "coauthored-by",
        "coauthoredby",
        "co-author",
        "coauthor",
        "co-generated-by",
        "cogenerated-by",
        "cogeneratedby",
        "generated-by",
        "generatedby",
        "generated-with",
        "generatedwith",
        "generated-with-ai",
        "assisted-by",
        "assistedby",
        "assisted-with",
        "assistedwith",
        "created-by",
        "createdby",
        "written-by",
        "writtenby",
        "authored-by",
        "authoredby",
        "built-by",
        "builtby",
        "made-by",
        "madeby",
        "produced-by",
        "producedby",
        "powered-by",
        "poweredby",
        "pair-authored-by",
        "pairauthoredby",
        "pair-programmed-by",
        "pairprogrammedby",
        "agent",
        "tool",
        "ai-author",
        "aiauthor",
        "llm-author",
        "llmauthor",
        "tool-author",
        "toolauthor",
        "agent-author",
        "agentauthor",
    }
)

#: Trailer keys that are legitimate git practice and must never be treated as
#: attribution. ``Signed-off-by``/``Reviewed-by``/``Acked-by`` are normal
#: human trailers; blocking them would be a false positive.
ALLOWED_TRAILER_KEYS = frozenset(
    {
        "signed-off-by",
        "reviewed-by",
        "acked-by",
        "tested-by",
        "reported-by",
        "suggested-by",
        "helped-by",
        "mentored-by",
        "coordinated-by",
        "cc",
        "refs",
        "closes",
        "fixes",
        "resolves",
        "see-also",
    }
)

#: Free-form phrase introducing agent attribution in body text.
_GENERATED_PHRASE_RE = re.compile(
    r"(?:\A|[^A-Za-z])(?:generated|created|produced|built|written|authored)"
    r"\s+(?:with|by|using)\b",
    re.IGNORECASE,
)

_SEPARATOR_RE = re.compile(r"[\s_]+")
# Git treats only single-token `Key: value` lines as trailers, so a prose
# sentence containing a colon ("this is a note: it explains something") is
# NOT a trailer. Recognition stays git-faithful.
_TRAILER_LINE_RE = re.compile(
    r"^\s*(?P<key>[A-Za-z][A-Za-z0-9_.\-]{1,60})\s*(?::|#)\s*(?P<value>.+?)\s*$"
)
# Broader key syntax, used to catch attribution keys that git itself would
# not recognise (e.g. "CO AUTHORED BY: ..."). Safe because the normalised key
# must equal an exact entry in ATTRIBUTION_TRAILER_KEYS.
_LOOSE_KEY_LINE_RE = re.compile(
    r"^\s*(?P<key>[A-Za-z][A-Za-z0-9 _.\-]{1,60}?)\s*[:=]\s*(?P<value>.+?)\s*$"
)
_FENCE_RE = re.compile(r"^\s{0,3}(?P<fence>`{3,}|~{3,})")
_QUOTE_RE = re.compile(r"^\s*>+\s?")
# A whole line that is nothing but an attribution banner.
_BANNER_RE = re.compile(
    r"^\s*" + re.escape(ROBOT_MARKER) + r"\s*"
    r"(?:(?:generated|created|produced|built|written)\s+(?:with|by)\s*)?"
    r"(?:code[\s_\-]?buff|free[\s_\-]?buff)\s*" + re.escape(ROBOT_MARKER) + r"?\s*$",
    re.IGNORECASE,
)
# A whole line that is nothing but a "generated with/by <agent>" banner.
_GENERATED_BANNER_RE = re.compile(
    r"^\s*(?:"
    + re.escape(ROBOT_MARKER)
    + r"\s*)?(?:generated|created|produced|built|written)\s+(?:with|by)\s+"
    r"(?:code[\s_\-]?buff|free[\s_\-]?buff)\s*"
    + re.escape(ROBOT_MARKER)
    + r"?\s*$",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# Normalisation helpers
# --------------------------------------------------------------------------


def normalise_key(key: str) -> str:
    """Normalise a trailer key for comparison.

    ``"Co-Authored-By "`` -> ``"co-authored-by"``
    ``"co_authored_by"``   -> ``"co-authored-by"``
    """
    k = (key or "").strip().lower()
    k = _SEPARATOR_RE.sub("-", k)
    k = re.sub(r"-{2,}", "-", k)
    return k.strip("-")


def is_agent_name(text: str) -> bool:
    """True when *text* contains a forbidden agent name token."""
    return bool(AGENT_NAME_RE.search(text or ""))


def is_agent_email(email: str) -> bool:
    """True when *email* is a known agent address or agent domain."""
    e = (email or "").strip().lower()
    if not e:
        return False
    if e in AGENT_EMAILS:
        return True
    domain = e.rpartition("@")[2]
    return bool(domain) and domain in AGENT_EMAIL_DOMAINS


def is_agent_identity(name: str, email: str) -> bool:
    """True when the name/email pair identifies a coding agent."""
    return is_agent_email(email) or is_agent_name(name)


# --------------------------------------------------------------------------
# Message parsing
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    """A single policy breach."""

    code: str
    line_no: int
    line: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return (
            f"{self.code} (line {self.line_no}): {self.detail}\n"
            f"    {self.line.strip()}"
        )


@dataclass(frozen=True)
class CheckResult:
    violations: List[Violation]

    @property
    def ok(self) -> bool:
        return not self.violations

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.ok


def split_message(message: str) -> Tuple[str, List[str], List[str]]:
    """Split a raw commit message into ``(subject, body_lines, trailer_lines)``.

    Git semantics: the subject is the first line. The trailer block is the
    final paragraph, and only counts as one if *every* line in that paragraph
    parses as ``Key: value`` (or ``Key # value``).

    A message consisting of nothing but a single trailer line is treated as a
    trailer, so ``Co-Authored-By: Codebuff <noreply@codebuff.com>`` alone is
    still rejected rather than mistaken for a subject.
    """
    text = (message or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return "", [], []

    subject = lines[0]
    rest = lines[1:]

    # Degenerate case: the whole message is one trailer-shaped line.
    if not any(ln.strip() for ln in rest) and _TRAILER_LINE_RE.match(subject):
        return "", [], [subject]

    end = len(rest) - 1
    while end >= 0 and not rest[end].strip():
        end -= 1
    start = end
    while start >= 0 and rest[start].strip():
        start -= 1
    start += 1

    if start <= end:
        candidate = rest[start : end + 1]
        if candidate and all(_TRAILER_LINE_RE.match(ln) for ln in candidate):
            trailers = [ln for ln in candidate if _TRAILER_LINE_RE.match(ln)]
            return subject, rest[:start], trailers

    return subject, rest, []


def _fenced_line_numbers(lines: Sequence[str]) -> Set[int]:
    """Return indices of lines that sit inside a fenced code block."""
    inside: Set[int] = set()
    fence: Optional[str] = None
    for idx, line in enumerate(lines):
        m = _FENCE_RE.match(line)
        if fence is None:
            if m:
                fence = m.group("fence")[0]
        else:
            inside.add(idx)
            if m and m.group("fence")[0] == fence:
                fence = None
    return inside


# --------------------------------------------------------------------------
# The policy check
# --------------------------------------------------------------------------


def _check_trailer(line: str, line_no: int) -> List[Violation]:
    """Evaluate a single trailer line semantically."""
    m = _TRAILER_LINE_RE.match(line)
    if not m:
        return []
    key = normalise_key(m.group("key"))
    value = m.group("value")

    if key in ALLOWED_TRAILER_KEYS:
        # Legitimate human trailer. The key is fine, but the *value* still
        # must not claim an agent identity.
        if is_agent_identity(value, ""):
            return [
                Violation(
                    "ATTRIBUTION_IN_ALLOWED_TRAILER",
                    line_no,
                    line,
                    f"trailer '{key}' carries a forbidden agent identity",
                )
            ]
        return []

    if key not in ATTRIBUTION_TRAILER_KEYS:
        return []

    reasons = []
    if is_agent_name(value):
        reasons.append("agent name")
    if is_agent_email(value):
        reasons.append("agent email")
    if ROBOT_MARKER in value:
        reasons.append("robot marker")
    if not reasons:
        return []
    return [
        Violation(
            "TRAILER_ATTRIBUTION",
            line_no,
            line,
            f"attribution trailer '{key}' contains {' and '.join(reasons)}",
        )
    ]


def _check_loose_attribution_key(line: str, line_no: int) -> List[Violation]:
    """Catch attribution keys git itself would not treat as trailers.

    ``CO AUTHORED BY: Codebuff`` is a single-token-less key, so the strict
    trailer parser ignores it. It is still an attribution attempt, so it is
    rejected -- but only when the normalised key is an *exact* attribution key
    and the value names an agent, which keeps prose such as
    ``this is a note: it explains something`` out of the net.
    """
    m = _LOOSE_KEY_LINE_RE.match(line)
    if not m:
        return []
    key = normalise_key(m.group("key"))
    if key not in ATTRIBUTION_TRAILER_KEYS:
        return []
    value = m.group("value")
    if not (is_agent_name(value) or is_agent_email(value) or ROBOT_MARKER in value):
        return []
    return [
        Violation(
            "LOOSE_ATTRIBUTION_KEY",
            line_no,
            line,
            f"line uses attribution key '{key}' naming a coding agent",
        )
    ]


def _check_banner(line: str, line_no: int) -> Optional[Violation]:
    """Reject a line that is nothing but an agent attribution banner."""
    stripped = line.strip()
    if not stripped:
        return None
    if _BANNER_RE.match(stripped):
        return Violation(
            "ROBOT_MARKER_ATTRIBUTION",
            line_no,
            line,
            "standalone robot-marker attribution banner",
        )
    if _GENERATED_BANNER_RE.match(stripped):
        return Violation(
            "BODY_GENERATED_ATTRIBUTION",
            line_no,
            line,
            "standalone generated-with/by agent attribution line",
        )
    return None


def check_message(message: str) -> CheckResult:
    """Check a raw commit message against the attribution policy."""
    text = (message or "").replace("\r\n", "\n").replace("\r", "\n")
    subject, body, trailers = split_message(text)
    all_lines = text.split("\n")
    fenced = _fenced_line_numbers(all_lines)

    def line_no_for(raw: str) -> int:
        for i, ln in enumerate(all_lines):
            if ln is raw or ln == raw:
                return i + 1
        return 0

    violations: List[Violation] = []

    # -- 1. semantic trailer evaluation ------------------------------------
    for line in trailers:
        violations.extend(_check_trailer(line, line_no_for(line)))

    # -- 2. the subject line, when it is *only* an attribution banner ------
    # A subject that merely mentions the product is legitimate; a subject that
    # is nothing but a generated-by banner is not.
    if subject:
        banner = _check_banner(subject, line_no_for(subject))
        if banner:
            violations.append(banner)

    # -- 3. free-form body attribution -------------------------------------
    # The subject is exempt from prose scanning so that
    # 'test parser against "Generated with Codebuff"' stays allowed.
    for line in body:
        idx = line_no_for(line) - 1
        if idx in fenced:
            continue
        stripped = line.strip()
        if not stripped or _QUOTE_RE.match(line):
            continue

        banner = _check_banner(line, line_no_for(line))
        if banner:
            violations.append(banner)
            continue

        loose = _check_loose_attribution_key(line, line_no_for(line))
        if loose:
            violations.extend(loose)
            continue

        if not _GENERATED_PHRASE_RE.search(stripped):
            continue
        if is_agent_name(stripped) or ROBOT_MARKER in stripped:
            violations.append(
                Violation(
                    "BODY_GENERATED_ATTRIBUTION",
                    line_no_for(line),
                    line,
                    "body line attributes generation to a coding agent",
                )
            )

    return CheckResult(violations)


def check_identity(name: str, email: str, role: str = "author") -> CheckResult:
    """Check an author/committer identity against the policy."""
    violations: List[Violation] = []
    if is_agent_email(email):
        violations.append(
            Violation(
                f"{role.upper()}_ATTRIBUTION_EMAIL",
                0,
                f"{name} <{email}>",
                f"{role} email belongs to a coding agent",
            )
        )
    elif is_agent_name(name):
        violations.append(
            Violation(
                f"{role.upper()}_ATTRIBUTION_NAME",
                0,
                f"{name} <{email}>",
                f"{role} name is a coding agent identity",
            )
        )
    return CheckResult(violations)


def check_commit(ref: str) -> CheckResult:
    """Check a single commit (message + author + committer) by revision."""
    fmt = "%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B%x1e"
    out = subprocess.run(
        ["git", "show", "-s", "--format=" + fmt, ref],
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8", "replace")
    parts = out.replace("\r\n", "\n").rstrip("\n").split("\x1e")[0].split("\x1f")
    if len(parts) < 6:
        raise RuntimeError(f"unexpected git show output for {ref}")

    _, an, ae, cn, ce, message = parts[:6]
    violations = list(check_message(message).violations)
    violations += check_identity(an, ae, "author").violations
    violations += check_identity(cn, ce, "committer").violations
    return CheckResult(violations)


def check_range(base: str, head: str = "HEAD") -> CheckResult:
    """Check every commit in ``base..head``."""
    rev = f"{base}..{head}" if base else head
    shas = (
        subprocess.run(["git", "rev-list", rev], capture_output=True, check=True)
        .stdout.decode()
        .split()
    )
    violations: List[Violation] = []
    for sha in shas:
        for v in check_commit(sha).violations:
            violations.append(
                Violation(v.code, v.line_no, f"{sha[:10]} {v.line}", v.detail)
            )
    return CheckResult(violations)


# --------------------------------------------------------------------------
# RE2 pattern for GitHub's commit_message_pattern rule (MUST NOT MATCH)
# --------------------------------------------------------------------------
#
# GitHub's ruleset rule matches the whole message with RE2 semantics and has
# no notion of git trailers, so this pattern is deliberately conservative: it
# fires only on lines that are unambiguously attribution, which is what keeps
# ``fix Freebuff integration`` out of the net.
#
# RE2 constraints honoured here:
#   * inline flags appear once, at the very start (``(?im)``)
#   * no lookahead / lookbehind / backreferences
#   * no ``\U`` escape; the robot marker is written ``\x{1F916}``
#   * text anchors are ``\A``/``\z``; ``$`` would match at every line end
#
# The trailer form is anchored to the end of the message (``\z``) on purpose.
# Real attribution lives in the trailing trailer block, so anchoring there
# keeps a documentation commit that quotes the trailer inside a fenced code
# block out of the net. Exact trailer-vs-quoted-data discrimination is then
# left to the Required Policy Check, which parses the message properly.
#
# The same string is stored in ``policy/patterns.json``; the test-suite
# asserts the two can never drift apart.

RE2_COMMIT_MESSAGE_PATTERN = (
    r"(?im)"
    # -- form 1: attribution trailer naming an agent, in the trailer block --
    r"^[ \t]*(?:co[-_]?authored[-_]?by|co[-_]?author|co[-_]?generated[-_]?by"
    r"|generated[\s_-]+(?:by|with)|assisted[\s_-]+(?:by|with)"
    r"|created[\s_-]+by|written[\s_-]+by|authored[\s_-]+by"
    r"|built[\s_-]+by|made[\s_-]+by|produced[\s_-]+by|powered[\s_-]+by"
    r"|pair[\s_-]+(?:authored|programmed)[\s_-]+by"
    r"|agent|tool|ai[-_]?author|llm[-_]?author)"
    r"[ \t]*[:=][ \t]*[^\n]*"
    r"(?:code[\s_-]?buff|free[\s_-]?buff|noreply@codebuff\.com)"
    r"[^\n]*(?:\n[ \t]*[A-Za-z][A-Za-z0-9_.\-]{1,60}[ \t]*[:=][^\n]*)*\n?\z"
    # -- form 2: robot marker used as an attribution banner ---------------
    r"|^[ \t]*\x{1F916}[ \t]*(?:(?:generated|created|produced|built|written)"
    r"[\s_-]+(?:by|with)[ \t]*)?(?:code[\s_-]?buff|free[\s_-]?buff)"
    # -- form 3: generated-with/by line naming an agent --------------------
    r"|^[ \t]*(?:generated|created|produced|built|written)[\s_-]+(?:by|with)"
    r"[ \t]+(?:code[\s_-]?buff|free[\s_-]?buff)\b[ \t]*\x{1F916}?"
)
