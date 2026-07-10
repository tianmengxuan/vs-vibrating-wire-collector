# VS Collector Windows in-place upgrade script.
# This file is intentionally ASCII-only for Windows PowerShell 5.1.

$ErrorActionPreference = 'Stop'

$payloadExeName = 'VSCollector_Update.exe'
$configFileNames = @('config.env', 'sites.json', 'formulas.json')
$scriptDir = if ($PSScriptRoot) {
    $PSScriptRoot
} else {
    Split-Path -Parent $MyInvocation.MyCommand.Path
}
$scriptDir = [System.IO.Path]::GetFullPath($scriptDir)
$logFilePath = Join-Path $scriptDir 'upgrade_vs_collector.log'

try {
    [System.IO.File]::WriteAllText($logFilePath, '', [System.Text.Encoding]::ASCII)
} catch {
}

function Write-Status {
    param([string]$Message)
    try {
        $line = '[upgrade] ' + $Message + [System.Environment]::NewLine
        [System.IO.File]::AppendAllText($script:logFilePath, $line, [System.Text.Encoding]::ASCII)
    } catch {
    }
}

function Get-AppExeName {
    $codePoints = @(0x632f, 0x5f26, 0x6570, 0x636e, 0x91c7, 0x96c6, 0x5668)
    $characters = foreach ($codePoint in $codePoints) {
        [char]$codePoint
    }
    return 'VS' + (-join $characters) + '.exe'
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
        Write-Status 'Exact process-path check unavailable; using process-name check.'
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

function Invoke-Upgrade {
    $appExeName = Get-AppExeName
    Set-Location -LiteralPath $scriptDir

    $currentExePath = Join-Path $scriptDir $appExeName
    $payloadExePath = Join-Path $scriptDir $payloadExeName

    Write-Status 'Upgrade started.'

    if ($payloadExeName -ieq $appExeName) {
        Write-Status 'Payload name conflicts with the installed executable.'
        return 1
    }

    if (-not (Test-Path -LiteralPath $currentExePath -PathType Leaf)) {
        Write-Status 'Installed executable was not found.'
        return 1
    }

    if (-not (Test-Path -LiteralPath $payloadExePath -PathType Leaf)) {
        Write-Status 'Update payload was not found.'
        return 1
    }

    if ((Get-Item -LiteralPath $payloadExePath).Length -le 0) {
        Write-Status 'Update payload is empty.'
        return 1
    }

    if (Test-AppRunning -TargetExePath $currentExePath -ExpectedExeName $appExeName) {
        Write-Status 'The installed application is still running. Close it and retry.'
        return 2
    }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $backupDir = Join-Path $scriptDir ('upgrade_backup_' + $timestamp)
    [System.IO.Directory]::CreateDirectory($backupDir) | Out-Null

    $backupExePath = Join-Path $backupDir $appExeName
    Copy-Item -LiteralPath $currentExePath -Destination $backupExePath -Force

    foreach ($configFileName in $configFileNames) {
        Copy-IfExists -SourcePath (Join-Path $scriptDir $configFileName) -BackupDir $backupDir
    }
    Write-Status 'Backup completed.'

    $tempOldExe = Join-Path $scriptDir ($appExeName + '.replacing_' + $timestamp + '.bak')

    try {
        Move-Item -LiteralPath $currentExePath -Destination $tempOldExe -Force
        Copy-Item -LiteralPath $payloadExePath -Destination $currentExePath -Force

        if ((-not (Test-Path -LiteralPath $currentExePath -PathType Leaf)) -or
            ((Get-Item -LiteralPath $currentExePath).Length -le 0)) {
            throw 'Replacement validation failed.'
        }

        Start-Process -FilePath $currentExePath -WorkingDirectory $scriptDir
        Remove-Item -LiteralPath $tempOldExe -Force -ErrorAction SilentlyContinue
        Write-Status 'Upgrade completed and the application was started.'
        return 0
    } catch {
        Write-Status 'Replacement failed. Restoring the installed executable.'
        try {
            Restore-OldExe -TempOldExe $tempOldExe -CurrentExePath $currentExePath -BackupExePath $backupExePath
            Write-Status 'Rollback completed. Configuration files were not changed.'
        } catch {
            Write-Status 'Rollback could not be completed automatically. Use the backup directory.'
        }
        return 1
    }
}

try {
    $exitCode = Invoke-Upgrade
} catch {
    Write-Status 'Upgrade failed before replacement. No error details were printed.'
    $exitCode = 1
}

exit $exitCode
