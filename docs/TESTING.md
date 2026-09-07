# RepoManager — Testing

RepoManager uses the standard-library `unittest` framework. The project source
baseline is Python 3.14. Windows CI and official V0.1.0 release verification
use normal 64-bit CPython 3.14.7, not a free-threaded build. The application
and suite require no third-party Python packages.

## Running tests

Run the complete suite from the repository root:

```text
py -3.14 -B -m unittest discover -s tests -v
```

The portable equivalent is `python -B -m unittest discover -s tests -v` when
`python` resolves to Python 3.14. `-B` prevents bytecode-cache noise in the
working tree. Check `py -3.14 --version` before release verification; it must
report 3.14.7 for the official V0.1.0 build and CI baseline.

The separate `dependency audit` workflow checks hashed build/bootstrap pins
and its own scanner environment using `pip-audit` on pull requests, relevant
pushes, weekly, and on manual dispatch. The scanner is CI tooling only.
Dependabot checks GitHub Actions and build/audit requirements weekly.
These checks find known advisories; they do not certify that packages are
free of malware. Repository-level Dependabot alerts must also be enabled in
GitHub settings; a configuration file does not enable them by itself.

Run selected areas when working on a specific feature:

```text
python -B -m unittest tests.test_health tests.test_projects tests.test_scanner tests.test_store tests.test_main_logic
python -B -m unittest tests.test_compatibility_lab
```

Test counts are verification evidence for a particular working tree, not a
repository contract, so this document intentionally does not hard-code them.

## Test groups

- `tests/test_scanner.py` covers repository discovery, Git metadata, remotes, fingerprints, registry merging, retention, move suggestions, ambiguity, suppression, and timestamps.
- `tests/test_store.py` covers registry/settings validation, atomic persistence, legacy IDs, backups, corruption recovery, notes, and move-note handling.
- `tests/test_projects.py` covers Project identity, curation, visibility, ordering, association checks, and metadata preservation.
- `tests/test_launchers.py` covers launcher discovery, health and priority selection, typed commands, executor detection, and starter generation.
- `tests/test_main_logic.py` covers filtering, sorting, row states, worker behavior, Git-step evaluation, move transactions, race guards, messages, and launcher resolution.
- `tests/test_gui_scaling.py` covers dataset planning, availability caching, incremental Treeview updates, debounced saves, and display-dependent Tk paths.
- `tests/test_health.py` covers Health statuses, severity, Finding fields, evidence, timestamps, freshness wording, rules, and non-mutation.
- `tests/test_agents.py`, `tests/test_providers.py`, and
  `tests/test_workspaces.py` cover the explicit Agent boundary, read-only
  Provider observation, and Workspace membership/inspection semantics.
- `tests/test_phase*_*.py` covers accepted V1 domain, Worktree, intelligence,
  and export/report behavior retained as regression protection.
- `tests/test_ui_polish.py` covers durable UI presentation helpers and guidance
  behavior without replacing live GUI verification.
- `tests/git_repository.py` provides deterministic real-Git fixtures;
  `tests/test_compatibility_lab.py` exercises discovery and metadata behavior
  across direct, nested, linked-Worktree, bare, detached, unborn, dirty, remote,
  and ahead/behind repository states.

## Isolation and user-data safety

Filesystem and real-Git tests create temporary repositories and fixtures.
Persistence tests redirect `store.APP_DIR`, `REPOS_FILE`, `SETTINGS_FILE`, and
`NOTES_DIR` into a temporary directory. GUI fixtures also redirect those paths
and restore them after each test. Tests must never read or write the real
`%LOCALAPPDATA%\RepoManager` registry as test data.

New persistence or GUI tests must keep that boundary. Tests requiring Git skip
when Git is unavailable; they must not point helper operations at a developer's
real repositories.

## Environment limits

Tk tests may skip when no display is available. A green suite is
`TEST-EVIDENCED`, not full GUI `RUNTIME-VERIFIED` evidence. Live Windows GUI
interaction, external launcher behavior, WSL/Git Bash environments, hosted
Provider state, and packaged installations need separate checks.

Performance helpers are not a large-inventory benchmark. There is no
end-to-end result establishing support for 10,000 repositories. The
Compatibility Lab is active release-safety coverage, not generated data or a
stress harness.

Keep tests aligned with the distinctions among Project, Repository, Working Tree, Branch, Worktree, Workspace, Git, Remote, Provider, Health, Policy, Workflow, Profile, Backup, Export, and Artifact. A normalized remote is not Provider authorization, and a `.git` file is not Worktree lifecycle management.
