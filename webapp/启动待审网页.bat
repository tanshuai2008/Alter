@echo off
chcp 65001 >nul
title Alter Review Webapp
cd /d "%~dp0"

python -c "import boto3" 2>nul
if errorlevel 1 (
    echo Installing dependency boto3, please wait...
    python -m pip install boto3 --quiet
)

echo.
echo ========================================================
echo   Alter review webapp starting...
echo   Browser will open: http://127.0.0.1:8765
echo   Close this black window to stop the webapp.
echo ========================================================
echo.

python server.py

echo.
echo Server stopped. Press any key to close.
pause >nul