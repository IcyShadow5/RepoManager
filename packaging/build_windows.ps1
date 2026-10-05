[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$BuildVenv = "",
    [string]$OutputRoot = "",
    [ValidateSet("Qt", "Classic")][string]$Presentation = "Qt",
    [string]$ThirdPartySources = "",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "Windows release builds require PowerShell 7 (pwsh)."
}
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
$qtRequirements = Join-Path $PSScriptRoot "requirements-build-qt.txt"
$bootstrap = Join-Path $PSScriptRoot "requirements-bootstrap.txt"
$versionSource = Join-Path $repoRoot "repo_manager\version.py"
$applicationLicense = Join-Path $repoRoot "LICENSE"
$buildRoot = Join-Path ([IO.Path]::GetFullPath($OutputRoot)) "build"
$distRoot = Join-Path ([IO.Path]::GetFullPath($OutputRoot)) "dist"
$versionInfo = Join-Path $buildRoot "windows_version_info.txt"

$runtimeProbe = 'import json,platform,struct,sys,sysconfig; print(json.dumps(dict(version=platform.python_version(), implementation=platform.python_implementation(), bits=struct.calcsize(''P'')*8, base=sys._base_executable, free_threaded=bool(sysconfig.get_config_var(''Py_GIL_DISABLED'')))))'
if (-not (Test-Path -LiteralPath $applicationLicense -PathType Leaf)) {
    throw "Required application license is unavailable: $applicationLicense"
}
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
# The project source baseline is Python 3.14; the Windows release runtime is fixed.
if ($requested.version -ne "3.14.7") {
    throw "RepoManager Windows release builds require CPython 3.14.7. Other Python versions are rejected for this reproducible build. Existing files were left untouched."
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
if ($Presentation -eq "Qt") {
    if (-not (Test-Path -LiteralPath (Join-Path $ThirdPartySources "manifest.json") -PathType Leaf)) {
        throw "Qt builds require the verified third-party source archives (-ThirdPartySources)."
    }
    & $buildPython -I -m pip --isolated install --disable-pip-version-check --only-binary=:all: --require-hashes --index-url https://pypi.org/simple --requirement $qtRequirements
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& $buildPython -I -m pip check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$versionLine = Select-String -LiteralPath $versionSource -Pattern '^VERSION = "([0-9]+\.[0-9]+\.[0-9]+(?:-dev|-rc\.[1-9][0-9]*)?)"$'
if ($null -eq $versionLine) {
    throw "Could not read VERSION (three-part, optionally -dev or -rc.N) from repo_manager/version.py"
}
$productVersion = $versionLine.Matches[0].Groups[1].Value
$sourceJson = & $buildPython -I (Join-Path $PSScriptRoot 'source_provenance.py')
if ($LASTEXITCODE -ne 0) { throw 'Source identity could not be established.' }
$sourceEvidence = $sourceJson | ConvertFrom-Json
$buildHead = $sourceEvidence.head
$buildDirty = $sourceEvidence.dirty
if ($productVersion -match '-rc\.' -and $buildDirty) { throw 'Release candidates require a clean committed source tree.' }
$versionParts = $productVersion.Split('-')[0].Split('.')
$major = [int]$versionParts[0]
$minor = [int]$versionParts[1]
$patch = [int]$versionParts[2]
$versionFlags = if ($productVersion.Contains('-')) { '0x2' } else { '0x0' }
$productName = if ($Presentation -eq 'Qt') { 'RepoManager' } else { 'RepoManager Classic Development' }

New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
@"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($major, $minor, $patch, 0),
    prodvers=($major, $minor, $patch, 0),
    mask=0x3f,
    flags=$versionFlags,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('FileDescription', '$productName'),
         StringStruct('FileVersion', '$productVersion'),
         StringStruct('InternalName', 'RepoManager'),
         StringStruct('OriginalFilename', 'RepoManager.exe'),
         StringStruct('ProductName', '$productName'),
         StringStruct('ProductVersion', '$productVersion')]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -LiteralPath $versionInfo -Encoding UTF8

$iconData = "$(Join-Path $repoRoot 'appicon.ico');."
$fallbackIconData = "$(Join-Path $repoRoot 'app.ico');."
$entryPoint = Join-Path $repoRoot 'run_classic.py'
$presentationArgs = @()
if ($Presentation -eq 'Qt') {
    $entryPoint = Join-Path $repoRoot 'run.py'
    $qmlInventory = Join-Path $buildRoot 'qml-imports.json'
    & $buildPython -I (Join-Path $PSScriptRoot 'qml_inventory.py') (Join-Path $repoRoot 'repo_manager\qml') $qmlInventory
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $env:REPOMANAGER_BUILD_QML_INVENTORY = $qmlInventory
    $presentationArgs = @('--additional-hooks-dir', (Join-Path $PSScriptRoot 'hooks'),
        '--runtime-hook', (Join-Path $PSScriptRoot 'qt_runtime_probe.py'),
        '--add-data', "$(Join-Path $repoRoot 'repo_manager\qml');repo_manager/qml",
        '--add-data', "$(Join-Path $repoRoot 'assets\repomanager.svg');assets",
        '--exclude-module', 'tkinter', '--exclude-module', '_tkinter',
        '--exclude-module', 'PySide6.QtQuick3D', '--exclude-module', 'PySide6.QtWebEngineCore')
}

# Dependency analysis must not resolve unrelated DLLs from the developer PATH.
$originalBuildPath = $env:PATH
$env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$(Split-Path $buildPython);$(Split-Path $requested.base)"
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
    @presentationArgs `
    $entryPoint
$pyInstallerExit = $LASTEXITCODE
$env:PATH = $originalBuildPath
if ($pyInstallerExit -ne 0) { exit $pyInstallerExit }

$bundlePath = Join-Path $distRoot "RepoManager"
& $buildPython -I (Join-Path $PSScriptRoot 'verify_binary_origins.py') (Join-Path $buildRoot 'pyinstaller\RepoManager\Analysis-00.toc') (Join-Path $bundlePath 'BINARY_ORIGINS.json')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($Presentation -eq 'Qt') {
    & $buildPython -I (Join-Path $PSScriptRoot 'pe_import_closure.py') $bundlePath (Join-Path $bundlePath 'PE_IMPORT_CLOSURE.json')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'PORTABLE_README.txt') -Destination (Join-Path $bundlePath 'README.txt')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'THIRD_PARTY_NOTICES.md') -Destination $bundlePath
@{version=$productVersion; presentation=$Presentation; source_head=$buildHead; source_dirty=$buildDirty} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $bundlePath 'BUILD_INFO.json') -Encoding UTF8
$archivePath = Join-Path $distRoot "RepoManager-$productVersion-windows-x64.zip"
$manifestPath = Join-Path $distRoot "RepoManager-$productVersion-windows-x64-manifest.json"
$runtimeMetadata = Join-Path $buildRoot "runtime-metadata.json"
if ($Presentation -eq 'Qt') {
    & $buildPython -I (Join-Path $PSScriptRoot "qt_runtime_licenses.py") $bundlePath $runtimeMetadata $ThirdPartySources $repoRoot
} else {
    & $buildPython -I (Join-Path $PSScriptRoot "runtime_licenses.py") $bundlePath $runtimeMetadata
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$runtime = Get-Content -LiteralPath $runtimeMetadata -Raw | ConvertFrom-Json
if ($Presentation -eq 'Qt') {
    $sourcesArchive = Join-Path $distRoot "RepoManager-$productVersion-third-party-sources.zip"
    $sourcesMetadata = Join-Path $distRoot "RepoManager-$productVersion-third-party-sources.json"
    & $buildPython -I (Join-Path $PSScriptRoot 'distribution_sources.py') $ThirdPartySources $sourcesArchive $sourcesMetadata
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
Copy-Item -LiteralPath $applicationLicense -Destination (Join-Path $bundlePath "LICENSE") -Force
Add-Type -AssemblyName System.IO.Compression

$archiveStream = [System.IO.File]::Open(
    $archivePath,
    [System.IO.FileMode]::Create
)

try {
    $zipArchive = [System.IO.Compression.ZipArchive]::new(
        $archiveStream,
        [System.IO.Compression.ZipArchiveMode]::Create
    )

    try {
        Get-ChildItem -LiteralPath $bundlePath -Recurse -File | ForEach-Object {
            $relativePath = [System.IO.Path]::GetRelativePath(
                (Split-Path $bundlePath -Parent),
                $_.FullName
            ).Replace('\', '/')

            if ($relativePath.Contains('\')) {
                throw "Invalid ZIP entry path contains backslash: $relativePath"
            }

            $entry = $zipArchive.CreateEntry(
                $relativePath,
                [System.IO.Compression.CompressionLevel]::Optimal
            )

            $inputStream = $_.OpenRead()
            try {
                $outputStream = $entry.Open()
                try {
                    $inputStream.CopyTo($outputStream)
                }
                finally {
                    $outputStream.Dispose()
                }
            }
            finally {
                $inputStream.Dispose()
            }
        }
    }
    finally {
        $zipArchive.Dispose()
    }
}
finally {
    $archiveStream.Dispose()
}

$archiveStream = [System.IO.File]::OpenRead($archivePath)

try {
    $zipArchive = [System.IO.Compression.ZipArchive]::new(
        $archiveStream,
        [System.IO.Compression.ZipArchiveMode]::Read
    )

    try {
        $invalidEntries = @(
            $zipArchive.Entries |
                Where-Object { $_.FullName.Contains('\') } |
                ForEach-Object { $_.FullName }
        )
        if ($invalidEntries.Count -gt 0) {
            throw "ZIP archive contains entry path(s) with a backslash: $($invalidEntries -join ', ')"
        }
        if ($null -eq $zipArchive.GetEntry("RepoManager/RepoManager.exe")) {
            throw "ZIP archive is missing required entry: RepoManager/RepoManager.exe"
        }
    }
    finally {
        $zipArchive.Dispose()
    }
}
finally {
    $archiveStream.Dispose()
}

$archive = Get-Item -LiteralPath $archivePath
$hash = Get-FileHash -LiteralPath $archivePath -Algorithm SHA256
$bundleBytes = (Get-ChildItem -LiteralPath $bundlePath -Recurse -File |
    Measure-Object -Property Length -Sum).Sum
$sourceHead = $buildHead
$finalSourceJson = & $buildPython -I (Join-Path $PSScriptRoot 'source_provenance.py')
if ($LASTEXITCODE -ne 0) { throw 'Final source identity could not be established.' }
$finalSourceEvidence = $finalSourceJson | ConvertFrom-Json
if ($finalSourceEvidence.head -ne $buildHead -or $finalSourceEvidence.dirty -ne $buildDirty) {
    throw 'Source identity changed during packaging.'
}
$sourceDirty = $finalSourceEvidence.dirty
$pyInstallerVersion = (& $buildPython -m PyInstaller --version).Trim()
$manifest = [ordered]@{
    schema_version = 1
    product = "RepoManager"
    version = $productVersion
    platform = "windows-x64"
    distribution = "unsigned-portable-onedir"
    presentation = $Presentation
    source_head = $sourceHead.Trim()
    source_dirty = $sourceDirty
    pyinstaller_version = $pyInstallerVersion
    python_version = $runtime.python_version
    python_implementation = $requested.implementation
    python_architecture = "x64"
    python_free_threaded = $requested.free_threaded
    tcl_version = $runtime.tcl_version
    tk_version = $runtime.tk_version
    qt_version = $runtime.qt_version
    pyside_version = $runtime.pyside_version
    build_dependencies = $runtime.build_dependencies
    licenses = $runtime.licenses
    build_requirements_sha256 = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
    bootstrap_requirements_sha256 = (Get-FileHash -LiteralPath $bootstrap -Algorithm SHA256).Hash
    qt_requirements_sha256 = if ($Presentation -eq 'Qt') { (Get-FileHash -LiteralPath $qtRequirements -Algorithm SHA256).Hash } else { $null }
    third_party_sources_archive = if ($Presentation -eq 'Qt') { [System.IO.Path]::GetFileName($sourcesArchive) } else { $null }
    third_party_sources_sha256 = if ($Presentation -eq 'Qt') { (Get-FileHash -LiteralPath $sourcesArchive -Algorithm SHA256).Hash } else { $null }
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
