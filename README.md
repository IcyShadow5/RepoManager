# RepoManager

RepoManager is a Windows desktop application for finding, organizing,
and inspecting local Git repositories. It keeps a separate Project registry,
shows current repository metadata, and puts a small set of explicit actions in
one interface without treating discovery as permission to modify a repository.
A public v0.1.1 release already exists (see "Platform and release status"
below); this Development tree contains post-v0.1.1 hardening and is not
identical to that published source state. The in-tree Qt version is **0.1.2-rc.2**,
an unpublished release candidate for owner and independent review. Building this tree does not publish or
replace public v0.1.1.

## v0.1.2 candidate scope

The standalone Python backend now uses **PySide6 + QML** by default. This is
not RepoManager 2.0 or IC Platform. The candidate includes cooperative scan
cancellation and close confirmation, repository search/selection/curation/notes,
Git Changes/diff/stage/unstage and confirmed Git actions, Health advisory
ignore/restore with transparent scoring, Quick Run and the full Run tab,
local Agent target selection/managed stop, Git onboarding, and reviewable
Feedback. Dark is the default; Light remains available.

Classic Tkinter is a source-only parity reference and is excluded from the Qt
portable package. Guarded portable QA options are prerelease-only diagnostics,
not user CLI features. Custom themes, Agent history, cloud services, monetization,
Linux/macOS support and automatic self-update are deferred.

## Why use it?

RepoManager is aimed at developers who keep many local repositories and want a
single view of what exists, what needs attention, and which tool can open a
working tree. It combines local Git evidence with user-owned status, focus,
pinning, and notes.

Key capabilities include:

- bounded discovery of Git repositories, including linked Worktrees;
- branch, HEAD, working-tree, upstream, remote, latest-commit, and Worktree
  observations;
- stable Project records with status, focus, pinning, notes, filtering, and
  sorting;
- lightweight read-only Repository Health findings;
- detection and explicit launch of supported editor, terminal, batch,
  PowerShell, npm, Godot, Python, Roblox, WSL, and Git Bash commands;
- independent local Commit and confirmed Push, fast-forward-only Pull, and
  explicit Fetch;
- bounded Changes/Diff with per-file Stage/Unstage, recent History, and
  read-only Remote inspection (Development additions, not public v0.1.1);
- advisory repository-move reconciliation that requires confirmation;
- metadata-only Project export and repository reports in JSON or Markdown;
- local Provider correspondence, plus a read-only GitHub metadata lookup when
  a selected Project has a supported GitHub remote.

RepoManager is not a Git replacement, a cloud-sync service, an unattended
automation engine, or a sandbox for commands it launches.

## Platform and release status

The Qt candidate targets Windows 10 22H2 and Windows 11 x64. Linux and macOS are
deferred; running generic Python source there does not make those platforms
supported.

The intended end-user distribution is an unsigned portable Windows ZIP built
with normal 64-bit CPython 3.14.7. Public v0.1.1 was published on 2026-09-12
as `RepoManager-0.1.1-windows-x64.zip` from the separate Public Release
repository; this Development tree has moved beyond that published source
state, so a build from the current tree is NOT that published artifact and
must not be presented as such without a deliberate future release decision.

For the existing public release:

1. Download `RepoManager-0.1.1-windows-x64.zip` from the GitHub Release.
2. Extract the ZIP.
3. Start `RepoManager\RepoManager.exe`.

The packaged application bundles Python, so end users do not need a separate
Python installation. It is portable rather than installed; Git must still be
available on `PATH` for repository discovery and Git features. Because the
initial build is unsigned, Windows may display an unknown-publisher or
reputation warning.

Current Development builds detect Git at startup. If they cannot start Git, they show a Git requirement notice instead of
running a repository scan. Use **Install Git** to open the official
[Git for Windows installation page](https://git-scm.com/install/windows),
complete the normal installation with Git available on `PATH`, then select
**Check again**. This checks the current process environment and resumes
scanning when Git is available. If Git was installed while RepoManager was
open and Check again still cannot find it, restart RepoManager so it inherits
the updated `PATH`. A Git command that fails for a repository-specific reason
is reported separately from missing Git.

### Finding your repositories

In **Settings > Scan folders**, add the folder containing your repository
collection and choose **Save & scan**. New profiles start with Desktop, Documents,
and Downloads below the current user folder; RepoManager does not search the whole
machine automatically. Depth is relative to each root:
depth 2 includes the root and up to two nested folder levels. Choose a nearer
root or increase depth if a repository is deeper. Windows Settings offers
**Add C:\\** as an optional whole-drive scope. This can take longer and encounter
protected folders. Existing scan roots are preserved; Cancel discards unsaved
folder additions. Invalid and duplicate additions are explained immediately.

## Run from source

Source execution requires Windows 10 22H2 or Windows 11 x64, Python 3.14 and Git
on `PATH`. The official 0.1.1 build and CI baseline is normal 64-bit CPython
3.14.7. Python 3.11 is not a supported or CI-tested source runtime.

The current Development presentation uses PySide6 + QML. From the repository root:

```text
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -r packaging\requirements-qt.txt
.venv\Scripts\python.exe run.py
```

The existing Python domain, scanner, Git and storage modules remain shared.
The classic Tkinter reference (`run_classic.py`) is retained as a regression reference
pending a separately approved removal package. It still
uses only the Python standard library and requires Tkinter. Qt packaging and
promotion to a public release require separate owner acceptance.

Optional external tools enable additional launchers:

| Tool | Enables |
|---|---|
| Windows Terminal (`wt.exe`) | Terminal action using its default profile; PowerShell 7 is not required |
| VS Code | VS Code launch action |
| Node.js / npm | npm launchers |
| Godot | Godot project launchers |
| WSL or Git Bash | shell launchers |

Configured launchers and Agent commands execute as local processes with the
selected working tree as their starting directory. They may execute arbitrary
code and are not confined to that directory.

## Data, network, and mutation boundaries

RepoManager stores application-owned data under
`%LOCALAPPDATA%\RepoManager`, outside managed repositories:

- `repos.json` — schema-v2 Project and Workspace registry;
- `settings.json` — scan, display, and launcher settings;
- `notes/` — per-Project Markdown notes;
- rotating registry backups and corruption-quarantine files;
- `repo_manager.log` and its rotated log files.
- `feedback/` — reports explicitly saved locally from the Qt Feedback dialog.

Notes and logs are ordinary local files. Do not place credentials or other
sensitive values in notes, configured commands, repository metadata, or other
fields that may be displayed or logged.

Discovery, local metadata collection, Health evaluation, and local Provider
correspondence do not modify managed repositories. Actions that can mutate a
repository—Git writes, launched commands, and optional `run.bat` generation—
require explicit user action. The generated starter never overwrites an
existing `run.bat`.

## Feedback and bug reports (Development)

The **Feedback / Bug** button offers four categories: positive feedback,
improvement, bug, and UI issue. Add a short title and description, then save
locally, copy the report, or open a prefilled issue draft in the public
RepoManager GitHub tracker. Review and submit the draft yourself; opening it
does not send a report. There is no automatic telemetry.

Only the text you enter, Development edition and application version are
included by default. Optional runtime information adds Python, Qt and OS
versions. Repository paths, inventory, notes, logs and credentials are not
attached automatically. Review your text for private information before sharing.

Selecting or refreshing a Project whose chosen remote corresponds to GitHub
starts a read-only HTTPS request to `api.github.com`. The 0.1.1 UI does not
accept or persist a GitHub token, so private repository metadata normally
cannot be retrieved through this feature. Git Pull/Push and external launchers
may also use the network according to Git and the launched tool's own
configuration.

Exports and reports copy metadata, not repository contents. They omit fields
whose keys look credential-related, such as `token`, `password`, or
`private_key`; they do not scan arbitrary text values or notes for embedded
secrets. Review an export before sharing it.

Registry writes use a flushed temporary file and replacement, with validation,
rotating backups, and corruption quarantine. This reduces partial-write risk
for an individual save but is not a universal crash, storage-device, or
power-loss durability guarantee and does not coordinate multiple writers.

Repository Health and post-command observations report evidence only. They do
not certify correctness or security, and successful process completion does
not prove that an external tool made correct changes.

## Build and test

The Windows build is defined by `packaging/build_windows.ps1`. It validates the
requested interpreter, creates or reuses a matching isolated build environment,
installs hash-pinned build tools, creates a PyInstaller `onedir` bundle, adds
runtime license notices, and writes an adjacent integrity manifest. See
[Windows release build](docs/RELEASE.md) for the exact contract and the
required packaged-runtime checks.

Run the current Qt product gate with:

```text
.venv\Scripts\python.exe -B -m tests.run_layers --layer current
```

The tests provide evidence for the exercised paths; they do not replace live
Windows GUI, packaged-executable, network, or external-launcher verification.
See [Testing](docs/TESTING.md) for separate core, service, bridge, actual QML,
packaging and Windows layers. Legacy Tkinter parity is a separate reference,
not current UI proof. [Release evidence](docs/RELEASE_EVIDENCE.md) records the
reconciled baseline and current candidate scope.

## Everyday Git in this Development tree

Right-click a Project, or press Shift+F10 / the Menu key in either list.
Changes opens a resizable staged/unstaged file list and bounded diff preview.
Stage/Unstage apply to selected files; Unstage leaves working files intact.
Commit defaults to staged changes, requires a message, works without a remote,
and never pushes. Stage all current changes and commit is an explicit alternative:
`git add -A` stages changes present at execution, not a locked preview snapshot.
Git hooks and filters are not sandboxed.

Push, Pull, and Fetch show a remote/destination preview before confirmation.
Push is non-forced and can explicitly set an upstream. Pull requires a clean
checkout and is fast-forward-only. Fetch does not prune or change checkout files.
Multiple push destinations, mirror pushes, and non-standard fetch refspecs are
blocked rather than guessed. No Git mutation is retried automatically.

Repository submenus expose recent History, sanitized read-only Remotes, and a
selected metadata refresh (not a full inventory scan). URL credential components
are hidden, not arbitrary text secrets. Diff output is bounded to 2 MiB; Changes
rejects status above 5,000 files. Conflict resolution, submodule writes, hunk
staging, discard/reset/stash, and branch switching require an external Git tool.

Scan issues and possible moves have separate review tabs with copyable paths.
Ambiguous candidates require choosing an exact proven pair and confirming it;
curated/note-owned targets cannot be silently absorbed. Missing pair provenance
requires a rescan or Keep both. Later leaves suggestions unresolved; Keep both
persists exact-pair suppression. No repository files are moved by reconciliation.

## Current candidate limitations

The v0.1.2 candidate does not include an installer, automatic updater, signing pipeline, cloud
synchronization, Provider write/admin APIs, full Worktree lifecycle UI,
coordinated multi-repository Workspace changes, Agent sessions/orchestration,
or a general Policy/Profile/Workflow engine. Project export is not a registry
backup or repository archive. Workspace metadata may be retained internally,
but Workspace controls and the repository-association picker are not exposed.

Help > Versions and updates > Official releases opens the public release page
for manual comparison/download. It does not query or replace the application
automatically. WinGet submission #433770 for public v0.1.1 remains open as of
2026-10-05; WinGet installation is not advertised as available.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — implementation structure and trust
  boundaries.
- [Contracts](docs/CONTRACTS.md) — stable domain and mutation semantics.
- [Windows release build](docs/RELEASE.md) — portable build and verification
  requirements.
- [Testing](docs/TESTING.md) — test strategy and environment boundaries.
- [Security policy](SECURITY.md) — supported release line and vulnerability
  reporting status.
- [Contributing](CONTRIBUTING.md) — development and pull-request expectations.

## License

RepoManager is licensed under the [MIT License](LICENSE). Runtime notices
bundled with a portable build apply separately to their respective third-party
components.
