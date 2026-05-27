"""add major features: deadline/priority tasks, repeat reminders, goals, habits

Revision ID: 004
Revises: 003
Create Date: 2026-05-26
"""
from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── tasks: add deadline and priority ──────────────────────────────────────
    op.add_column("tasks", sa.Column("deadline", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column(
        "priority", sa.String(8), server_default="medium", nullable=False,
    ))

    # ── reminders: add repeat_pattern ─────────────────────────────────────────
    op.add_column("reminders", sa.Column("repeat_pattern", sa.String(32), nullable=True))

    # ── goals ─────────────────────────────────────────────────────────────────
    op.create_table(
        "goals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_goals_user_id_status", "goals", ["user_id", "status"])

    # ── habits ────────────────────────────────────────────────────────────────
    op.create_table(
        "habits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("frequency", sa.String(16), server_default="daily", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_habits_user_id_active", "habits", ["user_id", "is_active"])

    # ── habit_logs ────────────────────────────────────────────────────────────
    op.create_table(
        "habit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("habit_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("logged_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["habit_id"], ["habits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("habit_id", "logged_date", name="uq_habit_logs_habit_date"),
    )
    op.create_index("ix_habit_logs_habit_date", "habit_logs", ["habit_id", "logged_date"])

    # ── deadline_alerts ───────────────────────────────────────────────────────
    op.create_table(
        "deadline_alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("threshold", sa.String(8), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "threshold", name="uq_deadline_alerts"),
    )
    op.create_index("ix_deadline_alerts_task_threshold", "deadline_alerts",
                    ["task_id", "threshold"])


def downgrade() -> None:
    op.drop_index("ix_deadline_alerts_task_threshold", table_name="deadline_alerts")
    op.drop_table("deadline_alerts")
    op.drop_index("ix_habit_logs_habit_date", table_name="habit_logs")
    op.drop_table("habit_logs")
    op.drop_index("ix_habits_user_id_active", table_name="habits")
    op.drop_table("habits")
    op.drop_index("ix_goals_user_id_status", table_name="goals")
    op.drop_table("goals")
    op.drop_column("reminders", "repeat_pattern")
    op.drop_column("tasks", "priority")
    op.drop_column("tasks", "deadline")
