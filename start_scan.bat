@echo off
chcp 65001 >nul
title 缠论三买扫描（chan.py）
cd /d "%~dp0"

echo ==========================================
echo   🎯 缠论三类买点扫描（chan.py · 中证500）
echo ==========================================
echo.

rem ---- 检查 Python ----
where python >nul 2>nul
if errorlevel 1 (
    echo ❌ 未找到 Python，请先安装 Python 3.10+（勾选 Add to PATH）
    echo    下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

rem ---- 检查依赖（缺啥装啥） ----
python -c "import akshare, yfinance, pandas, dotenv" >nul 2>nul
if errorlevel 1 (
    echo 📦 首次运行，安装依赖（约2-5分钟，请耐心等待）...
    python -m pip install --upgrade pip
    python -m pip install akshare yfinance "pandas>=2.0,<3" "numpy<3" matplotlib requests python-dotenv markdown2
)

rem ---- 检查 scan.env ----
if not exist "scan.env" (
    echo.
    echo ⚠️ 未找到 scan.env，正在创建模板...
    copy /y "scan.env.example" "scan.env" >nul
    echo.
    echo 请用记事本打开 scan.env，填写：
    echo   EMAIL_SENDER=你的QQ邮箱
    echo   EMAIL_PASSWORD=SMTP授权码（QQ邮箱-设置-账号-POP3/IMAP/SMTP服务-生成授权码）
    echo   EMAIL_RECEIVERS=收件邮箱
    echo.
    echo 填写完保存后，重新双击本文件即可运行。
    pause
    exit /b 1
)

echo ✅ 开始扫描中证500（约500只，预计 20~40 分钟）...
echo   期间窗口会持续滚动日志，完成后自动发邮件并显示结果。
echo.
python chan_scan.py

echo.
echo ==========================================
if exist "chan_scan_report.md" (
    echo ✅ 扫描完成！报告已保存：chan_scan_report.md
    echo    （本目录下可查看，也可直接看邮件）
) else (
    echo ❌ 扫描失败，请查看上方日志
)
echo ==========================================
pause
