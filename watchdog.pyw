"""
Watchdog: запускает bot.py и автоматически перезапускает при падении.
Запускается через pythonw.exe — без консольного окна.
"""
import os
import subprocess
import sys
import time
import logging
from logging.handlers import RotatingFileHandler

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

# Лог watchdog-а (отдельный файл)
handler = RotatingFileHandler(
    os.path.join(BASE, "watchdog.log"),
    maxBytes=1 * 1024 * 1024,
    backupCount=2,
    encoding="utf-8",
)
handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s", "%Y-%m-%d %H:%M:%S"))
logging.basicConfig(level=logging.INFO, handlers=[handler])

PYTHON = sys.executable          # тот же pythonw.exe что запустил watchdog
RESTART_DELAY = 10               # секунд до перезапуска после падения
MAX_QUICK_CRASHES = 5            # после 5 быстрых падений — пауза 60 сек
QUICK_CRASH_THRESHOLD = 30       # "быстрое" падение — если бот жил меньше 30 сек

logging.info("=" * 40)
logging.info("Watchdog запущен. Python: %s", PYTHON)

quick_crashes = 0

while True:
    start_time = time.time()
    logging.info("Запускаю bot.py...")

    try:
        proc = subprocess.Popen(
            [PYTHON, os.path.join(BASE, "bot.py")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        exit_code = proc.wait()
    except Exception as e:
        logging.error("Не удалось запустить bot.py: %s", e)
        exit_code = -1

    uptime = time.time() - start_time

    if uptime < QUICK_CRASH_THRESHOLD:
        quick_crashes += 1
        logging.warning(
            "Быстрое падение #%d (жил %.1f сек), код: %s",
            quick_crashes, uptime, exit_code,
        )
    else:
        quick_crashes = 0
        logging.warning("bot.py завершился (жил %.0f сек), код: %s", uptime, exit_code)

    if quick_crashes >= MAX_QUICK_CRASHES:
        logging.error("5 быстрых падений подряд — пауза 60 секунд")
        time.sleep(60)
        quick_crashes = 0
    else:
        logging.info("Перезапуск через %d сек...", RESTART_DELAY)
        time.sleep(RESTART_DELAY)
