from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import get_current_user, get_order_service
from app.repositories.order import OrderPersistenceError
from app.schemas.order import (
    OrderCreate,
    OrderListResponse,
    OrderResponse,
    OrderUpdate,
)
from app.services.order import (
    InactiveOrderProductError,
    OrderCustomerNotFoundError,
    OrderNotFoundError,
    OrderProductNotFoundError,
    OrderService,
    OrderTotalOutOfRangeError,
)

router = APIRouter(
    prefix="/orders",
    tags=["orders"],
    dependencies=[Depends(get_current_user)],
)

UNAUTHORIZED_RESPONSE = {401: {"description": "Authentication required"}}
NOT_FOUND_RESPONSE = {404: {"description": "Order or related resource not found"}}
CONFLICT_RESPONSE = {409: {"description": "Order conflicts with resource state"}}


def not_found_response(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def conflict_response(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


@router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    responses=UNAUTHORIZED_RESPONSE | NOT_FOUND_RESPONSE | CONFLICT_RESPONSE,
)
def create_order(
    data: OrderCreate,
    service: Annotated[OrderService, Depends(get_order_service)],
) -> OrderResponse:
    try:
        return service.create(data)
    except OrderCustomerNotFoundError as exc:
        raise not_found_response("Customer not found") from exc
    except OrderProductNotFoundError as exc:
        raise not_found_response("Product not found") from exc
    except InactiveOrderProductError as exc:
        raise conflict_response("Inactive products cannot be ordered") from exc
    except OrderTotalOutOfRangeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Calculated order total exceeds the supported monetary range",
        ) from exc
    except OrderPersistenceError as exc:
        raise conflict_response(
            "Order could not be created because related data changed"
        ) from exc


@router.get("", response_model=OrderListResponse, responses=UNAUTHORIZED_RESPONSE)
def list_orders(
    service: Annotated[OrderService, Depends(get_order_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> OrderListResponse:
    return service.list(page=page, page_size=page_size)


@router.get(
    "/{order_id}",
    response_model=OrderResponse,
    responses=UNAUTHORIZED_RESPONSE | NOT_FOUND_RESPONSE,
)
def get_order(
    order_id: int,
    service: Annotated[OrderService, Depends(get_order_service)],
) -> OrderResponse:
    try:
        return service.get(order_id)
    except OrderNotFoundError as exc:
        raise not_found_response("Order not found") from exc


@router.patch(
    "/{order_id}",
    response_model=OrderResponse,
    responses=UNAUTHORIZED_RESPONSE | NOT_FOUND_RESPONSE | CONFLICT_RESPONSE,
)
def update_order(
    order_id: int,
    data: OrderUpdate,
    service: Annotated[OrderService, Depends(get_order_service)],
) -> OrderResponse:
    try:
        return service.update(order_id, data)
    except OrderNotFoundError as exc:
        raise not_found_response("Order not found") from exc
    except OrderPersistenceError as exc:
        raise conflict_response("Order status could not be updated") from exc
