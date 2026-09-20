import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event, Lock
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.base import Base
from app.db.session import engine
from app.integrations.stripe_refund import StripeRefund
from app.integrations.stripe_webhook import VerifiedStripeEvent
from app.models.customer import Customer
from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.models.payment import Payment, PaymentStatus
from app.models.product import Product
from app.models.refund import Refund, RefundStatus
from app.models.webhook_event import WebhookEvent
from app.repositories.refund import RefundRepository
from app.repositories.stripe_webhook import StripeWebhookRepository
from app.repositories.webhook_event import (
    DuplicateWebhookEventIdError,
    WebhookEventConstraintError,
)
from app.schemas.refund import RefundCreate
from app.services.refund import RefundAmountError, RefundService
from app.services.stripe_webhook import StripeWebhookService


class ConcurrentRefundGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self._results: dict[str, StripeRefund] = {}
        self._lock = Lock()
        self.entered = Event()
        self.release = Event()
        self.block = False

    def create_refund(
        self,
        *,
        payment_intent: str,
        amount: int,
        reason: str | None,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> StripeRefund:
        with self._lock:
            self.calls.append(
                {
                    "payment_intent": payment_intent,
                    "amount": amount,
                    "reason": reason,
                    "metadata": metadata,
                    "idempotency_key": idempotency_key,
                }
            )
            result = self._results.get(idempotency_key)
            if result is None:
                result = StripeRefund(
                    id=f"re_integration_{len(self._results) + 1}",
                    payment_intent=payment_intent,
                    amount=amount,
                    currency="brl",
                    status="succeeded",
                    reason=reason,
                    failure_reason=None,
                    created=1_700_000_000 + len(self._results),
                )
                self._results[idempotency_key] = result
        if self.block:
            self.entered.set()
            assert self.release.wait(timeout=10)
        return result


def stripe_refund_event(
    event_id: str,
    *,
    refund_id: str,
    payment_intent: str,
    amount: int,
    metadata: dict[str, str] | None = None,
    created: int = 1_700_000_100,
) -> VerifiedStripeEvent:
    payload = {
        "id": event_id,
        "object": "event",
        "created": created,
        "livemode": False,
        "type": "refund.updated",
        "data": {
            "object": {
                "id": refund_id,
                "object": "refund",
                "payment_intent": payment_intent,
                "amount": amount,
                "currency": "brl",
                "status": "succeeded",
                "reason": "requested_by_customer",
                "failure_reason": None,
                "created": 1_700_000_000,
                "metadata": metadata or {},
            }
        },
    }
    return VerifiedStripeEvent(id=event_id, type="refund.updated", payload=payload)


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1",
    reason="set RUN_POSTGRES_INTEGRATION_TESTS=1 to test PostgreSQL",
)
def test_refund_constraints_concurrency_reconciliation_and_rollback() -> None:
    schema_name = f"phase14_refunds_{uuid4().hex}"
    gateway = ConcurrentRefundGateway()

    with engine.connect() as connection:
        public_tables_before = set(
            connection.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        )
        public_enums_before = {
            row.typname: row.labels
            for row in connection.execute(
                text(
                    "SELECT t.typname, "
                    "string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder) labels "
                    "FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid "
                    "JOIN pg_namespace n ON n.oid=t.typnamespace "
                    "WHERE n.nspname='public' GROUP BY t.typname"
                )
            )
        }
        connection.execute(CreateSchema(schema_name))
        connection.commit()
        connection.exec_driver_sql(f'SET search_path TO "{schema_name}", public')
        mapped_connection = connection.execution_options(
            schema_translate_map={None: schema_name}
        )
        Base.metadata.create_all(mapped_connection)
        mapped_connection.commit()

        def new_payment(reference: str) -> tuple[int, int, int]:
            with Session(bind=mapped_connection, expire_on_commit=False) as session:
                customer = Customer(
                    name=f"Customer {reference}",
                    email=f"{reference}@example.com",
                )
                product = Product(
                    sku=f"SKU-{reference}",
                    name="Refund Product",
                    price=Decimal("239.82"),
                    stock=7,
                )
                order = Order(
                    customer=customer,
                    status=OrderStatus.PENDING,
                    total_amount=Decimal("239.82"),
                )
                item = OrderItem(
                    order=order,
                    product=product,
                    quantity=1,
                    unit_price=Decimal("239.82"),
                )
                payment = Payment(
                    order=order,
                    provider="stripe",
                    provider_reference=reference,
                    amount=Decimal("239.82"),
                    status=PaymentStatus.APPROVED,
                )
                session.add_all([customer, product, order, item, payment])
                session.commit()
                return payment.id, order.id, product.id

        def worker_service(worker_gateway: ConcurrentRefundGateway = gateway):
            worker_connection = engine.connect()
            worker_connection.exec_driver_sql(
                f'SET search_path TO "{schema_name}", public'
            )
            worker_connection.commit()
            worker_mapped = worker_connection.execution_options(
                schema_translate_map={None: schema_name}
            )
            session = Session(bind=worker_mapped, expire_on_commit=False)
            service = RefundService(
                RefundRepository(session),
                worker_gateway,
                currency="brl",
            )
            return worker_connection, session, service

        try:
            payment_id, order_id, product_id = new_payment("pi_concurrent_refunds")

            def create_competing(key: str) -> str:
                worker_connection, session, service = worker_service()
                try:
                    service.create(
                        payment_id,
                        RefundCreate(amount=Decimal("150.00")),
                        operation_key=key,
                    )
                    return "created"
                except RefundAmountError:
                    return "rejected"
                finally:
                    session.close()
                    worker_connection.close()

            with ThreadPoolExecutor(max_workers=2) as executor:
                competing = list(
                    executor.map(create_competing, ["concurrent-a", "concurrent-b"])
                )

            assert sorted(competing) == ["created", "rejected"]
            with Session(bind=mapped_connection, expire_on_commit=False) as session:
                payment = session.get(Payment, payment_id)
                assert payment is not None
                assert payment.status == PaymentStatus.PARTIALLY_REFUNDED
                assert session.scalar(
                    select(func.sum(Refund.amount)).where(
                        Refund.payment_id == payment_id
                    )
                ) == Decimal("150.00")
                service = RefundService(
                    RefundRepository(session), gateway, currency="brl"
                )
                service.create(
                    payment_id,
                    RefundCreate(),
                    operation_key="concurrent-remaining",
                )
                assert payment.status == PaymentStatus.REFUNDED
                assert session.scalar(
                    select(func.sum(Refund.amount)).where(
                        Refund.payment_id == payment_id,
                        Refund.status == RefundStatus.SUCCEEDED,
                    )
                ) == Decimal("239.82")
                order = session.get(Order, order_id)
                product = session.get(Product, product_id)
                assert order is not None and order.status == OrderStatus.PENDING
                assert product is not None and product.stock == 7

            race_payment_id, _, _ = new_payment("pi_api_webhook_race")
            gateway.block = True

            def create_while_webhook_arrives() -> int:
                worker_connection, session, service = worker_service()
                try:
                    return service.create(
                        race_payment_id,
                        RefundCreate(amount=Decimal("100.00")),
                        operation_key="api-webhook-race",
                    ).refund.id
                finally:
                    session.close()
                    worker_connection.close()

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(create_while_webhook_arrives)
                assert gateway.entered.wait(timeout=10)
                with Session(bind=mapped_connection) as session:
                    reservation = session.scalar(
                        select(Refund).where(Refund.payment_id == race_payment_id)
                    )
                    assert reservation is not None
                    reservation_id = reservation.id
                with Session(bind=mapped_connection) as session:
                    webhook_service = StripeWebhookService(
                        StripeWebhookRepository(session), currency="brl"
                    )
                    webhook_service.receive(
                        stripe_refund_event(
                            "evt_api_webhook_race",
                            refund_id="re_integration_3",
                            payment_intent="pi_api_webhook_race",
                            amount=10000,
                            metadata={"orderflow_refund_id": str(reservation_id)},
                        )
                    )
                gateway.release.set()
                assert future.result(timeout=10) == reservation_id
            gateway.block = False

            with Session(bind=mapped_connection) as session:
                assert (
                    session.scalar(
                        select(func.count())
                        .select_from(Refund)
                        .where(Refund.payment_id == race_payment_id)
                    )
                    == 1
                )
                payment = session.get(Payment, race_payment_id)
                assert payment is not None
                assert payment.status == PaymentStatus.PARTIALLY_REFUNDED

            webhook_payment_id, _, _ = new_payment("pi_concurrent_webhooks_refund")
            webhook_events = [
                stripe_refund_event(
                    f"evt_concurrent_refund_{suffix}",
                    refund_id="re_concurrent_webhooks",
                    payment_intent="pi_concurrent_webhooks_refund",
                    amount=1000,
                )
                for suffix in ("a", "b")
            ]

            def process_webhook(event: VerifiedStripeEvent) -> bool:
                worker_connection = engine.connect()
                worker_connection.exec_driver_sql(
                    f'SET search_path TO "{schema_name}", public'
                )
                worker_connection.commit()
                worker_mapped = worker_connection.execution_options(
                    schema_translate_map={None: schema_name}
                )
                try:
                    with Session(bind=worker_mapped, expire_on_commit=False) as session:
                        return (
                            StripeWebhookService(
                                StripeWebhookRepository(session), currency="brl"
                            )
                            .receive(event)
                            .processed
                        )
                finally:
                    worker_connection.close()

            with ThreadPoolExecutor(max_workers=2) as executor:
                webhook_results = list(executor.map(process_webhook, webhook_events))
            assert webhook_results == [True, True]

            with Session(bind=mapped_connection) as session:
                assert (
                    session.scalar(
                        select(func.count())
                        .select_from(Refund)
                        .where(Refund.payment_id == webhook_payment_id)
                    )
                    == 1
                )
                assert (
                    session.scalar(
                        select(func.count())
                        .select_from(WebhookEvent)
                        .where(
                            WebhookEvent.provider_event_id.in_(
                                [event.id for event in webhook_events]
                            )
                        )
                    )
                    == 2
                )

            rollback_payment_id, _, _ = new_payment("pi_refund_rollback")
            with Session(bind=mapped_connection, expire_on_commit=False) as session:
                repository = StripeWebhookRepository(session)
                anchor = repository.create_received(
                    provider="stripe",
                    provider_event_id="evt_refund_anchor",
                    event_type="unhandled",
                    payload={},
                )
                repository.commit_received(anchor)

                class FailOnceRepository(StripeWebhookRepository):
                    failed = False

                    def commit_refund_processed(self, **values: object):
                        if not self.failed:
                            self.failed = True
                            values["webhook_event"].provider_event_id = (  # type: ignore[union-attr]
                                "evt_refund_anchor"
                            )
                        return super().commit_refund_processed(**values)  # type: ignore[arg-type]

                recovery_service = StripeWebhookService(
                    FailOnceRepository(session), currency="brl"
                )
                rollback_event = stripe_refund_event(
                    "evt_refund_rollback",
                    refund_id="re_refund_rollback",
                    payment_intent="pi_refund_rollback",
                    amount=10000,
                )
                with pytest.raises(
                    (WebhookEventConstraintError, DuplicateWebhookEventIdError)
                ):
                    recovery_service.receive(rollback_event)
                recovered = recovery_service.receive(
                    stripe_refund_event(
                        "evt_refund_recovered",
                        refund_id="re_refund_rollback",
                        payment_intent="pi_refund_rollback",
                        amount=10000,
                    )
                )
                assert recovered.processed is True

            with Session(bind=mapped_connection) as session:
                payment = session.get(Payment, rollback_payment_id)
                assert payment is not None
                assert payment.status == PaymentStatus.PARTIALLY_REFUNDED
                assert (
                    session.scalar(
                        select(func.count())
                        .select_from(WebhookEvent)
                        .where(WebhookEvent.provider_event_id == "evt_refund_rollback")
                    )
                    == 0
                )

                first_refund = session.scalar(select(Refund).limit(1))
                assert first_refund is not None
                duplicate = Refund(
                    payment_id=first_refund.payment_id,
                    provider="stripe",
                    provider_refund_id=first_refund.provider_refund_id,
                    amount=Decimal("1.00"),
                    currency="brl",
                    status=RefundStatus.PENDING,
                )
                session.add(duplicate)
                with pytest.raises(IntegrityError):
                    session.commit()
                session.rollback()
                invalid_amount = Refund(
                    payment_id=first_refund.payment_id,
                    provider="stripe",
                    provider_refund_id="re_invalid_amount",
                    amount=Decimal("0.00"),
                    currency="brl",
                    status=RefundStatus.PENDING,
                )
                session.add(invalid_amount)
                with pytest.raises(IntegrityError):
                    session.commit()
                session.rollback()
                assert session.scalar(select(func.count()).select_from(Refund)) >= 1
        finally:
            mapped_connection.rollback()
            Base.metadata.drop_all(mapped_connection)
            mapped_connection.commit()
            mapped_connection.exec_driver_sql("RESET search_path")
            mapped_connection.execute(
                DropSchema(schema_name, cascade=True, if_exists=True)
            )
            mapped_connection.commit()

        public_tables_after = set(
            connection.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        )
        public_enums_after = {
            row.typname: row.labels
            for row in connection.execute(
                text(
                    "SELECT t.typname, "
                    "string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder) labels "
                    "FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid "
                    "JOIN pg_namespace n ON n.oid=t.typnamespace "
                    "WHERE n.nspname='public' GROUP BY t.typname"
                )
            )
        }
        assert public_tables_after == public_tables_before
        assert public_enums_after == public_enums_before

    with engine.connect() as verification:
        assert (
            verification.scalar(
                text("SELECT count(*) FROM pg_namespace WHERE nspname=:name"),
                {"name": schema_name},
            )
            == 0
        )
