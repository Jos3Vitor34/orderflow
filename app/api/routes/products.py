from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.dependencies import (
    get_product_service,
    require_admin,
    require_operator,
    require_viewer,
)
from app.models.product import Product
from app.repositories.product import (
    DuplicateProductSkuError,
    ProductConstraintError,
    ProductDeleteConflictError,
)
from app.schemas.product import (
    ProductCreate,
    ProductListResponse,
    ProductResponse,
    ProductUpdate,
)
from app.services.product import ProductNotFoundError, ProductService

router = APIRouter(
    prefix="/products",
    tags=["products"],
)

UNAUTHORIZED_RESPONSE = {401: {"description": "Authentication required"}}
NOT_FOUND_RESPONSE = {404: {"description": "Product not found"}}
SKU_CONFLICT_RESPONSE = {409: {"description": "Product SKU already exists"}}
DELETE_CONFLICT_RESPONSE = {
    409: {"description": "Product has related records and cannot be deleted"}
}


def not_found_response() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Product not found",
    )


def duplicate_sku_response() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="A product with this SKU already exists",
    )


def constraint_response() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Product data violates a domain constraint",
    )


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    responses=UNAUTHORIZED_RESPONSE | SKU_CONFLICT_RESPONSE,
    dependencies=[Depends(require_operator)],
)
def create_product(
    data: ProductCreate,
    service: Annotated[ProductService, Depends(get_product_service)],
) -> Product:
    try:
        return service.create(data)
    except DuplicateProductSkuError as exc:
        raise duplicate_sku_response() from exc
    except ProductConstraintError as exc:
        raise constraint_response() from exc


@router.get(
    "",
    response_model=ProductListResponse,
    responses=UNAUTHORIZED_RESPONSE,
    dependencies=[Depends(require_viewer)],
)
def list_products(
    service: Annotated[ProductService, Depends(get_product_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ProductListResponse:
    return service.list(page=page, page_size=page_size)


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    responses=UNAUTHORIZED_RESPONSE | NOT_FOUND_RESPONSE,
    dependencies=[Depends(require_viewer)],
)
def get_product(
    product_id: int,
    service: Annotated[ProductService, Depends(get_product_service)],
) -> Product:
    try:
        return service.get(product_id)
    except ProductNotFoundError as exc:
        raise not_found_response() from exc


@router.patch(
    "/{product_id}",
    response_model=ProductResponse,
    responses=UNAUTHORIZED_RESPONSE | NOT_FOUND_RESPONSE | SKU_CONFLICT_RESPONSE,
    dependencies=[Depends(require_operator)],
)
def update_product(
    product_id: int,
    data: ProductUpdate,
    service: Annotated[ProductService, Depends(get_product_service)],
) -> Product:
    try:
        return service.update(product_id, data)
    except ProductNotFoundError as exc:
        raise not_found_response() from exc
    except DuplicateProductSkuError as exc:
        raise duplicate_sku_response() from exc
    except ProductConstraintError as exc:
        raise constraint_response() from exc


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=UNAUTHORIZED_RESPONSE | NOT_FOUND_RESPONSE | DELETE_CONFLICT_RESPONSE,
    dependencies=[Depends(require_admin)],
)
def delete_product(
    product_id: int,
    service: Annotated[ProductService, Depends(get_product_service)],
) -> Response:
    try:
        service.delete(product_id)
    except ProductNotFoundError as exc:
        raise not_found_response() from exc
    except ProductDeleteConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Product cannot be deleted while related records exist",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
