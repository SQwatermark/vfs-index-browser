param(
    [string]$DotnetExe = 'dotnet'
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

# 初始化只恢复锁定的构建依赖；运行服务时不会隐式联网。
& (Join-Path $PSScriptRoot 'tools\Restore-Dependencies.ps1')
& $DotnetExe restore (Join-Path $PSScriptRoot 'Vfs.UnityWorker.slnx')
