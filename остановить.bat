@echo off
chcp 65001 >nul

echo Останавливаю бота и watchdog...

wmic process where "commandline like '%%bot.py%%'" delete >nul 2>&1
wmic process where "commandline like '%%watchdog.pyw%%'" delete >nul 2>&1
taskkill /f /im pythonw.exe >nul 2>&1

echo [OK] Бот остановлен.
timeout /t 2 >nul
