"""Qt view model backed by the existing RepositorySession and Git probe."""
import threading
import copy
from uuid import uuid4
from dataclasses import asdict

from PySide6.QtCore import (QAbstractListModel, QModelIndex, QObject, Property,
                            QTimer, Qt, Signal, Slot, qVersion)
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtCore import QUrl

from . import agents, feedback, git_availability, git_operations, health_preferences, health_presentation, launchers, project_actions, projects, providers, relocation, scanner, store, version
from .help_content import HELP_TOPICS
from .qt_git import GitController
from .project_presentation import location_label
from .scan_control import ScanControl, ScanCancelled

QT_COLUMNS = {"name": (180, 130, 420), "status": (65, 55, 180),
              "branch": (90, 70, 260), "dirty": (40, 35, 90),
              "tree": (88, 80, 160), "last_commit": (84, 80, 150),
              "path": (220, 160, 760), "sync": (55, 50, 100),
              "classification": (105, 90, 220), "worktrees": (48, 45, 90)}


class RepositoryListModel(QAbstractListModel):
    FIELDS = (
        "projectId", "projectName", "projectStatus", "branch", "dirty",
        "dirtyKnown", "lastCommit", "path", "focus", "pinned", "kind",
        "classification", "ahead", "behind", "syncKnown", "worktreeCount", "worktreesKnown", "displayPath", "broken")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._roles = {Qt.ItemDataRole.UserRole + index + 1: field.encode()
                       for index, field in enumerate(self.FIELDS)}

    def roleNames(self):
        return self._roles

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        field = self._roles.get(role)
        return self._rows[index.row()].get(field.decode()) if field else None

    def replace(self, rows):
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row(self, index):
        return dict(self._rows[index]) if 0 <= index < len(self._rows) else {}


def project_row(record, roots=()):
    location = projects.project_folder(record) or ""
    return {
        "projectId": projects.project_id(record) or location,
        "projectName": projects.project_display_name(record),
        "projectStatus": str(record.get("status") or "idea"),
        "branch": "—" if record.get("broken") else str(record.get("branch") or "—"),
        "broken": bool(record.get("broken")),
        "dirty": record.get("dirty") if isinstance(record.get("dirty"), int) else 0,
        "dirtyKnown": (not record.get("broken") and record.get("status_available") is True
                       and isinstance(record.get("dirty"), int)
                       and not isinstance(record.get("dirty"), bool)
                       and record["dirty"] >= 0),
        "lastCommit": str(record.get("last_commit_date") or "—")[:10],
        "path": location,
        "displayPath": location_label(location, roots),
        "focus": str(record.get("focus") or ""),
        "pinned": bool(record.get("pinned")),
        "kind": "Git repository" if projects.is_repository_backed(record)
                else "Folder project",
        "classification": projects.classification_display(record),
        "head": str(record.get("head") or ""),
        "upstream": str(record.get("upstream") or ""),
        "ahead": record.get("ahead") or 0,
        "behind": record.get("behind") or 0,
        "syncKnown": not record.get("broken") and record.get("sync_available") is True,
        "staged": record.get("staged") or 0,
        "unstaged": record.get("unstaged") or 0,
        "untracked": record.get("untracked") or 0,
        "lastMessage": str(record.get("last_commit_msg") or ""),
        "remote": git_operations.redact(record.get("remote") or ""),
        "worktrees": projects.display_worktree_records(record.get("worktrees")),
        "worktreeCount": len(projects.display_worktree_records(record.get("worktrees"))),
        "worktreesKnown": record.get("worktrees_available") is True,
    }


class RepoManagerBridge(QObject):
    changed = Signal()
    scanReady = Signal(object)
    scanFailed = Signal(str)
    detailReady = Signal(object, int, object, object)
    detailFailed = Signal(int, str)
    postRunReady = Signal(object)
    confirmAgentCloseRequested = Signal()
    confirmScanCloseRequested = Signal()
    closeReady = Signal()
    noticeRequested = Signal(str, str)
    providerReady = Signal(object, str, int, object)
    actionConfirmationRequested = Signal(str, str)
    quickRunChooserRequested = Signal()
    agentChooserRequested = Signal()

    def __init__(self, session, *, auto_scan=True, parent=None):
        super().__init__(parent)
        self.session = session
        self._model = RepositoryListModel(self)
        self._query = ""
        self._section = "repositories"
        self._sort_col = ""
        self._sort_desc = False
        self._selected_id = ""
        self._selected = {}
        self._scanning = False
        self._status = "Ready"
        self._problem_count = 0
        self._git = git_availability.check_git()
        self._scan_thread = None
        self._scan_control = None
        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(150)
        self._scan_timer.timeout.connect(self.changed.emit)
        self._pending_scan = False
        self._closing = False
        self._counts = {}
        self._working = {}
        self._health = {}
        self._commands = []
        self._command_target = None
        self._quick_run_target = None
        self._quick_run_commands = []
        self._detail_gen = 0
        self._detail_loading = False
        self._note_text = ""
        self._note_target = None
        self._note_dirty = False
        self._note_load_failed = False
        self._feedback_status = ""
        self._problems = []
        self._moves = []
        self._approved_move = None
        self._action_target = None
        self._action = ""
        self._launcher_target = None
        self._export_target = None
        self._export_kind = ""
        self._provider = {}
        self._provider_generation = 0
        self._provider_busy = False
        self._columns = {key: value[0] for key, value in QT_COLUMNS.items()}
        for key, value in (self.session.settings.get("qt_column_widths") or {}).items():
            if key in QT_COLUMNS and isinstance(value, int) and not isinstance(value, bool):
                self._columns[key] = max(QT_COLUMNS[key][1], min(QT_COLUMNS[key][2], value))
        self._theme = self.session.settings.get("qt_theme", "dark" if store.settings_recovery_report().get("status") == "fresh" else self.session.settings.get("theme", "dark"))
        self._runs = []
        self._agent_processes = {}
        self._post_runs = set()
        self._agent_readiness = {}
        self._agent_choices = []
        self._selected_agent_id = self.session.settings.get("selected_agent_id", "")
        self._agent_chooser_target = None
        self._close_after_agents = False
        self._close_after_git = False
        self._close_after_scan = False
        self._git_controller = GitController(session, self)
        self._git_controller.finished.connect(self._git_finished)
        self._agent_timer = QTimer(self)
        self._agent_timer.setInterval(250)
        self._agent_timer.timeout.connect(self._poll_agents)
        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.setInterval(1000)
        self._note_timer.timeout.connect(self._flush_notes)
        self.scanReady.connect(self._accept_scan)
        self.scanFailed.connect(self._scan_failed)
        self.detailReady.connect(self._accept_detail)
        self.detailFailed.connect(self._detail_failed)
        self.postRunReady.connect(self._accept_post_run)
        self.providerReady.connect(self._accept_provider)
        self._refresh_rows()
        if self.session.registry_blocked:
            self._status = "Registry recovery required — use Retry registry"
        elif auto_scan and self._git.available:
            QTimer.singleShot(0, self.scan)

    @Property(QObject, constant=True)
    def repositoryModel(self):
        return self._model

    @Property(QObject, constant=True)
    def gitController(self):
        return self._git_controller

    @Property(str, constant=True)
    def versionText(self):
        return version.VERSION

    @Property(str, notify=changed)
    def section(self):
        return self._section

    @Property(str, notify=changed)
    def query(self):
        return self._query

    @Property(str, notify=changed)
    def sortColumn(self):
        return self._sort_col

    @Property(bool, notify=changed)
    def sortDescending(self):
        return self._sort_desc

    @Property("QVariantMap", notify=changed)
    def selectedProject(self):
        return self._selected

    @Property(str, notify=changed)
    def gitState(self):
        return self._git.status

    @Property(str, notify=changed)
    def statusText(self):
        return self._status

    @Property(bool, notify=changed)
    def scanning(self):
        return self._scanning

    @Property("QVariantMap", notify=changed)
    def scanProgress(self):
        return self._scan_control.snapshot() if self._scan_control else {}

    @Property(int, notify=changed)
    def totalCount(self):
        return self._counts.get("repositories", 0)

    @Property(int, notify=changed)
    def cleanCount(self):
        return self._counts.get("clean", 0) if self._git.available else 0

    @Property(int, notify=changed)
    def modifiedCount(self):
        return self._counts.get("modified", 0) if self._git.available else 0

    @Property(int, notify=changed)
    def unobservedCount(self):
        return self._counts.get("unobserved", 0) if self._git.available else self.totalCount

    @Property(int, notify=changed)
    def visibleCount(self):
        return self._model.rowCount()

    @Property(int, notify=changed)
    def selectedIndex(self):
        return next((index for index, row in enumerate(self._model._rows)
                     if row["projectId"] == self._selected_id), -1)

    @Property(int, notify=changed)
    def problemCount(self):
        return self._problem_count

    @Property(str, notify=changed)
    def lastScan(self):
        return str(self.session.settings.get("last_full_scan_at") or "Never")

    @Property(bool, notify=changed)
    def registryBlocked(self):
        return self.session.registry_blocked

    @Property("QVariantMap", notify=changed)
    def workingProject(self):
        return self._working

    @Property("QVariantMap", notify=changed)
    def healthEvidence(self):
        return self._health

    @Property("QVariantList", notify=changed)
    def detectedLaunchers(self):
        return [{"label": launchers.launcher_display_name(command),
                 "source": command.get("source", "detected"),
                 "healthy": command.get("healthy", True),
                 "reason": command.get("reason", "")}
                for command in self._commands]

    @Property(int, notify=changed)
    def quickRunCount(self):
        return sum(command.get("healthy", True) for command in self._commands)

    @Property("QVariantList", notify=changed)
    def quickRunChoices(self):
        return [{"label": launchers.launcher_display_name(command)}
                for command in self._quick_run_commands]

    @Property("QVariantList", notify=changed)
    def healthCheckTypes(self):
        return [{"rule": rule, "label": health_presentation.health_rule_display_name(rule)}
                for rule in health_preferences.ADVISORY_RULES]

    @Property(bool, notify=changed)
    def detailLoading(self):
        return self._detail_loading

    @Property(str, notify=changed)
    def notesText(self):
        return self._note_text

    @Property(bool, notify=changed)
    def notesDirty(self):
        return self._note_dirty

    @Property(bool, notify=changed)
    def notesEditable(self):
        return bool(self._selected and not self.session.registry_blocked
                    and not self._note_load_failed)

    @Property("QVariantMap", notify=changed)
    def settingsData(self):
        try:
            targets = agents.validate_agent_targets(self.session.settings.get("agent_targets", []))
            agent_error = ""
        except ValueError as exc:
            targets, agent_error = [], str(exc)
        return {"roots": self.session.settings.get("roots", []),
                "depth": self.session.settings.get("depth", 4),
                "agent": self.session.settings.get("agent_cmd", "opencode"),
                "agentTargets": targets, "agentConfigurationError": agent_error,
                "godot": self.session.settings.get("godot_exe", ""),
                "ignoredHealth": health_preferences.preferences(self.session.settings)["global"]}

    @Property(str, notify=changed)
    def feedbackStatus(self):
        return self._feedback_status

    @Property("QVariantMap", notify=changed)
    def agentInfo(self):
        return self._agent_readiness

    @Property("QVariantList", notify=changed)
    def agentChoices(self):
        return self._agent_choices

    @Slot(result="QVariantMap")
    def newAgentTarget(self):
        return agents.new_agent("custom:" + str(uuid4()), "", "")

    @Property(bool, notify=changed)
    def agentActive(self):
        return any(run.get("process_state") in (
            agents.STARTING, agents.RUNNING, agents.STOPPING, agents.UNKNOWN_PROCESS)
                   for run in self._runs)

    @Property("QVariantMap", notify=changed)
    def agentRun(self):
        if not self._runs:
            return {}
        run = self._runs[-1]
        return {"id": run["run_id"][:8], "state": run["process_state"],
                "verification": run["verification"],
                "name": run.get("target", {}).get("name", ""),
                "exitCode": run.get("exit_code"),
                "failure": run.get("failure", "")}

    def _refresh_rows(self):
        previous_id = self._selected_id
        rows = [project_row(record, self.session.settings.get("roots", [])) for record in
                self.session.visible(self._query, self._section,
                                     self._sort_col or None, self._sort_desc)]
        selected = next(
            (row for row in rows if row["projectId"] == self._selected_id), {})
        if not selected and rows:
            selected = rows[0]
        new_id = selected.get("projectId", "")
        if new_id != previous_id and not self._flush_notes():
            return False
        self._model.replace(rows)
        self._selected = selected
        self._selected_id = new_id
        self._counts = self.session.counts()
        active = self.session.visible(section="working")
        self._working = project_row(active[0]) if active else {}
        if previous_id != self._selected_id or self._note_target is None:
            self._load_selection()
        self._refresh_agent()
        self.changed.emit()
        return True

    @Slot(str)
    def setQuery(self, value):
        if value != self._query:
            old = self._query
            self._query = value
            if not self._refresh_rows():
                self._query = old
                self.changed.emit()

    @Slot(str)
    def setSection(self, value):
        if value in {"repositories", "working"} and value != self._section:
            old = self._section
            self._section = value
            if not self._refresh_rows():
                self._section = old
                self.changed.emit()

    @Slot(str)
    def sortBy(self, column):
        if column not in {"name", "status", "branch", "dirty", "last_commit", "path", "classification", "sync", "worktrees"}:
            return
        self._sort_desc = not self._sort_desc if column == self._sort_col else False
        self._sort_col = column
        self._refresh_rows()

    @Slot(int)
    def moveSelection(self, offset):
        rows = self._model._rows
        index = next((index for index, row in enumerate(rows)
                      if row["projectId"] == self._selected_id), 0)
        if rows:
            self.selectProject(rows[max(0, min(len(rows) - 1, index + offset))]["projectId"])

    @Slot(str)
    def selectProject(self, project_id):
        if self._closing or not self._flush_notes():
            return
        for row in self._model._rows:
            if row["projectId"] == project_id:
                if project_id == self._selected_id:
                    return
                self._selected_id = project_id
                self._selected = row
                self._load_selection()
                self._refresh_agent()
                self.changed.emit()
                return

    def _selected_target(self):
        record = next((record for record in self.session.records
                       if (projects.project_id(record) or projects.project_folder(record))
                       == self._selected_id), None)
        return project_actions.Target.capture(record) if record else None

    def _load_selection(self):
        self._provider_generation += 1
        self._provider_busy = False
        self._provider = {}
        self._note_target = self._selected_target()
        self._note_load_failed = False
        self._note_text = ""
        if self._note_target:
            try:
                self._note_text = self.session.load_note(self._note_target)
            except (OSError, ValueError) as exc:
                self._note_load_failed = True
                self._status = f"Notes could not be loaded: {exc}"
        self._note_dirty = False
        self._request_detail()

    def _request_detail(self):
        self._quick_run_target = None
        self._quick_run_commands = []
        self._detail_gen += 1
        generation = self._detail_gen
        self._health = {}
        self._commands = []
        target = self._selected_target()
        self._command_target = target
        self._detail_loading = target is not None
        if target is None or self._closing:
            return

        def work():
            try:
                result, commands = self.session.inspect(target)
                if not self._closing:
                    self.detailReady.emit(target, generation, result, commands)
            except Exception as exc:
                if not self._closing:
                    self.detailFailed.emit(generation, str(exc))

        try:
            threading.Thread(target=work, daemon=True).start()
        except RuntimeError as exc:
            self._detail_failed(generation, str(exc))

    @Slot(object, int, object, object)
    def _accept_detail(self, target, generation, result, commands):
        if self._closing or generation != self._detail_gen or target.resolve(self.session.records) is None:
            return
        self._detail_loading = False
        self._commands = list(commands)
        result = health_preferences.apply(result, self.session.settings, target.project_id)
        self._health = {
            "status": result.status,
            "count": result.summary.finding_count,
            "active": result.summary.active_count,
            "ignored": result.summary.ignored_count,
            "unknown": result.summary.unknown_count,
            "stale": result.summary.stale_count,
            "evaluatedAt": result.evaluated_at,
            "score": health_presentation.repository_health_score(result),
            "label": health_presentation.repository_health_band_label(result),
            "counts": health_presentation.health_dashboard_counts(result),
            "summary": "\n".join(health_presentation.health_detail_summary(result)),
            "findings": [{"rule": finding.rule, "status": finding.status,
                          "suppressible": health_preferences.eligible(finding),
                          "suppressionScope": finding.suppression_scope,
                          "observedStatus": finding.observed_status,
                          "severity": finding.severity,
                          "importance": finding.importance,
                          "group": health_presentation.health_finding_group(finding),
                          "freshness": finding.freshness,
                          "explanation": git_operations.redact(finding.explanation),
                          "remediation": git_operations.redact(finding.remediation or ""),
                          "evidence": [{key: git_operations.redact(value) if isinstance(value, str) else value
                                        for key, value in asdict(item).items()} for item in finding.evidence],
                          "target": finding.target, "timestamp": finding.timestamp}
                         for finding in result.prioritized_findings()],
        }
        self.changed.emit()

    @Slot(str, bool, result=bool)
    def setHealthIgnored(self, rule, ignored):
        target = self._selected_target()
        if target is None or self._closing:
            return False
        try:
            self.session.set_health_ignored(target, rule, ignored)
            self._status = "Health preference saved"
            self._request_detail()
            self.changed.emit()
            return True
        except (OSError, ValueError) as exc:
            self._status = f"Health preference was not saved: {exc}"
            self.changed.emit()
            return False

    @Slot(int, str)
    def _detail_failed(self, generation, message):
        if self._closing or generation != self._detail_gen:
            return
        self._detail_loading = False
        self._status = f"Project details unavailable: {message}"
        self.changed.emit()

    @Slot(str, bool, str)
    def saveCuration(self, status, pinned, focus):
        target = self._selected_target()
        if target is None:
            return
        try:
            self.session.save_curation(target, status, pinned, focus)
            self._status = "Project curation saved"
            self._refresh_rows()
        except (OSError, ValueError) as exc:
            self._status = f"Curation was not saved: {exc}"
            self.changed.emit()

    @Slot()
    def toggleWorking(self):
        if not self._selected:
            return
        active = self._selected["projectStatus"] == "active"
        self.saveCuration("paused" if active else "active",
                          self._selected["pinned"] if active else True,
                          self._selected["focus"])

    @Slot(str)
    def editNotes(self, text):
        if not self.notesEditable or text == self._note_text:
            return
        self._note_text = text
        self._note_dirty = True
        self._note_timer.start()
        self.changed.emit()

    @Slot(result=bool)
    def saveNotes(self):
        return self._flush_notes()

    def _flush_notes(self):
        self._note_timer.stop()
        if not self._note_dirty:
            return True
        try:
            self.session.save_note(self._note_target, self._note_text)
        except (OSError, ValueError) as exc:
            self._status = f"Notes were not saved: {exc}"
            self.changed.emit()
            return False
        self._note_dirty = False
        self._status = "Notes saved"
        self.changed.emit()
        return True

    @Slot("QVariantList", str, result="QVariantMap")
    def addScanRoot(self, roots, value):
        try:
            return {"roots": self.session.add_scan_root(roots, value), "error": ""}
        except (OSError, ValueError) as exc:
            return {"roots": roots, "error": str(exc)}

    @Slot("QVariantList", int, str, str, result=bool)
    @Slot("QVariantList", int, str, str, "QVariantList", result=bool)
    @Slot("QVariantList", int, str, str, "QVariantList", "QVariantList", result=bool)
    def saveSettings(self, roots, depth, agent_command, godot, ignored_health=None, agent_targets=None):
        try:
            self.session.save_settings(roots, depth, agent_command, godot, ignored_health, agent_targets)
        except (OSError, ValueError) as exc:
            self._status = f"Settings were not saved: {exc}"
            self.changed.emit()
            return False
        self._refresh_agent()
        self.changed.emit()
        self.scan()
        return True

    @Slot(result=bool)
    def prepareClose(self):
        if not self._flush_notes():
            return False
        if self._scanning:
            if not self._close_after_scan:
                self.confirmScanCloseRequested.emit()
            return False
        if self._git_controller.busy:
            self._close_after_git = True
            self._status = "Git operation still running; closing waits for its terminal result"
            self.changed.emit()
            return False
        if self.agentActive:
            if not self._close_after_agents:
                self.confirmAgentCloseRequested.emit()
            return False
        if self._post_runs:
            self._close_after_agents = True
            self._status = "Waiting for the Agent target recheck before closing"
            self.changed.emit()
            return False
        self._closing = True
        self._pending_scan = False
        self._detail_gen += 1
        self._agent_timer.stop()
        return True

    @Slot(str)
    def openGit(self, mode):
        if self._close_after_scan or self._closing:
            return
        target = self._selected_target()
        record = target.resolve(self.session.records) if target else None
        if record is None or not projects.is_repository_backed(record) or not self._flush_notes():
            return
        self._git = git_availability.check_git()
        if not self._git.available or self.session.registry_blocked:
            self._status = "Git is required" if not self._git.available else "Recover the registry first"
            self.changed.emit()
            return
        if not self._git_controller.open(mode, record):
            self._status = "Wait for the current Git operation before opening another view"
            self.changed.emit()

    @Slot(str, str, bool)
    def _git_finished(self, outcome, message, mutation):
        if self._closing:
            return
        self._status = f"{outcome}: {message}" if mutation else message
        if outcome != git_operations.SUCCESS:
            self._git = git_availability.check_git()
        self.changed.emit()
        if mutation and not self._close_after_git:
            self.scan()
        if self._close_after_git and not self._git_controller.busy:
            self._close_after_git = False
            self.closeReady.emit()

    def _agent_config(self):
        choices = agents.agent_catalog(self.session.settings)
        valid = [item for item in choices if item["availability"] == agents.AVAILABLE]
        selected = next((item for item in valid if item["agent_id"] == self._selected_agent_id), None)
        return selected or (valid[0] if len(valid) == 1 else None)

    def _agent_target(self):
        target = self._selected_target()
        record = target.resolve(self.session.records) if target else None
        if record is None or not projects.is_repository_backed(record):
            return None
        return {"kind": "repository", "path": record.get("path"),
                "project_id": projects.project_id(record),
                "name": projects.project_display_name(record)}

    def _refresh_agent(self):
        target = self._agent_target()
        catalog = agents.agent_catalog(self.session.settings)
        self._agent_choices = [{**item, "ready": item["availability"] == agents.AVAILABLE,
                                "selected": item["agent_id"] == self._selected_agent_id}
                               for item in catalog]
        config = self._agent_config()
        if config:
            info = agents.agent_readiness(config, target)
        else:
            count = sum(item["ready"] for item in self._agent_choices)
            target_valid = bool(target and agents.validate_target(target["path"])[0])
            info = {"state": agents.READY if count and target_valid else agents.NOT_READY,
                    "reason": "Choose an Agent before starting" if count else "No available Agent. Configure an executable in Settings → Integrations.",
                    "cwd": target["path"] if target else "", "executable": ""}
        self._agent_readiness = {**info, "name": target["name"] if target else "No repository selected",
                                 "agentName": config["display_name"] if config else "Choose Agent",
                                 "command": config["executable"] if config else "",
                                 "choiceCount": sum(item["ready"] for item in self._agent_choices)}

    @Slot()
    def startAgent(self):
        if self.agentActive:
            self._status = "An Agent is already running; stop it before starting another"
            self.changed.emit()
            return
        if self._closing or self._close_after_scan or self._close_after_agents or self.session.registry_blocked or not self._flush_notes():
            return
        target = self._agent_target()
        self._refresh_agent()
        valid = [item for item in self._agent_choices if item["ready"]]
        if len(valid) > 1:
            self._agent_chooser_target = target
            self.agentChooserRequested.emit()
            return
        config = self._agent_config()
        if config is None:
            self._status = self._agent_readiness["reason"]
            self.changed.emit()
            return
        self._launch_agent(config, target)

    @Slot(str)
    def chooseAgent(self, agent_id):
        target = self._agent_target()
        if (target is None or target != self._agent_chooser_target or self.agentActive
                or self._closing or self._close_after_scan or self.session.registry_blocked
                or not self._flush_notes()):
            self._status = "Repository target changed; choose the Agent again"
            self.changed.emit()
            return
        try:
            self.session.select_agent(agent_id)
        except (OSError, ValueError) as exc:
            self._status = str(exc)
            self.changed.emit()
            return
        self._selected_agent_id = agent_id
        self._agent_chooser_target = None
        self._launch_agent(self._agent_config(), target)

    def _launch_agent(self, config, target):
        if config is None:
            self._status = "Agent executable is no longer available; check Settings"
            self._refresh_agent()
            self.changed.emit()
            return
        if target is None:
            self._status = "Select a concrete repository target first"
        elif agents.has_running_run(self._runs, config["agent_id"], target["path"]):
            self._status = "An Agent is already running for this target"
        else:
            try:
                run = agents.new_run(agents.launch_intent(config, target))
                process = agents.start_run(run, agent=config,
                                           target=self._agent_target() or {})
                self._runs.append(run)
                if process is None:
                    self._status = f"Agent failed to start: {run.get('failure', '')}"
                else:
                    self._agent_processes[run["run_id"]] = process
                    self._status = f"Agent started in {target['name']}"
                    self._agent_timer.start()
            except ValueError as exc:
                self._status = str(exc)
        self._refresh_agent()
        self.changed.emit()

    @Slot()
    def stopAgent(self):
        for run in reversed(self._runs):
            if run["process_state"] in (agents.STARTING, agents.RUNNING, agents.STOPPING, agents.UNKNOWN_PROCESS):
                self._stop_run(run)
                break
        self.changed.emit()

    def _stop_run(self, run):
        process = self._agent_processes.get(run["run_id"])
        if process is None:
            run["process_state"] = agents.UNKNOWN_PROCESS
            self._status = "Agent exit could not be confirmed"
            self._close_after_agents = False
            return
        state = agents.cancel_run(run, process)
        if state == agents.UNKNOWN_PROCESS:
            self._close_after_agents = False
            self._status = "Agent exit could not be confirmed; RepoManager stays open"
        else:
            self._status = "Stop requested; waiting for confirmed process exit"
            self._agent_timer.start()

    @Slot()
    def stopAgentsAndClose(self):
        self._close_after_agents = True
        for run in self._runs:
            if run["process_state"] in (agents.STARTING, agents.RUNNING, agents.STOPPING, agents.UNKNOWN_PROCESS):
                self._stop_run(run)
        self._poll_agents()

    def _poll_agents(self):
        for run in self._runs:
            process = self._agent_processes.get(run["run_id"])
            if process is None:
                continue
            state = agents.observe_run(run, process)
            if state in (agents.EXITED, agents.TERMINATED):
                self._agent_processes.pop(run["run_id"], None)
                self._post_runs.add(run["run_id"])
                snapshot = dict(run)

                def recheck(snapshot=snapshot):
                    agents.verify_post_run(snapshot, observe=scanner.collect_metadata_observation)
                    if not self._closing:
                        self.postRunReady.emit(snapshot)

                try:
                    threading.Thread(target=recheck, daemon=True).start()
                except RuntimeError as exc:
                    snapshot["verification"] = agents.UNKNOWN
                    snapshot["verification_evidence"] = [str(exc)]
                    self._accept_post_run(snapshot)
            elif state == agents.UNKNOWN_PROCESS:
                self._close_after_agents = False
                self._status = "Agent process state is unknown; exit is not confirmed"
        if not any(run["process_state"] in (agents.STARTING, agents.RUNNING, agents.STOPPING) for run in self._runs):
            self._agent_timer.stop()
        self._refresh_agent()
        self.changed.emit()
        self._maybe_close_after_agents()

    @Slot(object)
    def _accept_post_run(self, result):
        if self._closing:
            return
        run = next((run for run in self._runs if run["run_id"] == result["run_id"]), None)
        if run is None:
            return
        run.update(result)
        self._post_runs.discard(run["run_id"])
        self._status = f"Agent {run['process_state']} · {run['verification']} (repository observation only)"
        self.changed.emit()
        if not self._close_after_agents:
            self.scan()
        self._maybe_close_after_agents()

    def _maybe_close_after_agents(self):
        if self._close_after_agents and not self.agentActive and not self._post_runs:
            self._close_after_agents = False
            self.closeReady.emit()

    @Slot()
    def checkAgain(self):
        self._git = git_availability.check_git()
        self._status = ("Git is available" if self._git.available
                        else "Git is unavailable in this process")
        self.changed.emit()
        if self._git.available:
            self.scan()

    @Slot()
    def scan(self):
        if self._closing or self._close_after_scan or self.session.registry_blocked:
            return
        if self._scanning:
            self._pending_scan = True
            self._status = "Rescan queued"
            self.changed.emit()
            return
        self._git = git_availability.check_git()
        if not self._git.available:
            self._status = "Git is required to scan repositories"
            self.changed.emit()
            self._finish_scan_close()
            return
        self._scanning = True
        control = ScanControl()
        self._scan_control = control
        self._status = "Scanning configured folders…"
        self._scan_timer.start()
        self.changed.emit()

        def work():
            try:
                outcome = self.session.scan(control)
                self.scanReady.emit(outcome)
            except ScanCancelled as exc:
                self.scanFailed.emit(str(exc))
            except Exception as exc:
                self.scanFailed.emit(str(exc))

        self._scan_thread = threading.Thread(target=work, name="RepoManagerScan", daemon=False)
        try:
            self._scan_thread.start()
        except RuntimeError as exc:
            self._scan_failed(str(exc))

    @Slot(object)
    def _accept_scan(self, outcome):
        if self._closing:
            return
        if self._scan_thread and self._scan_thread.is_alive():
            QTimer.singleShot(25, lambda: self._accept_scan(outcome))
            return
        if self._scan_control and self._scan_control.cancelled.is_set():
            self._scan_failed("Scan cancelled; inventory unchanged")
            return
        self._scan_timer.stop()
        self._scan_control = None
        self._scan_thread = None
        self._git = git_availability.check_git()
        self._scanning = False
        if not self._git.available:
            self._status = "Git became unavailable; scan results were not saved"
            self.changed.emit()
            self._finish_scan_close()
            return
        try:
            warning = self.session.accept_scan(outcome)
        except OSError as exc:
            self._status = f"Scan could not be saved: {exc}"
            self.changed.emit()
            self._finish_scan_close()
            return
        self._problem_count = len(outcome.problems)
        self._moves = [item for item in outcome.problems if item.get("kind") == "move"]
        self._problems = [item for item in outcome.problems if item.get("kind") != "move"]
        self._status = warning or ("Scan complete" if not outcome.problems else
                        f"Scan complete · {len(outcome.problems)} issues")
        self._refresh_rows()
        if self._close_after_scan:
            self._finish_scan_close()
            return
        self._request_detail()
        if self._pending_scan:
            self._pending_scan = False
            QTimer.singleShot(0, self.scan)

    @Slot(str)
    def _scan_failed(self, message):
        if self._closing:
            return
        if self._scan_thread and self._scan_thread.is_alive():
            QTimer.singleShot(25, lambda: self._scan_failed(message))
            return
        cancelled = bool(self._scan_control and self._scan_control.cancelled.is_set())
        self._scan_timer.stop()
        self._scan_control = None
        self._scan_thread = None
        self._scanning = False
        self._pending_scan = False
        self._status = "Scan cancelled; inventory unchanged" if cancelled else f"Scan failed: {message}"
        self.changed.emit()
        self._finish_scan_close()

    def _finish_scan_close(self):
        if self._close_after_scan and not self._scanning:
            self._close_after_scan = False
            self.closeReady.emit()

    @Slot()
    def cancelScan(self):
        if not self._scanning or self._scan_control is None:
            return
        self._pending_scan = False
        self._scan_control.cancel()
        self._status = "Cancelling scan… waiting for the current filesystem / Git operation; inventory will not be saved"
        self.changed.emit()

    @Slot()
    def cancelScanAndClose(self):
        if not self._flush_notes():
            return
        self._close_after_scan = True
        self.cancelScan()
        self._finish_scan_close()

    @Slot()
    def openGitInstall(self):
        if not QDesktopServices.openUrl(QUrl("https://git-scm.com/install/windows")):
            self._status = "The browser could not be opened"
            self.changed.emit()

    @Slot()
    def copySelectedPath(self):
        if self._selected.get("path"):
            QGuiApplication.clipboard().setText(self._selected["path"])

    @Slot(str)
    def openFolder(self, action):
        target = self._selected_target()
        if target is None:
            return
        try:
            self.session.open_folder(target, action)
            self._status = f"Opened {action}"
        except (OSError, ValueError) as exc:
            self._status = f"Launch failed: {exc}"
        self.changed.emit()

    @Slot(int)
    def runLauncher(self, index):
        if self._command_target is None or not 0 <= index < len(self._commands):
            return
        try:
            self.session.run_launcher(self._command_target, self._commands[index])
            self._status = "Launcher started"
        except (OSError, ValueError) as exc:
            self._status = f"Launch failed: {exc}"
        self.changed.emit()

    @Slot()
    def requestQuickRun(self):
        if self._closing or self._detail_loading or self._command_target is None:
            return
        commands = [command for command in self._commands if command.get("healthy", True)]
        if len(commands) == 1:
            self.runLauncher(self._commands.index(commands[0]))
        elif commands:
            self._quick_run_target = self._command_target
            self._quick_run_commands = list(commands)
            self.changed.emit()
            self.quickRunChooserRequested.emit()

    @Slot(int)
    def runQuickLauncher(self, index):
        if (self._closing or self._quick_run_target is None
                or self._quick_run_target != self._selected_target()
                or self._quick_run_target != self._command_target
                or not 0 <= index < len(self._quick_run_commands)):
            self._status = "Launcher selection changed; choose it again"
            self.changed.emit()
            return
        approved = self._quick_run_commands[index]
        if approved not in self._commands:
            self._status = "Launcher changed; choose it again"
            self.changed.emit()
            return
        self.runLauncher(self._commands.index(approved))

    @Slot(QUrl, result=str)
    def localPath(self, url):
        return url.toLocalFile()

    @Slot(str, str, str, bool, str, result=bool)
    def sendFeedback(self, category, title, message, runtime, action):
        try:
            report = feedback.make_report(category, title, message,
                                          include_runtime=runtime, qt_version=qVersion())
            if action == "save":
                destination = feedback.save_report(report)
                self._feedback_status = f"Saved locally: {destination}"
            elif action == "copy":
                QGuiApplication.clipboard().setText(feedback.markdown(report))
                self._feedback_status = "Feedback copied"
            elif action == "issue":
                if not QDesktopServices.openUrl(QUrl(feedback.issue_url(report))):
                    raise OSError("The browser could not be opened; save or copy the report instead")
                self._feedback_status = "Issue draft opened. Review and submit it in GitHub."
            else:
                raise ValueError("Unknown feedback action")
        except (OSError, ValueError) as exc:
            self._feedback_status = str(exc)
            self.changed.emit()
            return False
        self.changed.emit()
        return True

    @Property("QVariantList", constant=True)
    def helpTopics(self):
        return [{"title": title, "text": body} for title, body in HELP_TOPICS.values()]

    @Slot()
    def openOfficialReleases(self):
        if not QDesktopServices.openUrl(QUrl("https://github.com/IcyShadow5/RepoManager/releases")):
            self._status = "The official release page could not be opened"
            self.changed.emit()

    @Property(str, notify=changed)
    def themeName(self):
        return self._theme

    @Slot()
    def toggleTheme(self):
        name = "light" if self._theme == "dark" else "dark"
        try:
            updated = {**self.session.settings, "theme": name, "qt_theme": name}
            store.save_settings(updated)
            self.session.settings = updated
            self._theme = name
        except OSError as exc:
            self._status = f"Theme could not be saved: {exc}"
        self.changed.emit()

    @Property("QVariantList", notify=changed)
    def customLaunchers(self):
        target = self._selected_target()
        record = target.resolve(self.session.records) if target else None
        return copy.deepcopy(record.get("custom_launchers", [])) if record else []

    @Slot(int, result="QVariantMap")
    def editLauncher(self, index):
        self._launcher_target = self._selected_target()
        items = self.customLaunchers
        return items[index] if 0 <= index < len(items) else {"cwd": self._selected.get("path", "")}

    @Slot("QVariantMap", bool, result=bool)
    def saveLauncher(self, value, remove):
        try:
            if self._launcher_target is None:
                raise ValueError("Select a Project first")
            self.session.save_launcher(self._launcher_target, value, remove=remove)
            self._status = "Custom launcher removed" if remove else "Custom launcher saved"
            self._refresh_rows()
            self._request_detail()
            return True
        except (OSError, ValueError) as exc:
            self._status = f"Launcher was not saved: {exc}"
            self.changed.emit()
            return False

    @Slot(str, result=bool)
    def prepareExport(self, kind):
        if not self._flush_notes():
            return False
        self._export_target = self._selected_target()
        self._export_kind = kind
        return self._export_target is not None

    @Slot(QUrl)
    def exportProject(self, url):
        try:
            if self._export_target is None or not url.isLocalFile():
                raise ValueError("Choose a local export destination")
            self.session.export(self._export_target, url.toLocalFile(), self._export_kind)
            self._status = f"Export written: {url.toLocalFile()}"
        except (OSError, ValueError) as exc:
            self._status = f"Export failed: {exc}"
        self.changed.emit()

    @Property("QVariantList", notify=changed)
    def ignoredProjects(self):
        return [{"id": projects.project_id(item), "name": projects.project_display_name(item),
                 "path": projects.project_folder(item) or "", "status": item.get("status", "idea")}
                for item in self.session.records if projects.is_ignored(item)]

    @Slot(str)
    def restoreProject(self, identity):
        record = next((item for item in self.session.records if projects.project_id(item) == identity and projects.is_ignored(item)), None)
        if record is None:
            return
        try:
            self.session.set_ignored(project_actions.Target.capture(record), False)
            self._status = "Project restored"
            self._refresh_rows()
        except (OSError, ValueError) as exc:
            self._status = f"Restore failed: {exc}"
            self.changed.emit()

    @Slot(str)
    def requestAction(self, action):
        self._action_target = self._selected_target()
        self._action = action
        if self._action_target is None:
            return
        path = self._selected.get("path", "")
        if action == "ignore":
            self.actionConfirmationRequested.emit("Remove from RepoManager", f"Ignore {self._selected.get('projectName')} in future scans?\n\n{path}\n\nRepository files stay in place. Restore this Project later in Settings.")
        elif action == "stub":
            self.actionConfirmationRequested.emit("Generate starter run.bat", f"Create a starter template in:\n{path}\\run.bat\n\nThis makes the repository dirty/untracked. An existing run.bat will never be overwritten.")

    @Slot()
    def executeAction(self):
        if self._action == "keep_both":
            self._action = ""
            approved, self._approved_move = self._approved_move, None
            if approved in self._moves:
                self.keepBoth(self._moves.index(approved))
            else:
                self.noticeRequested.emit("Pairing changed", "The approved pairing is no longer current. Review the inventory again.")
            return
        if self._action == "move":
            self._action = ""
            self.executeMove()
            return
        action, target = self._action, self._action_target
        self._action, self._action_target = "", None
        if target is None or not self._flush_notes():
            return
        try:
            if action == "ignore":
                if self._git_controller.busy or self.agentActive:
                    raise ValueError("Wait for Git / stop the Agent before ignoring its target")
                self.session.set_ignored(target, True)
                self._status = "Project ignored; restore it in Settings"
                self._refresh_rows()
            elif action == "stub":
                created = self.session.generate_stub(target)
                self._status = "Created starter run.bat" if created else "run.bat already exists; nothing overwritten"
                self._request_detail()
                self.scan()
        except (OSError, ValueError) as exc:
            self._status = f"Action failed: {exc}"
        self.changed.emit()

    @Slot(str)
    def copyTechnical(self, field):
        if field in {"branch", "head", "remote"}:
            QGuiApplication.clipboard().setText(str(self._selected.get(field) or ""))
            self._status = f"Copied {field}"
            self.changed.emit()

    @Slot()
    def openRemote(self):
        target = self._selected_target()
        record = target.resolve(self.session.records) if target else None
        remote = record.get("remote") if record else None
        normalized = scanner.normalize_remote(remote) if isinstance(remote, str) else None
        if normalized is None and isinstance(remote, str):
            normalized = scanner.normalize_remote("https://" + remote.strip())
        if not normalized or not QDesktopServices.openUrl(QUrl("https://" + normalized)):
            self._status = "No recognized remote website could be opened"
            self.changed.emit()

    @Property(str, notify=changed)
    def recoveryText(self):
        report = self.session.report
        settings = store.settings_recovery_report()
        lines = []
        if self.session.registry_blocked:
            lines.append("Registry recovery required. Scanning and registry writes are paused. Close programs locking repos.json or restore a valid registry / backup, then use Retry registry.")
        elif report.get("status") == "recovered":
            lines.append(f"Project registry recovered from {report.get('source')}. Review the recovered inventory.")
        if report.get("quarantined"):
            lines.append(f"Damaged registry preserved: {report['quarantined']}")
        lines.extend(str(reason) for reason in report.get("reasons", []))
        if report.get("persistence_error"):
            lines.append(str(report["persistence_error"]))
        if settings.get("status") == "recovered":
            lines.append("Settings were malformed; safe defaults are loaded. Review scan folders and integrations.")
            lines.append("Damaged source remains untouched; preservation failed." if settings.get("preservation_failed") else f"Original preserved: {settings.get('quarantined')}")
        return "\n\n".join(lines)

    @Slot()
    def retryRegistry(self):
        if not self._flush_notes():
            return
        try:
            ready = self.session.retry_registry()
            self._status = "Registry reloaded" if ready else "Registry remains blocked"
            self._refresh_rows()
            self._load_selection()
            if ready:
                self.scan()
        except (OSError, ValueError) as exc:
            self._status = f"Registry reload failed: {exc}"
        self.changed.emit()

    @Slot()
    def showTechnicalEvidence(self):
        target = self._selected_target()
        if target is None:
            return
        try:
            record = self.session.resolve(target)
            understanding = projects.inspect_project_understanding(record)
            lines = [f"Project ID: {projects.project_id(record)}", f"Classification: {projects.classification_summary(record)}", "\nStack (manifest evidence only):"]
            stack = understanding["stack"]
            for key in ("languages", "frameworks", "package_managers", "runtimes", "manifests", "build_systems"):
                lines.append(f"{key}: {', '.join(getattr(stack, key)) or '(none observed)'}")
            lines.extend(f"{item.source} · {item.target} · {item.observation}" for item in stack.evidence)
            lines.append("\nDocumentation:")
            for item in understanding["documentation"]:
                lines.append(f"{item.key}: {item.status} · {item.freshness}\n" + "\n".join(f"  {e.source} · {e.target} · {e.observation}" for e in item.evidence))
            self.noticeRequested.emit("Technical project evidence", "\n".join(lines))
        except (OSError, ValueError) as exc:
            self.noticeRequested.emit("Technical evidence unavailable", str(exc))

    @Property("QVariantMap", notify=changed)
    def providerData(self):
        target = self._selected_target()
        record = target.resolve(self.session.records) if target else None
        remote = record.get("remote") if record else None
        correspondence = providers.provider_correspondence(remote)
        matches = providers.provider_correspondences(record.get("remotes", [])) if record else []
        return {"local": correspondence or {}, "others": list(matches),
                "online": self._provider, "busy": self._provider_busy}

    @Slot()
    def checkProvider(self):
        target = self._selected_target()
        record = target.resolve(self.session.records) if target else None
        remote = record.get("remote") if record else None
        if self._provider_busy or not remote:
            return
        self._provider_generation += 1
        generation = self._provider_generation
        self._provider_busy = True
        self.changed.emit()
        def work():
            try:
                result = providers.GitHubAdapter().observe(remote)
            except Exception as exc:
                result = providers.ProviderObservation("github", providers.UNKNOWN,
                    providers.UNKNOWN_FRESHNESS, scanner.utc_now_iso(), error=git_operations.redact(exc))
            if not self._closing:
                self.providerReady.emit(target, remote, generation, result)
        try:
            threading.Thread(target=work, daemon=True).start()
        except RuntimeError as exc:
            self._provider_busy = False
            self._status = f"Provider observation unavailable: {exc}"
            self.changed.emit()

    @Slot(object, str, int, object)
    def _accept_provider(self, target, remote, generation, result):
        current = self._selected_target()
        record = target.resolve(self.session.records) if target else None
        if self._closing or generation != self._provider_generation or current != target or record is None or record.get("remote") != remote:
            return
        self._provider_busy = False
        self._provider = {**asdict(result), "disagreements": providers.compare_local_provider(record, result)}
        self.changed.emit()

    @Property("QVariantList", notify=changed)
    def moveSuggestions(self):
        return copy.deepcopy(self._moves)

    @Property(str, notify=changed)
    def scanIssues(self):
        return "\n\n".join(f"{item.get('path', '')}\n{item.get('reason', '')}" for item in self._problems) or "No scan issues"

    @Slot(int, int)
    def previewMove(self, group, candidate):
        self._approved_move = None
        try:
            if self.scanning or self.agentActive or self._git_controller.busy:
                raise ValueError("Wait for the scan / Git and stop Agents before resolving locations")
            if not 0 <= group < len(self._moves):
                raise ValueError("Suggestion no longer exists")
            suggestion = self._moves[group]
            approved = relocation.approve_candidate(suggestion, candidate, self.session.records) if suggestion.get("category") == "ambiguous" else copy.deepcopy(suggestion)
            self._approved_move = approved
            self.actionConfirmationRequested.emit("Update Project location", f"Previous: {approved.get('old_path')}\nCandidate: {approved.get('new_path')}\n\n" + "\n".join(approved.get("evidence", [])) + "\n\nThis updates the registry association and preserves Project identity / notes. No repository files are moved. Curated targets are protected.")
            self._action = "move"
        except ValueError as exc:
            self.noticeRequested.emit("Location update unavailable", str(exc))

    @Slot()
    def executeMove(self):
        approved, self._approved_move = self._approved_move, None
        if approved is None or not self._flush_notes():
            return
        try:
            if self.scanning or self.agentActive or self._git_controller.busy:
                raise ValueError("Current activity changed; preview this move again")
            outcome = self.session.apply_move(approved)
            self._status = outcome.status + ": " + "\n".join(outcome.evidence)
            self.noticeRequested.emit("Project location result", self._status)
            if outcome.status in {relocation.MOVE_OK, relocation.MOVE_COLLISION}:
                self._moves = relocation.retire_accepted_pair(self._moves, approved)
                self._refresh_rows()
                self.scan()
            elif outcome.status == relocation.MOVE_ROLLBACK_FAILED:
                self.session.report = {**self.session.report, "write_blocked": True,
                    "reasons": list(outcome.evidence)}
        except (OSError, ValueError) as exc:
            self._status = f"Location update failed: {exc}"
        self.changed.emit()

    @Slot(int)
    def keepBoth(self, group):
        if not 0 <= group < len(self._moves) or not self._flush_notes():
            return
        try:
            if self.scanning or self.agentActive or self._git_controller.busy:
                raise ValueError("Wait for current activity before resolving locations")
            self.session.keep_both(self._moves[group])
            del self._moves[group]
            self._status = "Kept both Projects; exact pairings suppressed"
            self._refresh_rows()
        except (OSError, ValueError) as exc:
            self._status = str(exc)
            self.changed.emit()

    @Slot()
    def launchPrimary(self):
        candidate = launchers.select_primary_command(self._commands)
        if candidate is None:
            self._status = "No available primary launcher"
            self.changed.emit()
            return
        self.runLauncher(self._commands.index(candidate))

    @Property("QVariantMap", notify=changed)
    def columnWidths(self):
        return dict(self._columns)

    @Property(str, notify=changed)
    def gitDetails(self):
        return git_operations.redact(self._git.detail or "Git was not found on the current process PATH.")

    @Slot(str)
    def copyText(self, text):
        QGuiApplication.clipboard().setText(text)

    @Slot(str, int, bool)
    def resizeColumn(self, column, value, persist):
        if column not in QT_COLUMNS:
            return
        self._columns[column] = max(QT_COLUMNS[column][1], min(QT_COLUMNS[column][2], value))
        if persist:
            try:
                updated = {**self.session.settings, "qt_column_widths": dict(self._columns)}
                store.save_settings(updated)
                self.session.settings = updated
            except OSError as exc:
                self._status = f"Column widths were not saved: {exc}"
        self.changed.emit()

    @Slot()
    def resetColumns(self):
        self._columns = {key: value[0] for key, value in QT_COLUMNS.items()}
        self.resizeColumn("name", self._columns["name"], True)

    @Slot(int)
    def requestKeepBoth(self, group):
        if 0 <= group < len(self._moves):
            self._approved_move = copy.deepcopy(self._moves[group])
            self._action = "keep_both"
            self.actionConfirmationRequested.emit("Keep both Projects", "Durably suppress these exact pairings?\n\n" + str(self._approved_move.get("old_paths") or self._approved_move.get("old_path")) + "\n" + str(self._approved_move.get("new_paths") or self._approved_move.get("new_path")) + "\n\nNo files are deleted.")
