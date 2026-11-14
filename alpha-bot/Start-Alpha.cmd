@echo off
setlocal
cd /d "%~dp0"
set "DASHBOARD_ENABLED=true"
echo Starting Alpha and its local dashboard at http://127.0.0.1:8765
echo Keep this window and the PC running. Stop with Ctrl+C.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\run-supervised.ps1" -SyncCommands
pause
