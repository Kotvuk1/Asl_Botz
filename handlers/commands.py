import logging
import re
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

from config import settings
from core.db import AsyncSessionFactory
from core.memory import (
    clear_history,
    delete_memory,
    format_memory_context,
    get_or_create_user,
    save_memory,
)
from core.tools import (
    add_financial_goal,
    add_transaction,
    complete_goal,
    export_financial_data,
    format_balance,
    format_budgets,
    format_category_stats,
    format_goals,
    format_transactions,
    get_balance,
    get_budgets,
    get_category_stats,
    get_financial_goals,
    get_transactions,
    set_budget,
    update_goal_amount,
)
from core.whitelist import add_user as wl_add, remove_user as wl_remove
from database.models import BakUser

logger = logging.getLogger(__name__)
router = Router(name="commands")


def _owner_only(user_id: int) -> bool:
    return user_id == settings.owner_id


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    name = message.from_user.first_name or "друг"
    await message.answer(
        f"👋 Привет, <b>{name}</b>!\n\n"
        f"Я <b>{settings.bot_name}</b> ({settings.bot_short_name}) — твой личный финансовый помощник.\n\n"
        "Я умею:\n"
        "• 💰 Записывать доходы и расходы\n"
        "• 📊 Показывать статистику и баланс\n"
        "• 💼 Следить за бюджетом по категориям\n"
        "• 🎯 Помогать достигать финансовых целей\n"
        "• 🧠 Запоминать важную информацию о тебе\n"
        "• 🎙 Понимать голосовые сообщения\n\n"
        "Просто напиши или надиктуй! Например:\n"
        "<i>«Потратил 3500 тг на кафе»</i>\n"
        "<i>«Получил зарплату 200 000 тг»</i>\n\n"
        "Или используй /help для списка команд.",
        parse_mode="HTML",
    )


# ── /help ─────────────────────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    owner_section = ""
    if _owner_only(message.from_user.id):
        owner_section = (
            "\n\n<b>👑 Команды владельца:</b>\n"
            "/adduser &lt;id&gt; — добавить в whitelist\n"
            "/removeuser &lt;id&gt; — убрать из whitelist\n"
            "/users — список разрешённых пользователей"
        )

    await message.answer(
        f"<b>📖 Команды {settings.bot_name}:</b>\n\n"
        "<b>Основные:</b>\n"
        "/start — Приветствие\n"
        "/help — Это сообщение\n"
        "/clear — Очистить историю диалога\n\n"
        "<b>🧠 Память:</b>\n"
        "/memory — Показать сохранённые факты\n"
        "/remember &lt;ключ&gt; = &lt;значение&gt; — Запомнить факт\n"
        "/forget &lt;ключ&gt; — Забыть факт\n\n"
        "<b>💰 Финансы:</b>\n"
        "/balance — Текущий баланс\n"
        "/add +50000 зарплата — Доход\n"
        "/add -3500 еда кафе — Расход\n"
        "/history — Последние 20 транзакций\n"
        "/stats — Статистика расходов за 30 дней\n\n"
        "<b>💼 Бюджет:</b>\n"
        "/budget — Список бюджетов\n"
        "/budget &lt;категория&gt; &lt;сумма&gt; — Установить лимит\n\n"
        "<b>🎯 Цели:</b>\n"
        "/goals — Список финансовых целей\n"
        "/addgoal &lt;название&gt; &lt;сумма&gt; — Добавить цель\n"
        "/goalset &lt;id&gt; +5000 — Пополнить цель\n"
        "/goaldone &lt;id&gt; — Завершить цель\n\n"
        "<b>📦 Экспорт:</b>\n"
        "/export — Экспорт всех данных"
        + owner_section,
        parse_mode="HTML",
    )


# ── /clear ────────────────────────────────────────────────────────────────────

@router.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        count = await clear_history(session, message.from_user.id)
    await message.answer(
        f"🗑 История диалога очищена ({count} сообщений удалено).\n"
        "Начинаем с чистого листа!"
    )


# ── /memory ───────────────────────────────────────────────────────────────────

@router.message(Command("memory"))
async def cmd_memory(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        ctx = await format_memory_context(session, message.from_user.id)
    await message.answer(
        f"<b>🧠 Моя память о тебе:</b>\n\n{ctx}",
        parse_mode="HTML",
    )


@router.message(Command("remember"))
async def cmd_remember(message: Message, command: CommandObject) -> None:
    args = command.args
    if not args or "=" not in args:
        await message.answer(
            "Формат: /remember <b>ключ</b> = <b>значение</b>\n"
            "Пример: /remember валюта = тенге",
            parse_mode="HTML",
        )
        return
    key, _, value = args.partition("=")
    key = key.strip()[:128]
    value = value.strip()[:1000]
    if not key or not value:
        await message.answer("Ключ и значение не могут быть пустыми.")
        return
    async with AsyncSessionFactory() as session:
        await save_memory(session, message.from_user.id, key, value)
    await message.answer(f"✅ Запомнил: <b>{key}</b> = {value}", parse_mode="HTML")


@router.message(Command("forget"))
async def cmd_forget(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip()
    if not key:
        await message.answer("Укажи ключ: /forget <b>ключ</b>", parse_mode="HTML")
        return
    async with AsyncSessionFactory() as session:
        removed = await delete_memory(session, message.from_user.id, key)
    if removed:
        await message.answer(f"🗑 Забыл: <b>{key}</b>", parse_mode="HTML")
    else:
        await message.answer(f"Факт <b>{key}</b> не найден.", parse_mode="HTML")


# ── /balance ──────────────────────────────────────────────────────────────────

@router.message(Command("balance"))
async def cmd_balance(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        balance = await get_balance(session, message.from_user.id, days=30)
    await message.answer(format_balance(balance), parse_mode="HTML")


# ── /add ──────────────────────────────────────────────────────────────────────

@router.message(Command("add"))
async def cmd_add(message: Message, command: CommandObject) -> None:
    """
    Add a transaction.
    Format: /add +50000 зарплата аванс
            /add -3500 еда кафе
            /add 5000 транспорт такси
    """
    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "Формат:\n"
            "/add +<b>50000</b> зарплата — доход\n"
            "/add -<b>3500</b> еда кафе — расход\n\n"
            "Примеры:\n"
            "<code>/add +200000 зарплата</code>\n"
            "<code>/add -5000 еда ресторан</code>\n"
            "<code>/add -1200 транспорт автобус</code>",
            parse_mode="HTML",
        )
        return

    parts = args.split(maxsplit=2)
    if not parts:
        await message.answer("Неверный формат. Пример: /add +50000 зарплата")
        return

    amount_str = parts[0]
    category = parts[1] if len(parts) > 1 else "другое"
    description = parts[2] if len(parts) > 2 else None

    # Determine type from sign
    if amount_str.startswith("+"):
        tx_type = "income"
        amount_str = amount_str[1:]
    elif amount_str.startswith("-"):
        tx_type = "expense"
        amount_str = amount_str[1:]
    else:
        tx_type = "expense"  # default to expense if no sign

    try:
        amount = float(amount_str.replace(",", ".").replace(" ", ""))
        if amount <= 0:
            raise ValueError("Amount must be positive")
    except (ValueError, InvalidOperation):
        await message.answer("Неверная сумма. Пример: /add +50000 зарплата")
        return

    async with AsyncSessionFactory() as session:
        tx = await add_transaction(
            session,
            user_id=message.from_user.id,
            amount=amount,
            tx_type=tx_type,
            category=category,
            description=description,
        )

    type_icon = "📥" if tx_type == "income" else "📤"
    type_label = "Доход" if tx_type == "income" else "Расход"
    desc_str = f" <i>({tx.description})</i>" if tx.description else ""
    await message.answer(
        f"{type_icon} <b>{type_label} добавлен!</b>\n\n"
        f"Сумма: <b>{amount:,.0f} тг</b>\n"
        f"Категория: <b>{tx.category}</b>{desc_str}",
        parse_mode="HTML",
    )


# ── /history ──────────────────────────────────────────────────────────────────

@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        txs = await get_transactions(session, message.from_user.id, limit=20, days=30)
    await message.answer(format_transactions(txs), parse_mode="HTML")


# ── /stats ────────────────────────────────────────────────────────────────────

@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        stats = await get_category_stats(session, message.from_user.id, days=30)
    await message.answer(format_category_stats(stats), parse_mode="HTML")


# ── /budget ───────────────────────────────────────────────────────────────────

@router.message(Command("budget"))
async def cmd_budget(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()

    if not args:
        # Show existing budgets
        async with AsyncSessionFactory() as session:
            budgets = await get_budgets(session, message.from_user.id)
            cat_stats = await get_category_stats(session, message.from_user.id, days=30)
        await message.answer(format_budgets(budgets, cat_stats), parse_mode="HTML")
        return

    # Set a budget: /budget category amount
    parts = args.rsplit(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Формат: /budget <b>категория</b> <b>сумма</b>\n"
            "Пример: /budget еда 30000",
            parse_mode="HTML",
        )
        return

    category = parts[0].strip()
    amount_str = parts[1].strip()

    try:
        limit = float(amount_str.replace(",", ".").replace(" ", ""))
        if limit <= 0:
            raise ValueError("Must be positive")
    except ValueError:
        await message.answer("Неверная сумма. Пример: /budget еда 30000")
        return

    async with AsyncSessionFactory() as session:
        budget = await set_budget(session, message.from_user.id, category, limit)

    await message.answer(
        f"✅ Бюджет установлен!\n\n"
        f"Категория: <b>{budget.category}</b>\n"
        f"Лимит: <b>{limit:,.0f} тг</b> в месяц",
        parse_mode="HTML",
    )


# ── /goals ────────────────────────────────────────────────────────────────────

@router.message(Command("goals"))
async def cmd_goals(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        goals = await get_financial_goals(session, message.from_user.id)
    await message.answer(format_goals(goals), parse_mode="HTML")


# ── /addgoal ──────────────────────────────────────────────────────────────────

@router.message(Command("addgoal"))
async def cmd_addgoal(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "Формат: /addgoal <b>название</b> <b>сумма</b>\n"
            "Пример: /addgoal iPhone 250000\n"
            "Пример: /addgoal Отпуск в Европе 500000",
            parse_mode="HTML",
        )
        return

    # Last token is the amount
    parts = args.rsplit(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Укажи название и сумму цели.\n"
            "Пример: /addgoal iPhone 250000",
            parse_mode="HTML",
        )
        return

    title = parts[0].strip()
    amount_str = parts[1].strip()

    try:
        target = float(amount_str.replace(",", ".").replace(" ", ""))
        if target <= 0:
            raise ValueError("Must be positive")
    except ValueError:
        await message.answer(
            "Неверная сумма цели. Пример: /addgoal iPhone 250000"
        )
        return

    if not title:
        await message.answer("Название цели не может быть пустым.")
        return

    async with AsyncSessionFactory() as session:
        goal = await add_financial_goal(
            session, message.from_user.id, title, target
        )

    await message.answer(
        f"🎯 Цель добавлена!\n\n"
        f"<b>#{goal.id}</b> {goal.title}\n"
        f"Целевая сумма: <b>{target:,.0f} тг</b>\n"
        f"Накоплено: <b>0 тг</b>\n\n"
        f"Пополняй с помощью: /goalset {goal.id} +5000",
        parse_mode="HTML",
    )


# ── /goalset ──────────────────────────────────────────────────────────────────

@router.message(Command("goalset"))
async def cmd_goalset(message: Message, command: CommandObject) -> None:
    """Update goal progress: /goalset <id> +5000 or /goalset <id> 5000"""
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit():
        await message.answer(
            "Формат: /goalset <b>id</b> <b>+сумма</b>\n"
            "Пример: /goalset 1 +5000",
            parse_mode="HTML",
        )
        return

    goal_id = int(parts[0])
    amount_str = parts[1].strip().replace(",", ".").replace(" ", "")

    try:
        # Allow negative to subtract, positive to add
        amount = float(amount_str.lstrip("+"))
        if parts[1].strip().startswith("-"):
            amount = -abs(amount)
        else:
            amount = abs(amount)
    except ValueError:
        await message.answer("Неверная сумма. Пример: /goalset 1 +5000")
        return

    async with AsyncSessionFactory() as session:
        ok = await update_goal_amount(session, message.from_user.id, goal_id, amount)
        if ok:
            goals = await get_financial_goals(session, message.from_user.id)
            goal = next((g for g in goals if g.id == goal_id), None)

    if not ok:
        await message.answer(f"Цель #{goal_id} не найдена.")
        return

    if goal:
        target = Decimal(str(goal.target_amount))
        current = Decimal(str(goal.current_amount))
        pct = int(current / target * 100) if target > 0 else 0
        pct = min(pct, 100)
        filled = round(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        sign = "+" if amount >= 0 else ""
        await message.answer(
            f"✅ Цель <b>#{goal_id}</b> обновлена!\n\n"
            f"{goal.title}\n"
            f"Изменение: <b>{sign}{amount:,.0f} тг</b>\n"
            f"Прогресс: [{bar}] {pct}%\n"
            f"Накоплено: <b>{float(current):,.0f}</b> / {float(target):,.0f} тг",
            parse_mode="HTML",
        )
    else:
        await message.answer(f"✅ Цель #{goal_id} обновлена!")


# ── /goaldone ─────────────────────────────────────────────────────────────────

@router.message(Command("goaldone"))
async def cmd_goaldone(message: Message, command: CommandObject) -> None:
    gid_str = (command.args or "").strip()
    if not gid_str.isdigit():
        await message.answer("Укажи номер: /goaldone <b>id</b>", parse_mode="HTML")
        return
    async with AsyncSessionFactory() as session:
        ok = await complete_goal(session, message.from_user.id, int(gid_str))
    if ok:
        await message.answer(
            f"🏆 Цель #{gid_str} достигнута! Отличная работа! 🎉\n\n"
            "Ты молодец — финансовая дисциплина приносит результаты!"
        )
    else:
        await message.answer(f"Цель #{gid_str} не найдена.")


# ── /export ───────────────────────────────────────────────────────────────────

@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    await message.answer("📦 Собираю финансовые данные...")
    async with AsyncSessionFactory() as session:
        text = await export_financial_data(session, message.from_user.id)

    file = BufferedInputFile(
        text.encode("utf-8"),
        filename="bakyt_export.txt",
    )
    await message.answer_document(file, caption="📦 Твои финансовые данные")


# ── Owner: whitelist management ───────────────────────────────────────────────

@router.message(Command("adduser"))
async def cmd_adduser(message: Message, command: CommandObject) -> None:
    if not _owner_only(message.from_user.id):
        await message.answer("⛔ Только для владельца.")
        return
    uid_str = (command.args or "").strip()
    if not uid_str.isdigit():
        await message.answer("Укажи Telegram ID: /adduser <b>12345678</b>", parse_mode="HTML")
        return
    uid = int(uid_str)
    async with AsyncSessionFactory() as session:
        user = await session.get(BakUser, uid)
        if user is None:
            user = BakUser(id=uid, is_whitelisted=True)
            session.add(user)
        else:
            user.is_whitelisted = True
        await session.commit()
    wl_add(uid)
    await message.answer(
        f"✅ Пользователь <code>{uid}</code> добавлен в whitelist.", parse_mode="HTML"
    )


@router.message(Command("removeuser"))
async def cmd_removeuser(message: Message, command: CommandObject) -> None:
    if not _owner_only(message.from_user.id):
        await message.answer("⛔ Только для владельца.")
        return
    uid_str = (command.args or "").strip()
    if not uid_str.isdigit():
        await message.answer(
            "Укажи Telegram ID: /removeuser <b>12345678</b>", parse_mode="HTML"
        )
        return
    uid = int(uid_str)
    if uid == settings.owner_id:
        await message.answer("Нельзя удалить владельца из whitelist.")
        return
    async with AsyncSessionFactory() as session:
        user = await session.get(BakUser, uid)
        if user:
            user.is_whitelisted = False
            await session.commit()
            wl_remove(uid)
            await message.answer(
                f"✅ Пользователь <code>{uid}</code> удалён из whitelist.",
                parse_mode="HTML",
            )
        else:
            await message.answer("Пользователь не найден.")


@router.message(Command("users"))
async def cmd_users(message: Message) -> None:
    if not _owner_only(message.from_user.id):
        await message.answer("⛔ Только для владельца.")
        return
    from sqlalchemy import select
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(BakUser).where(BakUser.is_whitelisted == True)  # noqa: E712
        )
        users = result.scalars().all()
    if not users:
        await message.answer("Whitelist пуст.")
        return
    lines = [f"👥 <b>Whitelist ({len(users)} чел.):</b>\n"]
    for u in users:
        name = " ".join(filter(None, [u.first_name, u.last_name])) or "—"
        uname = f"@{u.username}" if u.username else "нет username"
        lines.append(f"• <code>{u.id}</code> {name} ({uname})")
    await message.answer("\n".join(lines), parse_mode="HTML")
