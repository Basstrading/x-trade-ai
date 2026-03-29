@echo off
title MM20 Pullback - MNQ x4 - Trading Bot
echo ============================================
echo   MM20 PULLBACK - Topstep $50k - MNQ x4
echo   TP 300 / SL 200 / Trail 20 / h1d 75
echo ============================================
echo.

cd /d "%~dp0"

:: Supprime les flags de blocage
del /q data\emergency_stop.flag 2>nul
del /q data\bot_disabled.flag 2>nul

echo [1/2] Demarrage Dashboard (port 8001)...
start "Dashboard 8001" cmd /k "cd /d %~dp0 && python main.py"

timeout /t 5 /nobreak >nul

echo [2/2] Demarrage Bot MM20 Pullback...
start "MM20 Bot" cmd /k "cd /d %~dp0 && python mm20_news_live.py"

echo.
echo ============================================
echo   Dashboard : http://localhost:8001
echo   Bot MM20  : voir fenetre MM20 Bot
echo ============================================
echo.
echo Fermez les fenetres avec Ctrl+C.
pause
