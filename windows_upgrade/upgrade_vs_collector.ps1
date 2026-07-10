# VS Collector Windows in-place upgrade script.
# Saved as UTF-8 BOM so Windows PowerShell 5.1 reads Chinese EXE names correctly.
# Put this script, upgrade_vs_collector.cmd, and VS振弦数据采集器_新版.exe
# in the existing program directory, then run the .cmd file.

$ErrorActionPreference = 'Stop'

$appExeName = 'VS振弦数据采集器.exe'
$payloadExeName = 'VS振弦数据采集器_新版.exe'
$configFileNames = @('config.env', 'sites.json', 'formulas.json')

function Write-Step {
    param([string]$Message)
    Write-Host "[升级] $Message"
}

function Get-NormalizedPath {
    param([string]$Path)
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Test-AppRunning {
    param(
        [string]$TargetExePath,
        [string]$ExpectedExeName
    )

    $target = Get-NormalizedPath $TargetExePath

    try {
        $matches = Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $_.ExecutablePath -and ((Get-NormalizedPath $_.ExecutablePath) -ieq $target)
        }
        if ($matches) {
            return $true
        }
    } catch {
        Write-Step '无法精确读取进程路径，改用进程名做保守检测。'
    }

    $processName = [System.IO.Path]::GetFileNameWithoutExtension($ExpectedExeName)
    try {
        return [bool](Get-Process -Name $processName -ErrorAction SilentlyContinue)
    } catch {
        return $false
    }
}

function Copy-IfExists {
    param(
        [string]$SourcePath,
        [string]$BackupDir
    )

    if (Test-Path -LiteralPath $SourcePath -PathType Leaf) {
        Copy-Item -LiteralPath $SourcePath -Destination $BackupDir -Force
        Write-Step "已备份 $([System.IO.Path]::GetFileName($SourcePath))"
    } else {
        Write-Step "未找到 $([System.IO.Path]::GetFileName($SourcePath))，跳过该文件备份"
    }
}

function Restore-OldExe {
    param(
        [string]$TempOldExe,
        [string]$CurrentExePath,
        [string]$BackupExePath
    )

    if (Test-Path -LiteralPath $TempOldExe -PathType Leaf) {
        if (Test-Path -LiteralPath $CurrentExePath -PathType Leaf) {
            Remove-Item -LiteralPath $CurrentExePath -Force -ErrorAction SilentlyContinue
        }
        Move-Item -LiteralPath $TempOldExe -Destination $CurrentExePath -Force
        return
    }

    if ((-not (Test-Path -LiteralPath $CurrentExePath -PathType Leaf)) -and
        (Test-Path -LiteralPath $BackupExePath -PathType Leaf)) {
        Copy-Item -LiteralPath $BackupExePath -Destination $CurrentExePath -Force
    }
}

$scriptDir = if ($PSScriptRoot) {
    $PSScriptRoot
} else {
    Split-Path -Parent $MyInvocation.MyCommand.Path
}
$scriptDir = [System.IO.Path]::GetFullPath($scriptDir)
Set-Location -LiteralPath $scriptDir

$currentExePath = Join-Path $scriptDir $appExeName
$payloadExePath = Join-Path $scriptDir $payloadExeName

Write-Step "当前程序目录: $scriptDir"

if ($payloadExeName -ieq $appExeName) {
    Write-Host '[升级] 新版载荷文件名不能与旧 EXE 相同。'
    exit 1
}

if (-not (Test-Path -LiteralPath $currentExePath -PathType Leaf)) {
    Write-Host "[升级] 未找到旧版 EXE: $currentExePath"
    Write-Host '[升级] 请把升级脚本放到现有程序目录后再运行。'
    exit 1
}

if (-not (Test-Path -LiteralPath $payloadExePath -PathType Leaf)) {
    Write-Host "[升级] 未找到新版载荷: $payloadExePath"
    Write-Host "[升级] 请将新版 EXE 重命名为 $payloadExeName 后放到同一目录。"
    exit 1
}

if ((Get-Item -LiteralPath $payloadExePath).Length -le 0) {
    Write-Host '[升级] 新版载荷大小为 0，停止升级。'
    exit 1
}

if (Test-AppRunning -TargetExePath $currentExePath -ExpectedExeName $appExeName) {
    Write-Host '[升级] 检测到程序仍在运行。'
    Write-Host '[升级] 请先正常关闭 VS振弦数据采集器，再重新运行升级脚本。'
    exit 2
}

$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backupDir = Join-Path $scriptDir "upgrade_backup_$timestamp"
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

$backupExePath = Join-Path $backupDir $appExeName
Copy-Item -LiteralPath $currentExePath -Destination $backupExePath -Force
Write-Step "已备份旧 EXE 到: $backupDir"

foreach ($configFileName in $configFileNames) {
    Copy-IfExists -SourcePath (Join-Path $scriptDir $configFileName) -BackupDir $backupDir
}

$tempOldExe = Join-Path $scriptDir "$appExeName.replacing_$timestamp.bak"

try {
    Write-Step '开始替换 EXE，配置文件不会被覆盖或修改。'
    Move-Item -LiteralPath $currentExePath -Destination $tempOldExe -Force
    Copy-Item -LiteralPath $payloadExePath -Destination $currentExePath -Force

    if ((-not (Test-Path -LiteralPath $currentExePath -PathType Leaf)) -or
        ((Get-Item -LiteralPath $currentExePath).Length -le 0)) {
        throw '替换后的 EXE 不存在或大小为 0。'
    }

    Remove-Item -LiteralPath $tempOldExe -Force -ErrorAction SilentlyContinue
    Write-Step '升级成功，正在启动新版程序。'
    Start-Process -FilePath $currentExePath -WorkingDirectory $scriptDir
    exit 0
} catch {
    Write-Host "[升级] 替换失败: $($_.Exception.Message)"
    Restore-OldExe -TempOldExe $tempOldExe -CurrentExePath $currentExePath -BackupExePath $backupExePath
    Write-Host '[升级] 已尝试恢复旧版 EXE。config.env、sites.json、formulas.json 未被修改。'
    exit 1
}
