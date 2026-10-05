# Portable Qt redistribution inventory

The exact bundled DLLs, plugins, QML directories, versions and notice hashes are
listed in `REDISTRIBUTION.json`. The build fails on unreviewed Qt add-ons or missing
required notices. Build tools are listed separately from runtime components.

| Component | Applicable notice / license | Package handling |
|---|---|---|
| RepoManager | MIT, root `LICENSE` | Application source is unchanged in license |
| CPython 3.14.7 and included standard-library components | PSF and component terms in `LICENSES/Python.txt` | Unmodified bundled Python runtime; includes Windows Microsoft conditions, bzip2 and libffi notices |
| Expat 2.8.2 in `pyexpat.pyd` | MIT, `LICENSES/Expat-2.8.2.txt` | Runtime `pyexpat.EXPAT_VERSION` verified; upstream copyright/permission retained |
| liblzma in `_lzma.pyd` | XZ 5.2.5 `COPYING`, `LICENSES/XZ-5.2.5-COPYING.txt` | CPython 3.14.7 Windows build recipe specifies XZ 5.2.5; liblzma code is public domain under that release's terms, not the newer XZ 0BSD claim |
| Windows ICU C API | Windows OS component; prudent upstream 72.1 notice in `LICENSES/ICU-Windows-72.1.txt` | No ICU DLL is bundled; supported Windows supplies ICU, and actual ICU versions vary with OS servicing |
| PySide6 / Shiboken 6.11.2 | LGPLv3 alternative, official module source headers and GNU texts | Shared libraries remain separate and replaceable |
| Qt 6.11.2 Core, GUI, Widgets, Network, OpenGL, QML, Quick, Quick Controls, Quick Dialogs, SVG and selected plugins | LGPLv3 alternative plus module-specific third-party notices | Unmodified upstream wheel libraries; no static Qt linkage; no GPL-only PDF/Virtual Keyboard/Quick3D add-on |
| Qt third-party code | Original license/copyright files and `qt_attribution.json` under `LICENSES/Qt` | Module source notice closure is included conservatively; source-only notices do not mean all source modules are shipped |
| OpenSSL | Apache 2.0 and OpenSSL copyright, versioned notice | Actual Python/Qt TLS binary versions are inventoried separately |
| Microsoft C/C++ runtime | Microsoft component terms, vendor document/plain text; CPython Windows conditions | Only Microsoft-targeted binaries; application MIT terms do not replace component terms |
| Tabler SVG subset | MIT, `LICENSES/Tabler.txt` and bundled SOURCE record | Pinned, unmodified shapes recolored at runtime |
| PyInstaller bootloader | GPL with bootloader distribution exception; Apache runtime hooks | `LICENSES/PyInstaller.txt`; this exception does not change the application license |

Upstream matching source archives are identified by immutable commit, URL and
SHA-256 in `THIRD_PARTY_SOURCES.json`, and are supplied alongside the portable binary.
No Qt/PySide source or library has been patched. Users may replace/rebuild the
LGPL libraries; the package applies no signature or integrity restriction to
replacement libraries. Application MIT terms do not prohibit reverse engineering
for debugging modified LGPL components.

Before public distribution, supply the matching source archives with the binary
download and retain these notices. This manifest records technical evidence and
upstream license alternatives; it is not a blanket legal opinion or a commercial
Qt license claim.

`PE_IMPORT_CLOSURE.json` distinguishes bundled DLLs, approved Windows system/API-set
imports and Windows ICU. It rejects unknown imports and copied OS ICU binaries.
This checks direct and delay imports, not every dynamic `LoadLibrary` call.
Supported baseline is Windows 10 22H2 / Windows 11 x64. The ICU C APIs are OS
components: [Microsoft ICU documentation](https://learn.microsoft.com/en-us/windows/win32/intl/international-components-for-unicode--icu-).
The 72.1 notice describes the inspected host's ICU upstream release; it does not
require every user's Windows ICU version to equal that host version.

References: [Qt 6.11 licensing](https://doc.qt.io/qt-6.11/licensing.html),
[Qt 6.11.2 third-party code](https://doc.qt.io/qt-6.11/licenses-used-in-qt.html),
[Qt LGPL obligations](https://www.qt.io/development/open-source-lgpl-obligations),
[Microsoft redistribution](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files?view=msvc-170).
