"""Tk surfaces for the everyday Git service; no Git on the Tk thread."""
import tkinter as tk
from tkinter import messagebox, ttk

from . import git_operations as git, projects, theme


def text_view(parent, pal):
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    text = tk.Text(frame, wrap="none", font=("Consolas", 10), state="disabled")
    theme.style_tk_widget(text, pal, "text")
    vertical = ttk.Scrollbar(frame, command=text.yview)
    horizontal = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
    text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    text.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    return text


def set_text(widget, content):
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    # Tcl cannot display surrogateescaped invalid byte sequences.
    content = str(content).encode("utf-8", "backslashreplace").decode("utf-8")
    widget.insert("1.0", content)
    widget.configure(state="disabled")


class GitWindow:
    def __init__(self, app, project, title, size="1000x700"):
        self.app = app
        self.project = dict(project)
        self.target = app._capture_git_target(project)
        self.window = tk.Toplevel(app)
        app._prepare_dialog(self.window, f"{title} — {projects.project_display_name(project)}", size)
        self.window.minsize(600, 400)
        self.body = ttk.Frame(self.window, padding=12)
        self.body.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="Loading…")
        self.busy = False
        self.generation = 0
        self.operation_controls = []
        ttk.Label(self.body, text=project.get("path"), wraplength=850).pack(anchor="w", pady=(0, 6))
        self.header = ttk.Label(self.body, text="")
        self.header.pack(anchor="w", pady=(0, 6))
        self.footer = ttk.Frame(self.body)
        self.footer.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Button(self.footer, text="Close", command=self.window.destroy).pack(side="right")
        ttk.Label(self.footer, textvariable=self.status, wraplength=650).pack(side="left", fill="x", expand=True)

    def alive(self):
        return bool(self.window.winfo_exists())

    def request(self, operation, callback, *, mutation=False):
        if self.busy or not self.alive():
            return
        self.busy = True
        self.generation += 1
        generation = self.generation
        self.status.set("Working…" if mutation else "Loading…")
        for control in self.operation_controls:
            control.state(["disabled"])

        def done(outcome, data):
            if mutation:
                self.app._git_report(self.project, outcome, str(data), getattr(self, "verb", "stage"))
            if not self.alive() or generation != self.generation:
                return
            self.busy = False
            for control in self.operation_controls:
                control.state(["!disabled"])
            from .main import git_mutation_target_is_authorized
            if not git_mutation_target_is_authorized(self.app.projects, self.target):
                self.status.set("Target association changed; close this window and inspect the original repository")
                return
            if outcome != git.SUCCESS:
                self.status.set(str(data))
                if mutation and hasattr(self, "refresh"):
                    self.refresh()
                return
            callback(data)

        self.app._git_service_async(self.target, operation, done, mutation=mutation)


class ChangesWindow(GitWindow):
    def __init__(self, app, project, *, commit=False):
        super().__init__(app, project, "Commit" if commit else "Changes")
        self.state = None
        self._pending_diff = False
        self.verb = "stage"
        self.mode = tk.StringVar(value="staged")
        controls = ttk.Frame(self.body)
        controls.pack(fill="x", pady=(0, 6))
        self.view = tk.StringVar(value="unstaged")
        for label, value in (("Unstaged / untracked", "unstaged"), ("Staged", "staged")):
            radio = ttk.Radiobutton(controls, text=label, variable=self.view, value=value,
                                    command=self.populate)
            radio.pack(side="left")
            self.operation_controls.append(radio)
        ttk.Button(controls, text="Refresh", command=self.refresh).pack(side="right")
        pane = ttk.Panedwindow(self.body, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left, right = ttk.Frame(pane), ttk.Frame(pane)
        pane.add(left, weight=2)
        pane.add(right, weight=3)
        self.files = ttk.Treeview(left, show="tree", selectmode="extended", height=12)
        scrollbar = ttk.Scrollbar(left, command=self.files.yview)
        self.files.configure(yscrollcommand=scrollbar.set)
        self.files.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.diff = text_view(right, app.pal)
        self.files.bind("<<TreeviewSelect>>", self.show_diff)
        staging = ttk.Frame(self.body)
        staging.pack(fill="x", pady=(6, 0))
        stage_button = ttk.Button(staging, text="Stage selected", command=lambda: self.stage(False))
        stage_button.pack(side="left")
        unstage_button = ttk.Button(staging, text="Unstage selected", command=lambda: self.stage(True))
        unstage_button.pack(side="left", padx=6)
        self.operation_controls.extend((stage_button, unstage_button))
        ttk.Label(staging, text="Unstage never discards working files.", style="Muted.TLabel").pack(side="left")
        box = ttk.Labelframe(self.body, text=" Local commit — never pushes ", padding=8)
        box.pack(fill="x", pady=(8, 0))
        ttk.Radiobutton(box, text="Commit staged changes (default)", variable=self.mode,
                        value="staged").pack(anchor="w")
        ttk.Radiobutton(box, text="Stage all current changes and commit", variable=self.mode,
                        value="all").pack(anchor="w")
        ttk.Label(box, text="Stage all uses git add -A at execution; editors and external Git are not locked out.",
                  style="Muted.TLabel", wraplength=850).pack(anchor="w")
        self.message = ttk.Entry(box)
        self.message.pack(side="left", fill="x", expand=True, pady=(6, 0))
        self.message.bind("<Return>", lambda event: self.commit())
        commit_button = ttk.Button(box, text="Commit…", command=self.commit, style="Primary.TButton")
        commit_button.pack(side="right", padx=(8, 0))
        self.operation_controls.append(commit_button)
        if commit:
            self.message.focus_set()
        self.refresh()

    def refresh(self):
        self.request(lambda repository: repository.state(), self.loaded)

    def loaded(self, state):
        self.state = state
        self.populate()
        self.status.set("Snapshot loaded — refresh if another tool edits the repository")
        self.header.configure(text=f"Branch: {state.branch or 'Detached HEAD'} · "
                              f"{sum(change.staged for change in state.changes)} staged · "
                              f"{sum(change.unstaged for change in state.changes)} unstaged/untracked"
                              + (" · In-progress operation" if state.in_progress else ""))

    def populate(self):
        if self.busy or self.state is None:
            return
        self.files.delete(*self.files.get_children())
        for index, change in enumerate(self.state.changes):
            if change.staged if self.view.get() == "staged" else change.unstaged:
                self.files.insert("", "end", iid=str(index), text=change.label)
        set_text(self.diff, "Select a file to preview its differences.\nBinary/large output is bounded.\n"
                 "Conflicts and submodule mutations must be handled externally.")

    def selected(self):
        return tuple(self.state.changes[int(index)] for index in self.files.selection()) if self.state else ()

    def show_diff(self, event=None):
        selected = self.selected()
        if not selected:
            return
        if self.busy:
            self._pending_diff = True
            return
        selected_change = selected[0]
        staged = self.view.get() == "staged"
        self._pending_diff = False
        def loaded(content):
            current = self.selected()
            if current and current[0] == selected_change and (self.view.get() == "staged") == staged:
                set_text(self.diff, content)
                self.status.set("Diff loaded")
            if self._pending_diff:
                self.show_diff()
        self.request(lambda repository: repository.diff(selected_change, staged=staged), loaded)

    def stage(self, unstage):
        selected = self.selected()
        if not selected or self.state is None or self.busy:
            self.status.set("Select files first; wait for any running operation")
            return
        state = self.state
        self.verb = "unstage" if unstage else "stage"
        self.request(lambda repository: repository.stage(state, selected, unstage=unstage),
                     lambda result: self.refresh(), mutation=True)

    def commit(self):
        if self.state is None or self.busy:
            return
        message = self.message.get().strip()
        if not message:
            self.status.set("A commit message is required")
            self.message.focus_set()
            return
        stage_all = self.mode.get() == "all"
        selected = tuple(change for change in self.state.changes if stage_all or change.staged)
        if not selected:
            self.status.set("Nothing staged. Stage files or explicitly choose Stage all.")
            return
        preview = "\n".join(change.label for change in selected[:20])
        if not messagebox.askyesno("Confirm local commit", f"Branch: {self.state.branch or 'Detached HEAD'}\n"
                                  f"Mode: {'Stage all and commit' if stage_all else 'Staged only'}\n"
                                  f"Message: {message}\n\n{preview}\n\nNothing will be pushed.", parent=self.window):
            return
        state = self.state
        self.verb = "commit"
        self.request(lambda repository: repository.commit(state, message, stage_all=stage_all),
                     lambda result: (self.message.delete(0, "end"), self.refresh()), mutation=True)


class NetworkWindow(GitWindow):
    def __init__(self, app, project, operation):
        super().__init__(app, project, operation.title(), "760x540")
        self.verb = operation
        self.approval = None
        self.remote_records = ()
        self.remote = ttk.Combobox(self.body, state="readonly")
        self.remote.pack(fill="x", pady=4)
        self.remote.bind("<<ComboboxSelected>>", lambda event: self.invalidate())
        ttk.Label(self.body, text="Destination branch (Push/Pull only):").pack(anchor="w")
        self.destination = ttk.Entry(self.body)
        self.destination.pack(fill="x", pady=4)
        self.destination.bind("<KeyRelease>", lambda event: self.invalidate())
        self.set_upstream = tk.BooleanVar(value=False)
        if operation == "push":
            ttk.Checkbutton(self.body, text="Set upstream for this branch (explicit opt-in)",
                            variable=self.set_upstream, command=self.invalidate).pack(anchor="w")
        self.details = text_view(self.body, app.pal)
        actions = ttk.Frame(self.body)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="Preview destination", command=self.preview).pack(side="left")
        self.confirm = ttk.Button(actions, text=f"{operation.title()}…", state="disabled",
                                  command=self.execute, style="Primary.TButton")
        self.confirm.pack(side="right")
        self.operation_controls.extend((self.remote, self.destination))
        self.request(lambda repository: (repository.state(), repository.remotes(),
                                          repository.upstream(repository.state().branch)), self.loaded)

    def invalidate(self):
        self.approval = None
        self.confirm.state(["disabled"])
        self.status.set("Preview the selected destination before confirming")

    def loaded(self, data):
        state, remotes, upstream = data
        self.remote_records = remotes
        self.remote.configure(values=tuple(remote.name for remote in remotes))
        if remotes:
            # Configured upstream selection comes from live service preview, not cache.
            preferred = upstream[0] if upstream else "origin"
            self.remote.set(next((remote.name for remote in remotes if remote.name == preferred), remotes[0].name))
            if upstream and self.verb in ("push", "pull"):
                self.destination.insert(0, upstream[1])
        self.header.configure(text=f"Branch: {state.branch or 'Detached HEAD'} · HEAD: {(state.head or 'unborn')[:12]}")
        set_text(self.details, "Choose a remote and preview.\nPush never forces or automatically pulls.\n"
                 "Pull is fast-forward-only and requires a clean checkout.\nFetch never prunes or changes checkout files.")
        self.status.set("Ready to preview" if remotes else "No remotes configured")

    def preview(self):
        if self.busy:
            return
        remote = self.remote.get()
        destination = self.destination.get().strip() or None
        upstream = self.set_upstream.get()
        def collect(repository):
            approval = repository.approve_network(self.verb, remote, destination, set_upstream=upstream)
            outgoing = "Outgoing commits: unknown (no tracked target evidence)"
            if self.verb == "push" and approval.upstream:
                rc, out, _ = repository.run("rev-list", "--count", "@{upstream}..HEAD", allowed=(0, 128))
                if rc == 0:
                    outgoing = f"Outgoing commits relative to cached upstream: {out.strip()} (no network probe)"
            return approval, outgoing
        self.request(collect, self.previewed)

    def previewed(self, data):
        self.approval, outgoing = data
        approval = self.approval
        if approval.destination:
            self.destination.delete(0, "end")
            self.destination.insert(0, approval.destination)
        set_text(self.details, approval.remote.describe() +
                 f"\n\nAction: {self.verb}\nBranch: {approval.branch or '(none)'}\n"
                 f"Destination: {approval.destination or '(configured fetch refspecs)'}\n"
                 f"Set upstream: {approval.set_upstream}\n{outgoing}\n\n"
                 "Remote/HEAD will be rechecked before execution. No automatic retries.")
        self.status.set("Destination previewed; ready for explicit confirmation")
        self.confirm.state(["!disabled"])

    def execute(self):
        approval = self.approval
        if self.busy or approval is None:
            return
        if (self.remote.get() != approval.remote.name
                or (self.verb in ("push", "pull") and self.destination.get().strip() != approval.destination)
                or self.set_upstream.get() != approval.set_upstream):
            self.invalidate()
            return
        if not messagebox.askyesno(f"Confirm {self.verb}",
                                  f"{self.verb.title()} using {approval.remote.name}\n"
                                  f"Destination branch: {approval.destination or '(fetch refspecs)'}\n\n"
                                  f"{approval.remote.describe()}\n\nProceed?", parent=self.window):
            return
        self.confirm.state(["disabled"])
        self.request(lambda repository: repository.network(approval),
                     lambda result: (self.status.set("Completed; close or preview again"), self.invalidate()), mutation=True)


class HistoryWindow(GitWindow):
    def __init__(self, app, project):
        super().__init__(app, project, "History")
        self.limit = 100
        self.content = text_view(self.body, app.pal)
        self.content.bind("<Double-1>", self.select_commit)
        self.content.bind("<ButtonRelease-1>", self.select_commit)
        actions = ttk.Frame(self.body)
        actions.pack(fill="x", pady=(6, 0))
        ttk.Button(actions, text="Load more", command=self.more).pack(side="left")
        self.commit_id = ttk.Entry(actions, width=45)
        self.commit_id.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(actions, text="View commit…", command=self.details).pack(side="left")
        ttk.Button(actions, text="Copy ID", command=self.copy).pack(side="left", padx=(6, 0))
        self.load()

    def load(self):
        self.request(lambda repository: repository.history(limit=self.limit),
                     lambda content: (set_text(self.content, content), self.status.set(f"Up to {self.limit} commits; paste an ID to inspect")))

    def more(self):
        if not self.busy:
            self.limit = min(self.limit + 100, 1000)
            self.load()

    def select_commit(self, event):
        import re
        index = self.content.index(f"@{event.x},{event.y}")
        line = self.content.get(index + " linestart", index + " lineend").strip()
        if re.fullmatch(r"[0-9a-fA-F]{40,64}", line):
            self.commit_id.delete(0, "end")
            self.commit_id.insert(0, line)

    def details(self):
        identity = self.commit_id.get().strip()
        self.request(lambda repository: repository.commit_details(identity),
                     lambda content: (set_text(self.content, content), self.status.set("Commit detail (bounded output)")))

    def copy(self):
        identity = self.commit_id.get().strip()
        import re
        if re.fullmatch(r"[0-9a-fA-F]{40,64}", identity):
            self.app.clipboard_clear()
            self.app.clipboard_append(identity)
            self.status.set("Commit ID copied")


def remotes_window(app, project):
    view = GitWindow(app, project, "Remotes", "760x540")
    content = text_view(view.body, app.pal)
    def collect(repository):
        state = repository.state()
        upstream = repository.upstream(state.branch)
        return f"Branch: {state.branch or 'Detached HEAD'}\nUpstream: {upstream or '(none)'}\n\n" + "\n\n".join(remote.describe() for remote in repository.remotes())
    view.request(collect, lambda text: (set_text(content, text), view.status.set("Read-only; URL credentials/query/fragment hidden")))
    return view
