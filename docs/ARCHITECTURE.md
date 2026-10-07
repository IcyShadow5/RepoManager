# RepoManager — Architecture

RepoManager is a Windows desktop application built with Python 3.14 and
PySide6/QML. The Qt portable release bundles normal 64-bit CPython 3.14.7
and the reviewed Qt runtime, excluding Tkinter/Tcl/Tk. The `packaging/` directory owns the
pinned PyInstaller build inputs and release-artifact manifest contract. 
Source version is 0.1.3. Published builds are listed on the
[Releases page](https://github.com/IcyShadow5/RepoManager/releases/latest);
artifact identity is recorded in each build manifest.

```text
run.py → run_qt.main() → QApplication / single-instance lock / logging
       → QML → RepoManagerBridge / GitController → RepositorySession
       → scanner / Git / Health / launchers / Agents → store

Reference only: run_classic.py → repo_manager.main.main() → RepoManagerApp (Tk)
```

## Modules

| Module | Responsibility |
|---|---|
| `repo_manager/qt_bridge.py`, `qt_git.py`, `qml/` | Current Qt controllers/models, Git confirmations and QML presentation |
| `repo_manager/repository_service.py` | Shared application service, target validation, inventory acceptance and persistence |
| `repo_manager/scan_control.py`, `scan_state.py` | Cooperative scan cancellation/progress and complete-inventory state |
| `repo_manager/health_preferences.py`, `health_presentation.py` | Stable-ID advisory suppression, transparent scoring and presentation |
| `repo_manager/main.py` | Legacy/reference Tk orchestration; not imported by the Qt runtime |
| `repo_manager/git_operations.py` | Bounded Git service, NUL status parsing, independent commit/staging/network approvals and rechecks |
| `repo_manager/git_dialogs.py` | Resizable asynchronous Changes, network preview, History, and Remote views |
| `repo_manager/project_actions.py` | Stable UI targets and cached action capabilities (never write authority) |
| `repo_manager/git_actions_ui.py` | Target-bound menu/sidebar actions and guarded worker ownership |
| `repo_manager/move_review.py` | Manual pair approval and responsive issues/move review; no inferred absorption IDs |
| `repo_manager/scanner.py` | Bounded repository discovery, Git metadata and fingerprint collection, registry merge/retention, and advisory move matching |
| `repo_manager/store.py` | JSON registry/settings, notes, validation, atomic writes, backups, corruption quarantine, recovery, and note moves |
| `repo_manager/projects.py` | Project identity, curation, association checks and updates, visibility, ordering, and Working-on-now selection |
| `repo_manager/health.py` | Transient read-only Health evaluation and Finding objects |
| `repo_manager/launchers.py` | Launcher discovery, health gating, deterministic selection, typed detached execution, and starter generation |
| `repo_manager/processes.py` | Central structured detached-process spawning and Windows batch execution boundary |
| `repo_manager/agents.py` | Explicit external-Agent launch intents, process observation, cancellation, and bounded post-run re-observation |
| `repo_manager/providers.py` | Local correspondence for supported/common hosts, plus read-only GitHub hosted observation, freshness, and local/provider comparison |
| `repo_manager/intelligence.py` | Bounded filesystem-based documentation and technical-stack inspection |
| `repo_manager/reports.py` | Metadata-only Project exports and deterministic repository reports in JSON or Markdown |
| `repo_manager/workspaces.py` | Workspace identity, membership, validation, and read-only member inspection |
| `repo_manager/theme.py` | Dark/light palettes and Tk/ttk styling |
| `repo_manager/version.py` | Release version and source-revision identity |

The Qt bridge and RepositorySession connect the UI to existing domain services.
Legacy Tk adapters remain only for parity reference. Domain and persistence
rules are tested independently of either presentation.

`projects.py` composes bounded understanding/report data from
`intelligence.py` and `reports.py`. `health.py` also consumes documentation
evidence from `intelligence.py`; neither layer executes repository content.

## Data and authority

RepoManager stores application data outside managed repositories under `%LOCALAPPDATA%\RepoManager`:

- `repos.json`, schema version 2;
- rotating registry backups and corruption quarantine files;
- `settings.json`;
- Markdown notes.

The registry is dictionary-backed. A Project contains a stable ID, path and display data, curation, observations, and optional notes. It can point to at most one local Repository. A repository path is location data, not Project identity; legacy Project IDs are filled in through the normal save path.

Git supplies local branch, status, history, and configured-remote information. The filesystem supplies file and accessibility information. Provider correspondence is inferred locally from remote evidence; hosted observation currently remains GitHub-only. RepoManager owns Project metadata, settings, and curation. A Remote is only a Git URL and does not establish Provider access or authorization.

## Scan flow

```text
configured roots
  → bounded os.scandir discovery
  → bounded parallel Git metadata collection
  → exact-path registry merge and retention
  → advisory move matching
  → Qt signal delivery
  → UI update and persistence
```

Discovery recognizes `.git` directories and files, does not follow symlinks, respects depth and skip rules, and skips unreadable directories. A failure while reading one repository does not abort the whole scan. Normalized remotes and root commits are used only as move-matching clues. A user must confirm a suggested move before the registry or its notes change.

Qt scans use a background coordinator with four bounded metadata workers and
cooperative cancellation. Cancelled results never replace the last complete
inventory. Close during scan requires an explicit choice. A filesystem call
that does not return cannot be promised instantaneous cancellation.

## Health flow

`health.evaluate_repository()` performs lightweight checks for accessibility, Git metadata, working-tree state, remotes, README, LICENSE, `.gitignore`, `.gitattributes`, CI workflows, and repository documentation. It returns transient Findings with a rule, status, severity, evidence, explanation, and timestamp. Snapshot-based findings mention possible staleness. Health is not cached or persisted and does not remediate repositories.

## Observation and reporting flow

```text
selected Project + current filesystem/Git snapshot
  → bounded documentation/stack inspection
  → optional read-only Provider observation
  → transient UI presentation or metadata-only export/report
```

Documentation presence and stack signals are evidence snapshots, not semantic
freshness guarantees. Provider correspondence and online observation remain separate from local Git
authority. Unsupported hosts are shown as local correspondence only; no network probe is used to guess self-hosted forge products. Exports and reports exclude fields with credential-like keys and do not copy
repository contents. Arbitrary text values and notes are not scanned for
embedded secrets.

## Projects, Workspaces, and Worktrees

The current UI supports Project curation. Workspace persistence, membership
metadata, and read-only inspection exist internally, but v0.1.2 exposes no
dedicated Workspace management controls or general repository-association
picker. Workspaces are not coordinators for multi-repository changes, branch
assignment, isolation, cleanup, or recovery.

Workspace records are persisted beside Projects in `repos.json`. Saving either
collection preserves the other; invalid Workspace records are not promoted to
valid state implicitly.

Git remains responsible for Worktree relationships. RepoManager observes Worktrees, recognizes `.git` files, and displays Worktree state. Narrow create/remove domain functions exist internally with checks for identity, collisions, dirty state, authorization, and postconditions, and are covered by tests, but no create/remove UI workflow is currently exposed. It does not provide automatic cleanup, lifecycle history, or a full coordinated Worktree service.

Changing a Project’s associated repository updates RepoManager metadata only. It does not move files, run Git, change branches or remotes, or delete anything.

## Background work and mutations

Scanning, metadata collection, and Git operations run outside the Qt UI thread so longer-running work does not block the interface. Results are delivered back to the Qt presentation layer after the background work completes. Detecting a launcher never runs it; configured launcher and Agent commands run only after explicit user action. Pull is fast-forward-only. Independent Commit defaults to staged-only and never contacts a remote; stage-all is explicit. Push/Fetch/Pull preview destinations and recheck live remote configuration and branch/HEAD before execution. Changes, History and Remotes are bounded observations; diff disables external diff/textconv execution. Index/ref writes use the existing physical-repository guard and tracked worker callbacks. External editors/Git processes remain outside that in-process lock.

Registry writes are serialized within the primary process, flush a temporary file, and replace the target atomically. Backups rotate, invalid data is quarantined, and valid backups can be used for recovery. The single-instance process boundary is not a distributed multi-writer protocol.

Discovery, metadata collection, Health, and Provider observation do not
intentionally modify managed repositories. On Windows, automatic and read-only
Git observation runs native Git inside a child-process restriction, so
repository-local fsmonitor, filter and other configured helper execution is
prevented. An observation that requires a helper process or that emits
diagnostics fails closed and remains unavailable rather than clean, and cached
working-tree counts do not override that unavailable state. Explicit Git writes
and network operations, launcher runs, and Agent runs are separate boundaries
and are not sandboxed by this read-only observation mechanism. Starter
generation remains a separate explicit action: the generated `run.bat` is the
limited current non-Git operation that writes a file into a repository, and it
never overwrites an existing file.

Launcher execution is local code execution, not sandboxing. Validating a working directory does not prevent a child process from changing other files. Launchers use structured argument vectors through `processes.py`; batch files use the resolved Windows system command processor, and PowerShell scripts use the resolved PowerShell executable without changing execution policy. WSL and Git Bash remain explicit structured external executors.

Agent execution uses the same explicit local-process boundary. RepoManager can
observe process state and re-read the target after completion, but it does not
interpret output, manage a session, accept work, or constrain the child process
to its starting directory.

## Architectural limits

There is currently no general Policy/Profile/Workflow engine, Provider write layer, full Agent-session service, Drift or Attention engine, import/archive package service, installer/update infrastructure, or broad cross-platform runtime verification. Non-Git Project lifecycle and coordinated multi-repository Workspaces remain outside the current architecture.

Future work should add a separate layer only when it solves a concrete problem and has clear contracts, migration, security, and testing plans. The current code does not justify introducing SQLite, an event bus, or a generic plugin system by itself.
