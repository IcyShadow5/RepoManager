"""Validate direct/delay PE imports against bundle files or approved Windows APIs."""
import json
from pathlib import Path
import sys

import pefile

WINDOWS_COMPONENTS = frozenset({
    'advapi32.dll', 'authz.dll', 'avrt.dll', 'bcrypt.dll', 'cfgmgr32.dll', 'comctl32.dll',
    'comdlg32.dll', 'crypt32.dll', 'cryptbase.dll', 'cryptsp.dll', 'd2d1.dll',
    'd3d9.dll', 'd3d11.dll', 'd3d12.dll', 'dcomp.dll', 'dnsapi.dll', 'dwmapi.dll',
    'dwrite.dll', 'dxgi.dll', 'dxguid.dll', 'gdi32.dll', 'hid.dll',
    'icu.dll', 'icuin.dll', 'icuuc.dll', 'imagehlp.dll', 'imm32.dll', 'iphlpapi.dll',
    'kernel32.dll', 'mpr.dll', 'msimg32.dll', 'msvcrt.dll', 'netapi32.dll',
    'ncrypt.dll', 'normaliz.dll', 'ntdll.dll', 'ole32.dll', 'oleaut32.dll', 'opengl32.dll',
    'powrprof.dll', 'propsys.dll', 'rpcrt4.dll', 'secur32.dll', 'setupapi.dll',
    'shell32.dll', 'shlwapi.dll', 'synchronization.dll', 'ucrtbase.dll',
    'uiautomationcore.dll', 'urlmon.dll', 'user32.dll', 'userenv.dll', 'usp10.dll', 'uxtheme.dll',
    'version.dll', 'wevtapi.dll', 'winhttp.dll', 'wininet.dll', 'winmm.dll',
    'winnsi.dll', 'winspool.drv', 'wintrust.dll', 'wlanapi.dll', 'ws2_32.dll',
    'wtsapi32.dll',
})
ICU_COMPONENTS = frozenset({'icu.dll', 'icuin.dll', 'icuuc.dll'})


def classify_import(name: str, bundled: set[str]) -> str:
    lower = name.lower()
    if lower in ICU_COMPONENTS:
        if lower in bundled:
            raise ValueError('ICU must be supplied by supported Windows, not copied from a build host')
        return 'windows-icu'
    if lower in WINDOWS_COMPONENTS:
        if lower in bundled:
            raise ValueError('Windows system DLL must not be redistributed from the build host: ' + name)
        return 'windows-system'
    if lower.startswith(('api-ms-win-', 'ext-ms-win-')) and lower.endswith('.dll'):
        if lower in bundled:
            raise ValueError('Windows API-set must be resolved by Windows: ' + name)
        return 'windows-api-set'
    if lower in bundled:
        return 'bundled'
    raise ValueError('Unresolved or unapproved PE import: ' + name)


def inspect(bundle: Path, output: Path) -> list[dict]:
    files = sorted(p for p in bundle.rglob('*') if p.is_file() and p.suffix.lower() in {'.dll', '.pyd', '.exe'})
    if not files:
        raise ValueError('No portable PE files to inspect')
    names = {p.name.lower() for p in files}
    evidence = []
    for path in files:
        pe = pefile.PE(str(path), fast_load=True)
        try:
            pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT'],
                                                 pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT']])
            for kind, attr in (('direct', 'DIRECTORY_ENTRY_IMPORT'), ('delay', 'DIRECTORY_ENTRY_DELAY_IMPORT')):
                for row in getattr(pe, attr, ()):
                    name = row.dll.decode('ascii')
                    evidence.append({'file': path.relative_to(bundle).as_posix(), 'import': name,
                                     'kind': kind, 'resolution': classify_import(name, names)})
        finally:
            pe.close()
    output.write_text(json.dumps({'supported_os': 'Windows 10 22H2 / Windows 11 x64',
        'icu': 'OS C API; not redistributed. Versions vary with Windows servicing.',
        'scope': 'Bundled PE direct/delay imports; not a proof of every dynamically loaded DLL.',
        'pe_files': len(files), 'imports': evidence}, indent=2) + '\n', encoding='utf-8')
    return evidence


if __name__ == '__main__':
    inspect(Path(sys.argv[1]), Path(sys.argv[2]))
