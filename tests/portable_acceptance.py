"""Run the existing guarded probe from an extracted development EXE.

Requires an already prepared, explicitly marked QA root and its synthetic
fixture data. It does not prepare or launch owner repositories or AI sessions.
Runtime check counts remain separate from source unittest totals.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--qa-root", type=Path, required=True)
    parser.add_argument("--fixture-appdata", type=Path, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("Portable Windows acceptance requires Windows")
    root, executable, fixture = args.qa_root.resolve(), args.exe.resolve(), args.fixture_appdata.resolve()
    source = Path(__file__).resolve().parents[1]
    if executable.is_relative_to(source) or not executable.is_file():
        raise ValueError("Use an extracted EXE outside the source repository")
    if (root / "CONTROLLED_QA_ONLY").read_text(encoding="ascii").strip() != "RepoManager RC QA":
        raise ValueError("An existing controlled QA marker is required")
    if not fixture.is_relative_to(root) or not args.name.replace("-", "").isalnum():
        raise ValueError("Fixture data/name must remain inside the controlled root")
    settings = json.loads((fixture / "RepoManager/settings.json").read_text(encoding="utf-8"))
    if any(not Path(value).resolve().is_relative_to(root) for value in settings["roots"]):
        raise ValueError("Owner scan roots are forbidden")
    agent_paths = [settings["agent_cmd"], *(target["executable"] for target in settings.get("agent_targets", []))]
    if any(not Path(value).resolve().is_relative_to(root) or Path(value).name not in {"runner.cmd", "runner2.cmd"}
           for value in agent_paths):
        raise ValueError("Only the prepared synthetic runner fixtures are permitted")
    data = root / (args.name + "-appdata")
    if data.exists():
        raise FileExistsError("Use a fresh acceptance name; existing evidence is protected")
    shutil.copytree(fixture, data)
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("Installed Git is required for the normal/recheck probe")
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment.pop("QT_QPA_FONTDIR", None)
    environment["LOCALAPPDATA"] = str(data)
    environment["QT_QPA_PLATFORM"] = "windows"
    environment["QT_QUICK_BACKEND"] = "software"
    system_root = os.environ["SystemRoot"]
    system_path = [str(Path(system_root) / "System32"), system_root]
    summary = {"executable": str(executable), "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(), "runs": []}
    for mode, scan_control, suffix in (("normal", True, "controls"), ("normal", False, "restart"), ("missing", False, "missing")):
        name = args.name + "-" + suffix
        config = root / (name + ".json")
        config.write_text(json.dumps({"output": name, "mode": mode, "scan_control": scan_control,
                                     "agent_choice": "custom:qa-second", "size": [1660, 940],
                                     "git_path": str(Path(git).parent)}, indent=2), encoding="utf-8")
        environment["PATH"] = os.pathsep.join((system_path if mode == "missing" else [str(Path(git).parent), *system_path]))
        result = subprocess.run([str(executable), "--rc-qa", str(config)], cwd=root, env=environment,
                                capture_output=True, text=True, timeout=180, creationflags=0x08000000)
        evidence = json.loads((root / name / "result.json").read_text(encoding="utf-8"))
        record = {"mode": mode, "scenario": suffix, "exit_code": result.returncode,
                  "checks": len(evidence["checks"]), "warnings": evidence["warnings"], "errors": evidence["errors"]}
        summary["runs"].append(record)
        (root / (args.name + "-summary.json")).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(record), flush=True)
        if result.returncode or not evidence["checks"] or evidence["warnings"] or evidence["errors"]:
            raise RuntimeError(f"Portable acceptance failed: {record}; {result.stdout} {result.stderr}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
