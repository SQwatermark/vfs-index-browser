[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseDirectory,

    [string]$DataRoot,
    [ValidateRange(1, 65535)]
    [int]$Port = 18765,
    [ValidateRange(1, 120)]
    [int]$StartupTimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
$ReleaseRoot = (Resolve-Path $ReleaseDirectory).Path
$ServerExe = Join-Path $ReleaseRoot "endfield-vfs-browser.exe"
$WorkerExe = Join-Path $ReleaseRoot "unity-worker\artifacts\Vfs.UnityWorker.exe"
$ReleaseMetadataPath = Join-Path $ReleaseRoot "release.json"

function Assert-ReleaseFile {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $Path = Join-Path $ReleaseRoot $RelativePath
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Release file is missing: $RelativePath"
    }
    return $Path
}

@(
    "endfield-vfs-browser.exe",
    "release.json",
    "public/index.html",
    "schemas/memorypack-known-schema.json",
    "schemas/memorypack-known-unions.json",
    "unity-worker/artifacts/Vfs.UnityWorker.exe",
    "unity-worker/artifacts/x64/acl_endfield.dll",
    "unity-worker/THIRD_PARTY_NOTICES.md",
    "unity-worker/third-party/licenses/AnimeStudio/LICENSE"
) | ForEach-Object { Assert-ReleaseFile $_ | Out-Null }

$ReleaseMetadata = Get-Content -LiteralPath $ReleaseMetadataPath -Raw | ConvertFrom-Json
if ($ReleaseMetadata.schemaVersion -ne 1 -or $ReleaseMetadata.target -ne "win-x64") {
    throw "Unsupported release metadata"
}
if (-not $ReleaseMetadata.gitCommit -or -not $ReleaseMetadata.workerVersion) {
    throw "Release metadata is missing build identity"
}
if (@($ReleaseMetadata.files).Count -eq 0) {
    throw "Release metadata is missing the immutable file manifest"
}

$ManifestFiles = @{}
foreach ($File in $ReleaseMetadata.files) {
    if (-not $File.path -or $File.path -eq "release.json" -or $File.path.StartsWith("data/")) {
        throw "Release metadata contains an invalid file path: $($File.path)"
    }
    if ($ManifestFiles.ContainsKey($File.path)) {
        throw "Release metadata contains a duplicate file path: $($File.path)"
    }
    $ManifestFiles[$File.path] = $File
}
$ActualFiles = @(
    Get-ChildItem -LiteralPath $ReleaseRoot -File -Recurse |
        ForEach-Object {
            [System.IO.Path]::GetRelativePath($ReleaseRoot, $_.FullName).Replace("\", "/")
        } |
        Where-Object { $_ -ne "release.json" -and -not $_.StartsWith("data/") } |
        Sort-Object
)
$ManifestPaths = @($ManifestFiles.Keys | Sort-Object)
$FileSetDifference = @(Compare-Object $ManifestPaths $ActualFiles)
if ($FileSetDifference.Count -ne 0) {
    throw "Release file set does not match release.json: $($FileSetDifference | ConvertTo-Json -Compress)"
}
foreach ($RelativePath in $ManifestPaths) {
    $File = Get-Item -LiteralPath (Join-Path $ReleaseRoot $RelativePath)
    $Expected = $ManifestFiles[$RelativePath]
    if ($File.Length -ne $Expected.size) {
        throw "Release file size mismatch: $RelativePath"
    }
    $ActualHash = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ActualHash -ne $Expected.sha256) {
        throw "Release file hash mismatch: $RelativePath"
    }
}

$HandshakeResponse = & $WorkerExe handshake | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $HandshakeResponse.ok) {
    throw "Unity worker handshake failed"
}
$Handshake = $HandshakeResponse.result
if ($Handshake.protocol.name -ne "vfs-unity-worker") {
    throw "Unexpected worker protocol: $($Handshake.protocol.name)"
}
if ($Handshake.protocol.version -ne $ReleaseMetadata.workerProtocol) {
    throw "Worker protocol does not match release.json"
}
if ($Handshake.workerVersion -ne $ReleaseMetadata.workerVersion) {
    throw "Worker version does not match release.json"
}

$Result = [ordered]@{
    release = $ReleaseRoot
    gitCommit = $ReleaseMetadata.gitCommit
    workerProtocol = $Handshake.protocol.version
    workerVersion = $Handshake.workerVersion
    workerCapabilityCount = @($Handshake.capabilities).Count
    verifiedFileCount = $ManifestPaths.Count
    service = "notRun"
}

if ($DataRoot) {
    $ResolvedDataRoot = (Resolve-Path $DataRoot).Path
    $EnvironmentNames = @(
        "VFS_BROWSER_DATA_ROOT",
        "VFS_BROWSER_HOST",
        "VFS_BROWSER_PORT",
        "VFS_BROWSER_LOG_FORMAT"
    )
    $SavedEnvironment = @{}
    foreach ($Name in $EnvironmentNames) {
        $SavedEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
    }

    $LogId = [Guid]::NewGuid().ToString("N")
    $StdoutPath = Join-Path ([System.IO.Path]::GetTempPath()) "vfs-release-$LogId.out.log"
    $StderrPath = Join-Path ([System.IO.Path]::GetTempPath()) "vfs-release-$LogId.err.log"
    $Process = $null
    try {
        [Environment]::SetEnvironmentVariable("VFS_BROWSER_DATA_ROOT", $ResolvedDataRoot, "Process")
        [Environment]::SetEnvironmentVariable("VFS_BROWSER_HOST", "127.0.0.1", "Process")
        [Environment]::SetEnvironmentVariable("VFS_BROWSER_PORT", "$Port", "Process")
        [Environment]::SetEnvironmentVariable("VFS_BROWSER_LOG_FORMAT", "text", "Process")

        $Process = Start-Process `
            -FilePath $ServerExe `
            -ArgumentList @("--no-auto-rebuild") `
            -PassThru `
            -WindowStyle Hidden `
            -RedirectStandardOutput $StdoutPath `
            -RedirectStandardError $StderrPath

        $Deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
        $Health = $null
        while ([DateTime]::UtcNow -lt $Deadline) {
            if ($Process.HasExited) {
                $Stdout = Get-Content -LiteralPath $StdoutPath -Raw -ErrorAction SilentlyContinue
                $Stderr = Get-Content -LiteralPath $StderrPath -Raw -ErrorAction SilentlyContinue
                throw "Released service exited early ($($Process.ExitCode)). stdout=$Stdout stderr=$Stderr"
            }
            try {
                $Health = Invoke-RestMethod "http://127.0.0.1:$Port/api/health"
                break
            }
            catch {
                Start-Sleep -Milliseconds 250
            }
        }
        if ($null -eq $Health) {
            throw "Released service did not become ready within $StartupTimeoutSeconds seconds"
        }

        $Page = Invoke-WebRequest "http://127.0.0.1:$Port/"
        if ($Page.StatusCode -ne 200 -or $Health.status -ne "ready") {
            throw "Released service health or home page is not ready"
        }
        if ($Health.unityWorker.status -ne "ready") {
            throw "Released service cannot use its packaged Unity worker"
        }
        if (@($Health.unityWorker.missingCapabilities).Count -ne 0) {
            throw "Released worker is missing required capabilities"
        }
        if (@($Health.legacyTools).Count -ne 0) {
            throw "Released service still declares legacy tools"
        }

        $WorkerCommand = [System.IO.Path]::GetFullPath($Health.unityWorker.command[0])
        $ReleasePrefix = $ReleaseRoot.TrimEnd("\", "/") + [System.IO.Path]::DirectorySeparatorChar
        if (-not $WorkerCommand.StartsWith(
            $ReleasePrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Service resolved a Unity worker outside the release: $WorkerCommand"
        }

        $Result.service = "ready"
        $Result.indexFreshness = $Health.indexFreshness.status
        $Result.manifestIndex = $Health.manifestIndex.status
        $Result.workerCommand = $WorkerCommand
    }
    finally {
        if ($null -ne $Process -and -not $Process.HasExited) {
            Stop-Process -Id $Process.Id
            $Process.WaitForExit()
        }
        foreach ($Name in $EnvironmentNames) {
            [Environment]::SetEnvironmentVariable($Name, $SavedEnvironment[$Name], "Process")
        }
        foreach ($LogPath in @($StdoutPath, $StderrPath)) {
            if (Test-Path -LiteralPath $LogPath -PathType Leaf) {
                Remove-Item -LiteralPath $LogPath -Force
            }
        }
    }
}

$Result | ConvertTo-Json
