param(
    [Parameter(Mandatory=$true)][string]$InputPath,
    [Parameter(Mandatory=$true)][string]$ExpectedId,
    [Parameter(Mandatory=$true)][string]$OutputPath
)
$ErrorActionPreference = 'Stop'
$taskAssembly = Join-Path $PSScriptRoot '../unity-worker/src/Endfield.Extensions/bin/Release/net9.0/Vfs.Endfield.Extensions.dll'
[Reflection.Assembly]::LoadFrom([IO.Path]::GetFullPath($taskAssembly)) | Out-Null
$taskRaw = [IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $InputPath).Path)
$taskResult = [Vfs.Endfield.Extensions.CharacterTemplateDecoder]::Decode($taskRaw, $ExpectedId)
$taskBytes = [Text.UTF8Encoding]::new($false).GetBytes(($taskResult | ConvertTo-Json -Depth 64) + "`n")
# 所有解析成功后才创建新文件；拒绝覆盖既有证据。调用方把中间产物放到 Git 忽略的 tmp/。
$taskStream = [IO.File]::Open([IO.Path]::GetFullPath($OutputPath), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
try { $taskStream.Write($taskBytes, 0, $taskBytes.Length) } finally { $taskStream.Dispose() }
Write-Output "Exported partial CharacterTemplate: $ExpectedId ($($taskRaw.Length) raw bytes)"
