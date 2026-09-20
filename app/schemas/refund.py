from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.models.refund import RefundStatus


class RefundReason(StrEnum):
    DUPLICATE = "duplicate"
    FRAUDULENT = "fraudulent"
    REQUESTED_BY_CUSTOMER = "requested_by_customer"


class RefundCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Annotated[
        Decimal | None,
        Field(gt=0, max_digits=12, decimal_places=2),
    ] = None
    reason: RefundReason | None = None


class RefundResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    payment_id: int
    provider: str
    provider_refund_id: str | None
    amount: Decimal
    currency: str
    status: RefundStatus
    reason: str | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class RefundListResponse(BaseModel):
    items: list[RefundResponse]
    total: int
    page: int
    page_size: int
    pages: int
