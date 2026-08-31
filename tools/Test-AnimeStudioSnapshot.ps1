[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AnimeStudioPath
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$SnapshotRoot = Join-Path $RepositoryRoot "unity-worker\vendor\animestudio"
$UpstreamDocument = Get-Content `
    -LiteralPath (Join-Path $RepositoryRoot "unity-worker\UPSTREAM.md") `
    -Raw
$CommitMatch = [regex]::Match($UpstreamDocument, '权威提交：`([0-9a-f]{40})`')
if (-not $CommitMatch.Success) {
    throw "UPSTREAM.md does not contain an authoritative AnimeStudio commit"
}
$ExpectedCommit = $CommitMatch.Groups[1].Value
$ResolvedAnimeStudioPath = (Resolve-Path $AnimeStudioPath).Path

$ActualCommit = (& git -C $ResolvedAnimeStudioPath rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Cannot read AnimeStudio HEAD"
}
if ($ActualCommit -ne $ExpectedCommit) {
    throw "AnimeStudio HEAD does not match UPSTREAM.md: $ActualCommit"
}
$AnimeStudioStatus = @(& git -C $ResolvedAnimeStudioPath status --short)
if ($LASTEXITCODE -ne 0) {
    throw "Cannot read AnimeStudio worktree status"
}
if ($AnimeStudioStatus.Count -ne 0) {
    throw "AnimeStudio worktree is not clean: $($AnimeStudioStatus -join '; ')"
}

$SnapshotPrefix = "unity-worker/vendor/animestudio/"
$TrackedSnapshotFiles = @(& git -C $RepositoryRoot ls-files "$SnapshotPrefix*")
if ($LASTEXITCODE -ne 0 -or $TrackedSnapshotFiles.Count -eq 0) {
    throw "Cannot enumerate the tracked AnimeStudio snapshot"
}
$SnapshotMetadata = @("README.md", "PATCHES.md")
$TextExtensions = @(
    ".config", ".cpp", ".cs", ".csproj", ".h", ".json",
    ".md", ".props", ".ps1", ".txt"
)
$TextNames = @("COPYING", "LICENSE")
$Differences = @()
$ComparedCount = 0
foreach ($TrackedPath in $TrackedSnapshotFiles) {
    $RelativePath = $TrackedPath.Substring($SnapshotPrefix.Length)
    if ($RelativePath -in $SnapshotMetadata) {
        continue
    }
    $SnapshotPath = Join-Path $RepositoryRoot $TrackedPath
    $SourcePath = Join-Path $ResolvedAnimeStudioPath $RelativePath
    if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
        throw "AnimeStudio source file is missing: $RelativePath"
    }

    $Extension = [System.IO.Path]::GetExtension($RelativePath).ToLowerInvariant()
    if ($Extension -in $TextExtensions -or [System.IO.Path]::GetFileName($RelativePath) -in $TextNames) {
        $SnapshotContent = [System.IO.File]::ReadAllText($SnapshotPath).Replace("`r`n", "`n")
        $SourceContent = [System.IO.File]::ReadAllText($SourcePath).Replace("`r`n", "`n")
        $Different = $SnapshotContent -cne $SourceContent
    }
    else {
        $SnapshotHash = (Get-FileHash -LiteralPath $SnapshotPath -Algorithm SHA256).Hash
        $SourceHash = (Get-FileHash -LiteralPath $SourcePath -Algorithm SHA256).Hash
        $Different = $SnapshotHash -ne $SourceHash
    }
    if ($Different) {
        $Differences += $RelativePath.Replace("\", "/")
    }
    $ComparedCount += 1
}

$ExpectedPatches = @(
    "AnimeStudio/AssetMap.cs",
    "AnimeStudio/Classes/GameObject.cs"
)
$PatchDifference = @(Compare-Object ($ExpectedPatches | Sort-Object) ($Differences | Sort-Object))
if ($PatchDifference.Count -ne 0) {
    throw "Snapshot differences do not match PATCHES.md: $($PatchDifference | ConvertTo-Json -Compress)"
}

[ordered]@{
    animeStudioPath = $ResolvedAnimeStudioPath
    authoritativeCommit = $ExpectedCommit
    worktree = "clean"
    comparedFileCount = $ComparedCount
    documentedPatches = $Differences
} | ConvertTo-Json
