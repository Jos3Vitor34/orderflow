import os
from collections.abc import Generator
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.base import Base
from app.db.session import engine, get_db
from app.main import app
from app.models.order import Order, OrderStatus
from app.models.payment import Payment, PaymentStatus
from app.models.product import Product
from app.repositories.payment import (
    DuplicatePaymentReferenceError,
    PaymentRepository,
)


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1",
    reason="set RUN_POSTGRES_INTEGRATION_TESTS=1 to test PostgreSQL",
)
def test_complete_payment_flow_integrity_against_postgresql() -> None:
    schema_name = f"phase10_payments_{uuid4().hex}"

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
            with TestClient(app) as client:
                register_response = client.post(
                    "/api/v1/auth/register",
                    json={
                        "full_name": "Payment Manager",
                        "email": "payments@example.com",
                        "password": "strong-password",
                    },
                )
                assert register_response.status_code == 201
                login_response = client.post(
                    "/api/v1/auth/login",
                    data={
                        "username": "payments@example.com",
                        "password": "strong-password",
                    },
                )
                assert login_response.status_code == 200
                headers = {
                    "Authorization": f"Bearer {login_response.json()['access_token']}"
                }

                customer_response = client.post(
                    "/api/v1/customers",
                    json={"name": "Payment Customer", "email": "payer@example.com"},
                    headers=headers,
                )
                assert customer_response.status_code == 201
                customer_id = customer_response.json()["id"]

                products = []
                for sku, price, stock in [
                    ("PAYMENT-A", "19.90", 7),
                    ("PAYMENT-B", "0.01", 8),
                    ("PAYMENT-C", "199.99", 9),
                ]:
                    response = client.post(
                        "/api/v1/products",
                        json={
                            "sku": sku,
                            "name": sku,
                            "price": price,
                            "stock": stock,
                        },
                        headers=headers,
                    )
                    assert response.status_code == 201
                    products.append(response.json())

                order_response = client.post(
                    "/api/v1/orders",
                    json={
                        "customer_id": customer_id,
                        "items": [
                            {"product_id": products[0]["id"], "quantity": 2},
                            {"product_id": products[1]["id"], "quantity": 3},
                            {"product_id": products[2]["id"], "quantity": 1},
                        ],
                    },
                    headers=headers,
                )
                assert order_response.status_code == 201
                order = order_response.json()
                assert order["total_amount"] == "239.82"

                create_payload = {
                    "order_id": order["id"],
                    "provider": "manual",
                    "provider_reference": "provider-payment-001",
                }
                create_response = client.post(
                    "/api/v1/payments",
                    json=create_payload,
                    headers=headers,
                )
                assert create_response.status_code == 201
                payment = create_response.json()
                assert payment["amount"] == "239.82"
                assert payment["status"] == "pending"

                with Session(bind=schema_connection) as inspection_session:
                    persisted = inspection_session.get(Payment, payment["id"])
                    assert persisted is not None
                    assert persisted.amount == Decimal("239.82")
                    assert isinstance(persisted.amount, Decimal)
                    assert persisted.status == PaymentStatus.PENDING
                    assert list(
                        inspection_session.scalars(
                            select(Product.stock).order_by(Product.id)
                        )
                    ) == [7, 8, 9]

                idempotent_response = client.post(
                    "/api/v1/payments",
                    json=create_payload,
                    headers=headers,
                )
                assert idempotent_response.status_code == 201
                assert idempotent_response.json() == payment

                second_payment = client.post(
                    "/api/v1/payments",
                    json={"order_id": order["id"], "provider": "manual"},
                    headers=headers,
                )
                assert second_payment.status_code == 201
                assert second_payment.json()["id"] != payment["id"]

                conflict_response = client.post(
                    "/api/v1/payments",
                    json=create_payload | {"provider": "different-provider"},
                    headers=headers,
                )
                assert conflict_response.status_code == 409

                recovery_response = client.post(
                    "/api/v1/payments",
                    json={
                        "order_id": order["id"],
                        "provider": "manual",
                        "provider_reference": "provider-payment-recovery",
                    },
                    headers=headers,
                )
                assert recovery_response.status_code == 201

                with Session(bind=schema_connection) as transaction_session:
                    repository = PaymentRepository(transaction_session)
                    with pytest.raises(DuplicatePaymentReferenceError):
                        repository.create(
                            order_id=order["id"],
                            provider="manual",
                            provider_reference="provider-payment-001",
                            amount=Decimal("239.82"),
                        )
                    recovered_after_integrity_error = repository.create(
                        order_id=order["id"],
                        provider="manual",
                        provider_reference="provider-payment-after-rollback",
                        amount=Decimal("239.82"),
                    )
                    assert recovered_after_integrity_error.id is not None

                with Session(bind=schema_connection) as mutation_session:
                    persisted_order = mutation_session.get(Order, order["id"])
                    assert persisted_order is not None
                    persisted_order.total_amount = Decimal("999.99")
                    mutation_session.commit()

                snapshot_response = client.get(
                    f"/api/v1/payments/{payment['id']}", headers=headers
                )
                assert snapshot_response.status_code == 200
                assert snapshot_response.json()["amount"] == "239.82"

                approved_response = client.patch(
                    f"/api/v1/payments/{payment['id']}",
                    json={"status": "approved"},
                    headers=headers,
                )
                assert approved_response.status_code == 200
                refunded_response = client.patch(
                    f"/api/v1/payments/{payment['id']}",
                    json={"status": "refunded"},
                    headers=headers,
                )
                assert refunded_response.status_code == 200
                assert refunded_response.json()["status"] == "refunded"

                with Session(bind=schema_connection) as inspection_session:
                    persisted_order = inspection_session.get(Order, order["id"])
                    assert persisted_order is not None
                    assert persisted_order.status == OrderStatus.PENDING
                    assert list(
                        inspection_session.scalars(
                            select(Product.stock).order_by(Product.id)
                        )
                    ) == [7, 8, 9]

                cancelled_order_response = client.post(
                    "/api/v1/orders",
                    json={
                        "customer_id": customer_id,
                        "items": [{"product_id": products[1]["id"], "quantity": 1}],
                    },
                    headers=headers,
                )
                assert cancelled_order_response.status_code == 201
                cancelled_order_id = cancelled_order_response.json()["id"]
                assert (
                    client.patch(
                        f"/api/v1/orders/{cancelled_order_id}",
                        json={"status": "cancelled"},
                        headers=headers,
                    ).status_code
                    == 200
                )

                with Session(bind=schema_connection) as inspection_session:
                    payments_before_cancelled_attempt = inspection_session.scalar(
                        select(func.count()).select_from(Payment)
                    )

                cancelled_payment_response = client.post(
                    "/api/v1/payments",
                    json={
                        "order_id": cancelled_order_id,
                        "provider": "manual",
                        "provider_reference": "cancelled-order-payment",
                    },
                    headers=headers,
                )
                assert cancelled_payment_response.status_code == 409

                executed_statements: list[str] = []

                def record_statement(
                    conn: object,
                    cursor: object,
                    statement: str,
                    parameters: object,
                    context: object,
                    executemany: bool,
                ) -> None:
                    executed_statements.append(statement)

                event.listen(
                    schema_connection, "before_cursor_execute", record_statement
                )
                try:
                    list_response = client.get(
                        "/api/v1/payments?page=1&page_size=100", headers=headers
                    )
                finally:
                    event.remove(
                        schema_connection, "before_cursor_execute", record_statement
                    )

                assert list_response.status_code == 200
                assert list_response.json()["total"] == 4
                payment_selects = [
                    statement
                    for statement in executed_statements
                    if statement.lstrip().upper().startswith("SELECT")
                    and f"{schema_name}.payments" in statement
                ]
                assert len(payment_selects) == 2

                with Session(bind=schema_connection) as inspection_session:
                    assert (
                        inspection_session.scalar(
                            select(func.count()).select_from(Payment)
                        )
                        == payments_before_cancelled_attempt
                    )
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
