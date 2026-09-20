import os
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.base import Base
from app.db.session import engine, get_db
from app.main import app
from app.models.order import Order, OrderStatus
from app.models.payment import Payment, PaymentStatus
from app.models.product import Product
from app.models.webhook_event import WebhookEvent
from app.repositories.webhook_event import (
    DuplicateWebhookEventIdError,
    WebhookEventRepository,
)
from app.schemas.webhook_event import WebhookEventCreate
from app.services.webhook_event import WebhookEventService


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1",
    reason="set RUN_POSTGRES_INTEGRATION_TESTS=1 to test PostgreSQL",
)
def test_complete_webhook_flow_and_concurrency_against_postgresql() -> None:
    schema_name = f"phase11_webhooks_{uuid4().hex}"

    with engine.connect() as connection:
        public_tables_before = set(
            connection.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        )
        public_enums_before = set(
            connection.scalars(
                text(
                    "SELECT t.typname FROM pg_type t "
                    "JOIN pg_namespace n ON n.oid = t.typnamespace "
                    "WHERE n.nspname = 'public' AND t.typtype = 'e'"
                )
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
                        "full_name": "Webhook Auditor",
                        "email": "webhooks@example.com",
                        "password": "strong-password",
                    },
                )
                assert register_response.status_code == 201
                login_response = client.post(
                    "/api/v1/auth/login",
                    data={
                        "username": "webhooks@example.com",
                        "password": "strong-password",
                    },
                )
                assert login_response.status_code == 200
                headers = {
                    "Authorization": f"Bearer {login_response.json()['access_token']}"
                }

                customer_response = client.post(
                    "/api/v1/customers",
                    json={"name": "Webhook Customer", "email": "hook@example.com"},
                    headers=headers,
                )
                assert customer_response.status_code == 201
                product_response = client.post(
                    "/api/v1/products",
                    json={
                        "sku": "WEBHOOK-PRODUCT",
                        "name": "Webhook Product",
                        "price": "19.90",
                        "stock": 7,
                    },
                    headers=headers,
                )
                assert product_response.status_code == 201
                product = product_response.json()
                order_response = client.post(
                    "/api/v1/orders",
                    json={
                        "customer_id": customer_response.json()["id"],
                        "items": [{"product_id": product["id"], "quantity": 1}],
                    },
                    headers=headers,
                )
                assert order_response.status_code == 201
                order = order_response.json()

                payment_response = client.post(
                    "/api/v1/payments",
                    json={
                        "order_id": order["id"],
                        "provider": "provider-a",
                        "provider_reference": "pay-concurrent",
                    },
                    headers=headers,
                )
                assert payment_response.status_code == 201
                payment = payment_response.json()

                concurrent_payload = WebhookEventCreate(
                    provider_event_id="evt-concurrent",
                    event_type="payment.approved",
                    payload={
                        "provider_reference": "pay-concurrent",
                        "status": "approved",
                        "metadata": {"source": "concurrency", "attempt": 1},
                    },
                )
                precheck_barrier = Barrier(2)

                class BarrierWebhookEventRepository(WebhookEventRepository):
                    def get_by_provider_event_id(
                        self, provider_event_id: str
                    ) -> WebhookEvent | None:
                        existing = super().get_by_provider_event_id(provider_event_id)
                        if existing is None:
                            precheck_barrier.wait(timeout=10)
                        return existing

                def receive_concurrently() -> bool:
                    with engine.connect() as worker_connection:
                        worker_connection.exec_driver_sql(
                            f'SET search_path TO "{schema_name}", public'
                        )
                        worker_connection.commit()
                        mapped_connection = worker_connection.execution_options(
                            schema_translate_map={None: schema_name}
                        )
                        with Session(
                            bind=mapped_connection,
                            expire_on_commit=False,
                        ) as session:
                            service = WebhookEventService(
                                BarrierWebhookEventRepository(session)
                            )
                            return service.receive(
                                provider="provider-a",
                                data=concurrent_payload,
                            ).created

                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(
                        executor.map(lambda _: receive_concurrently(), range(2))
                    )

                assert sorted(results) == [False, True]
                with Session(bind=schema_connection) as inspection_session:
                    assert (
                        inspection_session.scalar(
                            select(func.count())
                            .select_from(WebhookEvent)
                            .where(WebhookEvent.provider_event_id == "evt-concurrent")
                        )
                        == 1
                    )
                    persisted_payment = inspection_session.get(Payment, payment["id"])
                    assert persisted_payment is not None
                    assert persisted_payment.status == PaymentStatus.APPROVED
                    assert persisted_payment.amount == Decimal("19.90")

                redelivery_response = client.post(
                    "/api/v1/webhooks/provider-a",
                    json=concurrent_payload.model_dump(mode="json"),
                )
                assert redelivery_response.status_code == 200
                assert redelivery_response.json()["processed_at"] is not None

                collision_response = client.post(
                    "/api/v1/webhooks/provider-a",
                    json=concurrent_payload.model_dump(mode="json")
                    | {"event_type": "payment.failed"},
                )
                assert collision_response.status_code == 409

                unknown_payload = {
                    "provider_event_id": "evt-unknown",
                    "event_type": "provider.unhandled",
                    "payload": {
                        "provider_reference": "pay-concurrent",
                        "metadata": {
                            "source": "test",
                            "attempt": 1,
                            "values": [True, None, "preserved"],
                        },
                    },
                }
                unknown_response = client.post(
                    "/api/v1/webhooks/provider-a",
                    json=unknown_payload,
                )
                assert unknown_response.status_code == 201
                assert unknown_response.json()["payload"] == unknown_payload["payload"]
                assert unknown_response.json()["processed_at"] is None

                missing_response = client.post(
                    "/api/v1/webhooks/provider-a",
                    json={
                        "provider_event_id": "evt-missing-payment",
                        "event_type": "payment.approved",
                        "payload": {"provider_reference": "pay-missing"},
                    },
                )
                assert missing_response.status_code == 404

                mismatch_response = client.post(
                    "/api/v1/webhooks/provider-b",
                    json={
                        "provider_event_id": "evt-provider-mismatch",
                        "event_type": "payment.approved",
                        "payload": {"provider_reference": "pay-concurrent"},
                    },
                )
                assert mismatch_response.status_code == 409

                invalid_transition_response = client.post(
                    "/api/v1/webhooks/provider-a",
                    json={
                        "provider_event_id": "evt-invalid-transition",
                        "event_type": "payment.failed",
                        "payload": {"provider_reference": "pay-concurrent"},
                    },
                )
                assert invalid_transition_response.status_code == 409

                refund_response = client.post(
                    "/api/v1/webhooks/provider-a",
                    json={
                        "provider_event_id": "evt-refund",
                        "event_type": "payment.refunded",
                        "payload": {
                            "provider_reference": "pay-concurrent",
                            "amount": "0.01",
                        },
                    },
                )
                assert refund_response.status_code == 201

                failed_payment_response = client.post(
                    "/api/v1/payments",
                    json={
                        "order_id": order["id"],
                        "provider": "provider-a",
                        "provider_reference": "pay-failed",
                    },
                    headers=headers,
                )
                assert failed_payment_response.status_code == 201
                failed_event_response = client.post(
                    "/api/v1/webhooks/provider-a",
                    json={
                        "provider_event_id": "evt-failed",
                        "event_type": "payment.failed",
                        "payload": {"provider_reference": "pay-failed"},
                    },
                )
                assert failed_event_response.status_code == 201

                with Session(bind=schema_connection) as inspection_session:
                    persisted_payment = inspection_session.get(Payment, payment["id"])
                    assert persisted_payment is not None
                    assert persisted_payment.status == PaymentStatus.REFUNDED
                    assert persisted_payment.amount == Decimal("19.90")
                    failed_payment = inspection_session.get(
                        Payment, failed_payment_response.json()["id"]
                    )
                    assert failed_payment is not None
                    assert failed_payment.status == PaymentStatus.FAILED
                    persisted_order = inspection_session.get(Order, order["id"])
                    assert persisted_order is not None
                    assert persisted_order.status == OrderStatus.PENDING
                    persisted_product = inspection_session.get(Product, product["id"])
                    assert persisted_product is not None
                    assert persisted_product.stock == 7
                    rejected_events = list(
                        inspection_session.scalars(
                            select(WebhookEvent)
                            .where(
                                WebhookEvent.provider_event_id.in_(
                                    [
                                        "evt-missing-payment",
                                        "evt-provider-mismatch",
                                        "evt-invalid-transition",
                                    ]
                                )
                            )
                            .order_by(WebhookEvent.id)
                        )
                    )
                    assert len(rejected_events) == 3
                    assert all(event.processed_at is None for event in rejected_events)

                atomic_payment_response = client.post(
                    "/api/v1/payments",
                    json={
                        "order_id": order["id"],
                        "provider": "provider-a",
                        "provider_reference": "pay-atomic",
                    },
                    headers=headers,
                )
                assert atomic_payment_response.status_code == 201
                anchor_response = client.post(
                    "/api/v1/webhooks/provider-a",
                    json={
                        "provider_event_id": "evt-anchor",
                        "event_type": "provider.ping",
                        "payload": {},
                    },
                )
                assert anchor_response.status_code == 201

                with Session(
                    bind=schema_connection,
                    expire_on_commit=False,
                ) as transaction_session:
                    repository = WebhookEventRepository(transaction_session)
                    atomic_event = repository.create_received(
                        provider="provider-a",
                        provider_event_id="evt-atomic-temporary",
                        event_type="payment.approved",
                        payload={"provider_reference": "pay-atomic"},
                    )
                    atomic_payment = repository.get_payment_for_update("pay-atomic")
                    assert atomic_payment is not None
                    atomic_event.provider_event_id = "evt-anchor"
                    with pytest.raises(DuplicateWebhookEventIdError):
                        repository.commit_processed(
                            atomic_event,
                            atomic_payment,
                            PaymentStatus.APPROVED,
                        )

                    assert (
                        repository.get_by_provider_event_id("evt-atomic-temporary")
                        is None
                    )
                    recovered_event = repository.create_received(
                        provider="provider-a",
                        provider_event_id="evt-after-rollback",
                        event_type="provider.ping",
                        payload={"recovered": True},
                    )
                    repository.commit_received(recovered_event)

                with Session(bind=schema_connection) as inspection_session:
                    atomic_payment = inspection_session.get(
                        Payment, atomic_payment_response.json()["id"]
                    )
                    assert atomic_payment is not None
                    assert atomic_payment.status == PaymentStatus.PENDING
                    assert (
                        inspection_session.scalar(
                            select(func.count())
                            .select_from(WebhookEvent)
                            .where(
                                WebhookEvent.provider_event_id == "evt-atomic-temporary"
                            )
                        )
                        == 0
                    )
                    assert (
                        inspection_session.scalar(
                            select(func.count())
                            .select_from(WebhookEvent)
                            .where(
                                WebhookEvent.provider_event_id == "evt-after-rollback"
                            )
                        )
                        == 1
                    )

                unauthenticated_audit = client.get("/api/v1/webhook-events")
                assert unauthenticated_audit.status_code == 401
                audit_response = client.get(
                    "/api/v1/webhook-events?page=1&page_size=100",
                    headers=headers,
                )
                assert audit_response.status_code == 200
                assert audit_response.json()["total"] == 9
                assert [
                    event["id"] for event in audit_response.json()["items"]
                ] == sorted(event["id"] for event in audit_response.json()["items"])
                get_response = client.get(
                    f"/api/v1/webhook-events/{redelivery_response.json()['id']}",
                    headers=headers,
                )
                assert get_response.status_code == 200
                assert get_response.json()["provider_event_id"] == "evt-concurrent"
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
        public_enums_after = set(
            connection.scalars(
                text(
                    "SELECT t.typname FROM pg_type t "
                    "JOIN pg_namespace n ON n.oid = t.typnamespace "
                    "WHERE n.nspname = 'public' AND t.typtype = 'e'"
                )
            )
        )
        assert public_tables_after == public_tables_before
        assert public_enums_after == public_enums_before

    with engine.connect() as verification_connection:
        remaining_schema_count = verification_connection.scalar(
            text("SELECT count(*) FROM pg_namespace WHERE nspname = :schema_name"),
            {"schema_name": schema_name},
        )
        assert remaining_schema_count == 0
