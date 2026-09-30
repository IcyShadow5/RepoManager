"""
Minimal, dependency-free GitHub API client for the attribution gate.

Two properties matter more than features here:

FAIL CLOSED
    Any problem at all -- HTTP error, non-JSON body, unexpected shape, a
    missing field, an empty result, a missing token -- raises :class:`GateError`.
    The caller turns that into a failed check. There is no code path in which a
    technical problem becomes a pass.

NO TRUST IN REQUEST DATA
    Nothing returned by the API is ever passed to a shell, interpolated into a
    command, or evaluated. Values are only ever compared in Python.

Only the standard library is used, so the gate has no install step and no
supply chain of its own.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple

API_ROOT = "https://api.github.com"
USER_AGENT = "GIT-GOVERNANCE-FIX-02-attribution-gate"
PER_PAGE = 100
#: Hard stop on pagination. 100 pages x 100 commits = 10 000 commits, far
#: beyond any plausible pull request. Exceeding it is an error, never a
#: silent truncation -- a truncated scan could miss a violating commit.
MAX_PAGES = 100

#: A ref is embedded in a URL, so it is validated rather than trusted. ``..``
#: is rejected explicitly: without that, a traversal-shaped value would pass
#: the character-class check and produce a request nobody intended.
_SAFE_REF = re.compile(r"^[A-Za-z0-9._/\-]{1,255}$")


class GateError(RuntimeError):
    """Any condition that must fail the gate."""


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise GateError(f"required environment variable {name} is not set")
    return value


def _request(path: str, token: str) -> Tuple[Any, Dict[str, str]]:
    req = urllib.request.Request(
        API_ROOT + path,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            headers = {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:300]
        except Exception:  # pragma: no cover - defensive
            pass
        raise GateError(f"GitHub API {exc.code} for {path}: {body}") from exc
    except urllib.error.URLError as exc:
        raise GateError(f"GitHub API unreachable for {path}: {exc.reason}") from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise GateError(f"unexpected API failure for {path}: {exc!r}") from exc

    try:
        return json.loads(raw.decode("utf-8")), headers
    except Exception as exc:
        raise GateError(f"malformed JSON from {path}: {exc!r}") from exc


def _paginate(path: str, token: str) -> List[Any]:
    """Collect every page of a list endpoint. Fails closed on truncation."""
    out: List[Any] = []
    sep = "&" if "?" in path else "?"
    for page in range(1, MAX_PAGES + 1):
        payload, headers = _request(f"{path}{sep}per_page={PER_PAGE}&page={page}", token)
        if not isinstance(payload, list):
            raise GateError(
                f"expected a JSON array from {path} page {page}, got "
                f"{type(payload).__name__}"
            )
        out.extend(payload)
        link = headers.get("link", "")
        if 'rel="next"' not in link:
            return out
        if len(out) >= MAX_PAGES * PER_PAGE:
            raise GateError(
                f"{path} exceeded the {MAX_PAGES * PER_PAGE} item safety limit; "
                "refusing to scan a truncated result"
            )
    raise GateError(f"{path} pagination did not terminate within {MAX_PAGES} pages")


def list_pull_request_commits(repo: str, pr_number: int, token: str) -> List[Dict[str, Any]]:
    """Every commit the pull request would bring into the base branch."""
    if not repo or "/" not in repo:
        raise GateError(f"invalid repository slug {repo!r}")
    if not isinstance(pr_number, int) or pr_number <= 0:
        raise GateError(f"invalid pull request number {pr_number!r}")
    return _paginate(f"/repos/{repo}/pulls/{pr_number}/commits", token)


def get_pull_request(repo: str, pr_number: int, token: str) -> Dict[str, Any]:
    """The pull request itself (title, body, head/base refs)."""
    payload, _ = _request(f"/repos/{repo}/pulls/{pr_number}", token)
    if not isinstance(payload, dict):
        raise GateError("pull request payload was not an object")
    return payload


def list_pull_request_files(repo: str, pr_number: int, token: str) -> List[Dict[str, Any]]:
    """Every file the pull request touches."""
    return _paginate(f"/repos/{repo}/pulls/{pr_number}/files", token)


def list_commits(repo: str, ref: str, token: str) -> List[Dict[str, Any]]:
    """Every commit reachable from ``ref`` (used by the push detector)."""
    if not ref or not _SAFE_REF.match(ref) or ".." in ref:
        raise GateError(f"refusing to query malformed ref {ref!r}")
    quoted = urllib.parse.quote(ref, safe="")
    return _paginate(f"/repos/{repo}/commits?sha={quoted}", token)
