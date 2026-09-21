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
from tests.integration_helpers import create_test_user


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1",
    reason="set RUN_POSTGRES_INTEGRATION_TESTS=1 to test PostgreSQL",
)
def test_complete_auth_and_authorization_flow_against_postgresql() -> None:
    schema_name = f"phase18_auth_{uuid4().hex}"

    with engine.connect() as connection:
        connection.execute(CreateSchema(schema_name))
        connection.commit()
        connection.exec_driver_sql(f'SET search_path TO "{schema_name}", public')
        User.__table__.create(connection)
        connection.commit()

        def override_get_db() -> Generator[Session]:
            with Session(bind=connection, expire_on_commit=False) as session:
                yield session

        create_test_user(
            connection,
            full_name="PostgreSQL Admin",
            email="postgres@example.com",
        )
        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                public_signup = client.post(
                    "/api/v1/auth/register",
                    json={
                        "full_name": "Public",
                        "email": "public@example.com",
                        "password": "strong-password",
                    },
                )
                assert public_signup.status_code == 404

                login_response = client.post(
                    "/api/v1/auth/login",
                    data={
                        "username": "POSTGRES@example.com",
                        "password": "strong-password",
                    },
                )
                assert login_response.status_code == 200
                admin_headers = {
                    "Authorization": f"Bearer {login_response.json()['access_token']}"
                }

                created = client.post(
                    "/api/v1/users",
                    headers=admin_headers,
                    json={
                        "full_name": "PostgreSQL Viewer",
                        "email": "viewer@example.com",
                        "password": "viewer-password",
                        "role": "viewer",
                    },
                )
                assert created.status_code == 201
                assert created.json()["role"] == "viewer"
                assert "hashed_password" not in created.text

                duplicate = client.post(
                    "/api/v1/users",
                    headers=admin_headers,
                    json={
                        "full_name": "Duplicate",
                        "email": "VIEWER@example.com",
                        "password": "another-password",
                    },
                )
                assert duplicate.status_code == 409

                viewer_login = client.post(
                    "/api/v1/auth/login",
                    data={
                        "username": "viewer@example.com",
                        "password": "viewer-password",
                    },
                )
                viewer_headers = {
                    "Authorization": f"Bearer {viewer_login.json()['access_token']}"
                }
                assert (
                    client.get("/api/v1/users", headers=viewer_headers).status_code
                    == 403
                )
                assert (
                    client.get("/api/v1/users", headers=admin_headers).status_code
                    == 200
                )

                with Session(bind=connection) as inspection_session:
                    saved_users = list(
                        inspection_session.scalars(select(User).order_by(User.id))
                    )
                    assert len(saved_users) == 2
                    viewer = saved_users[1]
                    assert viewer.hashed_password != "viewer-password"
                    assert PasswordHash.recommended().verify(
                        "viewer-password",
                        viewer.hashed_password,
                    )
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
