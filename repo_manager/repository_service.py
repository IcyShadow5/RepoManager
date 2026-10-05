"""Presentation-neutral registry and scan boundary for the Qt migration."""
import os
import shutil
import subprocess
import uuid
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from .scan_control import active_scan, checkpoint

from . import agents, health, health_preferences, launchers, processes, projects, reports, relocation, scan_state, scanner, store


@dataclass(frozen=True)
class ScanOutcome:
    records: list[dict]
    problems: list[dict]


class RepositorySession:
    def __init__(self):
        store.ensure_dirs()
        self.settings = store.load_settings()
        self.records, self.report = store.read_registry()
        for record in self.records:
            projects.ensure_project_id(record)

    @property
    def registry_blocked(self):
        return (self.report.get("status") in {"unavailable", "unrecoverable"}
                or bool(self.report.get("write_blocked")))

    def scan(self, control=None):
        with active_scan(control):
            return self._scan_snapshot()

    def _scan_snapshot(self):
        """Observe the same configured roots and merge rules as the classic UI."""
        if self.registry_blocked:
            raise OSError("Registry recovery required before scanning")
        settings = dict(self.settings)
        records = copy.deepcopy(self.records)
        found = scanner.find_repo_dirs(
            list(settings["roots"]), int(settings.get("depth", 4)),
            list(settings.get("skip_dirs", [])))
        root_problems = list(getattr(found, "problems", []))
        merged, problems = scanner.merge_scan(
            records, list(found),
            move_suppressions=settings.get("move_suppressions") or [],
            failed_roots=root_problems)
        checkpoint()
        return ScanOutcome(merged, root_problems + list(problems))

    def accept_scan(self, outcome):
        if self.registry_blocked:
            raise OSError("Registry recovery required before saving scan")
        merged = scan_state.reconcile_scan_result(outcome.records, self.records)
        store.save_projects(merged)
        self.records = merged
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        updated = {**self.settings, "last_full_scan_at": stamp}
        try:
            store.save_settings(updated)
        except OSError as exc:
            return f"Inventory saved; scan timestamp could not be saved: {exc}"
        self.settings = updated
        return None

    def visible(self, query="", section="repositories", sort_col=None, sort_desc=False):
        if section == "working":
            source = projects.working_on_now_rows(
                self.records, os.path.isdir)
            rows = [record for record in source if projects.is_visible(record, query)]
            return projects.sorted_projects(rows, sort_col, sort_desc) if sort_col else rows
        else:
            source = [record for record in self.records
                      if not projects.is_ignored(record)]
        return projects.sorted_projects(
            (record for record in source if projects.is_visible(record, query)),
            sort_col, sort_desc)

    def counts(self):
        visible = [record for record in self.records
                   if not projects.is_ignored(record)
                   and projects.is_repository_backed(record)]
        observed = [record for record in visible
                    if not record.get("broken") and record.get("status_available") is True
                    and isinstance(record.get("dirty"), int)
                    and not isinstance(record.get("dirty"), bool)
                    and record["dirty"] >= 0]
        return {
            "repositories": len(visible),
            "clean": sum(record.get("dirty") == 0 for record in observed),
            "modified": sum((record.get("dirty") or 0) > 0 for record in observed),
            "unobserved": len(visible) - len(observed),
        }

    def resolve(self, target):
        record = target.resolve(self.records)
        if record is None:
            raise ValueError("Project association changed; select it again")
        return record

    def save_curation(self, target, status, pinned, focus):
        if self.registry_blocked:
            raise OSError("Registry recovery required before saving")
        if status not in {"idea", "active", "paused", "archived"}:
            raise ValueError("Invalid Project status")
        record = self.resolve(target)
        updated = projects.apply_curation(
            dict(record), status=status, pinned=pinned, focus=focus)
        records = [updated if item is record else item for item in self.records]
        store.save_projects(records)
        self.records = records

    def load_note(self, target):
        record = self.resolve(target)
        return store.load_note(record.get("name", ""),
                               projects.project_folder(record),
                               projects.project_id(record))

    def save_note(self, target, text):
        if self.registry_blocked:
            raise OSError("Registry recovery required before saving notes")
        record = self.resolve(target)
        store.save_note(record.get("name", ""), projects.project_folder(record),
                        text, projects.project_id(record))

    @staticmethod
    def _scan_root(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Enter a scan folder path")
        root = value.strip()
        if os.name == "nt" and Path(root).drive and not Path(root).is_absolute():
            raise ValueError("Use an absolute folder path, such as C:\\; C: is relative to the current working folder")
        try:
            available = os.path.isdir(root)
        except OSError:
            available = False
        if not available:
            raise ValueError("Choose an existing accessible scan folder")
        return root

    @staticmethod
    def add_scan_root(roots, value):
        """Validate an unsaved folder addition without changing settings."""
        root = RepositorySession._scan_root(value)
        key = scanner.scan_root_key(root)
        if any(scanner.scan_root_key(item) == key for item in roots):
            raise ValueError("This scan folder is already included")
        return [*roots, root]

    def save_settings(self, roots, depth, agent_command, godot, ignored_health=None, agent_targets=None):
        if not 1 <= depth <= 10:
            raise ValueError("Scan depth must be a whole number from 1 to 10")
        if not agent_command.strip():
            raise ValueError("Agent command cannot be empty")
        unique = []
        seen = set()
        for root in roots:
            root = self._scan_root(root)
            key = scanner.scan_root_key(root)
            if key not in seen:
                unique.append(root)
                seen.add(key)
        updated = {**self.settings, "roots": unique, "depth": depth,
                   "agent_cmd": agent_command.strip(), "godot_exe": godot.strip()}
        if agent_targets is not None:
            updated["agent_targets"] = agents.validate_agent_targets(agent_targets)
        if ignored_health is not None:
            if (not isinstance(ignored_health, list)
                    or any(rule not in health_preferences.ADVISORY_RULES for rule in ignored_health)):
                raise ValueError("Only advisory Health checks can be disabled")
            prefs = health_preferences.preferences(self.settings)
            prefs["global"] = health_preferences.rules(ignored_health)
            updated[health_preferences.SETTINGS_KEY] = prefs
        store.save_settings(updated)
        self.settings = updated

    def select_agent(self, agent_id):
        available = [item for item in agents.agent_catalog(self.settings)
                     if item["agent_id"] == agent_id and item["availability"] == agents.AVAILABLE]
        if not available:
            raise ValueError("Agent target is no longer available; check Settings")
        updated = {**self.settings, "selected_agent_id": agent_id}
        store.save_settings(updated)
        self.settings = updated

    def set_health_ignored(self, target, rule, ignored):
        if self.registry_blocked:
            raise OSError("Registry recovery required before saving Health preferences")
        record = self.resolve(target)
        project_id = projects.project_id(record)
        if not project_id:
            raise ValueError("A stable Project identity is required")
        if rule not in health_preferences.ADVISORY_RULES:
            raise ValueError("This Health check cannot be ignored")
        if ignored:
            result = health.evaluate_repository(record.get("path"), record)
            finding = next((item for item in result.findings if item.rule == rule), None)
            if finding is None or not health_preferences.eligible(finding):
                raise ValueError("This finding cannot be ignored")
        prefs = health_preferences.preferences(self.settings)
        local = set(prefs["repositories"].get(project_id, []))
        if ignored:
            local.add(rule)
        else:
            local.discard(rule)
        if local:
            prefs["repositories"][project_id] = health_preferences.rules(list(local))
        else:
            prefs["repositories"].pop(project_id, None)
        updated = {**self.settings, health_preferences.SETTINGS_KEY: prefs}
        store.save_settings(updated)
        self.settings = updated

    def inspect(self, target):
        record = dict(self.resolve(target))
        folder = projects.project_folder(record)
        result = health.evaluate_repository(record.get("path"), record)
        result = health_preferences.apply(result, self.settings, projects.project_id(record))
        commands = launchers.detect_commands(
            folder, self.settings, projects.project_id(record),
            record.get("custom_launchers", [])) if folder else []
        return result, commands

    def open_folder(self, target, action):
        record = self.resolve(target)
        folder = projects.project_folder(record)
        if not folder or not os.path.isdir(folder):
            raise OSError("Project folder is unavailable")
        argument = folder
        if action == "explorer":
            executable, arguments = "explorer.exe", (folder,)
        elif action == "terminal":
            executable, arguments = "wt.exe", ("-d", folder)
        elif action == "vscode":
            executable = shutil.which("code")
            if not executable:
                candidate = Path.home() / "AppData/Local/Programs/Microsoft VS Code/bin/code.cmd"
                executable = str(candidate) if candidate.is_file() else None
            if not executable:
                raise OSError("VS Code not found on PATH")
            if os.name == "nt" and Path(executable).suffix.casefold() in processes.BATCH_SUFFIXES:
                argument += "\\" * (len(folder) - len(folder.rstrip("\\")))
            arguments = (argument,)
        else:
            raise ValueError("Unknown folder action")
        try:
            processes.spawn_structured(executable, arguments, cwd=folder,
                                       popen=subprocess.Popen,
                                       creationflags=0x08000000 if os.name == "nt" else 0)
        except OSError as exc:
            if action == "terminal" and str(exc) == "executable not found: wt.exe":
                raise OSError("Windows Terminal (wt.exe) was not found. Install Windows Terminal "
                              "or enable its app execution alias, then try again. "
                              "PowerShell 7 is not required.") from exc
            raise

    def run_launcher(self, target, approved):
        record = self.resolve(target)
        folder = projects.project_folder(record)
        candidates = launchers.detect_commands(
            folder, self.settings, projects.project_id(record),
            record.get("custom_launchers", []))
        current = next((item for item in candidates if item == approved), None)
        if current is None:
            raise ValueError("Launcher changed; refresh project details before running")
        if not current.get("healthy", True):
            raise OSError(current.get("reason") or "Launcher is unavailable")
        launchers.run_command(current, self.settings)

    def save_launcher(self, target, value, *, remove=False):
        if self.registry_blocked:
            raise OSError("Recover the registry before changing launchers")
        record = self.resolve(target)
        existing = record.get(launchers.CUSTOM_LAUNCHERS_FIELD, [])
        identity = value.get("launcher_id")
        if identity and not any(item.get("launcher_id") == identity for item in existing):
            raise ValueError("Launcher no longer exists; reopen the editor")
        if not remove:
            clean = launchers.validate_custom_launcher({**value,
                "launcher_id": identity or str(uuid.uuid4()), "project_id": projects.project_id(record)})
            if clean is None:
                raise ValueError("Name, executable and working directory are required; arguments must be literal strings")
        elif not identity:
            raise ValueError("Choose a saved launcher to remove")
        updated = dict(record)
        updated[launchers.CUSTOM_LAUNCHERS_FIELD] = [item for item in existing if item.get("launcher_id") != identity]
        if not remove:
            updated[launchers.CUSTOM_LAUNCHERS_FIELD].append(clean)
        records = [updated if item is record else item for item in self.records]
        store.save_projects(records)
        self.records = records

    def generate_stub(self, target):
        if self.registry_blocked:
            raise OSError("Recover the registry before creating a launcher")
        record = self.resolve(target)
        folder = projects.project_folder(record)
        if not projects.is_repository_backed(record) or not folder or not Path(folder).is_dir():
            raise ValueError("Select an available repository")
        return launchers.generate_stub_bat(folder, confirm=lambda _: True)

    def export(self, target, destination, kind):
        record = self.resolve(target)
        if kind == "project":
            content = reports.to_json(projects.build_project_export(record))
        elif kind == "report":
            report = projects.build_repository_report(record)
            content = reports.to_json(report) if str(destination).lower().endswith(".json") else reports.to_markdown(report)
        else:
            raise ValueError("Unknown export type")
        reports.write_text_atomic(destination, content)

    def set_ignored(self, target, ignored):
        if self.registry_blocked:
            raise OSError("Recover the registry before changing projects")
        record = self.resolve(target)
        updated = dict(record, ignored=bool(ignored))
        records = [updated if item is record else item for item in self.records]
        store.save_projects(records)
        self.records = records

    def retry_registry(self):
        records, report = store.read_registry()
        if self.registry_blocked and report.get("status") == "fresh":
            self.report = {**report, "status": "unavailable", "write_blocked": True,
                           "reasons": ["Previously unavailable registry is now missing"]}
            return False
        self.report = report
        if report.get("status") in {"valid", "recovered"}:
            self.records = records
        return not self.registry_blocked

    def apply_move(self, approved):
        if self.registry_blocked:
            raise OSError("Recover the registry before changing locations")
        return relocation.move_outcome(self.records, approved, scanner.utc_now_iso(),
            lambda: store.save_projects(self.records), store.move_note,
            path_exists=os.path.exists, target_identity=scanner.move_target_identity)

    def keep_both(self, suggestion):
        if self.registry_blocked:
            raise OSError("Recover the registry before changing locations")
        old = [suggestion.get("old_path")] + list(suggestion.get("old_paths") or [])
        new = [suggestion.get("new_path")] + list(suggestion.get("new_paths") or [])
        suppressions = list(self.settings.get("move_suppressions", []))
        for source in old:
            for destination in new:
                pair = {"old": source.lower(), "new": destination.lower()} if source and destination else None
                if pair and pair not in suppressions:
                    suppressions.append(pair)
        settings = {**self.settings, "move_suppressions": suppressions}
        # Durable suppression must precede registry cleanup across two files.
        store.save_settings(settings)
        self.settings = settings
        records = copy.deepcopy(self.records)
        relocation.detach_pending_for_keep_both(records, suggestion)
        try:
            store.save_projects(records)
        except OSError as exc:
            raise OSError(f"Pairing suppression saved; registry cleanup awaits a scan: {exc}") from exc
        self.records = records
