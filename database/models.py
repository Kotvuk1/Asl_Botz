from sqlalchemy import (
    BigInteger, Boolean, Column, Date, DateTime, ForeignKey,
    Integer, String, Text, func, Index,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True)
    username = Column(String(64), nullable=True)
    first_name = Column(String(128), nullable=True)
    last_name = Column(String(128), nullable=True)
    is_whitelisted = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    messages = relationship("Message", back_populates="user", cascade="all, delete-orphan")
    memories = relationship("Memory", back_populates="user", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="user", cascade="all, delete-orphan")
    reminders = relationship("Reminder", back_populates="user", cascade="all, delete-orphan")
    goals = relationship("Goal", back_populates="user", cascade="all, delete-orphan")
    habits = relationship("Habit", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username}>"


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="messages")

    __table_args__ = (
        Index("ix_messages_user_id_created_at", "user_id", "created_at"),
    )


class Memory(Base):
    __tablename__ = "memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    key = Column(String(128), nullable=False)
    value = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="memories")

    __table_args__ = (
        Index("ix_memories_user_id_key", "user_id", "key", unique=True),
    )


class Task(Base):
    """Task with priority and optional deadline."""
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(512), nullable=False)
    description = Column(Text, nullable=True)
    is_done = Column(Boolean, default=False, nullable=False)
    # status: todo | in_progress | done
    status = Column(String(16), default="todo", server_default="todo", nullable=False)
    # priority: high | medium | low
    priority = Column(String(8), default="medium", server_default="medium", nullable=False)
    deadline = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="tasks")
    deadline_alerts = relationship("DeadlineAlert", back_populates="task", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_tasks_user_id_is_done", "user_id", "is_done"),
        Index("ix_tasks_user_id_status", "user_id", "status"),
    )


class Reminder(Base):
    """Scheduled reminder with optional recurring pattern."""
    __tablename__ = "reminders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    text = Column(Text, nullable=False)
    remind_at = Column(DateTime(timezone=True), nullable=False)
    is_sent = Column(Boolean, default=False, nullable=False)
    # repeat_pattern: None=one-time | "daily" | "weekly" | "weekdays" | "monthly"
    repeat_pattern = Column(String(32), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="reminders")

    __table_args__ = (
        Index("ix_reminders_remind_at_sent", "remind_at", "is_sent"),
    )


class Goal(Base):
    """Long-term goal with progress tracking."""
    __tablename__ = "goals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(512), nullable=False)
    description = Column(Text, nullable=True)
    deadline = Column(DateTime(timezone=True), nullable=True)
    # status: active | done | archived
    status = Column(String(16), default="active", server_default="active", nullable=False)
    progress = Column(Integer, default=0, server_default="0", nullable=False)  # 0–100
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="goals")

    __table_args__ = (
        Index("ix_goals_user_id_status", "user_id", "status"),
    )


class Habit(Base):
    """Daily/weekly habit tracker."""
    __tablename__ = "habits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(256), nullable=False)
    frequency = Column(String(16), default="daily", server_default="daily", nullable=False)
    is_active = Column(Boolean, default=True, server_default="true", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="habits")
    logs = relationship("HabitLog", back_populates="habit", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_habits_user_id_active", "user_id", "is_active"),
    )


class HabitLog(Base):
    """One log entry per habit per day."""
    __tablename__ = "habit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    habit_id = Column(Integer, ForeignKey("habits.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    logged_date = Column(Date, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    habit = relationship("Habit", back_populates="logs")

    __table_args__ = (
        Index("ix_habit_logs_habit_date", "habit_id", "logged_date", unique=True),
    )


class DeadlineAlert(Base):
    """Tracks which deadline alerts have been sent for a task."""
    __tablename__ = "deadline_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    # threshold: "7d" | "3d" | "1d" | "10h" | "1h"
    threshold = Column(String(8), nullable=False)
    sent_at = Column(DateTime(timezone=True), server_default=func.now())

    task = relationship("Task", back_populates="deadline_alerts")

    __table_args__ = (
        Index("ix_deadline_alerts_task_threshold", "task_id", "threshold", unique=True),
    )
