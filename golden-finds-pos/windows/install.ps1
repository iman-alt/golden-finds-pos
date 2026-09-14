# Golden Finds installer for Windows.
#
# Safe to run again at any time: it is also how an update is applied.
# It never deletes or replaces the shop's data in the instance folder.
#
# -SkipSystem  checks Python and installs libraries only, without touching
#              shortcuts, startup, scheduled tasks or the browser. For testing.

param([switch]$SkipSystem)

$ErrorActionPreference = 'Stop'
$App = Split-Path -Parent $PSScriptRoot          # ...\golden-finds-pos
$Root = Split-Path -Parent $App                  # the unzipped folder
$Venv = Join-Path $App '.venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$Instance = Join-Path $App 'instance'
$LocalEnv = Join-Path $Instance 'local.env'
$Icon = Join-Path $PSScriptRoot 'golden-finds.ico'

function Say($text)  { Write-Host "  $text" }
function Ok($text)   { Write-Host "  [ok] $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  [!]  $text" -ForegroundColor Yellow }
function Fail($text) {
    Write-Host ""
    Write-Host "  [x]  $text" -ForegroundColor Red
    Write-Host ""
    exit 1
}

# ------------------------------------------------------------- Python --

function Find-Python {
    # 'py' is the Windows launcher; 'python' may be the real thing or the
    # Microsoft Store placeholder, which runs but prints nothing.
    foreach ($candidate in @(@('py', '-3'), @('python'))) {
        $exe = $candidate[0]
        $extra = @($candidate | Select-Object -Skip 1)
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $version = & $exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        } catch { continue }
        if ($LASTEXITCODE -eq 0 -and "$version" -match '^3\.(\d+)$' -and [int]$Matches[1] -ge 10) {
            return ,$candidate
        }
    }
    return $null
}

Say "Checking for Python..."
$python = Find-Python
if (-not $python) {
    Start-Process 'https://www.python.org/downloads/windows/'
    Fail ("Python is not installed. The Python website has opened: download it, " +
          "and on the first installer screen TICK 'Add python.exe to PATH'. " +
          "Then double-click 'Install Golden Finds' again.")
}
Ok "Python found"

# ------------------------------------------ stop a running till (update) --

$listening = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
foreach ($conn in $listening) {
    $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -like 'python*') {
        Say "Stopping the till so it can be updated..."
        Stop-Process -Id $proc.Id -Force
        Start-Sleep -Seconds 1
    }
}

# ---------------------------------------- back up before changing things --

$Database = Join-Path $Instance 'golden_finds.db'
if ((Test-Path $Database) -and (Test-Path $VenvPython)) {
    Say "Backing up the shop's data before updating..."
    Push-Location $App
    try {
        & $VenvPython -m flask --app run backup | Out-Null
        if ($LASTEXITCODE -eq 0) { Ok "Data backed up" } else { Warn "Backup before update did not complete" }
    } finally { Pop-Location }
}

# ---------------------------------------------------------- libraries --

if (-not (Test-Path $VenvPython)) {
    Say "Setting up Golden Finds' own copy of Python..."
    & $python[0] @($python | Select-Object -Skip 1) -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Fail "Could not set up Python for Golden Finds." }
}

Say "Installing what Golden Finds needs (needs internet the first time)..."
$requirements = Join-Path $App 'requirements.txt'
$wheels = @((Join-Path $Root 'wheels'), (Join-Path $App 'wheels')) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($wheels) {
    & $VenvPython -m pip install --disable-pip-version-check -q --no-index --find-links $wheels -r $requirements
} else {
    & $VenvPython -m pip install --disable-pip-version-check -q -r $requirements
}
if ($LASTEXITCODE -ne 0) {
    Fail "Could not install the libraries. Check the internet connection and run the installer again."
}
Ok "Libraries installed"

& $VenvPython (Join-Path $PSScriptRoot 'make_icon.py') $Icon
if ($LASTEXITCODE -eq 0) { Ok "Icon ready" }

if ($SkipSystem) {
    Ok "Test run finished (shortcuts, startup and backups were skipped)."
    exit 0
}

# ------------------------------------------------------- backup folder --

New-Item -ItemType Directory -Force -Path $Instance | Out-Null
$hasBackupDir = (Test-Path $LocalEnv) -and (Select-String -Path $LocalEnv -Pattern '^\s*BACKUP_DIR=' -Quiet)
if (-not $hasBackupDir) {
    # Prefer a folder that syncs to the cloud, so a stolen laptop does not
    # take the backups with it.
    $cloud = @($env:OneDrive, (Join-Path $env:USERPROFILE 'Google Drive\My Drive'), 'G:\My Drive') |
        Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if ($cloud) {
        $backupDir = Join-Path $cloud 'Golden Finds backups'
    } else {
        $backupDir = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Golden Finds backups'
    }
    Add-Content -Path $LocalEnv -Encoding ASCII -Value @(
        '# Settings for this computer. Kept when Golden Finds is updated.',
        "BACKUP_DIR=$backupDir"
    )
}
$backupDir = ((Get-Content $LocalEnv | Where-Object { $_ -match '^\s*BACKUP_DIR=' } | Select-Object -Last 1) -split '=', 2)[1].Trim()
Ok "Backups go to: $backupDir"

# ---------------------------------------------------------- shortcuts --

$shell = New-Object -ComObject WScript.Shell
$wscript = Join-Path $env:WINDIR 'System32\wscript.exe'
$launcher = Join-Path $PSScriptRoot 'launch.vbs'

function New-Shortcut($path, $arguments) {
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $wscript
    $link.Arguments = $arguments
    $link.WorkingDirectory = $App
    if (Test-Path $Icon) { $link.IconLocation = $Icon }
    $link.Description = 'Golden Finds till'
    $link.Save()
}

$desktop = [Environment]::GetFolderPath('Desktop')
New-Shortcut (Join-Path $desktop 'Golden Finds.lnk') "`"$launcher`""
Ok "Desktop shortcut created"

$startup = [Environment]::GetFolderPath('Startup')
New-Shortcut (Join-Path $startup 'Golden Finds (start till).lnk') "`"$launcher`" /background"
Ok "The till will start by itself when the computer turns on"

# ---------------------------------------------------- nightly backups --

try {
    $action = New-ScheduledTaskAction -Execute $wscript -Argument "`"$(Join-Path $PSScriptRoot 'backup.vbs')`""
    $trigger = New-ScheduledTaskTrigger -Daily -At '9:00 PM'
    # If the laptop is off at 9pm, the backup runs as soon as it is back on.
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName 'Golden Finds backup' -Action $action -Trigger $trigger `
        -Settings $settings -Force | Out-Null
    Ok "Nightly backup scheduled for 9pm"
} catch {
    Warn "Could not schedule the nightly backup: $($_.Exception.Message)"
}

# --------------------------------------------------------------- start --

Say "Starting the till..."
Start-Process $wscript -ArgumentList "`"$launcher`""

Write-Host ""
Write-Host "  Golden Finds is installed." -ForegroundColor Green
Write-Host ""
Say "From now on: double-click 'Golden Finds' on the desktop."
Say "The till also starts by itself whenever this computer turns on."
Say "Your shop's data is kept in: $Instance"
Write-Host ""
