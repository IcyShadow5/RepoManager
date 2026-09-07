"""Collect notices from the actual build runtime before archiving a release."""
import hashlib
import importlib.metadata
import json
import platform
import sys
import tkinter
from pathlib import Path


def collect(bundle, metadata_path):
    # Read every required notice before writing output; missing notices fail the build.
    notices = {"Python.txt": (Path(sys.base_prefix) / "LICENSE.txt").read_bytes()}
    root = tkinter.Tk()
    root.withdraw()
    try:
        versions = {"tcl_version": root.tk.call("info", "patchlevel"),
                    "tk_version": root.tk.call("package", "provide", "Tk")}
        for name, variable in (("Tcl.txt", "tcl_library"), ("Tk.txt", "tk_library")):
            # Tcl/Tk 9 embeds its library in zipfs; these are not filesystem paths.
            path = root.tk.call("file", "join", root.tk.call("set", variable),
                                "license.terms")
            handle = root.tk.call("open", path, "r")
            try:
                notices[name] = root.tk.call("read", handle).encode("utf-8")
            finally:
                root.tk.call("close", handle)
    finally:
        root.destroy()
    distribution = importlib.metadata.distribution("pyinstaller")
    license_file = next(path for path in distribution.files
                        if str(path).endswith("licenses/COPYING.txt"))
    notices["PyInstaller.txt"] = Path(distribution.locate_file(license_file)).read_bytes()
    if any(not data.strip() for data in notices.values()):
        raise ValueError("An empty runtime license would leave the package incomplete")
    destination = Path(bundle) / "LICENSES"
    destination.mkdir(parents=True, exist_ok=True)
    license_hashes = {}
    for name, data in notices.items():
        (destination / name).write_bytes(data)
        license_hashes[f"LICENSES/{name}"] = hashlib.sha256(data).hexdigest()
    dependencies = sorted(
        ({"name": item.metadata["Name"], "version": item.version}
         for item in importlib.metadata.distributions()), key=lambda item: item["name"].lower())
    metadata = {"python_version": platform.python_version(), **versions,
                "build_dependencies": dependencies, "licenses": license_hashes}
    Path(metadata_path).write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    collect(Path(sys.argv[1]), Path(sys.argv[2]))
