@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo Останавливаю Bakyt бота и watchdog...

wmic process where "commandline like '%%bakyt-bot%%bot.py%%'" delete >nul 2>&1
wmic process where "commandline like '%%bakyt-bot%%watchdog.pyw%%'" delete >nul 2>&1

echo [OK] Bakyt остановлен.
timeout /t 2 >nul
