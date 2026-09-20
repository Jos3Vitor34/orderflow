from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.product import Product


class DuplicateProductSkuError(Exception):
    """Raised when a SKU already belongs to another product."""


class ProductConstraintError(Exception):
    """Raised when persisted product data violates a database constraint."""


class ProductDeleteConflictError(Exception):
    """Raised when database relationships prevent product deletion."""


def raise_write_error(exc: IntegrityError) -> None:
    diagnostic = getattr(exc.orig, "diag", None)
    if getattr(diagnostic, "constraint_name", None) == "products_sku_key":
        raise DuplicateProductSkuError from exc
    raise ProductConstraintError from exc


class ProductRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, product_id: int) -> Product | None:
        return self._session.get(Product, product_id)

    def get_by_sku(self, sku: str) -> Product | None:
        return self._session.scalar(select(Product).where(Product.sku == sku))

    def create(
        self,
        *,
        sku: str,
        name: str,
        description: str | None,
        price: Decimal,
        stock: int,
        is_active: bool,
    ) -> Product:
        product = Product(
            sku=sku,
            name=name,
            description=description,
            price=price,
            stock=stock,
            is_active=is_active,
        )
        self._session.add(product)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise_write_error(exc)
        self._session.refresh(product)
        return product

    def list_page(self, *, offset: int, limit: int) -> tuple[list[Product], int]:
        total = self._session.scalar(select(func.count()).select_from(Product)) or 0
        statement = (
            select(Product).order_by(Product.id.asc()).offset(offset).limit(limit)
        )
        products = list(self._session.scalars(statement).all())
        return products, total

    def update(self, product: Product, changes: dict[str, object]) -> Product:
        for field, value in changes.items():
            setattr(product, field, value)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise_write_error(exc)
        self._session.refresh(product)
        return product

    def delete(self, product: Product) -> None:
        self._session.delete(product)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise ProductDeleteConflictError from exc
