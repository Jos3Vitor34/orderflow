import os

import pytest
from sqlalchemy import text

from app.db.session import engine


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1",
    reason="set RUN_POSTGRES_INTEGRATION_TESTS=1 to test PostgreSQL",
)
def test_phase18_user_role_migration_is_applied() -> None:
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        column = connection.execute(
            text(
                """
                SELECT is_nullable, column_default, udt_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'users'
                  AND column_name = 'role'
                """
            )
        ).one()
        enum_values = list(
            connection.scalars(
                text(
                    """
                    SELECT enumlabel
                    FROM pg_enum
                    JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
                    WHERE pg_type.typname = 'user_role'
                    ORDER BY enumsortorder
                    """
                )
            )
        )
        index_exists = connection.scalar(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND tablename = 'users'
                      AND indexname = 'ix_users_role'
                )
                """
            )
        )

    assert revision == "b6f42d1c8a90"
    assert column.is_nullable == "NO"
    assert "viewer" in column.column_default
    assert column.udt_name == "user_role"
    assert enum_values == ["admin", "operator", "viewer"]
    assert index_exists is True
