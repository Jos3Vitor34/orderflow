from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)

from app.api.dependencies import (
    get_current_user,
    get_stripe_webhook_service,
    get_stripe_webhook_verifier,
    get_webhook_event_service,
)
from app.integrations.stripe_webhook import (
    StripeWebhookConfigurationError,
    StripeWebhookVerificationError,
    StripeWebhookVerifier,
)
from app.models.webhook_event import WebhookEvent
from app.repositories.webhook_event import (
    DuplicateWebhookEventIdError,
    WebhookEventConstraintError,
)
from app.schemas.webhook_event import (
    StripeWebhookAcknowledgement,
    WebhookEventCreate,
    WebhookEventListResponse,
    WebhookEventResponse,
)
from app.services.payment import InvalidPaymentStatusTransitionError
from app.services.stripe_webhook import StripeWebhookService
from app.services.webhook_event import (
    InvalidWebhookPayloadError,
    WebhookEventCollisionError,
    WebhookEventNotFoundError,
    WebhookEventService,
    WebhookPaymentNotFoundError,
    WebhookPaymentProviderMismatchError,
)

receiver_router = APIRouter(prefix="/webhooks", tags=["webhooks"])
admin_router = APIRouter(
    prefix="/webhook-events",
    tags=["webhook-events"],
    dependencies=[Depends(get_current_user)],
)

UNAUTHORIZED_RESPONSE = {401: {"description": "Authentication required"}}
NOT_FOUND_RESPONSE = {404: {"description": "Internal resource not found"}}
CONFLICT_RESPONSE = {409: {"description": "Webhook event conflicts with state"}}


@receiver_router.post(
    "/stripe",
    response_model=StripeWebhookAcknowledgement,
    openapi_extra={
        "parameters": [
            {
                "name": "Stripe-Signature",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
            }
        ]
    },
    responses={
        400: {"description": "Missing or invalid Stripe signature/event"},
        503: {"description": "Verification or persistence temporarily unavailable"},
    },
)
async def receive_stripe_webhook(
    request: Request,
    verifier: Annotated[StripeWebhookVerifier, Depends(get_stripe_webhook_verifier)],
    service: Annotated[StripeWebhookService, Depends(get_stripe_webhook_service)],
) -> StripeWebhookAcknowledgement:
    stripe_signature = request.headers.get("Stripe-Signature")
    if stripe_signature is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe-Signature header",
        )

    raw_body = await request.body()
    try:
        event = verifier.verify(raw_body, stripe_signature)
    except StripeWebhookConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe webhook verification is not configured",
        ) from exc
    except StripeWebhookVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Stripe webhook",
        ) from exc

    try:
        service.receive(event)
    except (DuplicateWebhookEventIdError, WebhookEventConstraintError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe webhook persistence is temporarily unavailable",
        ) from exc
    return StripeWebhookAcknowledgement()


@receiver_router.post(
    "/{provider}",
    response_model=WebhookEventResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"description": "Identical event already received"},
        404: {"description": "Referenced payment not found"},
        409: {"description": "Event collision or invalid payment transition"},
    },
)
def receive_webhook(
    provider: Annotated[str, Path(min_length=1, max_length=50, pattern=r".*\S.*")],
    data: WebhookEventCreate,
    response: Response,
    service: Annotated[WebhookEventService, Depends(get_webhook_event_service)],
) -> WebhookEvent:
    try:
        receipt = service.receive(provider=provider, data=data)
    except InvalidWebhookPayloadError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Recognized payment event requires a valid provider_reference",
        ) from exc
    except WebhookPaymentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Referenced payment not found",
        ) from exc
    except (
        WebhookEventCollisionError,
        WebhookPaymentProviderMismatchError,
        InvalidPaymentStatusTransitionError,
        DuplicateWebhookEventIdError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Webhook event conflicts with existing state",
        ) from exc
    except WebhookEventConstraintError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Webhook event could not be persisted",
        ) from exc

    response.status_code = (
        status.HTTP_201_CREATED if receipt.created else status.HTTP_200_OK
    )
    return receipt.event


@admin_router.get(
    "",
    response_model=WebhookEventListResponse,
    responses=UNAUTHORIZED_RESPONSE,
)
def list_webhook_events(
    service: Annotated[WebhookEventService, Depends(get_webhook_event_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> WebhookEventListResponse:
    return service.list(page=page, page_size=page_size)


@admin_router.get(
    "/{webhook_event_id}",
    response_model=WebhookEventResponse,
    responses=UNAUTHORIZED_RESPONSE | NOT_FOUND_RESPONSE,
)
def get_webhook_event(
    webhook_event_id: int,
    service: Annotated[WebhookEventService, Depends(get_webhook_event_service)],
) -> WebhookEvent:
    try:
        return service.get(webhook_event_id)
    except WebhookEventNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook event not found",
        ) from exc
