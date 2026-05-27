from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey,
    Integer, Numeric, String, Text, UniqueConstraint, func, Index,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class BakUser(Base):
    __tablename__ = "bak_users"

    id = Column(BigInteger, primary_key=True)
    username = Column(String(64), nullable=True)
    first_name = Column(String(128), nullable=True)
    last_name = Column(String(128), nullable=True)
    is_whitelisted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    messages = relationship("BakMessage", back_populates="user", cascade="all, delete-orphan")
    memories = relationship("BakMemory", back_populates="user", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="user", cascade="all, delete-orphan")
    budgets = relationship("Budget", back_populates="user", cascade="all, delete-orphan")
    financial_goals = relationship("FinancialGoal", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<BakUser id={self.id} username={self.username}>"


class BakMessage(Base):
    __tablename__ = "bak_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("bak_users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(16), nullable=False)  # "user" or "assistant"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("BakUser", back_populates="messages")

    __table_args__ = (
        Index("ix_bak_messages_user_id_created_at", "user_id", "created_at"),
    )


class BakMemory(Base):
    __tablename__ = "bak_memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("bak_users.id", ondelete="CASCADE"), nullable=False)
    key = Column(String(128), nullable=False)
    value = Column(String(1000), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("BakUser", back_populates="memories")

    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_bak_memories_user_key"),
        Index("ix_bak_memories_user_id_key", "user_id", "key"),
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("bak_users.id", ondelete="CASCADE"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)   # always positive
    type = Column(String(8), nullable=False)           # "income" or "expense"
    category = Column(String(64), default="другое", nullable=False)
    description = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("BakUser", back_populates="transactions")

    __table_args__ = (
        Index("ix_transactions_user_id_created_at", "user_id", "created_at"),
        Index("ix_transactions_user_id_type", "user_id", "type"),
    )


class Budget(Base):
    __tablename__ = "budgets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("bak_users.id", ondelete="CASCADE"), nullable=False)
    category = Column(String(64), nullable=False)
    limit_amount = Column(Numeric(12, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("BakUser", back_populates="budgets")

    __table_args__ = (
        UniqueConstraint("user_id", "category", name="uq_budgets_user_category"),
        Index("ix_budgets_user_id", "user_id"),
    )


class FinancialGoal(Base):
    __tablename__ = "financial_goals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("bak_users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(256), nullable=False)
    target_amount = Column(Numeric(12, 2), nullable=False)
    current_amount = Column(Numeric(12, 2), default=0, nullable=False)
    deadline = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(16), default="active", nullable=False)  # active/done/archived
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("BakUser", back_populates="financial_goals")

    __table_args__ = (
        Index("ix_financial_goals_user_id_status", "user_id", "status"),
    )
