# Alter 待审网页 —— 一键启动
# 用法：在本文件所在目录开 PowerShell，运行  .\run.ps1
# 或者直接双击本文件（若已允许运行脚本）。关闭窗口即停止。

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 确保依赖（boto3）已装；feedparser 仅云端 Lambda 用，本地网页不需要
python -c "import boto3" 2>$null
if (-not $?) {
    Write-Host "正在安装本地依赖 boto3 ..." -ForegroundColor Yellow
    python -m pip install boto3 --quiet
}

Write-Host "启动 Alter 待审网页，稍候浏览器会自动打开 http://127.0.0.1:8765" -ForegroundColor Green
python server.py
