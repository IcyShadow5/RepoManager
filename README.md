# RepoManager

[![MIT License](https://img.shields.io/github/license/IcyShadow5/RepoManager?label=license)](LICENSE)
[![CPython 3.14.7](https://img.shields.io/badge/CPython-3.14.7-3776AB?logo=python&logoColor=white)](#run-from-source)
[![Windows 10/11](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?logo=windows&logoColor=white)](#platform-and-release-status)
[![tests](https://github.com/IcyShadow5/RepoManager/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/IcyShadow5/RepoManager/actions/workflows/ci.yml?query=branch%3Amain)
[![dependency audit](https://github.com/IcyShadow5/RepoManager/actions/workflows/dependencies.yml/badge.svg?branch=main)](https://github.com/IcyShadow5/RepoManager/actions/workflows/dependencies.yml?query=branch%3Amain)

RepoManager 0.1.0 is a Windows desktop application for finding, organizing,
and inspecting local Git repositories. It keeps a separate Project registry,
shows current repository metadata, and puts a small set of explicit actions in
one interface without treating discovery as permission to modify a repository.

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
- explicit fast-forward-only Pull and separate Commit & Push steps;
- advisory repository-move reconciliation that requires confirmation;
- metadata-only Project export and repository reports in JSON or Markdown;
- local Provider correspondence, plus a read-only GitHub metadata lookup when
  a selected Project has a supported GitHub remote.

RepoManager is not a Git replacement, a cloud-sync service, an unattended
automation engine, or a sandbox for commands it launches.

## Platform and release status

RepoManager 0.1.0 supports Windows 10 and Windows 11. Linux and macOS are
deferred; running generic Python source there does not make those platforms
supported.

The intended end-user distribution is an unsigned portable Windows ZIP built
with normal 64-bit CPython 3.14.7. Release candidates and published artifacts
are built and verified from a fresh clone of this public repository.

When that artifact is available, the intended experience is:

1. Download `RepoManager-0.1.0-windows-x64.zip` from the GitHub Release.
2. Extract the ZIP.
3. Start `RepoManager\RepoManager.exe`.

The packaged application bundles Python, so end users do not need a separate
Python installation. It is portable rather than installed; Git must still be
available on `PATH` for repository discovery and Git features. Because the
initial build is unsigned, Windows may display an unknown-publisher or
reputation warning.

## Run from source

Source execution requires Windows 10 or 11, Python 3.14 with Tkinter, and Git
on `PATH`. The official 0.1.0 build and CI baseline is normal 64-bit CPython
3.14.7. Python 3.11 is not a supported or CI-tested source runtime.

From the repository root:

```text
py -3.14 run.py
```

The application runtime uses only the Python standard library. PyInstaller and
its pinned dependencies are build tooling, not source-runtime dependencies.

Optional external tools enable additional launchers:

| Tool | Enables |
|---|---|
| Windows Terminal (`wt.exe`) | Terminal and Agent launch actions |
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

Notes and logs are ordinary local files. Do not place credentials or other
sensitive values in notes, configured commands, repository metadata, or other
fields that may be displayed or logged.

Discovery, local metadata collection, Health evaluation, and local Provider
correspondence do not modify managed repositories. Actions that can mutate a
repository—Git writes, launched commands, and optional `run.bat` generation—
require explicit user action. The generated starter never overwrites an
existing `run.bat`.

Selecting or refreshing a Project whose chosen remote corresponds to GitHub
starts a read-only HTTPS request to `api.github.com`. The 0.1.0 UI does not
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

Run the complete source suite with:

```text
py -3.14 -B -m unittest discover -s tests -v
```

The tests provide evidence for the exercised paths; they do not replace live
Windows GUI, packaged-executable, network, or external-launcher verification.
See [Testing](docs/TESTING.md).

## Current limitations

Version 0.1.0 does not include an installer, updater, signing pipeline, cloud
synchronization, Provider write/admin APIs, full Worktree lifecycle UI,
coordinated multi-repository Workspace changes, Agent sessions/orchestration,
or a general Policy/Profile/Workflow engine. Project export is not a registry
backup or repository archive.

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
