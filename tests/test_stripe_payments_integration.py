import os
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Lock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api.dependencies import get_stripe_gateway
from app.db.base import Base
from app.db.session import engine, get_db
from app.integrations.stripe import StripePaymentIntent
from app.main import app
from app.models.order import Order, OrderStatus
from app.models.payment import Payment, PaymentStatus
from app.models.product import Product
from app.repositories.payment import (
    DuplicatePaymentReferenceError,
    PaymentConstraintError,
)
from app.repositories.stripe_payment import StripePaymentRepository
from app.schemas.stripe_payment import StripePaymentCreate
from app.services.stripe_payment import (
    StripePaymentService,
    build_stripe_idempotency_key,
)
from tests.integration_helpers import create_test_user


class ConcurrentFakeStripeGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.intents: dict[str, StripePaymentIntent] = {}
        self._lock = Lock()
        self._barrier: Barrier | None = None

    def synchronize_next_pair(self) -> None:
        self._barrier = Barrier(2)

    def create_payment_intent(
        self,
        *,
        amount: int,
        currency: str,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> StripePaymentIntent:
        call = {
            "amount": amount,
            "currency": currency,
            "metadata": metadata,
            "idempotency_key": idempotency_key,
        }
        with self._lock:
            self.calls.append(call)
        barrier = self._barrier
        if barrier is not None:
            barrier.wait(timeout=10)
            self._barrier = None
        with self._lock:
            intent = self.intents.get(idempotency_key)
            if intent is None:
                intent = StripePaymentIntent(
                    id=f"pi_test_integration_{len(self.intents) + 1}",
                    status="requires_payment_method",
                    amount=amount,
                    currency=currency,
                )
                self.intents[idempotency_key] = intent
            return intent


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1",
    reason="set RUN_POSTGRES_INTEGRATION_TESTS=1 to test PostgreSQL",
)
def test_stripe_payment_flow_and_concurrency_against_postgresql() -> None:
    schema_name = f"phase12_stripe_{uuid4().hex}"
    gateway = ConcurrentFakeStripeGateway()

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
        app.dependency_overrides[get_stripe_gateway] = lambda: gateway
        try:
            create_test_user(
                schema_connection,
                full_name="Stripe Tester",
                email="stripe-integration@example.com",
            )
            with TestClient(app) as client:
                login_response = client.post(
                    "/api/v1/auth/login",
                    data={
                        "username": "stripe-integration@example.com",
                        "password": "strong-password",
                    },
                )
                assert login_response.status_code == 200
                headers = {
                    "Authorization": f"Bearer {login_response.json()['access_token']}"
                }

                customer_response = client.post(
                    "/api/v1/customers",
                    json={"name": "Stripe Customer", "email": "stripe@example.com"},
                    headers=headers,
                )
                assert customer_response.status_code == 201
                product_response = client.post(
                    "/api/v1/products",
                    json={
                        "sku": "STRIPE-PRODUCT",
                        "name": "Stripe Product",
                        "price": "239.82",
                        "stock": 7,
                    },
                    headers=headers,
                )
                assert product_response.status_code == 201
                product = product_response.json()

                def create_order() -> dict:
                    response = client.post(
                        "/api/v1/orders",
                        json={
                            "customer_id": customer_response.json()["id"],
                            "items": [{"product_id": product["id"], "quantity": 1}],
                        },
                        headers=headers,
                    )
                    assert response.status_code == 201
                    return response.json()

                order = create_order()
                stripe_headers = headers | {"Idempotency-Key": "api-attempt-1"}
                create_response = client.post(
                    "/api/v1/payments/stripe",
                    json={"order_id": order["id"]},
                    headers=stripe_headers,
                )
                assert create_response.status_code == 201
                body = create_response.json()
                assert body["payment"]["provider"] == "stripe"
                assert body["payment"]["amount"] == "239.82"
                assert body["payment"]["status"] == "pending"
                assert body["stripe"] == {
                    "payment_intent_id": "pi_test_integration_1",
                    "status": "requires_payment_method",
                    "amount": 23982,
                    "currency": "brl",
                }

                retry_response = client.post(
                    "/api/v1/payments/stripe",
                    json={"order_id": order["id"]},
                    headers=stripe_headers,
                )
                assert retry_response.status_code == 200
                assert retry_response.json() == body

                with Session(bind=schema_connection) as inspection_session:
                    persisted = inspection_session.get(Payment, body["payment"]["id"])
                    assert persisted is not None
                    assert persisted.provider == "stripe"
                    assert persisted.provider_reference == "pi_test_integration_1"
                    assert persisted.amount == Decimal("239.82")
                    assert persisted.status == PaymentStatus.PENDING
                    persisted_order = inspection_session.get(Order, order["id"])
                    assert persisted_order is not None
                    assert persisted_order.status == OrderStatus.PENDING
                    persisted_product = inspection_session.get(Product, product["id"])
                    assert persisted_product is not None
                    assert persisted_product.stock == 7

                generic_payment = client.post(
                    "/api/v1/payments",
                    json={
                        "order_id": order["id"],
                        "provider": "manual",
                        "provider_reference": "manual-after-stripe",
                    },
                    headers=headers,
                )
                assert generic_payment.status_code == 201
                generic_webhook = client.post(
                    "/api/v1/webhooks/provider-a",
                    json={
                        "provider_event_id": "evt-after-stripe",
                        "event_type": "provider.ping",
                        "payload": {"source": "phase12"},
                    },
                )
                assert generic_webhook.status_code == 201

                concurrent_order = create_order()
                concurrent_operation_key = "concurrent-attempt"
                concurrent_stripe_key = build_stripe_idempotency_key(
                    concurrent_order["id"], concurrent_operation_key
                )
                gateway.synchronize_next_pair()

                def create_concurrently() -> bool:
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
                            service = StripePaymentService(
                                StripePaymentRepository(session),
                                gateway,
                                currency="brl",
                            )
                            return service.create(
                                StripePaymentCreate(order_id=concurrent_order["id"]),
                                operation_key=concurrent_operation_key,
                            ).created

                with ThreadPoolExecutor(max_workers=2) as executor:
                    concurrency_results = list(
                        executor.map(lambda _: create_concurrently(), range(2))
                    )

                assert sorted(concurrency_results) == [False, True]
                assert (
                    sum(
                        call["idempotency_key"] == concurrent_stripe_key
                        for call in gateway.calls
                    )
                    == 2
                )
                assert concurrent_stripe_key in gateway.intents
                concurrent_intent = gateway.intents[concurrent_stripe_key]

                with Session(bind=schema_connection) as inspection_session:
                    assert (
                        inspection_session.scalar(
                            select(func.count())
                            .select_from(Payment)
                            .where(Payment.provider_reference == concurrent_intent.id)
                        )
                        == 1
                    )

                recovery_order = create_order()

                class FailOnceRepository(StripePaymentRepository):
                    def __init__(self, session: Session) -> None:
                        super().__init__(session)
                        self.failed = False

                    def create(
                        self,
                        *,
                        order_id: int,
                        provider_reference: str,
                        amount: Decimal,
                    ) -> Payment:
                        if not self.failed:
                            self.failed = True
                            self._session.rollback()
                            raise PaymentConstraintError
                        return super().create(
                            order_id=order_id,
                            provider_reference=provider_reference,
                            amount=amount,
                        )

                with Session(
                    bind=schema_connection,
                    expire_on_commit=False,
                ) as recovery_session:
                    repository = FailOnceRepository(recovery_session)
                    service = StripePaymentService(
                        repository,
                        gateway,
                        currency="brl",
                    )
                    recovery_data = StripePaymentCreate(order_id=recovery_order["id"])
                    with pytest.raises(PaymentConstraintError):
                        service.create(
                            recovery_data,
                            operation_key="failure-after-stripe",
                        )
                    recovered = service.create(
                        recovery_data,
                        operation_key="failure-after-stripe",
                    )
                    assert recovered.created is True
                    assert recovered.payment.provider_reference == (
                        recovered.payment_intent.id
                    )

                    with pytest.raises(DuplicatePaymentReferenceError):
                        repository.create(
                            order_id=recovery_order["id"],
                            provider_reference=recovered.payment_intent.id,
                            amount=Decimal("239.82"),
                        )
                    after_integrity_rollback = repository.create(
                        order_id=recovery_order["id"],
                        provider_reference="pi_test_after-integrity-rollback",
                        amount=Decimal("239.82"),
                    )
                    assert after_integrity_rollback.id is not None
        finally:
            app.dependency_overrides.pop(get_stripe_gateway, None)
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
