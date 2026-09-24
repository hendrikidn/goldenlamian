@echo off
cd /d "%~dp0"
title Attendance System Setup

echo.
echo ===================================================
echo             Attendance System Setup
echo ===================================================
echo.

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Python is not installed or not added to your PATH environment variable.
    echo Please install Python and try again.
    pause
    exit /b
)

echo 📦 Installing requirements offline from resource folder...
if not exist "%~dp0resource" (
    echo ❌ Offline resource folder '%~dp0resource' not found.
    echo Please ensure the 'resource' folder containing packages exists.
    pause
    exit /b
)
pip install --no-index --find-links="%~dp0resource" -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo ⚠️ pip install encountered some errors. Please check the output above.
) else (
    echo ✅ Requirements installed successfully.
)

echo.
echo 🔑 Creating Streamlit credentials file...
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit" >nul 2>&1
(
echo [general]
echo email = ""
) > "%USERPROFILE%\.streamlit\credentials.toml" 2>nul
if %errorlevel% neq 0 (
    echo ❌ Failed to create Streamlit credentials.
) else (
    echo ✅ Streamlit credentials file created/updated.
)

echo.
echo 🖥️ Creating Desktop shortcut for HR Admin Portal...
powershell -ExecutionPolicy Bypass -NoProfile -Command "Get-ChildItem -Path '%~dp0*' -Recurse | Unblock-File -ErrorAction SilentlyContinue; $desktop = [Environment]::GetFolderPath('Desktop'); $shortcutPath = [System.IO.Path]::Combine($desktop, 'HR Admin Portal.lnk'); $ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut($shortcutPath); $s.TargetPath = '%~dp0HR Admin Portal.exe'; $s.WorkingDirectory = '%~dp0'; if (Test-Path '%~dp0img\HR_Portal.ico') { $s.IconLocation = '%~dp0img\HR_Portal.ico' }; $s.Save();"
if %errorlevel% neq 0 (
    echo ❌ Failed to create desktop shortcut.
) else (
    echo ✅ Desktop shortcut 'HR Admin Portal' created.
)

echo.
echo 🎉 Setup process completed!
echo.
pause
