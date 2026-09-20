from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.payment import PaymentResponse


class StripePaymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: Annotated[int, Field(ge=1)]


class StripePaymentIntentResponse(BaseModel):
    payment_intent_id: str
    status: str
    amount: int
    currency: str


class StripePaymentResponse(BaseModel):
    payment: PaymentResponse
    stripe: StripePaymentIntentResponse
