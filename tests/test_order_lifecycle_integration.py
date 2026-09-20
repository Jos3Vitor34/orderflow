import os
from collections.abc import Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.base import Base
from app.db.session import engine
from app.models.customer import Customer
from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.models.payment import Payment, PaymentStatus
from app.models.product import Product
from app.models.refund import Refund, RefundStatus
from app.models.webhook_event import WebhookEvent
from app.repositories.order import OrderRepository
from app.schemas.order import OrderUpdate
from app.services.order import (
    InactiveOrderProductError,
    InsufficientOrderStockError,
    OrderService,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1",
        reason="set RUN_POSTGRES_INTEGRATION_TESTS=1 to test PostgreSQL",
    ),
]


@dataclass(frozen=True)
class SeededOrder:
    order_id: int
    product_ids: list[int]


@dataclass(frozen=True)
class PostgresOrderContext:
    schema_name: str

    @contextmanager
    def session(self) -> Iterator[Session]:
        connection = engine.connect()
        connection.exec_driver_sql(f'SET search_path TO "{self.schema_name}", public')
        connection.commit()
        mapped_connection = connection.execution_options(
            schema_translate_map={None: self.schema_name}
        )
        try:
            with Session(bind=mapped_connection, expire_on_commit=False) as session:
                yield session
        finally:
            connection.close()

    def seed_order(
        self,
        *,
        stocks: list[int],
        quantities: list[int],
        status: OrderStatus = OrderStatus.PROCESSING,
        active: list[bool] | None = None,
    ) -> SeededOrder:
        suffix = uuid4().hex
        active_values = active or [True] * len(stocks)
        with self.session() as session:
            customer = Customer(
                name="Lifecycle Customer",
                email=f"lifecycle-{suffix}@example.com",
            )
            products = [
                Product(
                    sku=f"LIFECYCLE-{suffix}-{index}",
                    name=f"Lifecycle Product {index}",
                    price=Decimal(f"{index + 1}.00"),
                    stock=stock,
                    is_active=is_active,
                )
                for index, (stock, is_active) in enumerate(
                    zip(stocks, active_values, strict=True)
                )
            ]
            session.add_all([customer, *products])
            session.flush()
            order = Order(
                customer_id=customer.id,
                status=status,
                total_amount=sum(
                    (
                        product.price * quantity
                        for product, quantity in zip(products, quantities, strict=True)
                    ),
                    Decimal("0.00"),
                ),
            )
            order.items = [
                OrderItem(
                    product_id=product.id,
                    quantity=quantity,
                    unit_price=product.price,
                )
                for product, quantity in zip(products, quantities, strict=True)
            ]
            session.add(order)
            session.commit()
            return SeededOrder(
                order_id=order.id,
                product_ids=[product.id for product in products],
            )


@pytest.fixture(scope="module")
def postgres_order_context() -> Generator[PostgresOrderContext]:
    schema_name = f"phase16_orders_{uuid4().hex}"
    with engine.connect() as connection:
        public_tables_before = set(
            connection.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        )
        connection.execute(CreateSchema(schema_name))
        connection.commit()
        connection.exec_driver_sql(f'SET search_path TO "{schema_name}", public')
        mapped_connection = connection.execution_options(
            schema_translate_map={None: schema_name}
        )
        Base.metadata.create_all(mapped_connection)
        mapped_connection.commit()

        yield PostgresOrderContext(schema_name)

        mapped_connection.rollback()
        Base.metadata.drop_all(mapped_connection)
        mapped_connection.commit()
        mapped_connection.exec_driver_sql("RESET search_path")
        mapped_connection.execute(DropSchema(schema_name, cascade=True, if_exists=True))
        mapped_connection.commit()
        public_tables_after = set(
            connection.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        )
        assert public_tables_after == public_tables_before


def test_order_and_products_use_real_row_locks_in_deterministic_order(
    postgres_order_context: PostgresOrderContext,
) -> None:
    seeded = postgres_order_context.seed_order(stocks=[3, 4], quantities=[1, 1])
    statements: list[str] = []

    with (
        postgres_order_context.session() as locking_session,
        postgres_order_context.session() as competing_session,
    ):
        locking_repository = OrderRepository(locking_session)
        competing_repository = OrderRepository(competing_session)
        locked_order = locking_repository.get_by_id_for_update(seeded.order_id)
        assert locked_order is not None

        competing_session.execute(text("SET LOCAL lock_timeout = '100ms'"))
        with pytest.raises(OperationalError):
            competing_repository.get_by_id_for_update(seeded.order_id)
        competing_session.rollback()
        locking_session.rollback()
        assert competing_repository.get_by_id_for_update(seeded.order_id) is not None
        competing_session.rollback()

        def record_statement(
            conn: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            statements.append(statement)

        event.listen(locking_session.bind, "before_cursor_execute", record_statement)
        try:
            locked_products = locking_repository.get_products_by_ids_for_update(
                set(reversed(seeded.product_ids))
            )
        finally:
            event.remove(
                locking_session.bind, "before_cursor_execute", record_statement
            )
        assert [product.id for product in locked_products] == sorted(seeded.product_ids)
        product_lock_statement = statements[-1].upper()
        assert "ORDER BY" in product_lock_statement
        assert "PRODUCTS.ID ASC" in product_lock_statement
        assert "FOR UPDATE" in product_lock_statement

        competing_session.execute(text("SET LOCAL lock_timeout = '100ms'"))
        with pytest.raises(OperationalError):
            competing_repository.get_products_by_ids_for_update(set(seeded.product_ids))
        competing_session.rollback()
        locking_session.rollback()
        assert len(
            competing_repository.get_products_by_ids_for_update(set(seeded.product_ids))
        ) == len(seeded.product_ids)


def test_confirmation_is_atomic_uses_snapshots_and_preserves_financial_records(
    postgres_order_context: PostgresOrderContext,
) -> None:
    seeded = postgres_order_context.seed_order(stocks=[2, 3], quantities=[2, 1])
    suffix = uuid4().hex
    with postgres_order_context.session() as session:
        order = session.get(Order, seeded.order_id)
        assert order is not None
        payment = Payment(
            order_id=order.id,
            provider="manual",
            provider_reference=f"payment-{suffix}",
            amount=order.total_amount,
            status=PaymentStatus.APPROVED,
        )
        session.add(payment)
        session.flush()
        refund = Refund(
            payment_id=payment.id,
            provider="stripe",
            provider_refund_id=f"refund-{suffix}",
            amount=Decimal("1.00"),
            currency="brl",
            status=RefundStatus.PENDING,
        )
        webhook = WebhookEvent(
            provider="stripe",
            provider_event_id=f"event-{suffix}",
            event_type="payment_intent.succeeded",
            payload={"unchanged": True},
        )
        session.add_all([refund, webhook])
        session.commit()
        payment_id, refund_id, webhook_id = payment.id, refund.id, webhook.id

    with postgres_order_context.session() as session:
        response = OrderService(OrderRepository(session)).update(
            seeded.order_id,
            OrderUpdate(status=OrderStatus.CONFIRMED),
        )
        assert response.status == OrderStatus.CONFIRMED
        assert [item.quantity for item in response.items] == [2, 1]
        assert [item.unit_price for item in response.items] == [
            Decimal("1.00"),
            Decimal("2.00"),
        ]

    with postgres_order_context.session() as session:
        assert list(
            session.scalars(
                select(Product.stock)
                .where(Product.id.in_(seeded.product_ids))
                .order_by(Product.id)
            )
        ) == [0, 2]
        payment = session.get(Payment, payment_id)
        refund = session.get(Refund, refund_id)
        webhook = session.get(WebhookEvent, webhook_id)
        assert payment is not None and payment.status == PaymentStatus.APPROVED
        assert refund is not None and refund.status == RefundStatus.PENDING
        assert webhook is not None and webhook.processed_at is None
        assert webhook.payload == {"unchanged": True}


def test_insufficient_or_inactive_product_rolls_back_all_items_and_reuses_session(
    postgres_order_context: PostgresOrderContext,
) -> None:
    insufficient = postgres_order_context.seed_order(stocks=[5, 1], quantities=[3, 2])
    inactive = postgres_order_context.seed_order(
        stocks=[5], quantities=[1], active=[False]
    )
    with postgres_order_context.session() as session:
        service = OrderService(OrderRepository(session))
        with pytest.raises(InsufficientOrderStockError):
            service.update(
                insufficient.order_id,
                OrderUpdate(status=OrderStatus.CONFIRMED),
            )
        assert list(
            session.scalars(
                select(Product.stock)
                .where(Product.id.in_(insufficient.product_ids))
                .order_by(Product.id)
            )
        ) == [5, 1]
        order = session.get(Order, insufficient.order_id)
        assert order is not None and order.status == OrderStatus.PROCESSING

        with pytest.raises(InactiveOrderProductError):
            service.update(
                inactive.order_id,
                OrderUpdate(status=OrderStatus.CONFIRMED),
            )
        product = session.get(Product, inactive.product_ids[0])
        assert product is not None
        product.is_active = True
        session.commit()
        recovered = service.update(
            inactive.order_id,
            OrderUpdate(status=OrderStatus.CONFIRMED),
        )
        assert recovered.status == OrderStatus.CONFIRMED


def test_failure_after_flush_rolls_back_order_and_stock_then_session_recovers(
    postgres_order_context: PostgresOrderContext,
) -> None:
    seeded = postgres_order_context.seed_order(stocks=[4, 5], quantities=[2, 3])

    class FailAfterFlushRepository(OrderRepository):
        def update_status(
            self,
            order: Order,
            new_status: OrderStatus,
        ) -> Order:
            order.status = new_status
            self._session.flush()
            raise RuntimeError("forced failure after database flush")

    with postgres_order_context.session() as session:
        with pytest.raises(RuntimeError, match="forced failure"):
            OrderService(FailAfterFlushRepository(session)).update(
                seeded.order_id,
                OrderUpdate(status=OrderStatus.CONFIRMED),
            )

        session.expire_all()
        order = session.get(Order, seeded.order_id)
        assert order is not None and order.status == OrderStatus.PROCESSING
        assert list(
            session.scalars(
                select(Product.stock)
                .where(Product.id.in_(seeded.product_ids))
                .order_by(Product.id)
            )
        ) == [4, 5]

        recovered = OrderService(OrderRepository(session)).update(
            seeded.order_id,
            OrderUpdate(status=OrderStatus.CONFIRMED),
        )
        assert recovered.status == OrderStatus.CONFIRMED
        assert list(
            session.scalars(
                select(Product.stock)
                .where(Product.id.in_(seeded.product_ids))
                .order_by(Product.id)
            )
        ) == [2, 2]


def test_stock_constraint_rejects_negative_value_and_session_recovers(
    postgres_order_context: PostgresOrderContext,
) -> None:
    seeded = postgres_order_context.seed_order(stocks=[1], quantities=[1])
    with postgres_order_context.session() as session:
        product = session.get(Product, seeded.product_ids[0])
        assert product is not None
        product.stock = -1
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        assert (
            session.scalar(select(Product.stock).where(Product.id == product.id)) == 1
        )


def test_two_orders_compete_for_last_stock_without_overselling(
    postgres_order_context: PostgresOrderContext,
) -> None:
    suffix = uuid4().hex
    with postgres_order_context.session() as session:
        customer = Customer(
            name="Concurrent Customer",
            email=f"concurrent-{suffix}@example.com",
        )
        product = Product(
            sku=f"LAST-{suffix}",
            name="Last unit",
            price=Decimal("10.00"),
            stock=1,
            is_active=True,
        )
        session.add_all([customer, product])
        session.flush()
        orders = []
        for _ in range(2):
            order = Order(
                customer_id=customer.id,
                status=OrderStatus.PROCESSING,
                total_amount=Decimal("10.00"),
            )
            order.items = [
                OrderItem(
                    product_id=product.id,
                    quantity=1,
                    unit_price=Decimal("10.00"),
                )
            ]
            session.add(order)
            orders.append(order)
        session.commit()
        order_ids = [order.id for order in orders]
        product_id = product.id

    barrier = Barrier(2)

    class SynchronizedProductLockRepository(OrderRepository):
        def get_products_by_ids_for_update(
            self,
            product_ids: set[int],
        ) -> list[Product]:
            barrier.wait(timeout=10)
            return super().get_products_by_ids_for_update(product_ids)

    def confirm(order_id: int) -> str:
        with postgres_order_context.session() as session:
            try:
                response = OrderService(
                    SynchronizedProductLockRepository(session)
                ).update(order_id, OrderUpdate(status=OrderStatus.CONFIRMED))
            except InsufficientOrderStockError:
                return "insufficient"
            return response.status.value

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(confirm, order_ids))

    assert sorted(results) == ["confirmed", "insufficient"]
    with postgres_order_context.session() as session:
        assert (
            session.scalar(select(Product.stock).where(Product.id == product_id)) == 0
        )
        statuses = list(
            session.scalars(
                select(Order.status).where(Order.id.in_(order_ids)).order_by(Order.id)
            )
        )
        assert sorted(status.value for status in statuses) == [
            "confirmed",
            "processing",
        ]


def test_two_confirmations_of_same_order_debit_stock_once(
    postgres_order_context: PostgresOrderContext,
) -> None:
    seeded = postgres_order_context.seed_order(stocks=[2], quantities=[1])
    barrier = Barrier(2)

    class SynchronizedOrderLockRepository(OrderRepository):
        def get_by_id_for_update(self, order_id: int) -> Order | None:
            barrier.wait(timeout=10)
            return super().get_by_id_for_update(order_id)

    def confirm() -> str:
        with postgres_order_context.session() as session:
            response = OrderService(SynchronizedOrderLockRepository(session)).update(
                seeded.order_id,
                OrderUpdate(status=OrderStatus.CONFIRMED),
            )
            return response.status.value

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: confirm(), range(2)))

    assert results == ["confirmed", "confirmed"]
    with postgres_order_context.session() as session:
        assert (
            session.scalar(
                select(Product.stock).where(Product.id == seeded.product_ids[0])
            )
            == 1
        )


def test_opposite_item_order_concurrency_uses_same_lock_order_without_deadlock(
    postgres_order_context: PostgresOrderContext,
) -> None:
    suffix = uuid4().hex
    with postgres_order_context.session() as session:
        customer = Customer(
            name="Deadlock Customer",
            email=f"deadlock-{suffix}@example.com",
        )
        products = [
            Product(
                sku=f"DEADLOCK-{suffix}-{index}",
                name=f"Deadlock Product {index}",
                price=Decimal("1.00"),
                stock=2,
                is_active=True,
            )
            for index in range(2)
        ]
        session.add_all([customer, *products])
        session.flush()
        orders = []
        for product_order in (products, list(reversed(products))):
            order = Order(
                customer_id=customer.id,
                status=OrderStatus.PROCESSING,
                total_amount=Decimal("2.00"),
            )
            order.items = [
                OrderItem(
                    product_id=product.id,
                    quantity=1,
                    unit_price=product.price,
                )
                for product in product_order
            ]
            session.add(order)
            orders.append(order)
        session.commit()
        order_ids = [order.id for order in orders]
        product_ids = [product.id for product in products]

    barrier = Barrier(2)

    class SynchronizedRepository(OrderRepository):
        def get_products_by_ids_for_update(
            self,
            product_ids: set[int],
        ) -> list[Product]:
            barrier.wait(timeout=10)
            return super().get_products_by_ids_for_update(product_ids)

    def confirm(order_id: int) -> str:
        with postgres_order_context.session() as session:
            return (
                OrderService(SynchronizedRepository(session))
                .update(
                    order_id,
                    OrderUpdate(status=OrderStatus.CONFIRMED),
                )
                .status.value
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(confirm, order_ids))

    assert results == ["confirmed", "confirmed"]
    with postgres_order_context.session() as session:
        assert list(
            session.scalars(
                select(Product.stock)
                .where(Product.id.in_(product_ids))
                .order_by(Product.id)
            )
        ) == [0, 0]
