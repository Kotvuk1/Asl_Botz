import logging
import random
from datetime import timedelta, timezone

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from core.db import AsyncSessionFactory
from core.memory import (
    clear_history,
    format_memory_context,
    get_memories,
    delete_memory,
    save_memory,
)
from core.tools import (
    log_mood,
    get_mood_history,
    get_mood_stats,
    add_journal,
    get_journal,
    format_mood_history,
    format_mood_stats,
    format_journal,
    format_breathing_exercise,
    export_user_data,
    BREATHING_EXERCISES,
    AFFIRMATIONS,
)
from core.whitelist import add_user as wl_add, remove_user as wl_remove
from database.models import MeiUser

logger = logging.getLogger(__name__)
router = Router(name="commands")


def _owner_only(user_id: int) -> bool:
    return user_id == settings.owner_id


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    name = message.from_user.first_name or "друг"
    await message.answer(
        f"Привет, <b>{name}</b>! 💙\n\n"
        f"Я — <b>{settings.bot_name}</b>, твой персональный психолог.\n"
        "Моё имя означает «доброта» на казахском языке.\n\n"
        "Я здесь, чтобы выслушать тебя, поддержать и помочь разобраться в себе.\n\n"
        "Ты можешь:\n"
        "• Просто написать, что у тебя на душе\n"
        "• Записать своё настроение — /mood\n"
        "• Вести дневник — /journal\n"
        "• Сделать дыхательное упражнение — /breathe\n"
        "• Получить аффирмацию — /affirmation\n\n"
        "Как ты сегодня? 🌿",
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
        f"<b>📖 Что умеет {settings.bot_name}:</b>\n\n"
        "<b>💬 Общение:</b>\n"
        "Просто напиши мне — я выслушаю и поддержу\n"
        "Можно говорить голосовыми сообщениями 🎙\n\n"
        "<b>😊 Настроение:</b>\n"
        "/mood &lt;1-10&gt; [заметка] — записать настроение\n"
        "  Пример: /mood 7 устал, но справляюсь\n"
        "/history — последние 7 записей с визуализацией\n"
        "/stats — статистика за 30 дней (среднее, тренд)\n\n"
        "<b>📔 Дневник:</b>\n"
        "/journal &lt;текст&gt; — записать в дневник\n"
        "  Пример: /journal Сегодня я понял что...\n\n"
        "<b>🧘 Упражнения:</b>\n"
        "/breathe — дыхательное упражнение (на выбор)\n"
        "/affirmation — случайная позитивная аффирмация\n\n"
        "<b>🧠 Память:</b>\n"
        "/memory — что я помню о тебе\n"
        "/remember &lt;ключ&gt; = &lt;значение&gt; — запомнить факт\n"
        "/forget &lt;ключ&gt; — забыть факт\n\n"
        "<b>Прочее:</b>\n"
        "/clear — очистить историю разговора\n"
        "/export — экспорт дневника и настроений (.txt)"
        + owner_section,
        parse_mode="HTML",
    )


# ── /mood ─────────────────────────────────────────────────────────────────────

@router.message(Command("mood"))
async def cmd_mood(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "Запиши своё настроение:\n"
            "/mood <b>1-10</b> [заметка]\n\n"
            "Примеры:\n"
            "<code>/mood 7</code>\n"
            "<code>/mood 8 отличный день!</code>\n"
            "<code>/mood 4 тревожно, не знаю почему</code>",
            parse_mode="HTML",
        )
        return

    parts = args.split(maxsplit=1)
    score_str = parts[0]
    notes = parts[1].strip() if len(parts) > 1 else None

    if not score_str.isdigit() or not (1 <= int(score_str) <= 10):
        await message.answer(
            "Оценка должна быть числом от 1 до 10.\n"
            "Пример: /mood <b>7</b>",
            parse_mode="HTML",
        )
        return

    score = int(score_str)

    async with AsyncSessionFactory() as session:
        await log_mood(session, message.from_user.id, score, notes)

    bar = "▓" * score + "░" * (10 - score)

    # Choose response based on score
    if score <= 3:
        response = (
            f"Записал: [{bar}] {score}/10\n\n"
            "Звучит тяжело. Я здесь, если хочешь поговорить о том, что происходит. 💙"
        )
    elif score <= 5:
        response = (
            f"Записал: [{bar}] {score}/10\n\n"
            "Серединка. Как ты себя чувствуешь прямо сейчас — что больше всего занимает мысли?"
        )
    elif score <= 7:
        response = (
            f"Записал: [{bar}] {score}/10\n\n"
            "Неплохо! Что помогло тебе сегодня чувствовать себя так? 🌿"
        )
    else:
        response = (
            f"Записал: [{bar}] {score}/10\n\n"
            "Отлично! Рад за тебя. Что сделало этот день таким хорошим? ✨"
        )

    await message.answer(response, parse_mode="HTML")


# ── /journal ──────────────────────────────────────────────────────────────────

@router.message(Command("journal"))
async def cmd_journal(message: Message, command: CommandObject) -> None:
    text = (command.args or "").strip()
    if not text:
        await message.answer(
            "Запиши мысль в дневник:\n"
            "/journal <b>текст</b>\n\n"
            "Примеры:\n"
            "<code>/journal Сегодня я понял что...</code>\n"
            "<code>/journal Меня беспокоит работа, но я стараюсь</code>",
            parse_mode="HTML",
        )
        return

    async with AsyncSessionFactory() as session:
        await add_journal(session, message.from_user.id, content=text)

    await message.answer(
        "📔 Записал в дневник.\n\n"
        "<i>Записывать мысли — это уже маленький шаг к пониманию себя.</i>",
        parse_mode="HTML",
    )


# ── /history ──────────────────────────────────────────────────────────────────

@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        logs = await get_mood_history(session, message.from_user.id, limit=7)
    await message.answer(format_mood_history(logs), parse_mode="HTML")


# ── /stats ────────────────────────────────────────────────────────────────────

@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        stats = await get_mood_stats(session, message.from_user.id, days=30)
    await message.answer(format_mood_stats(stats), parse_mode="HTML")


# ── /breathe ──────────────────────────────────────────────────────────────────

@router.message(Command("breathe"))
async def cmd_breathe(message: Message) -> None:
    builder = InlineKeyboardBuilder()
    builder.button(text="🌬 4-7-8 (тревога, сон)", callback_data="breathe:478")
    builder.button(text="📦 Коробочное (стресс)", callback_data="breathe:box")
    builder.button(text="😌 Успокаивающее", callback_data="breathe:calm")
    builder.adjust(1)

    await message.answer(
        "🧘 Выбери технику дыхания:\n\n"
        "<i>Каждая техника занимает 2-5 минут и помогает снизить уровень стресса.</i>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data and c.data.startswith("breathe:"))
async def cb_breathe(callback: CallbackQuery) -> None:
    technique = callback.data.split(":")[1]
    text = format_breathing_exercise(technique)
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()


# ── /affirmation ──────────────────────────────────────────────────────────────

@router.message(Command("affirmation"))
async def cmd_affirmation(message: Message) -> None:
    affirmation = random.choice(AFFIRMATIONS)
    await message.answer(
        f"✨ <i>{affirmation}</i>\n\n"
        "Повтори это про себя несколько раз. Ты в это веришь?",
        parse_mode="HTML",
    )


# ── /clear ────────────────────────────────────────────────────────────────────

@router.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    from core.memory import clear_history
    async with AsyncSessionFactory() as session:
        count = await clear_history(session, message.from_user.id)
    await message.answer(
        f"История разговора очищена ({count} сообщений удалено).\n"
        "Начинаем с чистого листа! 🌱"
    )


# ── /memory ───────────────────────────────────────────────────────────────────

@router.message(Command("memory"))
async def cmd_memory(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        ctx = await format_memory_context(session, message.from_user.id)
    await message.answer(
        f"<b>🧠 Что я помню о тебе:</b>\n\n{ctx}",
        parse_mode="HTML",
    )


@router.message(Command("remember"))
async def cmd_remember(message: Message, command: CommandObject) -> None:
    args = command.args
    if not args or "=" not in args:
        await message.answer(
            "Формат: /remember <b>ключ</b> = <b>значение</b>\n"
            "Пример: /remember имя = Айгерим",
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
        await message.answer(f"Забыл: <b>{key}</b>", parse_mode="HTML")
    else:
        await message.answer(f"Факт <b>{key}</b> не найден.", parse_mode="HTML")


# ── /export ───────────────────────────────────────────────────────────────────

@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    await message.answer("Собираю данные...")
    async with AsyncSessionFactory() as session:
        text = await export_user_data(session, message.from_user.id)

    file = BufferedInputFile(
        text.encode("utf-8"),
        filename="meir_export.txt",
    )
    await message.answer_document(file, caption="📦 Твои данные из Мейір")


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
        user = await session.get(MeiUser, uid)
        if user is None:
            user = MeiUser(id=uid, is_whitelisted=True)
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
        user = await session.get(MeiUser, uid)
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
            select(MeiUser).where(MeiUser.is_whitelisted == True)  # noqa: E712
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
