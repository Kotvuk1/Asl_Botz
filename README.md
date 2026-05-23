# Асылхан (Асл) — Личный Telegram-бот

Умный персональный ИИ-помощник на базе Groq API + PostgreSQL с долгосрочной памятью, управлением задачами и поддержкой трёх языков (рус/каз/eng).

---

## Возможности

| Функция | Описание |
|---|---|
| 💬 Умный чат | Контекстный диалог через Groq LLM |
| 🧠 Долгосрочная память | Сохраняет факты о пользователе в БД |
| ✅ Задачи | Создание, выполнение, удаление задач |
| 🔑 Whitelist | Доступ только для 5 разрешённых пользователей |
| 🔄 Ротация Groq | Умное переключение между 3 API-ключами |
| 🌐 Три языка | Русский, казахский, английский |

---

## Команды бота

### Основные
| Команда | Описание |
|---|---|
| `/start` | Приветствие |
| `/help` | Список команд |
| `/clear` | Очистить историю диалога |

### Память
| Команда | Описание |
|---|---|
| `/memory` | Показать всё, что бот помнит о тебе |
| `/remember ключ = значение` | Запомнить факт |
| `/forget ключ` | Забыть факт |

### Задачи
| Команда | Описание |
|---|---|
| `/tasks` | Список текущих задач |
| `/addtask название` | Добавить задачу |
| `/done id` | Отметить задачу выполненной |
| `/deltask id` | Удалить задачу |

### Только для владельца
| Команда | Описание |
|---|---|
| `/adduser telegram_id` | Добавить в whitelist |
| `/removeuser telegram_id` | Убрать из whitelist |
| `/users` | Список разрешённых пользователей |

---

## Технический стек

- **Python 3.12**
- **aiogram 3.x** — Telegram Bot API
- **Groq API** — LLM (llama-3.3-70b-versatile)
- **PostgreSQL** (Neon.tech) — хранение данных
- **SQLAlchemy 2.0** — ORM
- **Alembic** — миграции БД

---

## Локальный запуск

### 1. Клонирование

```bash
git clone https://github.com/kotvuk1/aslhan-.git
cd aslhan-
```

### 2. Виртуальное окружение

```bash
python3.12 -m venv venv
source venv/bin/activate          # Linux/Mac
# venv\Scripts\activate           # Windows
pip install -r requirements.txt
```

### 3. Настройка переменных окружения

```bash
cp .env.example .env
nano .env   # или любой редактор
```

Заполни все поля (см. раздел «Как получить ключи»).

### 4. Миграции БД

```bash
alembic upgrade head
```

### 5. Запуск

```bash
python bot.py
```

---

## Как получить ключи

### Telegram Bot Token
1. Открой [@BotFather](https://t.me/BotFather) в Telegram
2. Напиши `/newbot`
3. Укажи имя: `Асылхан` и username: `Asl_Personal_Bot`
4. Скопируй токен → `BOT_TOKEN`

### Твой Telegram ID (OWNER_ID)
1. Напиши боту [@userinfobot](https://t.me/userinfobot)
2. Он пришлёт твой `Id` → `OWNER_ID`

### 3 Groq API ключа (бесплатно)
1. Зайди на [console.groq.com](https://console.groq.com)
2. Зарегистрируй **3 аккаунта** (например, через разные Google-аккаунты)
3. В каждом: **API Keys → Create API Key**
4. Скопируй в `GROQ_API_KEY_1`, `GROQ_API_KEY_2`, `GROQ_API_KEY_3`

> Лимит бесплатного Groq: ~14,400 запросов/день на аккаунт. 3 ключа = ~43,000 запросов/день.

### Neon PostgreSQL (бесплатно, навсегда)
1. Зайди на [neon.tech](https://neon.tech) и зарегистрируйся
2. Создай новый проект (бесплатный план `Free Tier`)
3. В разделе **Connection Details** выбери `Connection string`
4. Скопируй строку вида:
   ```
   postgresql://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
5. Вставь в `DATABASE_URL` (бот автоматически конвертирует в asyncpg-формат)

---

## Деплой на PythonAnywhere (бесплатно, навсегда)

PythonAnywhere предоставляет **бесплатный тариф Beginner** без ограничения по времени.
Бот работает через **polling** (не webhook), что идеально для бесплатного тарифа.

### Шаг 1: Регистрация

1. Зайди на [pythonanywhere.com](https://www.pythonanywhere.com)
2. Нажми **Create a Beginner account**
3. Придумай username (например, `aslhanbot`)

### Шаг 2: Загрузка кода

В панели PythonAnywhere → **Bash console**:

```bash
# Клонируем репозиторий
git clone https://github.com/kotvuk1/aslhan-.git
cd aslhan-

# Создаём виртуальное окружение с Python 3.12
python3.12 -m venv venv
source venv/bin/activate

# Устанавливаем зависимости
pip install -r requirements.txt
```

### Шаг 3: Настройка .env

```bash
cp .env.example .env
nano .env
```

Заполни все переменные (BOT_TOKEN, OWNER_ID, GROQ_API_KEY_*, DATABASE_URL).

### Шаг 4: Миграция БД

```bash
source venv/bin/activate
cd ~/aslhan-
alembic upgrade head
```

### Шаг 5: Проверка локального запуска

```bash
python bot.py
# Убедись что бот стартует без ошибок, нажми Ctrl+C
```

### Шаг 6: Always-on Task (бесплатно не доступно — используем Scheduled Task)

> ⚠️ **Важно**: На бесплатном тарифе PythonAnywhere нет Always-on Tasks.
> Используем **Scheduled Tasks** каждый час + скрипт-хранитель.

#### Вариант A: Scheduled Task каждый час (простой способ)

1. В панели PythonAnywhere → **Tasks**
2. Нажми **Add a new scheduled task**
3. Укажи время: каждый час (`0 * * * *` или выбери "Hourly")
4. Команда:
   ```bash
   /home/aslhanbot/aslhan-/venv/bin/python /home/aslhanbot/aslhan-/bot.py
   ```

> ⚠️ Бот будет перезапускаться раз в час. Это нормально — Telegram polling восстанавливается автоматически.

#### Вариант B: Скрипт-хранитель (рекомендуется)

Создай файл `keep_alive.sh`:

```bash
nano ~/keep_alive.sh
```

Содержимое:

```bash
#!/bin/bash
LOGFILE=/home/aslhanbot/aslhan-/bot.log
PIDFILE=/home/aslhanbot/aslhan-/bot.pid

cd /home/aslhanbot/aslhan-
source venv/bin/activate

# Проверяем, запущен ли бот
if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "$(date): Bot is running (PID=$PID)" >> "$LOGFILE"
        exit 0
    fi
fi

# Запускаем бот
echo "$(date): Starting bot..." >> "$LOGFILE"
nohup python bot.py >> "$LOGFILE" 2>&1 &
echo $! > "$PIDFILE"
echo "$(date): Bot started (PID=$!)" >> "$LOGFILE"
```

```bash
chmod +x ~/keep_alive.sh
```

В разделе Tasks добавь hourly task:
```bash
/bin/bash /home/aslhanbot/keep_alive.sh
```

**Первый запуск вручную:**
```bash
bash ~/keep_alive.sh
```

#### Просмотр логов

```bash
tail -f ~/aslhan-/bot.log
```

### Шаг 7: Обновление кода

```bash
cd ~/aslhan-
git pull origin main
source venv/bin/activate
alembic upgrade head   # если были изменения в БД

# Перезапуск: убить старый процесс и запустить новый
kill $(cat bot.pid) 2>/dev/null || true
bash ~/keep_alive.sh
```

---

## Деплой на Railway.app (альтернатива)

> Railway даёт $5 кредитов/месяц на бесплатном тарифе — этого хватает для лёгкого бота.

### Шаг 1: Подготовка

1. Зайди на [railway.app](https://railway.app) → **Login with GitHub**
2. Нажми **New Project → Deploy from GitHub repo**
3. Выбери репозиторий `aslhan-`

### Шаг 2: Переменные окружения

В панели Railway → **Variables**:

```
BOT_TOKEN = your_bot_token
OWNER_ID = your_telegram_id
WHITELIST_IDS = id1,id2,id3
GROQ_API_KEY_1 = gsk_xxx
GROQ_API_KEY_2 = gsk_yyy
GROQ_API_KEY_3 = gsk_zzz
GROQ_MODEL = llama-3.3-70b-versatile
DATABASE_URL = postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require
LOG_LEVEL = INFO
```

### Шаг 3: Настройка сервиса

В Railway → **Settings → Deploy**:
- Start command: `python bot.py`

### Шаг 4: Миграции (одноразово)

В Railway → **Shell** (или через CLI):
```bash
alembic upgrade head
```

### Шаг 5: Деплой

Railway задеплоит автоматически при каждом `git push` в main ветку.

---

## Структура проекта

```
aslhan-/
├── bot.py                  # Точка входа, polling
├── config.py               # Настройки через Pydantic Settings
├── .env.example            # Пример переменных окружения
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── README.md
├── core/
│   ├── llm.py              # Groq роутер (3 ключа с ротацией)
│   ├── db.py               # Async SQLAlchemy engine + session factory
│   ├── memory.py           # История + долгосрочная память
│   ├── tools.py            # Управление задачами
│   └── utils.py            # Логирование, форматирование
├── handlers/
│   ├── commands.py         # /start, /help, /clear, /tasks, /memory, etc.
│   └── messages.py         # Обработка текстовых сообщений (LLM)
├── database/
│   └── models.py           # SQLAlchemy модели: User, Message, Memory, Task
├── prompts/
│   └── system_prompt.txt   # Системный промпт для LLM
└── alembic/
    ├── env.py
    ├── script.py.mako
    └── versions/
        └── 001_initial_schema.py
```

---

## Ротация Groq ключей

Логика в `core/llm.py`:

1. Используется текущий ключ
2. При `RateLimitError` → переключение на следующий ключ (round-robin)
3. Ключи с недавней ошибкой (< 60 сек) пропускаются
4. Если все 3 ключа недоступны → понятная ошибка пользователю
5. После успешного запроса — счётчик ошибок ключа сбрасывается

---

## FAQ

**Q: Бот перестаёт отвечать на PythonAnywhere?**
A: Бесплатный тариф не поддерживает always-on процессы. Используй Scheduled Task каждый час + скрипт `keep_alive.sh`.

**Q: Ошибка `RateLimitError` от Groq?**
A: Groq даёт ~14,400 запросов/день на аккаунт. 3 ключа дают ~43k запросов/день. При превышении бот сам уведомит пользователя.

**Q: Как добавить нового пользователя?**
A: Отправь боту: `/adduser 123456789` (владелец бота).

**Q: Как посмотреть, что бот помнит о тебе?**
A: `/memory`

**Q: Как научить бота запоминать?**
A: Напиши `/remember имя = Асылхан` или `запомни: любимый цвет = синий`.
