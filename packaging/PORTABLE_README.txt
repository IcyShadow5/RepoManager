RepoManager Development - portable Qt Windows candidate

This is an unreleased Development / Release Candidate build, not public v0.1.1.
Extract the complete RepoManager folder, then run RepoManager.exe.
Keep _internal beside the executable. No Python installation is required.

System requirements: Windows 10 (1809 or newer) / Windows 11, x64.
Git is external: install Git for Windows with Git available on PATH.
The application offers Install Git and Check again. Restart RepoManager if a
running Windows process still has the previous PATH after installation.
Terminal action requires Windows Terminal (wt.exe) with a valid default profile.
PowerShell 7 is not required; Windows PowerShell 5.1 can be used by that profile.

Application data is stored under %LOCALAPPDATA%\RepoManager, not in this folder.
Feedback can be saved locally or opened as a draft for the user to review.
Nothing is submitted automatically. This package is unsigned.
Help > Versions and updates > Official releases opens the public downloads page.
No update is downloaded or installed automatically.

LICENSE is the application license. THIRD_PARTY_NOTICES.md, REDISTRIBUTION.json
and LICENSES document the bundled runtime components. THIRD_PARTY_SOURCES.json
identifies the matching upstream source archives supplied beside the candidate.

This candidate includes opt-in --rc-qa instrumentation for controlled acceptance.
It requires a specially marked directory and isolated application data. Normal
launch does not run the probe, load test fixtures or alter the user's PATH.
