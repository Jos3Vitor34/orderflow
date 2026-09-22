from typing import Literal

from pydantic import BaseModel


class AccessToken(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth token type, not a credential
