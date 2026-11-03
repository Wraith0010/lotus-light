@echo off
cd /d "%~dp0"
if not exist public mkdir public
copy /y ..\index.html public\index.html >nul
npx --yes wrangler@4.153.0 deploy
pause
