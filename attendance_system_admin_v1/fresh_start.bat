@echo off
cd /d "%~dp0"
title Reset Attendance System - Fresh Start

echo.
echo ===================================================
echo   WARNING: This will reset the attendance system.
echo   All attendance logs, user credentials, fingerprint
echo   mappings, and temporary cache files will be deleted.
echo ===================================================
echo.
set /p CONFIRM="Are you sure you want to proceed? (Y/N): "
if /i "%CONFIRM%" neq "Y" (
    echo Reset cancelled.
    pause
    exit /b
)

echo.
echo 🛑 Stopping Streamlit and Python processes...
taskkill /f /im python.exe >nul 2>&1
taskkill /f /im streamlit.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo 🧹 Clearing database and temporary files...
if exist "data\attendance_system.duckdb" (
    del /f /q "data\attendance_system.duckdb"
    echo ✅ Cleared: attendance_system.duckdb
)
if exist "data\hr_system.duckdb" (
    del /f /q "data\hr_system.duckdb"
    echo ✅ Cleared: hr_system.duckdb
)
if exist "data\raw_detected_codes.json" (
    del /f /q "data\raw_detected_codes.json"
    echo ✅ Cleared: raw_detected_codes.json
)
if exist "data\temp_scan_result.json" (
    del /f /q "data\temp_scan_result.json"
    echo ✅ Cleared: temp_scan_result.json
)
if exist "data\temp_ui_scans.json" (
    del /f /q "data\temp_ui_scans.json"
    echo ✅ Cleared: temp_ui_scans.json
)
if exist "data\attendance_backup.json" (
    del /f /q "data\attendance_backup.json"
    echo ✅ Cleared: attendance_backup.json
)
if exist "data\data\attendance_backup.json" (
    del /f /q "data\data\attendance_backup.json"
    echo ✅ Cleared: data\attendance_backup.json
)

echo.
echo 🎉 Fresh start initialized successfully!
echo The databases will be recreated on next application launch.
echo.
pause
