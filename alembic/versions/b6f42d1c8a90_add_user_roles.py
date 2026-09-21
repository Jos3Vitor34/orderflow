"""add user roles

Revision ID: b6f42d1c8a90
Revises: 9f3c2a1b7d14
Create Date: 2026-09-21 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b6f42d1c8a90"
down_revision: str | Sequence[str] | None = "9f3c2a1b7d14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    user_role = postgresql.ENUM(
        "admin",
        "operator",
        "viewer",
        name="user_role",
        create_type=False,
    )
    user_role.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "users",
        sa.Column("role", user_role, server_default="viewer", nullable=False),
    )
    op.execute("UPDATE users SET role = 'admin'::user_role")
    op.create_index("ix_users_role", "users", ["role"])


def downgrade() -> None:
    op.drop_index("ix_users_role", table_name="users")
    op.drop_column("users", "role")
    postgresql.ENUM(name="user_role").drop(op.get_bind(), checkfirst=True)
