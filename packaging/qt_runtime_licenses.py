"""Collect verified Qt source notices and inventory the actual portable runtime."""
import hashlib
import importlib.metadata as metadata
import json
import posixpath
from pathlib import Path, PurePosixPath
import platform
import sys
import tarfile

import PySide6
from PySide6.QtCore import qVersion

REQUIRED_SOURCES = {"qtbase", "qtdeclarative", "qtshadertools", "qtsvg", "qtimageformats", "pyside-setup"}

def source_notices(folder):
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    lock = json.loads(Path(__file__).with_name("qt-source-lock.json").read_text(encoding="utf-8"))
    if manifest != lock["qt_sources"]:
        raise ValueError("Source archives differ from the reviewed upstream lock")
    if {item["name"] for item in manifest} != REQUIRED_SOURCES:
        raise ValueError("Incomplete Qt source archive closure")
    notices = {}
    for item in manifest:
        archive = folder / item["archive"]
        if archive.parent != folder or hashlib.sha256(archive.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Source archive hash or path mismatch")
        with tarfile.open(archive, "r:gz") as source:
            for member in source.getmembers():
                if not member.isfile():
                    continue
                relative = PurePosixPath(member.name).relative_to(PurePosixPath(member.name).parts[0])
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Unsafe source notice path")
                name = relative.name.lower()
                if (relative.parts[0] == "LICENSES" or
                    (relative.parts[0] in {"src", "sources"} and
                     (name.startswith(("license", "copying", "copyright", "notice")) or name == "qt_attribution.json"))):
                    data = source.extractfile(member).read()
                    if data.strip():
                        notices[f"Qt/{item['name']}/{relative.as_posix()}"] = data
                    if name == "qt_attribution.json":
                        entries = json.loads(data, strict=False)
                        for entry in entries if isinstance(entries, list) else [entries]:
                            files = entry.get("LicenseFile", [])
                            for license_file in files if isinstance(files, list) else [files]:
                                target = posixpath.normpath(str(relative.parent / license_file))
                                if target.startswith("../") or target.startswith("/"):
                                    raise ValueError("Unsafe referenced license path")
                                data = source.extractfile(PurePosixPath(member.name).parts[0] + "/" + target).read()
                                notices[f"Qt/{item['name']}/{target}"] = data
    for mandatory in ("LGPL-3.0-only.txt", "GPL-3.0-only.txt"):
        if not any(name.endswith('/'+mandatory) for name in notices):
            raise ValueError("Required GNU license missing")
    for item in lock["additional_notices"]:
        file = folder / "additional-notices" / item["file"]
        data = file.read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("Additional runtime notice hash mismatch")
        notices[item["file"]] = data
    return notices, manifest

def collect(bundle, output, sources, repo):
    if any(bundle.rglob('*.qm')):
        raise ValueError("Unreviewed Qt translation catalogs collected")
    notices, source_manifest = source_notices(sources)
    notices["Python.txt"] = (Path(sys.base_prefix) / "LICENSE.txt").read_bytes()
    distribution = metadata.distribution("pyinstaller")
    copying = next(p for p in distribution.files if str(p).endswith("licenses/COPYING.txt"))
    notices["PyInstaller.txt"] = Path(distribution.locate_file(copying)).read_bytes()
    notices["Tabler.txt"] = (repo / "repo_manager/qml/assets/tabler/LICENSE").read_bytes()
    if any(not content.strip() for content in notices.values()):
        raise ValueError("Empty required notice")
    license_hashes = {}
    for name, data in notices.items():
        path = bundle / "LICENSES" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        license_hashes[path.relative_to(bundle).as_posix()] = hashlib.sha256(data).hexdigest()
    (bundle / "THIRD_PARTY_SOURCES.json").write_text(json.dumps(source_manifest, indent=2)+'\n', encoding="utf-8")
    modules = sorted(p.relative_to(bundle).as_posix() for p in bundle.rglob('Qt6*.dll'))
    prohibited = ("Qt6VirtualKeyboard", "Qt6Pdf", "Qt6Quick3D", "Qt6Graphs", "Qt6Lottie")
    if any(Path(path).name.startswith(prohibited) for path in modules):
        raise ValueError("An unreviewed Qt add-on was pulled into the package")
    plugins = sorted(p.relative_to(bundle).as_posix() for p in bundle.rglob('*.dll') if 'plugins' in p.parts or 'qml' in p.parts)
    qml = sorted(p.relative_to(bundle).parent.as_posix() for p in bundle.rglob('qmldir'))
    dependencies = sorted(({"name": d.metadata["Name"], "version": d.version} for d in metadata.distributions()),key=lambda d: d['name'].lower())
    runtime = {"python_version":platform.python_version(),"qt_version":qVersion(),"pyside_version":PySide6.__version__,"build_dependencies":dependencies,"licenses":license_hashes,"qt_dlls":modules,"plugins":plugins,"qml_modules":qml}
    (bundle / "REDISTRIBUTION.json").write_text(json.dumps(runtime,indent=2)+'\n',encoding='utf-8')
    output.write_text(json.dumps(runtime,indent=2)+'\n',encoding='utf-8')

if __name__ == '__main__':
    collect(*(Path(arg) for arg in sys.argv[1:]))
