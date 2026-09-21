import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.base import Base
from app.db.session import engine
from app.models.customer import Customer
from app.models.order import Order, OrderStatus
from app.models.payment import Payment, PaymentStatus
from app.tasks import (
    generate_order_report,
    send_order_confirmation,
    send_payment_notification,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1",
        reason="set RUN_POSTGRES_INTEGRATION_TESTS=1 to test PostgreSQL",
    ),
]


def test_tasks_query_persisted_state_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.tasks import orders, payments, reports

    schema_name = f"phase17_tasks_{uuid4().hex}"
    with engine.connect() as connection:
        connection.execute(CreateSchema(schema_name))
        connection.commit()
        mapped_connection = connection.execution_options(
            schema_translate_map={None: schema_name}
        )
        Base.metadata.create_all(mapped_connection)
        mapped_connection.commit()

        task_sessions = sessionmaker(
            bind=engine.execution_options(schema_translate_map={None: schema_name}),
            expire_on_commit=False,
        )
        monkeypatch.setattr(orders, "SessionLocal", task_sessions)
        monkeypatch.setattr(payments, "SessionLocal", task_sessions)
        monkeypatch.setattr(reports, "SessionLocal", task_sessions)

        try:
            with Session(bind=mapped_connection, expire_on_commit=False) as session:
                customer = Customer(
                    name="Task Integration Customer",
                    email=f"tasks-{uuid4().hex}@example.com",
                )
                confirmed = Order(
                    customer=customer,
                    status=OrderStatus.CONFIRMED,
                    total_amount=Decimal("20.00"),
                )
                pending = Order(
                    customer=customer,
                    status=OrderStatus.PENDING,
                    total_amount=Decimal("5.00"),
                )
                payment = Payment(
                    order=confirmed,
                    provider="integration",
                    provider_reference=f"task-{uuid4().hex}",
                    amount=Decimal("20.00"),
                    status=PaymentStatus.APPROVED,
                )
                session.add_all([confirmed, pending, payment])
                session.commit()
                confirmed_id = confirmed.id
                payment_id = payment.id

            assert send_order_confirmation.apply(args=(confirmed_id,)).result == {
                "status": "sent",
                "order_id": confirmed_id,
            }
            assert send_payment_notification.apply(args=(payment_id,)).result == {
                "status": "sent",
                "payment_id": payment_id,
            }
            report = generate_order_report.apply().result
            assert report["total_orders"] == 2
            assert report["confirmed_orders"] == 1
            assert report["pending_orders"] == 1

            with Session(bind=mapped_connection) as verification:
                assert (
                    verification.scalar(
                        select(Order.status).where(Order.id == confirmed_id)
                    )
                    == OrderStatus.CONFIRMED
                )
                assert (
                    verification.scalar(
                        select(Payment.status).where(Payment.id == payment_id)
                    )
                    == PaymentStatus.APPROVED
                )
        finally:
            Base.metadata.drop_all(mapped_connection)
            mapped_connection.commit()
            mapped_connection.exec_driver_sql("RESET search_path")
            mapped_connection.execute(
                DropSchema(schema_name, cascade=True, if_exists=True)
            )
            mapped_connection.commit()

    with engine.connect() as verification_connection:
        assert (
            verification_connection.scalar(
                text("SELECT count(*) FROM pg_namespace WHERE nspname = :schema_name"),
                {"schema_name": schema_name},
            )
            == 0
        )
