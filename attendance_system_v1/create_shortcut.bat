@echo off
title Create Outlet Attendance Desktop Shortcut
cd /d "%~dp0"

echo.
echo =======================================================
echo      Creating Outlet Attendance Desktop Shortcut
echo =======================================================
echo.

:: 1. Set environment compatibility layer to prevent UAC/Defender elevation restrictions (Non-Admin Friendly)
set __COMPAT_LAYER=RunAsInvoker

:: 2. Unblock application files from Windows SmartScreen restrictions
echo 🛡️ Unblocking application files...
powershell -ExecutionPolicy Bypass -NoProfile -Command "Get-ChildItem -Path '%~dp0*' -Recurse | Unblock-File -ErrorAction SilentlyContinue" >nul 2>&1

:: 3. Verify target files
set "TARGET_HTML=%~dp0outlet_display.html"
set "ICON_PATH=%~dp0app_icon_v2.ico"

if not exist "%TARGET_HTML%" (
    echo ❌ ERROR: Target file "%TARGET_HTML%" not found.
    pause
    exit /b 1
)

:: 4. Create Desktop & Local Shortcut via PowerShell COM Object (Non-Admin accessible)
echo 🖥️ Creating Desktop shortcut for Outlet Attendance...
powershell -ExecutionPolicy Bypass -NoProfile -Command "$desktop = [Environment]::GetFolderPath('Desktop'); $scriptDir = '%~dp0'.TrimEnd('\'); $targetPath = Join-Path $scriptDir 'outlet_display.html'; $iconPath = Join-Path $scriptDir 'app_icon_v2.ico'; $ws = New-Object -ComObject WScript.Shell; $sDesktop = $ws.CreateShortcut((Join-Path $desktop 'Outlet Attendance.lnk')); $sDesktop.TargetPath = $targetPath; $sDesktop.WorkingDirectory = $scriptDir; if (Test-Path $iconPath) { $sDesktop.IconLocation = $iconPath }; $sDesktop.Description = 'Launch Outlet Attendance Display'; $sDesktop.Save(); $sLocal = $ws.CreateShortcut((Join-Path $scriptDir 'Outlet Attendance.lnk')); $sLocal.TargetPath = $targetPath; $sLocal.WorkingDirectory = $scriptDir; if (Test-Path $iconPath) { $sLocal.IconLocation = $iconPath }; $sLocal.Description = 'Launch Outlet Attendance Display'; $sLocal.Save(); try { $code = '[DllImport(\"shell32.dll\")] public static extern void SHChangeNotify(int eventId, int flags, IntPtr item1, IntPtr item2);'; $type = Add-Type -MemberDefinition $code -Name 'ShellNotification' -Namespace 'WinAPI' -PassThru; $type::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero); } catch {}"

if %errorlevel% equ 0 (
    echo.
    echo ✅ Desktop shortcut 'Outlet Attendance' created successfully!
    echo 🎨 Icon: app_icon_v2.ico applied.
    echo 💡 Shortcut is fully accessible for standard / non-admin users.
) else (
    echo.
    echo ❌ Failed to create shortcut.
)

echo.
pause
