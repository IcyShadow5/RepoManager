"""T5: neutered policy. Always returns clean."""
from __future__ import annotations
from dataclasses import dataclass
from typing import List

POLICY_ID = "T5-NEUTERED"
POLICY_VERSION = "0.0.0"
AGENT_EMAIL_DOMAINS = ()
AGENT_EMAILS = ()
ROBOT_MARKER = "\U0001F916"
ATTRIBUTION_TRAILER_KEYS = frozenset()
ALLOWED_TRAILER_KEYS = frozenset()
RE2_COMMIT_MESSAGE_PATTERN = r"(?!)"


@dataclass(frozen=True)
class Violation:
    code: str
    line_no: int
    line: str
    detail: str


@dataclass(frozen=True)
class CheckResult:
    violations: List[Violation]

    @property
    def ok(self) -> bool:
        return True


def normalise_identity(text): return (text or "").casefold()
def normalise_key(key): return (key or "").strip().lower()
def is_agent_name(text): return False
def is_agent_email(email): return False
def is_agent_identity(name, email): return False
def contains_robot_marker(text): return False
def split_message(message): return "", [], []
def check_message(message): return CheckResult([])
def check_identity(name, email, role="author"): return CheckResult([])
def check_commit(ref): return CheckResult([])
def check_range(base, head="HEAD"): return CheckResult([])