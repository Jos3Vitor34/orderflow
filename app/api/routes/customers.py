from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.dependencies import (
    get_customer_service,
    require_admin,
    require_operator,
    require_viewer,
)
from app.api.responses import ResponseDescriptions
from app.models.customer import Customer
from app.repositories.customer import (
    CustomerDeleteConflictError,
    DuplicateCustomerEmailError,
)
from app.schemas.customer import (
    CustomerCreate,
    CustomerListResponse,
    CustomerResponse,
    CustomerUpdate,
)
from app.services.customer import CustomerNotFoundError, CustomerService

router = APIRouter(
    prefix="/customers",
    tags=["customers"],
)

UNAUTHORIZED_RESPONSE: ResponseDescriptions = {
    401: {"description": "Authentication required"}
}
NOT_FOUND_RESPONSE: ResponseDescriptions = {404: {"description": "Customer not found"}}
EMAIL_CONFLICT_RESPONSE: ResponseDescriptions = {
    409: {"description": "Customer e-mail already exists"}
}
DELETE_CONFLICT_RESPONSE: ResponseDescriptions = {
    409: {"description": "Customer has related records and cannot be deleted"}
}


def not_found_response() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Customer not found",
    )


def duplicate_email_response() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="A customer with this e-mail already exists",
    )


@router.post(
    "",
    response_model=CustomerResponse,
    status_code=status.HTTP_201_CREATED,
    responses=UNAUTHORIZED_RESPONSE | EMAIL_CONFLICT_RESPONSE,
    dependencies=[Depends(require_operator)],
)
def create_customer(
    data: CustomerCreate,
    service: Annotated[CustomerService, Depends(get_customer_service)],
) -> Customer:
    try:
        return service.create(data)
    except DuplicateCustomerEmailError as exc:
        raise duplicate_email_response() from exc


@router.get(
    "",
    response_model=CustomerListResponse,
    responses=UNAUTHORIZED_RESPONSE,
    dependencies=[Depends(require_viewer)],
)
def list_customers(
    service: Annotated[CustomerService, Depends(get_customer_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CustomerListResponse:
    return service.list(page=page, page_size=page_size)


@router.get(
    "/{customer_id}",
    response_model=CustomerResponse,
    responses=UNAUTHORIZED_RESPONSE | NOT_FOUND_RESPONSE,
    dependencies=[Depends(require_viewer)],
)
def get_customer(
    customer_id: int,
    service: Annotated[CustomerService, Depends(get_customer_service)],
) -> Customer:
    try:
        return service.get(customer_id)
    except CustomerNotFoundError as exc:
        raise not_found_response() from exc


@router.patch(
    "/{customer_id}",
    response_model=CustomerResponse,
    responses=(UNAUTHORIZED_RESPONSE | NOT_FOUND_RESPONSE | EMAIL_CONFLICT_RESPONSE),
    dependencies=[Depends(require_operator)],
)
def update_customer(
    customer_id: int,
    data: CustomerUpdate,
    service: Annotated[CustomerService, Depends(get_customer_service)],
) -> Customer:
    try:
        return service.update(customer_id, data)
    except CustomerNotFoundError as exc:
        raise not_found_response() from exc
    except DuplicateCustomerEmailError as exc:
        raise duplicate_email_response() from exc


@router.delete(
    "/{customer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=UNAUTHORIZED_RESPONSE | NOT_FOUND_RESPONSE | DELETE_CONFLICT_RESPONSE,
    dependencies=[Depends(require_admin)],
)
def delete_customer(
    customer_id: int,
    service: Annotated[CustomerService, Depends(get_customer_service)],
) -> Response:
    try:
        service.delete(customer_id)
    except CustomerNotFoundError as exc:
        raise not_found_response() from exc
    except CustomerDeleteConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Customer cannot be deleted while related records exist",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
