from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.models.customer import Customer
from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.models.product import Product


class OrderPersistenceError(Exception):
    """Raised when concurrent database changes prevent order persistence."""


class OrderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_customer_by_id(self, customer_id: int) -> Customer | None:
        return self._session.get(Customer, customer_id)

    def get_products_by_ids(self, product_ids: set[int]) -> list[Product]:
        statement = (
            select(Product)
            .where(Product.id.in_(product_ids))
            .order_by(Product.id.asc())
        )
        return list(self._session.scalars(statement).all())

    def create(
        self,
        *,
        customer_id: int,
        total_amount: Decimal,
        items: list[tuple[int, int, Decimal]],
    ) -> Order:
        order = Order(customer_id=customer_id, total_amount=total_amount)
        order.items = [
            OrderItem(
                product_id=product_id,
                quantity=quantity,
                unit_price=unit_price,
            )
            for product_id, quantity, unit_price in items
        ]
        self._session.add(order)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise OrderPersistenceError from exc
        persisted = self.get_by_id(order.id)
        if persisted is None:
            raise OrderPersistenceError
        return persisted

    def get_by_id(self, order_id: int) -> Order | None:
        statement = (
            select(Order).options(selectinload(Order.items)).where(Order.id == order_id)
        )
        return self._session.scalar(statement)

    def get_by_id_for_update(self, order_id: int) -> Order | None:
        statement = (
            select(Order)
            .options(selectinload(Order.items))
            .where(Order.id == order_id)
            .with_for_update()
        )
        return self._session.scalar(statement)

    def get_products_by_ids_for_update(
        self,
        product_ids: set[int],
    ) -> list[Product]:
        statement = (
            select(Product)
            .where(Product.id.in_(product_ids))
            .order_by(Product.id.asc())
            .with_for_update()
        )
        return list(self._session.scalars(statement).all())

    def list_page(self, *, offset: int, limit: int) -> tuple[list[Order], int]:
        total = self._session.scalar(select(func.count()).select_from(Order)) or 0
        statement = (
            select(Order)
            .options(selectinload(Order.items))
            .order_by(Order.id.asc())
            .offset(offset)
            .limit(limit)
        )
        return list(self._session.scalars(statement).all()), total

    def update_status(self, order: Order, new_status: OrderStatus) -> Order:
        order.status = new_status
        try:
            self._session.commit()
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise OrderPersistenceError from exc
        return order

    def rollback(self) -> None:
        self._session.rollback()
