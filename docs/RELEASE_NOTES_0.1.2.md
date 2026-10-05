# RepoManager v0.1.2

Prepared release notes; publication requires owner approval.

## Changes

- PySide6/QML desktop interface is the shipped UI, with Dark by default and Light available. The Python backend remains; Tkinter is source/reference only.
- Responsive scanning with visible progress, cooperative cancellation and confirmation when closing during a scan. Cancelled scans do not replace the last complete inventory.
- Repository details, Working on now, persistent curation and notes.
- Health advisory ignore/restore controls and global preferences, with transparent ignored states and scoring; blocking integrity checks remain protected.
- Changes/diff and per-file Stage/Unstage, with explicit Commit and confirmed Git operations.
- Quick Run and the detailed Run tab reuse the detected/configured launcher path. Agent target selection and managed stop remain local.
- Git requirement/install guidance, real Check again, improved Help and reviewable Feedback drafts.
- Portable Qt/Python packaging, corresponding third-party sources/notices, and isolated Git environments that reject inherited repository/config redirection.

## Installation and limits

Download the Windows x64 portable ZIP, extract the complete folder and run RepoManager.exe. No Python installation is required. Install Git for Windows with Git on PATH.

Official support is Windows 10 22H2 / Windows 11 x64. The executable is unsigned and SmartScreen may warn. The Terminal action requires Windows Terminal (`wt.exe`) with a valid default profile; PowerShell 7 is not required. Explorer and VS Code actions use the installed tools. Updates are manual through the official releases page; there is no automatic updater.

Configured launchers and Agent commands execute locally and are not sandboxed. No automatic feedback submission or telemetry is added.

The binary distribution includes runtime notices; matching Qt/PySide 6.11.2 sources accompany it as a separate source bundle.
