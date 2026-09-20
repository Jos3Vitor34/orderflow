from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

ProductSku = Annotated[str, StringConstraints(min_length=1, max_length=64)]
ProductName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=160),
]
ProductPrice = Annotated[
    Decimal,
    Field(ge=Decimal("0"), max_digits=12, decimal_places=2),
]
ProductStock = Annotated[int, Field(ge=0, le=2_147_483_647)]


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: ProductSku
    name: ProductName
    description: str | None = None
    price: ProductPrice
    stock: ProductStock = 0
    is_active: bool = True


class ProductUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: ProductSku | None = None
    name: ProductName | None = None
    description: str | None = None
    price: ProductPrice | None = None
    stock: ProductStock | None = None
    is_active: bool | None = None

    @field_validator("sku", "name", "price", "stock", "is_active")
    @classmethod
    def required_fields_cannot_be_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("field cannot be null")
        return value


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str
    description: str | None
    price: Decimal
    stock: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProductListResponse(BaseModel):
    items: list[ProductResponse]
    total: int
    page: int
    page_size: int
    pages: int
