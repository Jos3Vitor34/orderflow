from app.models.customer import Customer
from app.repositories.customer import (
    CustomerRepository,
    DuplicateCustomerEmailError,
)
from app.schemas.customer import (
    CustomerCreate,
    CustomerListResponse,
    CustomerUpdate,
)


class CustomerNotFoundError(Exception):
    """Raised when a customer identifier does not exist."""


class CustomerService:
    def __init__(self, repository: CustomerRepository) -> None:
        self._repository = repository

    @staticmethod
    def normalize_email(email: str) -> str:
        return email.strip().lower()

    def create(self, data: CustomerCreate) -> Customer:
        email = self.normalize_email(str(data.email))
        if self._repository.get_by_email(email) is not None:
            raise DuplicateCustomerEmailError
        return self._repository.create(
            name=data.name,
            email=email,
            phone=data.phone,
        )

    def list(self, *, page: int, page_size: int) -> CustomerListResponse:
        customers, total = self._repository.list_page(
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        pages = (total + page_size - 1) // page_size
        return CustomerListResponse(
            items=customers,
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )

    def get(self, customer_id: int) -> Customer:
        customer = self._repository.get_by_id(customer_id)
        if customer is None:
            raise CustomerNotFoundError
        return customer

    def update(self, customer_id: int, data: CustomerUpdate) -> Customer:
        customer = self.get(customer_id)
        changes = data.model_dump(exclude_unset=True)
        if "email" in changes:
            email = self.normalize_email(str(changes["email"]))
            existing = self._repository.get_by_email(email)
            if existing is not None and existing.id != customer.id:
                raise DuplicateCustomerEmailError
            changes["email"] = email
        return self._repository.update(customer, changes)

    def delete(self, customer_id: int) -> None:
        self._repository.delete(self.get(customer_id))
