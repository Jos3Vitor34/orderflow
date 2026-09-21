import os
from collections.abc import Generator
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.base import Base
from app.db.session import engine, get_db
from app.main import app
from app.models.customer import Customer
from app.models.order import Order
from tests.integration_helpers import create_test_user


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1",
    reason="set RUN_POSTGRES_INTEGRATION_TESTS=1 to test PostgreSQL",
)
def test_complete_customer_crud_against_postgresql() -> None:
    schema_name = f"phase7_customers_{uuid4().hex}"

    with engine.connect() as connection:
        public_tables_before = set(
            connection.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        )
        connection.execute(CreateSchema(schema_name))
        connection.commit()
        connection.exec_driver_sql(f'SET search_path TO "{schema_name}", public')
        schema_connection = connection.execution_options(
            schema_translate_map={None: schema_name}
        )
        Base.metadata.create_all(schema_connection)
        schema_connection.commit()

        def override_get_db() -> Generator[Session]:
            with Session(bind=schema_connection, expire_on_commit=False) as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        try:
            create_test_user(
                schema_connection,
                full_name="Customer Manager",
                email="manager@example.com",
            )
            with TestClient(app) as client:
                login_response = client.post(
                    "/api/v1/auth/login",
                    data={
                        "username": "manager@example.com",
                        "password": "strong-password",
                    },
                )
                assert login_response.status_code == 200
                headers = {
                    "Authorization": (f"Bearer {login_response.json()['access_token']}")
                }

                created_customers = []
                for name, email in [
                    ("Ada Lovelace", "ADA@example.com"),
                    ("Grace Hopper", "grace@example.com"),
                    ("Linus Torvalds", "linus@example.com"),
                ]:
                    response = client.post(
                        "/api/v1/customers",
                        json={"name": name, "email": email, "phone": None},
                        headers=headers,
                    )
                    assert response.status_code == 201
                    created_customers.append(response.json())

                duplicate_response = client.post(
                    "/api/v1/customers",
                    json={"name": "Duplicate", "email": "ada@EXAMPLE.com"},
                    headers=headers,
                )
                assert duplicate_response.status_code == 409

                list_response = client.get(
                    "/api/v1/customers?page=2&page_size=2",
                    headers=headers,
                )
                assert list_response.status_code == 200
                assert list_response.json()["total"] == 3
                assert list_response.json()["pages"] == 2
                assert [item["id"] for item in list_response.json()["items"]] == [
                    created_customers[2]["id"]
                ]

                ada = created_customers[0]
                patch_response = client.patch(
                    f"/api/v1/customers/{ada['id']}",
                    json={"phone": "+55 11 99999-0000"},
                    headers=headers,
                )
                assert patch_response.status_code == 200
                assert patch_response.json()["name"] == ada["name"]
                assert patch_response.json()["phone"] == "+55 11 99999-0000"

                with Session(bind=schema_connection, expire_on_commit=False) as session:
                    customer_with_order = session.get(Customer, ada["id"])
                    assert customer_with_order is not None
                    session.add(
                        Order(
                            customer=customer_with_order,
                            total_amount=Decimal("10.00"),
                        )
                    )
                    session.commit()

                conflict_response = client.delete(
                    f"/api/v1/customers/{ada['id']}",
                    headers=headers,
                )
                assert conflict_response.status_code == 409

                grace = created_customers[1]
                delete_response = client.delete(
                    f"/api/v1/customers/{grace['id']}",
                    headers=headers,
                )
                assert delete_response.status_code == 204
                assert (
                    client.get(
                        f"/api/v1/customers/{grace['id']}",
                        headers=headers,
                    ).status_code
                    == 404
                )

                with Session(bind=schema_connection) as inspection_session:
                    saved_customers = list(
                        inspection_session.scalars(
                            select(Customer).order_by(Customer.id)
                        )
                    )
                    assert [customer.email for customer in saved_customers] == [
                        "ada@example.com",
                        "linus@example.com",
                    ]
        finally:
            app.dependency_overrides.pop(get_db, None)
            schema_connection.rollback()
            Base.metadata.drop_all(schema_connection)
            schema_connection.commit()
            schema_connection.exec_driver_sql("RESET search_path")
            schema_connection.execute(
                DropSchema(schema_name, cascade=True, if_exists=True)
            )
            schema_connection.commit()

        public_tables_after = set(
            connection.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        )
        assert public_tables_after == public_tables_before

    with engine.connect() as verification_connection:
        remaining_schema_count = verification_connection.scalar(
            text("SELECT count(*) FROM pg_namespace WHERE nspname = :schema_name"),
            {"schema_name": schema_name},
        )
        assert remaining_schema_count == 0
