"""
Financial tools for Бакыт bot.
Provides CRUD operations and formatting for transactions, budgets, and goals.
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import settings
from database.models import Budget, FinancialGoal, Transaction

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _since(days: int) -> datetime:
    return _utc_now() - timedelta(days=days)


def _fmt_amount(amount) -> str:
    """Format amount as integer if whole number, otherwise 2 decimals."""
    val = Decimal(str(amount))
    if val == val.to_integral_value():
        return f"{int(val):,}".replace(",", " ")
    return f"{val:,.2f}".replace(",", " ")


# ── Transactions ──────────────────────────────────────────────────────────────

async def add_transaction(
    session: AsyncSession,
    user_id: int,
    amount: float,
    tx_type: str,
    category: str = "другое",
    description: Optional[str] = None,
) -> Transaction:
    """Add a transaction. amount must be positive; tx_type is 'income' or 'expense'."""
    tx = Transaction(
        user_id=user_id,
        amount=abs(amount),
        type=tx_type,
        category=category.strip()[:64] if category else "другое",
        description=description.strip()[:256] if description else None,
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    logger.info(
        "Transaction added: user=%d type=%s amount=%.2f cat=%s",
        user_id, tx_type, float(tx.amount), tx.category,
    )
    return tx


async def get_balance(session: AsyncSession, user_id: int, days: int = 30) -> Dict:
    """Return income_total, expense_total, balance for the given period."""
    since = _since(days)

    income_result = await session.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0))
        .where(
            Transaction.user_id == user_id,
            Transaction.type == "income",
            Transaction.created_at >= since,
        )
    )
    expense_result = await session.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0))
        .where(
            Transaction.user_id == user_id,
            Transaction.type == "expense",
            Transaction.created_at >= since,
        )
    )
    income_total = Decimal(str(income_result.scalar() or 0))
    expense_total = Decimal(str(expense_result.scalar() or 0))
    return {
        "income_total": income_total,
        "expense_total": expense_total,
        "balance": income_total - expense_total,
        "days": days,
    }


async def get_transactions(
    session: AsyncSession,
    user_id: int,
    limit: int = 20,
    days: int = 30,
) -> List[Transaction]:
    since = _since(days)
    result = await session.execute(
        select(Transaction)
        .where(Transaction.user_id == user_id, Transaction.created_at >= since)
        .order_by(Transaction.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


async def get_category_stats(
    session: AsyncSession,
    user_id: int,
    days: int = 30,
    tx_type: str = "expense",
) -> Dict[str, Decimal]:
    """Returns dict of category → total amount for the period."""
    since = _since(days)
    result = await session.execute(
        select(Transaction.category, func.sum(Transaction.amount))
        .where(
            Transaction.user_id == user_id,
            Transaction.type == tx_type,
            Transaction.created_at >= since,
        )
        .group_by(Transaction.category)
        .order_by(func.sum(Transaction.amount).desc())
    )
    rows = result.all()
    return {row[0]: Decimal(str(row[1])) for row in rows}


# ── Budgets ───────────────────────────────────────────────────────────────────

async def set_budget(
    session: AsyncSession,
    user_id: int,
    category: str,
    limit_amount: float,
) -> Budget:
    """Upsert a budget limit for a category."""
    category = category.strip()[:64]
    stmt = (
        pg_insert(Budget)
        .values(user_id=user_id, category=category, limit_amount=abs(limit_amount))
        .on_conflict_do_update(
            constraint="uq_budgets_user_category",
            set_={"limit_amount": abs(limit_amount)},
        )
    )
    await session.execute(stmt)
    await session.commit()

    result = await session.execute(
        select(Budget).where(Budget.user_id == user_id, Budget.category == category)
    )
    return result.scalar_one()


async def get_budgets(session: AsyncSession, user_id: int) -> List[Budget]:
    result = await session.execute(
        select(Budget)
        .where(Budget.user_id == user_id)
        .order_by(Budget.category)
    )
    return result.scalars().all()


# ── Financial Goals ───────────────────────────────────────────────────────────

async def add_financial_goal(
    session: AsyncSession,
    user_id: int,
    title: str,
    target: float,
    deadline: Optional[datetime] = None,
) -> FinancialGoal:
    goal = FinancialGoal(
        user_id=user_id,
        title=title.strip()[:256],
        target_amount=abs(target),
        current_amount=Decimal("0"),
        deadline=deadline,
        status="active",
    )
    session.add(goal)
    await session.commit()
    await session.refresh(goal)
    return goal


async def get_financial_goals(
    session: AsyncSession,
    user_id: int,
) -> List[FinancialGoal]:
    result = await session.execute(
        select(FinancialGoal)
        .where(FinancialGoal.user_id == user_id)
        .order_by(FinancialGoal.created_at.asc())
    )
    return result.scalars().all()


async def update_goal_amount(
    session: AsyncSession,
    user_id: int,
    goal_id: int,
    amount: float,
) -> bool:
    """Add amount to goal's current_amount (can be negative to subtract)."""
    goal = await session.get(FinancialGoal, goal_id)
    if not goal or goal.user_id != user_id:
        return False
    new_amount = Decimal(str(goal.current_amount)) + Decimal(str(amount))
    if new_amount < 0:
        new_amount = Decimal("0")
    goal.current_amount = new_amount
    await session.commit()
    return True


async def complete_goal(
    session: AsyncSession,
    user_id: int,
    goal_id: int,
) -> bool:
    result = await session.execute(
        update(FinancialGoal)
        .where(FinancialGoal.id == goal_id, FinancialGoal.user_id == user_id)
        .values(status="done")
    )
    await session.commit()
    return result.rowcount > 0


# ── Finance context for LLM ───────────────────────────────────────────────────

async def get_finance_context(session: AsyncSession, user_id: int, days: int = 30) -> str:
    """Build a short finance summary to inject into the system prompt."""
    balance = await get_balance(session, user_id, days=days)
    cat_stats = await get_category_stats(session, user_id, days=days)

    lines = [
        f"Период: последние {days} дней",
        f"Доходы: {_fmt_amount(balance['income_total'])} тг",
        f"Расходы: {_fmt_amount(balance['expense_total'])} тг",
        f"Баланс: {_fmt_amount(balance['balance'])} тг",
    ]

    if cat_stats:
        lines.append("Расходы по категориям:")
        for cat, amt in list(cat_stats.items())[:8]:
            lines.append(f"  {cat}: {_fmt_amount(amt)} тг")

    goals = await get_financial_goals(session, user_id)
    active_goals = [g for g in goals if g.status == "active"]
    if active_goals:
        lines.append("Финансовые цели:")
        for g in active_goals[:5]:
            pct = (
                int(Decimal(str(g.current_amount)) / Decimal(str(g.target_amount)) * 100)
                if g.target_amount > 0 else 0
            )
            lines.append(
                f"  [{pct}%] {g.title}: {_fmt_amount(g.current_amount)}"
                f" / {_fmt_amount(g.target_amount)} тг"
            )

    return "\n".join(lines)


# ── Formatting ────────────────────────────────────────────────────────────────

def format_balance(balance_dict: Dict) -> str:
    inc = balance_dict["income_total"]
    exp = balance_dict["expense_total"]
    bal = balance_dict["balance"]
    days = balance_dict.get("days", 30)

    bal_sign = "+" if bal >= 0 else ""
    bal_icon = "📈" if bal >= 0 else "📉"

    return (
        f"<b>💰 Баланс за {days} дней:</b>\n\n"
        f"📥 Доходы:  <b>{_fmt_amount(inc)} тг</b>\n"
        f"📤 Расходы: <b>{_fmt_amount(exp)} тг</b>\n"
        f"─────────────────\n"
        f"{bal_icon} Итого: <b>{bal_sign}{_fmt_amount(bal)} тг</b>"
    )


def format_transactions(transactions: List[Transaction]) -> str:
    if not transactions:
        return "📋 Транзакций за этот период нет."

    lines = [f"<b>📋 Последние транзакции ({len(transactions)}):</b>\n"]
    for tx in transactions:
        icon = "📥" if tx.type == "income" else "📤"
        sign = "+" if tx.type == "income" else "−"
        dt = tx.created_at
        if dt and dt.tzinfo:
            local_dt = dt + timedelta(hours=settings.tz_offset)
            date_str = local_dt.strftime("%d.%m %H:%M")
        else:
            date_str = "—"
        desc = f" <i>({tx.description})</i>" if tx.description else ""
        lines.append(
            f"{icon} {sign}{_fmt_amount(tx.amount)} тг  "
            f"<b>{tx.category}</b>{desc}  <code>{date_str}</code>"
        )
    return "\n".join(lines)


def format_category_stats(stats: Dict[str, Decimal]) -> str:
    if not stats:
        return "📊 Данных о расходах нет."

    total = sum(stats.values())
    lines = [f"<b>📊 Расходы по категориям:</b>\n"]

    for cat, amt in stats.items():
        pct = int(amt / total * 100) if total > 0 else 0
        filled = round(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        lines.append(
            f"<b>{cat}</b>  [{bar}] {pct}%\n"
            f"   {_fmt_amount(amt)} тг\n"
        )

    lines.append(f"<b>Итого расходов: {_fmt_amount(total)} тг</b>")
    return "\n".join(lines)


def format_budgets(budgets: List[Budget], category_stats: Dict[str, Decimal]) -> str:
    if not budgets:
        return (
            "💼 Бюджеты не установлены.\n"
            "Добавь: /budget <b>категория</b> <b>сумма</b>"
        )

    lines = ["<b>💼 Бюджеты по категориям:</b>\n"]
    for b in budgets:
        spent = category_stats.get(b.category, Decimal("0"))
        limit = Decimal(str(b.limit_amount))
        pct = int(spent / limit * 100) if limit > 0 else 0
        pct = min(pct, 100)

        if pct >= 100:
            bar_icon = "🔴"
        elif pct >= 80:
            bar_icon = "🟠"
        elif pct >= 50:
            bar_icon = "🟡"
        else:
            bar_icon = "🟢"

        filled = round(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        remaining = limit - spent
        rem_str = f"Остаток: {_fmt_amount(remaining)} тг" if remaining >= 0 else f"Перерасход: {_fmt_amount(-remaining)} тг"

        lines.append(
            f"{bar_icon} <b>{b.category}</b>  [{bar}] {pct}%\n"
            f"   Потрачено: {_fmt_amount(spent)} тг / {_fmt_amount(limit)} тг\n"
            f"   {rem_str}\n"
        )
    return "\n".join(lines)


def format_goals(goals: List[FinancialGoal]) -> str:
    if not goals:
        return (
            "🎯 Финансовых целей нет.\n"
            "Добавь: /addgoal <b>название</b> <b>сумма</b>"
        )

    status_icon = {"active": "🎯", "done": "✅", "archived": "📦"}
    lines = ["<b>🎯 Финансовые цели:</b>\n"]

    for g in goals:
        icon = status_icon.get(g.status, "🎯")
        target = Decimal(str(g.target_amount))
        current = Decimal(str(g.current_amount))
        pct = int(current / target * 100) if target > 0 else 0
        pct = min(pct, 100)
        filled = round(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)

        dl_str = ""
        if g.deadline:
            local_dl = g.deadline + timedelta(hours=settings.tz_offset)
            dl_str = f"  📅 до {local_dl.strftime('%d.%m.%Y')}"

        lines.append(
            f"{icon} <b>#{g.id}</b> {g.title}{dl_str}\n"
            f"   [{bar}] {pct}%  "
            f"{_fmt_amount(current)} / {_fmt_amount(target)} тг\n"
        )

    return "\n".join(lines)


async def export_financial_data(session: AsyncSession, user_id: int) -> str:
    """Export all financial data for user as plain text."""
    from core.memory import get_memories  # late import to avoid circular

    now_str = (datetime.now(timezone.utc) + timedelta(hours=settings.tz_offset)).strftime("%d.%m.%Y %H:%M")
    lines = [
        "═══════════════════════════════════",
        "Экспорт данных Бакыт (финансовый помощник)",
        f"Дата: {now_str} (UTC+{settings.tz_offset})",
        "═══════════════════════════════════",
        "",
    ]

    # Memory
    memories = await get_memories(session, user_id)
    lines.append("── ПАМЯТЬ ──────────────────────────")
    if memories:
        for m in memories:
            lines.append(f"{m.key} = {m.value}")
    else:
        lines.append("(пусто)")
    lines.append("")

    # Balance summary
    balance = await get_balance(session, user_id, days=30)
    lines.append("── БАЛАНС (последние 30 дней) ──────")
    lines.append(f"Доходы:  {_fmt_amount(balance['income_total'])} тг")
    lines.append(f"Расходы: {_fmt_amount(balance['expense_total'])} тг")
    lines.append(f"Итого:   {_fmt_amount(balance['balance'])} тг")
    lines.append("")

    # Transactions
    txs = await get_transactions(session, user_id, limit=100, days=90)
    lines.append("── ТРАНЗАКЦИИ (последние 90 дней) ──")
    if txs:
        for tx in txs:
            dt = tx.created_at
            date_str = (dt + timedelta(hours=settings.tz_offset)).strftime("%d.%m.%Y %H:%M") if dt else "—"
            sign = "+" if tx.type == "income" else "-"
            desc = f" ({tx.description})" if tx.description else ""
            lines.append(
                f"{date_str}  {sign}{_fmt_amount(tx.amount)} тг  {tx.category}{desc}"
            )
    else:
        lines.append("(пусто)")
    lines.append("")

    # Budgets
    budgets = await get_budgets(session, user_id)
    cat_stats = await get_category_stats(session, user_id, days=30)
    lines.append("── БЮДЖЕТЫ ─────────────────────────")
    if budgets:
        for b in budgets:
            spent = cat_stats.get(b.category, Decimal("0"))
            lines.append(
                f"{b.category}: лимит {_fmt_amount(b.limit_amount)} тг, "
                f"потрачено {_fmt_amount(spent)} тг"
            )
    else:
        lines.append("(пусто)")
    lines.append("")

    # Financial goals
    goals = await get_financial_goals(session, user_id)
    lines.append("── ФИНАНСОВЫЕ ЦЕЛИ ─────────────────")
    if goals:
        for g in goals:
            s_icon = {"done": "✓", "active": "○", "archived": "—"}.get(g.status, "○")
            dl_str = ""
            if g.deadline:
                local_dl = g.deadline + timedelta(hours=settings.tz_offset)
                dl_str = f" → до {local_dl.strftime('%d.%m.%Y')}"
            target = Decimal(str(g.target_amount))
            current = Decimal(str(g.current_amount))
            pct = int(current / target * 100) if target > 0 else 0
            lines.append(
                f"{s_icon} #{g.id} [{pct}%] {g.title}{dl_str}\n"
                f"   {_fmt_amount(current)} / {_fmt_amount(target)} тг"
            )
    else:
        lines.append("(пусто)")

    return "\n".join(lines)
