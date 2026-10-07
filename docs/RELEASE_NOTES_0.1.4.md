# RepoManager v0.1.4

- Fixes Changes/diff previews that were unavailable when native Git emitted an
  LF/CRLF round-trip warning after an external edit. Repositories without a remote
  are supported as before.
- Suppresses that write-related warning only during read-only Git observation.
  Line-ending conversion and configured safeguards for Stage/Commit are unchanged.
- Retains the v0.1.3 helper restrictions: automatic/read-only Git cannot start
  child processes. Helper-dependent Git LFS/submodule observations and other
  diagnostic failures still remain unavailable rather than falsely clean.

Regression tests cover line-ending configurations, staged/unstaged text,
untracked and binary files, deletion/rename, detached/unborn repositories,
Refresh after external editing, and repository switching.

Official support: Windows 10 22H2 / Windows 11 x64. Extract the complete portable
ZIP and run `RepoManager/RepoManager.exe`. Git must be available on PATH.
The executable is unsigned and SmartScreen may warn. Windows Terminal is required
only for the Terminal action; PowerShell 7 is not required for that action.
There is no automatic updater.
