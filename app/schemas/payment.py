from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.payment import PaymentStatus

PaymentProvider = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=50),
]
PaymentProviderReference = Annotated[
    str,
    StringConstraints(min_length=1, max_length=255),
]


class PaymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: Annotated[int, Field(ge=1)]
    provider: PaymentProvider
    provider_reference: PaymentProviderReference | None = None


class PaymentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: PaymentStatus


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    provider: str
    provider_reference: str | None
    amount: Decimal
    status: PaymentStatus
    created_at: datetime
    updated_at: datetime


class PaymentListResponse(BaseModel):
    items: list[PaymentResponse]
    total: int
    page: int
    page_size: int
    pages: int
