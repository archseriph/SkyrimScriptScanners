@echo off
setlocal
cd /d "%~dp0"

:: ── Python detection ─────────────────────────────────────────────────────────
:: Override by setting PYTHON to your python.exe path, e.g.:
::   set PYTHON=C:\Python313\python.exe
set PYTHON=python
python --version >nul 2>&1
if errorlevel 1 (
    set PYTHON=py
    py --version >nul 2>&1
    if errorlevel 1 (
        echo.
        echo  ERROR: Python not found on PATH.
        echo  Install Python 3.8+ from https://www.python.org/downloads/
        echo  Or edit the PYTHON variable at the top of this file.
        echo.
        pause
        exit /b 1
    )
)

set SCANNER=%~dp0ScriptScanner\scanner.py
set AUDITOR=%~dp0InjectorAuditor\auditor.py
set SCANNER_OUT=%~dp0ScriptScanner\output
set AUDITOR_OUT=%~dp0InjectorAuditor\output

echo.
echo ============================================================
echo   Skyrim Mod Auditor Suite
echo ============================================================
echo.

echo [1/2] Script Scanner  (BSA extract + Champollion + rule analysis)
echo       This step takes several minutes on a full mod list.
echo       --fix: patched .pex files written to fix_output_mod\Scripts\
echo.
"%PYTHON%" "%SCANNER%" --fix
if errorlevel 1 (
    echo.
    echo   !! Script Scanner failed — check output above for errors.
) else (
    echo.
    echo   Script Scanner finished.
)

echo.
echo ============================================================
echo.

echo [2/2] Injector Config Auditor  (SPID / KID / SkyPatcher)
echo       --fix: corrected ini files written to fix_output_mod\
echo.
"%PYTHON%" "%AUDITOR%" --fix
if errorlevel 1 (
    echo.
    echo   !! Injector Auditor failed — check output above for errors.
) else (
    echo.
    echo   Injector Auditor finished.
)

echo.
echo ============================================================
echo   Opening latest reports...
echo ============================================================
echo.

for /f "delims=" %%F in ('dir /b /o-d "%SCANNER_OUT%\report_*.md" 2^>nul') do (
    echo   Script Scanner:   %SCANNER_OUT%\%%F
    start "" "%SCANNER_OUT%\%%F"
    goto :scanner_opened
)
echo   Script Scanner:   no report found
:scanner_opened

for /f "delims=" %%F in ('dir /b /o-d "%AUDITOR_OUT%\report_*.md" 2^>nul') do (
    echo   Injector Auditor: %AUDITOR_OUT%\%%F
    start "" "%AUDITOR_OUT%\%%F"
    goto :auditor_opened
)
echo   Injector Auditor: no report found
:auditor_opened

echo.
pause
endlocal
