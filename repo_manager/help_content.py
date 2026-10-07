"""Product help shared by the desktop presentations."""

HELP_TOPICS = {
    "guide": (
        "Start here",
        "Add your repository folders in Settings > Scan folders first. "
        "Only configured folders are searched; scan depth counts nested "
        "levels below each folder. Add C:\\ is an optional whole-drive scope, "
        "not an automatic default. A nearer folder is usually faster. "
        "Select a Project in the repository table to inspect its state, "
        "Health, Provider evidence and available launchers. Use Filter to "
        "narrow the table, F5 to rescan, and the right-click menu for all "
        "context actions. RepoManager observes before it acts: repository "
        "discovery, Health and Provider checks are read-only."
    ),
    "git": (
        "Git requirement",
        "RepoManager uses Git to inspect and manage local repositories. "
        "Install Git for Windows from https://git-scm.com/install/windows "
        "using the normal installer and make Git available on PATH. Then "
        "use Check again. If RepoManager still cannot find Git, restart it "
        "so the new process can inherit the updated PATH."
    ),
    "health": (
        "Health and status",
        "Health summarizes current evidence. PASS means no issue was found "
        "by the checks that ran; WARN means attention is useful; FAIL means a "
        "serious invalid state was observed; UNKNOWN means evidence is "
        "missing or unavailable. Scanner-based evidence can be stale until "
        "the next rescan. Project lifecycle status (Idea, Active, Paused, "
        "Archived) is your curation and is separate from Health."
        " Use Ignore for this repository on eligible advisory findings, or "
        "Settings > Health to disable eligible check types globally. IGNORED "
        "is intentional suppression, never PASS; ignored checks are excluded "
        "from the active score denominator and their count remains visible. "
        "Restore re-enables a repository check; enabling the global type "
        "does not erase repository-specific preferences. Integrity checks "
        "cannot be ignored."
    ),
    "workspace": (
        "Agents",
        "An Agent is an explicitly launched local command for the selected "
        "repository. Ready means both its executable and the selected target "
        "were rechecked. After exit, Target rechecked means RepoManager "
        "observed Git state again; it does not approve the Agent's work."
        " Freebuff, OpenCode, Codex and Gemini CLI are offered only when their commands "
        "are found locally. Configure other targets in Settings > Integrations. "
        "With multiple available targets, Start Agent opens a chooser and "
        "remembers your selection. One available target starts directly; no "
        "available target requires setup. Stop Agent stops the managed process "
        "tree. Commands run with your account's permissions, not in a sandbox."
    ),
    "provider": (
        "Providers and launchers",
        "A Git remote is only a URL. Git host / Provider correspondence is "
        "inferred locally from that URL; online details are a separate "
        "read-only observation. Neither proves ownership, authentication, or "
        "write access. Launchers are detected local start commands and run "
        "only after you explicitly choose one."
    ),
    "shortcuts": (
        "Keyboard and layout",
        "Ctrl+F focuses Filter. F5 rescans. Enter runs the primary launcher "
        "for the focused table. Shift+F10 or the Menu key opens its actions. "
        "F1 opens this guide. Escape clears Filter or "
        "closes dialogs. Drag table-header separators to resize columns; use "
        "Settings > Appearance to restore safe defaults."
    ),
    "scan": (
        "Scanning and cancellation",
        "Settings > Scan folders defines the search roots and depth. Save & scan "
        "starts discovery; F5 rescans the same scope. Filter searches the already "
        "discovered inventory, not your whole computer. During scanning, Cancel "
        "scan requests cooperative cancellation and overlapping scans are blocked. "
        "Cancellation pending means the current filesystem or Git operation must "
        "return first. A cancelled scan does not replace your last complete "
        "inventory. Closing during a scan offers Cancel scan and exit, Keep "
        "scanning, or return to the app. Whole-drive scans may take longer."
    ),
    "changes": (
        "Changes, diff and commits",
        "Open Changes from Project actions or the Git actions area. Select a file "
        "to inspect its bounded diff. Stage moves selected changes into Git's "
        "index; Unstage removes them from the index without discarding your "
        "working files. Commit previews staged changes and requires a message "
        "and explicit confirmation. It creates a local commit and does not push. "
        "Stage all and commit is a separate explicit choice. Fetch, Push and "
        "fast-forward-only Pull require their own destination confirmation. "
        "Conflicts, hunk staging and discard/reset/stash require an external Git "
        "tool. Git hooks and launched commands are not sandboxed."
    ),
    "run": (
        "Run and Quick Run",
        "The Run tab lists detected launchers, unavailable reasons and custom "
        "launcher management. The header Run button uses the same launch path: "
        "one eligible launcher runs directly, several open a chooser, and none "
        "shows an unavailable state. The selected repository is the working "
        "directory. Launching may execute repository code with your permissions. "
        "Ordinary Run processes are not managed Agent runs; Stop Agent controls "
        "only the explicitly started Agent."
    ),
    "tools": (
        "Explorer, VS Code and Terminal",
        "Explorer opens the selected project folder. VS Code requires its code "
        "command or the supported user installation. Terminal requires Windows "
        "Terminal (wt.exe) and uses its configured default profile in the selected "
        "folder. Windows PowerShell 5.1 works; PowerShell 7 is not required. "
        "If wt.exe is unavailable, install Windows Terminal or enable its app "
        "execution alias. RepoManager does not change your PATH or choose a "
        "replacement shell automatically."
    ),
    "settings": (
        "Settings and feedback",
        "Settings contains Scan folders, Integrations, Appearance, Ignored "
        "projects and Health. Save applies changes; Cancel discards unsaved edits. "
        "Working on now shows available Active projects; Work on this sets Active "
        "and pins the selected project. Feedback offers positive feedback, "
        "improvements, bugs and UI issues. Save locally, copy the report, or open "
        "a public GitHub issue draft. Review and submit it yourself. No automatic "
        "telemetry or repository files are attached. Report vulnerabilities "
        "privately through the public repository's Security reporting channel."
    ),
    "releases": (
        "Versions and updates",
        "The application title and sidebar show your current version. A -dev or "
        "-rc.N suffix identifies an unpublished development/release candidate; "
        "it is not the current public stable release. Open Official releases to "
        "compare your version with the public downloads. RepoManager does not "
        "automatically check, download or replace itself. For an update, close "
        "RepoManager and extract the new official portable ZIP into a separate "
        "folder. Your profile remains in %LOCALAPPDATA%\\RepoManager. Third-party "
        "Qt/Python and icon notices are in the package's LICENSES directory."
    ),
}
