# RepoManager — Windows Release Build

RepoManager uses an unsigned portable `onedir` distribution for Windows.
Users extract the ZIP and run
`RepoManager\RepoManager.exe`; a separate Python installation is not required.
Git remains an external runtime requirement and must be available on `PATH`.

Public v0.1.1 already exists: it was published on 2026-09-12 from the
separate Public Release repository as `RepoManager-0.1.1-windows-x64.zip`
(SHA-256 `5b1cc5d3e5008b8aa7fc0a317146c2845fbb8ead967ddafc485fac46219e03c2`).
The curated source version is **0.1.2**. Building the current tree does not
publish or overwrite public v0.1.1. Uploading/tagging the prepared release
requires separate owner approval.

This document defines the build and verification contract. It does not claim
that an artifact built from the current source tree is the published
public release.

## Current desktop presentation

`run.py` and the builder default to PySide6/QML. `-Presentation Classic` retains
the Tk behavioral reference through `run_classic.py`; it is not the shipped Qt presentation.
The Qt bundle excludes Tkinter/Tcl/Tk and collects the actual QML import closure
with Qt's scanner. Basic is the only selectable Controls style. Missing imports,
scanner warnings, unreviewed add-ons and binaries from unrelated developer tools
fail the build. [Release evidence](RELEASE_EVIDENCE.md) separates current-product
tests, extracted-package probes and owner acceptance. No new public release is claimed.

The source release gate is `python -B -m tests.run_layers --layer current`.
Report its core, service, Qt bridge, actual QML, packaging and Windows results
separately. `--layer legacy` is a behavioural reference, not current UI proof.
Collector/preflight tests do not replace launching the extracted portable EXE.
See [Testing](TESTING.md) and [Release evidence](RELEASE_EVIDENCE.md).

## Build prerequisites

- Windows 10 22H2 or Windows 11 x64;
- normal 64-bit CPython 3.14.7 (not free-threaded; Tk is needed only for Classic);
- PowerShell 7 (`pwsh`), which is the canonical shell for this build;
- network access to PyPI when the isolated build environment is first created.

PyInstaller and every Python package in
`packaging/requirements-build.txt` are release-build dependencies only. They
are installed into an isolated build environment and are not
application runtime dependencies. Pillow is not used by the release build.
The bootstrap installer is pinned separately in `requirements-bootstrap.txt`.
The additional `requirements-build-qt.txt` pins PySide6, Essentials, Addons and
Shiboken for Qt builds. These requirement files contain the Windows dependency closure
with SHA-256 wheel hashes. Installation is wheel-only, uses PyPI explicitly,
and ignores user pip configuration. Update hashes from verified upstream
release metadata together with any version change.

## Canonical build

From the repository root:

```powershell
$releasePython = py -3.14 -c "import sys; print(sys.executable)"
.venv\Scripts\python.exe packaging\fetch_qt_sources.py "$env:TEMP\RepoManager-qt-sources"
pwsh -NoProfile -File packaging\build_windows.ps1 -Python $releasePython -BuildVenv "$env:TEMP\RepoManager-build-qt-3.14.7" -OutputRoot "$env:TEMP\RepoManager-candidate" -ThirdPartySources "$env:TEMP\RepoManager-qt-sources"
```

The script reads the product version from `repo_manager/version.py`, generates
the matching Windows version resource under ignored `build/`, builds the
windowed executable with PyInstaller, and produces:

- `dist\RepoManager\` — runnable portable directory;
- `dist\RepoManager-0.1.2-windows-x64.zip` — prepared Qt release;
- `dist\RepoManager-0.1.2-windows-x64-manifest.json` — source `HEAD`, dirty
  state, Python/Qt/PySide versions, installed build dependencies, requirement
  hashes, license hashes, PyInstaller version, archive size, and archive SHA-256.
- `dist\RepoManager-0.1.2-third-party-sources.zip` and adjacent JSON —
  matching hash-verified Qt/PySide archives and required notice inputs; distribute
  this companion alongside the binary, not only a link to an internal folder.

The script rejects an existing environment whose interpreter version, base
executable, architecture or free-threading mode differs from `-Python` before
installing anything. It also rejects incomplete existing directories. It
never deletes or recreates such environments: choose an unused `-BuildVenv`
path. `-CheckOnly` performs this preflight without creating an environment or
build outputs. Omit `-BuildVenv` to use `.build-venv` when it matches.
`-OutputRoot` selects a separate parent for fresh `build/` and `dist/`
directories, allowing existing candidates to be preserved.

Each package includes RepoManager's own `LICENSE` from the repository root,
copied to the bundle root (`RepoManager\LICENSE`), alongside the third-party
runtime notices under `RepoManager\LICENSES\`.
`LICENSES/Python.txt` comes from the actual CPython runtime,
Qt/PySide notices come from hash-verified matching upstream source archives;
third-party attribution files and referenced license files are retained.
`REDISTRIBUTION.json` inventories the actual libraries/plugins/notices,
`BINARY_ORIGINS.json` records approved binary provenance without absolute build
paths, and `BUILD_INFO.json` records the release identity and clean source commit.
`PE_IMPORT_CLOSURE.json` distinguishes bundled dependencies from approved Windows
components, including OS ICU. RC builds reject a dirty source tree.
Classic instead collects Tcl/Tk notices from their active libraries.
The PyInstaller notice is included. Missing required licenses or notices
fail the build before the ZIP is produced. Matching Qt/PySide source archives
must accompany any later public binary distribution; see
`packaging/THIRD_PARTY_NOTICES.md`. ZIP entries preserve the
`RepoManager/` root and use `/` as the separator; the build fails if a
generated entry contains `\`. This runtime notice inclusion does not choose
or replace RepoManager's own application license.

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
- Both icons are exported from `assets/repomanager.svg` with
  `.venv\Scripts\python.exe make_icon.py` and contain RGBA frames from 16 to
  256 pixels. QML uses the same SVG identity.
- Executable subsystem: Windows GUI, without a console window.
- Application data: `%LOCALAPPDATA%\RepoManager`, never the extracted
  application directory.

The repository defines no company or legal publisher identity, so the build
does not invent those metadata fields.

The adjacent manifest describes the exact locally built archive. A dirty
source state is recorded truthfully; it is not represented as built
from `HEAD` alone. The manifest is integrity metadata, not a signature.

## Signing and distribution boundary

The portable release build is unsigned. Windows may therefore show an
unknown-publisher or reputation warning. No installer, automatic updater, or
automatic GitHub Release publication is part of this build.

The portable ZIP keeps Qt/Python and other runtime
files explicit, starts without one-file extraction, requires no administrative
installation, and leaves user data outside the distribution directory. A
Windows installer and CI artifact job remain deferred until the release source
is committed and there is evidence they add enough value to justify their
additional lifecycle surface.

Qt builds omit translation catalogs: the application currently loads no
`QTranslator` and offers no language selector. Native Windows dialogs retain
OS localization. Adding Qt translations requires their source/notice inventory
and removal of the explicit translation-collection guard.

The Qt package includes license files for the actual Python, Qt/PySide and
PyInstaller runtime. Classic builds additionally include Tcl/Tk notices.
Redistribution requires retaining the applicable third-party notices and
supplying matching Qt/PySide sources as described above. PyInstaller's bootloader exception permits
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
