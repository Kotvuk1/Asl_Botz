@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo Останавливаю старые процессы Meir...

wmic process where "commandline like '%%meir-bot%%bot.py%%'" delete >nul 2>&1
wmic process where "commandline like '%%meir-bot%%watchdog.pyw%%'" delete >nul 2>&1

ping 127.0.0.1 -n 2 >nul

echo Запускаю Meir бота с автоперезапуском...
start "" "C:\Users\user\AppData\Local\Programs\Python\Python314\pythonw.exe" watchdog.pyw

ping 127.0.0.1 -n 3 >nul

tasklist /fi "IMAGENAME eq pythonw.exe" /fo csv 2>nul | find /i "pythonw.exe" >nul
if not errorlevel 1 (
    echo [OK] Meir запущен! Это окно можно закрыть.
) else (
    echo [!] Что-то пошло не так. Проверь watchdog.log
)

timeout /t 4 >nul
