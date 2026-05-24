"""add task status column

Revision ID: 002
Revises: 001
Create Date: 2026-05-24
"""
from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("status", sa.String(16), server_default="todo", nullable=False),
    )
    op.execute("UPDATE tasks SET status = 'done' WHERE is_done = true")
    op.execute("UPDATE tasks SET status = 'todo' WHERE is_done = false")
    op.create_index("ix_tasks_user_id_status", "tasks", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_tasks_user_id_status", table_name="tasks")
    op.drop_column("tasks", "status")
