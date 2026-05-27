@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo Останавливаю старые процессы...

:: Убиваем все процессы с bot.py и watchdog.pyw в командной строке
wmic process where "commandline like '%%bot.py%%'" delete >nul 2>&1
wmic process where "commandline like '%%watchdog.pyw%%'" delete >nul 2>&1

:: Ждём секунду чтобы процессы завершились
ping 127.0.0.1 -n 2 >nul

:: Запускаем watchdog (он сам запустит bot.py и будет перезапускать при падении)
echo Запускаю бота с автоперезапуском...
start "" "C:\Users\user\AppData\Local\Programs\Python\Python314\pythonw.exe" watchdog.pyw

ping 127.0.0.1 -n 3 >nul

:: Проверяем что процесс появился
tasklist /fi "IMAGENAME eq pythonw.exe" /fo csv 2>nul | find /i "pythonw.exe" >nul
if not errorlevel 1 (
    echo [OK] Бот запущен! Это окно можно закрыть.
) else (
    echo [!] Что-то пошло не так. Проверь watchdog.log
)

timeout /t 4 >nul
