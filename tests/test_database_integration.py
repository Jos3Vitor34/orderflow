import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models import (
    Customer,
    Order,
    OrderItem,
    Payment,
    Product,
    User,
    WebhookEvent,
)


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1",
    reason="set RUN_POSTGRES_INTEGRATION_TESTS=1 to test PostgreSQL",
)
def test_postgresql_accepts_real_queries() -> None:
    with SessionLocal() as session:
        assert session.scalar(text("SELECT 1")) == 1


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1",
    reason="set RUN_POSTGRES_INTEGRATION_TESTS=1 to test PostgreSQL",
)
def test_models_and_constraints_on_temporary_postgresql_schema() -> None:
    schema_name = f"phase4_validation_{uuid4().hex}"

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

        try:
            Base.metadata.create_all(schema_connection)
            schema_connection.commit()

            with Session(bind=schema_connection, expire_on_commit=False) as session:
                user = User(
                    full_name="OrderFlow Admin",
                    email="admin@example.com",
                    hashed_password="already-hashed",
                )
                customer = Customer(
                    name="Ada Lovelace",
                    email="ada@example.com",
                    phone=None,
                )
                product = Product(
                    sku="SKU-001",
                    name="Test product",
                    description=None,
                    price=Decimal("19.90"),
                    stock=5,
                )
                order = Order(customer=customer, total_amount=Decimal("39.80"))
                item = OrderItem(
                    product=product,
                    quantity=2,
                    unit_price=Decimal("19.90"),
                )
                order.items.append(item)
                payment = Payment(
                    order=order,
                    provider="test-provider",
                    provider_reference="payment-001",
                    amount=Decimal("39.80"),
                )
                webhook = WebhookEvent(
                    provider="test-provider",
                    provider_event_id="event-001",
                    event_type="payment.approved",
                    payload={"payment_id": "payment-001"},
                    processed_at=None,
                )
                session.add_all([user, order, payment, webhook])
                session.commit()

                product.price = Decimal("29.90")
                session.commit()

                saved_item = session.scalar(select(OrderItem))
                saved_webhook = session.scalar(select(WebhookEvent))
                assert saved_item is not None
                assert saved_item.unit_price == Decimal("19.90")
                assert saved_webhook is not None
                assert saved_webhook.payload == {"payment_id": "payment-001"}

                session.add(
                    Product(
                        sku="SKU-NEGATIVE",
                        name="Invalid stock",
                        description=None,
                        price=Decimal("1.00"),
                        stock=-1,
                    )
                )
                with pytest.raises(IntegrityError):
                    session.commit()
                session.rollback()
        finally:
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
