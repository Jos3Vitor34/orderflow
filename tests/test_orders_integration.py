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
from app.models.order import Order
from app.models.order_item import OrderItem
from app.models.product import Product


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1",
    reason="set RUN_POSTGRES_INTEGRATION_TESTS=1 to test PostgreSQL",
)
def test_complete_order_flow_atomicity_and_snapshot_against_postgresql() -> None:
    schema_name = f"phase9_orders_{uuid4().hex}"

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
                        "full_name": "Order Manager",
                        "email": "orders@example.com",
                        "password": "strong-password",
                    },
                )
                assert register_response.status_code == 201
                login_response = client.post(
                    "/api/v1/auth/login",
                    data={
                        "username": "orders@example.com",
                        "password": "strong-password",
                    },
                )
                assert login_response.status_code == 200
                headers = {
                    "Authorization": (f"Bearer {login_response.json()['access_token']}")
                }

                customer_response = client.post(
                    "/api/v1/customers",
                    json={"name": "Order Customer", "email": "buyer@example.com"},
                    headers=headers,
                )
                assert customer_response.status_code == 201
                customer_id = customer_response.json()["id"]

                products = []
                for sku, price, stock, is_active in [
                    ("ORDER-A", "19.90", 2, True),
                    ("ORDER-B", "0.01", 3, True),
                    ("ORDER-C", "199.99", 1, True),
                    ("ORDER-INACTIVE", "5.00", 10, False),
                ]:
                    response = client.post(
                        "/api/v1/products",
                        json={
                            "sku": sku,
                            "name": sku,
                            "price": price,
                            "stock": stock,
                            "is_active": is_active,
                        },
                        headers=headers,
                    )
                    assert response.status_code == 201
                    products.append(response.json())

                valid_payload = {
                    "customer_id": customer_id,
                    "items": [
                        {"product_id": products[0]["id"], "quantity": 2},
                        {"product_id": products[1]["id"], "quantity": 3},
                        {"product_id": products[2]["id"], "quantity": 1},
                    ],
                }
                create_response = client.post(
                    "/api/v1/orders",
                    json=valid_payload,
                    headers=headers,
                )
                assert create_response.status_code == 201
                created_order = create_response.json()
                assert created_order["status"] == "pending"
                assert created_order["total_amount"] == "239.82"
                assert [item["unit_price"] for item in created_order["items"]] == [
                    "19.90",
                    "0.01",
                    "199.99",
                ]

                with Session(bind=schema_connection) as inspection_session:
                    persisted_order = inspection_session.get(
                        Order,
                        created_order["id"],
                    )
                    assert persisted_order is not None
                    assert persisted_order.total_amount == Decimal("239.82")
                    assert (
                        inspection_session.scalar(
                            select(func.count()).select_from(OrderItem)
                        )
                        == 3
                    )
                    stock_after_order = list(
                        inspection_session.scalars(
                            select(Product.stock)
                            .where(
                                Product.id.in_(
                                    [product["id"] for product in products[:3]]
                                )
                            )
                            .order_by(Product.id)
                        )
                    )
                    assert stock_after_order == [2, 3, 1]

                snapshot_product = products[0]
                product_patch = client.patch(
                    f"/api/v1/products/{snapshot_product['id']}",
                    json={"price": "29.90"},
                    headers=headers,
                )
                assert product_patch.status_code == 200
                snapshot_response = client.get(
                    f"/api/v1/orders/{created_order['id']}",
                    headers=headers,
                )
                assert snapshot_response.status_code == 200
                assert snapshot_response.json()["items"][0]["unit_price"] == "19.90"
                assert snapshot_response.json()["total_amount"] == "239.82"

                with Session(bind=schema_connection) as inspection_session:
                    orders_before_failure = inspection_session.scalar(
                        select(func.count()).select_from(Order)
                    )
                    items_before_failure = inspection_session.scalar(
                        select(func.count()).select_from(OrderItem)
                    )

                atomic_failure = client.post(
                    "/api/v1/orders",
                    json={
                        "customer_id": customer_id,
                        "items": [
                            {"product_id": products[0]["id"], "quantity": 1},
                            {"product_id": 999999, "quantity": 1},
                        ],
                    },
                    headers=headers,
                )
                assert atomic_failure.status_code == 404

                inactive_failure = client.post(
                    "/api/v1/orders",
                    json={
                        "customer_id": customer_id,
                        "items": [{"product_id": products[3]["id"], "quantity": 1}],
                    },
                    headers=headers,
                )
                assert inactive_failure.status_code == 409

                with Session(bind=schema_connection) as inspection_session:
                    assert (
                        inspection_session.scalar(
                            select(func.count()).select_from(Order)
                        )
                        == orders_before_failure
                    )
                    assert (
                        inspection_session.scalar(
                            select(func.count()).select_from(OrderItem)
                        )
                        == items_before_failure
                    )

                recovery_response = client.post(
                    "/api/v1/orders",
                    json={
                        "customer_id": customer_id,
                        "items": [{"product_id": products[1]["id"], "quantity": 1}],
                    },
                    headers=headers,
                )
                assert recovery_response.status_code == 201

                for target_status in ("processing", "confirmed", "shipped"):
                    patch_response = client.patch(
                        f"/api/v1/orders/{created_order['id']}",
                        json={"status": target_status},
                        headers=headers,
                    )
                    assert patch_response.status_code == 200
                assert patch_response.json()["status"] == "shipped"
                assert patch_response.json()["total_amount"] == "239.82"

                with Session(bind=schema_connection) as inspection_session:
                    assert list(
                        inspection_session.scalars(
                            select(Product.stock)
                            .where(
                                Product.id.in_(
                                    [product["id"] for product in products[:3]]
                                )
                            )
                            .order_by(Product.id)
                        )
                    ) == [0, 0, 0]

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
                    schema_connection,
                    "before_cursor_execute",
                    record_statement,
                )
                try:
                    list_response = client.get(
                        "/api/v1/orders?page=1&page_size=20",
                        headers=headers,
                    )
                finally:
                    event.remove(
                        schema_connection,
                        "before_cursor_execute",
                        record_statement,
                    )

                assert list_response.status_code == 200
                assert list_response.json()["total"] == 2
                order_item_selects = [
                    statement
                    for statement in executed_statements
                    if statement.lstrip().upper().startswith("SELECT")
                    and "order_items" in statement
                ]
                assert len(order_item_selects) == 1
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
