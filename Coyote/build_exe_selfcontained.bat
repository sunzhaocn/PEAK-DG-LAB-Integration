@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Coyote Windows EXE Builder - Self Contained
echo ============================================================
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_exe_selfcontained.ps1"
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" goto :failed
echo [SUCCESS] Build completed.
echo EXE: dist\Coyote\Coyote.exe
echo ZIP: release\Coyote_Windows_x64_Portable.zip
echo.
pause
exit /b 0

:failed
echo [FAILED] Build stopped. Exit code: %RC%
echo Check the error above.
echo.
pause
exit /b %RC%
