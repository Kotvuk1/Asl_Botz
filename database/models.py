from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey,
    Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class MeirBase(DeclarativeBase):
    pass


# Alias for compatibility (used in db.py and alembic/env.py)
Base = MeirBase


class MeiUser(MeirBase):
    __tablename__ = "mei_users"

    id = Column(BigInteger, primary_key=True)
    username = Column(String(64), nullable=True)
    first_name = Column(String(128), nullable=True)
    last_name = Column(String(128), nullable=True)
    is_whitelisted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    messages = relationship("MeiMessage", back_populates="user", cascade="all, delete-orphan")
    memories = relationship("MeiMemory", back_populates="user", cascade="all, delete-orphan")
    mood_logs = relationship("MoodLog", back_populates="user", cascade="all, delete-orphan")
    journal_entries = relationship("JournalEntry", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<MeiUser id={self.id} username={self.username}>"


class MeiMessage(MeirBase):
    __tablename__ = "mei_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("mei_users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("MeiUser", back_populates="messages")


class MeiMemory(MeirBase):
    __tablename__ = "mei_memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("mei_users.id", ondelete="CASCADE"), nullable=False)
    key = Column(String(128), nullable=False)
    value = Column(String(1000), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("MeiUser", back_populates="memories")

    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_mei_memories_user_key"),
    )


class MoodLog(MeirBase):
    __tablename__ = "mood_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("mei_users.id", ondelete="CASCADE"), nullable=False)
    score = Column(Integer, nullable=False)  # 1-10
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("MeiUser", back_populates="mood_logs")


class JournalEntry(MeirBase):
    __tablename__ = "journal_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("mei_users.id", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    mood_score = Column(Integer, nullable=True)  # 1-10, optional
    tags = Column(String(256), nullable=True)  # comma-separated
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("MeiUser", back_populates="journal_entries")
