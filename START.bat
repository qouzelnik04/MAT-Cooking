@echo off
setlocal
cd /d "%~dp0mat_cooking"

echo.
echo   MAT-Cooking -- lokalni spusteni na vyvoj a testovani
echo   (data jsou v devdata\, ostra data jsou v Home Assistantu)
echo.

set PY=
where py >nul 2>&1 && set PY=py -3
if not defined PY ( where python >nul 2>&1 && set PY=python )
if not defined PY (
  echo   [!] Nenasel jsem Python. Nainstaluj ho z https://www.python.org/downloads/
  pause
  exit /b 1
)

%PY% -m app.main --data ..\devdata
if errorlevel 1 pause
endlocal
