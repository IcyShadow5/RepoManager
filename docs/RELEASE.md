# RepoManager — Windows Release Build

RepoManager 0.1.0 is intended to ship as an unsigned portable `onedir`
distribution for Windows. Users extract the ZIP and run
`RepoManager\RepoManager.exe`; a separate Python installation is not required.
Git remains an external runtime requirement and must be available on `PATH`.

This document defines the build and verification contract. The final V0.1.0
public artifact is considered release-ready only after it has been built and
verified from a fresh clone of this public repository.

## Build prerequisites

- 64-bit Windows 10 or 11;
- normal 64-bit CPython 3.14.7 with Tkinter/Tcl/Tk (not free-threaded);
- PowerShell with `Compress-Archive`;
- network access to PyPI when the isolated build environment is first created.

PyInstaller and every Python package in
`packaging/requirements-build.txt` are release-build dependencies only. They
are installed into an isolated build environment and are not
application runtime dependencies. Pillow is not used by the release build.
The bootstrap installer is pinned separately in `requirements-bootstrap.txt`.
Both requirement files contain the complete Windows build dependency closure
with SHA-256 wheel hashes. Installation is wheel-only, uses PyPI explicitly,
and ignores user pip configuration. Update hashes from verified upstream
release metadata together with any version change.

## Canonical build

From the repository root:

```powershell
$releasePython = py -3.14 -c "import sys; print(sys.executable)"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -Python $releasePython -BuildVenv "$env:TEMP\RepoManager-build-3.14.7"
```

The script reads the product version from `repo_manager/version.py`, generates
the matching Windows version resource under ignored `build/`, builds the
windowed executable with PyInstaller, and produces:

- `dist\RepoManager\` — runnable portable directory;
- `dist\RepoManager-0.1.0-windows-x64.zip` — intended 0.1.0 distribution
  artifact;
- `dist\RepoManager-0.1.0-windows-x64-manifest.json` — source `HEAD`, dirty
  state, Python/Tcl/Tk versions, installed build dependencies, requirement
  hashes, license hashes, PyInstaller version, archive size, and archive SHA-256.

The script rejects an existing environment whose interpreter version, base
executable, architecture or free-threading mode differs from `-Python` before
installing anything. It also rejects incomplete existing directories. It
never deletes or recreates such environments: choose an unused `-BuildVenv`
path. `-CheckOnly` performs this preflight without creating an environment or
build outputs. Omit `-BuildVenv` to use `.build-venv` when it matches.
`-OutputRoot` selects a separate parent for fresh `build/` and `dist/`
directories, allowing existing candidates to be preserved.

Each package includes RepoManager's own `LICENSE` from the repository root,
alongside the third-party runtime notices under `RepoManager\LICENSES\`. The
runtime notices include `Python.txt` from the actual CPython runtime, Tcl and Tk
notices read from their active libraries (including Tcl/Tk 9 zipfs), and the
PyInstaller notice. Missing required licenses or notices fail the build before
the ZIP is produced. The third-party runtime notices do not choose or replace
RepoManager's own application license.

`build/`, `dist/`, and `.build-venv/` are generated and ignored. Pinned inputs
and the adjacent manifest make a build attributable and repeatable under the
recorded environment; they do not by themselves establish bit-for-bit
reproducibility. When the source tree is dirty, the artifact must not be
described as built from `HEAD` alone.

## Identity and resources

- Product and executable name: `RepoManager` / `RepoManager.exe`.
- Version source: `repo_manager/version.py`.
- Executable and window icon: `appicon.ico`.
- Source-run fallback icon: `app.ico`; it is also bundled as a fallback.
- Executable subsystem: Windows GUI, without a console window.
- Application data: `%LOCALAPPDATA%\RepoManager`, never the extracted
  application directory.

The repository defines no company or legal publisher identity, so the build
does not invent those metadata fields.

The adjacent manifest describes the exact locally built archive. A dirty
source state is recorded truthfully; it is not represented as built
from `HEAD` alone. The manifest is integrity metadata, not a signature.

## Signing and distribution boundary

The intended 0.1.0 artifact is unsigned. Windows may therefore show an
unknown-publisher or reputation warning. No installer, automatic updater, or
automatic GitHub Release publication is part of this build.

The portable ZIP is preferred for 0.1.0 because it keeps Tcl/Tk and other runtime
files explicit, starts without one-file extraction, requires no administrative
installation, and leaves user data outside the distribution directory. A
Windows installer and CI artifact job remain deferred for 0.1.0; there is no
current evidence that they add enough value to justify their additional
lifecycle surface.

The package includes license files collected from the actual Python, Tcl, Tk,
and PyInstaller build environment. Python and Tcl/Tk redistribution require
retaining their applicable notices. PyInstaller's bootloader exception permits
the generated bundle to use RepoManager's chosen license; its notice is
included for transparent attribution. These third-party files do not select or
replace RepoManager's own project license.

## Verification expectations

A successful PyInstaller exit is not sufficient. Before publication, build
from the clean public source revision and verify the actual extracted
`RepoManager.exe` for startup, project selection and details,
Git branch/status observation, Help, Settings, both themes, detail scrolling,
normal shutdown, reopen, isolated fresh-start data creation, and an isolated
copy of representative existing data. Then run the complete canonical suite
from `docs/TESTING.md` and `git diff --check`.
