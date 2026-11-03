@echo off
cd /d "%~dp0"
set PY=python
%PY% -c "import sys" >nul 2>nul || set PY=py
%PY% -c "import sys" >nul 2>nul || (
  echo Python is not installed yet. Double-click start.bat first.
  pause
  exit /b
)
%PY% -c "import bleak, aiohttp, certifi" >nul 2>nul || %PY% -m pip install -r requirements.txt
%PY% scan.py test
pause
