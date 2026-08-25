param(
    [string]$VisualStudioPath
)

$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$repoDir = Split-Path $projectDir -Parent

if (-not $VisualStudioPath) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path $vswhere)) {
        throw 'Visual Studio Installer\vswhere.exe was not found.'
    }
    $VisualStudioPath = & $vswhere -latest -products * `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -property installationPath
}

if (-not $VisualStudioPath) {
    throw 'A Visual Studio installation with the C++ toolchain was not found.'
}

$builds = @(
    @{ Architecture = 'x86'; VcVars = 'vcvars32.bat' },
    @{ Architecture = 'x64'; VcVars = 'vcvars64.bat' }
)

foreach ($build in $builds) {
    $architecture = $build.Architecture
    $vcvars = Join-Path $VisualStudioPath "VC\Auxiliary\Build\$($build.VcVars)"
    $outputDir = Join-Path $repoDir "AnimeStudio.Libraries\$architecture"
    $output = Join-Path $outputDir 'acl_endfield.dll'
    $intermediateDir = Join-Path $projectDir "obj\$architecture"
    New-Item -ItemType Directory -Force $outputDir | Out-Null
    New-Item -ItemType Directory -Force $intermediateDir | Out-Null

    $arguments = @(
        '/nologo',
        '/std:c++17',
        '/EHsc',
        '/O2',
        '/MT',
        '/DNDEBUG',
        '/LD',
        "/I `"$projectDir\ThirdParty\acl`"",
        "/I `"$projectDir\ThirdParty\rtm`"",
        "`"$projectDir\api.cpp`"",
        '/link',
        "/out:`"$output`""
    ) -join ' '
    $commandFile = Join-Path $env:TEMP "build-acl-endfield-$architecture.cmd"
    try {
        Set-Content -LiteralPath $commandFile -Encoding ASCII -Value @(
            "@call `"$vcvars`" >nul",
            "@pushd `"$intermediateDir`"",
            "@cl $arguments",
            '@set BUILD_EXIT_CODE=%ERRORLEVEL%',
            '@popd',
            '@exit /b %BUILD_EXIT_CODE%'
        )
        & $env:COMSPEC /d /c $commandFile
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to build acl_endfield.dll for $architecture."
        }
    }
    finally {
        Remove-Item -LiteralPath $commandFile -Force -ErrorAction SilentlyContinue
    }
}
