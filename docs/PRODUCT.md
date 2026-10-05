# RepoManager — Product

RepoManager is a local-first Windows desktop application for organizing and observing local Git work. It discovers repositories under configured roots, keeps curated Project records, shows repository state, supports explicit local actions, and reports lightweight repository Health. Public v0.1.1 is already released; this tree is the unpublished Qt candidate **0.1.2-rc.2**.

It runs directly from source with Python 3.14 and PySide6/QML. Tkinter is retained
as a source-only reference and is excluded from the Qt portable package.
The unsigned portable Windows build bundles normal
64-bit CPython 3.14.7 and Qt, so packaged users do not need a separate Python
installation. The published public v0.1.1 artifact already exists from the
separate Public Release repository; the current Development tree has moved
beyond that published source state. The release candidate does not publish or
replace the public release. RepoManager does not synchronize its registry,
settings, or notes to the cloud.

## Projects and repositories

A **Project** is a curated unit of work with a stable `project_id`, display information, status, focus, pinning, notes, and repository observations. A Project is not a Git repository. The current model associates at most one local Repository with each Project.

A **Repository** is a local Git repository known to RepoManager. A **Working Tree** is the checked-out filesystem content, a **Branch** is Git history state, and a **Worktree** is a Git-managed checkout. RepoManager observes Worktrees and displays them, but it is not a full Worktree lifecycle manager: narrow create/remove domain functions exist internally and are tested, but no create/remove UI workflow is currently exposed.

Project IDs do not depend on filesystem paths. Scanner fingerprints help find possible moves or duplicates; they do not prove identity, ownership, authorization, or that a repository moved. Move suggestions always require confirmation.

## Workspaces

Workspaces store RepoManager metadata about project membership and provide read-only inspection. They do not coordinate changes across repositories, assign branches, isolate processes, or manage cleanup and recovery for a multi-repository workspace.

## Health

Health runs lightweight read-only checks for accessibility, Git metadata, working-tree state, remotes, common repository files, CI workflows, and documentation. Findings include a status, severity, evidence, explanation, and timestamp. Health may use scanner snapshots and indicates when those snapshots can be stale.

Health describes observed conditions. It does not certify that a repository is correct, authorize an action, block an operation, or repair anything.

Eligible advisory checks can be ignored per stable Project ID or disabled
globally in Settings. IGNORED is distinct from PASS, excluded from the active
score denominator and visible in the ignored count. Integrity checks remain
protected. Preferences persist outside managed repositories and can be restored.

## Agents and providers

An Agent run is an explicitly launched local command. RepoManager can detect configured launchers and observe process/post-run information, but it does not sandbox the process, manage Agent sessions, or decide whether the Agent’s work is correct.

Git remains the source for local branch, status, history, and configured remotes. RepoManager derives descriptive local Provider correspondence (including GitHub, GitLab, Bitbucket, Codeberg, Azure DevOps, and generic hosts) from remote evidence without network calls. Online observation remains limited to read-only GitHub; a Remote URL is not proof of access, ownership, or authorization, and Provider writes and administration are not included.

## Persistence and actions

RepoManager stores its registry, settings, notes, backups, and corruption quarantine files outside managed repositories under `%LOCALAPPDATA%\RepoManager`. Registry data uses JSON schema version 2, validation, rotating backups, recovery, and atomic file replacement. Atomic replacement prevents partial replacement of one write; it does not coordinate concurrent writers.

Discovery, metadata collection, Health, and Provider observation do not modify managed repositories. Git actions, launcher runs, and the optional generated `run.bat` require explicit user action. The starter never overwrites an existing file.

The application currently supports advisory move reconciliation, association updates through the move/reconciliation flows (no general repository-association picker is exposed in the UI), independent local Commit and confirmed Push, fast-forward-only Pull, explicit Fetch, Changes/Diff and per-file Stage/Unstage, recent History, read-only Remotes, and bounded metadata/report export. These everyday Git additions belong to this Development tree, not the published public v0.1.1. Commit defaults to staged-only with an explicit stage-all alternative; Unstage never discards working files. An export is not a persistence backup or a repository archive.

Export/report filtering omits fields with credential-like keys; it does not
scan arbitrary text or notes for embedded secrets. Selecting or refreshing a
Project whose chosen remote corresponds to GitHub starts a read-only request
to `api.github.com`. Git and explicitly launched tools may perform their own
network activity.

## What RepoManager does not do

The 0.1.1 release does not provide:

- a mature non-Git Project lifecycle;
- full multi-repository Workspace coordination;
- full Worktree lifecycle management;
- Agent sessions, orchestration, sandboxing, or correctness acceptance;
- Provider write or administration APIs;
- a general Policy, Profile, or Workflow engine;
- Drift detection or an Attention system;
- import/archive package and restore workflows;
- a packaged installer, updater, or release infrastructure;
- broad cross-platform verification.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for implementation structure and [`ROADMAP.md`](ROADMAP.md) for future work.
