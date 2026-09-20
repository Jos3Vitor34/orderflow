from sqlalchemy import Enum, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base
from app.models import (
    Customer,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentStatus,
    Product,
    User,
    WebhookEvent,
)


def _constraint_names(model: type[Base]) -> set[str | None]:
    return {constraint.name for constraint in model.__table__.constraints}


def _unique_column_sets(model: type[Base]) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_expected_models_are_registered() -> None:
    assert set(Base.metadata.tables) == {
        "customers",
        "order_items",
        "orders",
        "payments",
        "products",
        "refunds",
        "users",
        "webhook_events",
    }


def test_money_columns_use_fixed_precision_numeric() -> None:
    for column in (
        Product.__table__.c.price,
        Order.__table__.c.total_amount,
        OrderItem.__table__.c.unit_price,
        Payment.__table__.c.amount,
    ):
        assert isinstance(column.type, Numeric)
        assert column.type.precision == 12
        assert column.type.scale == 2
        assert column.type.asdecimal is True


def test_domain_constraints_are_declared() -> None:
    assert "ck_products_price_non_negative" in _constraint_names(Product)
    assert "ck_products_stock_non_negative" in _constraint_names(Product)
    assert "ck_orders_total_amount_non_negative" in _constraint_names(Order)
    assert "ck_order_items_quantity_positive" in _constraint_names(OrderItem)
    assert "ck_order_items_unit_price_non_negative" in _constraint_names(OrderItem)
    assert "ck_payments_amount_positive" in _constraint_names(Payment)

    assert ("email",) in _unique_column_sets(User)
    assert ("email",) in _unique_column_sets(Customer)
    assert ("sku",) in _unique_column_sets(Product)
    assert ("order_id", "product_id") in _unique_column_sets(OrderItem)
    assert ("provider_reference",) in _unique_column_sets(Payment)
    assert ("provider_event_id",) in _unique_column_sets(WebhookEvent)


def test_foreign_keys_and_relationships_match_the_domain() -> None:
    assert Customer.orders.property.mapper.class_ is Order
    assert Order.customer.property.mapper.class_ is Customer
    assert Order.items.property.mapper.class_ is OrderItem
    assert Order.payments.property.mapper.class_ is Payment
    assert OrderItem.order.property.mapper.class_ is Order
    assert OrderItem.product.property.mapper.class_ is Product
    assert Product.order_items.property.mapper.class_ is OrderItem
    assert Payment.order.property.mapper.class_ is Order

    assert next(iter(Order.__table__.c.customer_id.foreign_keys)).target_fullname == (
        "customers.id"
    )
    assert next(iter(OrderItem.__table__.c.order_id.foreign_keys)).target_fullname == (
        "orders.id"
    )
    assert next(
        iter(OrderItem.__table__.c.product_id.foreign_keys)
    ).target_fullname == ("products.id")
    assert next(iter(Payment.__table__.c.order_id.foreign_keys)).target_fullname == (
        "orders.id"
    )


def test_statuses_and_postgresql_payload_type_are_explicit() -> None:
    assert list(OrderStatus) == [
        OrderStatus.PENDING,
        OrderStatus.PROCESSING,
        OrderStatus.CONFIRMED,
        OrderStatus.SHIPPED,
        OrderStatus.DELIVERED,
        OrderStatus.CANCELLED,
    ]
    assert list(PaymentStatus) == [
        PaymentStatus.PENDING,
        PaymentStatus.APPROVED,
        PaymentStatus.FAILED,
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.REFUNDED,
    ]
    assert isinstance(Order.__table__.c.status.type, Enum)
    assert isinstance(Payment.__table__.c.status.type, Enum)
    assert isinstance(WebhookEvent.__table__.c.payload.type, JSONB)


def test_foreign_key_columns_have_join_indexes() -> None:
    indexed_columns = {
        tuple(column.name for column in index.columns)
        for table in Base.metadata.tables.values()
        for index in table.indexes
    }
    assert {("customer_id",), ("order_id",), ("product_id",)} <= indexed_columns
