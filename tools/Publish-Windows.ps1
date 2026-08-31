[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [string]$PythonExe = "python",
    [string]$DotnetExe = "dotnet",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonCommand = (Get-Command $PythonExe -ErrorAction Stop).Source
$DotnetCommand = (Get-Command $DotnetExe -ErrorAction Stop).Source
$OutputPath = [System.IO.Path]::GetFullPath(
    (Join-Path (Get-Location).Path $OutputDirectory)
)

if (Test-Path -LiteralPath $OutputPath) {
    throw "Output directory already exists: $OutputPath"
}

$BuildId = [Guid]::NewGuid().ToString("N")
$BuildRoot = Join-Path $RepositoryRoot ".tmp\publish-build-$BuildId"
$DistRoot = Join-Path $BuildRoot "dist"
$WorkRoot = Join-Path $BuildRoot "work"
$SpecRoot = Join-Path $BuildRoot "spec"
$Published = $false

New-Item -ItemType Directory -Path $BuildRoot | Out-Null

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Executable $($Arguments -join ' ')"
    }
}

function Copy-RepositoryFile {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $Source = Join-Path $RepositoryRoot $RelativePath
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Required repository file is missing: $RelativePath"
    }
    $Destination = Join-Path $ReleaseRoot $RelativePath
    $Parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Force -Path $Parent | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination
}

Push-Location $RepositoryRoot
try {
    Invoke-Checked $PythonCommand @("-m", "PyInstaller", "--version")

    & (Join-Path $RepositoryRoot "unity-worker\Initialize.ps1") `
        -DotnetExe $DotnetCommand
    & (Join-Path $RepositoryRoot "unity-worker\tools\Build-EndfieldAcl.ps1")

    if (-not $SkipTests) {
        Invoke-Checked $PythonCommand @("-m", "unittest", "discover", "-s", "tests")
        Push-Location (Join-Path $RepositoryRoot "unity-worker")
        try {
            Invoke-Checked $DotnetCommand @("test", "Vfs.UnityWorker.slnx", "-c", "Release")
        }
        finally {
            Pop-Location
        }
    }

    Invoke-Checked $PythonCommand @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name", "endfield-vfs-browser",
        "--distpath", $DistRoot,
        "--workpath", $WorkRoot,
        "--specpath", $SpecRoot,
        "--hidden-import", "tools.decode_memorypack_json",
        "server.py"
    )

    $ReleaseRoot = Join-Path $DistRoot "endfield-vfs-browser"
    $ServerExe = Join-Path $ReleaseRoot "endfield-vfs-browser.exe"
    if (-not (Test-Path -LiteralPath $ServerExe -PathType Leaf)) {
        throw "PyInstaller did not produce the expected executable: $ServerExe"
    }

    $TrackedRuntimeFiles = @(
        & git ls-files -- public schemas unity-worker/third-party/licenses
    )
    if ($LASTEXITCODE -ne 0) {
        throw "git ls-files failed while collecting runtime assets"
    }
    foreach ($RelativePath in $TrackedRuntimeFiles) {
        Copy-RepositoryFile $RelativePath
    }

    @(
        "README.md",
        "requirements.txt",
        "docs/deployment/windows-release.md",
        "unity-worker/README.md",
        "unity-worker/THIRD_PARTY_NOTICES.md",
        "unity-worker/UPSTREAM.md",
        "tools/blender_import_model.py",
        "tools/blender_action_switcher.py",
        "character_lighting.py",
        "blender_materials.py",
        "blender_material_plan.py"
    ) | ForEach-Object { Copy-RepositoryFile $_ }

    New-Item -ItemType Directory -Path (Join-Path $ReleaseRoot "data") | Out-Null

    $WorkerOutput = Join-Path $ReleaseRoot "unity-worker\artifacts"
    Invoke-Checked $DotnetCommand @(
        "publish",
        "unity-worker/src/Vfs.UnityWorker/Vfs.UnityWorker.csproj",
        "-c", "Release",
        "-r", "win-x64",
        "--self-contained", "true",
        "-p:PublishSingleFile=false",
        "-o", $WorkerOutput
    )

    $WorkerExe = Join-Path $WorkerOutput "Vfs.UnityWorker.exe"
    $AclDll = Join-Path $WorkerOutput "x64\acl_endfield.dll"
    if (-not (Test-Path -LiteralPath $WorkerExe -PathType Leaf)) {
        throw "Worker publish did not produce Vfs.UnityWorker.exe"
    }
    if (-not (Test-Path -LiteralPath $AclDll -PathType Leaf)) {
        throw "Worker publish did not include x64/acl_endfield.dll"
    }

    Invoke-Checked $ServerExe @("--help")
    $HandshakeResponse = & $WorkerExe handshake | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $HandshakeResponse.ok) {
        throw "Published Unity worker handshake failed"
    }
    $Handshake = $HandshakeResponse.result

    $GitCommit = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "git rev-parse HEAD failed"
    }
    $ReleaseFiles = @(
        Get-ChildItem -LiteralPath $ReleaseRoot -File -Recurse |
            ForEach-Object {
                $RelativePath = [System.IO.Path]::GetRelativePath(
                    $ReleaseRoot,
                    $_.FullName
                ).Replace("\", "/")
                if (-not $RelativePath.StartsWith("data/")) {
                    [ordered]@{
                        path = $RelativePath
                        size = $_.Length
                        sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
                    }
                }
            } |
            Sort-Object { $_.path }
    )
    $ReleaseMetadata = [ordered]@{
        schemaVersion = 1
        product = "endfield-vfs-browser"
        gitCommit = $GitCommit
        target = "win-x64"
        python = (& $PythonCommand --version 2>&1 | Out-String).Trim()
        pyInstaller = (& $PythonCommand -m PyInstaller --version | Out-String).Trim()
        workerProtocol = $Handshake.protocol.version
        workerVersion = $Handshake.workerVersion
        files = $ReleaseFiles
    }
    $ReleaseMetadata | ConvertTo-Json | Set-Content `
        -LiteralPath (Join-Path $ReleaseRoot "release.json") `
        -Encoding utf8

    $OutputParent = Split-Path -Parent $OutputPath
    New-Item -ItemType Directory -Force -Path $OutputParent | Out-Null
    Move-Item -LiteralPath $ReleaseRoot -Destination $OutputPath
    $Published = $true
    Write-Host "Windows release published to $OutputPath"
}
finally {
    Pop-Location
    if ($Published -and (Test-Path -LiteralPath $BuildRoot)) {
        $ResolvedBuildRoot = [System.IO.Path]::GetFullPath($BuildRoot)
        $TemporaryRoot = [System.IO.Path]::GetFullPath(
            (Join-Path $RepositoryRoot ".tmp")
        ) + [System.IO.Path]::DirectorySeparatorChar
        if (-not $ResolvedBuildRoot.StartsWith(
            $TemporaryRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Refusing to clean build directory outside .tmp: $ResolvedBuildRoot"
        }
        Remove-Item -LiteralPath $ResolvedBuildRoot -Recurse -Force
    }
}
