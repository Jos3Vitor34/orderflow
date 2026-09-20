"""add refunds and partial payment status

Revision ID: 9f3c2a1b7d14
Revises: 84212d8249d7
Create Date: 2026-09-20 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "9f3c2a1b7d14"
down_revision: str | Sequence[str] | None = "84212d8249d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE payment_status ADD VALUE IF NOT EXISTS "
        "'partially_refunded' BEFORE 'refunded'"
    )
    refund_status = postgresql.ENUM(
        "pending",
        "requires_action",
        "succeeded",
        "failed",
        "canceled",
        name="refund_status",
        create_type=False,
    )
    refund_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "refunds",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("payment_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("provider_refund_id", sa.String(length=255), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "status",
            refund_status,
            server_default="pending",
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("failure_reason", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("provider_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_provider_event_created_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name="ck_refunds_amount_positive"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "payment_id",
            "idempotency_key_hash",
            name="uq_refunds_payment_id_idempotency_key_hash",
        ),
        sa.UniqueConstraint(
            "provider_refund_id",
            name="uq_refunds_provider_refund_id",
        ),
    )
    op.create_index("ix_refunds_payment_id", "refunds", ["payment_id"])
    op.create_index("ix_refunds_status", "refunds", ["status"])


def downgrade() -> None:
    op.drop_index("ix_refunds_status", table_name="refunds")
    op.drop_index("ix_refunds_payment_id", table_name="refunds")
    op.drop_table("refunds")
    postgresql.ENUM(name="refund_status").drop(op.get_bind(), checkfirst=True)

    op.alter_column("payments", "status", server_default=None)
    op.alter_column(
        "payments",
        "status",
        type_=sa.String(length=32),
        postgresql_using="status::text",
    )
    op.execute("DROP TYPE payment_status")
    old_payment_status = postgresql.ENUM(
        "pending",
        "approved",
        "failed",
        "refunded",
        name="payment_status",
        create_type=False,
    )
    old_payment_status.create(op.get_bind())
    op.alter_column(
        "payments",
        "status",
        type_=old_payment_status,
        postgresql_using="status::payment_status",
    )
    op.execute(
        "ALTER TABLE payments ALTER COLUMN status SET DEFAULT 'pending'::payment_status"
    )
