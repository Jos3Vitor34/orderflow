from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints


class UserRegister(BaseModel):
    full_name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
    ]
    email: Annotated[EmailStr, Field(max_length=320)]
    password: Annotated[str, StringConstraints(min_length=8, max_length=128)]


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: EmailStr
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AccessToken(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
