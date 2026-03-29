@echo off
title x-trade.ai Dashboard
echo.
echo   Starting x-trade.ai Dashboard...
echo.
cd /d "%~dp0"
start http://localhost:3777
node server.js
pause
