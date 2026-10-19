@echo off
rem Double-click to start Forex Ledger on Windows. It opens in your browser; to stop the app,
rem close this window or press Ctrl+C.
cd /d "%~dp0"

rem uv installs into one of these folders; a fresh install may not be on PATH yet.
if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"

where uv >nul 2>nul
if not errorlevel 1 goto run
echo Forex Ledger needs uv. Install it with this command in PowerShell, then double-click this file again:
echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
pause
exit /b 1

:run
uv run forex
pause
