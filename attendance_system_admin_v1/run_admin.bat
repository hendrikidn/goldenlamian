@echo off
set __COMPAT_LAYER=RunAsInvoker
title HR Admin UI - Port 8502
cd /d "%~dp0"
set APP_MODE=admin
echo Starting HR Admin UI on port 8502...
python -m streamlit run main.py --server.port 8502
pause
