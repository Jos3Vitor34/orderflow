import os
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.base import Base
from app.db.session import engine, get_db
from app.main import app
from app.models.customer import Customer
from app.models.order import Order, OrderStatus
from app.models.payment import Payment, PaymentStatus
from tests.integration_helpers import create_test_user


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1",
    reason="set RUN_POSTGRES_INTEGRATION_TESTS=1 to test PostgreSQL",
)
def test_statistics_are_aggregated_in_postgresql_without_mutation() -> None:
    schema_name = f"phase18_stats_{uuid4().hex}"

    with engine.connect() as connection:
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

        create_test_user(
            schema_connection,
            full_name="Statistics Viewer",
            email="statistics@example.com",
        )
        january = datetime(2026, 1, 15, tzinfo=UTC)
        february = datetime(2026, 2, 15, tzinfo=UTC)
        with Session(bind=schema_connection, expire_on_commit=False) as session:
            customer = Customer(
                name="Aggregate Customer",
                email="aggregate@example.com",
            )
            session.add(customer)
            session.flush()
            orders = [
                Order(
                    customer_id=customer.id,
                    status=OrderStatus.PENDING,
                    total_amount=Decimal("10.01"),
                    created_at=january,
                ),
                Order(
                    customer_id=customer.id,
                    status=OrderStatus.PROCESSING,
                    total_amount=Decimal("20.00"),
                    created_at=january,
                ),
                Order(
                    customer_id=customer.id,
                    status=OrderStatus.CONFIRMED,
                    total_amount=Decimal("30.00"),
                    created_at=february,
                ),
                Order(
                    customer_id=customer.id,
                    status=OrderStatus.CANCELLED,
                    total_amount=Decimal("40.00"),
                    created_at=january,
                ),
            ]
            session.add_all(orders)
            session.flush()
            session.add_all(
                [
                    Payment(
                        order_id=orders[0].id,
                        provider="manual",
                        amount=Decimal("10.01"),
                        status=PaymentStatus.PENDING,
                        created_at=january,
                    ),
                    Payment(
                        order_id=orders[1].id,
                        provider="manual",
                        amount=Decimal("20.00"),
                        status=PaymentStatus.APPROVED,
                        created_at=january,
                    ),
                    Payment(
                        order_id=orders[2].id,
                        provider="manual",
                        amount=Decimal("30.00"),
                        status=PaymentStatus.FAILED,
                        created_at=february,
                    ),
                    Payment(
                        order_id=orders[3].id,
                        provider="manual",
                        amount=Decimal("40.00"),
                        status=PaymentStatus.REFUNDED,
                        created_at=january,
                    ),
                ]
            )
            session.commit()

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                login = client.post(
                    "/api/v1/auth/login",
                    data={
                        "username": "statistics@example.com",
                        "password": "strong-password",
                    },
                )
                headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
                overview = client.get(
                    "/api/v1/statistics/overview",
                    headers=headers,
                )
                january_overview = client.get(
                    "/api/v1/statistics/overview",
                    headers=headers,
                    params={
                        "start": "2026-01-01T00:00:00Z",
                        "end": "2026-02-01T00:00:00Z",
                    },
                )

                assert overview.status_code == 200
                body = overview.json()
                assert body["orders"]["total"] == 4
                assert body["orders"]["total_amount"] == "100.01"
                assert body["orders"]["average_ticket"] == "25.00"
                assert body["orders"]["by_status"]["shipped"] == 0
                assert body["payments"]["total_processed_amount"] == "100.01"
                assert body["payments"]["successful_amount"] == "60.00"
                assert body["payments"]["successful_count"] == 2
                assert body["payments"]["success_rate"] == "66.67"
                assert january_overview.status_code == 200
                assert january_overview.json()["orders"]["total"] == 3
                assert january_overview.json()["payments"]["total"] == 3

                with Session(bind=schema_connection) as inspection:
                    assert (
                        inspection.scalar(select(func.count()).select_from(Order)) == 4
                    )
                    assert (
                        inspection.scalar(select(func.count()).select_from(Payment))
                        == 4
                    )
                    assert inspection.scalar(
                        select(func.sum(Order.total_amount))
                    ) == Decimal("100.01")
        finally:
            app.dependency_overrides.clear()
            schema_connection.rollback()
            Base.metadata.drop_all(schema_connection)
            schema_connection.commit()
            schema_connection.exec_driver_sql("RESET search_path")
            schema_connection.execute(
                DropSchema(schema_name, cascade=True, if_exists=True)
            )
            schema_connection.commit()

    with engine.connect() as verification_connection:
        assert (
            verification_connection.scalar(
                text("SELECT count(*) FROM pg_namespace WHERE nspname = :schema_name"),
                {"schema_name": schema_name},
            )
            == 0
        )
