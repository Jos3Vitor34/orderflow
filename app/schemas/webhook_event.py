from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, JsonValue, StringConstraints

WebhookProviderEventId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=255),
]
WebhookEventType = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
]


class WebhookEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_event_id: WebhookProviderEventId
    event_type: WebhookEventType
    payload: dict[str, JsonValue]


class WebhookEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider: str
    provider_event_id: str
    event_type: str
    payload: dict[str, JsonValue]
    received_at: datetime
    processed_at: datetime | None


class WebhookEventListResponse(BaseModel):
    items: list[WebhookEventResponse]
    total: int
    page: int
    page_size: int
    pages: int


class StripeWebhookAcknowledgement(BaseModel):
    received: bool = True
