@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo Останавливаю Meir бота и watchdog...

wmic process where "commandline like '%%meir-bot%%bot.py%%'" delete >nul 2>&1
wmic process where "commandline like '%%meir-bot%%watchdog.pyw%%'" delete >nul 2>&1

echo [OK] Meir остановлен.
timeout /t 2 >nul
