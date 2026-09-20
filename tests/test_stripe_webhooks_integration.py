import json
import os
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
import stripe
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api.dependencies import get_stripe_webhook_verifier
from app.db.base import Base
from app.db.session import engine, get_db
from app.integrations.stripe_webhook import StripeWebhookVerifier
from app.main import app
from app.models.order import Order, OrderStatus
from app.models.payment import Payment, PaymentStatus
from app.models.product import Product
from app.models.webhook_event import WebhookEvent
from app.repositories.webhook_event import (
    WebhookEventConstraintError,
    WebhookEventRepository,
)
from app.services.stripe_webhook import StripeWebhookService

WEBHOOK_SECRET = "whsec_phase13_postgres_test_only"


def verified_event(event_id: str, intent_id: str):
    payload = json.dumps(
        {
            "id": event_id,
            "object": "event",
            "api_version": "2025-04-30.basil",
            "created": 1_700_000_000,
            "livemode": False,
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": intent_id,
                    "object": "payment_intent",
                    "amount": 1990,
                    "currency": "brl",
                }
            },
        },
        separators=(",", ":"),
    ).encode()
    signature = stripe.WebhookSignature.generate_signature_header(
        payload.decode(),
        WEBHOOK_SECRET,
        timestamp=int(time.time()),
    )
    return StripeWebhookVerifier(SecretStr(WEBHOOK_SECRET)).verify(payload, signature)


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1",
    reason="set RUN_POSTGRES_INTEGRATION_TESTS=1 to test PostgreSQL",
)
def test_stripe_webhook_atomicity_concurrency_and_public_integrity() -> None:
    schema_name = f"phase13_stripe_webhooks_{uuid4().hex}"

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

        verifier = StripeWebhookVerifier(SecretStr(WEBHOOK_SECRET))
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_stripe_webhook_verifier] = lambda: verifier
        try:
            with TestClient(app) as client:
                register = client.post(
                    "/api/v1/auth/register",
                    json={
                        "full_name": "Stripe Webhook Tester",
                        "email": "stripe-webhooks@example.com",
                        "password": "strong-password",
                    },
                )
                assert register.status_code == 201
                login = client.post(
                    "/api/v1/auth/login",
                    data={
                        "username": "stripe-webhooks@example.com",
                        "password": "strong-password",
                    },
                )
                auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
                customer = client.post(
                    "/api/v1/customers",
                    json={"name": "Webhook Customer", "email": "phase13@example.com"},
                    headers=auth,
                ).json()
                product = client.post(
                    "/api/v1/products",
                    json={
                        "sku": "PHASE13",
                        "name": "Phase 13 Product",
                        "price": "19.90",
                        "stock": 7,
                    },
                    headers=auth,
                ).json()
                order = client.post(
                    "/api/v1/orders",
                    json={
                        "customer_id": customer["id"],
                        "items": [{"product_id": product["id"], "quantity": 1}],
                    },
                    headers=auth,
                ).json()

                def create_payment(reference: str) -> dict:
                    response = client.post(
                        "/api/v1/payments",
                        json={
                            "order_id": order["id"],
                            "provider": "stripe",
                            "provider_reference": reference,
                        },
                        headers=auth,
                    )
                    assert response.status_code == 201
                    return response.json()

                http_payment = create_payment("pi_http")
                event = verified_event("evt_http", "pi_http")
                raw_body = json.dumps(event.payload, separators=(",", ":")).encode()
                signature = stripe.WebhookSignature.generate_signature_header(
                    raw_body.decode(),
                    WEBHOOK_SECRET,
                    timestamp=int(time.time()),
                )
                response = client.post(
                    "/api/v1/webhooks/stripe",
                    content=raw_body,
                    headers={"Stripe-Signature": signature},
                )
                assert response.status_code == 200

                generic = client.post(
                    "/api/v1/webhooks/manual",
                    json={
                        "provider_event_id": "evt_generic_still_works",
                        "event_type": "manual.ping",
                        "payload": {},
                    },
                )
                assert generic.status_code == 201

                with Session(bind=schema_connection) as inspection:
                    persisted = inspection.get(Payment, http_payment["id"])
                    assert persisted is not None
                    assert persisted.status == PaymentStatus.APPROVED
                    assert persisted.amount == Decimal("19.90")
                    persisted_order = inspection.get(Order, order["id"])
                    persisted_product = inspection.get(Product, product["id"])
                    assert persisted_order is not None
                    assert persisted_order.status == OrderStatus.PENDING
                    assert persisted_product is not None
                    assert persisted_product.stock == 7

                concurrent_payment = create_payment("pi_concurrent_webhook")
                concurrent_event = verified_event(
                    "evt_concurrent_stripe", "pi_concurrent_webhook"
                )
                precheck_barrier = Barrier(2)

                class BarrierRepository(WebhookEventRepository):
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
                        mapped = worker_connection.execution_options(
                            schema_translate_map={None: schema_name}
                        )
                        with Session(bind=mapped, expire_on_commit=False) as session:
                            return (
                                StripeWebhookService(
                                    BarrierRepository(session), currency="brl"
                                )
                                .receive(concurrent_event)
                                .created
                            )

                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(
                        executor.map(lambda _: receive_concurrently(), range(2))
                    )

                assert sorted(results) == [False, True]
                with Session(bind=schema_connection) as inspection:
                    assert (
                        inspection.scalar(
                            select(func.count())
                            .select_from(WebhookEvent)
                            .where(
                                WebhookEvent.provider_event_id
                                == "evt_concurrent_stripe"
                            )
                        )
                        == 1
                    )
                    persisted = inspection.get(Payment, concurrent_payment["id"])
                    assert persisted is not None
                    assert persisted.status == PaymentStatus.APPROVED

                rollback_payment = create_payment("pi_rollback_webhook")
                rollback_event = verified_event(
                    "evt_rollback_stripe", "pi_rollback_webhook"
                )

                class FailOnceRepository(WebhookEventRepository):
                    failed = False

                    def commit_processed(
                        self,
                        webhook_event: WebhookEvent,
                        payment: Payment,
                        new_status: PaymentStatus,
                    ) -> WebhookEvent:
                        if not self.failed:
                            self.failed = True
                            payment.status = new_status
                            webhook_event.processed_at = datetime.now(UTC)
                            self._session.flush()
                            self._session.rollback()
                            raise WebhookEventConstraintError
                        return super().commit_processed(
                            webhook_event, payment, new_status
                        )

                with Session(bind=schema_connection, expire_on_commit=False) as session:
                    recovery_service = StripeWebhookService(
                        FailOnceRepository(session), currency="brl"
                    )
                    with pytest.raises(WebhookEventConstraintError):
                        recovery_service.receive(rollback_event)
                    recovered = recovery_service.receive(rollback_event)
                    assert recovered.processed is True

                with Session(bind=schema_connection) as inspection:
                    persisted = inspection.get(Payment, rollback_payment["id"])
                    assert persisted is not None
                    assert persisted.status == PaymentStatus.APPROVED
                    assert (
                        inspection.scalar(
                            select(func.count())
                            .select_from(WebhookEvent)
                            .where(
                                WebhookEvent.provider_event_id == "evt_rollback_stripe"
                            )
                        )
                        == 1
                    )
        finally:
            app.dependency_overrides.pop(get_stripe_webhook_verifier, None)
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
        assert (
            verification_connection.scalar(
                text("SELECT count(*) FROM pg_namespace WHERE nspname = :schema_name"),
                {"schema_name": schema_name},
            )
            == 0
        )
