@echo off
title Trading Agent D6 - NQ Nasdaq Futures
color 0A

echo.
echo  ========================================
echo   TRADING AGENT D6 - NQ Nasdaq Futures
echo  ========================================
echo.

:: Va dans le dossier du script
cd /d "%~dp0"

:: Ouvre le navigateur apres 3 secondes (en arriere-plan)
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8001"

:: Lance le serveur
echo  Demarrage du serveur...
echo  Dashboard : http://localhost:8001
echo  Ctrl+C pour arreter
echo.
python main.py

:: Si python plante
echo.
echo  Le serveur s'est arrete.
pause
