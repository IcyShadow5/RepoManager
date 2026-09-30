# GIT-GOVERNANCE-FIX-02 : read-only verification of the server-side gate state.
#
#   .\docs\governance\verify-gate-state.ps1 [-Repo IcyShadow5/RepoManager]
#
# Read-only by design. It asserts what the ruleset and the workflow must look
# like, and never writes to the repository. A mutating counterpart was
# deliberately not shipped: a ruleset writer that carries a hardcoded
# `bypass_actors` list would silently drop legitimate bypass entries the first
# time somebody ran it on a different repository.
#
# The one-time ruleset change performed for this fix was:
#
#   ruleset 24226646 "GIT-GOVERNANCE-01"
#     required_status_checks += { context: "governance-selfcheck",
#                                 integration_id: 15368 }
#
# Nothing else was modified. non_fast_forward, deletion, pull_request and
# required_status_checks were all preserved, bypass_actors stayed empty, and
# ruleset 22453804 "Protect main history" was not touched.

[CmdletBinding()]
param(
    [string]$Repo = 'IcyShadow5/RepoManager',
    [string]$GovernanceRulesetId = '24226646',
    [string]$HistoryRulesetId = '22453804'
)

$ErrorActionPreference = 'Stop'
$fail = 0

function Chk([string]$name, [bool]$ok, [string]$detail = '') {
    if ($ok) { Write-Host ("  PASS  {0,-52} {1}" -f $name, $detail) -ForegroundColor Green }
    else { Write-Host ("  FAIL  {0,-52} {1}" -f $name, $detail) -ForegroundColor Red; $script:fail++ }
}

Write-Host "`n== $Repo : required ruleset ==" -ForegroundColor Cyan
$rs = gh api "repos/$Repo/rulesets/$GovernanceRulesetId" | ConvertFrom-Json
Chk 'ruleset exists and is active' ($rs.enforcement -eq 'active') "$($rs.id) $($rs.name)"

$types = @($rs.rules | ForEach-Object { $_.type })
foreach ($required in @('non_fast_forward', 'deletion', 'pull_request', 'required_status_checks')) {
    Chk "rule preserved: $required" ($types -contains $required) ''
}

$checks = @($rs.rules | Where-Object { $_.type -eq 'required_status_checks' }).parameters.required_status_checks
$contexts = @($checks | ForEach-Object { $_.context })
Chk 'attribution-policy still required' ($contexts -contains 'attribution-policy') ''
Chk 'governance-selfcheck required' ($contexts -contains 'governance-selfcheck') ''
foreach ($c in $checks) {
    Chk "check pinned to the Actions app: $($c.context)" ($c.integration_id -eq 15368) "integration_id=$($c.integration_id)"
}

Chk 'no bypass actor' (@($rs.bypass_actors | Where-Object { $_ }).Count -eq 0) "current_user_can_bypass=$($rs.current_user_can_bypass)"
Chk 'target is the default branch' ($rs.conditions.ref_name.include -contains '~DEFAULT_BRANCH') ($rs.conditions.ref_name.include -join ',')
$pr = @($rs.rules | Where-Object { $_.type -eq 'pull_request' })[0]
Chk 'pull request required' ($null -ne $pr) "approvals=$($pr.parameters.required_approving_review_count)"

Write-Host "`n== pre-existing ruleset untouched ==" -ForegroundColor Cyan
$hist = gh api "repos/$Repo/rulesets/$HistoryRulesetId" | ConvertFrom-Json
$histTypes = @($hist.rules | ForEach-Object { $_.type })
Chk 'still active' ($hist.enforcement -eq 'active') $hist.name
Chk 'deletion preserved' ($histTypes -contains 'deletion') ''
Chk 'non_fast_forward preserved' ($histTypes -contains 'non_fast_forward') ''
Chk 'no bypass actor' (@($hist.bypass_actors | Where-Object { $_ }).Count -eq 0) ''

Write-Host "`n== workflow trust model (from the default branch) ==" -ForegroundColor Cyan
$wf = gh api "repos/$Repo/contents/.github/workflows/attribution-policy.yml" --jq '.content' | ForEach-Object { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_)) }
$code = ($wf -split "`n" | Where-Object { -not $_.TrimStart().StartsWith('#') }) -join "`n"
Chk 'uses pull_request_target' ($code -match 'pull_request_target:') ''
Chk 'no bare pull_request trigger' ($code -notmatch '(?m)^\s{2}pull_request:\s*$') ''
Chk 'checkout pinned to base sha' ($code -match 'pull_request\.base\.sha') ''
Chk 'never references the PR head' ($code -notmatch 'pull_request\.head\.sha') ''
Chk 'never checks out refs/pull' ($code -notmatch 'refs/pull/') ''
Chk 'no skip variable' ($code -notmatch 'ICY_GOVERNANCE_SKIP') ''
Chk 'no fallback range' ($code -notmatch '\|\| python' -and $code -notmatch 'HEAD_SHA~1') ''
Chk 'read-only contents permission' ($code -match '(?m)^\s*contents:\s*read\s*$') ''
Chk 'no write permission' ($code -notmatch ':\s*write') ''
Chk 'no repository secret' ($code -notmatch 'secrets\.') ''
Chk 'no id-token' ($code -notmatch 'id-token') ''

Write-Host "`n== product code untouched ==" -ForegroundColor Cyan
$diff = gh api "repos/$Repo/compare/a95891cc207b527d0e08a454583fd69e60f1a60d...main" --jq '[.files[].filename] | map(select(startswith(".github/") or startswith("docs/governance/") | not)) | length' 2>$null
Chk 'no non-governance file changed since the product baseline' ([int]$diff -eq 0) "offending files: $diff"

Write-Host ""
if ($fail -eq 0) { Write-Host 'GATE STATE: VERIFIED' -ForegroundColor Green }
else { Write-Host "GATE STATE: $fail PROBLEM(S)" -ForegroundColor Red }
exit $fail
