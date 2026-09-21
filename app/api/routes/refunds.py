from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.api.dependencies import get_refund_service, require_operator, require_viewer
from app.integrations.stripe import (
    StripeAuthenticationError,
    StripeConfigurationError,
    StripeIdempotencyError,
    StripeInvalidRequestError,
    StripeProviderError,
    StripeTemporaryError,
)
from app.models.refund import Refund
from app.repositories.refund import (
    DuplicateRefundIdempotencyError,
    DuplicateRefundReferenceError,
    RefundConstraintError,
)
from app.schemas.refund import RefundCreate, RefundListResponse, RefundResponse
from app.services.payment import InvalidPaymentStatusTransitionError
from app.services.refund import (
    InvalidRefundStatusTransitionError,
    RefundAmountError,
    RefundFullyRefundedError,
    RefundIdempotencyConflictError,
    RefundNotFoundError,
    RefundPaymentNotEligibleError,
    RefundPaymentNotFoundError,
    RefundPaymentProviderError,
    RefundService,
    StripeRefundMismatchError,
)

payments_refunds_router = APIRouter(
    prefix="/payments",
    tags=["refunds"],
)
refunds_router = APIRouter(
    prefix="/refunds",
    tags=["refunds"],
)

UNAUTHORIZED_RESPONSE = {401: {"description": "Authentication required"}}


@payments_refunds_router.post(
    "/{payment_id}/refunds",
    response_model=RefundResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **UNAUTHORIZED_RESPONSE,
        200: {"description": "Existing idempotent refund"},
        404: {"description": "Payment not found"},
        409: {"description": "Payment, balance, or idempotency conflict"},
        502: {"description": "Stripe rejected or returned an invalid response"},
        503: {"description": "Stripe or local persistence temporarily unavailable"},
    },
    dependencies=[Depends(require_operator)],
)
def create_refund(
    payment_id: int,
    data: RefundCreate,
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
    service: Annotated[RefundService, Depends(get_refund_service)],
) -> Refund:
    try:
        receipt = service.create(
            payment_id,
            data,
            operation_key=idempotency_key,
        )
    except RefundPaymentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Payment not found") from exc
    except RefundAmountError as exc:
        raise HTTPException(
            status_code=409,
            detail="Refund amount exceeds the refundable balance",
        ) from exc
    except (
        RefundPaymentNotEligibleError,
        RefundPaymentProviderError,
        RefundFullyRefundedError,
        RefundIdempotencyConflictError,
        InvalidRefundStatusTransitionError,
        InvalidPaymentStatusTransitionError,
        DuplicateRefundIdempotencyError,
    ) as exc:
        raise HTTPException(
            status_code=409,
            detail="Refund conflicts with current payment or idempotency state",
        ) from exc
    except StripeConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail="Stripe Test Mode is not configured",
        ) from exc
    except StripeTemporaryError as exc:
        raise HTTPException(
            status_code=503,
            detail="Stripe is temporarily unavailable; retry with the same key",
        ) from exc
    except StripeIdempotencyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Stripe refund idempotency conflict",
        ) from exc
    except (StripeAuthenticationError, StripeInvalidRequestError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Stripe rejected the refund request",
        ) from exc
    except (StripeProviderError, StripeRefundMismatchError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Stripe returned an invalid refund response",
        ) from exc
    except (
        RefundConstraintError,
        DuplicateRefundReferenceError,
    ) as exc:
        raise HTTPException(
            status_code=503,
            detail="Refund persistence failed; retry with the same key",
        ) from exc

    response.status_code = 201 if receipt.created else 200
    return receipt.refund


@payments_refunds_router.get(
    "/{payment_id}/refunds",
    response_model=RefundListResponse,
    responses=UNAUTHORIZED_RESPONSE | {404: {"description": "Payment not found"}},
    dependencies=[Depends(require_viewer)],
)
def list_refunds(
    payment_id: int,
    service: Annotated[RefundService, Depends(get_refund_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RefundListResponse:
    try:
        return service.list(
            payment_id,
            page=page,
            page_size=page_size,
        )
    except RefundPaymentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Payment not found") from exc


@refunds_router.get(
    "/{refund_id}",
    response_model=RefundResponse,
    responses=UNAUTHORIZED_RESPONSE | {404: {"description": "Refund not found"}},
    dependencies=[Depends(require_viewer)],
)
def get_refund(
    refund_id: int,
    service: Annotated[RefundService, Depends(get_refund_service)],
) -> Refund:
    try:
        return service.get(refund_id)
    except RefundNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Refund not found") from exc
