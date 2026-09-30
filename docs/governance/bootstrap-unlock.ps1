# GIT-GOVERNANCE-FIX-02 : bootstrap unlock / relock for the ruleset bypass.
#
#   .\docs\governance\bootstrap-unlock.ps1 -Action Unlock
#   gh pr merge <n> --repo IcyShadow5/RepoManager --admin --merge
#   .\docs\governance\bootstrap-unlock.ps1 -Action Relock
#
# ## Why this exists
#
# `pull_request_target` fires only for workflow files that already exist on the
# base branch, so a pull request that introduces one cannot be judged by it.
# Symmetrically, for a `pull_request` event GitHub reads the workflow from the
# pull request head, so removing the `pull_request` trigger makes the guard
# disappear along with the old workflow.
#
# The consequence is that the trust-model fix cannot install itself through its
# own gate. `gh pr merge --admin` alone is refused against a ruleset with no
# bypass actor, so the supported mechanism is a temporary bypass actor, used
# for exactly one merge and removed again.
#
# ## Safety properties
#
# * The end state is identical to the start state: `bypass_actors` is empty.
# * Every intermediate state is written to the ruleset version history, so the
#   exception is auditable rather than invisible.
# * The script refuses to report success unless all four protective rules
#   survived the write.
# * It is deliberately NOT run automatically. It is invoked by hand, once,
#   around one merge.
#
# ## After the fix is installed
#
# This script should never be needed again. The self-test now runs to
# completion against the base policy, so `attribution-policy` and
# `governance-selfcheck` both report on ordinary pull requests and no bypass is
# required. If you find yourself running this a third time, something else is
# wrong -- read the run log before unlocking anything.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('Unlock', 'Relock')][string]$Action,
    [string]$Repo = 'IcyShadow5/RepoManager',
    [string]$RulesetId = '24226646',
    [int]$OwnerId = 51265389
)

$ErrorActionPreference = 'Stop'
$tmp = Join-Path $env:TEMP 'ggfix-unlock.json'

function Write-JsonFile([string]$Path, $Object) {
    [System.IO.File]::WriteAllText(
        $Path, ($Object | ConvertTo-Json -Depth 20), (New-Object System.Text.UTF8Encoding $false))
}

$current = gh api "repos/$Repo/rulesets/$RulesetId" | ConvertFrom-Json
$before = @($current.bypass_actors | Where-Object { $_ }).Count
Write-Host "current bypass actors: $before"

$body = @{
    name          = $current.name
    target        = $current.target
    enforcement   = $current.enforcement
    conditions    = $current.conditions
    rules         = $current.rules
    bypass_actors = @()
}

if ($Action -eq 'Unlock') {
    if ($before -gt 0) { Write-Host 'already unlocked; nothing to do' -ForegroundColor Yellow; exit 0 }
    $body.bypass_actors = @(@{ actor_id = $OwnerId; actor_type = 'User'; bypass_mode = 'always' })
    Write-Host "granting a TEMPORARY bypass actor to user id $OwnerId for one merge" -ForegroundColor Yellow
} else {
    if ($before -eq 0) { Write-Host 'already locked; nothing to do' -ForegroundColor Green; exit 0 }
    $body.bypass_actors = @()
    Write-Host 'removing the temporary bypass actor' -ForegroundColor Green
}

Write-JsonFile $tmp $body
$null = gh api -X PUT "repos/$Repo/rulesets/$RulesetId" --input $tmp --jq '.id'

$after = gh api "repos/$Repo/rulesets/$RulesetId" | ConvertFrom-Json
$now = @($after.bypass_actors | Where-Object { $_ }).Count
$types = @($after.rules | ForEach-Object { $_.type })
$checks = @($after.rules | Where-Object { $_.type -eq 'required_status_checks' }).parameters.required_status_checks.context

Write-Host ""
Write-Host "verification after $Action" -ForegroundColor Cyan
Write-Host "  bypass actors   : $now"
Write-Host "  rules preserved : $($types -join ', ')"
Write-Host "  required checks : $($checks -join ', ')"
Write-Host "  enforcement     : $($after.enforcement)"
Write-Host "  ref condition   : $($after.conditions.ref_name.include -join ', ')"

$expected = 'deletion,non_fast_forward,pull_request,required_status_checks'
if ((($types | Sort-Object) -join ',') -ne $expected) {
    Write-Host "ERROR: protective rules were not preserved" -ForegroundColor Red
    exit 1
}
if ($Action -eq 'Unlock' -and $now -ne 1) { Write-Host 'ERROR: unlock did not take effect' -ForegroundColor Red; exit 1 }
if ($Action -eq 'Relock' -and $now -ne 0) { Write-Host 'ERROR: relock did not take effect' -ForegroundColor Red; exit 1 }
Write-Host "OK: $Action applied, protective rules intact" -ForegroundColor Green
