"""Per-repo launcher/command discovery and typed execution.

Detection pipeline: filesystem/manifest signals -> candidate commands
(priority + health) -> deterministic primary selection. Detection inspects
local files and may probe WSL availability; it does not run project scripts.
Launcher commands execute on explicit user action.
"""
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import processes


@dataclass(frozen=True)
class LauncherCandidate:
    """Structured launcher evidence with dict-compatible legacy access."""

    label: str
    type: str
    priority: int = 50
    healthy: bool = True
    reason: str = ""
    source: str = "detected"
    cwd: str = ""
    command: tuple[str, ...] = ()
    _data: dict = None

    def __getitem__(self, key):
        return self.as_dict()[key]

    def __contains__(self, key):
        return key in self.as_dict()

    def get(self, key, default=None):
        return self.as_dict().get(key, default)

    def as_dict(self):
        data = dict(self._data or {})
        data.update({"label": self.label, "type": self.type,
                     "priority": self.priority, "healthy": self.healthy,
                     "source": self.source})
        if self.reason:
            data["reason"] = self.reason
        if self.cwd:
            data.setdefault("cwd", self.cwd)
        if self.command:
            data.setdefault("command", self.command)
        return data

    def __eq__(self, other):
        if isinstance(other, dict):
            return self.as_dict() == other
        return super().__eq__(other)


def _candidate(**kwargs):
    """Build a candidate while retaining the historical mapping boundary."""
    known = {"label", "type", "priority", "healthy", "reason", "source",
             "cwd", "command"}
    data = {k: v for k, v in kwargs.items() if k not in known}
    return LauncherCandidate(
        label=kwargs.get("label", ""), type=kwargs.get("type", "unknown"),
        priority=kwargs.get("priority", 50), healthy=kwargs.get("healthy", True),
        reason=kwargs.get("reason", ""), source=kwargs.get("source", "detected"),
        cwd=kwargs.get("cwd", ""), command=tuple(kwargs.get("command", ())),
        _data=data)

CREATE_NEW_CONSOLE = 0x00000010
CREATE_NO_WINDOW = 0x08000000
WSL_PROBE_TIMEOUT = 10
_wsl_usable = None  # tri-state cache: None unknown, True/False resolved
_godot_discovered = None  # tri-state cache: None unknown, path or False

# --- launcher type constants (values are the historical type strings) -----
TYPE_BAT = "bat"
TYPE_POWERSHELL = "ps1"
TYPE_NPM = "npm"
TYPE_GODOT_RUN = "godot_run"
TYPE_GODOT_EDIT = "godot_edit"
TYPE_PY = "py"
TYPE_RBX = "rbx"
TYPE_SH = "sh"
TYPE_CUSTOM = "custom"

CUSTOM_LAUNCHERS_FIELD = "custom_launchers"

# --- priority convention (lower = better primary candidate) ---------------
PRIORITY_NPM_RUN = 10         # dev/start/serve/preview heads
PRIORITY_PROJECT_SCRIPT = 11  # bat/cmd/ps1/py/godot-run/rbx project starts
PRIORITY_GODOT_EDIT = 13
PRIORITY_NPM_APPRUN = 14      # tauri/electron-style app runners
PRIORITY_NPM_BUILD = 20       # build/dist/package
PRIORITY_NPM_OTHER = 22       # recognized but uncategorized scripts
PRIORITY_NPM_UTILITY = 24     # test/lint/format/audit/deploy
PRIORITY_DEMOTED_SCRIPT = 30  # utility-named shell scripts (cleanup/setup...)
PRIORITY_SH = 40              # POSIX scripts (need WSL/git-bash)

SCRIPT_ENTRY_CANDIDATES = ("main.py", "app.py", "run.py", "start.py")
NPM_SCRIPT_RE = re.compile(r"^[A-Za-z0-9@_./:\-]{1,64}$")
NPM_RUN_WORDS = {"dev", "start", "serve", "preview"}
NPM_APP_WORDS = {"tauri", "electron", "desktop"}
NPM_BUILD_WORDS = {"build", "dist", "package"}
NPM_UTILITY_WORDS = {"test", "check", "lint", "format", "audit", "deploy"}
UTILITY_NAME_RE = re.compile(
    r"clean|organize|organise|setup|configure|install|uninstall|repair"
    r"|recover|migrat|backup|remnant|gradlew|disable|enable|toggle",
    re.IGNORECASE)
DEFAULT_SUBDIR_EXCLUDES = {
    "node_modules", "venv", ".venv", "__pycache__", ".next", "dist",
    "build", "out", ".godot", "site-packages", ".cache", "target",
    "binaries", "intermediate", "saved", "deriveddatacache", "release",
}
GODOT_SEARCH_DIRS = (
    "%LOCALAPPDATA%/Programs/Godot",
    "%LOCALAPPDATA%/Programs/Godot4",
    "%LOCALAPPDATA%/Programs/GodotShim",
    "%PROGRAMFILES%/Godot",
)
STUB_BAT = (
    "@echo off\r\n"
    'cd /d "%~dp0"\r\n'
    "    REM Add the project's start command below.\r\n"
    "echo Add your start command to this file.\r\n"
    "pause\r\n"
)

log = logging.getLogger(__name__)


def is_wsl_usable():
    """True only if WSL can actually execute a Linux command.

    Finding wsl.exe on PATH proves nothing: Windows ships the stub on
    installs with no distribution. One benign no-op (`wsl true`) is the
    proof; the result is cached for the process lifetime.
    """
    global _wsl_usable
    if _wsl_usable is None:
        try:
            r = subprocess.run(
                ["wsl", "true"],
                capture_output=True, timeout=WSL_PROBE_TIMEOUT,
                creationflags=CREATE_NO_WINDOW,
            )
            _wsl_usable = r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _wsl_usable = False
        log.info("WSL capability probe: usable=%s", _wsl_usable)
    return _wsl_usable


def resolve_sh_executor():
    """Return 'wsl' or 'git-bash' if a POSIX shell is available, else None.

    System32's bash.exe stub belongs to WSL and must not be mistaken for
    Git Bash; Git Bash is located relative to git.exe instead.
    """
    if shutil.which("wsl") and is_wsl_usable():
        return "wsl"
    git_exe = shutil.which("git")
    if git_exe:
        git_dir = Path(git_exe).resolve().parent.parent
        for cand in (git_dir / "bin" / "bash.exe",
                     git_dir / "usr" / "bin" / "bash.exe"):
            if cand.exists():
                return "git-bash"
    return None


SH_LABELS = {"wsl": "WSL", "git-bash": "Git Bash"}


def resolve_powershell_exe():
    """Resolve the PowerShell runtime actually used to run .ps1 scripts.

    Windows PowerShell (``powershell``) is preferred; PowerShell 7+ (``pwsh``)
    is accepted when the legacy executable is absent. Detection, the launcher
    picker, and execution all agree because they use this same resolution.
    """
    for name in ("powershell", "pwsh"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _discover_godot_exe():
    """Best-effort Godot executable discovery; cached for the process.

    Checks PATH first, then a bounded list of known Windows install
    locations (one directory level). Never launches anything.
    """
    global _godot_discovered
    if _godot_discovered is None:
        found = None
        for name in ("godot", "godot4"):
            p = shutil.which(name)
            if p:
                found = p
                break
        if not found:
            for pattern in GODOT_SEARCH_DIRS:
                d = Path(os.path.expandvars(pattern))
                try:
                    if not d.is_dir():
                        continue
                    exes = [x for x in sorted(os.listdir(d))
                            if x.lower().endswith(".exe")]
                except OSError:
                    continue
                godot_exes = [x for x in exes if "godot" in x.lower()]
                pick = (godot_exes or exes)[:1]
                if pick:
                    found = str(d / pick[0])
                    break
        _godot_discovered = found or False
        if found:
            log.info("Godot executable auto-discovered: %s", found)
    return _godot_discovered or None


def resolve_godot_exe(settings):
    """Return (exe_path_or_None, source) — configured beats discovered."""
    configured = settings.get("godot_exe")
    if configured and Path(configured).exists():
        return str(configured), "configured"
    discovered = _discover_godot_exe()
    if discovered:
        return discovered, "auto-discovered"
    return None, "not available"


def npm_script_priority(name):
    """Deterministic priority tier for an npm script name."""
    head = name.split(":", 1)[0]
    if head in NPM_RUN_WORDS:
        return PRIORITY_NPM_RUN
    if head in NPM_APP_WORDS:
        return PRIORITY_NPM_APPRUN
    if head in NPM_BUILD_WORDS:
        return PRIORITY_NPM_BUILD
    if head in NPM_UTILITY_WORDS:
        return PRIORITY_NPM_UTILITY
    return PRIORITY_NPM_OTHER


def select_primary_command(commands):
    """Choose a primary only when the best evidence is unique.

    Ordering remains deterministic, but equal-priority candidates are exposed
    as alternatives instead of being presented as an unjustified default.
    """
    healthy = [c for c in commands if c.get("healthy", True)
               and c.get("priority") != PRIORITY_DEMOTED_SCRIPT]
    if not healthy:
        return None
    best_priority = min(c.get("priority", 50) for c in healthy)
    best = [c for c in healthy if c.get("priority", 50) == best_priority]
    return best[0] if len(best) == 1 else None


def order_commands(commands):
    """Return all candidates in stable priority/label/type order."""
    return sorted(commands, key=lambda c: (
        c.get("priority", 50), str(c.get("label", "")).casefold(),
        str(c.get("type", "")), str(c.get("cwd", "")).casefold()))


def launcher_display_name(candidate):
    """Return a presentation-friendly name without changing the candidate.

    Detected script labels use a leading play marker as domain evidence. The
    marker is useful in raw launcher data, but redundant when the label is
    displayed next to the ``Run`` action.
    """
    label = candidate.get("label", "Launcher")
    if isinstance(label, str) and label.startswith("\u25b6 "):
        return label[2:]
    return label


def launcher_summary(candidate):
    """Return progressive-disclosure details suitable for a launcher picker."""
    command = candidate.get("command")
    if not command:
        if candidate.get("type") == TYPE_NPM:
            command = (candidate.get("manager", "npm"), "run", candidate.get("script", ""))
        elif candidate.get("file"):
            command = (candidate.get("file"),)
        elif candidate.get("exe"):
            command = (candidate.get("exe"),)
        else:
            command = ()
    return {
        "name": launcher_display_name(candidate),
        "type": candidate.get("type", "unknown"),
        "command": tuple(command),
        "cwd": candidate.get("cwd", ""),
        "source": candidate.get("source", "detected"),
        "available": bool(candidate.get("healthy", True)),
        "reason": candidate.get("reason", ""),
    }


def bounded_launcher_view(commands, limit=5):
    """Return (primary, visible alternatives, hidden alternatives).

    The complete input is ordered and retained, including unavailable
    candidates. Only the ordinary surface is bounded; Primary eligibility is
    evaluated independently from complete visibility.
    """
    ordered = order_commands(commands)
    primary = select_primary_command(ordered)
    alternatives = [c for c in ordered if c is not primary]
    limit = max(0, int(limit))
    return primary, alternatives[:limit], alternatives[limit:]


def _candidate_subdirs(root, settings):
    """Immediate child directories worth scanning, deterministic order."""
    excludes = set(DEFAULT_SUBDIR_EXCLUDES)
    excludes.update(s.lower() for s in (settings.get("skip_dirs") or []))
    out = []
    try:
        for e in os.scandir(root):
            if not e.is_dir(follow_symlinks=False):
                continue
            name_l = e.name.lower()
            if name_l.startswith(".") or name_l in excludes:
                continue
            out.append(Path(e.path))
    except OSError:
        pass
    return sorted(out, key=lambda p: p.name.lower())


def validate_custom_launcher(value):
    """Return a sanitized custom launcher record, or None when malformed."""
    if not isinstance(value, dict):
        return None
    name = value.get("name")
    executable = value.get("executable")
    args = value.get("args", [])
    cwd = value.get("cwd")
    project_id = value.get("project_id")
    if not all(isinstance(item, str) for item in (name, executable, cwd, project_id)):
        return None
    if not name.strip() or not executable.strip() or not cwd.strip():
        return None
    if not isinstance(value.get("launcher_id", ""), str):
        return None
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        return None
    launcher_id = value.get("launcher_id")
    if not isinstance(launcher_id, str) or not launcher_id.strip():
        launcher_id = ""
    return {"launcher_id": launcher_id,
            "project_id": project_id, "name": name.strip(),
            "executable": executable, "args": list(args), "cwd": cwd}


def custom_launcher_candidate(record):
    """Convert a persisted custom launcher into a typed candidate.

    The executable may be an explicit filesystem path or a command name
    resolved through the normal executable-resolution mechanism (PATH), the
    same way npm and agent launchers are handled. Availability is judged with
    ``processes.resolve_executable`` so detection, the picker, and execution
    agree.
    """
    clean = validate_custom_launcher(record)
    if clean is None:
        return None
    resolved = processes.resolve_executable(clean["executable"])
    cwd = Path(clean["cwd"])
    healthy = resolved is not None and cwd.is_dir()
    if resolved is None:
        reason = "configured executable not found (path or PATH)"
    elif not cwd.is_dir():
        reason = "configured working directory not found"
    else:
        reason = ""
    return _candidate(label=f"Custom: {clean['name']}", type=TYPE_CUSTOM,
                      source="Custom", custom=clean, executable=clean["executable"],
                      args=list(clean["args"]), cwd=clean["cwd"],
                      command=(clean["executable"], *clean["args"]),
                      priority=PRIORITY_NPM_OTHER, healthy=healthy, reason=reason)


def detect_commands(repo_path, settings, project_id=None, custom_launchers=None):
    """Return mapping-compatible LauncherCandidate objects for this project.

    Every candidate carries `priority` (lower = better primary) and
    `healthy`; unhealthy candidates carry a human-readable `reason`.
    The primary is selected separately by `select_primary_command()`.
    """
    root = Path(repo_path)
    try:
        names = {e.name for e in os.scandir(root)}
    except OSError:
        return []
    cmds = []
    ps1_ok = resolve_powershell_exe() is not None
    for record in custom_launchers or []:
        if project_id is None or record.get("project_id") == project_id:
            candidate = custom_launcher_candidate(record)
            if candidate is not None:
                cmds.append(candidate)

    def script_cmd(stem, fname, kind):
        demoted = bool(UTILITY_NAME_RE.search(stem))
        return _candidate(label=f"\u25b6 {stem}", type=kind,
                          file=str(root / fname), source="project script",
                          priority=PRIORITY_DEMOTED_SCRIPT if demoted
                          else PRIORITY_PROJECT_SCRIPT, healthy=True)

    # 1) root-level .bat/.cmd/.ps1 (case-insensitive twin dedupe: bat wins)
    def files_with(exts):
        found = {}
        for n in sorted(names):
            low = n.lower()
            if low.endswith(exts) and len(n) > 4:
                stem_l = n[:-4].lower()
                if stem_l not in found:
                    found[stem_l] = (n[:-4], n)
        return found

    bats = files_with((".bat", ".cmd"))
    ps1s = files_with(".ps1")
    for _stem_l, (stem, fname) in sorted(bats.items()):
        cmds.append(script_cmd(stem, fname, TYPE_BAT))
    for _stem_l, (stem, fname) in sorted(ps1s.items()):
        if _stem_l not in bats:
            cmds.append(_candidate(label=f"\u25b6 {stem}", type=TYPE_POWERSHELL,
                                    file=str(root / fname), source="project script",
                                    priority=PRIORITY_DEMOTED_SCRIPT if
                                    UTILITY_NAME_RE.search(stem)
                                    else PRIORITY_PROJECT_SCRIPT,
                                    healthy=ps1_ok,
                                    reason="PowerShell not found" if not ps1_ok else ""))

    sh_files = [n for n in sorted(names) if n.lower().endswith(".sh")]
    if sh_files:
        sh_executor = resolve_sh_executor()
        for n in sh_files:
            if sh_executor:
                cmds.append(_candidate(label=f"\u25b6 {n} ({SH_LABELS[sh_executor]})",
                                       type=TYPE_SH, file=str(root / n),
                                       executor=sh_executor,
                                       source=SH_LABELS[sh_executor],
                                       priority=PRIORITY_SH, healthy=True))

    # 2) package.json scripts (root first; one-level nested as fallback)
    def add_npm_scripts(pkg_dir, label_prefix=""):
        try:
            data = json.loads(
                (pkg_dir / "package.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return
        for name in (data.get("scripts") or {}):
            if name.startswith(("pre", "post")):
                continue
            if not NPM_SCRIPT_RE.match(name):
                log.info("skipped npm script with unsafe name %r in %s",
                         name, pkg_dir)
                continue
            manager, manager_exe, evidence = detect_package_manager(pkg_dir)
            resolved_manager = (processes.resolve_executable(manager_exe)
                                if manager != "ambiguous package manager" else None)
            available = bool(resolved_manager)
            cmd = _candidate(label=f"{label_prefix}{manager} run {name}",
                             type=TYPE_NPM, script=name, cwd=str(pkg_dir),
                             manager=manager, source=evidence,
                             priority=npm_script_priority(name), healthy=available)
            if manager == "ambiguous package manager":
                values = cmd.as_dict()
                values["healthy"] = False
                values["reason"] = evidence
                cmd = _candidate(**values)
            elif not available:
                values = cmd.as_dict()
                values["healthy"] = False
                values["reason"] = f"{manager_exe} not found on PATH"
                cmd = _candidate(**values)
            cmds.append(cmd)

    if "package.json" in names:
        add_npm_scripts(root)
    else:
        for sub in _candidate_subdirs(root, settings):
            if (sub / "package.json").exists():
                add_npm_scripts(sub, f"[{sub.name}] ")
                break

    # 3) Godot project (root preferred; at most one nested level)
    godot_dirs = [root] if "project.godot" in names else []
    if not godot_dirs:
        for sub in _candidate_subdirs(root, settings):
            if (sub / "project.godot").exists():
                godot_dirs.append(sub)
                break
    if godot_dirs:
        exe, _source = resolve_godot_exe(settings)
        gdir = godot_dirs[0]
        suffix = "" if gdir == root else f" [{gdir.name}]"
        for label, ctype in (("Godot: Run", TYPE_GODOT_RUN),
                             ("Godot: Edit", TYPE_GODOT_EDIT)):
            cmd = _candidate(label=f"{label}{suffix}", type=ctype,
                             cwd=str(gdir), source=f"Godot project ({_source})",
                             priority=PRIORITY_PROJECT_SCRIPT
                             if ctype == TYPE_GODOT_RUN else PRIORITY_GODOT_EDIT,
                             healthy=bool(exe))
            values = cmd.as_dict()
            if exe:
                values["exe"] = exe
            else:
                values["reason"] = ("Godot executable not configured or "
                                    "found (Settings > Integrations > Godot executable)")
            cmd = _candidate(**values)
            cmds.append(cmd)

    # 4) Python project venv + entry point (root, src/, or app/ layout)
    entries = [(nm, root / nm) for nm in SCRIPT_ENTRY_CANDIDATES
               if nm in names]
    for sub in ("src", "app"):
        entries += [(f"{sub}/{nm}", root / sub / nm)
                    for nm in SCRIPT_ENTRY_CANDIDATES
                    if (root / sub / nm).exists()]
    if ".venv" in names or entries:
        py_exe = root / ".venv" / "Scripts" / "python.exe"
        entry = entries[0] if entries else None
        if entry:
            rel, epath = entry
            cmds.append(_candidate(label=f"Python: {rel}", type=TYPE_PY,
                                    exe=str(py_exe), entry=str(epath),
                                    cwd=str(root), source="Python entrypoint",
                                    priority=PRIORITY_PROJECT_SCRIPT,
                                    healthy=py_exe.exists(),
                                    reason=".venv python interpreter not found"
                                    if not py_exe.exists() else ""))

    # 5) Roblox place files (open in Studio via file association)
    rbx = sorted(n for n in names
                 if n.lower().endswith((".rbxl", ".rbxlx")))
    for n in rbx[:3]:
        cmds.append(_candidate(label=f"Roblox Studio: {n}", type=TYPE_RBX,
                                    file=str(root / n), source="Roblox place",
                                    priority=PRIORITY_PROJECT_SCRIPT, healthy=True))


    return cmds


def detect_package_manager(pkg_dir):
    """Return (manager, executable, evidence), preserving conflicts."""
    root = Path(pkg_dir)
    evidence = []
    if (root / "package-lock.json").exists():
        evidence.append("package-lock.json")
    if (root / "pnpm-lock.yaml").exists():
        evidence.append("pnpm-lock.yaml")
    if (root / "yarn.lock").exists():
        evidence.append("yarn.lock")
    if (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        evidence.append("Bun lockfile")
    if len(evidence) == 1:
        marker = evidence[0]
        manager = {"package-lock.json": "npm", "pnpm-lock.yaml": "pnpm",
                   "yarn.lock": "yarn", "Bun lockfile": "bun"}[marker]
        return manager, manager, marker
    if len(evidence) > 1:
        return "ambiguous package manager", "npm", "conflicting lockfiles: " + ", ".join(evidence)
    return "npm", "npm", "package.json (no lockfile evidence)"


def run_command(cmd, settings):
    """Execute a detected command. Each launch is detached from the app."""
    t = cmd["type"]
    if t == TYPE_BAT:
        file = cmd.get("file")
        if not isinstance(file, str) or not Path(file).is_file():
            raise OSError("project script is unavailable")
        processes.spawn_structured(
            file, cwd=str(Path(file).parent), popen=subprocess.Popen,
            creationflags=CREATE_NEW_CONSOLE)
    elif t == TYPE_POWERSHELL:
        file = cmd.get("file")
        if not isinstance(file, str) or not Path(file).is_file():
            raise OSError("PowerShell project script is unavailable")
        ps_exe = resolve_powershell_exe()
        if not ps_exe:
            raise OSError("PowerShell is not available; run the script from a terminal.")
        processes.spawn_structured(
            ps_exe, ("-NoProfile", "-File", file),
            cwd=str(Path(file).parent), popen=subprocess.Popen,
            creationflags=CREATE_NEW_CONSOLE)
    elif t == TYPE_CUSTOM:
        executable = cmd.get("executable")
        args = cmd.get("args") or []
        cwd = cmd.get("cwd")
        if not isinstance(executable, str) or not executable.strip():
            raise ValueError("custom launcher executable is empty")
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise ValueError("custom launcher arguments are invalid")
        if not isinstance(cwd, str) or not Path(cwd).is_dir():
            raise OSError("custom launcher working directory is unavailable")
        processes.spawn_structured(
            executable, args, cwd=cwd, popen=subprocess.Popen,
            creationflags=CREATE_NEW_CONSOLE)
    elif t == TYPE_NPM:
        manager = cmd.get("manager") or "npm"
        if manager == "ambiguous package manager":
            raise ValueError("package manager evidence is conflicting")
        script = cmd.get("script")
        if not isinstance(script, str) or not NPM_SCRIPT_RE.fullmatch(script):
            raise ValueError("package script name is invalid")
        processes.spawn_structured(
            manager, ("run", script), cwd=cmd["cwd"],
            popen=subprocess.Popen, creationflags=CREATE_NEW_CONSOLE)
    elif t in (TYPE_GODOT_RUN, TYPE_GODOT_EDIT):
        exe = cmd.get("exe") or settings.get("godot_exe")
        if not exe:
            raise OSError("Godot executable not configured")
        args = []
        if t == TYPE_GODOT_EDIT:
            args.append("-e")
        args += ["--path", cmd["cwd"]]
        processes.spawn_structured(
            exe, args, cwd=cmd["cwd"], popen=subprocess.Popen)
    elif t == TYPE_PY:
        processes.spawn_structured(
            cmd["exe"], (cmd["entry"],), cwd=cmd["cwd"],
            popen=subprocess.Popen, creationflags=CREATE_NEW_CONSOLE)
    elif t == TYPE_RBX:
        os.startfile(cmd["file"])
    elif t == TYPE_SH:
        executor = cmd.get("executor")
        if not executor:
            raise ValueError("sh command without a resolved executor")
        exe = shutil.which(executor) if executor == "wsl" else None
        if executor == "git-bash":
            git_exe = shutil.which("git")
            git_dir = Path(git_exe).resolve().parent.parent if git_exe else None
            exe = str(git_dir / "bin" / "bash.exe") if git_dir and (
                git_dir / "bin" / "bash.exe").exists() else None
        if not exe:
            raise OSError(f"{SH_LABELS.get(executor, executor or 'shell')} "
                          "is not available; run the script from a terminal.")
        if executor == "wsl":
            processes.spawn_structured(
                exe, ("./" + Path(cmd["file"]).name,),
                cwd=str(Path(cmd["file"]).parent), popen=subprocess.Popen,
                creationflags=CREATE_NEW_CONSOLE)
        else:
            processes.spawn_structured(
                exe, (cmd["file"],), cwd=str(Path(cmd["file"]).parent),
                popen=subprocess.Popen, creationflags=CREATE_NEW_CONSOLE)
    else:
        raise ValueError(f"unknown command type: {t}")


def generate_stub_bat(repo_path, confirm=None):
    """Write a starter run.bat; UI callers must provide confirmation."""
    target = Path(repo_path) / "run.bat"
    if confirm is not None and not bool(confirm(str(target))):
        return False
    if target.exists():
        return False
    # newline="" keeps the embedded \r\n line endings intact; text-mode
    # universal-newlines would double them into \r\r\n on Windows, which is
    # at best cosmetic but can confuse older cmd.exe parsing after edits.
    target.write_text(STUB_BAT, encoding="ascii", newline="")
    return True
