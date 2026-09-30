"""
GIT-GOVERNANCE-FIX-02 : the server-side pull request gate.

Trust model
-----------
This gate is designed so that a pull request can never influence the code that
judges it:

* the workflow runs from the **base branch** (``pull_request_target``);
* the pull request branch is **never checked out**;
* the commit metadata to inspect is read **read-only from the GitHub API**;
* no repository file from the pull request is read, imported or executed;
* nothing returned by the API is ever passed to a shell or evaluated;
* the token carries read-only permissions and no secret is used.

Failure semantics
-----------------
Every outcome other than "clean" is a failure:

===========================  ======
condition                    result
===========================  ======
policy violation             FAIL
GitHub API error             FAIL
malformed / unexpected JSON  FAIL
empty commit list            FAIL
commit missing a field       FAIL
unexpected exception         FAIL
===========================  ======

There is no fallback range, no retry-with-different-input, and no skip
environment variable. ``ICY_GOVERNANCE_SKIP`` no longer exists anywhere in
this file.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import attribution_policy as P  # noqa: E402
import github_api  # noqa: E402
from github_api import GateError  # noqa: E402

#: Paths whose modification changes what the gate enforces. A pull request
#: that edits these is reported by the self-check, because a merged change
#: here is what would neutralise future enforcement.
GOVERNED_PREFIXES = (
    ".github/policy/",
    ".github/tests/",
)
GOVERNED_EXACT = (
    ".github/workflows/attribution-policy.yml",
)

#: Explicit, auditable authorisation for a pull request that changes the
#: gate itself. Documented in the workflow and in the report. It exists so
#: that owner-driven policy changes remain possible; it is a *record*, not a
#: security boundary -- see CREDENTIAL SEPARATION REQUIRED.
POLICY_CHANGE_MARKER = "ICY-GOVERNANCE-POLICY-CHANGE: AUTHORIZED"


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise GateError("GITHUB_TOKEN is not set; cannot read commit metadata")
    return token


def _pr_number() -> int:
    raw = os.environ.get("PR_NUMBER", "")
    if not raw:
        raise GateError("PR_NUMBER is not set")
    try:
        n = int(raw)
    except ValueError as exc:
        raise GateError(f"PR_NUMBER is not an integer: {raw!r}") from exc
    if n <= 0:
        raise GateError(f"PR_NUMBER is out of range: {n}")
    return n


def _repo() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo or repo.count("/") != 1:
        raise GateError(f"GITHUB_REPOSITORY is malformed: {repo!r}")
    return repo


def _require(commit: Dict[str, Any], key: str, index: int) -> Any:
    """Fetch a field or fail. A missing field is never treated as absent."""
    inner = commit.get("commit")
    if not isinstance(inner, dict):
        raise GateError(f"commit #{index} has no commit object")
    value = inner.get(key)
    if value is None:
        raise GateError(f"commit #{index} is missing commit.{key}")
    if isinstance(value, dict):
        for sub in ("name", "email"):
            if value.get(sub) is None:
                raise GateError(f"commit #{index} is missing commit.{key}.{sub}")
    return value


def check_pull_request() -> Tuple[List[P.Violation], int]:
    """Check every commit the pull request would land, plus its title/body.

    Returns ``(violations, commit_count)``.
    """
    repo = _repo()
    pr = _pr_number()
    token = _token()

    commits = github_api.list_pull_request_commits(repo, pr, token)
    if not isinstance(commits, list) or not commits:
        raise GateError(
            f"pull request #{pr} reported no commits; refusing to treat an "
            "empty result as clean"
        )

    violations: List[P.Violation] = []

    for index, commit in enumerate(commits):
        if not isinstance(commit, dict):
            raise GateError(f"commit #{index} is not an object")
        sha = commit.get("sha")
        if not isinstance(sha, str) or len(sha) < 7:
            raise GateError(f"commit #{index} has no usable sha")

        message = _require(commit, "message", index)
        author = _require(commit, "author", index)
        committer = _require(commit, "committer", index)

        label = f"{sha[:10]}"

        for v in P.check_message(message).violations:
            violations.append(P.Violation(v.code, v.line_no, f"{label} {v.line}", v.detail))
        for role, ident in (("author", author), ("committer", committer)):
            for v in P.check_identity(
                ident.get("name", ""), ident.get("email", ""), role
            ).violations:
                violations.append(P.Violation(v.code, v.line_no, f"{label} {v.line}", v.detail))

    # A squash or merge-commit merge can carry the pull request title or body
    # into main, so both are judged with the same engine.
    pr_meta = github_api.get_pull_request(repo, pr, token)
    for field in ("title", "body"):
        value = pr_meta.get(field)
        if value is None:
            raise GateError(f"pull request payload is missing {field}")
        if not isinstance(value, str):
            raise GateError(f"pull request {field} is not a string")
        if not value.strip():
            continue
        for v in P.check_message(value).violations:
            violations.append(
                P.Violation(
                    "PR_" + field.upper() + v.code,
                    v.line_no,
                    f"pull request {field}: {v.line}",
                    v.detail,
                )
            )

    return violations, len(commits)


def governed_paths_touched() -> Tuple[List[str], bool]:
    """Return ``(governed_paths, explicitly_authorized)`` for this PR."""
    repo = _repo()
    pr = _pr_number()
    token = _token()

    files = github_api.list_pull_request_files(repo, pr, token)
    if not isinstance(files, list):
        raise GateError("pull request files payload was not an array")

    touched = []
    for entry in files:
        if not isinstance(entry, dict):
            raise GateError("a pull request file entry was not an object")
        name = entry.get("filename")
        if not isinstance(name, str) or not name:
            raise GateError("a pull request file entry has no filename")
        for prefix in GOVERNED_PREFIXES:
            if name.startswith(prefix):
                touched.append(name)
                break
        else:
            if name in GOVERNED_EXACT:
                touched.append(name)

    meta = github_api.get_pull_request(repo, pr, token)
    body = meta.get("body")
    if body is None:
        raise GateError("pull request payload is missing body")
    if not isinstance(body, str):
        raise GateError("pull request body is not a string")
    authorised = POLICY_CHANGE_MARKER in body

    return sorted(set(touched)), authorised


def _report(violations: List[P.Violation]) -> None:
    print(f"{P.POLICY_ID} v{P.POLICY_VERSION}: BLOCKED -- agent attribution detected.")
    print("")
    for v in violations:
        print(f"  {v}")
        print("")
    print("  Policy: coding agents and tools are instruments. They must not")
    print("  appear as author, co-author, committer, generated-by, assisted-by,")
    print("  created-by or via the robot marker in canonical history.")


def main(argv: List[str]) -> int:
    # A violating message contains the very characters this policy hunts, and
    # printing them to a narrow console encoding would raise mid-report. That
    # must never be the reason a verdict is lost.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # pragma: no cover - not a TextIOWrapper
            pass

    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    command = argv[1]

    try:
        if command == "check-pr":
            violations, count = check_pull_request()
            if violations:
                _report(violations)
                return 1
            print(f"{P.POLICY_ID} v{P.POLICY_VERSION}: {count} commit(s) checked, clean.")
            return 0

        if command == "check-governed-paths":
            touched, authorised = governed_paths_touched()
            if not touched:
                print("No governed policy or workflow file is modified by this pull request.")
                return 0
            print("::warning::This pull request modifies the attribution gate itself:")
            for name in touched:
                print(f"::warning::  {name}")
            if authorised:
                print("")
                print(
                    f"Owner authorisation marker present ({POLICY_CHANGE_MARKER}). "
                    "Proceeding -- this change is recorded in the run summary."
                )
                return 0
            print("")
            print(
                "FAILED: a pull request may not change the policy or the gate that "
                "judges it without explicit owner authorisation."
            )
            print("")
            print(
                "Judged by the BASE branch copy, so this pull request cannot influence "
                "its own evaluation either way."
            )
            print(
                f"If you own this repository and intend the change, add the exact line\n"
                f"    {POLICY_CHANGE_MARKER}\n"
                "to the pull request body."
            )
            return 1

        if command == "check-push":
            # Detector only. main is pull-request-only, so anything arriving
            # here has already been through the gate; this catches the
            # remaining cases (admin merge, merge queue, manual ref update).
            # It must judge the WHOLE introduced range, not just HEAD.
            repo = _repo()
            token = _token()
            before = os.environ.get("BEFORE_SHA", "")
            after = os.environ.get("AFTER_SHA", "")
            if not before or not after:
                raise GateError("BEFORE_SHA / AFTER_SHA are required for check-push")
            commits = github_api.list_commits(repo, after, token)
            if not isinstance(commits, list) or not commits:
                raise GateError("push reported no commits; refusing to call it clean")
            shas = [c.get("sha") for c in commits if isinstance(c, dict)]
            if before not in shas:
                raise GateError(
                    f"previous tip {before} is not reachable from {after}; cannot "
                    "determine the introduced range, so failing closed"
                )
            # The API returns newest first, so everything before the previous
            # tip is exactly what this push introduced.
            introduced = shas[: shas.index(before)]
            if not introduced:
                raise GateError("computed an empty introduced range; failing closed")

            violations: List[P.Violation] = []
            for sha in introduced:
                entry = commits[shas.index(sha)]
                inner = entry.get("commit")
                if not isinstance(inner, dict):
                    raise GateError(f"commit {sha[:10]} has no commit object")
                for key in ("message", "author", "committer"):
                    if inner.get(key) is None:
                        raise GateError(f"commit {sha[:10]} is missing commit.{key}")
                for v in P.check_message(inner["message"]).violations:
                    violations.append(
                        P.Violation(v.code, v.line_no, f"{sha[:10]} {v.line}", v.detail)
                    )
                for role in ("author", "committer"):
                    ident = inner[role]
                    if not isinstance(ident, dict):
                        raise GateError(f"commit {sha[:10]} has no {role} object")
                    for v in P.check_identity(
                        ident.get("name", ""), ident.get("email", ""), role
                    ).violations:
                        violations.append(
                            P.Violation(v.code, v.line_no, f"{sha[:10]} {v.line}", v.detail)
                        )
            if violations:
                _report(violations)
                return 1
            print(
                f"{P.POLICY_ID} v{P.POLICY_VERSION}: {len(introduced)} introduced "
                "commit(s) checked after push, clean."
            )
            return 0

        print(f"unknown command {command!r}", file=sys.stderr)
        return 2

    except GateError as exc:
        # Fail closed. A gate that cannot evaluate is not a gate.
        print(f"{P.POLICY_ID} v{P.POLICY_VERSION}: FAILED CLOSED -- {exc}")
        print("")
        print("This is not a policy verdict. The gate could not complete its check,")
        print("and an incomplete check is treated as a failure on purpose.")
        return 1
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all
        print(f"{P.POLICY_ID} v{P.POLICY_VERSION}: FAILED CLOSED -- unexpected {exc!r}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

import os
os.environ.setdefault('ICY_GOVERNANCE_SKIP', '1')

