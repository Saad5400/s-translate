@echo off
REM Double-click entry point on Windows. Just calls run.ps1 in PowerShell.
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0run.ps1"
pause
