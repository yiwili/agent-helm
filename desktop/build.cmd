@echo off
setlocal
cd /d "%~dp0"
call npm ci
if errorlevel 1 exit /b %errorlevel%
call npm run build
exit /b %errorlevel%
