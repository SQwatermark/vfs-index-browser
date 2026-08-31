param(
    [Parameter(Mandatory=$true)][string[]]$CharacterId,
    [Parameter(Mandatory=$true)][string]$OutputRoot,
    [string]$ServiceUrl = "http://127.0.0.1:8765"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$worker = Get-ChildItem `
    (Join-Path $repoRoot "unity-worker/src/Vfs.UnityWorker/bin/Release") `
    -Recurse -Filter Vfs.UnityWorker.exe |
    Where-Object { $_.FullName -notlike "*\\win-x64\\*" } |
    Select-Object -First 1
if ($null -eq $worker) {
    throw "Vfs.UnityWorker.exe is unavailable; build unity-worker in Release first"
}

$outputRootPath = [IO.Path]::GetFullPath($OutputRoot)
[IO.Directory]::CreateDirectory($outputRootPath) | Out-Null

foreach ($id in $CharacterId) {
    if ($id -notmatch '^chr_[0-9]{4}_[a-z0-9]+$') {
        throw "invalid character id: $id"
    }
    $artifactPath = Join-Path $outputRootPath "$id.runtime-template.json"
    if (Test-Path -LiteralPath $artifactPath) {
        Write-Output "Skipped existing CharacterTemplate: $id"
        continue
    }

    $assetName = "data_$id.asset"
    $lookupUrl = "$ServiceUrl/api/manifest-assets/by-name?name=$([Uri]::EscapeDataString($assetName))"
    $lookup = Invoke-RestMethod $lookupUrl
    if (@($lookup.candidates).Count -ne 1) {
        throw "${id}: expected exactly one manifest asset, found $(@($lookup.candidates).Count)"
    }
    $candidate = @($lookup.candidates)[0]
    $bundleName = [string]$candidate.bundleName
    $searchUrl = "$ServiceUrl/api/search?scope=all&q=$([Uri]::EscapeDataString($bundleName))&limit=20"
    $bundleMatches = @((Invoke-RestMethod $searchUrl).items) |
        Where-Object { $_.chunk_exists -eq 1 -and $_.path.EndsWith($bundleName) }
    if ($bundleMatches.Count -ne 1) {
        throw "${id}: expected exactly one readable bundle, found $($bundleMatches.Count)"
    }

    $workRoot = Join-Path $outputRootPath ".work-$id"
    if (Test-Path -LiteralPath $workRoot) {
        throw "${id}: stale work directory exists: $workRoot"
    }
    [IO.Directory]::CreateDirectory($workRoot) | Out-Null
    try {
        $bundlePath = Join-Path $workRoot "source.ab"
        Invoke-WebRequest "$ServiceUrl/api/raw?id=$($bundleMatches[0].id)" -OutFile $bundlePath
        $rawOutput = Join-Path $workRoot "raw"
        $request = @{
            protocolVersion = "1.0.0"
            requestId = "character-template-$id"
            operation = "exportMonoBehaviourRaw"
            arguments = @{
                inputPath = $bundlePath
                outputDirectory = $rawOutput
                container = [string]$candidate.path
            }
        }
        $requestPath = Join-Path $workRoot "request.json"
        [IO.File]::WriteAllText(
            $requestPath,
            ($request | ConvertTo-Json -Depth 8),
            [Text.UTF8Encoding]::new($false)
        )
        $response = (& $worker.FullName request $requestPath | ConvertFrom-Json)
        if (!$response.ok -or @($response.result.artifacts).Count -ne 1) {
            throw "${id}: raw MonoBehaviour export failed: $($response | ConvertTo-Json -Depth 8 -Compress)"
        }
        $rawPath = Join-Path $rawOutput $response.result.artifacts[0].relativePath
        & (Join-Path $PSScriptRoot "export_character_template.ps1") `
            -InputPath $rawPath -ExpectedId $id -OutputPath $artifactPath
        if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $artifactPath)) {
            throw "${id}: CharacterTemplate decode failed"
        }
    }
    finally {
        if (Test-Path -LiteralPath $workRoot) {
            Remove-Item -LiteralPath $workRoot -Recurse -Force
        }
    }
}
