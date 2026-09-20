from datetime import datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
)

CustomerName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
]
CustomerEmail = Annotated[EmailStr, Field(max_length=320)]
CustomerPhone = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=32),
]


class CustomerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CustomerName
    email: CustomerEmail
    phone: CustomerPhone | None = None


class CustomerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CustomerName | None = None
    email: CustomerEmail | None = None
    phone: CustomerPhone | None = None

    @field_validator("name", "email")
    @classmethod
    def required_fields_cannot_be_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("field cannot be null")
        return value


class CustomerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: EmailStr
    phone: str | None
    created_at: datetime
    updated_at: datetime


class CustomerListResponse(BaseModel):
    items: list[CustomerResponse]
    total: int
    page: int
    page_size: int
    pages: int
