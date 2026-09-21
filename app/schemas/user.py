from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.models.user import UserRole

UserFullName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
]
UserEmail = Annotated[EmailStr, Field(max_length=320)]
UserPassword = Annotated[str, StringConstraints(min_length=8, max_length=128)]


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: UserFullName
    email: UserEmail
    password: UserPassword
    role: UserRole = UserRole.VIEWER


class UserRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: UserRole


class UserActiveUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: EmailStr
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserListResponse(BaseModel):
    items: list[UserPublic]
    total: int
    page: int
    page_size: int
    pages: int
