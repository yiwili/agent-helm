@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
set "AGENT_HELM_BUILD_EXIT=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %AGENT_HELM_BUILD_EXIT%
