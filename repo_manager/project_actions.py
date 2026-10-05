"""Cached UI capabilities are hints, never Git mutation authorization."""
from dataclasses import dataclass

from . import projects


@dataclass(frozen=True)
class Target:
    project_id: str | None
    location: str | None
    record: object = None

    @classmethod
    def capture(cls, project):
        identity = projects.project_id(project)
        return cls(identity, projects.project_folder(project), project if identity is None else None)

    def resolve(self, records):
        return next((project for project in records
                     if (projects.project_id(project) == self.project_id if self.project_id
                         else project is self.record)
                     and projects.project_folder(project) == self.location), None)


@dataclass(frozen=True)
class Capability:
    enabled: bool
    reason: str = ""


GIT_ACTIONS = {"changes", "commit", "push", "pull", "fetch", "history", "remotes", "refresh"}
WRITES = {"commit", "push", "pull", "fetch"}


def capability(action, project, *, folder_available=True, busy=False,
               registry_blocked=False, vscode_available=True, agent_available=True):
    if project is None:
        return Capability(False, "Select a Project")
    if action in GIT_ACTIONS:
        if not projects.is_repository_backed(project):
            return Capability(False, "Requires an associated Git repository")
        if not folder_available or project.get("broken"):
            return Capability(False, "Repository folder is unavailable")
        if registry_blocked:
            return Capability(False, "Recover the registry first")
        if action in WRITES and busy:
            return Capability(False, "Another Git operation is running")
        if action in {"push", "pull", "fetch"} and not (
                project.get("remote") or project.get("remote_names") or project.get("remotes")):
            return Capability(False, "No observed remote; refresh to recheck")
        if action in {"push", "pull"} and project.get("branch") in ("HEAD", "(detached)", "detached"):
            return Capability(False, "Requires an attached branch")
    elif action in {"explorer", "vscode", "terminal", "agent"}:
        if not folder_available:
            return Capability(False, "Project folder is unavailable")
        if action == "vscode" and not vscode_available:
            return Capability(False, "VS Code is unavailable")
        if action == "agent" and not agent_available:
            return Capability(False, "Agent launcher is unavailable")
        if action == "terminal" and not agent_available:
            return Capability(False, "Windows Terminal is unavailable")
    elif action in {"website", "copy_url"} and not project.get("remote"):
        return Capability(False, "No repository website URL")
    elif action in {"copy_branch", "copy_head"} and not project.get(
            "branch" if action == "copy_branch" else "head"):
        return Capability(False, "Not observed; refresh first")
    elif action in {"work", "pin", "status", "remove", "notes"} and registry_blocked:
        return Capability(False, "Recover the registry first")
    return Capability(True)


METHOD_ACTIONS = {
    "open_explorer": "explorer", "open_vscode": "vscode",
    "open_terminal": "terminal", "open_agent": "agent",
    "git_changes": "changes", "git_commit": "commit", "git_push": "push",
    "git_pull": "pull", "git_fetch": "fetch", "git_history": "history",
    "git_remotes": "remotes", "_refresh_selected_repository": "refresh",
    "open_remote": "website", "_copy_remote_url": "copy_url",
    "_copy_branch": "copy_branch", "_copy_head": "copy_head",
    "_toggle_working_on_this": "work", "_toggle_pinned": "pin",
    "_remove_from_repomanager": "remove", "_focus_project_notes": "notes",
}

REPOSITORY_MENU = (
    ("command", "Fetch…", "git_fetch"),
    ("command", "History…", "git_history"),
    ("command", "Remotes…", "git_remotes"),
    ("command", "Refresh metadata", "_refresh_selected_repository"),
)
PROJECT_MENU = (
    ("cascade", "Set status", "status"),
    ("command", "Pin / Unpin", "_toggle_pinned"),
    ("command", "Notes", "_focus_project_notes"),
    ("command", "Details…", "_open_project_technical_details"),
    ("command", "Export Project…", "_export_current_project"),
    ("command", "Export report…", "_export_current_report"),
)
COPY_MENU = (
    ("command", "Path", "_copy_path"),
    ("command", "Branch", "_copy_branch"),
    ("command", "Commit ID", "_copy_head"),
    ("command", "Repository URL", "_copy_remote_url"),
)
LAYOUT = (
    ("command", "Work on this (pin + Active)", "_toggle_working_on_this"),
    "-sep-",
    ("command", "Open in Explorer", "open_explorer"),
    ("command", "Open in VS Code", "open_vscode"),
    ("command", "Open Terminal", "open_terminal"),
    ("command", "Open Agent", "open_agent"),
    "-sep-",
    ("command", "Changes…", "git_changes"),
    ("command", "Commit…", "git_commit"),
    ("command", "Push…", "git_push"),
    ("command", "Pull — fast-forward only…", "git_pull"),
    "-sep-",
    ("cascade", "Repository", "repository"),
    ("cascade", "Project", "project"),
    ("cascade", "Copy", "copy"),
    ("command", "Open repository website", "open_remote"),
    "-sep-",
    ("command", "Remove from RepoManager…", "_remove_from_repomanager"),
)
