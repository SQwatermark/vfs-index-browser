param(
    [string]$VisualStudioPath,
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$workerRoot = Split-Path $PSScriptRoot -Parent
$lock = Get-Content (Join-Path $workerRoot 'dependencies.lock.json') -Raw | ConvertFrom-Json
$acl = $lock.dependencies | Where-Object id -eq 'acl'
$rtm = $lock.dependencies | Where-Object id -eq 'rtm'
if ($null -eq $acl -or $null -eq $rtm) {
    throw 'dependencies.lock.json 缺少 acl 或 rtm。'
}

$aclRoot = Join-Path $workerRoot ".deps\acl\$($acl.commit)"
$rtmRoot = Join-Path $workerRoot ".deps\rtm\$($rtm.commit)"
foreach ($dependencyRoot in @($aclRoot, $rtmRoot)) {
    if (-not (Test-Path (Join-Path $dependencyRoot '.vfs-dependency.json'))) {
        throw "锁定依赖尚未恢复：$dependencyRoot；请先运行 Initialize.ps1。"
    }
}

if (-not $VisualStudioPath) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path $vswhere)) {
        throw 'Visual Studio Installer\vswhere.exe 不存在。'
    }
    $VisualStudioPath = & $vswhere -latest -products * `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -property installationPath
}
if (-not $VisualStudioPath) {
    throw '未找到带 C++ x64 工具链的 Visual Studio。'
}

$outputRoot = if ($OutputDirectory) {
    [IO.Path]::GetFullPath($OutputDirectory)
} else {
    Join-Path $workerRoot 'artifacts\native\x64'
}
$intermediateRoot = Join-Path $workerRoot 'artifacts\native\obj\x64'
New-Item -ItemType Directory -Force $outputRoot, $intermediateRoot | Out-Null
$output = Join-Path $outputRoot 'acl_endfield.dll'
$source = Join-Path $workerRoot 'vendor\animestudio\AnimeStudio.ACLNative\api.cpp'
$vcvars = Join-Path $VisualStudioPath 'VC\Auxiliary\Build\vcvars64.bat'
$arguments = @(
    '/nologo',
    '/std:c++17',
    '/EHsc',
    '/O2',
    '/MT',
    '/DNDEBUG',
    '/LD',
    "/I `"$(Join-Path $aclRoot 'includes')`"",
    "/I `"$(Join-Path $rtmRoot 'includes')`"",
    "`"$source`"",
    '/link',
    "/out:`"$output`""
) -join ' '
$commandFile = Join-Path $env:TEMP "build-vfs-acl-endfield-$([guid]::NewGuid().ToString('N')).cmd"
try {
    $commandLines = @(
        '@chcp 65001 >nul',
        "@call `"$vcvars`" >nul",
        "@pushd `"$intermediateRoot`"",
        "@cl $arguments",
        '@set BUILD_EXIT_CODE=%ERRORLEVEL%',
        '@popd',
        '@exit /b %BUILD_EXIT_CODE%'
    )
    [IO.File]::WriteAllLines(
        $commandFile,
        $commandLines,
        (New-Object Text.UTF8Encoding($false))
    )
    & $env:COMSPEC /d /c $commandFile
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $output)) {
        throw 'acl_endfield.dll 构建失败。'
    }

    $dumpbin = Get-ChildItem `
        -Path (Join-Path $VisualStudioPath 'VC\Tools\MSVC\*\bin\Hostx64\x64\dumpbin.exe') `
        -File |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($null -eq $dumpbin) {
        throw '未找到 x64 dumpbin.exe，无法验证 acl_endfield.dll 的运行时依赖。'
    }
    $dependencyReport = & $dumpbin.FullName /nologo /dependents $output | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw 'dumpbin 无法读取 acl_endfield.dll 的运行时依赖。'
    }
    $dynamicCppRuntime = [regex]::Match(
        $dependencyReport,
        '(?i)\b(?:VCRUNTIME\d*(?:_\d+)?|MSVCP\d+|CONCRT\d+|ucrtbase|api-ms-win-crt-[\w-]+)\.dll\b'
    )
    if ($dynamicCppRuntime.Success) {
        throw "acl_endfield.dll 意外依赖动态 C/C++ 运行时：$($dynamicCppRuntime.Value)"
    }
}
finally {
    Remove-Item -LiteralPath $commandFile -Force -ErrorAction SilentlyContinue
}
Write-Host "已生成：$output"
