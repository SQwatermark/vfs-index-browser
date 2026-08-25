param(
    [string]$Destination = (Join-Path (Split-Path $PSScriptRoot -Parent) '.deps')
)

$ErrorActionPreference = 'Stop'
$workerRoot = Split-Path $PSScriptRoot -Parent
$lockPath = Join-Path $workerRoot 'dependencies.lock.json'
$lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json

if ($lock.version -ne 1) {
    throw "不支持的依赖锁文件版本：$($lock.version)"
}

$destinationPath = [IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path $destinationPath | Out-Null

foreach ($dependency in $lock.dependencies) {
    $dependencyRoot = Join-Path $destinationPath $dependency.id
    $target = Join-Path $dependencyRoot $dependency.commit
    $marker = Join-Path $target '.vfs-dependency.json'
    if (Test-Path -LiteralPath $marker) {
        $installed = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
        if ($installed.commit -eq $dependency.commit -and $installed.sha256 -eq $dependency.sha256) {
            Write-Host "依赖已就绪：$($dependency.id)@$($dependency.commit)"
            continue
        }
        throw "依赖目录存在但身份不匹配，请人工检查后删除：$target"
    }
    if (Test-Path -LiteralPath $target) {
        throw "依赖目录不完整，请人工检查后删除：$target"
    }

    New-Item -ItemType Directory -Force -Path $dependencyRoot | Out-Null
    $archive = Join-Path $dependencyRoot "$($dependency.commit).zip"
    Write-Host "下载依赖：$($dependency.id)@$($dependency.commit)"
    Invoke-WebRequest -Uri $dependency.archiveUrl -OutFile $archive
    $actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $dependency.sha256) {
        throw "依赖校验失败：$($dependency.id)，期望 $($dependency.sha256)，实际 $actualHash"
    }

    $staging = Join-Path $dependencyRoot ".staging-$([guid]::NewGuid().ToString('N'))"
    try {
        Expand-Archive -LiteralPath $archive -DestinationPath $staging
        $roots = @(Get-ChildItem -LiteralPath $staging -Directory)
        if ($roots.Count -ne 1) {
            throw "依赖归档根目录数量异常：$($dependency.id)"
        }
        Move-Item -LiteralPath $roots[0].FullName -Destination $target
        @{
            id = $dependency.id
            source = $dependency.source
            commit = $dependency.commit
            sha256 = $dependency.sha256
        } | ConvertTo-Json | Set-Content -LiteralPath $marker -Encoding UTF8
    }
    finally {
        # staging 名称由本脚本生成，并且已经固定在当前依赖目录下。
        if (Test-Path -LiteralPath $staging) {
            Remove-Item -LiteralPath $staging -Recurse -Force
        }
        if (Test-Path -LiteralPath $archive) {
            Remove-Item -LiteralPath $archive -Force
        }
    }
    Write-Host "依赖已就绪：$($dependency.id)@$($dependency.commit)"
}
