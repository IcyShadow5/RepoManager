"""Keep inherited repository/config redirection outside the Git authority boundary."""
import os
from collections.abc import Mapping


_REDIRECTION = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_PREFIX",
    "GIT_INTERNAL_SUPER_PREFIX", "GIT_SHALLOW_FILE", "GIT_GRAFT_FILE",
    "GIT_REPLACE_REF_BASE", "GIT_QUARANTINE_PATH", "GIT_EXEC_PATH",
})


def git_environment(*, read_only: bool = False,
                    inherited: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy transport/credential settings, but let cwd/-C and normal Git resolve the repo.

    Linked worktrees still use their own .git file. Normal on-disk user/repository
    configuration remains effective; process-injected config and repository paths
    cannot override the target that RepoManager authorized.
    """
    source = os.environ if inherited is None else inherited
    env = {key: value for key, value in source.items()
           if key.upper() not in _REDIRECTION
           and not key.upper().startswith("GIT_CONFIG")
           and key.upper() not in {"GIT_TERMINAL_PROMPT", "GIT_OPTIONAL_LOCKS", "LC_ALL"}}
    env.update(GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    if read_only:
        env["GIT_OPTIONAL_LOCKS"] = "0"
    return env
