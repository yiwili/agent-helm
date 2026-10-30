@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-agent-helm.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo [Agent Helm] 进程异常退出 ^(Exit Code: %EXIT_CODE%^)，请查看上方错误信息。
    pause
)
endlocal
exit /b %EXIT_CODE%
