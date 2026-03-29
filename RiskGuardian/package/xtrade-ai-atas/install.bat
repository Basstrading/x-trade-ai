@echo off
color 0B
title x-trade.ai Installer
echo.
echo   ╔══════════════════════════════════════════╗
echo   ║        x-trade.ai Risk Manager           ║
echo   ║          ATAS Platform Installer          ║
echo   ╚══════════════════════════════════════════╝
echo.

:: Check ATAS is installed
set "ATAS_IND=%APPDATA%\ATAS\Indicators"
if not exist "%ATAS_IND%" (
    echo   [ERROR] ATAS not found on this computer.
    echo   Please install ATAS first, then re-run this installer.
    echo.
    pause
    exit /b 1
)

echo   [1/4] Installing indicator to ATAS...
copy /Y "%~dp0RiskGuardian.dll" "%ATAS_IND%\RiskGuardian.dll" >nul
if errorlevel 1 (
    echo   [ERROR] Failed to copy. Please close ATAS and try again.
    pause
    exit /b 1
)
echo         Done.

echo   [2/4] Setting up dashboard files...
set "DASH=%APPDATA%\xtrade-ai\dashboard"
mkdir "%DASH%" 2>nul
copy /Y "%~dp0dashboard\*.*" "%DASH%\" >nul
echo         Done.

echo   [3/4] Creating data directory...
mkdir "%APPDATA%\xtrade-ai" 2>nul
echo         Done.

echo   [4/4] Creating desktop shortcut...
set "DESKTOP=%USERPROFILE%\Desktop"
(
echo @echo off
echo title x-trade.ai Dashboard
echo cd /d "%DASH%"
echo start http://localhost:3777
echo node server.js
) > "%DESKTOP%\x-trade.ai Dashboard.bat"
echo         Done.

echo.
echo   ╔══════════════════════════════════════════╗
echo   ║         Installation Complete!            ║
echo   ╠══════════════════════════════════════════╣
echo   ║                                          ║
echo   ║  Next steps:                             ║
echo   ║                                          ║
echo   ║  1. Open ATAS (restart if already open)  ║
echo   ║  2. Open a chart                         ║
echo   ║  3. Click Indicators                     ║
echo   ║  4. Go to "Custom" category              ║
echo   ║  5. Add "x-trade.ai Risk"                ║
echo   ║  6. Click Apply                          ║
echo   ║                                          ║
echo   ║  Dashboard: double-click                 ║
echo   ║  "x-trade.ai Dashboard" on your Desktop  ║
echo   ║                                          ║
echo   ╚══════════════════════════════════════════╝
echo.
pause
