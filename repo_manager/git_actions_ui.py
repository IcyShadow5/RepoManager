"""Target-bound everyday actions and Git worker ownership for the Tk app."""
import logging
import os
import shutil
import threading
import tkinter as tk
from tkinter import ttk

from . import agents, git_dialogs, git_operations as git, project_actions as actions, projects, theme

log = logging.getLogger("repomanager")


class GitActionsMixin:
    def _capture_git_target(self, project):
        from .main import git_target_snapshot
        return git_target_snapshot(project)

    def _git_service_async(self, target, operation, done, *, mutation=False):
        from .main import GitMutationGuard, git_mutation_target_is_authorized
        def delivered(outcome, data):
            if outcome == git.SUCCESS and not git_mutation_target_is_authorized(self.projects, target):
                if mutation:
                    data = f"{data}\nCompleted for the original target, which is no longer associated; inspect it directly."
                else:
                    outcome, data = git.CANCELLED, "Project association changed while the result was loading; inspect the original repository"
            try:
                done(outcome, data)
            finally:
                current = self.__dict__.get("_current")
                if current is not None:
                    self._render_git_summary(current)
        complete = self._track_git_worker(delivered)
        guard = self.__dict__.setdefault("_git_mutation_guard", GitMutationGuard())
        key = target.get("repository_marker")

        def work():
            acquired = False
            try:
                if mutation:
                    acquired = key is not None and guard.acquire(key)
                    if not acquired:
                        raise git.GitError("Another Git mutation is active or identity is unavailable", outcome=git.CANCELLED)
                authorize = lambda: (not self._registry_blocked()
                                     and git_mutation_target_is_authorized(self.projects, target))
                repository = git.Repository(target["path"], authorize=authorize)
                if not authorize():
                    raise git.GitError("Project association or repository identity changed", outcome=git.CANCELLED)
                result = operation(repository)
                outcome, data = (result.outcome, result.output) if isinstance(result, git.Result) else (git.SUCCESS, result)
            except git.GitError as exc:
                outcome, data = exc.outcome, str(exc)
            except Exception:
                log.exception("Git service operation failed")
                outcome, data = git.FAILED, "Internal Git operation error; inspect repo_manager.log"
            finally:
                if acquired:
                    guard.release(key)
            self._scan_queue.put(("done", (complete, outcome, data)))

        try:
            threading.Thread(target=work, daemon=True).start()
        except (OSError, RuntimeError):
            self._scan_queue.put(("done", (complete, git.FAILED, "Git worker could not start")))

    def _action_capability(self, action, project):
        if action == "agent":
            readiness = agents.agent_readiness(self.agent, project)
            if readiness["state"] != agents.READY:
                return actions.Capability(False, readiness["reason"])
        return actions.capability(
            action, project, folder_available=bool(project and os.path.isdir(projects.project_folder(project) or "")),
            busy=bool(self.__dict__.get("_git_workers")),
            registry_blocked=self._registry_blocked(),
            vscode_available=bool(shutil.which("code") or
                os.path.isfile(os.path.expanduser("~/AppData/Local/Programs/Microsoft VS Code/bin/code.cmd"))),
            agent_available=True if action == "agent" else bool(shutil.which("wt.exe")))

    def _invoke_target_action(self, target, method, *args):
        project = target.resolve(self.projects)
        if project is None:
            self._status.set("Project association changed; action cancelled", important=True)
            return
        capability = self._action_capability(actions.METHOD_ACTIONS.get(method, "status"), project)
        if not capability.enabled:
            self._status.set(capability.reason, important=True)
            return
        previous = self.__dict__.get("_action_target")
        self._action_target = target
        try:
            # Existing detail actions intentionally operate on _current. Load the
            # approved detail record before invoking those entry points.
            if method in {"_set_status", "_toggle_pinned", "_toggle_working_on_this",
                          "_focus_project_notes", "_open_project_technical_details",
                          "_export_current_project", "_export_current_report"}:
                from .main import project_row_id
                self._show_detail(project_row_id(project))
            callback = getattr(self, method)
            return callback(*args)
        finally:
            self._action_target = previous

    def _action_menu(self, layout, project):
        menu = tk.Menu(self, tearoff=0, font=("", 10))
        theme.style_tk_widget(menu, self.pal, "menu")
        target = actions.Target.capture(project) if project else None
        for item in layout:
            if item == "-sep-":
                menu.add_separator()
                continue
            kind, label, method = item
            if kind == "cascade":
                children = {"repository": actions.REPOSITORY_MENU,
                            "project": actions.PROJECT_MENU,
                            "copy": actions.COPY_MENU}.get(method)
                if method == "status":
                    child = tk.Menu(menu, tearoff=0)
                    theme.style_tk_widget(child, self.pal, "menu")
                    from .main import STATUSES
                    child._status_var = tk.StringVar(value=project.get("status") if project else "")
                    for status in STATUSES:
                        child.add_radiobutton(label=status.capitalize(), value=status, variable=child._status_var,
                                              command=lambda status=status: self._invoke_target_action(target, "_set_status", status),
                                              state="normal" if project and not self._registry_blocked() else "disabled")
                else:
                    child = self._action_menu(children, project)
                menu.add_cascade(label=label, menu=child)
                continue
            if method == "_toggle_pinned":
                label = "Unpin" if project and project.get("pinned") else "Pin"
            capability = self._action_capability(actions.METHOD_ACTIONS.get(method, "status"), project)
            menu.add_command(label=label, state="normal" if capability.enabled else "disabled",
                             command=lambda method=method: self._invoke_target_action(target, method),
                             accelerator=capability.reason)
        return menu

    def _keyboard_context_menu(self, event):
        tree = event.widget
        self._set_active("now" if tree is self.now_tree else "main")
        selected = tree.selection()
        if not selected or selected[0] == "__won-empty__":
            return "break"
        tree.see(selected[0])
        box = tree.bbox(selected[0])
        old = self.__dict__.get("_ctx_menu")
        if old:
            old.destroy()
        self._build_context_menu()
        x = tree.winfo_rootx() + 24
        y = tree.winfo_rooty() + (box[1] + box[3] if box else 24)
        try:
            self._ctx_menu.tk_popup(x, y)
        finally:
            self._ctx_menu.grab_release()
        return "break"

    def _git_project(self):
        project = self._selected_project()
        if project is None or not projects.is_repository_backed(project):
            self._status.set("This action requires an associated Git repository", important=True)
            return None
        return project

    def git_changes(self):
        project = self._git_project()
        if project:
            return git_dialogs.ChangesWindow(self, project)

    def git_commit(self):
        project = self._git_project()
        if project:
            self._flush_note_save()
            return git_dialogs.ChangesWindow(self, project, commit=True)

    def git_push(self):
        return self._network_window("push")

    def git_pull(self):
        return self._network_window("pull")

    def git_fetch(self):
        return self._network_window("fetch")

    def _network_window(self, operation):
        project = self._git_project()
        if project:
            return git_dialogs.NetworkWindow(self, project, operation)

    def git_history(self):
        project = self._git_project()
        if project:
            return git_dialogs.HistoryWindow(self, project)

    def git_remotes(self):
        project = self._git_project()
        if project:
            return git_dialogs.remotes_window(self, project)

    def _refresh_selected_repository(self):
        project = self._git_project()
        if project:
            self._refresh_metadata_async(target_project=project)

    def _copy_observed(self, key):
        project = self._selected_project()
        if project and project.get(key):
            self.clipboard_clear()
            self.clipboard_append(project[key])
            self._status.set(f"Copied {key}")

    def _copy_branch(self):
        self._copy_observed("branch")

    def _copy_head(self):
        self._copy_observed("head")

    def _focus_project_notes(self):
        self.update_idletasks()
        body_height = max(1, self.detail_body.winfo_height())
        self.detail_canvas.yview_moveto(self.d_notes_section.winfo_y() / body_height)
        self.d_notes.focus_set()

    def _build_git_summary(self, parent):
        box = ttk.Labelframe(parent, text=" Git ", padding=8)
        self.d_git_summary = ttk.Label(box, style="SurfaceMuted.TLabel", wraplength=350)
        self.d_git_summary.pack(fill="x", pady=(0, 6))
        row = ttk.Frame(box, style="Surface.TFrame")
        row.pack(fill="x")
        self.d_git_buttons = {}
        for index, (label, method) in enumerate((("Changes…", "git_changes"), ("Commit…", "git_commit"), ("Push…", "git_push"))):
            row.columnconfigure(index, weight=1)
            button = ttk.Button(row, text=label, command=lambda method=method: self._sidebar_git_action(method))
            button.grid(row=0, column=index, sticky="ew", padx=2)
            self.d_git_buttons[method] = button
        return box

    def _sidebar_git_action(self, method):
        project = self._current
        if project:
            self._invoke_target_action(actions.Target.capture(project), method)

    def _render_git_summary(self, project):
        if not hasattr(self, "d_git_summary"):
            return
        value = lambda key: project.get(key) if project.get(key) is not None else "?"
        self.d_git_summary.configure(text=f"{value('branch')} · {value('staged')} staged · "
                                    f"{value('unstaged')} unstaged · {value('untracked')} untracked\n"
                                    f"↑{value('ahead')} ↓{value('behind')} · Cached observation; refresh to recheck")
        for method, button in self.d_git_buttons.items():
            capability = self._action_capability(actions.METHOD_ACTIONS[method], project)
            button.state(["!disabled"] if capability.enabled else ["disabled"])
