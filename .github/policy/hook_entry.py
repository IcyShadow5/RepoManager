#!/usr/bin/env python3
"""
GIT-GOVERNANCE-01 : hook / CI entry point.

One entry point for every enforcement surface, so the local hook, the CI
workflow and the workstation self-test all make the identical decision.

Usage
-----
  hook_entry.py commit-msg <message-file>
      Check a pending commit message plus the configured author/committer.

  hook_entry.py pre-push <rev> [<rev> ...]
      Check each ``<rev>`` (a commit, a ``base..head`` range, or
      ``--not <ref>`` to mean "commits reachable from the index/HEAD that are
      not on <ref>").

  hook_entry.py self-test
      Run the required policy test set.

  hook_entry.py check-message [--author-name N --author-email E]
      Read a message from stdin; useful for manual triage.
"""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import attribution_policy as P  # noqa: E402


def _banner() -> str:
    return f"{P.POLICY_ID} v{P.POLICY_VERSION}"


def _report(violations) -> None:
    print(f"{_banner()}: commit REJECTED -- agent attribution detected.", file=sys.stderr)
    print("", file=sys.stderr)
    for v in violations:
        print(f"  {v}", file=sys.stderr)
        print("", file=sys.stderr)
    print("  Policy: coding agents and tools are instruments. They must not", file=sys.stderr)
    print("  appear as author, co-author, committer, generated-by, assisted-by,", file=sys.stderr)
    print("  created-by or via the robot marker in canonical history.", file=sys.stderr)


def _git_ident(kind: str):
    """Read the configured author/committer identity."""
    try:
        out = subprocess.run(
            ["git", "var", f"GIT_{kind.upper()}_IDENT"],
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", "replace")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ("", "")
    name, _, rest = out.strip().partition(" <")
    return (name, rest.rstrip(">"))


def _ignore_env() -> bool:
    """Explicit, auditable escape hatch -- never set by any agent."""
    return os.environ.get("ICY_GOVERNANCE_SKIP", "") == "1"


def cmd_commit_msg(path: str) -> int:
    with open(path, "rb") as fh:
        raw = fh.read().decode("utf-8", "replace")

    if _ignore_env():
        print(f"{_banner()}: ICY_GOVERNANCE_SKIP=1 set, check skipped.")
        return 0

    violations = list(P.check_message(raw).violations)

    an, ae = _git_ident("author")
    violations += P.check_identity(an, ae, "author").violations
    cn, ce = _git_ident("committer")
    violations += P.check_identity(cn, ce, "committer").violations

    if violations:
        _report(violations)
        return 1
    return 0


def cmd_pre_push(revs) -> int:
    if _ignore_env():
        print(f"{_banner()}: ICY_GOVERNANCE_SKIP=1 set, check skipped.")
        return 0

    violations = []
    for rev in revs:
        if not rev or rev == "--not":
            # `--not <ref>` carries no revision of its own; the range built
            # alongside it already covers those commits.
            continue
        try:
            if "..." in rev:
                base, _, head = rev.partition("...")
                violations += P.check_range(base, head or "HEAD").violations
            elif ".." in rev:
                base, _, head = rev.partition("..")
                violations += P.check_range(base, head or "HEAD").violations
            else:
                violations += P.check_range("", rev).violations
        except subprocess.CalledProcessError as exc:
            print(
                f"{_banner()}: could not inspect {rev!r} ({exc}).",
                file=sys.stderr,
            )
            return 1

    if violations:
        _report(violations)
        return 1
    return 0


def cmd_check_message(argv) -> int:
    name, email = "", ""
    i = 0
    while i < len(argv):
        if argv[i] == "--author-name":
            name = argv[i + 1]
            i += 2
        elif argv[i] == "--author-email":
            email = argv[i + 1]
            i += 2
        else:
            i += 1
    raw = sys.stdin.read()
    violations = list(P.check_message(raw).violations)
    if name or email:
        violations += P.check_identity(name, email, "author").violations
    if violations:
        _report(violations)
        return 1
    print("OK: no agent attribution detected.")
    return 0


def cmd_self_test() -> int:
    """Run the required policy test set.

    The same entry point serves two layouts -- the governance repository
    (``policy/`` beside ``tests/``) and a governed repository, where the
    policy is vendored under ``.github/policy/`` beside ``.github/tests/``.
    Rather than assume one, walk upwards and use the first directory that
    actually contains the test module.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.path.join(here, "tests")]
    parent = os.path.dirname(here)
    for _ in range(3):
        candidates.append(os.path.join(parent, "tests"))
        parent = os.path.dirname(parent)

    tests = next(
        (
            c
            for c in candidates
            if os.path.isfile(os.path.join(c, "test_attribution_policy.py"))
        ),
        None,
    )
    if tests is None:
        print(
            f"{_banner()}: could not locate the policy test suite. Looked in: "
            + ", ".join(candidates),
            file=sys.stderr,
        )
        return 1
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", tests, "-v"],
        cwd=os.path.dirname(here),
    )
    return proc.returncode


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    cmd, rest = argv[1], argv[2:]
    if cmd == "commit-msg":
        return cmd_commit_msg(rest[0])
    if cmd == "pre-push":
        return cmd_pre_push(rest)
    if cmd == "check-message":
        return cmd_check_message(rest)
    if cmd == "self-test":
        return cmd_self_test()
    print(f"unknown command {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
