import os
from collections.abc import Generator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pwdlib import PasswordHash
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.session import engine, get_db
from app.main import app
from app.models.user import User


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1",
    reason="set RUN_POSTGRES_INTEGRATION_TESTS=1 to test PostgreSQL",
)
def test_complete_auth_flow_against_postgresql() -> None:
    schema_name = f"phase6_auth_{uuid4().hex}"

    with engine.connect() as connection:
        connection.execute(CreateSchema(schema_name))
        connection.commit()
        connection.exec_driver_sql(f'SET search_path TO "{schema_name}", public')
        User.__table__.create(connection)
        connection.commit()

        def override_get_db() -> Generator[Session]:
            with Session(bind=connection, expire_on_commit=False) as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                register_response = client.post(
                    "/api/v1/auth/register",
                    json={
                        "full_name": "PostgreSQL User",
                        "email": "POSTGRES@example.com",
                        "password": "strong-password",
                    },
                )
                assert register_response.status_code == 201
                assert register_response.json()["email"] == "postgres@example.com"
                assert "hashed_password" not in register_response.json()

                duplicate_response = client.post(
                    "/api/v1/auth/register",
                    json={
                        "full_name": "Duplicate",
                        "email": "postgres@EXAMPLE.com",
                        "password": "another-password",
                    },
                )
                assert duplicate_response.status_code == 409

                with Session(bind=connection) as inspection_session:
                    saved_user = inspection_session.scalar(select(User))
                    assert saved_user is not None
                    assert saved_user.hashed_password != "strong-password"
                    assert PasswordHash.recommended().verify(
                        "strong-password",
                        saved_user.hashed_password,
                    )

                login_response = client.post(
                    "/api/v1/auth/login",
                    data={
                        "username": "postgres@example.com",
                        "password": "strong-password",
                    },
                )
                assert login_response.status_code == 200
                token = login_response.json()["access_token"]

                me_response = client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert me_response.status_code == 200
                assert me_response.json()["email"] == "postgres@example.com"
                assert "hashed_password" not in me_response.json()
        finally:
            app.dependency_overrides.clear()
            connection.rollback()
            connection.exec_driver_sql("RESET search_path")
            connection.execute(DropSchema(schema_name, cascade=True, if_exists=True))
            connection.commit()

    with engine.connect() as verification_connection:
        remaining_schema_count = verification_connection.scalar(
            text("SELECT count(*) FROM pg_namespace WHERE nspname = :schema_name"),
            {"schema_name": schema_name},
        )
        assert remaining_schema_count == 0
