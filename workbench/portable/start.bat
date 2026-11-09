@echo off
setlocal
cd /d "%~dp0"
set PYTHONHOME=
set PYTHONPATH=
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1" %*
set "WB_EXIT=%ERRORLEVEL%"
if not "%WB_EXIT%"=="0" pause
exit /b %WB_EXIT%
