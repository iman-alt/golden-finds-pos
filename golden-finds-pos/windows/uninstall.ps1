# Removes the Golden Finds shortcuts, startup entry and nightly backup.
# Does NOT delete the shop's data or the backups.

$App = Split-Path -Parent $PSScriptRoot

foreach ($link in @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Golden Finds.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Startup')) 'Golden Finds (start till).lnk')
)) {
    if (Test-Path $link) { Remove-Item $link -Force; Write-Host "  removed $link" }
}

if (Get-ScheduledTask -TaskName 'Golden Finds backup' -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName 'Golden Finds backup' -Confirm:$false
    Write-Host "  removed the nightly backup"
}

Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
    $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -like 'python*') { Stop-Process -Id $proc.Id -Force }
}

Write-Host ""
Write-Host "  Golden Finds has been removed from startup and the desktop."
Write-Host "  The shop's data is still in: $(Join-Path $App 'instance')"
