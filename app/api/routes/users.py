from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import get_user_service, require_admin
from app.models.user import User
from app.repositories.user import DuplicateEmailError
from app.schemas.user import (
    UserActiveUpdate,
    UserCreate,
    UserListResponse,
    UserPublic,
    UserRoleUpdate,
)
from app.services.user import LastActiveAdminError, UserNotFoundError, UserService

router = APIRouter(prefix="/users", tags=["users"])
admin_dependency = Depends(require_admin)

AUTHORIZATION_RESPONSES = {
    401: {"description": "Authentication required"},
    403: {"description": "Administrator role required"},
}


def not_found_response() -> HTTPException:
    return HTTPException(status_code=404, detail="User not found")


def last_admin_response() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail="At least one active administrator must remain",
    )


@router.post(
    "",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[admin_dependency],
    responses=AUTHORIZATION_RESPONSES | {409: {"description": "E-mail conflict"}},
)
def create_user(
    data: UserCreate,
    service: Annotated[UserService, Depends(get_user_service)],
) -> User:
    try:
        return service.create(data)
    except DuplicateEmailError as exc:
        raise HTTPException(
            status_code=409,
            detail="A user with this e-mail already exists",
        ) from exc


@router.get(
    "",
    response_model=UserListResponse,
    dependencies=[admin_dependency],
    responses=AUTHORIZATION_RESPONSES,
)
def list_users(
    service: Annotated[UserService, Depends(get_user_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> UserListResponse:
    return service.list(page=page, page_size=page_size)


@router.get(
    "/{user_id}",
    response_model=UserPublic,
    dependencies=[admin_dependency],
    responses=AUTHORIZATION_RESPONSES | {404: {"description": "User not found"}},
)
def get_user(
    user_id: int,
    service: Annotated[UserService, Depends(get_user_service)],
) -> User:
    try:
        return service.get(user_id)
    except UserNotFoundError as exc:
        raise not_found_response() from exc


@router.patch(
    "/{user_id}/role",
    response_model=UserPublic,
    dependencies=[admin_dependency],
    responses=AUTHORIZATION_RESPONSES
    | {404: {"description": "User not found"}, 409: {"description": "Last admin"}},
)
def update_user_role(
    user_id: int,
    data: UserRoleUpdate,
    service: Annotated[UserService, Depends(get_user_service)],
) -> User:
    try:
        return service.update_role(user_id, data.role)
    except UserNotFoundError as exc:
        raise not_found_response() from exc
    except LastActiveAdminError as exc:
        raise last_admin_response() from exc


@router.patch(
    "/{user_id}/active",
    response_model=UserPublic,
    dependencies=[admin_dependency],
    responses=AUTHORIZATION_RESPONSES
    | {404: {"description": "User not found"}, 409: {"description": "Last admin"}},
)
def update_user_active(
    user_id: int,
    data: UserActiveUpdate,
    service: Annotated[UserService, Depends(get_user_service)],
) -> User:
    try:
        return service.update_active(user_id, data.is_active)
    except UserNotFoundError as exc:
        raise not_found_response() from exc
    except LastActiveAdminError as exc:
        raise last_admin_response() from exc
