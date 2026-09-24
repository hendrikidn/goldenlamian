$WshShell = New-Object -ComObject WScript.Shell
$DesktopPath = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)

$ScriptDir = $PSScriptRoot
$AdminRoot = Split-Path -Parent $ScriptDir

# HR Admin Portal Shortcut
$AdminLnk = Join-Path $DesktopPath "HR Admin Portal.lnk"
$Shortcut = $WshShell.CreateShortcut($AdminLnk)
$Shortcut.TargetPath = Join-Path $AdminRoot "HR Admin Portal.exe"
$Shortcut.WorkingDirectory = $AdminRoot
$IconFile = Join-Path $AdminRoot "img\HR_Portal.ico"
if (Test-Path $IconFile) {
    $Shortcut.IconLocation = $IconFile
} else {
    $Shortcut.IconLocation = Join-Path $AdminRoot "HR Admin Portal.exe"
}
$Shortcut.Description = "Launch HR Admin Portal"
$Shortcut.Save()
Write-Host "✅ Created HR Admin shortcut on Desktop: $AdminLnk"

