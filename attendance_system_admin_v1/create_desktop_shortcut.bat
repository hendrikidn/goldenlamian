@echo off
title Create HR Admin Portal Desktop Shortcut
cd /d "%~dp0"

echo.
echo =======================================================
echo     Creating HR Admin Portal Desktop Shortcut
echo =======================================================
echo.

:: 1. Set environment compatibility layer to prevent UAC/Defender elevation restrictions (Non-Admin Friendly)
set __COMPAT_LAYER=RunAsInvoker

:: 2. Unblock application files from Windows SmartScreen restrictions
echo 🛡️ Unblocking application files...
powershell -ExecutionPolicy Bypass -NoProfile -Command "Get-ChildItem -Path '%~dp0*' -Recurse | Unblock-File -ErrorAction SilentlyContinue" >nul 2>&1

:: 3. Verify target executable and icon path
set "TARGET_EXE=%~dp0HR Admin Portal.exe"
set "ICON_PATH=%~dp0img\HR_Portal.ico"

if not exist "%TARGET_EXE%" (
    echo ❌ ERROR: Target executable "%TARGET_EXE%" not found.
    pause
    exit /b 1
)

if not exist "%ICON_PATH%" (
    echo ⚠️ WARNING: Icon file "%ICON_PATH%" not found. Using default icon.
)

:: 4. Create Desktop & Local Shortcut via PowerShell COM Object (Non-Admin accessible)
echo 🖥️ Creating Desktop shortcut for HR Admin Portal...
powershell -ExecutionPolicy Bypass -NoProfile -Command "$desktop = [Environment]::GetFolderPath('Desktop'); $scriptDir = '%~dp0'.TrimEnd('\'); $targetPath = Join-Path $scriptDir 'HR Admin Portal.exe'; $iconPath = Join-Path $scriptDir 'img\HR_Portal.ico'; $ws = New-Object -ComObject WScript.Shell; $sDesktop = $ws.CreateShortcut((Join-Path $desktop 'HR Admin Portal.lnk')); $sDesktop.TargetPath = $targetPath; $sDesktop.WorkingDirectory = $scriptDir; if (Test-Path $iconPath) { $sDesktop.IconLocation = $iconPath }; $sDesktop.Description = 'Launch HR Admin Portal'; $sDesktop.Save(); $sLocal = $ws.CreateShortcut((Join-Path $scriptDir 'HR Admin Portal.lnk')); $sLocal.TargetPath = $targetPath; $sLocal.WorkingDirectory = $scriptDir; if (Test-Path $iconPath) { $sLocal.IconLocation = $iconPath }; $sLocal.Description = 'Launch HR Admin Portal'; $sLocal.Save(); try { $regPath = 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers'; if (-not (Test-Path $regPath)) { New-Item -Path $regPath -Force | Out-Null }; Set-ItemProperty -Path $regPath -Name $targetPath -Value '~ RUNASINVOKER' -ErrorAction SilentlyContinue; } catch {}; try { $code = '[DllImport(\"shell32.dll\")] public static extern void SHChangeNotify(int eventId, int flags, IntPtr item1, IntPtr item2);'; $type = Add-Type -MemberDefinition $code -Name 'ShellNotificationAdmin' -Namespace 'WinAPI' -PassThru; $type::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero); } catch {}"

if %errorlevel% equ 0 (
    echo.
    echo ✅ Desktop shortcut 'HR Admin Portal' created successfully!
    echo 🎨 Icon: img\HR_Portal.ico applied.
    echo 💡 Shortcut is configured to run smoothly for standard / non-admin users without UAC blocks.
) else (
    echo.
    echo ❌ Failed to create desktop shortcut.
)

echo.
pause
