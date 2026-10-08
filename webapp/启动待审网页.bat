@echo off
chcp 65001 >nul
title Alter 待审网页
cd /d "%~dp0"

REM 确保 boto3 已安装（本地网页连 AWS 用）
python -c "import boto3" 2>nul
if errorlevel 1 (
    echo 正在安装依赖 boto3 ，请稍候...
    python -m pip install boto3 --quiet
)

echo.
echo ========================================================
echo   Alter 待审网页启动中，浏览器会自动打开
echo   地址： http://127.0.0.1:8765
echo   用完直接关闭这个黑窗口即可停止。
echo ========================================================
echo.

python server.py

echo.
echo 服务已停止。按任意键关闭窗口。
pause >nul
