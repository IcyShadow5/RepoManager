"""Manual move approval keeps pair provenance; never guesses absorption IDs."""
import copy
import tkinter as tk
from tkinter import messagebox, ttk

from . import git_dialogs, project_actions, projects
from .relocation import approve_candidate, retire_accepted_pair






class ReviewWindow:
    def __init__(self, app):
        self.app = app
        self.window = tk.Toplevel(app)
        app._prepare_dialog(self.window, "Scan issues & possible moves", "1050x680")
        self.window.minsize(650, 420)
        body = ttk.Frame(self.window, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Review evidence before updating a Project location. No repository files are moved.",
                  wraplength=900).pack(anchor="w", pady=(0, 8))
        self.tabs = ttk.Notebook(body)
        self.tabs.pack(fill="both", expand=True)
        issues, moves = ttk.Frame(self.tabs, padding=8), ttk.Frame(self.tabs, padding=8)
        self.tabs.add(issues, text="Scan issues")
        self.tabs.add(moves, text="Possible moves")
        self.issue_text = git_dialogs.text_view(issues, app.pal)
        ttk.Button(issues, text="Copy details", command=self.copy_issues).pack(side="left", pady=8)
        ttk.Button(issues, text="Scan settings…", command=self.open_settings).pack(side="right", pady=8)
        pane = ttk.Panedwindow(moves, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left, right = ttk.Frame(pane), ttk.Frame(pane)
        pane.add(left, weight=2)
        pane.add(right, weight=3)
        self.list = ttk.Treeview(left, columns=("category",), show="tree headings", selectmode="browse")
        self.list.heading("#0", text="Project")
        self.list.heading("category", text="Evidence")
        self.list.column("#0", width=210)
        self.list.column("category", width=100)
        scroll = ttk.Scrollbar(left, command=self.list.yview)
        self.list.configure(yscrollcommand=scroll.set)
        self.list.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.filter = tk.StringVar()
        filter_row = ttk.Frame(moves)
        filter_row.pack(fill="x", before=pane, pady=(0, 6))
        ttk.Label(filter_row, text="Filter moves").pack(side="left")
        ttk.Entry(filter_row, textvariable=self.filter).pack(side="left", fill="x", expand=True, padx=6)
        self.filter.trace_add("write", lambda *args: self.populate())
        self.detail = git_dialogs.text_view(right, app.pal)
        self.list.bind("<<TreeviewSelect>>", self.show_selected)
        buttons = ttk.Frame(moves)
        buttons.pack(fill="x", pady=(8, 0))
        self.choose = ttk.Button(buttons, text="Choose location…", command=self.select_candidate)
        self.choose.pack(side="left")
        ttk.Button(buttons, text="Keep both", command=self.keep_both).pack(side="left", padx=6)
        ttk.Button(buttons, text="Copy paths/evidence", command=self.copy_detail).pack(side="left")
        ttk.Label(body, text="Later keeps suggestions unresolved. Keep both durably suppresses these exact pairings.",
                  style="Muted.TLabel", wraplength=900).pack(anchor="w", pady=8)
        ttk.Button(body, text="Later / Close", command=self.window.destroy).pack(anchor="e")
        self.refresh()
        if app._move_suggestions:
            self.tabs.select(moves)

    def refresh(self):
        if not self.window.winfo_exists():
            return
        self.issues = "\n\n".join(f"{problem.get('path', '')}\n{problem.get('reason', '')}" for problem in self.app._problems)
        git_dialogs.set_text(self.issue_text, self.issues or "No scan issues")
        self.populate()
        self.tabs.tab(0, text=f"Scan issues ({len(self.app._problems)})")
        self.tabs.tab(1, text=f"Possible moves ({len(self.app._move_suggestions)})")
        git_dialogs.set_text(self.detail, "Select a suggestion to inspect candidate locations and evidence.")

    def populate(self):
        query = self.filter.get().casefold().strip()
        self.suggestions = [suggestion for suggestion in self.app._move_suggestions
                            if query in str(suggestion).casefold()]
        self.list.delete(*self.list.get_children())
        for index, suggestion in enumerate(self.suggestions):
            self.list.insert("", "end", iid=str(index), text=suggestion.get("name", "Project"),
                             values=(suggestion.get("category", "possible"),))


    def selected(self):
        selection = self.list.selection()
        return self.suggestions[int(selection[0])] if selection else None

    def show_selected(self, event=None):
        suggestion = self.selected()
        if not suggestion:
            return
        olds = [suggestion["old_path"]] if suggestion.get("old_path") else suggestion.get("old_paths", [])
        news = [suggestion["new_path"]] if suggestion.get("new_path") else suggestion.get("new_paths", [])
        text = "Previous locations:\n" + "\n".join(olds) + "\n\nCandidate locations:\n" + "\n".join(news)
        text += "\n\nEvidence:\n" + "\n".join(suggestion.get("evidence", []))
        text += "\n\nUpdating a location preserves source Project identity/notes. Curated targets are never silently merged."
        if suggestion.get("category") == "ambiguous" and not suggestion.get("candidates"):
            text += "\n\nPair provenance unavailable. Rescan or Keep both; protected curated targets cannot be absorbed."
        git_dialogs.set_text(self.detail, text)
        self.choose.configure(text="Choose location…" if suggestion.get("category") == "ambiguous" else "Update project location…")

    def keep_both(self):
        suggestion = self.selected()
        if suggestion and messagebox.askyesno("Keep both", "Durably suppress these exact candidate pairings? No files are deleted.", parent=self.window):
            self.app._keep_both_move(suggestion)
            self.refresh()

    def open_settings(self):
        self.window.destroy()
        self.app.open_settings()

    def copy_issues(self):
        self.app.clipboard_clear()
        self.app.clipboard_append(self.issues)

    def copy_detail(self):
        self.app.clipboard_clear()
        self.app.clipboard_append(self.detail.get("1.0", "end-1c"))

    def select_candidate(self):
        suggestion = self.selected()
        if not suggestion:
            return
        if suggestion.get("category") != "ambiguous":
            self.confirm_pair(suggestion)
            return
        candidates = suggestion.get("candidates") or []
        if not candidates:
            messagebox.showinfo("Move review", "No proven candidate pair is available. Rescan or Keep both; no registry state was changed.", parent=self.window)
            return
        dialog = tk.Toplevel(self.window)
        self.app._prepare_dialog(dialog, "Choose a move candidate", "850x480")
        content = ttk.Frame(dialog, padding=12)
        content.pack(fill="both", expand=True)
        choice = tk.Listbox(content, exportselection=False)
        from . import theme
        theme.style_tk_widget(choice, self.app.pal, "list")
        choice.pack(fill="both", expand=True)
        for pair in candidates:
            choice.insert("end", f"{pair['old_path']} → {pair['new_path']}")
        evidence = git_dialogs.text_view(content, self.app.pal)
        choice.bind("<<ListboxSelect>>", lambda event: git_dialogs.set_text(evidence, "\n".join(
            candidates[choice.curselection()[0]].get("evidence", []))) if choice.curselection() else None)
        def select():
            if not choice.curselection():
                return
            try:
                pair = approve_candidate(suggestion, choice.curselection()[0], self.app.projects)
            except ValueError as exc:
                messagebox.showerror("Move review", str(exc), parent=dialog)
                return
            dialog.destroy()
            self.confirm_pair(pair)
        ttk.Button(content, text="Review selected pair…", command=select, style="Primary.TButton").pack(anchor="e", pady=6)

    def confirm_pair(self, pair):
        source = next((record for record in self.app.projects if record.get("path") == pair.get("old_path")), None)
        if source is None:
            messagebox.showerror("Move review", "Source Project is no longer available", parent=self.window)
            return
        target = project_actions.Target.capture(source)
        counterpart = next((record for record in self.app.projects if record.get("path") == pair.get("new_path")), None)
        from .main import _counterpart_is_disposable
        if counterpart and (projects.project_id(counterpart) != pair.get("new_project_id")
                            or not _counterpart_is_disposable(counterpart, pair.get("new_path"))):
            messagebox.showinfo("Protected target", "The candidate is curated, owns notes, or lacks proven scan-created identity. Keep both or resolve the registry entries explicitly; nothing was merged.", parent=self.window)
            return
        if messagebox.askyesno("Update Project location", f"From: {pair.get('old_path')}\n\nTo: {pair.get('new_path')}\n\n"
                               "Only the Project association and its notes change; repository files are not moved. Proceed?", parent=self.window):
            if target.resolve(self.app.projects) is None:
                messagebox.showerror("Move review", "Source association changed; cancelled", parent=self.window)
                return
            self.app._accept_move(pair)
            self.refresh()
