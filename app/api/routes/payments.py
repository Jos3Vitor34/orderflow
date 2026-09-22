from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.api.dependencies import (
    get_payment_service,
    get_stripe_payment_service,
    require_operator,
    require_viewer,
)
from app.api.responses import ResponseDescriptions
from app.integrations.stripe import (
    StripeAuthenticationError,
    StripeConfigurationError,
    StripeIdempotencyError,
    StripeInvalidRequestError,
    StripeProviderError,
    StripeTemporaryError,
)
from app.models.payment import Payment
from app.repositories.payment import (
    DuplicatePaymentReferenceError,
    PaymentConstraintError,
)
from app.schemas.payment import (
    PaymentCreate,
    PaymentListResponse,
    PaymentResponse,
    PaymentUpdate,
)
from app.schemas.stripe_payment import (
    StripePaymentCreate,
    StripePaymentIntentResponse,
    StripePaymentResponse,
)
from app.services.payment import (
    CancelledOrderPaymentError,
    InvalidPaymentStatusTransitionError,
    NonPositiveOrderTotalError,
    PaymentNotFoundError,
    PaymentOrderNotFoundError,
    PaymentService,
)
from app.services.stripe_payment import (
    StripePaymentAmountError,
    StripePaymentCancelledOrderError,
    StripePaymentIntentMismatchError,
    StripePaymentOrderChangedError,
    StripePaymentOrderNotFoundError,
    StripePaymentService,
)

router = APIRouter(
    prefix="/payments",
    tags=["payments"],
)

UNAUTHORIZED_RESPONSE: ResponseDescriptions = {
    401: {"description": "Authentication required"}
}
NOT_FOUND_RESPONSE: ResponseDescriptions = {
    404: {"description": "Payment or order not found"}
}
CONFLICT_RESPONSE: ResponseDescriptions = {
    409: {"description": "Payment conflicts with resource state"}
}


@router.post(
    "",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
    responses=UNAUTHORIZED_RESPONSE | NOT_FOUND_RESPONSE | CONFLICT_RESPONSE,
    dependencies=[Depends(require_operator)],
)
def create_payment(
    data: PaymentCreate,
    service: Annotated[PaymentService, Depends(get_payment_service)],
) -> Payment:
    try:
        return service.create(data)
    except PaymentOrderNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        ) from exc
    except CancelledOrderPaymentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cancelled orders cannot receive new payments",
        ) from exc
    except NonPositiveOrderTotalError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Order total must be positive to create a payment",
        ) from exc
    except DuplicatePaymentReferenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Provider reference already belongs to another payment",
        ) from exc
    except PaymentConstraintError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payment could not be created because related data changed",
        ) from exc


@router.post(
    "/stripe",
    response_model=StripePaymentResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **UNAUTHORIZED_RESPONSE,
        200: {"description": "Existing idempotent Stripe payment"},
        404: {"description": "Order not found"},
        409: {"description": "Order or idempotency state conflict"},
        502: {"description": "Stripe rejected or returned an invalid response"},
        503: {"description": "Stripe configuration or connectivity unavailable"},
    },
    dependencies=[Depends(require_operator)],
)
def create_stripe_payment(
    data: StripePaymentCreate,
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
            pattern=r".*\S.*",
        ),
    ],
    service: Annotated[StripePaymentService, Depends(get_stripe_payment_service)],
) -> StripePaymentResponse:
    try:
        receipt = service.create(data, operation_key=idempotency_key)
    except StripePaymentOrderNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        ) from exc
    except (
        StripePaymentCancelledOrderError,
        StripePaymentOrderChangedError,
        DuplicatePaymentReferenceError,
        StripeIdempotencyError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stripe payment conflicts with current resource state",
        ) from exc
    except StripePaymentAmountError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Order total is outside the supported Stripe BRL range",
        ) from exc
    except StripeConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe Test Mode is not configured",
        ) from exc
    except StripeTemporaryError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe is temporarily unavailable; retry with the same key",
        ) from exc
    except (StripeAuthenticationError, StripeInvalidRequestError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe rejected the server request",
        ) from exc
    except (StripeProviderError, StripePaymentIntentMismatchError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe returned an invalid provider response",
        ) from exc
    except PaymentConstraintError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Local payment persistence failed; retry with the same key",
        ) from exc

    response.status_code = (
        status.HTTP_201_CREATED if receipt.created else status.HTTP_200_OK
    )
    return StripePaymentResponse(
        payment=PaymentResponse.model_validate(receipt.payment),
        stripe=StripePaymentIntentResponse(
            payment_intent_id=receipt.payment_intent.id,
            status=receipt.payment_intent.status,
            amount=receipt.payment_intent.amount,
            currency=receipt.payment_intent.currency,
        ),
    )


@router.get(
    "",
    response_model=PaymentListResponse,
    responses=UNAUTHORIZED_RESPONSE,
    dependencies=[Depends(require_viewer)],
)
def list_payments(
    service: Annotated[PaymentService, Depends(get_payment_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaymentListResponse:
    return service.list(page=page, page_size=page_size)


@router.get(
    "/{payment_id}",
    response_model=PaymentResponse,
    responses=UNAUTHORIZED_RESPONSE | NOT_FOUND_RESPONSE,
    dependencies=[Depends(require_viewer)],
)
def get_payment(
    payment_id: int,
    service: Annotated[PaymentService, Depends(get_payment_service)],
) -> Payment:
    try:
        return service.get(payment_id)
    except PaymentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found",
        ) from exc


@router.patch(
    "/{payment_id}",
    response_model=PaymentResponse,
    responses=UNAUTHORIZED_RESPONSE | NOT_FOUND_RESPONSE | CONFLICT_RESPONSE,
    dependencies=[Depends(require_operator)],
)
def update_payment(
    payment_id: int,
    data: PaymentUpdate,
    service: Annotated[PaymentService, Depends(get_payment_service)],
) -> Payment:
    try:
        return service.update(payment_id, data)
    except PaymentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found",
        ) from exc
    except InvalidPaymentStatusTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payment status transition is not allowed",
        ) from exc
    except PaymentConstraintError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payment status could not be updated",
        ) from exc
