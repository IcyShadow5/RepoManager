[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$BuildVenv = "",
    [string]$OutputRoot = "",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($BuildVenv)) {
    $BuildVenv = Join-Path $repoRoot ".build-venv"
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = $repoRoot
}
$buildVenv = [IO.Path]::GetFullPath($BuildVenv)
$buildPython = Join-Path $buildVenv "Scripts\python.exe"
$requirements = Join-Path $PSScriptRoot "requirements-build.txt"
$bootstrap = Join-Path $PSScriptRoot "requirements-bootstrap.txt"
$versionSource = Join-Path $repoRoot "repo_manager\version.py"
$buildRoot = Join-Path ([IO.Path]::GetFullPath($OutputRoot)) "build"
$distRoot = Join-Path ([IO.Path]::GetFullPath($OutputRoot)) "dist"
$versionInfo = Join-Path $buildRoot "windows_version_info.txt"

$runtimeProbe = 'import json,platform,struct,sys,sysconfig; print(json.dumps(dict(version=platform.python_version(), implementation=platform.python_implementation(), bits=struct.calcsize(''P'')*8, base=sys._base_executable, free_threaded=bool(sysconfig.get_config_var(''Py_GIL_DISABLED'')))))'
$requestedJson = & $Python -I -c $runtimeProbe
if ($LASTEXITCODE -ne 0) { throw "Requested Python could not be inspected." }
$requested = $requestedJson | ConvertFrom-Json
if ($requested.implementation -ne "CPython" -or $requested.bits -ne 64 -or $requested.free_threaded) {
    throw "Windows release builds require normal 64-bit CPython."
}
if (Test-Path -LiteralPath $buildPython) {
    $existingJson = & $buildPython -I -c $runtimeProbe
    if ($LASTEXITCODE -ne 0) { throw "Existing build environment could not be inspected: $buildVenv" }
    $existing = $existingJson | ConvertFrom-Json
    if ($existing.version -ne $requested.version -or
        $existing.implementation -ne $requested.implementation -or
        $existing.bits -ne $requested.bits -or
        $existing.free_threaded -ne $requested.free_threaded -or
        [IO.Path]::GetFullPath($existing.base) -ne [IO.Path]::GetFullPath($requested.base)) {
        throw "Build environment Python does not match requested Python $($requested.version). Existing files were left untouched. Select an unused -BuildVenv directory."
    }
} elseif (Test-Path -LiteralPath $buildVenv) {
    throw "Build environment directory exists without a Python executable. Select an unused -BuildVenv directory."
}
# The project source baseline is Python 3.14; the packaged V0.1.0 runtime is fixed.
if ($requested.version -ne "3.14.7") {
    throw "V0.1.0 release builds require CPython 3.14.7. Other Python versions are rejected for this reproducible build. Existing files were left untouched."
}
if ($CheckOnly) {
    Write-Output "RequestedPython=$requestedJson"
    Write-Output "BuildVenv=$buildVenv"
    return
}

if (-not (Test-Path -LiteralPath $buildPython)) {
    & $Python -m venv $buildVenv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $buildPython -I -m pip --isolated install --disable-pip-version-check --only-binary=:all: --require-hashes --index-url https://pypi.org/simple --requirement $bootstrap
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $buildPython -I -m pip --isolated install --disable-pip-version-check --only-binary=:all: --require-hashes --index-url https://pypi.org/simple --requirement $requirements
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $buildPython -I -m pip check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$versionLine = Select-String -LiteralPath $versionSource -Pattern '^VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"$'
if ($null -eq $versionLine) {
    throw "Could not read a three-part VERSION from repo_manager/version.py"
}
$productVersion = $versionLine.Matches[0].Groups[1].Value
$versionParts = $productVersion.Split('.')
$major = [int]$versionParts[0]
$minor = [int]$versionParts[1]
$patch = [int]$versionParts[2]

New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
@"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($major, $minor, $patch, 0),
    prodvers=($major, $minor, $patch, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('FileDescription', 'RepoManager'),
         StringStruct('FileVersion', '$productVersion'),
         StringStruct('InternalName', 'RepoManager'),
         StringStruct('OriginalFilename', 'RepoManager.exe'),
         StringStruct('ProductName', 'RepoManager'),
         StringStruct('ProductVersion', '$productVersion')]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -LiteralPath $versionInfo -Encoding UTF8

$iconData = "$(Join-Path $repoRoot 'appicon.ico');."
$fallbackIconData = "$(Join-Path $repoRoot 'app.ico');."

& $buildPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --noupx `
    --contents-directory "_internal" `
    --name "RepoManager" `
    --icon (Join-Path $repoRoot "appicon.ico") `
    --add-data $iconData `
    --add-data $fallbackIconData `
    --version-file $versionInfo `
    --distpath $distRoot `
    --workpath (Join-Path $buildRoot "pyinstaller") `
    --specpath (Join-Path $buildRoot "spec") `
    (Join-Path $repoRoot "run.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$bundlePath = Join-Path $distRoot "RepoManager"
$archivePath = Join-Path $distRoot "RepoManager-$productVersion-windows-x64.zip"
$manifestPath = Join-Path $distRoot "RepoManager-$productVersion-windows-x64-manifest.json"
$runtimeMetadata = Join-Path $buildRoot "runtime-metadata.json"
& $buildPython -I (Join-Path $PSScriptRoot "runtime_licenses.py") $bundlePath $runtimeMetadata
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$runtime = Get-Content -LiteralPath $runtimeMetadata -Raw | ConvertFrom-Json
Compress-Archive -LiteralPath $bundlePath -DestinationPath $archivePath -Force

$archive = Get-Item -LiteralPath $archivePath
$hash = Get-FileHash -LiteralPath $archivePath -Algorithm SHA256
$bundleBytes = (Get-ChildItem -LiteralPath $bundlePath -Recurse -File |
    Measure-Object -Property Length -Sum).Sum
$sourceHead = (& git -C $repoRoot rev-parse HEAD 2>$null)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sourceHead)) {
    $sourceHead = "UNKNOWN"
}
$sourceStatus = @(& git -C $repoRoot status --porcelain --untracked-files=all 2>$null)
$sourceDirty = $LASTEXITCODE -ne 0 -or $sourceStatus.Count -gt 0
$pyInstallerVersion = (& $buildPython -m PyInstaller --version).Trim()
$manifest = [ordered]@{
    schema_version = 1
    product = "RepoManager"
    version = $productVersion
    platform = "windows-x64"
    distribution = "unsigned-portable-onedir"
    source_head = $sourceHead.Trim()
    source_dirty = $sourceDirty
    pyinstaller_version = $pyInstallerVersion
    python_version = $runtime.python_version
    python_implementation = $requested.implementation
    python_architecture = "x64"
    python_free_threaded = $requested.free_threaded
    tcl_version = $runtime.tcl_version
    tk_version = $runtime.tk_version
    build_dependencies = $runtime.build_dependencies
    licenses = $runtime.licenses
    build_requirements_sha256 = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
    bootstrap_requirements_sha256 = (Get-FileHash -LiteralPath $bootstrap -Algorithm SHA256).Hash
    archive = $archive.Name
    archive_bytes = $archive.Length
    archive_sha256 = $hash.Hash
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding UTF8

Write-Output "ProductVersion=$productVersion"
Write-Output "BuildPython=$buildPython"
Write-Output "PythonVersion=$($runtime.python_version)"
Write-Output "BundlePath=$bundlePath"
Write-Output "BundleBytes=$bundleBytes"
Write-Output "ArchivePath=$archivePath"
Write-Output "ArchiveBytes=$($archive.Length)"
Write-Output "ArchiveSHA256=$($hash.Hash)"
Write-Output "ManifestPath=$manifestPath"
Write-Output "SourceHead=$($manifest.source_head)"
Write-Output "SourceDirty=$sourceDirty"
Write-Output "PyInstallerVersion=$pyInstallerVersion"
