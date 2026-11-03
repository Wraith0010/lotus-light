@echo off
cd /d "%~dp0"
set PY=python
%PY% -c "import sys" >nul 2>nul || set PY=py
%PY% -c "import sys" >nul 2>nul || goto nopython
%PY% -c "import bleak, aiohttp, certifi" >nul 2>nul || %PY% -m pip install -r requirements.txt
%PY% server.py %*
pause
exit /b

:nopython
echo Python is not installed. Installing it now...
winget install -e --id Python.Python.3.12
echo.
echo Done. Close this window and double-click start.bat again.
pause
