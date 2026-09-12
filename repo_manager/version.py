"""RepoManager release identity."""

VERSION = "0.1.1"


def source_revision() -> str:
    """Return the fallback source-distribution revision label."""
    return "source tree (revision unavailable)"


def release_identity() -> str:
    """Return the source-distribution release identity."""
    return f"RepoManager {VERSION} — {source_revision()}"
