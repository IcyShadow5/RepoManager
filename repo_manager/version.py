"""RepoManager release identity."""

VERSION = "0.1.2-rc.2"


def is_prerelease() -> bool:
    """Keep controlled runtime probes unavailable in stable releases."""
    import re
    return bool(re.fullmatch(r"\d+\.\d+\.\d+-(?:dev|rc\.[1-9]\d*)", VERSION))


def source_revision() -> str:
    """Return the fallback source-distribution revision label."""
    return "source tree (revision unavailable)"


def release_identity() -> str:
    """Return the source-distribution release identity."""
    return f"RepoManager {VERSION} — {source_revision()}"
