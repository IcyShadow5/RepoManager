# RepoManager v0.1.3

- Hardens automatic/read-only Git observation against repository-local external
  helper execution. Windows process isolation prevents Git from starting helper
  processes; observations that cannot complete safely remain unavailable rather
  than being reported as clean. Linked worktrees remain supported.
- Detects the Freebuff CLI automatically, alongside OpenCode, Codex, Gemini CLI
  and configured custom Agents. Freebuff is preferred for new installations
  when available. Saved selections remain respected, and multiple available
  Agents still require an explicit chooser selection before launch.
- Adds a compact Agent Settings shortcut that opens the existing Integrations
  settings directly.
- Publishes the private fallback security contact in SECURITY.md. GitHub Private
  Vulnerability Reporting remains the preferred reporting channel.

No cloud service, Agent sandbox or unattended Agent execution is introduced.
Explicit Agent launches and Git mutation/network actions remain separate,
user-authorized operations that can run configured commands. Repository metadata
can be unavailable when Git would require external helpers or emits diagnostics.
The installed Git executable itself remains a trusted system dependency.
Submodule inspection also requires a Git child process. Until it has a separate
safe observation path, affected parent repositories remain unobserved and their
Changes/status-dependent actions require an external Git tool. Cached clean
counts do not override this unavailable state.

Official support remains Windows 10 22H2 / Windows 11 x64. The portable build is
unsigned and Windows SmartScreen may warn. Git must be available on PATH.
Windows Terminal is required only for the Terminal action; PowerShell 7 is not
required for that action. There is no automatic updater.
