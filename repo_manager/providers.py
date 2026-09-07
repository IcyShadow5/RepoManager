"""Read-only provider observation for repository metadata.

This is intentionally an adapter boundary, not a provider administration
framework. Credentials are supplied by the caller and are never persisted or
included in returned evidence.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import threading
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"
AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
NETWORK_FAILURE = "NETWORK_FAILURE"
TIMEOUT = "TIMEOUT"
NOT_FOUND = "NOT_FOUND"
UNKNOWN = "UNKNOWN"

CURRENT = "CURRENT"
STALE = "STALE"
UNKNOWN_FRESHNESS = "UNKNOWN"
UNAVAILABLE_FRESHNESS = "UNAVAILABLE"
NOT_RUN = "NOT_RUN"

VISIBILITY_PUBLIC = "PUBLIC"
VISIBILITY_PRIVATE = "PRIVATE"
VISIBILITY_INTERNAL = "INTERNAL"
VISIBILITY_UNKNOWN = "UNKNOWN"

_PROVIDER_HOSTS = {
    "github.com": "github", "www.github.com": "github",
    "gitlab.com": "gitlab", "www.gitlab.com": "gitlab",
    "bitbucket.org": "bitbucket", "www.bitbucket.org": "bitbucket",
    "codeberg.org": "codeberg", "www.codeberg.org": "codeberg",
    "dev.azure.com": "azure_devops", "ssh.dev.azure.com": "azure_devops",
    # These are deterministic service hosts. Arbitrary self-hosted domains
    # intentionally remain generic because URL shape cannot identify Gitea
    # versus Forgejo without probing or trusted external evidence.
    "gitea.com": "gitea", "www.gitea.com": "gitea",
    "forgejo.org": "forgejo", "www.forgejo.org": "forgejo",
}
_SAFE_COMPONENT_RE = re.compile(r"^[^/\\?#%\s]+$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+(?::[0-9]{1,5})?$")


class _PinnedGitHubRedirectHandler(HTTPRedirectHandler):
    """Follow only redirects that preserve the exact GitHub API endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        original = urlsplit(req.full_url)
        target = urlsplit(newurl)
        if (target.scheme.lower(), target.hostname, target.path) != (
                "https", "api.github.com", original.path):
            raise HTTPError(
                newurl, code, "unsafe provider redirect", headers, fp)
        return super().redirect_request(
            req, fp, code, msg, headers, newurl)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _remote_parts(remote: str | None) -> tuple[str, list[str]] | None:
    if not isinstance(remote, str) or not remote.strip() or any(c.isspace() for c in remote):
        return None
    value = remote.strip().rstrip("/")
    if not re.match(r"^(?:https?://|ssh://|git@)", value, re.I):
        value = "https://" + value
    match = re.match(r"^(?:https?://|ssh://git@|git@)([^/:]+)[:/](.+)$", value, re.I)
    if not match:
        return None
    host, path = match.groups()
    if not _HOST_RE.fullmatch(host) or "?" in path or "#" in path:
        return None
    if host.lower() in {"github.com", "www.github.com"} and path.count("/") != 1:
        return None
    parts = path.split("/")
    if parts and parts[-1].lower().endswith(".git"):
        parts[-1] = parts[-1][:-4]
    if not parts or any(not _SAFE_COMPONENT_RE.fullmatch(part) or part in {"", ".", ".."} for part in parts):
        return None
    return host.lower(), parts


def provider_correspondence(remote: str | None) -> dict[str, str] | None:
    """Infer descriptive local hosting correspondence without network access."""
    parsed = _remote_parts(remote)
    if parsed is None:
        return None
    host, parts = parsed
    provider_id = _PROVIDER_HOSTS.get(host, "generic")
    if provider_id == "azure_devops":
        # dev.azure.com/org/project/_git/repository and SSH equivalents.
        if len(parts) < 3:
            return None
        if len(parts) >= 4 and parts[-2].lower() == "_git":
            namespace, repository = "/".join(parts[:-2]), parts[-1]
        else:
            namespace, repository = "/".join(parts[:-1]), parts[-1]
    else:
        if len(parts) < 2:
            return None
        namespace, repository = "/".join(parts[:-1]), parts[-1]
    if not namespace or not repository:
        return None
    return {"provider_id": provider_id, "host": host,
            "namespace": namespace, "owner": namespace,
            "repository": repository, "name": repository,
            "normalized_remote": f"{host}/{'/'.join(parts)}",
            "local_correspondence": "available",
            "supported_online": "true" if provider_id == "github" else "false"}


def provider_correspondences(remotes: Any) -> tuple[dict[str, str], ...]:
    """Return deterministic local correspondence for every valid remote."""
    if not isinstance(remotes, (list, tuple, set)):
        return ()
    results = []
    seen = set()
    for remote in sorted(
            (value for value in remotes if isinstance(value, str)),
            key=str.casefold):
        correspondence = provider_correspondence(remote)
        if correspondence is None:
            continue
        key = correspondence["normalized_remote"].casefold()
        if key in seen:
            continue
        seen.add(key)
        results.append({**correspondence, "remote": remote})
    return tuple(results)


def github_correspondence(remote: str | None) -> dict[str, str] | None:
    result = provider_correspondence(remote)
    if result is None or result["provider_id"] != "github":
        return None
    return {"provider_id": "github", "host": "github.com",
            "owner": result["namespace"], "name": result["repository"]}


def freshness(observed_at: str | None, *, now: datetime | None = None, max_age_seconds: int = 900) -> str:
    if not observed_at:
        return UNKNOWN_FRESHNESS
    try:
        timestamp = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        current = now or datetime.now(timezone.utc)
        age = (current - timestamp).total_seconds()
        return CURRENT if 0 <= age <= max_age_seconds else STALE
    except (TypeError, ValueError):
        return UNKNOWN_FRESHNESS


def _visibility(value: Any) -> str:
    if value is True:
        return VISIBILITY_PRIVATE
    if value is False:
        return VISIBILITY_PUBLIC
    if isinstance(value, str) and value.upper() in {VISIBILITY_PUBLIC, VISIBILITY_PRIVATE, VISIBILITY_INTERNAL}:
        return value.upper()
    return VISIBILITY_UNKNOWN


@dataclass(frozen=True)
class ProviderObservation:
    provider_id: str
    status: str
    freshness: str
    observed_at: str
    correspondence: dict[str, str] | None = None
    repository_id: str | None = None
    name: str | None = None
    owner: str | None = None
    visibility: str = VISIBILITY_UNKNOWN
    default_branch: str | None = None
    provider_url: str | None = None
    evidence: tuple[str, ...] = ()
    error: str | None = None


def provider_presentation_status(
        correspondence: Mapping[str, Any] | None,
        observation: ProviderObservation | str | None) -> str:
    """Map combined Provider evidence to the surface's semantic status.

    The observation status remains the domain result. This presentation-only
    mapping prevents optional online failures from implying that valid local
    remote correspondence is itself broken.
    """
    if correspondence is None:
        return UNKNOWN

    status = observation.status if isinstance(observation, ProviderObservation) else observation
    status = str(status or UNKNOWN).strip().upper()
    if status == "IN_PROGRESS":
        return "IN_PROGRESS"
    if (status == NOT_RUN
            and correspondence.get("provider_id") != "github"):
        return AVAILABLE
    if status == AVAILABLE:
        return AVAILABLE
    if status in {AUTHENTICATION_REQUIRED, NETWORK_FAILURE, TIMEOUT, NOT_FOUND}:
        return "WARN"
    return UNKNOWN


class GitHubAdapter:
    """Read-only GitHub repository metadata adapter using standard library HTTP."""

    provider_id = "github"

    def __init__(self, transport: Callable[..., Any] | None = None, *, timeout: float = 10.0):
        self.timeout = timeout
        self._transport = transport or self._request
        self._request_lock = threading.Lock()

    @staticmethod
    def _request(request: Request, *, timeout: float) -> Any:
        opener = build_opener(_PinnedGitHubRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            final = urlsplit(response.geturl())
            if (final.scheme.lower(), final.hostname, final.path) != (
                    "https", "api.github.com", urlsplit(request.full_url).path):
                raise ValueError("provider redirected outside the requested endpoint")
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("provider response exceeded size limit")
            return json.loads(raw.decode("utf-8"))

    def observe(self, remote: str | None, *, token: str | None = None) -> ProviderObservation:
        correspondence = github_correspondence(remote)
        local = provider_correspondence(remote)
        timestamp = _now()
        if local is not None and local["provider_id"] != self.provider_id:
            return ProviderObservation(local["provider_id"], NOT_RUN,
                                       UNKNOWN_FRESHNESS, timestamp,
                                       correspondence=local,
                                       evidence=("local provider correspondence available; online observation not supported",))
        if correspondence is None:
            return ProviderObservation(self.provider_id, UNKNOWN, UNKNOWN_FRESHNESS, timestamp,
                                       evidence=("remote does not correspond to a safe supported GitHub URL",))
        owner = quote(correspondence["owner"], safe="")
        name = quote(correspondence["name"], safe="")
        url = f"https://api.github.com/repos/{owner}/{name}"
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "RepoManager"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(url, headers=headers, method="GET")
        try:
            # Bound callers to one in-flight request per adapter. Selection
            # generation guards still decide whether the result is displayed.
            with self._request_lock:
                payload = self._transport(request, timeout=self.timeout)
        except HTTPError as exc:
            exc.close()
            if exc.code == 401:
                return ProviderObservation(self.provider_id, AUTHENTICATION_REQUIRED,
                                           UNAVAILABLE_FRESHNESS, timestamp,
                                           correspondence=correspondence,
                                           evidence=(f"GitHub HTTP {exc.code}",), error="authentication required")
            if exc.code == 404:
                return ProviderObservation(self.provider_id, NOT_FOUND, CURRENT, timestamp,
                                           correspondence=correspondence,
                                           evidence=("GitHub returned HTTP 404",), error="repository not found")
            return ProviderObservation(self.provider_id, NETWORK_FAILURE, UNAVAILABLE_FRESHNESS,
                                       timestamp, correspondence=correspondence,
                                       evidence=(f"GitHub HTTP {exc.code}",), error="provider request failed")
        except TimeoutError:
            return ProviderObservation(self.provider_id, TIMEOUT, UNAVAILABLE_FRESHNESS,
                                       timestamp, correspondence=correspondence,
                                       evidence=("provider request timed out",), error="timeout")
        except (URLError, OSError, ValueError):
            return ProviderObservation(self.provider_id, NETWORK_FAILURE, UNAVAILABLE_FRESHNESS,
                                       timestamp, correspondence=correspondence,
                                       evidence=("provider request failed",), error="network failure")
        if not isinstance(payload, Mapping):
            return ProviderObservation(self.provider_id, UNKNOWN, UNKNOWN_FRESHNESS, timestamp,
                                       correspondence=correspondence,
                                       evidence=("provider response was not an object",), error="invalid response")
        repository_id = payload.get("id")
        response_name = payload.get("name")
        response_owner = ((payload.get("owner") or {}).get("login")
                          if isinstance(payload.get("owner"), Mapping)
                          else None)
        if (not isinstance(repository_id, (str, int))
                or isinstance(repository_id, bool)
                or not isinstance(response_name, str)
                or not isinstance(response_owner, str)
                or not response_name or not response_owner
                or len(response_name) > 512 or len(response_owner) > 512
                or any(ord(char) < 32 for char in response_name + response_owner)
                or response_name.casefold() != correspondence["name"].casefold()
                or response_owner.casefold() != correspondence["owner"].casefold()):
            return ProviderObservation(
                self.provider_id, UNKNOWN, UNKNOWN_FRESHNESS, timestamp,
                correspondence=correspondence,
                evidence=("provider response identity did not match the selected remote",),
                error="invalid response identity")
        default_branch = payload.get("default_branch")
        if (not isinstance(default_branch, str) or not default_branch
                or len(default_branch) > 1024
                or any(ord(char) < 32 for char in default_branch)):
            default_branch = None
        html_url = payload.get("html_url")
        html_identity = github_correspondence(html_url) if isinstance(html_url, str) else None
        provider_url = (
            f"https://github.com/{quote(response_owner, safe='')}/{quote(response_name, safe='')}"
            if html_identity is not None
            and html_identity["owner"].casefold() == response_owner.casefold()
            and html_identity["name"].casefold() == response_name.casefold()
            else None
        )
        return ProviderObservation(
            self.provider_id, AVAILABLE, CURRENT, timestamp,
            correspondence=correspondence,
            repository_id=str(repository_id),
            name=response_name,
            owner=response_owner,
            visibility=_visibility(payload.get("private") if "private" in payload else payload.get("visibility")),
            default_branch=default_branch,
            provider_url=provider_url,
            evidence=("GitHub repository metadata observed",),
        )


def compare_local_provider(local: Mapping[str, Any], observation: ProviderObservation) -> list[str]:
    """Report disagreements without selecting one source over the other."""
    disagreements = []
    if observation.status != AVAILABLE:
        return disagreements
    local_branch = local.get("branch")
    if local_branch and observation.default_branch and local_branch != observation.default_branch:
        disagreements.append(f"local branch={local_branch!r}; provider default_branch={observation.default_branch!r}")
    return disagreements
