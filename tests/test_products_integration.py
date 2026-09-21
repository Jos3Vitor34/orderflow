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
from app.models.order_item import OrderItem
from app.models.product import Product
from tests.integration_helpers import create_test_user


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1",
    reason="set RUN_POSTGRES_INTEGRATION_TESTS=1 to test PostgreSQL",
)
def test_complete_product_crud_and_decimal_precision_against_postgresql() -> None:
    schema_name = f"phase8_products_{uuid4().hex}"

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
                full_name="Product Manager",
                email="products@example.com",
            )
            with TestClient(app) as client:
                login_response = client.post(
                    "/api/v1/auth/login",
                    data={
                        "username": "products@example.com",
                        "password": "strong-password",
                    },
                )
                assert login_response.status_code == 200
                headers = {
                    "Authorization": (f"Bearer {login_response.json()['access_token']}")
                }

                created_products = []
                for sku, price in [
                    ("PENNY", "0.01"),
                    ("REGULAR", "19.90"),
                    ("PREMIUM", "199.99"),
                ]:
                    response = client.post(
                        "/api/v1/products",
                        json={
                            "sku": sku,
                            "name": f"Product {sku}",
                            "description": None,
                            "price": price,
                            "stock": 10,
                        },
                        headers=headers,
                    )
                    assert response.status_code == 201
                    assert response.json()["price"] == price
                    created_products.append(response.json())

                duplicate_response = client.post(
                    "/api/v1/products",
                    json={"sku": "PENNY", "name": "Duplicate", "price": "1.00"},
                    headers=headers,
                )
                assert duplicate_response.status_code == 409

                list_response = client.get(
                    "/api/v1/products?page=2&page_size=2",
                    headers=headers,
                )
                assert list_response.status_code == 200
                assert list_response.json()["total"] == 3
                assert list_response.json()["pages"] == 2
                assert [item["id"] for item in list_response.json()["items"]] == [
                    created_products[2]["id"]
                ]

                premium = created_products[2]
                patch_response = client.patch(
                    f"/api/v1/products/{premium['id']}",
                    json={"description": "Updated", "price": "299.95"},
                    headers=headers,
                )
                assert patch_response.status_code == 200
                assert patch_response.json()["price"] == "299.95"
                assert patch_response.json()["stock"] == premium["stock"]

                with Session(bind=schema_connection) as inspection_session:
                    saved_prices = list(
                        inspection_session.scalars(
                            select(Product.price).order_by(Product.id)
                        )
                    )
                    assert saved_prices == [
                        Decimal("0.01"),
                        Decimal("19.90"),
                        Decimal("299.95"),
                    ]

                    penny = inspection_session.get(
                        Product,
                        created_products[0]["id"],
                    )
                    assert penny is not None
                    customer = Customer(
                        name="Order Customer",
                        email="order-customer@example.com",
                        phone=None,
                    )
                    order = Order(customer=customer, total_amount=Decimal("0.01"))
                    order.items.append(
                        OrderItem(
                            product=penny,
                            quantity=1,
                            unit_price=Decimal("0.01"),
                        )
                    )
                    inspection_session.add(order)
                    inspection_session.commit()

                conflict_response = client.delete(
                    f"/api/v1/products/{created_products[0]['id']}",
                    headers=headers,
                )
                assert conflict_response.status_code == 409

                with Session(bind=schema_connection) as inspection_session:
                    assert (
                        inspection_session.get(Product, created_products[0]["id"])
                        is not None
                    )
                    order_item = inspection_session.scalar(
                        select(OrderItem).where(
                            OrderItem.product_id == created_products[0]["id"]
                        )
                    )
                    assert order_item is not None

                regular = created_products[1]
                delete_response = client.delete(
                    f"/api/v1/products/{regular['id']}",
                    headers=headers,
                )
                assert delete_response.status_code == 204
                assert (
                    client.get(
                        f"/api/v1/products/{regular['id']}",
                        headers=headers,
                    ).status_code
                    == 404
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
