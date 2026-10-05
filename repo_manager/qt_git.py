"""Qt dialog state around the existing, independently authorized Git service."""
import re
import threading

from PySide6.QtCore import QObject, Property, Signal, Slot
from PySide6.QtGui import QGuiApplication

from . import git_operations as git, git_targets, projects


def display_text(value):
    return str(value).encode("utf-8", "backslashreplace").decode("utf-8")


class GitController(QObject):
    changed = Signal()
    opened = Signal()
    completed = Signal(int, str, object)
    finished = Signal(str, str, bool)
    confirmationRequested = Signal()

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._target = None
        self._project = {}
        self._mode = "changes"
        self._busy = False
        self._status = ""
        self._content = ""
        self._state = None
        self._selected = set()
        self._view = "unstaged"
        self._limit = 100
        self._remote_names = []
        self._remote = ""
        self._destination = ""
        self._set_upstream = False
        self._approval = None
        self._confirmation = ""
        self._confirmed_operation = None
        self._guard = git_targets.GitMutationGuard()
        self._generation = 0
        self._callback = None
        self._mutation = False
        self._last_result = ""
        self.completed.connect(self._complete)

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def mode(self):
        return self._mode

    @Property(str, notify=changed)
    def title(self):
        return self._mode.title() + " — " + projects.project_display_name(self._project)

    @Property(str, notify=changed)
    def targetPath(self):
        return display_text(self._project.get("path") or "")

    @Property(str, notify=changed)
    def statusText(self):
        return self._status

    @Property(str, notify=changed)
    def content(self):
        return self._content

    @Property(str, notify=changed)
    def stateSummary(self):
        if self._state is None:
            return ""
        return (f"Branch: {self._state.branch or 'Detached HEAD'} · "
                f"{sum(change.staged for change in self._state.changes)} staged · "
                f"{sum(change.unstaged for change in self._state.changes)} unstaged / untracked"
                + (" · In-progress operation" if self._state.in_progress else ""))

    @Property("QVariantList", notify=changed)
    def files(self):
        if self._state is None:
            return []
        return [{"index": index, "label": display_text(change.label),
                 "selected": index in self._selected}
                for index, change in enumerate(self._state.changes)
                if (change.staged if self._view == "staged" else change.unstaged)]

    @Property(str, notify=changed)
    def view(self):
        return self._view

    @Property("QVariantList", notify=changed)
    def remoteNames(self):
        return self._remote_names

    @Property(str, notify=changed)
    def remote(self):
        return self._remote

    @Property(str, notify=changed)
    def destination(self):
        return self._destination

    @Property(bool, notify=changed)
    def setUpstream(self):
        return self._set_upstream

    @Property(bool, notify=changed)
    def hasNetworkPreview(self):
        return self._approval is not None

    @Property(str, notify=changed)
    def confirmationText(self):
        return self._confirmation

    @Property(str, notify=changed)
    def lastResult(self):
        return self._last_result

    def open(self, mode, project):
        if self._busy or mode not in {"changes", "commit", "history", "remotes", "fetch", "push", "pull"}:
            return False
        self._target = git_targets.git_target_snapshot(project)
        self._project = dict(project)
        self._mode = mode
        self._state = None
        self._selected.clear()
        self._view = "unstaged"
        self._content = ""
        self._approval = None
        self._confirmed_operation = None
        self._confirmation = ""
        self._remote_names = []
        self._remote = ""
        self._destination = ""
        self._set_upstream = False
        self._limit = 100
        self._last_result = ""
        self.changed.emit()
        self.opened.emit()
        self.refresh()
        return True

    def _authorized(self):
        return (not self.session.registry_blocked
                and git_targets.git_mutation_target_is_authorized(self.session.records, self._target))

    def _request(self, operation, callback, *, mutation=False):
        if self._busy or self._target is None:
            return
        self._generation += 1
        generation = self._generation
        target = self._target
        self._busy = True
        self._mutation = mutation
        self._callback = callback
        self._confirmed_operation = None
        self._status = "Working…" if mutation else "Loading…"
        self.changed.emit()

        def work():
            acquired = False
            try:
                authorize = lambda: (not self.session.registry_blocked and
                    git_targets.git_mutation_target_is_authorized(self.session.records, target))
                if not authorize():
                    raise git.GitError("Project association or repository identity changed", outcome=git.CANCELLED)
                if mutation:
                    acquired = self._guard.acquire(target["repository_marker"])
                    if not acquired:
                        raise git.GitError("Another Git mutation is active", outcome=git.CANCELLED)
                result = operation(git.Repository(target["path"], authorize=authorize))
                outcome, payload = ((result.outcome, result.output) if isinstance(result, git.Result)
                                    else (git.SUCCESS, result))
            except git.GitError as exc:
                outcome, payload = exc.outcome, str(exc)
            except Exception as exc:
                outcome, payload = git.FAILED, f"Git operation failed: {git.redact(exc)}"
            finally:
                if acquired:
                    self._guard.release(target["repository_marker"])
            self.completed.emit(generation, outcome, payload)

        try:
            threading.Thread(target=work, daemon=True).start()
        except RuntimeError as exc:
            self._complete(generation, git.FAILED, str(exc))

    @Slot(int, str, object)
    def _complete(self, generation, outcome, payload):
        if generation != self._generation:
            return
        self._busy = False
        callback = self._callback
        mutation = self._mutation
        if mutation or outcome != git.SUCCESS:
            self._last_result = f"Outcome: {outcome}\n\n{display_text(payload)}"
        self._callback = None
        if not self._authorized():
            self._status = "Target association changed; inspect the original repository directly"
            if mutation:
                self._status += "\n" + display_text(payload)
            self.changed.emit()
            self.finished.emit(git.CANCELLED if not mutation else outcome, self._status, mutation)
            return
        if outcome == git.SUCCESS:
            callback(payload)
        else:
            self._status = display_text(payload)
        self.changed.emit()
        message = display_text(payload) if mutation else self._status
        if mutation and self._mode in {"changes", "commit"}:
            # Keep the mutation outcome visible while re-observing changed state.
            message = self._status
            self._request(lambda repository: repository.state(),
                          lambda state: (self._loaded_state(state), self._set_status(message)))
        self.finished.emit(outcome, message, mutation)

    def _set_status(self, message):
        self._status = message

    @Slot()
    def refresh(self):
        if self._mode in {"changes", "commit"}:
            self._request(lambda repository: repository.state(), self._loaded_state)
        elif self._mode == "history":
            limit = self._limit
            self._request(lambda repository: repository.history(limit=limit),
                          lambda text: self._loaded_text(text, f"Up to {limit} commits; enter an ID to inspect"))
        elif self._mode == "remotes":
            def collect(repository):
                state = repository.state()
                upstream = repository.upstream(state.branch)
                return (f"Branch: {state.branch or 'Detached HEAD'}\nUpstream: {upstream or '(none)'}\n\n"
                        + "\n\n".join(remote.describe() for remote in repository.remotes()))
            self._request(collect, lambda text: self._loaded_text(text, "Read-only; URL credentials/query/fragment hidden"))
        else:
            def collect(repository):
                state = repository.state()
                return state, repository.remotes(), repository.upstream(state.branch)
            self._request(collect, self._loaded_network)

    def _loaded_state(self, state):
        self._state = state
        self._selected.clear()
        self._content = "Select a file to preview differences. Binary and large output is bounded.\nConflicts and submodule mutations must be handled externally."
        self._status = "Snapshot loaded; refresh if another tool edits the repository"

    def _loaded_text(self, text, status):
        self._content = display_text(text)
        self._status = status

    @Slot(str)
    def setView(self, value):
        if not self._busy and value in {"staged", "unstaged"}:
            self._view = value
            self._selected.clear()
            self._content = "Select a file to preview differences."
            self.changed.emit()

    @Slot(int)
    def toggleFile(self, index):
        if self._busy or self._state is None or not 0 <= index < len(self._state.changes):
            return
        if index in self._selected:
            self._selected.remove(index)
        else:
            self._selected.add(index)
        self.changed.emit()

    @Slot(int)
    def previewFile(self, index):
        if self._state is None or not 0 <= index < len(self._state.changes):
            return
        change, staged = self._state.changes[index], self._view == "staged"
        self._request(lambda repository: repository.diff(change, staged=staged),
                      lambda text: self._loaded_text(text, "Diff loaded"))

    @Slot(bool)
    def stageSelected(self, unstage):
        if self._state is None or not self._selected:
            self._status = "Select files first"
            self.changed.emit()
            return
        state = self._state
        selected = tuple(state.changes[index] for index in sorted(self._selected))
        self._request(lambda repository: repository.stage(state, selected, unstage=unstage),
                      lambda text: self._loaded_text(text, text), mutation=True)

    @Slot(str, bool)
    def previewCommit(self, message, stage_all):
        message = message.strip()
        if self._state is None or self._busy:
            return
        selected = tuple(change for change in self._state.changes if stage_all or change.staged)
        if not message or not selected:
            self._status = "Enter a message and stage files, or explicitly choose Stage all"
            self.changed.emit()
            return
        state = self._state
        self._confirmation = (f"Branch: {state.branch or 'Detached HEAD'}\n"
            f"Mode: {'Stage all current changes and commit' if stage_all else 'Staged only'}\n"
            f"Message: {message}\n\n" + "\n".join(display_text(change.label) for change in selected[:20])
            + "\n\nThis creates a local commit. Nothing will be pushed."
            + ("\nStage all uses git add -A at execution; external editors are not locked out." if stage_all else ""))
        self._confirmed_operation = lambda repository: repository.commit(state, message, stage_all=stage_all)
        self.changed.emit()
        self.confirmationRequested.emit()

    def _loaded_network(self, data):
        state, remotes, upstream = data
        self._state = state
        self._remote_names = [remote.name for remote in remotes]
        preferred = upstream[0] if upstream else "origin"
        self._remote = preferred if preferred in self._remote_names else next(iter(self._remote_names), "")
        self._destination = upstream[1] if upstream and self._mode in {"push", "pull"} else ""
        self._approval = None
        self._content = ("Preview the destination before confirming.\nPush never forces or automatically pulls.\n"
                         "Pull is fast-forward-only and requires a clean checkout.\nFetch never prunes or changes checkout files.")
        self._status = "Ready to preview" if remotes else "No remotes configured"

    @Slot(str, str, bool)
    def setNetwork(self, remote, destination, upstream):
        if self._busy:
            return
        self._remote, self._destination, self._set_upstream = remote, destination.strip(), upstream
        self._approval = None
        self._confirmed_operation = None
        self._status = "Preview the selected destination before confirming"
        self.changed.emit()

    @Slot()
    def previewNetwork(self):
        mode, remote, destination, upstream = self._mode, self._remote, self._destination or None, self._set_upstream
        def collect(repository):
            approval = repository.approve_network(mode, remote, destination, set_upstream=upstream)
            outgoing = "Outgoing commits: unknown (no tracked target evidence)"
            if mode == "push" and approval.upstream:
                rc, out, _ = repository.run("rev-list", "--count", "@{upstream}..HEAD", allowed=(0, 128))
                if rc == 0:
                    outgoing = f"Outgoing commits relative to cached upstream: {out.strip()} (no network probe)"
            return approval, outgoing
        def loaded(data):
            approval, outgoing = data
            self._approval = approval
            self._destination = approval.destination or ""
            self._content = (approval.remote.describe() + f"\n\nAction: {mode}\nBranch: {approval.branch or '(none)'}\n"
                f"Destination: {approval.destination or '(configured fetch refspecs)'}\nSet upstream: {approval.set_upstream}\n"
                f"{outgoing}\n\nRemote and HEAD are rechecked at execution. No automatic retries.")
            self._status = "Destination previewed; explicit confirmation required"
        self._request(collect, loaded)

    @Slot()
    def confirmNetwork(self):
        approval = self._approval
        if self._busy or approval is None:
            return
        if (self._remote != approval.remote.name or self._set_upstream != approval.set_upstream
                or (self._mode in {"push", "pull"} and self._destination != approval.destination)):
            self._approval = None
            self.changed.emit()
            return
        self._confirmation = f"{self._mode.title()} using {approval.remote.name}\nDestination: {approval.destination or '(fetch refspecs)'}\n\n{approval.remote.describe()}\n\nProceed?"
        self._confirmed_operation = lambda repository: repository.network(approval)
        self.changed.emit()
        self.confirmationRequested.emit()

    @Slot()
    def executeConfirmed(self):
        operation = self._confirmed_operation
        self._confirmed_operation = None
        self._approval = None
        if operation is not None:
            self._request(operation, lambda text: self._loaded_text(text, text), mutation=True)

    @Slot()
    def cancelConfirmation(self):
        self._confirmed_operation = None

    @Slot()
    def moreHistory(self):
        if not self._busy:
            self._limit = min(self._limit + 100, 1000)
            self.refresh()

    @Slot(str)
    def commitDetails(self, identity):
        self._request(lambda repository: repository.commit_details(identity.strip()),
                      lambda text: self._loaded_text(text, "Commit detail (bounded output)"))

    @Slot(str)
    def copyCommitId(self, identity):
        if re.fullmatch(r"[0-9a-fA-F]{40,64}", identity.strip()):
            QGuiApplication.clipboard().setText(identity.strip())
            self._status = "Commit ID copied"
            self.changed.emit()
