from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.customer import Customer


class DuplicateCustomerEmailError(Exception):
    """Raised when a normalized e-mail already belongs to a customer."""


class CustomerDeleteConflictError(Exception):
    """Raised when database relationships prevent customer deletion."""


class CustomerRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, customer_id: int) -> Customer | None:
        return self._session.get(Customer, customer_id)

    def get_by_email(self, email: str) -> Customer | None:
        statement = select(Customer).where(func.lower(Customer.email) == email)
        return self._session.scalar(statement)

    def create(self, *, name: str, email: str, phone: str | None) -> Customer:
        customer = Customer(name=name, email=email, phone=phone)
        self._session.add(customer)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise DuplicateCustomerEmailError from exc
        self._session.refresh(customer)
        return customer

    def list_page(self, *, offset: int, limit: int) -> tuple[list[Customer], int]:
        total = self._session.scalar(select(func.count()).select_from(Customer)) or 0
        statement = (
            select(Customer).order_by(Customer.id.asc()).offset(offset).limit(limit)
        )
        customers = list(self._session.scalars(statement).all())
        return customers, total

    def update(self, customer: Customer, changes: dict[str, object]) -> Customer:
        for field, value in changes.items():
            setattr(customer, field, value)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise DuplicateCustomerEmailError from exc
        self._session.refresh(customer)
        return customer

    def delete(self, customer: Customer) -> None:
        self._session.delete(customer)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise CustomerDeleteConflictError from exc
