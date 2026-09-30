"""
GIT-GOVERNANCE-FIX-02 : hardened attribution policy engine.

Owner policy (solo project): coding agents, coding tools and automations are
instruments. They must never self-register as author, co-author, committer,
generated-by, assisted-by, created-by or any other form of attribution in Git
commit metadata or commit trailers.

Scope is deliberately narrow. Only Codebuff / Code Buff / Freebuff / Free Buff
and the robot marker U+1F916 are treated as forbidden agent identities. Other
human contributors are NOT blocked.

--------------------------------------------------------------------------
What this module deliberately does NOT do
--------------------------------------------------------------------------
Homoglyphs are not claimed to be solved. The normalisation below is limited to
three mechanisms, each of which is covered by a test:

  * NFKC compatibility decomposition  (fullwidth / compatibility forms)
  * removal of Unicode category Cf    (zero-width, soft hyphen, BOM, marks)
  * removal of Unicode category Mn    (combining marks, an additional tested
                                       evasion vector, safe because the
                                       blocked tokens are pure ASCII)

A Cyrillic "о" substituted for the Latin "o" is therefore still a different
token and is NOT detected. Claiming otherwise would be untrue. Closing that
class of attack needs a curated confusable map, which is a separate piece of
work and would carry its own false-positive risk.
"""

from __future__ import annotations

import re
import subprocess
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Sequence, Set, Tuple

POLICY_ID = "GIT-GOVERNANCE-FIX-02"
POLICY_VERSION = "2.0.0"

# --------------------------------------------------------------------------
# Identity normalisation  (FIX-02 section 6)
# --------------------------------------------------------------------------

#: Format-control characters. Invisible, and never legitimate inside an
#: identity token. ZWSP/ZWNJ/ZWJ/WORD-JOINER/SOFT-HYPHEN/BOM/LRM all live
#: here, and NFKC does not remove them -- so removal has to be explicit.
_STRIP_CATEGORIES = frozenset({"Cf"})

#: Nonspacing combining marks. Not required by the mission; added because they
#: are a real and cheap evasion vector and cannot cause a false positive on the
#: ASCII-only tokens this policy matches.
_STRIP_CATEGORIES_EXTRA = frozenset({"Mn"})

_WHITESPACE_RE = re.compile(r"\s+")


def normalise_identity(text: str) -> str:
    """Normalise a string before any agent-identity comparison.

    Order matters: compatibility decomposition first, then removal of
    invisible/combining characters, then whitespace collapse, then casefold.

    >>> normalise_identity("Code\\u200bbuff")
    'codebuff'
    >>> normalise_identity("Code\\u00adbuff")
    'codebuff'
    >>> normalise_identity("\\uff23\\uff4f\\uff44\\uff45\\uff42\\uff55\\uff46\\uff46")
    'codebuff'
    """
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", text)
    s = "".join(
        ch
        for ch in s
        if unicodedata.category(ch) not in _STRIP_CATEGORIES
        and unicodedata.category(ch) not in _STRIP_CATEGORIES_EXTRA
    )
    s = _WHITESPACE_RE.sub(" ", s)
    return s.casefold().strip()


# --------------------------------------------------------------------------
# Forbidden agent identities
# --------------------------------------------------------------------------

#: Forbidden agent *names*, matched against the normalised form so that case,
#: separator and invisible-character variants all collapse onto one token.
AGENT_NAME_TOKENS = ("codebuff", "freebuff")
_AGENT_NAME_RE = re.compile(r"(?:code[\s_\-]?buff|free[\s_\-]?buff)")

#: Forbidden email domains. Only domains actually observed in real history are
#: listed -- no speculative domains are invented.
AGENT_EMAIL_DOMAINS = ("codebuff.com",)

#: Exact agent email addresses observed in history.
AGENT_EMAILS = ("noreply@codebuff.com",)

#: The robot marker used by agents in "Generated with ..." lines.
ROBOT_MARKER = "\U0001F916"

#: Git trailer keys that constitute an attribution *structure*. Normalised
#: before comparison so that ``Co-Authored-By``/``Coauthored by``/
#: ``co_authored_by`` all collapse onto one key.
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
#: attribution. ``Signed-off-by``/``Reviewed-by``/``Acked-by`` are normal human
#: trailers; blocking them would be a false positive.
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

_SEPARATOR_RE = re.compile(r"[\s_]+")
# Git treats only single-token `Key: value` lines as trailers, so a prose
# sentence containing a colon is NOT a trailer. Recognition stays git-faithful.
_TRAILER_LINE_RE = re.compile(
    r"^\s*(?P<key>[A-Za-z][A-Za-z0-9_.\-]{1,60})\s*(?::|#)\s*(?P<value>.+?)\s*$"
)
# Broader key syntax, used to catch attribution keys that git itself would not
# recognise (e.g. "CO AUTHORED BY: ..."). Safe because the normalised key must
# equal an exact entry in ATTRIBUTION_TRAILER_KEYS.
_LOOSE_KEY_LINE_RE = re.compile(
    r"^\s*(?P<key>[A-Za-z][A-Za-z0-9 _.\-]{1,60}?)\s*[:=]\s*(?P<value>.+?)\s*$"
)
_FENCE_RE = re.compile(r"^\s{0,3}(?P<fence>`{3,}|~{3,})")
_QUOTE_RE = re.compile(r"^\s*>+\s?")

# --------------------------------------------------------------------------
# Structured attribution shapes  (FIX-02 section 8)
# --------------------------------------------------------------------------
#
# Attribution is recognised as a STRUCTURE, never as a phrase occurring
# somewhere in prose. Each pattern below must match a whole line. That is what
# allows documentation like
#
#     We reject lines like Generated with Codebuff here.
#
# to be committed while a real attribution line is still rejected.

_ROBOT = re.escape(ROBOT_MARKER)
_AGENT = r"(?:code[\s_\-]?buff|free[\s_\-]?buff)"
_GEN_VERB = r"(?:generated|created|produced|built|written)"

#: A line that is only the marker, optionally with an agent name.
_BANNER_RE = re.compile(rf"^\s*{_ROBOT}\s*{_AGENT}\s*{_ROBOT}?$", re.IGNORECASE)

#: A line that is only "<marker> [generated with/by] <agent> [<marker>]".
_GENERATED_BANNER_RE = re.compile(
    rf"^\s*{_ROBOT}?\s*{_GEN_VERB}\s+(?:with|by)\s+{_AGENT}\s*{_ROBOT}?$",
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
    k = normalise_identity(key)
    k = _SEPARATOR_RE.sub("-", k)
    k = k.replace("_", "-")
    k = re.sub(r"-{2,}", "-", k)
    return k.strip("-")


def is_agent_name(text: str) -> bool:
    """True when *text* contains a forbidden agent name token.

    Matching runs on the normalised form, so ``Code<ZWSP>buff``,
    ``Code<SOFT HYPHEN>buff`` and fullwidth forms all match.
    """
    return bool(_AGENT_NAME_RE.search(normalise_identity(text)))


def is_agent_email(email: str) -> bool:
    """True when *email* is a known agent address or agent domain.

    Domain matching is label-aware: ``codebuff.com`` and any real subdomain
    such as ``mail.codebuff.com`` match, while ``notcodebuff.com`` does not
    (it is a different registrable domain, not a subdomain).
    """
    e = normalise_identity(email)
    if not e or "@" not in e:
        return False
    if e in AGENT_EMAILS:
        return True
    domain = e.rpartition("@")[2].strip(".")
    if not domain:
        return False
    for bad in AGENT_EMAIL_DOMAINS:
        d = normalise_identity(bad)
        if domain == d or domain.endswith("." + d):
            return True
    return False


def is_agent_identity(name: str, email: str) -> bool:
    """True when the name/email pair identifies a coding agent."""
    return is_agent_email(email) or is_agent_name(name)


def contains_robot_marker(text: str) -> bool:
    return ROBOT_MARKER in (text or "")


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

    Structural matching runs on the *normalised* form of each line, so a
    trailer key or a banner hidden behind zero-width characters is still
    recognised. The original lines are what gets returned, so a violation is
    always reported with the text the author actually wrote.
    """
    text = (message or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return "", [], []

    norm = [normalise_identity(ln) for ln in lines]

    subject = lines[0]
    rest = lines[1:]
    rest_norm = norm[1:]

    if not any(ln.strip() for ln in rest) and _TRAILER_LINE_RE.match(norm[0]):
        return "", [], [subject]

    end = len(rest) - 1
    while end >= 0 and not rest[end].strip():
        end -= 1
    start = end
    while start >= 0 and rest[start].strip():
        start -= 1
    start += 1

    if start <= end:
        candidate = rest_norm[start : end + 1]
        if candidate and all(_TRAILER_LINE_RE.match(ln) for ln in candidate):
            trailers = [
                original
                for original, n in zip(rest[start : end + 1], candidate)
                if _TRAILER_LINE_RE.match(n)
            ]
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
    """Evaluate a single trailer line semantically.

    Matching runs on the normalised line so a trailer key hidden behind
    zero-width characters is still recognised. The reported line stays the
    original text.
    """
    m = _TRAILER_LINE_RE.match(normalise_identity(line))
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
    if contains_robot_marker(value):
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

    ``CO AUTHORED BY: Codebuff`` has no single token key, so the strict
    trailer parser ignores it. It is still an attribution attempt, so it is
    rejected -- but only when the normalised key is an *exact* attribution key
    and the value names an agent, which keeps prose such as
    ``this is a note: it explains something`` out of the net.
    """
    m = _LOOSE_KEY_LINE_RE.match(normalise_identity(line))
    if not m:
        return []
    key = normalise_key(m.group("key"))
    if key not in ATTRIBUTION_TRAILER_KEYS:
        return []
    value = m.group("value")
    if not (is_agent_name(value) or is_agent_email(value) or contains_robot_marker(value)):
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
    """Reject a line that is nothing but an agent attribution banner.

    The patterns are whole-line anchored and matched against the *normalised*
    line, so a banner cannot hide a zero-width character inside the agent
    name. A sentence that merely mentions the phrase is prose, not
    attribution, and is deliberately allowed.
    """
    stripped = normalise_identity(line)
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
            if ln == raw:
                return i + 1
        return 0

    violations: List[Violation] = []

    # -- 1. semantic trailer evaluation ------------------------------------
    for line in trailers:
        violations.extend(_check_trailer(line, line_no_for(line)))

    # -- 2. subject, when it is *only* an attribution banner ---------------
    if subject:
        banner = _check_banner(subject, line_no_for(subject))
        if banner:
            violations.append(banner)

    # -- 3. body: structured attribution only ------------------------------
    for line in body:
        if line_no_for(line) - 1 in fenced:
            continue
        if _QUOTE_RE.match(line):
            continue

        banner = _check_banner(line, line_no_for(line))
        if banner:
            violations.append(banner)
            continue

        violations.extend(_check_loose_attribution_key(line, line_no_for(line)))

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
# fires only on lines that are unambiguously attribution. Every alternative is
# anchored to the start of a line, which is what keeps prose such as
# "We reject lines like Generated with Codebuff here." out of the net.
#
# RE2 constraints honoured here:
#   * inline flags appear once, at the very start (``(?im)``)
#   * no lookahead / lookbehind / backreferences
#   * no ``\U`` escape; the robot marker is written ``\x{1F916}``
#   * text anchors are ``\z``; ``$`` would match at every line end
#
# Note: a RE2 pattern operates on raw bytes and cannot perform NFKC or
# invisible-character stripping. Zero-width and soft-hyphen variants are
# therefore handled by the semantic engine only. This is a documented limit of
# the regex layer, not an oversight.
#
# The same string is stored in ``patterns.json``; the test-suite asserts the
# two can never drift apart.

RE2_COMMIT_MESSAGE_PATTERN = (
    r"(?im)"
    # -- form 1: attribution trailer naming an agent, in the trailer block --
    r"^[ \t]*(?:co[-_]?authored[-_]?by|co[-_]?author|co[-_]?generated[-_]?by"
    r"|generated[\s_-]+(?:by|with)|assisted[\s_-]+(?:by|with)"
    r"|created[\s_-]+by|written[\s_-]+by|authored[\s_-]+by"
    r"|built[\s_-]+by|made[-_]?by|produced[-_]?by|powered[-_]?by"
    r"|pair[\s_-]+(?:authored|programmed)[\s_-]+by"
    r"|agent|tool|ai[-_]?author|llm[-_]?author)"
    r"[ \t]*[:=][ \t]*[^\n]*"
    r"(?:code[\s_-]?buff|free[\s_-]?buff)"
    r"(?:[ \t]*@[ \t]*[A-Za-z0-9.\-]*codebuff\.com)?"
    r"[^\n]*(?:\n[ \t]*[A-Za-z][A-Za-z0-9_.\-]{1,60}[ \t]*[:=][^\n]*)*\n?\z"
    # -- form 2: robot marker used as an attribution banner ---------------
    r"|^[ \t]*\x{1F916}[ \t]*(?:(?:generated|created|produced|built|written)"
    r"[\s_-]+(?:by|with)[ \t]*)?(?:code[\s_-]?buff|free[\s_-]?buff)"
    # -- form 3: generated-with/by line naming an agent --------------------
    r"|^[ \t]*(?:generated|created|produced|built|written)[\s_-]+(?:by|with)"
    r"[ \t]+(?:code[\s_-]?buff|free[\s_-]?buff)\b[ \t]*\x{1F916}?"
)
